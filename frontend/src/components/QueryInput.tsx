import React, { useState, useRef, useEffect, useImperativeHandle, forwardRef } from "react";
import type { QueryMode, QueryStatus } from "../types";

// Raw SQL the backend will accept must start with SELECT or WITH. Anything else
// submitted in SQL mode (a natural-language question, SHOW/DESCRIBE, etc.) would
// fail the validator, so it's better routed to AI.
const looksLikeSQL = (text: string): boolean => {
  const t = text.trim().toUpperCase();
  return t.startsWith("SELECT") || t.startsWith("WITH");
};

export interface QueryInputHandle {
  insertText: (text: string) => void;
  setQuery: (text: string, mode?: QueryMode) => void;
}

interface QueryInputProps {
  onSubmit: (question: string, mode: QueryMode) => void;
  status: QueryStatus;
  aiEnabled: boolean;
  hasResults?: boolean;
  onNewQuery?: () => void;
}

const EXAMPLE_QUESTIONS = [
  "Show total tasks per department with completion rate",
  "Who are the top 3 highest paid employees in each department?",
  "Which employees prefer remote work and what is their task completion rate?",
  "Show average performance score per department ordered by score",
];

const QueryInput = forwardRef<QueryInputHandle, QueryInputProps>(({
  onSubmit,
  status,
  aiEnabled,
  hasResults = false,
  onNewQuery,
}, ref) => {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<QueryMode>(aiEnabled ? "ai" : "sql");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isLoading = status === "loading";

  useImperativeHandle(ref, () => ({
    insertText: (text: string) => {
      const el = textareaRef.current;
      if (!el) return;
      const start = el.selectionStart ?? question.length;
      const end   = el.selectionEnd   ?? question.length;
      const newVal = question.slice(0, start) + text + question.slice(end);
      setQuestion(newVal);
      el.focus();
      // Restore cursor position after the inserted text
      setTimeout(() => {
        el.setSelectionRange(start + text.length, start + text.length);
      }, 0);
    },
    setQuery: (text: string, newMode?: QueryMode) => {
      setQuestion(text);
      if (newMode) setMode(newMode);
      textareaRef.current?.focus();
    },
  }));

  useEffect(() => {
    if (!aiEnabled && mode === "ai") setMode("sql");
  }, [aiEnabled, mode]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim() || isLoading) return;
    const q = question.trim();
    // Guard the common trap: mode left on SQL from a previous query, then a
    // natural-language question typed. Rather than fail it against the
    // SELECT/WITH validator, route it to AI when AI is available and it isn't
    // actually SQL. Real SQL (SELECT/WITH) is submitted as-is.
    let effectiveMode = mode;
    if (mode === "sql" && aiEnabled && !looksLikeSQL(q)) {
      effectiveMode = "ai";
      setMode("ai");
    }
    onSubmit(q, effectiveMode);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      handleSubmit(e as unknown as React.FormEvent);
    }
  };

  const handleExampleClick = (example: string) => {
    setQuestion(example);
    if (looksLikeSQL(example)) setMode("sql");
    else if (aiEnabled) setMode("ai");
    textareaRef.current?.focus();
  };

  return (
    <div className={`query-input-card ${hasResults ? "query-input-card--compact" : ""}`}>
      {hasResults && (
        <div className="refine-bar">
          <span className="refine-label">Refine or</span>
          <button className="refine-new-btn" onClick={onNewQuery} type="button">
            start fresh →
          </button>
        </div>
      )}

      <div className="query-input-header">
        {!hasResults && <h2 className="section-title">Query</h2>}
        <div className="mode-toggle" style={hasResults ? { marginLeft: "auto" } : {}}>
          <button
            id="mode-ai-btn"
            className={`mode-btn ${mode === "ai" ? "mode-btn--active" : ""}`}
            onClick={() => aiEnabled && setMode("ai")}
            disabled={!aiEnabled}
            title={!aiEnabled ? "Enable AI Engine in header to use AI mode" : "Natural language"}
          >
            AI
          </button>
          <button
            id="mode-sql-btn"
            className={`mode-btn ${mode === "sql" ? "mode-btn--active" : ""}`}
            onClick={() => setMode("sql")}
          >
            SQL
          </button>
        </div>
      </div>

      <form onSubmit={handleSubmit}>
        <div className="textarea-wrapper">
          <textarea
            ref={textareaRef}
            id="query-textarea"
            className={`query-textarea ${mode === "sql" ? "query-textarea--sql" : ""}`}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              mode === "ai"
                ? hasResults
                  ? "Refine your analysis…"
                  : 'Ask anything… e.g., "What are the top 5 products by revenue?"'
                : "Trino SQL… e.g., SELECT * FROM postgres_source.public.orders LIMIT 10"
            }
            rows={hasResults ? 2 : 4}
            disabled={isLoading}
            spellCheck={mode === "ai"}
          />
        </div>

        <div className="query-actions">
          {!hasResults && (
            <div className="example-chips">
              <span className="example-label">Try:</span>
              {EXAMPLE_QUESTIONS.slice(0, 3).map((ex, i) => (
                <button
                  key={i} type="button" className="example-chip"
                  onClick={() => handleExampleClick(ex)}
                  disabled={isLoading}
                >
                  {ex.length > 36 ? ex.slice(0, 36) + "…" : ex}
                </button>
              ))}
            </div>
          )}

          <button
            id="submit-query-btn" type="submit"
            className={`submit-btn ${isLoading ? "submit-btn--loading" : ""}`}
            disabled={!question.trim() || isLoading}
            style={hasResults ? { marginLeft: "auto" } : {}}
          >
            {isLoading ? (
              <><span className="spinner" />Running…</>
            ) : (
              <><span className="submit-arrow">▶</span>{hasResults ? "Run" : "Run Query"}<kbd>⌘↵</kbd></>
            )}
          </button>
        </div>
      </form>
    </div>
  );
});

QueryInput.displayName = "QueryInput";
export default QueryInput;
