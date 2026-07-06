import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  NavLink,
  useLocation,
} from "react-router-dom";
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

// New pages
import DashboardsListPage from "./pages/DashboardsListPage";
import DashboardBuilderPage from "./pages/DashboardBuilderPage";
import DataSourcesPage from "./pages/DataSourcesPage";

import "./App.css";

// ── Navigation bar with active link styling ───────────────────
function NavBar() {
  return (
    <nav className="top-nav">
      <NavLink
        to="/"
        end
        className={({ isActive }: { isActive: boolean }) => `top-nav-link ${isActive ? "active" : ""}`}
      >
        🔎 Query
      </NavLink>
      <NavLink
        to="/dashboards"
        className={({ isActive }: { isActive: boolean }) => `top-nav-link ${isActive ? "active" : ""}`}
      >
        📊 Dashboards
      </NavLink>
      <NavLink
        to="/datasources"
        className={({ isActive }: { isActive: boolean }) => `top-nav-link ${isActive ? "active" : ""}`}
      >
        🗄️ Data Sources
      </NavLink>
    </nav>
  );
}

// ── Query / Home page ─────────────────────────────────────────
function QueryPage() {
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
    executeQuery(question, mode);
  };

  const handleSuggestionClick = (question: string) => {
    if (queryInputRef.current) {
      queryInputRef.current.setQuery(question, "ai");
    }
    executeQuery(question, "ai");
  };

  const handleReset = () => {
    reset();
    if (queryInputRef.current) {
      queryInputRef.current.setQuery("", "sql");
    }
  };

  const handleUploadSuccess = useCallback(async () => {
    setShowUpload(false);
    await refreshDatasets();
  }, [refreshDatasets]);

  return (
    <div className="app-layout">
      <LeftSidebar
        datasets={datasets}
        history={history}
        onHistorySelect={(question, mode) => handleQuery(question, mode as QueryMode)}
        onQueryTable={(trinoPath) => {
          if (queryInputRef.current) queryInputRef.current.setQuery(`SELECT * FROM ${trinoPath} LIMIT 10`, "sql");
        }}
        onInsertColumn={(trinoPath, col) => {
          if (queryInputRef.current) queryInputRef.current.insertText(`${col}`);
        }}
      />

      <main className="main-content">
        {showUpload ? (
          <div className="upload-container">
            <div className="upload-header">
              <h2>Upload Dataset</h2>
              <button
                className="btn-ghost"
                onClick={() => setShowUpload(false)}
              >
                ✕ Close
              </button>
            </div>
            <FileUpload onUploadSuccess={handleUploadSuccess} />
          </div>
        ) : (
          <>
            <QueryInput
              ref={queryInputRef}
              onSubmit={handleQuery}
              status={status}
              aiEnabled={aiEnabled}
              hasResults={!!result}
              onNewQuery={handleReset}
            />

            {status === "error" && error && (
              <div className="error-banner">
                <div className="error-banner-content">
                  <span className="error-icon">⚠️</span>
                  <div>
                    <strong>Query Failed</strong>
                    <p>{error}</p>
                  </div>
                </div>
                <button className="error-dismiss" onClick={handleReset}>
                  ✕
                </button>
              </div>
            )}

            {status === "success" && result && (
              <div className="results-container">
                {result.plan && (
                  <QueryPlanView 
                    plan={result.plan}
                    executionTimeMs={result.execution_time_ms}
                    rowCount={result.row_count}
                    mode={result.mode}
                  />
                )}
                {result.columns && result.columns.length > 0 && (
                  <>
                    <div className="results-meta">
                      <span>
                        {result.row_count} row{result.row_count !== 1 ? "s" : ""}
                      </span>
                      <span>{result.execution_time_ms}ms</span>
                      <button
                        className="btn-ghost btn-sm"
                        onClick={handleReset}
                      >
                        New Query
                      </button>
                    </div>
                    <ResultsChart
                      columns={result.columns}
                      rows={result.rows as string[][]}
                    />
                    <ResultsTable
                      columns={result.columns}
                      rows={result.rows as string[][]}
                      rowCount={result.row_count}
                    />
                  </>
                )}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}

// ── Root App with Router ──────────────────────────────────────
const App: React.FC = () => {
  return (
    <BrowserRouter>
      <div className="app-shell">
        <Header />
        <NavBar />
        <div className="page-content">
          <Routes>
            <Route path="/" element={<QueryPage />} />
            <Route path="/dashboards" element={<DashboardsListPage />} />
            <Route path="/dashboards/:id" element={<DashboardBuilderPage />} />
            <Route path="/datasources" element={<DataSourcesPage />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  );
};

export default App;
