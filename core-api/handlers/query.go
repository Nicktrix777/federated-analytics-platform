package handlers

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/core-api/middleware"
	"github.com/federated-analytics/core-api/models"
	"github.com/federated-analytics/core-api/services"
)

// maxQueryRepairAttempts bounds how many times a failed AI-generated query is
// sent back to the AI Engine to repair before giving up.
const maxQueryRepairAttempts = 2

// queryDeadlineSeconds is the maximum wall-clock budget for the entire
// execute+repair+zero-row cycle. Past this deadline further repair attempts
// and the zero-row pass are skipped to respect WriteTimeout (430s) and the
// upstream AI client timeout (300s).
const queryDeadlineSeconds = 360

// QueryHandler orchestrates the full query pipeline:
//  1. Receive natural language question or raw SQL from frontend
//  2. [AI mode] Call AI Engine → receive QueryPlan or Clarification
//  3. [AI mode] Validate the QueryPlan (safety check)
//  4. [SQL mode] Wrap raw SQL in a minimal QueryPlan
//  5. Call Query Service to execute against Trino
//  6. [AI mode] Self-heal on failure (bounded repair loop with stage events)
//  7. [AI mode] Zero-row sanity pass (filter-literal correction)
//  8. Return plan + results to frontend
//
// Architecture boundary:
//
//	This handler is the ONLY place where the AI plan is validated
//	before execution. Nothing passes through here unchecked.
type QueryHandler struct {
	aiClient        *services.AIClient
	queryClient     *services.QueryClient
	metadataSvc     *services.MetadataService
	conversationSvc *services.ConversationService
	curationSvc     *services.CurationService
	aiEnabled       bool
}

func NewQueryHandler(
	aiClient *services.AIClient,
	queryClient *services.QueryClient,
	metadataSvc *services.MetadataService,
	conversationSvc *services.ConversationService,
	curationSvc *services.CurationService,
	aiEnabled bool,
) *QueryHandler {
	return &QueryHandler{
		aiClient:        aiClient,
		queryClient:     queryClient,
		metadataSvc:     metadataSvc,
		conversationSvc: conversationSvc,
		curationSvc:     curationSvc,
		aiEnabled:       aiEnabled,
	}
}

// convoTurnLimit caps how many prior turns are loaded into a follow-up's
// context — enough to resolve references without bloating the prompt.
const convoTurnLimit = 6

// loadConversationMessages returns structured ChatMessage objects for the
// given conversation, plus whether the conversation is active (valid id). It is
// best-effort: any DB error just yields nil messages, never a failed request.
func (h *QueryHandler) loadConversationMessages(conversationID string) (messages []models.ChatMessage, active bool) {
	if h.conversationSvc == nil || !services.ValidConversationID(conversationID) {
		return nil, false
	}
	// Register/refresh the conversation up front so an immediately-following
	// request sees it even if this one records no turn (e.g. it errors).
	if err := h.conversationSvc.EnsureConversation(conversationID); err != nil {
		return nil, false
	}
	msgs, err := h.conversationSvc.BuildMessages(conversationID, convoTurnLimit)
	if err != nil || len(msgs) == 0 {
		return nil, true
	}
	return msgs, true
}

// recordPlanTurn appends a successful plan exchange with structured payload,
// best-effort.
func (h *QueryHandler) recordPlanTurn(conversationID, question, sqlText string, rowCount int, confidence float64) {
	if h.conversationSvc == nil || !services.ValidConversationID(conversationID) {
		return
	}
	payload, _ := json.Marshal(map[string]interface{}{
		"sql":        sqlText,
		"row_count":  rowCount,
		"confidence": confidence,
	})
	_ = h.conversationSvc.AppendTurn(conversationID, question, "plan", sqlText, rowCount, payload)
}

