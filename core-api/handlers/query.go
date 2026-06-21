package handlers

import (
	"fmt"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"

	"github.com/federated-analytics/core-api/middleware"
	"github.com/federated-analytics/core-api/models"
	"github.com/federated-analytics/core-api/services"
)

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
	aiClient    *services.AIClient
	queryClient *services.QueryClient
	metadataSvc *services.MetadataService
	aiEnabled   bool
}

func NewQueryHandler(
	aiClient *services.AIClient,
	queryClient *services.QueryClient,
	metadataSvc *services.MetadataService,
	aiEnabled bool,
) *QueryHandler {
	return &QueryHandler{
		aiClient:    aiClient,
		queryClient: queryClient,
		metadataSvc: metadataSvc,
		aiEnabled:   aiEnabled,
	}
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

	requestID, _ := c.Get(middleware.AuditRequestIDKey)
	reqIDStr := ""
	if rid, ok := requestID.(uuid.UUID); ok {
		reqIDStr = rid.String()
	}

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

		// Fetch schema metadata to provide context to the AI Engine
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

		// Call AI Engine — it produces a QueryPlan, never executes anything
		generatedPlan, err := h.aiClient.GeneratePlan(req.Question, datasets)
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

	// Execute the validated SQL via the Query Service
	result, err := h.queryClient.Execute(sqlToExecute)
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
