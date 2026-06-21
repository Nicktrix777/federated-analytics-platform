import axios from "axios";
import type { QueryResponse, HistoryEntry, DatasetMeta } from "../types";

// The frontend ONLY talks to the Core API.
// It never directly contacts the AI Engine, Query Service, Trino, or any database.
const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8081";
const API_TOKEN = import.meta.env.VITE_API_TOKEN || "poc-demo-token-2024";

const client = axios.create({
  baseURL: BASE_URL,
  headers: {
    "Content-Type": "application/json",
    Authorization: `Bearer ${API_TOKEN}`,
  },
  timeout: 120000, // 2 minutes for slow federated queries
});

// ── API Methods ───────────────────────────────────────────────

export const api = {
  /** Submit a natural language or raw SQL query */
  query: async (
    question: string,
    mode: "ai" | "sql",
  ): Promise<QueryResponse> => {
    const response = await client.post<QueryResponse>("/api/query", {
      question,
      mode,
    });
    return response.data;
  },

  /** Fetch query history from audit log */
  getHistory: async (limit = 20): Promise<HistoryEntry[]> => {
    const response = await client.get<{
      history: HistoryEntry[];
      count: number;
    }>(`/api/history?limit=${limit}`);
    return response.data.history;
  },

  /** Fetch available dataset metadata */
  getDatasets: async (): Promise<DatasetMeta[]> => {
    const response = await client.get<{ datasets: DatasetMeta[] }>(
      "/api/metadata/datasets",
    );
    return response.data.datasets;
  },

  /** Health check */
  health: async (): Promise<{ status: string; ai_enabled: boolean }> => {
    const response = await client.get("/api/health");
    return response.data;
  },
};

export default client;
