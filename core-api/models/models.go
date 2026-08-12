package models

import (
	"encoding/json"
	"time"
)

// ──────────────────────────────────────────────────────────
// Query Request — from Frontend to Core API
// ──────────────────────────────────────────────────────────

type QueryRequest struct {
	Question string `json:"question" binding:"required"`
	Mode     string `json:"mode" binding:"required,oneof=ai sql"`
	// ConversationID threads multi-turn context. Optional: a well-formed UUID
	// (minted by the frontend per chat session) enables follow-up memory;
	// empty/invalid disables it for this request. AI mode only.
	ConversationID string `json:"conversation_id,omitempty"`
}

// ──────────────────────────────────────────────────────────
// Chat Message — structured transcript unit (PR4)
//
// The Core API assembles these from conversation_turns and sends them to
// the AI Engine, which renders them into prompt text. The frontend never
// sends or sees these — it keeps sending {question, mode, conversation_id}.
// ──────────────────────────────────────────────────────────

type ChatMessage struct {
	Role    string          `json:"role"` // "user" | "assistant"
	Kind    string          `json:"kind"` // "question" | "answer" | "plan" | "clarification"
	Content string          `json:"content"`
	Payload json.RawMessage `json:"payload,omitempty"`
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
	Clarification   *Clarification  `json:"clarification,omitempty"`
	Columns         []string        `json:"columns"`
	Rows            [][]interface{} `json:"rows"`
	RowCount        int             `json:"row_count"`
	ExecutionTimeMs int64           `json:"execution_time_ms"`
	AIEnabled       bool            `json:"ai_enabled"`
}

// ──────────────────────────────────────────────────────────
// Clarification (PR5) — alternative terminal outcome
//
// The AI Engine returns this instead of a QueryPlan when the question
// is genuinely ambiguous. The Core API records it as a clarification
// turn and sends it to the frontend, which renders option buttons.
// ──────────────────────────────────────────────────────────

type Clarification struct {
	Question string   `json:"question"`
	Options  []string `json:"options"`
	Kind     string   `json:"kind"`
}

// ──────────────────────────────────────────────────────────
// Dataset Metadata
// ──────────────────────────────────────────────────────────

type DatasetColumn struct {
	ColumnName  string `json:"column_name"`
	DataType    string `json:"data_type"`
	Description string `json:"description"`
	IsJoinable  bool   `json:"is_joinable"`
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
	ID           int    `json:"id"`
	Name         string `json:"name"`
	SourceType   string `json:"source_type"`
	Host         string `json:"host"`
	Port         int    `json:"port"`
	DatabaseName string `json:"database_name"`
	Username     string `json:"username,omitempty"`
	ExtraConfig  string `json:"extra_config,omitempty"` // JSON
	TrinoCatalog string `json:"trino_catalog"`
	// TrinoSchema distinguishes multiple data_sources rows that share one
	// static Trino catalog (zoho_books, tally: one catalog, one schema per
	// registered customer connection). Empty for source types that still map
	// one catalog to one data_source (postgresql/mongodb/elasticsearch/mysql).
	TrinoSchema string `json:"trino_schema,omitempty"`
	// BridgeToken is set only in Create()'s response for a new tally
	// datasource — a one-time reveal of the generated bridge token so the
	// customer can copy it into their tally-bridge config. Never populated
	// by any read path (GetByID/List never select password_encrypted).
	BridgeToken       string     `json:"bridge_token,omitempty"`
	IsActive          bool       `json:"is_active"`
	SchemaCache       string     `json:"schema_cache,omitempty"` // JSON
	LastSchemaRefresh *time.Time `json:"last_schema_refresh,omitempty"`
	CreatedAt         time.Time  `json:"created_at"`
	UpdatedAt         time.Time  `json:"updated_at"`
}

