// ── Shared Types — Federated Analytics Platform v2 ───────────

// ── Data Sources ─────────────────────────────────────────────

export interface DataSource {
  id: number;
  name: string;
  source_type: "postgresql" | "mongodb" | "elasticsearch" | "mysql" | "trino";
  host: string;
  port: number;
  database_name: string;
  username?: string;
  extra_config?: string;
  trino_catalog: string;
  is_active: boolean;
  schema_cache?: string;
  last_schema_refresh?: string;
  created_at: string;
  updated_at: string;
}

export interface CreateDataSourcePayload {
  name: string;
  source_type: string;
  host: string;
  port: number;
  database_name: string;
  username?: string;
  password?: string;
  trino_catalog: string;
  extra_config?: string;
}

export interface SchemaRefreshResult {
  data_source_id: number;
  trino_catalog: string;
  tables_found: number;
  message: string;
}

export interface SyncCatalogsResult {
  new_sources: string[];
  new_datasets: string[];
  inferred_relationships: number;
  message: string;
}

// ── Datasets ─────────────────────────────────────────────────

export interface DatasetColumn {
  column_name: string;
  data_type: string;
  description: string;
  is_joinable: boolean;
  sample_values?: string;
}

export interface DatasetMeta {
  id: number;
  name: string;
  description: string;
  source_type: string;
  trino_path: string;
  columns: DatasetColumn[];
}

// ── Query ─────────────────────────────────────────────────────

export interface QueryStep {
  step_id: number;
  description: string;
  catalog: string;
  schema_name: string;
  table: string;
}

export interface QueryPlan {
  question: string;
  sql: string;
  steps: QueryStep[];
  confidence: number;
  explanation: string;
}

export interface Clarification {
  question: string;
  options: string[];
  kind: string;
}

export interface QueryResponse {
  request_id: string;
  question: string;
  mode: "ai" | "sql";
  plan?: QueryPlan;
  clarification?: Clarification;
  columns: string[];
  rows: unknown[][];
  row_count: number;
  execution_time_ms: number;
  ai_enabled: boolean;
}

export interface HistoryEntry {
  id: number;
  request_id: string;
  question: string;
  mode: string;
  status: string;
  row_count: number;
  duration_ms: number;
  created_at: string;
}

export type QueryMode = "ai" | "sql";
export type QueryStatus = "idle" | "loading" | "success" | "error" | "clarification";

// ── Conversations / Transcript (PR7) ─────────────────────────

// One entry in the sidebar "Chats" list.
export interface ConversationSummary {
  id: string;
  title: string;
  turn_count: number;
  created_at: string;
  last_active_at: string;
}

// A hydrated turn returned by GET /api/conversations/:id/turns. Result rows are
// not persisted, so a plan turn only carries sql/row_count/confidence.
export interface ConversationTurnDTO {
  id: number;
  question: string;
  kind: "plan" | "clarification";
  sql?: string;
  row_count: number;
  confidence?: number;
  clarification?: Clarification;
  created_at: string;
}

// The outcome of a single transcript turn, mirroring the terminal SSE event.
export type TurnOutcome =
  | { type: "result"; result: QueryResponse; hydrated?: boolean }
  | { type: "clarification"; clarification: Clarification; answered: boolean }
  | { type: "error"; message: string };

// Client-side transcript state — one entry per exchange, mirroring
// conversation_turns. Each turn owns its own progress events so timelines
// don't bleed across turns.
export interface TranscriptTurn {
  id: string;
  userText: string;
  userKind: "question" | "answer";
  mode: QueryMode;
  progress: AIProgressEvent[];
  status: "streaming" | "done" | "error";
  outcome?: TurnOutcome;
}

// ── AI streaming progress (SSE) ──────────────────────────────
// One progress event received on a /stream endpoint. `type` is the SSE
// event name ("stage" | "llm" | "tool" | "widget" | "sheet" | future
// types) and `data` its JSON payload — see docs/sse-events.md.

export interface AIProgressEventData {
  stage?: string;
  detail?: string;
  phase?: "start" | "end";
  agent?: string;
  model?: string;
  tool?: string;
  duration_ms?: number;
  input_tokens?: number;
  output_tokens?: number;
  title?: string;
  status?: string;
  attempt?: number;
  elapsed_ms?: number;
  request_id?: string;
  [key: string]: unknown;
}

