import React, { useMemo, useState, useRef } from "react";
import ReactECharts from "echarts-for-react";
import {
  classifyColumns,
  recommendChart,
  prepareChart,
  buildChartOption,
  toNumber,
  formatMetricValue,
  MAX_SERIES,
  type ChartForm,
} from "../lib/chartTheme";
import { humanizeColumn, formatCompact, formatFull } from "../lib/format";
import { useTheme } from "../theme";
import { Icon, type IconName } from "./ui/Icon";

interface ResultsChartProps {
  columns: string[];
  rows: unknown[][];
}

type Selection = ChartForm | "auto";

const TYPE_ICONS: Record<ChartForm, IconName> = {
  bar: "chart-bar",
  line: "chart-line",
  area: "chart-area",
  pie: "chart-pie",
};

/**
 * Query-result visualisation.
 *
 * The charting rules — column roles, unit detection, sorting, top-N folding,
 * marks and chrome — all live in lib/chartTheme so this view and dashboard
 * tiles render identically. What's local here is the type switcher, the
 * caption that says what's actually plotted, and PNG export.
 *
 * On Auto the recommender may decide the data has no chart worth drawing (one
 * aggregate row, or a metric identical across every category). Rather than
 * emit chart-shaped furniture we show the finding as a stat — the reader can
 * still force any chart type from the toolbar.
 */
