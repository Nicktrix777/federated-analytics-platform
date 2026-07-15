package handlers

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/core-api/middleware"
	"github.com/federated-analytics/core-api/models"
	"github.com/federated-analytics/core-api/services"
)

// maxQueryRepairAttempts bounds how many times a failed AI-generated query is
// sent back to the AI Engine to repair before giving up.
const maxQueryRepairAttempts = 2

// QueryHandler orchestrates the full query pipeline:
//   1. Receive natural language question or raw SQL from frontend
//   2. [AI mode] Call AI Engine → receive QueryPlan
//   3. [AI mode] Validate the QueryPlan (safety check)
//   4. [SQL mode] Wrap raw SQL in a minimal QueryPlan
//   5. Call Query Service to execute against Trino
//   6. Return plan + results to frontend
//
// Architecture boundary:
//   This handler is the ONLY place where the AI plan is validated
//   before execution. Nothing passes through here unchecked.

type QueryHandler struct {
	aiClient        *services.AIClient
	queryClient     *services.QueryClient
	metadataSvc     *services.MetadataService
	conversationSvc *services.ConversationService
	aiEnabled       bool
}

func NewQueryHandler(
	aiClient *services.AIClient,
	queryClient *services.QueryClient,
	metadataSvc *services.MetadataService,
	conversationSvc *services.ConversationService,
	aiEnabled bool,
) *QueryHandler {
	return &QueryHandler{
		aiClient:        aiClient,
		queryClient:     queryClient,
		metadataSvc:     metadataSvc,
		conversationSvc: conversationSvc,
		aiEnabled:       aiEnabled,
	}
}

// convoTurnLimit caps how many prior turns are loaded into a follow-up's
// context — enough to resolve references without bloating the prompt.
const convoTurnLimit = 6

// loadConversationContext returns a pre-rendered "prior turns" block for the
// given conversation, plus whether the conversation is active (valid id). It is
// best-effort: any DB error just yields an empty block, never a failed request.
func (h *QueryHandler) loadConversationContext(conversationID string) (context string, active bool) {
	if h.conversationSvc == nil || !services.ValidConversationID(conversationID) {
		return "", false
	}
	// Register/refresh the conversation up front so an immediately-following
	// request sees it even if this one records no turn (e.g. it errors).
	if err := h.conversationSvc.EnsureConversation(conversationID); err != nil {
		return "", false
	}
	turns, err := h.conversationSvc.RecentTurns(conversationID, convoTurnLimit)
	if err != nil || len(turns) == 0 {
		return "", true
	}
	var b strings.Builder
	b.WriteString("Earlier turns in this conversation (oldest first). The new question may be a follow-up referring to these:\n")
	for i, t := range turns {
		fmt.Fprintf(&b, "%d. Q: %q\n", i+1, t.Question)
		if t.SQL != "" {
			fmt.Fprintf(&b, "   SQL: %s\n", t.SQL)
		}
	}
	return b.String(), true
}

// recordConversationTurn appends a successful exchange, best-effort.
func (h *QueryHandler) recordConversationTurn(conversationID, question, sqlText string, rowCount int) {
	if h.conversationSvc == nil || !services.ValidConversationID(conversationID) {
		return
	}
	_ = h.conversationSvc.AppendTurn(conversationID, question, sqlText, rowCount)
}

