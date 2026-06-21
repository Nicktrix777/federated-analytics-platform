import React, { useState, useEffect } from "react";
import Header from "./components/Header";
import QueryInput from "./components/QueryInput";
import QueryPlanView from "./components/QueryPlan";
import ResultsTable from "./components/ResultsTable";
import ResultsChart from "./components/ResultsChart";
import QueryHistory from "./components/QueryHistory";
import { useQuery } from "./hooks/useQuery";
import { api } from "./api/client";
import type { DatasetMeta, QueryMode } from "./types";
import "./App.css";

const App: React.FC = () => {
  const [aiEnabled, setAiEnabled] = useState(true);
  const [datasets, setDatasets] = useState<DatasetMeta[]>([]);
  const { status, result, error, history, executeQuery, loadHistory } =
    useQuery();

  // Load datasets and history on mount
  useEffect(() => {
    const init = async () => {
      try {
        const [dsData] = await Promise.all([api.getDatasets(), loadHistory()]);
        setDatasets(dsData || []);
      } catch {
        // Non-critical — app works without metadata
      }
    };
    init();
  }, []);

  const handleQuery = (question: string, mode: QueryMode) => {
    // Respect the AI toggle
    const effectiveMode = mode === "ai" && !aiEnabled ? "sql" : mode;
    executeQuery(question, effectiveMode);
  };

  const handleHistorySelect = (question: string, mode: string) => {
    executeQuery(question, (mode as QueryMode) || "sql");
  };

  return (
    <div className="app">
      <Header
        aiEnabled={aiEnabled}
        onToggleAI={setAiEnabled}
        datasets={datasets}
      />

      <div className="app-layout">
        {/* Left: History Sidebar */}
        <QueryHistory history={history} onSelect={handleHistorySelect} />

        {/* Main Content */}
        <main className="main-content">
          <QueryInput
            onSubmit={handleQuery}
            status={status}
            aiEnabled={aiEnabled}
          />

          {/* Error State */}
          {status === "error" && error && (
            <div className="error-card">
              <div className="error-icon">⚠️</div>
              <div className="error-content">
                <div className="error-title">Query Failed</div>
                <div className="error-message">{error}</div>
              </div>
            </div>
          )}

          {/* Loading Skeleton */}
          {status === "loading" && (
            <div className="loading-state">
              <div className="loading-pulse">
                <div className="loading-bar" style={{ width: "60%" }} />
                <div className="loading-bar" style={{ width: "40%" }} />
                <div className="loading-bar" style={{ width: "80%" }} />
              </div>
              <p className="loading-label">
                {aiEnabled
                  ? "🤖 AI is generating your query plan..."
                  : "⚡ Executing federated query..."}
              </p>
            </div>
          )}

          {/* Results */}
          {status === "success" && result && (
            <div className="results-section">
              {/* Query Plan (AI mode or direct SQL) */}
              {result.plan && (
                <QueryPlanView
                  plan={result.plan}
                  executionTimeMs={result.execution_time_ms}
                  rowCount={result.row_count}
                  mode={result.mode}
                />
              )}

              {/* Results Grid: Table + Chart side-by-side if enough columns */}
              {result.columns.length > 0 && (
                <div className="results-grid">
                  <ResultsTable
                    columns={result.columns}
                    rows={result.rows}
                    rowCount={result.row_count}
                  />
                  {result.columns.length >= 2 && result.rows.length > 0 && (
                    <ResultsChart columns={result.columns} rows={result.rows} />
                  )}
                </div>
              )}

              {result.columns.length === 0 && (
                <div className="card">
                  <div className="empty-state">
                    <span className="empty-icon">✅</span>
                    <span>
                      Query executed successfully — no results returned
                    </span>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Empty state */}
          {status === "idle" && (
            <div className="welcome-section">
              <div className="welcome-hero">
                <div className="welcome-icon-ring">
                  <span className="welcome-icon">⬡</span>
                </div>
                <h2>Federated Query Intelligence</h2>
                <p>
                  Type a natural language question or raw SQL above to query
                  across your connected data sources — simultaneously.
                </p>
              </div>
              <div className="architecture-callout">
                <div className="arch-flow">
                  <div className="arch-node arch-node--frontend">
                    <span>🖥️</span>
                    <span>Frontend</span>
                  </div>
                  <span className="arch-arrow">→</span>
                  <div className="arch-node arch-node--api">
                    <span>⚙️</span>
                    <span>Core API</span>
                  </div>
                  <span className="arch-arrow">→</span>
                  <div className="arch-node arch-node--ai">
                    <span>🤖</span>
                    <span>AI Engine</span>
                  </div>
                  <span className="arch-arrow">→</span>
                  <div className="arch-node arch-node--query">
                    <span>🔗</span>
                    <span>Trino</span>
                  </div>
                  <span className="arch-arrow">→</span>
                  <div className="arch-sources">
                    <div className="arch-node arch-node--pg">
                      <span>🐘</span>
                      <span>PostgreSQL</span>
                    </div>
                    <div className="arch-node arch-node--mongo">
                      <span>🍃</span>
                      <span>MongoDB</span>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
};

export default App;
