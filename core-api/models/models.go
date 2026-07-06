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
	Mode     string `json:"mode" binding:"required,oneof=ai sql"`
}

// ──────────────────────────────────────────────────────────
// Query Plan — produced by AI Engine, consumed by Query Service
// ──────────────────────────────────────────────────────────

type QueryStep struct {
	StepID      int    `json:"step_id"`
	Description string `json:"description"`
	Catalog     string `json:"catalog"`
	SchemaName  string `json:"schema_name"`
	Table       string `json:"table"`
}

type QueryPlan struct {
	Question    string      `json:"question"`
	SQL         string      `json:"sql"`
	Steps       []QueryStep `json:"steps"`
	Confidence  float64     `json:"confidence"`
	Explanation string      `json:"explanation"`
}

// ──────────────────────────────────────────────────────────
// Execute Request / Response
// ──────────────────────────────────────────────────────────

type ExecuteRequest struct {
	SQL string `json:"sql" binding:"required"`
}

type ExecuteResponse struct {
	Columns         []string        `json:"columns"`
	Rows            [][]interface{} `json:"rows"`
	RowCount        int             `json:"row_count"`
	ExecutionTimeMs int64           `json:"execution_time_ms"`
}

// ──────────────────────────────────────────────────────────
// Query Response — Core API to Frontend
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
// Audit Log
// ──────────────────────────────────────────────────────────

type AuditLog struct {
	ID           int64      `json:"id"`
	RequestID    uuid.UUID  `json:"request_id"`
	UserToken    string     `json:"user_token"`
	Question     string     `json:"question"`
	Mode         string     `json:"mode"`
	QueryPlan    *QueryPlan `json:"query_plan,omitempty"`
	SQLExecuted  string     `json:"sql_executed"`
	Status       string     `json:"status"`
	ErrorMessage string     `json:"error_message,omitempty"`
	RowCount     int        `json:"row_count"`
	DurationMs   int64      `json:"duration_ms"`
	CreatedAt    time.Time  `json:"created_at"`
}

// ──────────────────────────────────────────────────────────
// Dataset Metadata
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
	TrinoPath   string          `json:"trino_path"`
	Columns     []DatasetColumn `json:"columns"`
}

// ──────────────────────────────────────────────────────────
// Data Sources — registered external connections (NEW)
// ──────────────────────────────────────────────────────────

type DataSource struct {
	ID                 int        `json:"id"`
	Name               string     `json:"name"`
	SourceType         string     `json:"source_type"`
	Host               string     `json:"host"`
	Port               int        `json:"port"`
	DatabaseName       string     `json:"database_name"`
	Username           string     `json:"username,omitempty"`
	ExtraConfig        string     `json:"extra_config,omitempty"` // JSON
	TrinoCatalog       string     `json:"trino_catalog"`
	IsActive           bool       `json:"is_active"`
	SchemaCache        string     `json:"schema_cache,omitempty"` // JSON
	LastSchemaRefresh  *time.Time `json:"last_schema_refresh,omitempty"`
	CreatedAt          time.Time  `json:"created_at"`
	UpdatedAt          time.Time  `json:"updated_at"`
}

type CreateDataSourceRequest struct {
	Name         string `json:"name" binding:"required"`
	SourceType   string `json:"source_type" binding:"required,oneof=postgresql mongodb elasticsearch mysql trino"`
	Host         string `json:"host" binding:"required"`
	Port         int    `json:"port" binding:"required"`
	DatabaseName string `json:"database_name"`
	Username     string `json:"username"`
	Password     string `json:"password"`
	TrinoCatalog string `json:"trino_catalog" binding:"required"`
	ExtraConfig  string `json:"extra_config"`
}

type UpdateDataSourceRequest struct {
	Name         string `json:"name"`
	Host         string `json:"host"`
	Port         int    `json:"port"`
	DatabaseName string `json:"database_name"`
	Username     string `json:"username"`
	Password     string `json:"password"`
	IsActive     *bool  `json:"is_active"`
	ExtraConfig  string `json:"extra_config"`
}

type SchemaRefreshResult struct {
	DataSourceID int    `json:"data_source_id"`
	TrinoCatalog string `json:"trino_catalog"`
	TablesFound  int    `json:"tables_found"`
	Message      string `json:"message"`
}

// ──────────────────────────────────────────────────────────
// Dashboards (NEW)
// ──────────────────────────────────────────────────────────

type Dashboard struct {
	ID          int               `json:"id"`
	Name        string            `json:"name"`
	Description string            `json:"description"`
	Layout      string            `json:"layout"` // JSON
	IsActive    bool              `json:"is_active"`
	Widgets     []DashboardWidget `json:"widgets,omitempty"`
	CreatedAt   time.Time         `json:"created_at"`
	UpdatedAt   time.Time         `json:"updated_at"`
}

type DashboardWidget struct {
	ID            int       `json:"id"`
	DashboardID   int       `json:"dashboard_id"`
	Title         string    `json:"title"`
	QuerySQL      string    `json:"query_sql"`
	ChartType     string    `json:"chart_type"`
	ChartConfig   string    `json:"chart_config"`   // JSON
	GridPosition  string    `json:"grid_position"`  // JSON
	RefreshRateMs int       `json:"refresh_rate_ms"`
	CreatedAt     time.Time `json:"created_at"`
	UpdatedAt     time.Time `json:"updated_at"`
}

type CreateDashboardRequest struct {
	Name        string `json:"name" binding:"required"`
	Description string `json:"description"`
	Layout      string `json:"layout"`
}

type UpdateDashboardRequest struct {
	Name        string `json:"name"`
	Description string `json:"description"`
	Layout      string `json:"layout"`
}

type CreateWidgetRequest struct {
	Title         string `json:"title" binding:"required"`
	QuerySQL      string `json:"query_sql" binding:"required"`
	ChartType     string `json:"chart_type"`
	ChartConfig   string `json:"chart_config"`
	GridPosition  string `json:"grid_position"`
	RefreshRateMs int    `json:"refresh_rate_ms"`
}

type UpdateWidgetRequest struct {
	Title         string `json:"title"`
	QuerySQL      string `json:"query_sql"`
	ChartType     string `json:"chart_type"`
	ChartConfig   string `json:"chart_config"`
	GridPosition  string `json:"grid_position"`
	RefreshRateMs *int   `json:"refresh_rate_ms"`
}

// ──────────────────────────────────────────────────────────
// Error Response
// ──────────────────────────────────────────────────────────

type ErrorResponse struct {
	Error   string `json:"error"`
	Details string `json:"details,omitempty"`
}