// HandleQuery is the main POST /api/query handler
func (h *QueryHandler) HandleQuery(c *gin.Context) {
	var req models.QueryRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	// Store audit context
	c.Set(middleware.AuditQuestionKey, req.Question)
	c.Set(middleware.AuditModeKey, req.Mode)

	reqIDStr := middleware.RequestIDFromContext(c)

	var plan *models.QueryPlan
	var sqlToExecute string
	// Captured in AI mode so a failed query can be re-planned/repaired with the
	// same schema context.
	var datasets []models.DatasetMeta

	switch req.Mode {
	case "ai":
		if !h.aiEnabled {
			c.JSON(http.StatusBadRequest, models.ErrorResponse{
				Error:   "AI mode is disabled",
				Details: "Set AI_ENABLED=true in environment or use mode=sql",
			})
			return
		}

		// Fetch schema metadata to provide context to the AI Engine
		var err error
		datasets, err = h.metadataSvc.GetAllDatasets()
		if err != nil {
			c.JSON(http.StatusInternalServerError, models.ErrorResponse{
				Error:   "Failed to fetch metadata",
				Details: err.Error(),
			})
			c.Set(middleware.AuditStatusKey, "error")
			c.Set(middleware.AuditErrorKey, err.Error())
			return
		}

		// Multi-turn: load prior turns so a follow-up resolves against history.
		convoContext, _ := h.loadConversationContext(req.ConversationID)

		// Call AI Engine — it produces a QueryPlan, never executes anything
		generatedPlan, err := h.aiClient.GeneratePlan(reqIDStr, req.Question, datasets, convoContext)
		if err != nil {
			c.JSON(http.StatusBadGateway, models.ErrorResponse{
				Error:   "AI Engine failed to generate query plan",
				Details: err.Error(),
			})
			c.Set(middleware.AuditStatusKey, "error")
			c.Set(middleware.AuditErrorKey, err.Error())
			return
		}

		// ── VALIDATION BOUNDARY ──────────────────────────────────
		// Validate the AI-generated plan before any execution.
		// This is the safety gate between the non-deterministic AI
		// and the deterministic execution layer.
		if err := validateQueryPlan(generatedPlan); err != nil {
			c.JSON(http.StatusUnprocessableEntity, models.ErrorResponse{
				Error:   "AI Engine produced an invalid query plan",
				Details: err.Error(),
			})
			c.Set(middleware.AuditStatusKey, "error")
			c.Set(middleware.AuditErrorKey, err.Error())
			return
		}

		plan = generatedPlan
		sqlToExecute = plan.SQL

	case "sql":
		// Direct SQL mode — user provides raw SQL
		sqlToExecute = req.Question
		// Validate it's a safe SELECT
		if err := validateRawSQL(sqlToExecute); err != nil {
			c.JSON(http.StatusBadRequest, models.ErrorResponse{
				Error:   "Invalid SQL",
				Details: err.Error(),
			})
			c.Set(middleware.AuditStatusKey, "error")
			c.Set(middleware.AuditErrorKey, err.Error())
			return
		}
		// Wrap in a minimal plan for consistent response shape
		plan = &models.QueryPlan{
			Question:    req.Question,
			SQL:         sqlToExecute,
			Confidence:  1.0,
			Explanation: "Direct SQL mode — query executed as-is",
		}
	}

	c.Set(middleware.AuditPlanKey, plan)
	c.Set(middleware.AuditSQLKey, sqlToExecute)

	// Execute the validated SQL via the Query Service. For AI-generated SQL,
	// self-heal on execution failure: send the Trino error back to the AI
	// Engine to repair (bounded), re-validate, and retry — the same safety net
	// the dashboard flow uses, so a single bad nested query no longer hard-fails.
	// Raw user SQL (mode=sql) is never rewritten.
	var result *models.ExecuteResponse
	var err error
	if req.Mode == "ai" {
		var executedSQL string
		executedSQL, result, err = h.executeWithRepair(reqIDStr, sqlToExecute, datasets, req.Question)
		if executedSQL != sqlToExecute {
			sqlToExecute = executedSQL
			plan.SQL = executedSQL
			c.Set(middleware.AuditSQLKey, sqlToExecute)
		}
	} else {
		result, err = h.queryClient.Execute(reqIDStr, sqlToExecute)
	}
	if err != nil {
		c.JSON(http.StatusBadGateway, models.ErrorResponse{
			Error:   "Query execution failed",
			Details: err.Error(),
		})
		c.Set(middleware.AuditStatusKey, "error")
		c.Set(middleware.AuditErrorKey, err.Error())
		return
	}

	c.Set(middleware.AuditRowCountKey, result.RowCount)
	c.Set(middleware.AuditStatusKey, "success")

	// Record the exchange so the next question in this conversation has context
	// (AI mode only — a "turn" is an NL question resolved to SQL).
	if req.Mode == "ai" {
		h.recordConversationTurn(req.ConversationID, req.Question, sqlToExecute, result.RowCount)
	}

	c.JSON(http.StatusOK, models.QueryResponse{
		RequestID:       reqIDStr,
		Question:        req.Question,
		Mode:            req.Mode,
		Plan:            plan,
		Columns:         result.Columns,
		Rows:            result.Rows,
		RowCount:        result.RowCount,
		ExecutionTimeMs: result.ExecutionTimeMs,
		AIEnabled:       h.aiEnabled,
	})
}

