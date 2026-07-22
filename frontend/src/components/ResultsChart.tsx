import React, { useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import {
  classifyColumns,
  autoDetectChartType,
  toNumber,
  getChartTheme,
} from "../lib/chartTheme";
import { useTheme } from "../theme";

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
  const { theme } = useTheme();

  const { chartOption, autoType, hasData } = useMemo(() => {
    // Column roles (numeric/time/categorical, best label column) come from the
    // shared classifier in lib/chartTheme — the same logic DashboardWidgetCard
    // uses, so the query-results view and dashboard tiles agree.
    const cls = classifyColumns(columns, rows);
    if (!cls.hasData) {
      return { chartOption: null, autoType: "bar" as ChartType, hasData: false };
    }
    const { numericCols, labelColIdx, firstNumIdx } = cls;
    const autoType: ChartType = autoDetectChartType(rows, cls);
    // Theme-aware palette + chrome; recomputed when `theme` toggles (see deps).
    const ct = getChartTheme();
    const baseColors = ct.ramp;
    const AXIS = ct.axis, GRID = ct.grid, AXIS_LINE = ct.axisLine, TOOLTIP_BG = ct.tooltipBg, TEXT = ct.text;
    const accentFade = ct.accentFade;

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
          animation: true,
          animationDuration: 1000,
          animationEasing: "cubicOut",
          tooltip: {
            trigger: "item",
            formatter: "{b}: {c} ({d}%)",
            backgroundColor: TOOLTIP_BG,
            borderColor: AXIS_LINE,
            textStyle: { color: TEXT },
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
          animation: true,
          animationDuration: 1000,
          animationEasing: "cubicOut",
          tooltip: {
            trigger: "axis",
            backgroundColor: TOOLTIP_BG,
            borderColor: AXIS_LINE,
            textStyle: { color: TEXT, fontSize: 12 },
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
          animation: true,
          animationDuration: 1000,
          animationEasing: "cubicOut",
          tooltip: {
            trigger: "axis",
            axisPointer: { type: "shadow" },
            backgroundColor: TOOLTIP_BG,
            borderColor: AXIS_LINE,
            textStyle: { color: TEXT, fontSize: 12 },
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
        animation: true,
        animationDuration: 1000,
        animationEasing: "cubicOut",
        tooltip: {
          trigger: "axis",
          axisPointer: { type: "shadow" },
          backgroundColor: TOOLTIP_BG,
          borderColor: AXIS_LINE,
          textStyle: { color: TEXT, fontSize: 12 },
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
  }, [columns, rows, userChartType, theme]);

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
