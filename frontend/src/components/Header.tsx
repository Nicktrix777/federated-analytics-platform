import React from "react";
import type { DatasetMeta } from "../types";

interface HeaderProps {
  aiEnabled: boolean;
  onToggleAI: (enabled: boolean) => void;
  datasets: DatasetMeta[];
}

const Header: React.FC<HeaderProps> = ({ aiEnabled, onToggleAI, datasets }) => {
  return (
    <header className="header">
      <div className="header-left">
        <div className="logo">
          <span className="logo-icon">⬡</span>
          <div>
            <h1 className="logo-title">FederateIQ</h1>
            <span className="logo-subtitle">Federated Analytics Platform</span>
          </div>
        </div>

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
      </div>

      <div className="header-right">
        <div
          className="ai-toggle"
          title={
            aiEnabled
              ? "AI mode enabled — click to disable"
              : "AI mode disabled — click to enable"
          }
        >
          <span className="ai-toggle-label">AI Engine</span>
          <button
            id="ai-toggle-btn"
            className={`toggle-btn ${aiEnabled ? "toggle-btn--on" : "toggle-btn--off"}`}
            onClick={() => onToggleAI(!aiEnabled)}
            aria-label={`AI Engine ${aiEnabled ? "enabled" : "disabled"}`}
          >
            <span className="toggle-knob" />
          </button>
          <span
            className={`ai-status ${aiEnabled ? "ai-status--on" : "ai-status--off"}`}
          >
            {aiEnabled ? "ON" : "OFF"}
          </span>
        </div>

        <div className="connection-indicator" title="Connected to Core API">
          <span className="connection-dot" />
          <span>Connected</span>
        </div>
      </div>
    </header>
  );
};

export default Header;
