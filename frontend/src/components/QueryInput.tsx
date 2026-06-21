import React, { useState, useRef, useEffect } from "react";
import type { QueryMode, QueryStatus } from "../types";

interface QueryInputProps {
  onSubmit: (question: string, mode: QueryMode) => void;
  status: QueryStatus;
  aiEnabled: boolean;
}

const EXAMPLE_QUESTIONS = [
  "SELECT * FROM postgres_source.public.orders LIMIT 10",
  "What are the top 5 products by total sales?",
  "Show me monthly revenue for this year",
  "Which region has the highest average order value?",
  "Compare product categories by total orders and revenue",
];

const QueryInput: React.FC<QueryInputProps> = ({
  onSubmit,
  status,
  aiEnabled,
}) => {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<QueryMode>(aiEnabled ? "ai" : "sql");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const isLoading = status === "loading";

  // Sync mode with aiEnabled flag
  useEffect(() => {
    if (!aiEnabled && mode === "ai") {
      setMode("sql");
    }
  }, [aiEnabled, mode]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim() || isLoading) return;
    onSubmit(question.trim(), mode);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      handleSubmit(e as unknown as React.FormEvent);
    }
  };

  const handleExampleClick = (example: string) => {
    setQuestion(example);
    // Auto-detect mode from example
    const looksLikeSQL =
      example.trim().toUpperCase().startsWith("SELECT") ||
      example.trim().toUpperCase().startsWith("WITH");
    if (looksLikeSQL) setMode("sql");
    else if (aiEnabled) setMode("ai");
    textareaRef.current?.focus();
  };

  return (
    <div className="query-input-card">
      <div className="query-input-header">
        <h2 className="section-title">
          <span className="section-title-icon">💬</span>
          Ask a Question
        </h2>
        <div className="mode-toggle">
          <button
            id="mode-ai-btn"
            className={`mode-btn ${mode === "ai" ? "mode-btn--active" : ""}`}
            onClick={() => aiEnabled && setMode("ai")}
            disabled={!aiEnabled}
            title={
              !aiEnabled
                ? "Enable AI Engine in the header to use AI mode"
                : "AI-powered natural language"
            }
          >
            <span>🤖</span> AI Mode
          </button>
          <button
            id="mode-sql-btn"
            className={`mode-btn ${mode === "sql" ? "mode-btn--active" : ""}`}
            onClick={() => setMode("sql")}
          >
            <span>⌨️</span> SQL Mode
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
                ? 'Ask anything about your data... e.g., "What are the top 5 products by revenue?"'
                : "Enter Trino SQL... e.g., SELECT * FROM postgres_source.public.orders LIMIT 10"
            }
            rows={4}
            disabled={isLoading}
            spellCheck={mode === "ai"}
          />
          {mode === "ai" && (
            <div className="textarea-badge">
              <span>🤖 AI</span>
            </div>
          )}
        </div>

        <div className="query-actions">
          <div className="example-chips">
            <span className="example-label">Examples:</span>
            {EXAMPLE_QUESTIONS.slice(0, 3).map((ex, i) => (
              <button
                key={i}
                type="button"
                className="example-chip"
                onClick={() => handleExampleClick(ex)}
                disabled={isLoading}
              >
                {ex.length > 40 ? ex.slice(0, 40) + "…" : ex}
              </button>
            ))}
          </div>

          <button
            id="submit-query-btn"
            type="submit"
            className={`submit-btn ${isLoading ? "submit-btn--loading" : ""}`}
            disabled={!question.trim() || isLoading}
          >
            {isLoading ? (
              <>
                <span className="spinner" />
                Executing...
              </>
            ) : (
              <>
                <span>▶</span>
                Run Query
                <kbd>⌘↵</kbd>
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
};

export default QueryInput;
