// ── Shared Types ─────────────────────────────────────────────

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