// HandleQueryStream is POST /api/query/stream — the SSE variant of
// HandleQuery (see docs/sse-events.md). AI mode only: SQL mode has no
// pipeline to stream, so mode="sql" is rejected with 400 and callers should
// use POST /api/query instead.
//
// Failures before the stream opens (bad request body, AI disabled, metadata
// fetch) return plain JSON errors; once SSE has started, every failure is a
// terminal `error` event.
func (h *QueryHandler) HandleQueryStream(c *gin.Context) {
	var req models.QueryRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	// Store audit context
	c.Set(middleware.AuditQuestionKey, req.Question)
	c.Set(middleware.AuditModeKey, req.Mode)

	if req.Mode != "ai" {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{
			Error:   "Streaming supports mode=ai only",
			Details: "Use POST /api/query for direct SQL execution",
		})
		c.Set(middleware.AuditStatusKey, "error")
		return
	}
	if !h.aiEnabled {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{
			Error:   "AI mode is disabled",
			Details: "Set AI_ENABLED=true in environment or use mode=sql",
		})
		c.Set(middleware.AuditStatusKey, "error")
		return
	}

	datasets, err := h.metadataSvc.GetAllDatasets()
	if err != nil {
		c.JSON(http.StatusInternalServerError, models.ErrorResponse{
			Error:   "Failed to fetch metadata",
			Details: err.Error(),
		})
		c.Set(middleware.AuditStatusKey, "error")
		c.Set(middleware.AuditErrorKey, err.Error())
		return
	}

	reqIDStr := middleware.RequestIDFromContext(c)
	sse := newSSEStream(c, reqIDStr)

	fail := func(detail string, statusCode int) {
		sse.emitError(detail, statusCode)
		c.Set(middleware.AuditStatusKey, "error")
		c.Set(middleware.AuditErrorKey, detail)
	}

	// Multi-turn: load prior turns so a follow-up resolves against history.
	convoContext, _ := h.loadConversationContext(req.ConversationID)

	// Proxy the AI Engine's pipeline events; consume the terminal plan event.
	terminal, ok := proxyAIStream(sse, "plan", func(onEvent func(services.SSEEvent) error) error {
		return h.aiClient.StreamPlan(reqIDStr, req.Question, datasets, convoContext, onEvent)
	})
	if !ok {
		c.Set(middleware.AuditStatusKey, "error")
		return
	}

	var planEvent struct {
		Plan *models.QueryPlan `json:"plan"`
	}
	if err := json.Unmarshal(terminal, &planEvent); err != nil || planEvent.Plan == nil {
		fail("AI Engine sent a malformed plan event", http.StatusBadGateway)
		return
	}
	plan := planEvent.Plan

	// ── VALIDATION BOUNDARY ── same safety gate as the non-streaming path.
	if err := validateQueryPlan(plan); err != nil {
		fail("AI Engine produced an invalid query plan: "+err.Error(), http.StatusUnprocessableEntity)
		return
	}

	c.Set(middleware.AuditPlanKey, plan)
	c.Set(middleware.AuditSQLKey, plan.SQL)

	sse.emit("stage", gin.H{"stage": "executing_sql"})

	result, err := h.queryClient.Execute(reqIDStr, plan.SQL)
	if err != nil {
		fail("Query execution failed: "+err.Error(), http.StatusBadGateway)
		return
	}

	c.Set(middleware.AuditRowCountKey, result.RowCount)
	c.Set(middleware.AuditStatusKey, "success")

	// Record the exchange for multi-turn context (stream path is AI-only).
	h.recordConversationTurn(req.ConversationID, req.Question, plan.SQL, result.RowCount)

	// Terminal result event — same JSON shape as the non-streaming response.
	sse.emit("result", models.QueryResponse{
		RequestID:       reqIDStr,
		Question:        req.Question,
		Mode:            req.Mode,
		Plan:            plan,
		Columns:         result.Columns,
		Rows:            result.Rows,
		RowCount:        result.RowCount,
		ExecutionTimeMs: result.ExecutionTimeMs,
		AIEnabled:       h.aiEnabled,
	})
}

