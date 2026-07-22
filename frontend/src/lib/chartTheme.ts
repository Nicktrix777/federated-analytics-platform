// Shared chart classifier + option builder.
//
// The column-role classification (numeric/time/categorical detection, best
// label column, multi-series grouping) was previously inlined inside
// ResultsChart. It is extracted here so DashboardWidgetCard consumes the SAME
// logic — killing the old col0=label / col1=value assumption and adding
// multi-series + robust numeric handling to dashboard tiles.
//
// The palette is the coral single-hue ramp from the v3 design tokens
// (--ramp-1..6). Keeping it as constants here (rather than reading computed
// CSS vars at render time) matches ResultsChart's existing values exactly and
// avoids a getComputedStyle round-trip inside echarts option building.

// Static greyscale fallback ramp (dark). Live values come from getChartTheme(),
// which reads the active theme's --ramp-* / chrome tokens off :root so charts
// recolor when the light/dark theme toggles.
export const RAMP = ["#ffffff", "#cfcfcf", "#a0a0a0", "#757575", "#505050", "#333333"];

export interface ChartTheme {
  ramp: string[];
  axis: string;
  grid: string;
  axisLine: string;
  text: string;
  tooltipBg: string;
  accentFade: (o: number) => string;
}

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined" || typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

// Read the chart palette + chrome from the current theme's CSS tokens. Call
// this at option-build time (inside a render that depends on the theme) so the
// returned colors track light/dark. Charts are monochrome: identity comes from
// lightness steps of the greyscale ramp plus the legend, never hue alone.
export function getChartTheme(): ChartTheme {
  const isLight =
    typeof document !== "undefined" &&
    document.documentElement.getAttribute("data-theme") === "light";
  return {
    ramp: [1, 2, 3, 4, 5, 6].map((i) => cssVar(`--ramp-${i}`, RAMP[i - 1])),
    axis: cssVar("--text-muted", isLight ? "rgba(0,0,0,0.45)" : "rgba(255,255,255,0.42)"),
    grid: cssVar("--border-subtle", isLight ? "rgba(0,0,0,0.08)" : "rgba(255,255,255,0.07)"),
    axisLine: cssVar("--border-default", isLight ? "rgba(0,0,0,0.14)" : "rgba(255,255,255,0.14)"),
    text: cssVar("--text-primary", isLight ? "#0a0a0a" : "#ffffff"),
    tooltipBg: cssVar("--bg-card", isLight ? "#ffffff" : "#101010"),
    accentFade: (o: number) => (isLight ? `rgba(0,0,0,${o})` : `rgba(255,255,255,${o})`),
  };
}

