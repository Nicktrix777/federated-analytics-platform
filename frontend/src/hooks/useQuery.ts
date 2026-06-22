import { useState, useCallback } from "react";
import { api } from "../api/client";
import type {
  QueryResponse,
  HistoryEntry,
  QueryMode,
  QueryStatus,
} from "../types";

interface QueryState {
  status: QueryStatus;
  result: QueryResponse | null;
  error: string | null;
  history: HistoryEntry[];
}

export function useQuery() {
  const [state, setState] = useState<QueryState>({
    status: "idle",
    result: null,
    error: null,
    history: [],
  });

  const executeQuery = useCallback(
    async (question: string, mode: QueryMode) => {
      setState((prev) => ({ ...prev, status: "loading", error: null }));

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
    [],
  );

  const loadHistory = useCallback(async () => {
    try {
      const history = await api.getHistory(30);
      setState((prev) => ({ ...prev, history }));
    } catch {
      // History loading is non-critical
    }
  }, []);

  const reset = useCallback(() => {
    setState((prev) => ({
      ...prev,
      status: "idle",
      result: null,
      error: null,
    }));
  }, []);

  return {
    ...state,
    executeQuery,
    loadHistory,
    reset,
  };
}
