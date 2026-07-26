import React, { useState } from "react";
import { Icon } from "./ui/Icon";
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import sql from "react-syntax-highlighter/dist/esm/languages/hljs/sql";
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs";
import type { QueryPlan } from "../types";

SyntaxHighlighter.registerLanguage("sql", sql);

interface QueryPlanProps {
  plan: QueryPlan;
  executionTimeMs: number;
  rowCount: number;
  mode: string;
}

const QueryPlanView: React.FC<QueryPlanProps> = ({
  plan,
  executionTimeMs,
  rowCount,
  mode,
}) => {
  const [expanded, setExpanded] = useState(true);
  const [activeTab, setActiveTab] = useState<"sql" | "steps" | "json">("sql");

  const confidenceColor =
    plan.confidence >= 0.8
      ? "var(--green)"
      : plan.confidence >= 0.5
        ? "var(--amber)"
        : "var(--red)";

  const confidencePct = Math.round(plan.confidence * 100);

  return (
    <div className="card query-plan-card">
      <div
        className="card-header"
        onClick={() => setExpanded(!expanded)}
        style={{ cursor: "pointer" }}
      >
        <div className="card-title">
          <Icon name={expanded ? "chevron-down" : "chevron-right"} size={12} />
          <span className="section-title">Query Plan</span>
          {mode === "ai" && (
            <span className="badge badge--ai">AI</span>
          )}
          {mode === "sql" && (
            <span className="badge badge--sql">SQL</span>
          )}
        </div>
        <div className="plan-stats">
          {mode === "ai" && (
            <div
              className="confidence-meter"
              title={`AI Confidence: ${confidencePct}%`}
            >
              <span className="confidence-dot" style={{ background: confidenceColor }} />
              <span>{confidencePct}% confident</span>
            </div>
          )}
          <span className="stat-chip">{executionTimeMs}ms</span>
          <span className="stat-chip">{rowCount} rows</span>
        </div>
      </div>

      {expanded && (
        <div className="card-body">
          {plan.explanation && (
            <div className="plan-explanation">
              {plan.explanation}
            </div>
          )}

          <div className="tab-bar">
            <button
              className={`tab-btn ${activeTab === "sql" ? "tab-btn--active" : ""}`}
              onClick={() => setActiveTab("sql")}
            >
              SQL
            </button>
            {plan.steps && plan.steps.length > 0 && (
              <button
                className={`tab-btn ${activeTab === "steps" ? "tab-btn--active" : ""}`}
                onClick={() => setActiveTab("steps")}
              >
                Steps ({plan.steps.length})
              </button>
            )}
            <button
              className={`tab-btn ${activeTab === "json" ? "tab-btn--active" : ""}`}
              onClick={() => setActiveTab("json")}
            >
              Raw JSON
            </button>
          </div>

          {activeTab === "sql" && (
            <div className="code-block">
              <SyntaxHighlighter
                language="sql"
                style={atomOneDark}
                customStyle={{
                  background: "var(--bg-code)",
                  borderRadius: "8px",
                  padding: "16px",
                  margin: 0,
                  fontSize: "13px",
                  lineHeight: "1.6",
                }}
              >
                {plan.sql}
              </SyntaxHighlighter>
            </div>
          )}

          {activeTab === "steps" && (
            <div className="steps-list">
              {plan.steps.map((step) => (
                <div key={step.step_id} className="step-item">
                  <div className="step-number">{step.step_id}</div>
                  <div className="step-content">
                    <div className="step-description">{step.description}</div>
                    <div className="step-path">
                      <span className="step-catalog">{step.catalog}</span>
                      <span className="step-sep">.</span>
                      <span className="step-schema">{step.schema_name}</span>
                      <span className="step-sep">.</span>
                      <span className="step-table">{step.table}</span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {activeTab === "json" && (
            <div className="code-block">
              <SyntaxHighlighter
                language="json"
                style={atomOneDark}
                customStyle={{
                  background: "var(--bg-code)",
                  borderRadius: "8px",
                  padding: "16px",
                  margin: 0,
                  fontSize: "12px",
                  lineHeight: "1.6",
                }}
              >
                {JSON.stringify(plan, null, 2)}
              </SyntaxHighlighter>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default QueryPlanView;
