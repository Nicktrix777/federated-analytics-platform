import React from "react";
import { buildDashboardInsights, type Insight, type WidgetData } from "../lib/insights";
import { Icon, type IconName } from "./ui/Icon";

interface Props {
  /** Live results, keyed by widget id, collected as each tile loads. */
  widgetData: WidgetData[];
  /** Tiles still fetching — drives the "reading N widgets" state. */
  pending: number;
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
    case "scale":
      return "chart-bar";
    default:
      return "info";
  }
}

/**
 * Headline findings across a dashboard's tiles.
 *
 * Computed from the rows the widgets already fetched — no second query, no
 * model call — so the strip fills in as tiles land and always agrees with what
 * is drawn below it. At most one finding per widget, so one busy tile can't
 * crowd out the rest.
 */
const DashboardInsights: React.FC<Props> = ({ widgetData, pending }) => {
  const insights = React.useMemo(() => buildDashboardInsights(widgetData), [widgetData]);

  if (insights.length === 0) {
    if (pending > 0) {
      return (
        <div className="dash-insights dash-insights--loading">
          <Icon name="lightbulb" size={14} />
          <span>Reading {pending} widget{pending === 1 ? "" : "s"}…</span>
        </div>
      );
    }
    return null;
  }

  return (
    <section className="dash-insights" aria-label="Dashboard insights">
      <div className="dash-insights-head">
        <Icon name="lightbulb" size={14} />
        <span>Insights</span>
        {pending > 0 && <span className="dash-insights-pending">+{pending} loading</span>}
      </div>
      <div className="dash-insights-grid">
        {insights.map((insight, i) => (
          <article key={i} className="dash-insight">
            <Icon name={insightIcon(insight)} size={14} className="dash-insight-icon" />
            <div className="dash-insight-body">
              <p className="dash-insight-text">{insight.text}</p>
              {insight.source && <span className="dash-insight-source">{insight.source}</span>}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
};

export default DashboardInsights;
