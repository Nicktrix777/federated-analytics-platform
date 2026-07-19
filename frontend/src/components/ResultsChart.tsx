import React, { useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";

interface ResultsChartProps {
  columns: string[];
  rows: unknown[][];
}

type ChartType = "bar" | "line" | "pie" | "auto";

/**
 * Smart chart visualization with:
 * - Robust numeric detection (handles stringified numbers from Trino NUMERIC/DECIMAL)
 * - Multi-series bar charts for multi-metric queries
 * - Better label column selection (prefers categorical columns)
 * - Manual chart type override
 * - Graceful handling of edge cases (single row, booleans, all-text data)
 */
const ResultsChart: React.FC<ResultsChartProps> = ({ columns, rows }) => {
  const [userChartType, setUserChartType] = useState<ChartType>("auto");

  const { chartOption, autoType, hasData } = useMemo(() => {
    if (rows.length === 0 || columns.length === 0) {
      return { chartOption: null, autoType: "bar" as ChartType, hasData: false };
    }

    // ── Column Classification ──────────────────────────────────
    // Coerce a value to number, handling Trino's stringified NUMERIC/DECIMAL
    const toNumber = (v: unknown): number | null => {
      if (v === null || v === undefined || v === "") return null;
      if (typeof v === "number") return isNaN(v) ? null : v;
      if (typeof v === "boolean") return v ? 1 : 0;
      if (typeof v === "string") {
        const cleaned = v.replace(/,/g, "").trim();
        const n = parseFloat(cleaned);
        return isNaN(n) ? null : n;
      }
      return null;
    };

    const isNumericColumn = (colIdx: number): boolean => {
      const vals = rows.map((r) => r[colIdx]).filter((v) => v !== null && v !== undefined && v !== "");
      if (vals.length === 0) return false;
      const numericCount = vals.filter((v) => toNumber(v) !== null).length;
      return numericCount / vals.length >= 0.8; // 80%+ numeric values = numeric column
    };

    const isBooleanColumn = (colIdx: number): boolean => {
      const vals = rows.map((r) => r[colIdx]).filter((v) => v !== null && v !== undefined && v !== "");
      if (vals.length === 0) return false;
      return vals.every((v) => typeof v === "boolean" || v === "true" || v === "false");
    };

    const isTimeColumn = (colIdx: number): boolean => {
      const name = columns[colIdx]?.toLowerCase() || "";
      return /date|year|month|week|time|day|quarter|period/.test(name);
    };

    const isCategoricalLabel = (colIdx: number): boolean => {
      if (isNumericColumn(colIdx) || isBooleanColumn(colIdx)) return false;
      return true;
    };

    // Classify all columns
    const numericCols: number[] = [];
    const categoricalCols: number[] = [];

    for (let i = 0; i < columns.length; i++) {
      if (isBooleanColumn(i)) continue; // Skip booleans for charting
      if (isNumericColumn(i)) numericCols.push(i);
      else if (isCategoricalLabel(i)) categoricalCols.push(i);
    }

    if (numericCols.length === 0) {
      return { chartOption: null, autoType: "bar" as ChartType, hasData: false };
    }

    // Pick best label column: prefer categorical columns with meaningful names
    const labelPreferenceKeywords = ["name", "department", "category", "region", "title", "label", "type", "group", "status", "project", "period", "quarter"];
    // Only use categoricalCols for labeling — never a numeric col
    let labelColIdx: number | null = categoricalCols[0] ?? null;
    for (const keyword of labelPreferenceKeywords) {
      const found = categoricalCols.find((i) => columns[i]?.toLowerCase().includes(keyword));
      if (found !== undefined) { labelColIdx = found; break; }
    }

    // Time series detection
    const hasTimeAxis = categoricalCols.some((i) => isTimeColumn(i));
    const firstNumIdx = numericCols[0];

    // Auto-detect chart type
    let autoType: ChartType = "bar";
    const total = rows.reduce((sum, r) => sum + (toNumber(r[firstNumIdx]) ?? 0), 0);
    const isPieable =
      rows.length <= 10 &&
      rows.length >= 2 &&
      total > 0 &&
      rows.every((r) => (toNumber(r[firstNumIdx]) ?? 0) >= 0) &&
      numericCols.length === 1 &&
      labelColIdx !== null; // Need a real categorical label for pie

    if (hasTimeAxis) autoType = "line";
    else if (isPieable) autoType = "pie";
    else autoType = "bar";

    // Coral monochrome ramp — a single-hue palette so charts read as one
    // color system (matches the v3 single-accent UI). Cycles for multi-series
    // and for per-row single-series bars.
    const baseColors = [
      "#d4816a", "#eb9c83", "#b5674f", "#f4c2b1",
      "#8f4d39", "#c9765d", "#e0a892", "#6b3829",
    ];
    // Shared dark-theme chart chrome.
    const AXIS = "#8a827b";
    const GRID = "rgba(255,255,255,0.06)";
    const AXIS_LINE = "rgba(255,255,255,0.12)";
    const TOOLTIP_BG = "rgba(8,8,9,0.96)";
    const accentFade = (o: number) => `rgba(212,129,106,${o})`;

    const effectiveType = userChartType === "auto" ? autoType : userChartType;

    // For pie, only use categorical labels. For bar/line, use categorical if available or row index.
    const labels = rows.map((r, i) =>
      labelColIdx !== null ? String(r[labelColIdx] ?? "") : `Row ${i + 1}`
    );

    // ── Pie Chart ─────────────────────────────────────────────
    if (effectiveType === "pie") {
      // If we don't have a categorical column but user forced pie, use row index labels
      const pieLabels = labelColIdx !== null
        ? rows.map((r) => String(r[labelColIdx!] ?? ""))
        : rows.map((_, i) => `Item ${i + 1}`);
      const values = rows.map((r) => toNumber(r[firstNumIdx]) ?? 0);
      return {
        autoType,
        hasData: true,
        chartOption: {
          backgroundColor: "transparent",
          tooltip: {
            trigger: "item",
            formatter: "{b}: {c} ({d}%)",
            backgroundColor: TOOLTIP_BG,
            borderColor: AXIS_LINE,
            textStyle: { color: "#f2efea" },
          },
          legend: {
            orient: "vertical",
            right: "5%",
            top: "middle",
            textStyle: { color: AXIS, fontSize: 11 },
          },
          series: [{
            type: "pie",
            radius: ["38%", "68%"],
            center: ["42%", "50%"],
            data: pieLabels.map((label, i) => ({ name: label, value: values[i] })),
            emphasis: { itemStyle: { shadowBlur: 8, shadowColor: "rgba(0,0,0,0.2)" } },
            itemStyle: { borderRadius: 3, borderColor: "#000000", borderWidth: 2 },
            label: { color: AXIS, fontSize: 11 },
            color: baseColors,
          }],
        },
      };
    }

    // ── Line Chart ────────────────────────────────────────────
    if (effectiveType === "line") {
      const series = numericCols.slice(0, 4).map((colIdx, si) => ({
        name: columns[colIdx],
        type: "line",
        data: rows.map((r) => toNumber(r[colIdx])),
        smooth: true,
        symbol: "circle",
        symbolSize: 5,
        lineStyle: { color: baseColors[si % baseColors.length], width: 2.5 },
        itemStyle: { color: baseColors[si % baseColors.length] },
        areaStyle: si === 0 ? {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: accentFade(0.2) },
              { offset: 1, color: accentFade(0.01) },
            ],
          },
        } : undefined,
      }));

      return {
        autoType,
        hasData: true,
        chartOption: {
          backgroundColor: "transparent",
          tooltip: {
            trigger: "axis",
            backgroundColor: TOOLTIP_BG,
            borderColor: AXIS_LINE,
            textStyle: { color: "#f2efea", fontSize: 12 },
          },
          legend: numericCols.length > 1 ? {
            textStyle: { color: AXIS, fontSize: 11 },
            top: 0,
          } : undefined,
          grid: { left: "3%", right: "4%", bottom: "15%", top: numericCols.length > 1 ? "15%" : "8%", containLabel: true },
          xAxis: {
            type: "category",
            data: labels,
            axisLabel: { color: AXIS, rotate: rows.length > 12 ? 45 : 0, fontSize: 11 },
            axisLine: { lineStyle: { color: AXIS_LINE } },
          },
          yAxis: {
            type: "value",
            axisLabel: { color: AXIS, fontSize: 11 },
            splitLine: { lineStyle: { color: GRID } },
          },
          series,
        },
      };
    }

    // ── Bar Chart (default — supports multi-series) ────────────
    const isHorizontal = rows.length > 8 || numericCols.length === 1;

    const series = numericCols.slice(0, 4).map((colIdx, si) => ({
      name: columns[colIdx],
      type: "bar",
      data: rows.map((r, ri) => ({
        value: toNumber(r[colIdx]),
        itemStyle: {
          color: numericCols.length > 1
            ? baseColors[si % baseColors.length]
            : baseColors[ri % baseColors.length],
          borderRadius: isHorizontal ? [0, 3, 3, 0] : [3, 3, 0, 0],
        },
      })),
      label: numericCols.length === 1 ? {
        show: true,
        position: isHorizontal ? "right" : "top",
        color: AXIS,
        fontSize: 10,
        formatter: (p: { value: number | null }) =>
          p.value !== null && typeof p.value === "number"
            ? (Number.isInteger(p.value) ? p.value.toLocaleString() : p.value.toFixed(2))
            : "",
      } : undefined,
    }));

    if (isHorizontal) {
      return {
        autoType,
        hasData: true,
        chartOption: {
          backgroundColor: "transparent",
          tooltip: {
            trigger: "axis",
            axisPointer: { type: "shadow" },
            backgroundColor: TOOLTIP_BG,
            borderColor: AXIS_LINE,
            textStyle: { color: "#f2efea", fontSize: 12 },
          },
          legend: numericCols.length > 1 ? { textStyle: { color: AXIS, fontSize: 11 } } : undefined,
          grid: { left: "3%", right: numericCols.length === 1 ? "12%" : "5%", bottom: "5%", top: numericCols.length > 1 ? "12%" : "5%", containLabel: true },
          xAxis: {
            type: "value",
            axisLabel: { color: AXIS, fontSize: 11 },
            splitLine: { lineStyle: { color: GRID } },
          },
          yAxis: {
            type: "category",
            data: labels,
            axisLabel: {
              color: AXIS,
              fontSize: 11,
              formatter: (val: string) => val.length > 22 ? val.slice(0, 22) + "…" : val,
            },
            axisLine: { lineStyle: { color: AXIS_LINE } },
          },
          series,
        },
      };
    }

    // Vertical bar
    return {
      autoType,
      hasData: true,
      chartOption: {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          backgroundColor: TOOLTIP_BG,
          borderColor: AXIS_LINE,
          textStyle: { color: "#f2efea", fontSize: 12 },
        },
        legend: numericCols.length > 1 ? { textStyle: { color: AXIS, fontSize: 11 } } : undefined,
        grid: { left: "3%", right: "4%", bottom: "15%", top: numericCols.length > 1 ? "15%" : "5%", containLabel: true },
        xAxis: {
          type: "category",
          data: labels,
          axisLabel: { color: AXIS, rotate: rows.length > 6 ? 35 : 0, fontSize: 11 },
          axisLine: { lineStyle: { color: AXIS_LINE } },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: AXIS, fontSize: 11 },
          splitLine: { lineStyle: { color: GRID } },
        },
        series,
      },
    };
  }, [columns, rows, userChartType]);

  if (!hasData || !chartOption) {
    return (
      <div className="card">
        <div className="empty-state">
          <span className="empty-icon">📉</span>
          <span>No numeric data to visualize</span>
        </div>
      </div>
    );
  }

  const chartTypeLabel =
    (userChartType !== "auto" ? userChartType : autoType).charAt(0).toUpperCase() +
    (userChartType !== "auto" ? userChartType : autoType).slice(1);

  return (
    <div className="card chart-card">
      <div className="card-header">
        <div className="card-title">
          <span className="section-title">Visualization</span>
          <span className="badge badge--chart">{chartTypeLabel}</span>
        </div>
        <div className="chart-type-controls">
          {(["auto", "bar", "line", "pie"] as ChartType[]).map((t) => (
            <button
              key={t}
              className={`chart-type-btn ${userChartType === t ? "chart-type-btn--active" : ""}`}
              onClick={() => setUserChartType(t)}
              title={t === "auto" ? "Auto-detect best chart type" : `Switch to ${t} chart`}
            >
              {t === "auto" ? "Auto" : t === "bar" ? "Bar" : t === "line" ? "Line" : "Pie"}
            </button>
          ))}
        </div>
      </div>
      <div className="card-body">
        <ReactECharts
          option={chartOption}
          style={{ height: "320px", width: "100%" }}
          opts={{ renderer: "svg" }}
        />
      </div>
    </div>
  );
};

export default ResultsChart;
