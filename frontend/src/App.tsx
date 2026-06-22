import React, { useState, useEffect, useRef, useCallback } from "react";
import Header from "./components/Header";
import QueryInput, { QueryInputHandle } from "./components/QueryInput";
import QueryPlanView from "./components/QueryPlan";
import ResultsTable from "./components/ResultsTable";
import ResultsChart from "./components/ResultsChart";
import LeftSidebar from "./components/LeftSidebar";
import FileUpload from "./components/FileUpload";
import { useQuery } from "./hooks/useQuery";
import { api } from "./api/client";
import type { DatasetMeta, QueryMode } from "./types";
import "./App.css";

const App: React.FC = () => {
  const [aiEnabled, setAiEnabled] = useState(true);
  const [datasets, setDatasets] = useState<DatasetMeta[]>([]);
  const [showUpload, setShowUpload] = useState(false);

  const queryInputRef = useRef<QueryInputHandle>(null);

  const { status, result, error, history, executeQuery, loadHistory, reset } =
    useQuery();

  const refreshDatasets = useCallback(async () => {
    try {
      const data = await api.getDatasets();
      setDatasets(data || []);
    } catch {
      // non-critical
    }
  }, []);

  useEffect(() => {
    const init = async () => {
      await Promise.all([refreshDatasets(), loadHistory()]);
    };
    init();
  }, []);

  const handleQuery = (question: string, mode: QueryMode) => {
    const effectiveMode = mode === "ai" && !aiEnabled ? "sql" : mode;
    setShowUpload(false);
    executeQuery(question, effectiveMode);
  };

  const handleHistorySelect = (question: string, mode: string) => {
    setShowUpload(false);
    executeQuery(question, (mode as QueryMode) || "sql");
  };

  const handleNewQuery = () => {
    reset();
    setShowUpload(false);
  };

  // Schema panel: click a table → run SELECT * query in SQL mode
  const handleQueryTable = (trinoPath: string) => {
    const sql = `SELECT * FROM ${trinoPath} LIMIT 10`;
    queryInputRef.current?.setQuery(sql, "sql");
    executeQuery(sql, "sql");
    setShowUpload(false);
  };

  // Schema panel: click a column → insert column name at cursor
  const handleInsertColumn = (_trinoPath: string, columnName: string) => {
    queryInputRef.current?.insertText(columnName);
  };

  const hasResults = status === "success" && result;

  return (
    <div className="app">
      <Header
        aiEnabled={aiEnabled}
        onToggleAI={setAiEnabled}
        datasets={datasets}
        onNewQuery={handleNewQuery}
        onToggleUpload={() => setShowUpload((v) => !v)}
        showUpload={showUpload}
      />

      <div className="app-layout">
        {/* Left: Schema + History Sidebar */}
        <LeftSidebar
          datasets={datasets}
          history={history}
          onHistorySelect={handleHistorySelect}
          onQueryTable={handleQueryTable}
          onInsertColumn={handleInsertColumn}
        />

        {/* Main Content */}
        <main className="main-content">
          {/* Upload Panel (collapsible) */}
          {showUpload && (
            <div className="upload-panel">
              <div className="upload-panel-header">
                <span className="upload-panel-title">Upload Data</span>
                <button
                  className="upload-panel-close"
                  onClick={() => setShowUpload(false)}
                  aria-label="Close upload panel"
                >
                  ✕
                </button>
              </div>
              <FileUpload
                onUploadSuccess={async (res) => {
                  await refreshDatasets();
                  setShowUpload(false);
                  executeQuery(`SELECT * FROM ${res.trino_path} LIMIT 10`, "sql");
                }}
              />
            </div>
          )}

          <QueryInput
            ref={queryInputRef}
            onSubmit={handleQuery}
            status={status}
            aiEnabled={aiEnabled}
            hasResults={!!hasResults}
            onNewQuery={handleNewQuery}
          />

          {/* Error */}
          {status === "error" && error && (
            <div className="error-card">
              <div className="error-icon">⚠</div>
              <div className="error-content">
                <div className="error-title">Query Failed</div>
                <div className="error-message">{error}</div>
              </div>
              <button className="error-dismiss" onClick={handleNewQuery}>✕</button>
            </div>
          )}

          {/* Loading */}
          {status === "loading" && (
            <div className="loading-state">
              <div className="loading-pulse">
                <div className="loading-bar" style={{ width: "60%" }} />
                <div className="loading-bar" style={{ width: "40%" }} />
                <div className="loading-bar" style={{ width: "75%" }} />
              </div>
              <p className="loading-label">
                {aiEnabled ? "AI generating query plan…" : "Executing federated query…"}
              </p>
            </div>
          )}

          {/* Results */}
          {hasResults && (
            <div className="results-section">
              {result.plan && (
                <QueryPlanView
                  plan={result.plan}
                  executionTimeMs={result.execution_time_ms}
                  rowCount={result.row_count}
                  mode={result.mode}
                />
              )}

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
                    <span>Query executed — no rows returned</span>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Welcome */}
          {status === "idle" && (
            <div className="welcome-section">
              <div className="welcome-hero">
                <div className="welcome-icon-ring">
                  <span className="welcome-monogram">F</span>
                </div>
                <h2>Federated Query Intelligence</h2>
                <p>
                  Ask a question in plain English or write SQL to query across
                  all connected data sources simultaneously.
                </p>
                <div className="welcome-actions">
                  <button
                    className="welcome-action-btn welcome-action-btn--primary"
                    onClick={() => {
                      const ta = document.getElementById("query-textarea") as HTMLTextAreaElement;
                      ta?.focus();
                    }}
                  >
                    Start querying
                  </button>
                  <button
                    className="welcome-action-btn"
                    onClick={() => setShowUpload(true)}
                  >
                    Upload data
                  </button>
                </div>
              </div>

              <div className="architecture-callout">
                <div className="arch-label">Architecture</div>
                <div className="arch-flow">
                  <div className="arch-node">
                    <span className="arch-node-icon">⬡</span>
                    <span>Frontend</span>
                  </div>
                  <span className="arch-arrow">→</span>
                  <div className="arch-node">
                    <span className="arch-node-icon">⚙</span>
                    <span>Core API</span>
                  </div>
                  <span className="arch-arrow">→</span>
                  <div className="arch-node">
                    <span className="arch-node-icon">◎</span>
                    <span>AI Engine</span>
                  </div>
                  <span className="arch-arrow">→</span>
                  <div className="arch-node">
                    <span className="arch-node-icon">⟁</span>
                    <span>Trino</span>
                  </div>
                  <span className="arch-arrow">→</span>
                  <div className="arch-sources">
                    <div className="arch-node">
                      <span className="arch-node-icon">🐘</span>
                      <span>PostgreSQL</span>
                    </div>
                    <div className="arch-node">
                      <span className="arch-node-icon">🍃</span>
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