// Coerce a value to number, handling Trino's stringified NUMERIC/DECIMAL.
export const toNumber = (v: unknown): number | null => {
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

export interface ColumnClassification {
  numericCols: number[];
  categoricalCols: number[];
  labelColIdx: number | null;
  hasTimeAxis: boolean;
  firstNumIdx: number;
  hasData: boolean;
}

// Classify every column into numeric / categorical, pick the best label
// column, and detect a time axis. Mirrors ResultsChart's original logic.
export function classifyColumns(
  columns: string[],
  rows: unknown[][]
): ColumnClassification {
  const empty: ColumnClassification = {
    numericCols: [],
    categoricalCols: [],
    labelColIdx: null,
    hasTimeAxis: false,
    firstNumIdx: 0,
    hasData: false,
  };
  if (rows.length === 0 || columns.length === 0) return empty;

  const isNumericColumn = (colIdx: number): boolean => {
    const vals = rows.map((r) => r[colIdx]).filter((v) => v !== null && v !== undefined && v !== "");
    if (vals.length === 0) return false;
    const numericCount = vals.filter((v) => toNumber(v) !== null).length;
    return numericCount / vals.length >= 0.8; // 80%+ numeric = numeric column
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

  const numericCols: number[] = [];
  const categoricalCols: number[] = [];
  for (let i = 0; i < columns.length; i++) {
    if (isBooleanColumn(i)) continue; // skip booleans for charting
    if (isNumericColumn(i)) numericCols.push(i);
    else categoricalCols.push(i);
  }

  if (numericCols.length === 0) {
    return { ...empty, numericCols, categoricalCols };
  }

  // Prefer a categorical column with a meaningful name for labelling.
  const labelPreferenceKeywords = [
    "name", "department", "category", "region", "title", "label",
    "type", "group", "status", "project", "period", "quarter",
  ];
  let labelColIdx: number | null = categoricalCols[0] ?? null;
  for (const keyword of labelPreferenceKeywords) {
    const found = categoricalCols.find((i) => columns[i]?.toLowerCase().includes(keyword));
    if (found !== undefined) { labelColIdx = found; break; }
  }

  return {
    numericCols,
    categoricalCols,
    labelColIdx,
    hasTimeAxis: categoricalCols.some((i) => isTimeColumn(i)),
    firstNumIdx: numericCols[0],
    hasData: true,
  };
}

// Auto-pick bar / line / pie from the data shape. (Widget cards layer their own
// number/area/gauge/table handling on top of this.)
export function autoDetectChartType(
  rows: unknown[][],
  cls: ColumnClassification
): "bar" | "line" | "pie" {
  if (cls.hasTimeAxis) return "line";
  const total = rows.reduce((sum, r) => sum + (toNumber(r[cls.firstNumIdx]) ?? 0), 0);
  const isPieable =
    rows.length <= 10 &&
    rows.length >= 2 &&
    total > 0 &&
    rows.every((r) => (toNumber(r[cls.firstNumIdx]) ?? 0) >= 0) &&
    cls.numericCols.length === 1 &&
    cls.labelColIdx !== null;
  return isPieable ? "pie" : "bar";
}

// Build a compact echarts option for a dashboard tile. Handles multi-series
// bar (vertical/horizontal), line, area and pie. `colorsOverride` (from a
// widget's chart_config) wins over the theme ramp. Reads the active theme so
// colors track light/dark. Returns null when there's nothing to plot.
export function buildWidgetChartOption(
  type: "bar" | "line" | "area" | "pie",
  columns: string[],
  rows: unknown[][],
  cls: ColumnClassification,
  colorsOverride?: string[]
): Record<string, unknown> | null {
  if (!cls.hasData) return null;
  const t = getChartTheme();
  const colors = colorsOverride && colorsOverride.length > 0 ? colorsOverride : t.ramp;
  const { numericCols, labelColIdx, firstNumIdx } = cls;
  const labels = rows.map((r, i) =>
    labelColIdx !== null ? String(r[labelColIdx] ?? "") : `Row ${i + 1}`
  );
  const base = {
    backgroundColor: "transparent",
    animation: true,
    animationDuration: 800,
    animationEasing: "cubicOut",
  };

  if (type === "pie") {
    const values = rows.map((r) => toNumber(r[firstNumIdx]) ?? 0);
    return {
      ...base,
      tooltip: {
        trigger: "item", formatter: "{b}: {c} ({d}%)",
        backgroundColor: t.tooltipBg, borderColor: t.axisLine, textStyle: { color: t.text },
      },
      series: [{
        type: "pie",
        radius: ["42%", "70%"],
        data: labels.map((label, i) => ({ name: label, value: values[i] })),
        itemStyle: { borderRadius: 3, borderColor: t.tooltipBg, borderWidth: 2 },
        label: { color: t.axis, fontSize: 11 },
        color: colors,
      }],
    };
  }

  const isLine = type === "line" || type === "area";
  const isArea = type === "area";

  if (isLine) {
    const series = numericCols.slice(0, 4).map((colIdx, si) => ({
      name: columns[colIdx],
      type: "line",
      data: rows.map((r) => toNumber(r[colIdx])),
      smooth: true,
      symbol: "circle",
      symbolSize: 5,
      lineStyle: { color: colors[si % colors.length], width: 2.5 },
      itemStyle: { color: colors[si % colors.length] },
      areaStyle: isArea || si === 0 ? {
        color: {
          type: "linear", x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: t.accentFade(isArea ? 0.28 : 0.2) },
            { offset: 1, color: t.accentFade(0.01) },
          ],
        },
      } : undefined,
    }));
    return {
      ...base,
      tooltip: { trigger: "axis", backgroundColor: t.tooltipBg, borderColor: t.axisLine, textStyle: { color: t.text, fontSize: 12 } },
      legend: numericCols.length > 1 ? { textStyle: { color: t.axis, fontSize: 10 }, top: 0 } : undefined,
      grid: { left: "3%", right: "4%", bottom: "12%", top: numericCols.length > 1 ? "16%" : "8%", containLabel: true },
      xAxis: {
        type: "category", data: labels,
        axisLabel: { color: t.axis, rotate: rows.length > 12 ? 45 : 0, fontSize: 10 },
        axisLine: { lineStyle: { color: t.axisLine } },
      },
      yAxis: { type: "value", axisLabel: { color: t.axis, fontSize: 10 }, splitLine: { lineStyle: { color: t.grid } } },
      series,
    };
  }

  // Bar (multi-series; horizontal for many rows or single metric)
  const isHorizontal = rows.length > 8 || numericCols.length === 1;
  const series = numericCols.slice(0, 4).map((colIdx, si) => ({
    name: columns[colIdx],
    type: "bar",
    data: rows.map((r, ri) => ({
      value: toNumber(r[colIdx]),
      itemStyle: {
        color: numericCols.length > 1 ? colors[si % colors.length] : colors[ri % colors.length],
        borderRadius: isHorizontal ? [0, 3, 3, 0] : [3, 3, 0, 0],
      },
    })),
  }));
  const catAxis = {
    type: "category", data: labels,
    axisLabel: {
      color: t.axis, fontSize: 10,
      formatter: (val: string) => (val.length > 18 ? val.slice(0, 18) + "…" : val),
    },
    axisLine: { lineStyle: { color: t.axisLine } },
  };
  const valAxis = { type: "value", axisLabel: { color: t.axis, fontSize: 10 }, splitLine: { lineStyle: { color: t.grid } } };
  return {
    ...base,
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, backgroundColor: t.tooltipBg, borderColor: t.axisLine, textStyle: { color: t.text, fontSize: 12 } },
    legend: numericCols.length > 1 ? { textStyle: { color: t.axis, fontSize: 10 }, top: 0 } : undefined,
    grid: { left: "3%", right: "4%", bottom: isHorizontal ? "5%" : "12%", top: numericCols.length > 1 ? "16%" : "6%", containLabel: true },
    xAxis: isHorizontal ? valAxis : catAxis,
    yAxis: isHorizontal ? catAxis : valAxis,
    series,
  };
}

// Build a single-value gauge option (monochrome progress arc, theme-aware).
export function buildGaugeOption(value: number, max: number): Record<string, unknown> {
  const t = getChartTheme();
  const safeMax = max > 0 ? max : Math.max(100, value);
  return {
    backgroundColor: "transparent",
    series: [{
      type: "gauge",
      min: 0,
      max: safeMax,
      progress: { show: true, width: 10, itemStyle: { color: t.ramp[0] } },
      axisLine: { lineStyle: { width: 10, color: [[1, t.grid]] } },
      axisTick: { show: false },
      splitLine: { length: 8, lineStyle: { color: t.axisLine } },
      axisLabel: { color: t.axis, fontSize: 9, distance: 12 },
      pointer: { itemStyle: { color: t.ramp[0] } },
      anchor: { show: true, size: 10, itemStyle: { color: t.ramp[2] } },
      detail: { valueAnimation: true, color: t.text, fontSize: 22, fontWeight: 700, offsetCenter: [0, "62%"] },
      data: [{ value }],
    }],
  };
}