type CreateDataSourceRequest struct {
	Name       string `json:"name" binding:"required"`
	SourceType string `json:"source_type" binding:"required,oneof=postgresql mongodb elasticsearch mysql trino zoho_books tally"`
	// Host, Port and TrinoCatalog are required for the live JDBC/wire-protocol
	// source types but not for zoho_books/tally (API/XML-backed, resolved by
	// the connector plugin, not by dialing host:port from core-api) — enforced
	// per-type in DataSourceService.Create, not via a blanket binding tag,
	// since Gin's binding tags can't express "required only when type=X".
	Host         string `json:"host"`
	Port         int    `json:"port"`
	DatabaseName string `json:"database_name"`
	Username     string `json:"username"`
	Password     string `json:"password"`
	TrinoCatalog string `json:"trino_catalog"`
	ExtraConfig  string `json:"extra_config"`

	// zoho_books only — a one-time Self Client grant code the customer
	// generates in Zoho's API Console. Create() exchanges it for a refresh
	// token immediately and discards the code; only the resulting refresh
	// token is ever stored (encrypted, alongside ClientID/ClientSecret, as
	// the JSON blob in password_encrypted).
	ClientID       string `json:"client_id"`
	ClientSecret   string `json:"client_secret"`
	GrantCode      string `json:"grant_code"`
	OrganizationID string `json:"organization_id"`
	DataCenter     string `json:"data_center"`
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

// SyncCatalogsResult summarizes what SyncCatalogsFromTrino discovered:
// newly-registered Trino catalogs (data_sources) and newly-registered
// tables/indices (datasets) that weren't previously known to the platform.
type SyncCatalogsResult struct {
	NewSources            []string `json:"new_sources"`
	NewDatasets           []string `json:"new_datasets"`
	InferredRelationships int      `json:"inferred_relationships"`
	Message               string   `json:"message"`
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
	ChartConfig   string    `json:"chart_config"`  // JSON
	GridPosition  string    `json:"grid_position"` // JSON
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
// AI Dashboard Generation
// ──────────────────────────────────────────────────────────

type GenerateDashboardRequest struct {
	Prompt string `json:"prompt" binding:"required"`
}

type RefineDashboardRequest struct {
	Instruction string `json:"instruction" binding:"required"`
}

// WidgetPlan is one AI-proposed widget (SQL not yet persisted or executed).
type WidgetPlan struct {
	Title        string          `json:"title"`
	SQL          string          `json:"sql"`
	ChartType    string          `json:"chart_type"`
	GridPosition json.RawMessage `json:"grid_position"`
	Explanation  string          `json:"explanation,omitempty"`
}

// DroppedWidget records a widget the AI proposed that never made it onto the
// dashboard because its SQL could not be executed, even after repair
// attempts. Surfacing these is what stops a generate/refine call from
// reporting success while quietly discarding what the user asked for.
type DroppedWidget struct {
	Title  string `json:"title"`
	Reason string `json:"reason"`
}

// DashboardPlan is the AI Engine's full dashboard proposal. The Core API
// validates every widget's SQL before any of it reaches the database.
type DashboardPlan struct {
	Name           string          `json:"name"`
	Description    string          `json:"description"`
	Widgets        []WidgetPlan    `json:"widgets"`
	Confidence     float64         `json:"confidence"`
	Explanation    string          `json:"explanation"`
	DroppedWidgets []DroppedWidget `json:"dropped_widgets,omitempty"`
}

// ──────────────────────────────────────────────────────────
// Reports (Excel export)
// ──────────────────────────────────────────────────────────

type Report struct {
	ID          int           `json:"id"`
	Name        string        `json:"name"`
	Description string        `json:"description"`
	IsActive    bool          `json:"is_active"`
	Sheets      []ReportSheet `json:"sheets,omitempty"`
	CreatedAt   time.Time     `json:"created_at"`
	UpdatedAt   time.Time     `json:"updated_at"`
}

type ReportSheet struct {
	ID            int       `json:"id"`
	ReportID      int       `json:"report_id"`
	Title         string    `json:"title"`
	Description   string    `json:"description"`
	QuerySQL      string    `json:"query_sql"`
	ColumnFormats string    `json:"column_formats"` // JSON {column: text|integer|number|currency|percent|date|datetime}
	Position      int       `json:"position"`
	MaxRows       int       `json:"max_rows"`
	CreatedAt     time.Time `json:"created_at"`
	UpdatedAt     time.Time `json:"updated_at"`
}

type CreateReportRequest struct {
	Name        string `json:"name" binding:"required"`
	Description string `json:"description"`
}

type UpdateReportRequest struct {
	Name        string `json:"name"`
	Description string `json:"description"`
}

type CreateSheetRequest struct {
	Title         string `json:"title" binding:"required"`
	QuerySQL      string `json:"query_sql" binding:"required"`
	Description   string `json:"description"`
	ColumnFormats string `json:"column_formats"`
	Position      int    `json:"position"`
	MaxRows       int    `json:"max_rows"`
}

type UpdateSheetRequest struct {
	Title         string `json:"title"`
	QuerySQL      string `json:"query_sql"`
	Description   string `json:"description"`
	ColumnFormats string `json:"column_formats"`
	Position      *int   `json:"position"`
	MaxRows       *int   `json:"max_rows"`
}

// ──────────────────────────────────────────────────────────
// AI Report Generation
// ──────────────────────────────────────────────────────────

type GenerateReportRequest struct {
	Prompt string `json:"prompt" binding:"required"`
}

type RefineReportRequest struct {
	Instruction string `json:"instruction" binding:"required"`
}

// SheetPlan is one AI-proposed report sheet (SQL not yet persisted or executed).
type SheetPlan struct {
	Title         string            `json:"title"`
	Description   string            `json:"description"`
	SQL           string            `json:"sql"`
	ColumnFormats map[string]string `json:"column_formats"`
	Position      int               `json:"position"`
}

// DroppedSheet records a sheet the AI proposed that never made it into the
// report because its SQL could not be executed, even after repair attempts.
// Same rationale as DroppedWidget.
type DroppedSheet struct {
	Title  string `json:"title"`
	Reason string `json:"reason"`
}

// ReportPlan is the AI Engine's full report proposal. The Core API validates
// every sheet's SQL before any of it reaches the database.
type ReportPlan struct {
	Name          string         `json:"name"`
	Description   string         `json:"description"`
	Sheets        []SheetPlan    `json:"sheets"`
	Confidence    float64        `json:"confidence"`
	Explanation   string         `json:"explanation"`
	DroppedSheets []DroppedSheet `json:"dropped_sheets,omitempty"`
}

// ──────────────────────────────────────────────────────────────
// Curation Queue (PR6) — items needing operator attention
//
// Populated when the query repair loop exhausts all attempts or when
// a zero-row result cannot be auto-corrected. v1: list-only (no UI,
// no resolve endpoint — flip status via SQL).
// ──────────────────────────────────────────────────────────────

type CurationItem struct {
	ID             int       `json:"id"`
	Kind           string    `json:"kind"`
	DatasetID      *int      `json:"dataset_id,omitempty"`
	ColumnName     string    `json:"column_name,omitempty"`
	Question       string    `json:"question,omitempty"`
	Detail         string    `json:"detail,omitempty"`
	Status         string    `json:"status"`
	RequestID      string    `json:"request_id,omitempty"`
	ConversationID string    `json:"conversation_id,omitempty"`
	CreatedAt      time.Time `json:"created_at"`
}

// ──────────────────────────────────────────────────────────────
// Error Response
// ──────────────────────────────────────────────────────────────

type ErrorResponse struct {
	Error   string `json:"error"`
	Details string `json:"details,omitempty"`
}