// executeWithRepair runs AI-generated SQL and, on execution failure, sends the
// SQL + the exact Trino error back to the AI Engine to repair (up to
// maxQueryRepairAttempts), re-validating the static safety gate and re-executing
// each attempt. Returns the SQL that actually ran (possibly repaired) alongside
// the result. This mirrors the dashboard widget flow so a single malformed
// query — e.g. bad nested UNNEST — self-heals instead of hard-failing.
func (h *QueryHandler) executeWithRepair(
	reqID, sql string, datasets []models.DatasetMeta, question string,
) (string, *models.ExecuteResponse, error) {
	result, err := h.queryClient.Execute(reqID, sql)
	if err == nil {
		return sql, result, nil
	}

	for attempt := 1; attempt <= maxQueryRepairAttempts; attempt++ {
		log.Printf("query: AI SQL failed to execute (attempt %d/%d), repairing: %v",
			attempt, maxQueryRepairAttempts, err)
		repaired, rerr := h.aiClient.RepairWidgetSQL(reqID, sql, err.Error(), "table", question, datasets)
		if rerr != nil {
			log.Printf("query: repair call failed: %v", rerr)
			break
		}
		// A repaired query must still clear the static safety gate before we run it.
		if verr := validateRawSQL(repaired); verr != nil {
			log.Printf("query: repaired SQL failed safety check: %v", verr)
			break
		}
		sql = repaired
		result, err = h.queryClient.Execute(reqID, sql)
		if err == nil {
			log.Printf("query: AI SQL repaired successfully on attempt %d", attempt)
			return sql, result, nil
		}
	}
	return sql, nil, err
}

// validateQueryPlan enforces safety constraints on AI-generated plans.
// This is the critical validation gate between AI output and execution.
func validateQueryPlan(plan *models.QueryPlan) error {
	if plan == nil {
		return errorf("plan is nil")
	}
	if strings.TrimSpace(plan.SQL) == "" {
		return errorf("plan contains no SQL")
	}
	return validateRawSQL(plan.SQL)
}

// validateRawSQL ensures only SELECT statements are allowed.
// Protects against SQL injection and destructive operations.
func validateRawSQL(sql string) error {
	normalized := strings.TrimSpace(strings.ToUpper(sql))

	dangerousKeywords := []string{
		"INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE ",
		"ALTER ", "CREATE ", "GRANT ", "REVOKE ", "EXECUTE ",
	}
	for _, kw := range dangerousKeywords {
		if strings.Contains(normalized, kw) {
			return errorf("only SELECT queries are allowed; found forbidden keyword: %s", kw)
		}
	}

	if !strings.HasPrefix(normalized, "SELECT") && !strings.HasPrefix(normalized, "WITH") {
		return errorf("query must start with SELECT or WITH (CTE), got: %.30s...", normalized)
	}

	return nil
}

func errorf(format string, args ...interface{}) error {
	return fmt.Errorf(format, args...)
}
