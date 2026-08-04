import axios from "axios";
import type {
  QueryResponse,
  HistoryEntry,
  ConversationSummary,
  ConversationTurnDTO,
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
  Report,
  ReportSheet,
  CreateReportPayload,
  CreateSheetPayload,
  UpdateSheetPayload,
  AIReportResponse,
  LLMSettingsConfig,
  LLMSettingsResponse,
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

  // ── Conversations (PR7 — "Chats" sidebar tab) ──────────────
  getConversations: async (limit = 30): Promise<ConversationSummary[]> => {
    const response = await client.get<{
      conversations: ConversationSummary[];
      count: number;
    }>(`/api/conversations?limit=${limit}`);
    return response.data.conversations;
  },

  getConversationTurns: async (
    id: string
  ): Promise<ConversationTurnDTO[]> => {
    const response = await client.get<{
      turns: ConversationTurnDTO[];
      count: number;
    }>(`/api/conversations/${id}/turns`);
    return response.data.turns;
  },

};

// ── LLM Settings API ──────────────────────────────────────────

export const llmSettingsApi = {
  get: async (): Promise<LLMSettingsResponse> => {
    const response = await client.get<LLMSettingsResponse>("/api/llm-settings");
    return response.data;
  },

  // Sends only the editable fields; the AI Engine validates + hot-reloads. A
  // validation error surfaces as a 400 (axios throws) with detail in the body.
  update: async (
    config: Partial<LLMSettingsConfig>
  ): Promise<LLMSettingsResponse & { status: string }> => {
    const response = await client.put<LLMSettingsResponse & { status: string }>(
      "/api/llm-settings",
      { config }
    );
    return response.data;
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

// ── Reports API ───────────────────────────────────────────────

export const reportsApi = {
  list: async (): Promise<Report[]> => {
    const response = await client.get<{ reports: Report[] }>("/api/reports");
    return response.data.reports;
  },

  create: async (payload: CreateReportPayload): Promise<Report> => {
    const response = await client.post<Report>("/api/reports", payload);
    return response.data;
  },

  // AI: design a full report from a natural-language brief.
  // The multi-agent pipeline can take a while — allow up to 5 minutes.
  generate: async (prompt: string): Promise<AIReportResponse> => {
    const response = await client.post<AIReportResponse>(
      "/api/reports/generate",
      { prompt },
      { timeout: 300000 }
    );
    return response.data;
  },

  // AI: apply a natural-language instruction to an existing report.
  refine: async (
    id: number,
    instruction: string
  ): Promise<AIReportResponse> => {
    const response = await client.post<AIReportResponse>(
      `/api/reports/${id}/refine`,
      { instruction },
      { timeout: 300000 }
    );
    return response.data;
  },

  get: async (id: number): Promise<Report> => {
    const response = await client.get<Report>(`/api/reports/${id}`);
    return response.data;
  },

  update: async (
    id: number,
    payload: Partial<CreateReportPayload>
  ): Promise<Report> => {
    const response = await client.put<Report>(`/api/reports/${id}`, payload);
    return response.data;
  },

  delete: async (id: number): Promise<void> => {
    await client.delete(`/api/reports/${id}`);
  },

  // Download the report as a formatted .xlsx. The endpoint needs the auth
  // header, so a plain <a href> won't do — fetch the bytes as a blob and
  // trigger the download from an object URL. The exporter runs every
  // sheet's SQL live under a 360s server deadline — allow a bit more.
  download: async (id: number): Promise<void> => {
    const response = await client.get<Blob>(`/api/reports/${id}/download`, {
      responseType: "blob",
      timeout: 400000,
    });

    const disposition = String(
      response.headers["content-disposition"] ?? ""
    );
    const match = /filename="?([^";]+)"?/i.exec(disposition);
    const filename = match?.[1] || `report-${id}.xlsx`;

    const url = URL.createObjectURL(response.data);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  },

  createSheet: async (
    reportId: number,
    payload: CreateSheetPayload
  ): Promise<ReportSheet> => {
    const response = await client.post<ReportSheet>(
      `/api/reports/${reportId}/sheets`,
      payload
    );
    return response.data;
  },

  updateSheet: async (
    reportId: number,
    sheetId: number,
    payload: UpdateSheetPayload
  ): Promise<ReportSheet> => {
    const response = await client.put<ReportSheet>(
      `/api/reports/${reportId}/sheets/${sheetId}`,
      payload
    );
    return response.data;
  },

  deleteSheet: async (reportId: number, sheetId: number): Promise<void> => {
    await client.delete(`/api/reports/${reportId}/sheets/${sheetId}`);
  },
};
