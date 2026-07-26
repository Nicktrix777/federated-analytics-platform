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
import LeftSidebar from "./components/LeftSidebar";
import FileUpload from "./components/FileUpload";
import Transcript from "./components/Transcript";
import { useQuery } from "./hooks/useQuery";
import { api } from "./api/client";
import type { DatasetMeta, QueryMode, ConversationSummary } from "./types";
import { ToastProvider } from "./components/ui";
import { Button } from "./components/ui";
import { Icon, type IconName } from "./components/ui/Icon";
import { ThemeProvider } from "./theme";

// New pages
import DashboardsListPage from "./pages/DashboardsListPage";
import DashboardBuilderPage from "./pages/DashboardBuilderPage";
import ReportsListPage from "./pages/ReportsListPage";
import ReportDetailPage from "./pages/ReportDetailPage";
import DataSourcesPage from "./pages/DataSourcesPage";
import LLMSettingsPage from "./pages/LLMSettingsPage";

import "./App.css";

// ── Navigation bar with active link styling ───────────────────
const NAV_ITEMS: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: "/", label: "Query", icon: "search", end: true },
  { to: "/dashboards", label: "Dashboards", icon: "dashboard" },
  { to: "/reports", label: "Reports", icon: "report" },
  { to: "/datasources", label: "Data Sources", icon: "database" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

function NavBar() {
  return (
    <nav className="top-nav">
      {NAV_ITEMS.map(({ to, label, icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }: { isActive: boolean }) =>
            `top-nav-link ${isActive ? "active" : ""}`
          }
        >
          <Icon name={icon} size={15} />
          <span>{label}</span>
        </NavLink>
      ))}
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
              <Button variant="ghost" onClick={() => setShowUpload(false)}>
                <Icon name="close" size={14} /> Close
              </Button>
            </div>
            <FileUpload onUploadSuccess={handleUploadSuccess} />
          </div>
        ) : (
          <>
            {hasTurns ? (
              <Transcript
                turns={turns}
                datasets={datasets}
                onClarify={(option) => handleQuery(option, "ai")}
                onFollowUp={(question) => handleQuery(question, "ai")}
              />
            ) : (
              <div className="chat-welcome">
                <div className="chat-welcome-mark">
                  <Icon name="sparkles" size={26} />
                </div>
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

// ── Route Transition Wrapper ──────────────────────────────────
function AnimatedRoutes() {
  const location = useLocation();
  return (
    <div key={location.pathname} className="page-transition-enter">
      <Routes location={location}>
        <Route path="/" element={<QueryPage />} />
        <Route path="/dashboards" element={<DashboardsListPage />} />
        <Route path="/dashboards/:id" element={<DashboardBuilderPage />} />
        <Route path="/reports" element={<ReportsListPage />} />
        <Route path="/reports/:id" element={<ReportDetailPage />} />
        <Route path="/datasources" element={<DataSourcesPage />} />
        <Route path="/settings" element={<LLMSettingsPage />} />
      </Routes>
    </div>
  );
}

// ── Root App with Router ──────────────────────────────────────
const App: React.FC = () => {
  return (
    <BrowserRouter>
      <ThemeProvider>
        <ToastProvider>
          <div className="app-shell">
            <Header />
            <NavBar />
            <div className="page-content">
              <AnimatedRoutes />
            </div>
          </div>
        </ToastProvider>
      </ThemeProvider>
    </BrowserRouter>
  );
};

export default App;
