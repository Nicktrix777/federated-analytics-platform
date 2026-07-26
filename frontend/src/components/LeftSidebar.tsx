import React, { useState } from "react";
import type {
  DatasetMeta,
  HistoryEntry,
  ConversationSummary,
} from "../types";
import SchemaPanel from "./SchemaPanel";
import { Icon } from "./ui/Icon";

interface LeftSidebarProps {
  datasets: DatasetMeta[];
  history: HistoryEntry[];
  conversations: ConversationSummary[];
  onHistorySelect: (question: string, mode: string) => void;
  onChatSelect: (id: string) => void;
  onNewChat: () => void;
  onQueryTable: (trinoPath: string) => void;
  onInsertColumn: (trinoPath: string, columnName: string) => void;
}

type Tab = "schema" | "chats" | "history";

// Relative time for list rows ("just now", "5m ago", ...).
function formatTime(iso: string): string {
  const d = new Date(iso);
  const mins = Math.floor((Date.now() - d.getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  if (mins < 1440) return `${Math.floor(mins / 60)}h ago`;
  return d.toLocaleDateString();
}

const LeftSidebar: React.FC<LeftSidebarProps> = ({
  datasets,
  history,
  conversations,
  onHistorySelect,
  onChatSelect,
  onNewChat,
  onQueryTable,
  onInsertColumn,
}) => {
  const [activeTab, setActiveTab] = useState<Tab>("schema");

  const tab = (id: Tab, label: string, count?: number) => (
    <button
      className={`sidebar-tab ${activeTab === id ? "sidebar-tab--active" : ""}`}
      onClick={() => setActiveTab(id)}
    >
      {label}
      {count !== undefined && count > 0 && (
        <span className="sidebar-tab-count">{count}</span>
      )}
    </button>
  );

  return (
    <div className="left-sidebar">
      <div className="sidebar-tabs">
        {tab("schema", "Schema")}
        {tab("chats", "Chats", conversations.length)}
        {tab("history", "History", history.length)}
      </div>

      <div className="sidebar-content">
        {activeTab === "schema" && (
          <SchemaPanel
            datasets={datasets}
            onQueryTable={onQueryTable}
            onInsertColumn={onInsertColumn}
          />
        )}

        {activeTab === "chats" && (
          <div className="history-panel-inner">
            <button className="new-chat-btn" onClick={onNewChat}>
              <Icon name="plus" size={13} /> New chat
            </button>
            {conversations.length === 0 ? (
              <div className="history-empty">
                <span>No chats yet</span>
              </div>
            ) : (
              conversations.map((c) => (
                <button
                  key={c.id}
                  className="history-item"
                  onClick={() => onChatSelect(c.id)}
                  title={c.title}
                >
                  <div className="history-item-header">
                    <span className="chat-item-title">{c.title}</span>
                    <span className="history-time">
                      {formatTime(c.last_active_at)}
                    </span>
                  </div>
                  <div className="history-meta">
                    <span className="history-success">
                      {c.turn_count} turn{c.turn_count !== 1 ? "s" : ""}
                    </span>
                  </div>
                </button>
              ))
            )}
          </div>
        )}

        {activeTab === "history" && (
          <div className="history-panel-inner">
            {history.length === 0 ? (
              <div className="history-empty">
                <span>No queries yet</span>
              </div>
            ) : (
              history.map((entry) => (
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
                        <Icon name="check" size={11} />
                        {entry.row_count.toLocaleString()} rows · {entry.duration_ms}ms
                      </span>
                    ) : (
                      <span className="history-error">
                        <Icon name="close" size={11} />
                        Error
                      </span>
                    )}
                  </div>
                </button>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default LeftSidebar;
