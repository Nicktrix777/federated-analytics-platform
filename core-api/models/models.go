package models

import (
	"time"

	"github.com/google/uuid"
)

// ──────────────────────────────────────────────────────────
// Query Request — from Frontend to Core API
// ──────────────────────────────────────────────────────────

type QueryRequest struct {
	Question string `json:"question" binding:"required"`
	Mode     string `json:"mode" binding:"required,oneof=ai sql"` // "ai" or "sql"
}

// ──────────────────────────────────────────────────────────
// Query Plan — produced by AI Engine, consumed by Query Service
// This is the central artifact that crosses the AI/deterministic boundary.
// ──────────────────────────────────────────────────────────

type QueryStep struct {
	StepID      int    `json:"step_id"`
	Description string `json:"description"`
	Catalog     string `json:"catalog"`     // Trino catalog, e.g. "postgres_source"
	SchemaName  string `json:"schema_name"` // e.g. "public"
	Table       string `json:"table"`       // e.g. "orders"
}

type QueryPlan struct {
	Question    string      `json:"question"`
	SQL         string      `json:"sql"`         // The Trino SQL to execute
	Steps       []QueryStep `json:"steps"`       // Reasoning steps
	Confidence  float64     `json:"confidence"`  // 0.0 to 1.0
	Explanation string      `json:"explanation"` // Human-readable explanation
}

// ──────────────────────────────────────────────────────────
// Execute Request — Core API to Query Service
// ──────────────────────────────────────────────────────────

type ExecuteRequest struct {
	SQL string `json:"sql" binding:"required"`
}

// ──────────────────────────────────────────────────────────
// Execute Response — from Query Service
// ──────────────────────────────────────────────────────────

type ExecuteResponse struct {
	Columns         []string        `json:"columns"`
	Rows            [][]interface{} `json:"rows"`
	RowCount        int             `json:"row_count"`
	ExecutionTimeMs int64           `json:"execution_time_ms"`
}

// ──────────────────────────────────────────────────────────
// Query Response — from Core API to Frontend
// Contains both the plan (for transparency) and results
// ──────────────────────────────────────────────────────────

type QueryResponse struct {
	RequestID       string          `json:"request_id"`
	Question        string          `json:"question"`
	Mode            string          `json:"mode"`
	Plan            *QueryPlan      `json:"plan,omitempty"`
	Columns         []string        `json:"columns"`
	Rows            [][]interface{} `json:"rows"`
	RowCount        int             `json:"row_count"`
	ExecutionTimeMs int64           `json:"execution_time_ms"`
	AIEnabled       bool            `json:"ai_enabled"`
}

// ──────────────────────────────────────────────────────────
// Audit Log — stored in postgres-meta
// ──────────────────────────────────────────────────────────

type AuditLog struct {
	ID           int64      `json:"id"`
	RequestID    uuid.UUID  `json:"request_id"`
	UserToken    string     `json:"user_token"`
	Question     string     `json:"question"`
	Mode         string     `json:"mode"`
	QueryPlan    *QueryPlan `json:"query_plan,omitempty"`
	SQLExecuted  string     `json:"sql_executed"`
	Status       string     `json:"status"` // "pending", "success", "error"
	ErrorMessage string     `json:"error_message,omitempty"`
	RowCount     int        `json:"row_count"`
	DurationMs   int64      `json:"duration_ms"`
	CreatedAt    time.Time  `json:"created_at"`
}

// ──────────────────────────────────────────────────────────
// Dataset Metadata — read from postgres-meta, returned to frontend
// ──────────────────────────────────────────────────────────

type DatasetColumn struct {
	ColumnName   string `json:"column_name"`
	DataType     string `json:"data_type"`
	Description  string `json:"description"`
	IsJoinable   bool   `json:"is_joinable"`
	SampleValues string `json:"sample_values,omitempty"`
}

type DatasetMeta struct {
	ID          int             `json:"id"`
	Name        string          `json:"name"`
	Description string          `json:"description"`
	SourceType  string          `json:"source_type"`
	TrinoPath   string          `json:"trino_path"` // catalog.schema.table
	Columns     []DatasetColumn `json:"columns"`
}

// ──────────────────────────────────────────────────────────
// Error Response
// ──────────────────────────────────────────────────────────

type ErrorResponse struct {
	Error   string `json:"error"`
	Details string `json:"details,omitempty"`
}
