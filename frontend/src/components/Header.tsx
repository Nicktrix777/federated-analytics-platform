import React from "react";
import type { DatasetMeta } from "../types";
import { useTheme } from "../theme";

interface HeaderProps {
  aiEnabled?: boolean;
  onToggleAI?: (enabled: boolean) => void;
  datasets?: DatasetMeta[];
  onNewQuery?: () => void;
  onToggleUpload?: () => void;
  showUpload?: boolean;
}

const Header: React.FC<HeaderProps> = ({
  aiEnabled = true,
  onToggleAI,
  datasets = [],
  onNewQuery,
  onToggleUpload,
  showUpload = false,
}) => {
  const { theme, toggle } = useTheme();
  return (
    <header className="header">
      <div className="header-left">
        <div className="logo">
          <span className="logo-mark">F</span>
          <div>
            <h1 className="logo-title">FederateIQ</h1>
            <span className="logo-subtitle">Federated Analytics</span>
          </div>
        </div>

        {datasets.length > 0 && (
          <div className="source-badges">
            {datasets.map((ds) => (
              <span
                key={ds.id}
                className={`source-badge source-badge--${ds.source_type}`}
              >
                <span className="source-badge-dot" />
                {ds.source_type === "postgresql" ? "🐘" : "🍃"} {ds.name}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="header-right">
        <button
          className="header-btn theme-toggle"
          onClick={toggle}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>

        {onToggleUpload && (
          <button
            id="upload-btn"
            className={`header-btn ${showUpload ? "header-btn--active" : ""}`}
            onClick={onToggleUpload}
            title="Upload a CSV or Excel file"
          >
            ↑ Upload
          </button>
        )}

        {onNewQuery && (
          <button
            id="new-query-btn"
            className="header-btn header-btn--primary"
            onClick={onNewQuery}
            title="Clear results and start a new query"
          >
            + New Query
          </button>
        )}

        {onToggleAI && (
          <div
            className="ai-toggle"
            title={
              aiEnabled
                ? "AI mode enabled — click to disable"
                : "AI mode disabled — click to enable"
            }
          >
            <span className="ai-toggle-label">AI</span>
            <button
              id="ai-toggle-btn"
              className={`toggle-btn ${aiEnabled ? "toggle-btn--on" : "toggle-btn--off"}`}
              onClick={() => onToggleAI(!aiEnabled)}
              aria-label={`AI Engine ${aiEnabled ? "enabled" : "disabled"}`}
            >
              <span className="toggle-knob" />
            </button>
          </div>
        )}

        <div className="connection-indicator" title="Connected to Core API">
          <span className="connection-dot" />
          <span>Live</span>
        </div>
      </div>
    </header>
  );
};

export default Header;
