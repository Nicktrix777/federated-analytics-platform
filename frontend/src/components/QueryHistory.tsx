import React from "react";
import type { HistoryEntry } from "../types";

interface QueryHistoryProps {
  history: HistoryEntry[];
  onSelect: (question: string, mode: string) => void;
}

const QueryHistory: React.FC<QueryHistoryProps> = ({ history, onSelect }) => {
  const formatTime = (iso: string): string => {
    const d = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 1) return "just now";
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffMins < 1440) return `${Math.floor(diffMins / 60)}h ago`;
    return d.toLocaleDateString();
  };

  return (
    <aside className="history-panel">
      <div className="history-header">
        <span className="section-title-icon">🕐</span>
        <span className="section-title">Query History</span>
        <span className="badge">{history.length}</span>
      </div>

      {history.length === 0 ? (
        <div className="history-empty">
          <span className="empty-icon">📭</span>
          <span>No queries yet</span>
        </div>
      ) : (
        <div className="history-list">
          {history.map((entry) => (
            <button
              key={entry.id}
              className={`history-item history-item--${entry.status}`}
              onClick={() => onSelect(entry.question, entry.mode)}
              title={`Click to re-run: ${entry.question}`}
            >
              <div className="history-item-header">
                <span
                  className={`history-mode-badge history-mode-badge--${entry.mode}`}
                >
                  {entry.mode === "ai" ? "🤖" : "⌨️"} {entry.mode.toUpperCase()}
                </span>
                <span className="history-time">
                  {formatTime(entry.created_at)}
                </span>
              </div>
              <div className="history-question">
                {entry.question.length > 80
                  ? entry.question.slice(0, 80) + "…"
                  : entry.question}
              </div>
              <div className="history-meta">
                {entry.status === "success" ? (
                  <span className="history-success">
                    ✓ {entry.row_count.toLocaleString()} rows ·{" "}
                    {entry.duration_ms}ms
                  </span>
                ) : (
                  <span className="history-error">✗ Error</span>
                )}
              </div>
            </button>
          ))}
        </div>
      )}
    </aside>
  );
};

export default QueryHistory;
