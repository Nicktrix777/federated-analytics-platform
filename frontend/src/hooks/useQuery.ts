import { useState, useCallback } from "react";
import { api } from "../api/client";
import { streamAIOperation, SSEConnectionError } from "../api/sse";
import type {
  QueryResponse,
  HistoryEntry,
  QueryMode,
  QueryStatus,
  AIProgressEvent,
  AIProgressEventData,
} from "../types";

interface QueryState {
  status: QueryStatus;
  result: QueryResponse | null;
  error: string | null;
  history: HistoryEntry[];
  /** SSE progress events of the current/last AI query (empty in SQL mode). */
  progress: AIProgressEvent[];
}

export function useQuery() {
  const [state, setState] = useState<QueryState>({
    status: "idle",
    result: null,
    error: null,
    history: [],
    progress: [],
  });

  const loadHistory = useCallback(async () => {
    try {
      const history = await api.getHistory(30);
      setState((prev) => ({ ...prev, history }));
    } catch {
      // History loading is non-critical
    }
  }, []);

  const executeQuery = useCallback(
    async (question: string, mode: QueryMode) => {
      setState((prev) => ({
        ...prev,
        status: "loading",
        error: null,
        result: null,
        progress: [],
      }));

      // AI mode: consume the streaming endpoint so we can show live
      // pipeline progress. Falls back to the blocking endpoint if the
      // stream cannot be established (e.g. backend not updated yet).
      if (mode === "ai") {
        try {
          const terminal = await streamAIOperation(
            "/api/query/stream",
            { question, mode },
            ["result"],
            (type, data) => {
              setState((prev) => ({
                ...prev,
                progress: [
                  ...prev.progress,
                  {
                    type,
                    data: (data ?? {}) as AIProgressEventData,
                    ts: Date.now(),
                  },
                ],
              }));
            }
          );

          if (terminal.type === "error") {
            const data = terminal.data as { detail?: string } | undefined;
            setState((prev) => ({
              ...prev,
              status: "error",
              error: data?.detail || "Query failed",
              result: null,
            }));
            return;
          }

          // Terminal `result` event carries the same QueryResponse the
          // non-streaming endpoint returns.
          setState((prev) => ({
            ...prev,
            status: "success",
            result: terminal.data as QueryResponse,
            error: null,
          }));
          loadHistory();
          return;
        } catch (err) {
          if (!(err instanceof SSEConnectionError)) {
            setState((prev) => ({
              ...prev,
              status: "error",
              error: (err as Error).message || "Query failed",
              result: null,
            }));
            return;
          }
          // Stream endpoint unreachable — fall through to the
          // non-streaming call below.
        }
      }

      try {
        const result = await api.query(question, mode);
        setState((prev) => ({
          ...prev,
          status: "success",
          result,
          error: null,
        }));
        // Refresh history after successful query
        loadHistory();
      } catch (err: unknown) {
        const error = err as {
          response?: { data?: { error?: string; details?: string } };
          message?: string;
        };

        let errorMsg = error.message || "Query failed";
        if (error.response?.data) {
          const apiErr = error.response.data.error;
          const details = error.response.data.details;
          if (apiErr && details) {
            errorMsg = `${apiErr}: ${details}`;
          } else if (apiErr) {
            errorMsg = apiErr;
          } else if (details) {
            errorMsg = details;
          }
        }

        setState((prev) => ({
          ...prev,
          status: "error",
          error: errorMsg,
          result: null,
        }));
      }
    },
    [loadHistory]
  );

  const reset = useCallback(() => {
    setState((prev) => ({
      ...prev,
      status: "idle",
      result: null,
      error: null,
      progress: [],
    }));
  }, []);

  return {
    ...state,
    executeQuery,
    loadHistory,
    reset,
  };
}
