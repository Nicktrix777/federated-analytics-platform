import React, { useMemo } from "react";
import ReactECharts from "echarts-for-react";

interface ResultsChartProps {
  columns: string[];
  rows: unknown[][];
}

/**
 * Auto-detects the best chart type based on result shape:
 * - 2 columns (label + number) → bar chart
 * - 3+ columns with time-like first column → line chart
 * - 2 columns summing to meaningful proportions → pie chart
 * - Fallback → horizontal bar chart
 */
const ResultsChart: React.FC<ResultsChartProps> = ({ columns, rows }) => {
  const chartOption = useMemo(() => {
    if (rows.length === 0 || columns.length === 0) return null;

    // Find numeric and categorical columns
    const numericCols: number[] = [];
    const categoricalCols: number[] = [];

    columns.forEach((_, i) => {
      const values = rows.map((r) => r[i]).filter((v) => v !== null);
      const isNum = values.every((v) => typeof v === "number");
      if (isNum) numericCols.push(i);
      else categoricalCols.push(i);
    });

    if (numericCols.length === 0) return null;

    const catIdx = categoricalCols[0] ?? 0;
    const numIdx = numericCols[0] ?? 1;

    const labels = rows.map((r) => String(r[catIdx] ?? ""));
    const values = rows.map((r) => Number(r[numIdx] ?? 0));

    // Detect time-series (contains date/year/month keywords)
    const isTimeSeries = columns[catIdx]
      ?.toLowerCase()
      .match(/(date|year|month|week|time|day)/);

    // Detect proportional data (good for pie)
    const total = values.reduce((a, b) => a + b, 0);
    const isPieable =
      rows.length <= 8 &&
      rows.length >= 2 &&
      total > 0 &&
      values.every((v) => v >= 0) &&
      columns.length === 2;

    const baseColors = [
      "#3b82f6",
      "#8b5cf6",
      "#06b6d4",
      "#10b981",
      "#f59e0b",
      "#ef4444",
      "#ec4899",
      "#84cc16",
    ];

    if (isPieable && !isTimeSeries) {
      // Pie chart
      return {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "item",
          formatter: "{b}: {c} ({d}%)",
          backgroundColor: "rgba(17, 24, 39, 0.9)",
          borderColor: "rgba(255,255,255,0.1)",
          textStyle: { color: "#e5e7eb" },
        },
        legend: {
          orient: "vertical",
          right: "5%",
          top: "middle",
          textStyle: { color: "#9ca3af", fontSize: 12 },
        },
        series: [
          {
            type: "pie",
            radius: ["40%", "70%"],
            center: ["40%", "50%"],
            data: labels.map((label, i) => ({
              name: label,
              value: values[i],
            })),
            emphasis: {
              itemStyle: {
                shadowBlur: 10,
                shadowOffsetX: 0,
                shadowColor: "rgba(0, 0, 0, 0.5)",
              },
            },
            itemStyle: {
              borderRadius: 4,
              borderColor: "#0a0f1e",
              borderWidth: 2,
            },
            label: {
              color: "#9ca3af",
              fontSize: 11,
            },
            color: baseColors,
          },
        ],
      };
    }

    if (isTimeSeries) {
      // Line chart
      return {
        backgroundColor: "transparent",
        tooltip: {
          trigger: "axis",
          backgroundColor: "rgba(17, 24, 39, 0.9)",
          borderColor: "rgba(255,255,255,0.1)",
          textStyle: { color: "#e5e7eb" },
          axisPointer: { lineStyle: { color: "rgba(255,255,255,0.2)" } },
        },
        grid: {
          left: "3%",
          right: "4%",
          bottom: "15%",
          top: "10%",
          containLabel: true,
        },
        xAxis: {
          type: "category",
          data: labels,
          axisLabel: {
            color: "#6b7280",
            rotate: rows.length > 12 ? 45 : 0,
            fontSize: 11,
          },
          axisLine: { lineStyle: { color: "rgba(255,255,255,0.1)" } },
          splitLine: { show: false },
        },
        yAxis: {
          type: "value",
          axisLabel: { color: "#6b7280", fontSize: 11 },
          splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
        },
        series: [
          {
            type: "line",
            data: values,
            smooth: true,
            symbol: "circle",
            symbolSize: 6,
            lineStyle: { color: "#3b82f6", width: 2.5 },
            itemStyle: { color: "#3b82f6" },
            areaStyle: {
              color: {
                type: "linear",
                x: 0,
                y: 0,
                x2: 0,
                y2: 1,
                colorStops: [
                  { offset: 0, color: "rgba(59, 130, 246, 0.3)" },
                  { offset: 1, color: "rgba(59, 130, 246, 0.02)" },
                ],
              },
            },
          },
        ],
      };
    }

    // Default: horizontal bar chart (works for most analytical queries)
    return {
      backgroundColor: "transparent",
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        backgroundColor: "rgba(17, 24, 39, 0.9)",
        borderColor: "rgba(255,255,255,0.1)",
        textStyle: { color: "#e5e7eb" },
      },
      grid: {
        left: "3%",
        right: "8%",
        bottom: "5%",
        top: "5%",
        containLabel: true,
      },
      xAxis: {
        type: "value",
        axisLabel: { color: "#6b7280", fontSize: 11 },
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.05)" } },
      },
      yAxis: {
        type: "category",
        data: labels,
        axisLabel: {
          color: "#9ca3af",
          fontSize: 11,
          formatter: (val: string) =>
            val.length > 20 ? val.slice(0, 20) + "…" : val,
        },
        axisLine: { lineStyle: { color: "rgba(255,255,255,0.1)" } },
      },
      series: [
        {
          type: "bar",
          data: values.map((v, i) => ({
            value: v,
            itemStyle: {
              color: baseColors[i % baseColors.length],
              borderRadius: [0, 4, 4, 0],
            },
          })),
          emphasis: {
            itemStyle: { opacity: 0.8 },
          },
          label: {
            show: true,
            position: "right",
            color: "#6b7280",
            fontSize: 11,
            formatter: (p: { value: number }) =>
              typeof p.value === "number" && !Number.isInteger(p.value)
                ? p.value.toFixed(2)
                : p.value?.toLocaleString(),
          },
        },
      ],
    };
  }, [columns, rows]);

  if (!chartOption) {
    return (
      <div className="card">
        <div className="empty-state">
          <span className="empty-icon">📉</span>
          <span>No numeric data to visualize</span>
        </div>
      </div>
    );
  }

  const chartType =
    chartOption.series?.[0]?.type === "pie"
      ? "Donut"
      : chartOption.series?.[0]?.type === "line"
        ? "Line"
        : "Bar";

  return (
    <div className="card chart-card">
      <div className="card-header">
        <div className="card-title">
          <span className="section-title-icon">📈</span>
          <span className="section-title">Visualization</span>
          <span className="badge badge--chart">{chartType} Chart</span>
        </div>
        <span className="chart-auto-label">Auto-detected</span>
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