export interface AIProgressEvent {
  type: string;
  data: AIProgressEventData;
  /** Client-side receive time (Date.now()). */
  ts: number;
}

// ── Dashboards ───────────────────────────────────────────────

export type ChartType =
  | "table"
  | "bar"
  | "line"
  | "pie"
  | "area"
  | "scatter"
  | "number"
  | "gauge";

export interface GridPosition {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface DashboardWidget {
  id: number;
  dashboard_id: number;
  title: string;
  query_sql: string;
  chart_type: ChartType;
  chart_config: string; // JSON
  grid_position: string; // JSON GridPosition
  refresh_rate_ms: number;
  created_at: string;
  updated_at: string;
}

export interface Dashboard {
  id: number;
  name: string;
  description: string;
  layout: string; // JSON
  is_active: boolean;
  widgets?: DashboardWidget[];
  created_at: string;
  updated_at: string;
}

export interface CreateDashboardPayload {
  name: string;
  description?: string;
  layout?: string;
}

// A widget the AI proposed but could not add — its SQL never ran successfully
// against the data source, even after repair attempts.
export interface DroppedWidget {
  title: string;
  reason: string;
}

// Response from the AI generate/refine endpoints
export interface AIDashboardResponse {
  dashboard: Dashboard;
  explanation: string;
  confidence: number;
  dropped_widgets: DroppedWidget[] | null;
}

export interface CreateWidgetPayload {
  title: string;
  query_sql: string;
  chart_type: ChartType;
  chart_config?: string;
  grid_position?: string;
  refresh_rate_ms?: number;
}

export interface UpdateWidgetPayload {
  title?: string;
  query_sql?: string;
  chart_type?: ChartType;
  chart_config?: string;
  grid_position?: string;
  refresh_rate_ms?: number;
}

// ── Reports ──────────────────────────────────────────────────

// Excel column-format vocabulary — shared with the AI engine and the
// core-api exporter.
export type ColumnFormat =
  | "text"
  | "integer"
  | "number"
  | "currency"
  | "percent"
  | "date"
  | "datetime";

export interface ReportSheet {
  id: number;
  report_id: number;
  title: string;
  description: string;
  query_sql: string;
  column_formats: string; // JSON {column_name: ColumnFormat}
  position: number;
  max_rows: number;
  created_at: string;
  updated_at: string;
}

export interface Report {
  id: number;
  name: string;
  description: string;
  is_active: boolean;
  sheets?: ReportSheet[];
  created_at: string;
  updated_at: string;
}

export interface CreateReportPayload {
  name: string;
  description?: string;
}

// A sheet the AI proposed but could not add — its SQL never ran successfully
// against the data source, even after repair attempts.
export interface DroppedSheet {
  title: string;
  reason: string;
}

// Response from the AI generate/refine endpoints
export interface AIReportResponse {
  report: Report;
  explanation: string;
  confidence: number;
  dropped_sheets: DroppedSheet[] | null;
}

export interface CreateSheetPayload {
  title: string;
  description?: string;
  query_sql: string;
  column_formats?: string;
  position?: number;
  max_rows?: number;
}

export interface UpdateSheetPayload {
  title?: string;
  description?: string;
  query_sql?: string;
  column_formats?: string;
  position?: number;
  max_rows?: number;
}

// ── LLM Settings (runtime-editable, non-secret) ───────────────
// Every model field is a provider-prefixed string, e.g. "openai:gpt-4o",
// "anthropic:claude-sonnet-5", "google_genai:gemini-flash-latest". API keys are
// NOT part of this — they stay in the AI Engine's environment.
export interface LLMSettingsConfig {
  llm_model: string;
  sql_generator_model: string;
  schema_analyst_model: string;
  fast_path_model: string;
  dashboard_widget_sql_model: string;
  embedding_model: string;
  llm_base_url: string;
  fast_path_enabled: boolean;
  fast_path_confidence_threshold: number;
  llm_frontier_rpm: number;
  llm_fast_rpm: number;
  llm_embed_rpm: number;
}

export interface LLMSettingsResponse {
  config: LLMSettingsConfig;
  // Which provider API keys are configured (booleans only — never the values).
  provider_keys_present: Record<string, boolean>;
  known_providers: string[];
  version: number | null;
}
