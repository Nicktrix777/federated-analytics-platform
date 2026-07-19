import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  NavLink,
} from "react-router-dom";
import Header from "./components/Header";
import QueryInput, { QueryInputHandle } from "./components/QueryInput";
import LeftSidebar from "./components/LeftSidebar";
import FileUpload from "./components/FileUpload";
import Transcript from "./components/Transcript";
import { useQuery } from "./hooks/useQuery";
import { api } from "./api/client";
import type { DatasetMeta, QueryMode, ConversationSummary } from "./types";

// New pages
import DashboardsListPage from "./pages/DashboardsListPage";
import DashboardBuilderPage from "./pages/DashboardBuilderPage";
import ReportsListPage from "./pages/ReportsListPage";
import ReportDetailPage from "./pages/ReportDetailPage";
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
        to="/reports"
        className={({ isActive }: { isActive: boolean }) => `top-nav-link ${isActive ? "active" : ""}`}
      >
        📄 Reports
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
  const [aiEnabled] = useState(true);
  const [datasets, setDatasets] = useState<DatasetMeta[]>([]);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [showUpload, setShowUpload] = useState(false);

  const queryInputRef = useRef<QueryInputHandle>(null);

  const {
    status,
    turns,
    history,
    executeQuery,
    loadHistory,
    newConversation,
    loadConversation,
  } = useQuery();

  const refreshDatasets = useCallback(async () => {
    try {
      setDatasets((await api.getDatasets()) || []);
    } catch {
      // non-critical
    }
  }, []);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations((await api.getConversations(30)) || []);
    } catch {
      // non-critical
    }
  }, []);

  useEffect(() => {
    void Promise.all([refreshDatasets(), loadHistory(), refreshConversations()]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Run a query, then refresh the chat list so a new thread / title appears.
  const handleQuery = useCallback(
    async (question: string, mode: QueryMode) => {
      await executeQuery(question, mode);
      refreshConversations();
    },
    [executeQuery, refreshConversations]
  );

  const handleNewChat = useCallback(() => {
    newConversation();
    queryInputRef.current?.setQuery("", aiEnabled ? "ai" : "sql");
  }, [newConversation, aiEnabled]);

  const handleChatSelect = useCallback(
    (id: string) => {
      void loadConversation(id);
    },
    [loadConversation]
  );

  const handleUploadSuccess = useCallback(async () => {
    setShowUpload(false);
    await refreshDatasets();
  }, [refreshDatasets]);

  const hasTurns = turns.length > 0;

  return (
    <div className="app-layout">
      <LeftSidebar
        datasets={datasets}
        history={history}
        conversations={conversations}
        onHistorySelect={(question, mode) => handleQuery(question, mode as QueryMode)}
        onChatSelect={handleChatSelect}
        onNewChat={handleNewChat}
        onQueryTable={(trinoPath) => {
          queryInputRef.current?.setQuery(`SELECT * FROM ${trinoPath} LIMIT 10`, "sql");
        }}
        onInsertColumn={(_trinoPath, col) => {
          queryInputRef.current?.insertText(`${col}`);
        }}
      />

      <main className="main-content main-content--chat">
        {showUpload ? (
          <div className="upload-container">
            <div className="upload-header">
              <h2>Upload Dataset</h2>
              <button className="btn-ghost" onClick={() => setShowUpload(false)}>
                ✕ Close
              </button>
            </div>
            <FileUpload onUploadSuccess={handleUploadSuccess} />
          </div>
        ) : (
          <>
            {hasTurns ? (
              <Transcript
                turns={turns}
                onClarify={(option) => handleQuery(option, "ai")}
              />
            ) : (
              <div className="chat-welcome">
                <div className="chat-welcome-mark">◆</div>
                <h2>Ask your data anything</h2>
                <p>
                  Natural-language questions across every connected source.
                  Follow up to refine — the thread remembers.
                </p>
              </div>
            )}

            <div className="composer-dock">
              <QueryInput
                ref={queryInputRef}
                onSubmit={handleQuery}
                status={status}
                aiEnabled={aiEnabled}
                hasResults={hasTurns}
                onNewQuery={handleNewChat}
              />
            </div>
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
            <Route path="/reports" element={<ReportsListPage />} />
            <Route path="/reports/:id" element={<ReportDetailPage />} />
            <Route path="/datasources" element={<DataSourcesPage />} />
          </Routes>
        </div>
      </div>
    </BrowserRouter>
  );
};

export default App;
