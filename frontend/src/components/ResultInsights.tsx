import React from "react";
import { readResult, suggestFollowUps, type Insight } from "../lib/insights";
import type { DatasetMeta, QueryPlan } from "../types";
import { Icon, type IconName } from "./ui/Icon";

interface Props {
  columns: string[];
  rows: unknown[][];
  plan?: QueryPlan;
  datasets?: DatasetMeta[];
  /** Run a suggested follow-up as the next turn in this conversation. */
  onFollowUp?: (question: string) => void;
}

function insightIcon(i: Insight): IconName {
  switch (i.kind) {
    case "trend":
      return i.direction === "down" ? "trending-down" : i.direction === "flat" ? "minus" : "trending-up";
    case "leader":
      return "arrow-up";
    case "concentration":
      return "chart-pie";
    case "spread":
      return "columns";
    case "outlier":
      return "spark";
    case "gaps":
      return "alert";
    case "scale":
      return "chart-bar";
    default:
      return "info";
  }
}

/**
 * The reading of a query result: what the data says, and what to ask next.
 *
 * Every sentence is derived from the returned rows (see lib/insights), so it
 * costs no extra round-trip and can't assert a figure the result doesn't
 * contain. Follow-ups are grounded in the schema of the tables the query
 * actually read, so each one names a real column.
 */
const ResultInsights: React.FC<Props> = ({ columns, rows, plan, datasets, onFollowUp }) => {
  const { summary, insights } = React.useMemo(() => readResult(columns, rows), [columns, rows]);
  const followUps = React.useMemo(
    () => suggestFollowUps({ columns, rows, plan, datasets }),
    [columns, rows, plan, datasets]
  );

  const shown = insights.slice(0, 4);
  // Render whenever there is anything to say. A result with no numeric column
  // produces no chart, which is precisely when the suggestions matter most —
  // bailing out here used to hide them along with the (empty) findings.
  if (shown.length === 0 && followUps.length === 0 && !summary) return null;

  return (
    <div className="card insights-card">
      <div className="card-header">
        <div className="card-title">
          <Icon name="lightbulb" size={14} className="insights-header-icon" />
          <span className="section-title">What this shows</span>
        </div>
      </div>
      <div className="card-body insights-body">
        <p className="insights-summary">{summary}</p>

        {shown.length > 0 && (
          <ul className="insights-list">
            {shown.map((insight, i) => (
              <li key={i} className={`insight-row insight-row--${insight.kind}`}>
                <Icon name={insightIcon(insight)} size={14} className="insight-icon" />
                <span className="insight-text">{insight.text}</span>
              </li>
            ))}
          </ul>
        )}

        {followUps.length > 0 && onFollowUp && (
          <div className="followups">
            <div className="followups-label">Ask next</div>
            <div className="followups-row">
              {followUps.map((q) => (
                <button
                  key={q}
                  className="followup-chip"
                  onClick={() => onFollowUp(q)}
                  title={`Ask: ${q}`}
                >
                  <span>{q}</span>
                  <Icon name="arrow-right" size={13} />
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ResultInsights;
