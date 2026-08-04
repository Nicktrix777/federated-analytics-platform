import { useState, useCallback, useRef } from "react";
import { api } from "../api/client";
import { streamAIOperation, SSEConnectionError } from "../api/sse";
import { safeUUID } from "../lib/uuid";
import type {
  QueryResponse,
  Clarification,
  HistoryEntry,
  QueryMode,
  QueryStatus,
  AIProgressEventData,
  TranscriptTurn,
  TurnOutcome,
} from "../types";

interface QueryState {
  /** "loading" while any turn is streaming, else "idle". Drives the composer. */
  status: QueryStatus;
  /** The transcript — one entry per exchange, mirroring conversation_turns. */
  turns: TranscriptTurn[];
  history: HistoryEntry[];
}

const emptyClarification = (): Clarification => ({
  question: "",
  options: [],
  kind: "ambiguous",
});

export function useQuery() {
  const [state, setState] = useState<QueryState>({
    status: "idle",
    turns: [],
    history: [],
  });

  // One conversation id per hook instance so follow-up questions ("now break
  // that down by region") carry prior turns as context. newConversation()
  // starts a fresh thread; loadConversation() adopts an existing one.
  const conversationIdRef = useRef<string>(safeUUID());

  const loadHistory = useCallback(async () => {
    try {
      const history = await api.getHistory(30);
      setState((prev) => ({ ...prev, history }));
    } catch {
      // History loading is non-critical
    }
  }, []);

  // Update one turn in place (immutably) by id.
  const patchTurn = useCallback(
    (id: string, patch: (t: TranscriptTurn) => TranscriptTurn) => {
      setState((prev) => ({
        ...prev,
        turns: prev.turns.map((t) => (t.id === id ? patch(t) : t)),
      }));
    },
    []
  );

  // Finalize a turn's outcome and drop the composer back to idle.
  const completeTurn = useCallback(
    (id: string, outcome: TurnOutcome, status: "done" | "error") => {
      setState((prev) => ({
        ...prev,
        status: "idle",
        turns: prev.turns.map((t) =>
          t.id === id ? { ...t, status, outcome } : t
        ),
      }));
    },
    []
  );

  const executeQuery = useCallback(
    async (question: string, mode: QueryMode) => {
      const turnId = safeUUID();

      // Append the new turn. If the previous turn asked an unanswered
      // clarification, this exchange answers it — mark it so and tag this
      // turn as an "answer".
      setState((prev) => {
        const turns = prev.turns.slice();
        const last = turns[turns.length - 1];
        let userKind: TranscriptTurn["userKind"] = "question";
        if (
          last &&
          last.outcome?.type === "clarification" &&
          !last.outcome.answered
        ) {
          userKind = "answer";
          turns[turns.length - 1] = {
            ...last,
            outcome: { ...last.outcome, answered: true },
          };
        }
        turns.push({
          id: turnId,
          userText: question,
          userKind,
          mode,
          progress: [],
          status: "streaming",
        });
        return { ...prev, status: "loading", turns };
      });

      // ── AI mode: stream pipeline progress into this turn ────────
      if (mode === "ai") {
        try {
          const terminal = await streamAIOperation(
            "/api/query/stream",
            { question, mode, conversation_id: conversationIdRef.current },
            ["result", "clarification"],
            (type, data) => {
              patchTurn(turnId, (t) => ({
                ...t,
                progress: [
                  ...t.progress,
                  { type, data: (data ?? {}) as AIProgressEventData, ts: Date.now() },
                ],
              }));
            }
          );

          if (terminal.type === "error") {
            const data = terminal.data as { detail?: string } | undefined;
            completeTurn(
              turnId,
              { type: "error", message: data?.detail || "Query failed" },
              "error"
            );
            return;
          }

          if (terminal.type === "clarification") {
            const data = terminal.data as QueryResponse;
            completeTurn(
              turnId,
              {
                type: "clarification",
                clarification: data?.clarification ?? emptyClarification(),
                answered: false,
              },
              "done"
            );
            return;
          }

          // Terminal `result` — same QueryResponse the blocking endpoint returns.
          completeTurn(
            turnId,
            { type: "result", result: terminal.data as QueryResponse },
            "done"
          );
          loadHistory();
          return;
        } catch (err) {
          if (!(err instanceof SSEConnectionError)) {
            completeTurn(
              turnId,
              { type: "error", message: (err as Error).message || "Query failed" },
              "error"
            );
            return;
          }
          // Stream endpoint unreachable — fall through to the blocking call.
        }
      }

      // ── Blocking path (SQL mode, or SSE fallback) ──────────────
      try {
        const result = await api.query(question, mode, conversationIdRef.current);
        if (result.clarification) {
          completeTurn(
            turnId,
            { type: "clarification", clarification: result.clarification, answered: false },
            "done"
          );
        } else {
          completeTurn(turnId, { type: "result", result }, "done");
        }
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
          if (apiErr && details) errorMsg = `${apiErr}: ${details}`;
          else if (apiErr) errorMsg = apiErr;
          else if (details) errorMsg = details;
        }
        completeTurn(turnId, { type: "error", message: errorMsg }, "error");
      }
    },
    [patchTurn, completeTurn, loadHistory]
  );

  // Start a fresh conversation thread (drops prior-turn context) and clear the
  // transcript.
  const newConversation = useCallback(() => {
    conversationIdRef.current = safeUUID();
    setState((prev) => ({ ...prev, status: "idle", turns: [] }));
  }, []);

  // Adopt an existing conversation and hydrate its transcript. Result rows are
  // not persisted (§14) — plan turns render the plan card + a "run again to
  // view" note; clarification turns render answered/unanswered from position.
  const loadConversation = useCallback(async (id: string) => {
    try {
      const dtos = await api.getConversationTurns(id);
      conversationIdRef.current = id;
      const turns: TranscriptTurn[] = dtos.map((d, i) => {
        let outcome: TurnOutcome;
        if (d.kind === "clarification") {
          outcome = {
            type: "clarification",
            clarification: d.clarification ?? emptyClarification(),
            // Answered if any later turn exists in the thread.
            answered: i < dtos.length - 1,
          };
        } else {
          outcome = {
            type: "result",
            hydrated: true,
            result: {
              request_id: "",
              question: d.question,
              mode: "ai",
              plan: d.sql
                ? {
                    question: d.question,
                    sql: d.sql,
                    steps: [],
                    confidence: d.confidence ?? 0,
                    explanation: "",
                  }
                : undefined,
              columns: [],
              rows: [],
              row_count: d.row_count,
              execution_time_ms: 0,
              ai_enabled: true,
            },
          };
        }
        const prevKind = i > 0 ? dtos[i - 1].kind : null;
        return {
          id: String(d.id),
          userText: d.question,
          userKind: prevKind === "clarification" ? "answer" : "question",
          mode: "ai",
          progress: [],
          status: "done",
          outcome,
        };
      });
      setState((prev) => ({ ...prev, status: "idle", turns }));
    } catch {
      // non-critical — leave the current transcript untouched
    }
  }, []);

  return {
    ...state,
    executeQuery,
    loadHistory,
    newConversation,
    loadConversation,
  };
}
