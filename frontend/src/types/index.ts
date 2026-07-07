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

export interface QueryResponse {
  request_id: string;
  question: string;
  mode: "ai" | "sql";
  plan?: QueryPlan;
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
export type QueryStatus = "idle" | "loading" | "success" | "error";

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

// Response from the AI generate/refine endpoints
export interface AIDashboardResponse {
  dashboard: Dashboard;
  explanation: string;
  confidence: number;
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