const ResultsChart: React.FC<ResultsChartProps> = ({ columns, rows }) => {
  const [selection, setSelection] = useState<Selection>("auto");
  const { theme } = useTheme();
  const chartRef = useRef<ReactECharts>(null);

  const view = useMemo(() => {
    const cls = classifyColumns(columns, rows);
    const rec = recommendChart(columns, rows, cls);

    // Auto deferred to a non-chart form — show the stat instead.
    if (selection === "auto" && (rec.form === "stat" || rec.form === "table")) {
      return { kind: rec.form, reason: rec.reason, cls } as const;
    }

    const form: ChartForm =
      selection === "auto"
        ? (rec.form as ChartForm)
        : selection;
    const prep = prepareChart(columns, rows, cls, form, { labelChars: 22 });
    if (!prep.hasData) {
      return {
        kind: "table" as const,
        reason: "No numeric column to plot — the table below has the full result.",
        cls,
      };
    }
    return {
      kind: "chart" as const,
      form,
      prep,
      option: buildChartOption(form, prep, { density: "comfortable", themeHint: theme }),
      autoForm: rec.form,
      cls,
    };
    // `theme` is a real dependency: option colours come from CSS tokens.
  }, [columns, rows, selection, theme]);

  const handleDownload = () => {
    const instance = chartRef.current?.getEchartsInstance();
    if (!instance) return;
    const url = instance.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "transparent" });
    const a = document.createElement("a");
    a.href = url;
    a.download = "chart.png";
    a.click();
  };

  const typeSwitcher = (
    <div className="chart-toolbar">
      <div className="chart-type-controls" role="group" aria-label="Chart type">
        <button
          className={`chart-type-btn ${selection === "auto" ? "chart-type-btn--active" : ""}`}
          onClick={() => setSelection("auto")}
          title="Let the data pick the right form"
        >
          Auto
        </button>
        {(["bar", "line", "area", "pie"] as const).map((t) => (
          <button
            key={t}
            className={`chart-type-btn chart-type-btn--icon ${
              selection === t ? "chart-type-btn--active" : ""
            }`}
            onClick={() => setSelection(t)}
            title={`Switch to ${t} chart`}
            aria-label={`${t} chart`}
            aria-pressed={selection === t}
          >
            <Icon name={TYPE_ICONS[t]} size={14} />
          </button>
        ))}
      </div>
      {view.kind === "chart" && (
        <button
          className="chart-type-btn chart-type-btn--icon"
          onClick={handleDownload}
          title="Download chart as PNG"
          aria-label="Download chart as PNG"
        >
          <Icon name="download" size={14} />
        </button>
      )}
    </div>
  );

  // ── Non-chart forms ─────────────────────────────────────────
  if (view.kind === "stat" || view.kind === "table") {
    const cls = view.cls;
    const metricIdx = cls.hasData ? cls.firstNumIdx : 0;
    const values = rows
      .map((r) => toNumber(r[metricIdx]))
      .filter((v): v is number => v !== null);
    const headline = values.length > 0 ? values[0] : null;
    const metricName = cls.hasData ? humanizeColumn(columns[metricIdx]) : "";

    return (
      <div className="card chart-card">
        <div className="card-header">
          <div className="card-title">
            <span className="section-title">Visualization</span>
            <span className="badge badge--chart">
              {view.kind === "stat" ? "Auto · Stat" : "Auto · Table"}
            </span>
          </div>
          {typeSwitcher}
        </div>
        <div className="card-body">
          {view.kind === "stat" && headline !== null ? (
            <div className="stat-callout">
              <div className="stat-callout-value" title={formatFull(headline)}>
                {formatCompact(headline)}
              </div>
              {metricName && <div className="stat-callout-label">{metricName}</div>}
              {rows.length > 1 && (
                <div className="stat-callout-sub">
                  across {rows.length} {(
                    cls.labelColIdx !== null
                      ? humanizeColumn(columns[cls.labelColIdx]).toLowerCase()
                      : "row"
                  )} values
                </div>
              )}
            </div>
          ) : (
            <div className="empty-state">
              <Icon name="chart-area-off" size={22} className="empty-state-icon" />
              <span>{view.reason}</span>
            </div>
          )}
          <p className="chart-caption">{view.reason}</p>
        </div>
      </div>
    );
  }

  const { prep, option, form, autoForm } = view;
  if (!option) return null;

  // Caption: state plainly what is drawn, including anything the chart had to
  // leave out. A silently truncated chart reads as the whole picture.
  const captionParts: string[] = [];
  const metric = prep.series.map((s) => s.name).join(", ");
  captionParts.push(
    prep.labelName ? `${metric} by ${prep.labelName.toLowerCase()}` : metric
  );
  if (prep.unit !== "count") captionParts.push(`measured in ${prep.unit}`);
  if (prep.sorted) captionParts.push("ranked high to low");
  if (prep.foldedCount > 0) {
    captionParts.push(
      `top ${prep.labels.length - 1} of ${prep.totalCategories}, remainder grouped as Other`
    );
  }
  if (prep.droppedSeries.length > 0) {
    captionParts.push(
      `${prep.droppedSeries.map(humanizeColumn).join(", ")} not plotted (max ${MAX_SERIES} series)`
    );
  }
  if (prep.scaleDropped.length > 0) {
    captionParts.push(
      `${prep.scaleDropped
        .map(humanizeColumn)
        .join(", ")} left out — a different order of magnitude, so it needs its own chart`
    );
  }
  const range = prep.series[0]?.values.filter((v): v is number => v !== null) ?? [];
  if (range.length > 1) {
    captionParts.push(
      `range ${formatMetricValue(Math.min(...range), prep.unit, true)}–${formatMetricValue(
        Math.max(...range),
        prep.unit,
        true
      )}`
    );
  }

  return (
    <div className="card chart-card">
      <div className="card-header">
        <div className="card-title">
          <span className="section-title">Visualization</span>
          <span className="badge badge--chart">
            {selection === "auto" ? `Auto · ${autoForm}` : form}
          </span>
        </div>
        {typeSwitcher}
      </div>
      <div className="card-body">
        <ReactECharts
          ref={chartRef}
          key={`${form}-${theme}`}
          option={option}
          notMerge
          style={{ height: "340px", width: "100%" }}
          opts={{ renderer: "svg" }}
        />
        <p className="chart-caption">{captionParts.join(" · ")}</p>
      </div>
    </div>
  );
};

export default ResultsChart;
