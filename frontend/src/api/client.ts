import axios from "axios";
import type {
  QueryResponse,
  HistoryEntry,
  DatasetMeta,
  DataSource,
  CreateDataSourcePayload,
  SchemaRefreshResult,
  SyncCatalogsResult,
  Dashboard,
  DashboardWidget,
  CreateDashboardPayload,
  CreateWidgetPayload,
  UpdateWidgetPayload,
  AIDashboardResponse,
} from "../types";

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";
export const API_TOKEN =
  import.meta.env.VITE_API_TOKEN || "poc-demo-token-2024";

const client = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${API_TOKEN}`,
  },
  timeout: 120000,
});

// ── Query API ─────────────────────────────────────────────────

export const api = {
  query: async (
    question: string,
    mode: "ai" | "sql",
    conversationId?: string
  ): Promise<QueryResponse> => {
    const response = await client.post<QueryResponse>(
      "/api/query",
      { question, mode, conversation_id: conversationId },
      // The core-api waits up to 5 minutes for the AI engine in AI mode —
      // the 120s instance default would abort the request too early.
      { timeout: mode === "ai" ? 300000 : undefined }
    );
    return response.data;
  },

  getHistory: async (limit = 20): Promise<HistoryEntry[]> => {
    const response = await client.get<{
      history: HistoryEntry[];
      count: number;
    }>(`/api/history?limit=${limit}`);
    return response.data.history;
  },

  getDatasets: async (): Promise<DatasetMeta[]> => {
    const response = await client.get<{ datasets: DatasetMeta[] }>(
      "/api/metadata/datasets"
    );
    return response.data.datasets;
  },

};

// ── Data Sources API ──────────────────────────────────────────

export const dataSourcesApi = {
  list: async (): Promise<DataSource[]> => {
    const response = await client.get<{ data_sources: DataSource[] }>(
      "/api/datasources"
    );
    return response.data.data_sources;
  },

  create: async (
    payload: CreateDataSourcePayload
  ): Promise<DataSource> => {
    const response = await client.post<DataSource>(
      "/api/datasources",
      payload
    );
    return response.data;
  },

  delete: async (id: number): Promise<void> => {
    await client.delete(`/api/datasources/${id}`);
  },

  refreshSchema: async (id: number): Promise<SchemaRefreshResult> => {
    const response = await client.post<SchemaRefreshResult>(
      `/api/datasources/${id}/refresh`
    );
    return response.data;
  },

  syncCatalogs: async (): Promise<SyncCatalogsResult> => {
    const response = await client.post<SyncCatalogsResult>(
      "/api/datasources/sync"
    );
    return response.data;
  },
};

// ── Dashboards API ────────────────────────────────────────────

export const dashboardsApi = {
  list: async (): Promise<Dashboard[]> => {
    const response = await client.get<{ dashboards: Dashboard[] }>(
      "/api/dashboards"
    );
    return response.data.dashboards;
  },

  create: async (payload: CreateDashboardPayload): Promise<Dashboard> => {
    const response = await client.post<Dashboard>("/api/dashboards", payload);
    return response.data;
  },

  // AI: design a full dashboard from a natural-language brief.
  // The multi-agent pipeline can take a while — allow up to 5 minutes.
  generate: async (prompt: string): Promise<AIDashboardResponse> => {
    const response = await client.post<AIDashboardResponse>(
      "/api/dashboards/generate",
      { prompt },
      { timeout: 300000 }
    );
    return response.data;
  },

  // AI: apply a natural-language instruction to an existing dashboard.
  refine: async (
    id: number,
    instruction: string
  ): Promise<AIDashboardResponse> => {
    const response = await client.post<AIDashboardResponse>(
      `/api/dashboards/${id}/refine`,
      { instruction },
      { timeout: 300000 }
    );
    return response.data;
  },

  get: async (id: number): Promise<Dashboard> => {
    const response = await client.get<Dashboard>(`/api/dashboards/${id}`);
    return response.data;
  },

  update: async (
    id: number,
    payload: Partial<CreateDashboardPayload>
  ): Promise<Dashboard> => {
    const response = await client.put<Dashboard>(
      `/api/dashboards/${id}`,
      payload
    );
    return response.data;
  },

  delete: async (id: number): Promise<void> => {
    await client.delete(`/api/dashboards/${id}`);
  },

  createWidget: async (
    dashboardId: number,
    payload: CreateWidgetPayload
  ): Promise<DashboardWidget> => {
    const response = await client.post<DashboardWidget>(
      `/api/dashboards/${dashboardId}/widgets`,
      payload
    );
    return response.data;
  },

  updateWidget: async (
    dashboardId: number,
    widgetId: number,
    payload: UpdateWidgetPayload
  ): Promise<DashboardWidget> => {
    const response = await client.put<DashboardWidget>(
      `/api/dashboards/${dashboardId}/widgets/${widgetId}`,
      payload
    );
    return response.data;
  },

  deleteWidget: async (
    dashboardId: number,
    widgetId: number
  ): Promise<void> => {
    await client.delete(
      `/api/dashboards/${dashboardId}/widgets/${widgetId}`
    );
  },
};
