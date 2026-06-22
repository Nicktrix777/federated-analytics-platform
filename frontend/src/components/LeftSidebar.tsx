import React, { useState } from "react";
import type { DatasetMeta, HistoryEntry } from "../types";
import SchemaPanel from "./SchemaPanel";
import QueryHistory from "./QueryHistory";

interface LeftSidebarProps {
  datasets: DatasetMeta[];
  history: HistoryEntry[];
  onHistorySelect: (question: string, mode: string) => void;
  onQueryTable: (trinoPath: string) => void;
  onInsertColumn: (trinoPath: string, columnName: string) => void;
}

const LeftSidebar: React.FC<LeftSidebarProps> = ({
  datasets,
  history,
  onHistorySelect,
  onQueryTable,
  onInsertColumn,
}) => {
  const [activeTab, setActiveTab] = useState<"schema" | "history">("schema");

  return (
    <div className="left-sidebar">
      <div className="sidebar-tabs">
        <button
          className={`sidebar-tab ${activeTab === "schema" ? "sidebar-tab--active" : ""}`}
          onClick={() => setActiveTab("schema")}
        >
          Schema
        </button>
        <button
          className={`sidebar-tab ${activeTab === "history" ? "sidebar-tab--active" : ""}`}
          onClick={() => setActiveTab("history")}
        >
          History
          {history.length > 0 && (
            <span style={{
              marginLeft: "4px",
              fontSize: "9px",
              background: "var(--bg-raised)",
              border: "1px solid var(--border)",
              borderRadius: "2px",
              padding: "0 4px",
              color: "var(--text-muted)",
            }}>
              {history.length}
            </span>
          )}
        </button>
      </div>

      <div className="sidebar-content">
        {activeTab === "schema" ? (
          <SchemaPanel
            datasets={datasets}
            onQueryTable={onQueryTable}
            onInsertColumn={onInsertColumn}
          />
        ) : (
          <div className="history-panel-inner">
            {history.length === 0 ? (
              <div className="history-empty">
                <span>No queries yet</span>
              </div>
            ) : (
              history.map((entry) => {
                const formatTime = (iso: string): string => {
                  const d = new Date(iso);
                  const diffMs = Date.now() - d.getTime();
                  const mins = Math.floor(diffMs / 60000);
                  if (mins < 1) return "just now";
                  if (mins < 60) return `${mins}m ago`;
                  if (mins < 1440) return `${Math.floor(mins / 60)}h ago`;
                  return d.toLocaleDateString();
                };

                return (
                  <button
                    key={entry.id}
                    className={`history-item history-item--${entry.status}`}
                    onClick={() => onHistorySelect(entry.question, entry.mode)}
                    title={`Re-run: ${entry.question}`}
                  >
                    <div className="history-item-header">
                      <span className={`history-mode-badge history-mode-badge--${entry.mode}`}>
                        {entry.mode.toUpperCase()}
                      </span>
                      <span className="history-time">{formatTime(entry.created_at)}</span>
                    </div>
                    <div className="history-question">
                      {entry.question.length > 75
                        ? entry.question.slice(0, 75) + "…"
                        : entry.question}
                    </div>
                    <div className="history-meta">
                      {entry.status === "success" ? (
                        <span className="history-success">
                          ✓ {entry.row_count.toLocaleString()} rows · {entry.duration_ms}ms
                        </span>
                      ) : (
                        <span className="history-error">✗ Error</span>
                      )}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default LeftSidebar;