// recordClarificationTurn appends a clarification exchange with structured
// payload, best-effort. The SQL and row_count columns are empty/0.
func (h *QueryHandler) recordClarificationTurn(conversationID, question string, clar *models.Clarification) {
	if h.conversationSvc == nil || !services.ValidConversationID(conversationID) || clar == nil {
		return
	}
	payload, _ := json.Marshal(clar)
	_ = h.conversationSvc.AppendTurn(conversationID, question, "clarification", "", 0, payload)
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
	handlerStart := time.Now()

	var plan *models.QueryPlan
	var sqlToExecute string

	switch req.Mode {
	case "ai":
		if !h.aiEnabled {
			c.JSON(http.StatusBadRequest, models.ErrorResponse{
				Error:   "AI mode is disabled",
				Details: "Set AI_ENABLED=true in environment or use mode=sql",
			})
			return
		}

		// Multi-turn: load prior turns so a follow-up resolves against history.
		convoMessages, _ := h.loadConversationMessages(req.ConversationID)

		// Call AI Engine — it produces a QueryPlan or Clarification, never executes anything.
		generatedPlan, clarification, err := h.aiClient.GeneratePlan(reqIDStr, req.Question, nil, convoMessages)
		if err != nil {
			c.JSON(http.StatusBadGateway, models.ErrorResponse{
				Error:   "AI Engine failed to generate query plan",
				Details: err.Error(),
			})
			c.Set(middleware.AuditStatusKey, "error")
			c.Set(middleware.AuditErrorKey, err.Error())
			return
		}

		// PR5: clarification is a terminal outcome — return it without executing SQL.
		if clarification != nil {
			h.recordClarificationTurn(req.ConversationID, req.Question, clarification)
			c.Set(middleware.AuditStatusKey, "success")
			c.JSON(http.StatusOK, models.QueryResponse{
				RequestID:     reqIDStr,
				Question:      req.Question,
				Mode:          req.Mode,
				Clarification: clarification,
				Columns:       []string{},
				Rows:          [][]interface{}{},
				RowCount:      0,
				AIEnabled:     h.aiEnabled,
			})
			return
		}

		// ── VALIDATION BOUNDARY ──────────────────────────────────
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

	deadline := handlerStart.Add(queryDeadlineSeconds * time.Second)

	var result *models.ExecuteResponse
	var err error
	if req.Mode == "ai" {
		// AI mode: unified repair loop + zero-row pass + curation on exhaustion.
		var executedSQL string
		var clarification *models.Clarification
		executedSQL, result, clarification, err = h.executeWithRepairBlocking(
			reqIDStr, sqlToExecute, req.Question, req.ConversationID, deadline,
		)
		if executedSQL != sqlToExecute {
			sqlToExecute = executedSQL
			plan.SQL = executedSQL
			c.Set(middleware.AuditSQLKey, sqlToExecute)
		}
		// Repair-exhausted produces a clarification instead of a bare error.
		if clarification != nil {
			h.recordClarificationTurn(req.ConversationID, req.Question, clarification)
			c.Set(middleware.AuditStatusKey, "success")
			c.JSON(http.StatusOK, models.QueryResponse{
				RequestID:     reqIDStr,
				Question:      req.Question,
				Mode:          req.Mode,
				Clarification: clarification,
				Columns:       []string{},
				Rows:          [][]interface{}{},
				RowCount:      0,
				AIEnabled:     h.aiEnabled,
			})
			return
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

	if req.Mode == "ai" {
		confidence := 0.0
		if plan != nil {
			confidence = plan.Confidence
		}
		h.recordPlanTurn(req.ConversationID, req.Question, sqlToExecute, result.RowCount, confidence)
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
// pipeline to stream, so mode="sql" is rejected with 400.
//
// Failures before the stream opens return plain JSON errors; once SSE has
// started every failure is a terminal `error` event.
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

	reqIDStr := middleware.RequestIDFromContext(c)
	handlerStart := time.Now()
	sse := newSSEStream(c, reqIDStr)

	fail := func(detail string, statusCode int) {
		sse.emitError(detail, statusCode)
		c.Set(middleware.AuditStatusKey, "error")
		c.Set(middleware.AuditErrorKey, detail)
	}

	// Multi-turn: load prior turns so a follow-up resolves against history.
	convoMessages, _ := h.loadConversationMessages(req.ConversationID)

	// Proxy the AI Engine's pipeline events; consume the terminal event.
	// PR5: accept both "plan" and "clarification" as terminal events.
	terminalName, terminal, ok := proxyAIStream(sse, []string{"plan", "clarification"}, func(onEvent func(services.SSEEvent) error) error {
		return h.aiClient.StreamPlan(reqIDStr, req.Question, nil, convoMessages, onEvent)
	})
	if !ok {
		c.Set(middleware.AuditStatusKey, "error")
		return
	}

	// PR5: clarification terminal from the AI Engine — record and re-emit.
	if terminalName == "clarification" {
		var clar models.Clarification
		if err := json.Unmarshal(terminal, &clar); err != nil {
			fail("AI Engine sent a malformed clarification event", http.StatusBadGateway)
			return
		}
		h.recordClarificationTurn(req.ConversationID, req.Question, &clar)
		c.Set(middleware.AuditStatusKey, "success")
		sse.emit("clarification", models.QueryResponse{
			RequestID:     reqIDStr,
			Question:      req.Question,
			Mode:          req.Mode,
			Clarification: &clar,
			Columns:       []string{},
			Rows:          [][]interface{}{},
			RowCount:      0,
			AIEnabled:     h.aiEnabled,
		})
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

	deadline := handlerStart.Add(queryDeadlineSeconds * time.Second)

	// Start heartbeat: the AI Engine heartbeats stopped when its stream closed;
	// Trino execution can take up to 120s, and the repair loop adds more.
	sse.startHeartbeat()
	defer sse.stopHeartbeat()

	// PR6: unified repair loop + zero-row pass, now on the streaming path too.
	executedSQL, result, clarification, err := h.executeWithRepairStreaming(
		reqIDStr, plan.SQL, req.Question, req.ConversationID, deadline, sse,
	)
	if executedSQL != plan.SQL {
		plan.SQL = executedSQL
		c.Set(middleware.AuditSQLKey, plan.SQL)
	}

	// Repair-exhausted → clarification terminal (not a raw error).
	if clarification != nil {
		h.recordClarificationTurn(req.ConversationID, req.Question, clarification)
		c.Set(middleware.AuditStatusKey, "success")
		sse.emit("clarification", models.QueryResponse{
			RequestID:     reqIDStr,
			Question:      req.Question,
			Mode:          req.Mode,
			Clarification: clarification,
			Columns:       []string{},
			Rows:          [][]interface{}{},
			RowCount:      0,
			AIEnabled:     h.aiEnabled,
		})
		return
	}

	if err != nil {
		fail("Query execution failed: "+err.Error(), http.StatusBadGateway)
		return
	}

	c.Set(middleware.AuditRowCountKey, result.RowCount)
	c.Set(middleware.AuditStatusKey, "success")

	// Record the exchange for multi-turn context.
	h.recordPlanTurn(req.ConversationID, req.Question, plan.SQL, result.RowCount, plan.Confidence)

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

// ── execProgress ──────────────────────────────────────────────
// execProgress is an optional stage-event emitter for the repair loop.
// nil means blocking path (no SSE events to emit).
type execProgress func(stage, detail string)

// ── executeWithRepairBlocking ─────────────────────────────────
// executeWithRepairBlocking runs the repair+zero-row cycle for the non-streaming
// (blocking) path. Returns (executedSQL, result, clarification, error).
//
// clarification is non-nil when all repairs are exhausted — the caller converts
// this into an HTTP 200 QueryResponse with an empty rows array.
func (h *QueryHandler) executeWithRepairBlocking(
	reqID, sql, question, conversationID string,
	deadline time.Time,
) (string, *models.ExecuteResponse, *models.Clarification, error) {
	return h.executeWithRepair(reqID, sql, question, conversationID, deadline, nil)
}

// ── executeWithRepairStreaming ────────────────────────────────
// executeWithRepairStreaming runs the repair+zero-row cycle for the streaming
// path. stage events are emitted on sse so the frontend's AIProgressTimeline
// shows repairing_sql / executing_sql / zero_rows_retry stages.
func (h *QueryHandler) executeWithRepairStreaming(
	reqID, sql, question, conversationID string,
	deadline time.Time,
	sse *sseStream,
) (string, *models.ExecuteResponse, *models.Clarification, error) {
	progress := func(stage, detail string) {
		payload := gin.H{"stage": stage}
		if detail != "" {
			payload["detail"] = detail
		}
		sse.emit("stage", payload)
	}
	return h.executeWithRepair(reqID, sql, question, conversationID, deadline, progress)
}

// ── executeWithRepair ─────────────────────────────────────────
// executeWithRepair is the unified core: execute → repair loop → zero-row pass.
//
//   - On success with rows: return immediately.
//   - On execution failure: try up to maxQueryRepairAttempts repair calls.
//     Each attempt emits a "repairing_sql" stage event (streaming path).
//   - If repairs exhaust: build a repair_exhausted Clarification, add a
//     needs_curation row, return (sql, nil, clarification, nil).
//   - On success with 0 rows: attempt one zero-row filter-literal correction
//     (if within deadline). If the corrected SQL produces rows, emit
//     "zero_rows_retry" and return the new result. If still zero rows or
//     worse, return the original result + a zero_rows_unresolved curation row.
//   - progress is nil on the blocking path; stage events are no-ops then.
func (h *QueryHandler) executeWithRepair(
	reqID, sql, question, conversationID string,
	deadline time.Time,
	progress execProgress,
) (string, *models.ExecuteResponse, *models.Clarification, error) {
	emitStage := func(stage, detail string) {
		if progress != nil {
			progress(stage, detail)
		}
	}

	emitStage("executing_sql", "")
	result, execErr := h.queryClient.Execute(reqID, sql)
	if execErr == nil {
		// Zero-row sanity pass — only if within the deadline budget.
		return h.handleZeroRow(reqID, sql, question, conversationID, result, deadline, emitStage)
	}

	// ── Repair loop ───────────────────────────────────────────────────────────
	lastErr := execErr
	for attempt := 1; attempt <= maxQueryRepairAttempts; attempt++ {
		if time.Now().After(deadline) {
			log.Printf("query: deadline exceeded before repair attempt %d, stopping", attempt)
			break
		}

		errFirstLine := firstLine(lastErr.Error())
		log.Printf("query: AI SQL failed (attempt %d/%d), repairing: %v",
			attempt, maxQueryRepairAttempts, lastErr)
		emitStage("repairing_sql", fmt.Sprintf("attempt %d/%d: %s", attempt, maxQueryRepairAttempts, errFirstLine))

		repaired, rerr := h.aiClient.RepairWidgetSQL(reqID, sql, lastErr.Error(), "table", question, "error")
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
		emitStage("executing_sql", "")
		result, execErr = h.queryClient.Execute(reqID, sql)
		if execErr == nil {
			log.Printf("query: AI SQL repaired successfully on attempt %d", attempt)
			return h.handleZeroRow(reqID, sql, question, conversationID, result, deadline, emitStage)
		}
		lastErr = execErr
	}

	// ── Repair exhausted → clarification ─────────────────────────────────────
	errFirstLine := firstLine(lastErr.Error())
	log.Printf("query: repair exhausted after %d attempt(s), building clarification: %v",
		maxQueryRepairAttempts, lastErr)

	// Record in the curation queue so operators can review.
	if h.curationSvc != nil {
		h.curationSvc.Add("repair_exhausted", question, errFirstLine, reqID, conversationID)
	}

	// Build a repair_exhausted clarification payload that carries the failed
	// SQL and error so the user's reply replays with full failure context.
	clar := &models.Clarification{
		Question: fmt.Sprintf(
			"I wasn't able to run that query after %d repair attempt(s). "+
				"The database said: %s. "+
				"Could you rephrase your question or clarify what you're looking for?",
			maxQueryRepairAttempts, errFirstLine,
		),
		Options: []string{},
		Kind:    "repair_exhausted",
	}
	return sql, nil, clar, nil
}

// ── handleZeroRow ─────────────────────────────────────────────
// handleZeroRow runs the zero-row sanity pass. If result has rows, it passes
// through unchanged. If result has 0 rows and we're within the deadline, one
// filter-literal correction attempt is made. Returns the best result we have.
func (h *QueryHandler) handleZeroRow(
	reqID, sql, question, conversationID string,
	result *models.ExecuteResponse,
	deadline time.Time,
	emitStage func(stage, detail string),
) (string, *models.ExecuteResponse, *models.Clarification, error) {
	// Non-zero result or deadline already passed — nothing to do.
	if result.RowCount != 0 || time.Now().After(deadline) {
		return sql, result, nil, nil
	}

	log.Printf("query: zero rows returned, attempting filter-literal correction")
	corrected, rerr := h.aiClient.RepairWidgetSQL(reqID, sql, "", "table", question, "zero_rows")
	if rerr != nil {
		log.Printf("query: zero-row repair call failed: %v", rerr)
		// Return original zero-row result without a curation entry (repair wasn't possible).
		return sql, result, nil, nil
	}

	// If the AI returned the SQL unchanged, zero rows is genuinely correct.
	if strings.TrimSpace(corrected) == strings.TrimSpace(sql) {
		log.Printf("query: zero-row correction: AI confirms zero rows is correct")
		return sql, result, nil, nil
	}

	// Validate the corrected SQL before executing.
	if verr := validateRawSQL(corrected); verr != nil {
		log.Printf("query: zero-row corrected SQL failed safety check: %v", verr)
		return sql, result, nil, nil
	}

	emitStage("zero_rows_retry", "")
	newResult, execErr := h.queryClient.Execute(reqID, corrected)
	if execErr == nil && newResult.RowCount > 0 {
		log.Printf("query: zero-row correction produced %d rows", newResult.RowCount)
		return corrected, newResult, nil, nil
	}

	// Correction didn't help — return original result and queue for curation.
	log.Printf("query: zero-row correction did not improve result (err=%v)", execErr)
	if h.curationSvc != nil {
		h.curationSvc.Add("zero_rows_unresolved", question, "Query returned zero rows and auto-correction did not help", reqID, conversationID)
	}
	return sql, result, nil, nil
}

// ── validateQueryPlan ─────────────────────────────────────────

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

// firstLine returns the first line of a multi-line string (or the whole string
// if it has no newlines). Used to keep stage event detail terse.
func firstLine(s string) string {
	if idx := strings.IndexByte(s, '\n'); idx >= 0 {
		return s[:idx]
	}
	return s
}

func errorf(format string, args ...interface{}) error {
	return fmt.Errorf(format, args...)
}
