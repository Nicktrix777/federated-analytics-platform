package handlers

import (
	"database/sql"
	"fmt"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
)

type HistoryHandler struct {
	db *sql.DB
}

func NewHistoryHandler(db *sql.DB) *HistoryHandler {
	return &HistoryHandler{db: db}
}

type HistoryEntry struct {
	ID         int64     `json:"id"`
	RequestID  string    `json:"request_id"`
	Question   string    `json:"question"`
	Mode       string    `json:"mode"`
	Status     string    `json:"status"`
	RowCount   int       `json:"row_count"`
	DurationMs int64     `json:"duration_ms"`
	CreatedAt  time.Time `json:"created_at"`
}

func (h *HistoryHandler) HandleHistory(c *gin.Context) {
	limit := 20
	if l := c.Query("limit"); l != "" {
		if parsed, err := strconv.Atoi(l); err == nil && parsed > 0 && parsed <= 100 {
			limit = parsed
		}
	}

	rows, err := h.db.Query(`
		SELECT id, request_id::text, question, mode, status,
		       COALESCE(row_count, 0), COALESCE(duration_ms, 0), created_at
		FROM audit_logs
		ORDER BY created_at DESC
		LIMIT $1
	`, limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": fmt.Sprintf("Failed to fetch history: %v", err)})
		return
	}
	defer rows.Close()

	var entries []HistoryEntry
	for rows.Next() {
		var e HistoryEntry
		if err := rows.Scan(&e.ID, &e.RequestID, &e.Question, &e.Mode, &e.Status,
			&e.RowCount, &e.DurationMs, &e.CreatedAt); err != nil {
			continue
		}
		entries = append(entries, e)
	}

	if entries == nil {
		entries = []HistoryEntry{}
	}

	c.JSON(http.StatusOK, gin.H{"history": entries, "count": len(entries)})
}
