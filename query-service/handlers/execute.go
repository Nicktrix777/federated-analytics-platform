package handlers

import (
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"github.com/federated-analytics/query-service/models"
	trinoclient "github.com/federated-analytics/query-service/trino"
)

// ExecuteHandler runs validated SQL against Trino.
//
// This handler performs a SECOND validation layer (defense in depth).
// The Core API already validated the SQL, but we validate again here
// because the Query Service is the last gate before actual execution.

type ExecuteHandler struct {
	trino *trinoclient.Client
}

func NewExecuteHandler(trino *trinoclient.Client) *ExecuteHandler {
	return &ExecuteHandler{trino: trino}
}

func (h *ExecuteHandler) HandleExecute(c *gin.Context) {
	var req models.ExecuteRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{Error: err.Error()})
		return
	}

	// Second-layer validation: only SELECT/WITH allowed
	if err := validateSQL(req.SQL); err != nil {
		c.JSON(http.StatusBadRequest, models.ErrorResponse{
			Error:   "SQL validation failed",
			Details: err.Error(),
		})
		return
	}

	start := time.Now()

	columns, rows, err := h.trino.Execute(req.SQL)
	if err != nil {
		c.JSON(http.StatusBadGateway, models.ErrorResponse{
			Error:   "Trino query execution failed",
			Details: err.Error(),
		})
		return
	}

	elapsed := time.Since(start).Milliseconds()

	if rows == nil {
		rows = [][]interface{}{}
	}

	c.JSON(http.StatusOK, models.ExecuteResponse{
		Columns:         columns,
		Rows:            rows,
		RowCount:        len(rows),
		ExecutionTimeMs: elapsed,
	})
}

func validateSQL(sql string) error {
	normalized := strings.TrimSpace(strings.ToUpper(sql))
	forbidden := []string{"INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE ", "ALTER ", "GRANT ", "REVOKE "}
	for _, kw := range forbidden {
		if strings.Contains(normalized, kw) {
			return fmt.Errorf("forbidden keyword: %s", kw)
		}
	}
	if !strings.HasPrefix(normalized, "SELECT") && !strings.HasPrefix(normalized, "WITH") {
		return fmt.Errorf("only SELECT or CTE (WITH) queries are allowed")
	}
	return nil
}
