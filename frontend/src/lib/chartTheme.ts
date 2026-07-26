// Shared chart classifier, data preparation and echarts option builder.
//
// One pipeline serves both the Query Studio result chart and dashboard tiles:
//
//   classifyColumns()  → what each column IS (metric / category / time)
//   prepareChart()     → what actually gets plotted (sorted, top-N, capped)
//   buildChartOption() → the echarts option (marks, chrome, labels, tooltip)
//
// Both call sites used to build their own options and drifted apart. They now
// share every rule below, so a chart looks and behaves the same wherever it
// is rendered.
//
// ── Palette note ──────────────────────────────────────────────
// Hue is the identity channel for series; the chrome (axes, grid, labels,
// surfaces) stays monochrome so only the data is coloured. Slots are assigned
// in a FIXED order and never cycled — a filter that removes a series must not
// repaint the survivors.
//
// The steps were validated, not eyeballed (dataviz validate_palette.js,
// adjacent-pair mode, which for a pie maps exactly to ring-adjacent slices):
//
//   dark  #56B4E9,#009E73,#E69F00,#CC79A7 on #101010 → CVD ΔE 11.4, normal 18.4, contrast ≥3:1
//   light #0072B2,#009E73,#D55E00,#CC79A7 on #ffffff → CVD ΔE 11.0, normal 16.4, contrast ≥3:1
//
// Four slots is the cap. A 9th (or 5th) series is never a generated hue: the
// extra metric columns are reported as "not plotted" instead. Identity never
// rests on hue alone either — a legend is always present for two or more
// series, lines carry distinct dash patterns, and bars are named on the axis.

import {
  formatCompact,
  formatFull,
  humanizeColumn,
  truncateLabel,
  looksTemporal,
  parseTemporal,
  formatTemporal,
  detectGranularity,
} from "./format";

// Max categorical series a monochrome palette can carry legibly.
export const MAX_SERIES = 4;
// Category caps before the tail folds into "Other".
const MAX_BAR_CATEGORIES = 12;
const MAX_PIE_SLICES = 6;
const OTHER_LABEL = "Other";

// Static fallbacks — used before the stylesheet resolves, and whenever the
// caller's theme disagrees with the DOM (see getChartTheme).
export const RAMP = ["#56B4E9", "#009E73", "#E69F00", "#B79BFF", "#F0E442", "#CC79A7"];
const SERIES_DARK = ["#56B4E9", "#009E73", "#E69F00", "#CC79A7"];
const SERIES_LIGHT = ["#0072B2", "#009E73", "#D55E00", "#CC79A7"];

// Dash patterns are the non-colour identity channel for multi-series lines.
const LINE_DASH: (number[] | undefined)[] = [undefined, [6, 4], [2, 3], [9, 3, 2, 3]];

export interface ChartTheme {
  /** 4-slot categorical palette for multi-series marks. */
  series: string[];
  /** 6-step ramp for direct-labelled segment fills (pie slices). */
  ramp: string[];
  axis: string;
  grid: string;
  axisLine: string;
  text: string;
  textMuted: string;
  tooltipBg: string;
  /** Card surface — the colour of the 2px gaps and marker rings. */
  surface: string;
  accentFade: (o: number) => string;
}

/** `#56B4E9` + 0.12 → `rgba(86,180,233,0.12)`. Area washes wear the series
 *  hue, not a neutral tint, so the fill reads as belonging to its line. */
export function withAlpha(color: string, alpha: number): string {
  const hex = color.trim();
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return hex;
  const int = parseInt(m[1], 16);
  return `rgba(${(int >> 16) & 255}, ${(int >> 8) & 255}, ${int & 255}, ${alpha})`;
}

function cssVar(name: string, fallback: string): string {
  if (typeof window === "undefined" || typeof document === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

const RAMP_LIGHT = ["#0072B2", "#009E73", "#D55E00", "#CC79A7", "#7B5AB6", "#8A6D00"];

/**
 * Read the chart palette + chrome from the active theme's CSS tokens. Call at
 * option-build time inside a render that depends on the theme so colours track
 * the light/dark toggle.
 *
 * `expected` is the theme the caller believes it is rendering for. When it
 * disagrees with the DOM (the toggle's attribute hasn't been applied yet) we
 * use the static palette instead of reading the outgoing theme's variables —
 * otherwise a chart can be built entirely in the wrong colours and, since
 * nothing re-renders afterwards, stay that way.
 */
export function getChartTheme(expected?: "light" | "dark"): ChartTheme {
  const domTheme =
    typeof document !== "undefined" &&
    document.documentElement.getAttribute("data-theme") === "light"
      ? "light"
      : "dark";
  const active = expected ?? domTheme;
  const isLight = active === "light";
  const stale = expected !== undefined && expected !== domTheme;
  const read = stale ? (_name: string, fallback: string) => fallback : cssVar;

  const seriesFallback = isLight ? SERIES_LIGHT : SERIES_DARK;
  const rampFallback = isLight ? RAMP_LIGHT : RAMP;
  return {
    series: [1, 2, 3, 4].map((i) => read(`--series-${i}`, seriesFallback[i - 1])),
    ramp: [1, 2, 3, 4, 5, 6].map((i) => read(`--ramp-${i}`, rampFallback[i - 1])),
    axis: read("--text-muted", isLight ? "rgba(0,0,0,0.45)" : "rgba(255,255,255,0.42)"),
    grid: read("--border-subtle", isLight ? "rgba(0,0,0,0.08)" : "rgba(255,255,255,0.07)"),
    axisLine: read("--border-default", isLight ? "rgba(0,0,0,0.14)" : "rgba(255,255,255,0.14)"),
    text: read("--text-primary", isLight ? "#0a0a0a" : "#ffffff"),
    textMuted: read("--text-secondary", isLight ? "rgba(0,0,0,0.62)" : "rgba(255,255,255,0.66)"),
    tooltipBg: read("--bg-card", isLight ? "#ffffff" : "#101010"),
    surface: read("--bg-card", isLight ? "#ffffff" : "#101010"),
    accentFade: (o: number) => (isLight ? `rgba(0,0,0,${o})` : `rgba(255,255,255,${o})`),
  };
}

// ── Value coercion ────────────────────────────────────────────

/**
 * Coerce a value to a number, handling Trino's stringified NUMERIC/DECIMAL.
 *
 * Dates are explicitly rejected. `parseFloat("2023-05-01")` returns 2023, which
 * used to make every date column look numeric — a monthly hiring trend plotted
 * its own month column as a flat series at y=2023.
 */
export const toNumber = (v: unknown): number | null => {
  if (v === null || v === undefined || v === "") return null;
  if (typeof v === "number") return isNaN(v) ? null : v;
  if (typeof v === "boolean") return v ? 1 : 0;
  if (v instanceof Date) return null;
  if (typeof v === "string") {
    if (looksTemporal(v)) return null;
    const cleaned = v.replace(/,/g, "").replace(/[$€£%]/g, "").trim();
    if (cleaned === "" || !/^[-+]?[\d.eE+-]+$/.test(cleaned)) return null;
    const n = parseFloat(cleaned);
    return isNaN(n) ? null : n;
  }
  return null;
};

// ── Column classification ─────────────────────────────────────

export interface ColumnClassification {
  numericCols: number[];
  categoricalCols: number[];
  temporalCols: number[];
  labelColIdx: number | null;
  /** True when the label column is a date/timestamp. */
  hasTimeAxis: boolean;
  firstNumIdx: number;
  hasData: boolean;
}

const TIME_NAME = /(^|_)(date|year|month|week|day|quarter|period|time|ts|timestamp|created|updated)($|_)/;
const LABEL_PREFERENCE = [
  "name", "department", "category", "region", "title", "label",
  "type", "group", "status", "project", "period", "quarter",
];

/**
 * Classify every column as metric / category / time, and pick the best label
 * column. A time column is never a metric, and is preferred as the label so a
 * trend plots against its own axis.
 */
export function classifyColumns(
  columns: string[],
  rows: unknown[][]
): ColumnClassification {
  const empty: ColumnClassification = {
    numericCols: [], categoricalCols: [], temporalCols: [],
    labelColIdx: null, hasTimeAxis: false, firstNumIdx: 0, hasData: false,
  };
  if (rows.length === 0 || columns.length === 0) return empty;

  const colValues = (i: number) =>
    rows.map((r) => r[i]).filter((v) => v !== null && v !== undefined && v !== "");

  const isTemporal = (i: number): boolean => {
    const vals = colValues(i);
    if (vals.length === 0) return false;
    // Value shape decides; the column name only breaks ties for all-numeric
    // columns that are really year buckets (2021, 2022, …).
    const temporalCount = vals.filter(looksTemporal).length;
    if (temporalCount / vals.length >= 0.8) return true;
    if (!TIME_NAME.test(columns[i]?.toLowerCase() ?? "")) return false;
    const asYears = vals.every((v) => {
      const n = toNumber(v);
      return n !== null && Number.isInteger(n) && n >= 1900 && n <= 2200;
    });
    return asYears;
  };

  const isNumeric = (i: number): boolean => {
    const vals = colValues(i);
    if (vals.length === 0) return false;
    return vals.filter((v) => toNumber(v) !== null).length / vals.length >= 0.8;
  };

  const isBoolean = (i: number): boolean => {
    const vals = colValues(i);
    if (vals.length === 0) return false;
    return vals.every((v) => typeof v === "boolean" || v === "true" || v === "false");
  };

  // An identifier column is numeric but meaningless as a metric — plotting
  // employee_id as a bar height is noise.
  const isIdentifier = (i: number): boolean => {
    const name = columns[i]?.toLowerCase() ?? "";
    if (!/(^|_)(id|ids|uuid|guid|key|code)($|_)/.test(name)) return false;
    const vals = colValues(i);
    return new Set(vals.map(String)).size === vals.length;
  };

  const numericCols: number[] = [];
  const categoricalCols: number[] = [];
  const temporalCols: number[] = [];

  for (let i = 0; i < columns.length; i++) {
    if (isTemporal(i)) { temporalCols.push(i); categoricalCols.push(i); continue; }
    if (isBoolean(i)) { categoricalCols.push(i); continue; }
    if (isNumeric(i)) {
      if (isIdentifier(i)) categoricalCols.push(i);
      else numericCols.push(i);
      continue;
    }
    categoricalCols.push(i);
  }

  if (numericCols.length === 0) {
    return { ...empty, numericCols, categoricalCols, temporalCols };
  }

  // Time wins the label slot; otherwise prefer a meaningfully-named category,
  // then the first category, then the lowest-cardinality one.
  let labelColIdx: number | null = null;
  if (temporalCols.length > 0) {
    labelColIdx = temporalCols[0];
  } else {
    const plainCategories = categoricalCols.filter((i) => !temporalCols.includes(i));
    labelColIdx = plainCategories[0] ?? null;
    for (const keyword of LABEL_PREFERENCE) {
      const found = plainCategories.find((i) => columns[i]?.toLowerCase().includes(keyword));
      if (found !== undefined) { labelColIdx = found; break; }
    }
  }

  return {
    numericCols,
    categoricalCols,
    temporalCols,
    labelColIdx,
    hasTimeAxis: labelColIdx !== null && temporalCols.includes(labelColIdx),
    firstNumIdx: numericCols[0],
    hasData: true,
  };
}

// ── Choosing the form ─────────────────────────────────────────
// Sometimes the honest answer is "this isn't a chart". A single aggregate row
// drawn as a one-bar bar chart, or eight identical bars, is chart-shaped
// furniture that hides the actual finding. The recommender can therefore
// return a non-chart verdict, and the caller renders the right thing.

export type ChartForm = "bar" | "line" | "area" | "pie";

export type Recommendation =
  | { form: ChartForm; reason?: string }
  | { form: "stat"; reason: string }
  | { form: "table"; reason: string };

/**
 * Pick the form that fits the data's job — magnitude, change over time,
 * part-to-whole, or a single headline number.
 */
export function recommendChart(
  columns: string[],
  rows: unknown[][],
  cls: ColumnClassification
): Recommendation {
  if (!cls.hasData) {
    return { form: "table", reason: "No numeric column to plot — the table below has the result." };
  }

  const metric = humanizeColumn(columns[cls.firstNumIdx]);
  const values = rows
    .map((r) => toNumber(r[cls.firstNumIdx]))
    .filter((v): v is number => v !== null);

  if (values.length === 0) {
    return { form: "table", reason: `Every ${metric.toLowerCase()} value is empty.` };
  }

  // One row, one number: the number IS the chart.
  if (rows.length === 1) {
    return { form: "stat", reason: "A single aggregate row — the number is the whole result." };
  }

  // Two rows is a two-slice pie or a two-bar chart; a bar is still honest,
  // but zero variance is not worth a plot at any row count.
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (max === min && rows.length >= 2) {
    return {
      form: "stat",
      reason: `${metric} is identical across all ${rows.length} — there is no distribution to plot.`,
    };
  }

  // Time on the label axis → change over time.
  if (cls.hasTimeAxis && rows.length > 1) {
    return { form: cls.numericCols.length > 1 ? "line" : "area" };
  }

  // A label column that is unique per row, over many rows, is an identifier
  // list rather than a set of categories — the "chart" would be a barcode.
  if (cls.labelColIdx !== null && rows.length > 25) {
    const distinct = new Set(rows.map((r) => String(r[cls.labelColIdx!] ?? ""))).size;
    if (distinct === rows.length) {
      return {
        form: "bar",
        reason: `${rows.length} distinct rows — showing the top ${MAX_BAR_CATEGORIES} by ${metric.toLowerCase()}.`,
      };
    }
  }

  // Part-to-whole, at a glance only: few slices, all positive, one metric,
  // and no slice so dominant that the rest are unreadable slivers.
  const total = values.reduce((s, v) => s + v, 0);
  const topShare = total > 0 ? max / total : 1;
  const isPieable =
    rows.length >= 3 &&
    rows.length <= MAX_PIE_SLICES &&
    total > 0 &&
    values.every((v) => v >= 0) &&
    cls.numericCols.length === 1 &&
    cls.labelColIdx !== null &&
    topShare <= 0.9;
  if (isPieable) return { form: "pie" };

  return { form: "bar" };
}

/**
 * Back-compat shim for callers that only understand the three plot types.
 * New code should use recommendChart, which can also say "don't plot this".
 */
export function autoDetectChartType(
  rows: unknown[][],
  cls: ColumnClassification,
  columns: string[] = []
): "bar" | "line" | "pie" {
  const rec = recommendChart(columns, rows, cls);
  if (rec.form === "line" || rec.form === "area") return "line";
  if (rec.form === "pie") return "pie";
  return "bar";
}

// ── Units ─────────────────────────────────────────────────────
// A y-axis reading "0 · 500K · 1M" says nothing about what is being counted.
// Detecting the unit from the metric's name lets the axis title and every
// tick, label and tooltip agree on how the number should read.

export type MetricUnit = "count" | "percent" | "currency" | "duration";

const CURRENCY_NAME = /(salary|revenue|cost|price|amount|spend|pay|budget|income|sales|value|fee|charge)/;
const PERCENT_NAME = /(pct|percent|rate|ratio|share|proportion)/;
const DURATION_NAME = /(hours|hrs|minutes|mins|seconds|secs|duration|elapsed|_ms$|latency)/;

function detectUnit(columnName: string, values: number[]): MetricUnit {
  const n = (columnName || "").toLowerCase();
  if (PERCENT_NAME.test(n)) return "percent";
  if (CURRENCY_NAME.test(n)) return "currency";
  if (DURATION_NAME.test(n)) return "duration";
  // An all-integer column is a count; anything else stays generic.
  return values.every(Number.isInteger) ? "count" : "count";
}

/** Axis tick text for a value, in the metric's unit. */
export function formatAxisValue(v: number, unit?: MetricUnit): string {
  if (unit === "percent") return `${formatCompact(v)}`;
  return formatCompact(v);
}

/** Full-precision value text for tooltips and direct labels. */
export function formatMetricValue(v: number, unit?: MetricUnit, compact = false): string {
  const body = compact ? formatCompact(v) : formatFull(v);
  if (unit === "percent") return `${body}%`;
  if (unit === "duration") return `${body}h`;
  return body;
}

// ── Data preparation ──────────────────────────────────────────

export interface PreparedSeries {
  name: string;
  values: (number | null)[];
  colIdx: number;
}

export interface PreparedChart {
  /** Axis labels, already formatted (dates humanised, long text truncated). */
  labels: string[];
  /** Full labels for tooltips, never truncated. */
  fullLabels: string[];
  series: PreparedSeries[];
  labelName: string;
  isTemporal: boolean;
  /** Metric columns beyond the 4-slot palette cap. */
  droppedSeries: string[];
  /** Metric columns left out because their scale is incommensurable. */
  scaleDropped: string[];
  /** Unit of the primary metric — drives axis title and value formatting. */
  unit: MetricUnit;
  /** Currency label when known; the axis title falls back to "amount". */
  currencyHint?: string;
  /** Categories folded into the "Other" bucket, 0 when nothing was folded. */
  foldedCount: number;
  totalCategories: number;
  sorted: boolean;
  hasData: boolean;
}

const EMPTY_PREPARED: PreparedChart = {
  labels: [], fullLabels: [], series: [], labelName: "",
  isTemporal: false, droppedSeries: [], scaleDropped: [], unit: "count",
  foldedCount: 0, totalCategories: 0, sorted: false, hasData: false,
};

/** Typical magnitude of a column, robust to a single huge outlier. */
function magnitudeOf(rows: unknown[][], colIdx: number): number {
  const vals = rows
    .map((r) => toNumber(r[colIdx]))
    .filter((v): v is number => v !== null && v !== 0)
    .map(Math.abs)
    .sort((a, b) => a - b);
  if (vals.length === 0) return 0;
  return vals[Math.floor(vals.length / 2)];
}

// Beyond this ratio, plotting two metrics against one axis flattens the
// smaller one to an invisible sliver (headcount 10 next to salary 1.5M).
const MAX_SCALE_RATIO = 20;

/**
 * Turn raw rows into exactly what the chart draws.
 *
 * Rules, in order:
 *  1. Cap metric columns at the palette size; report the rest.
 *  2. Time series keep chronological order. Everything else sorts by value
 *     descending — an unsorted category bar chart makes the reader do the
 *     ranking by eye.
 *  3. Fold the long tail into a labelled "Other" bucket so the chart never
 *     grows an unreadable 40-category axis.
 */
export function prepareChart(
  columns: string[],
  rows: unknown[][],
  cls: ColumnClassification,
  chartType: "bar" | "line" | "area" | "pie",
  opts?: { maxCategories?: number; labelChars?: number }
): PreparedChart {
  if (!cls.hasData || rows.length === 0) return EMPTY_PREPARED;

  const { numericCols, labelColIdx } = cls;
  const usableCols = numericCols.slice(0, MAX_SERIES);
  const droppedSeries = numericCols.slice(MAX_SERIES).map((i) => columns[i]);
  // Pie renders one metric only — a second series has nowhere to go.
  const capped = chartType === "pie" ? usableCols.slice(0, 1) : usableCols;

  // One axis, always. Two y-scales on one plot invent a relationship that
  // isn't in the data, and sharing a single scale between metrics orders of
  // magnitude apart hides the smaller one entirely. So we plot the metrics
  // that share a scale and name the ones we left out — the caller surfaces
  // that rather than silently dropping them.
  const scaleDropped: string[] = [];
  const activeCols: number[] = [];
  if (capped.length > 0) {
    const primaryMagnitude = magnitudeOf(rows, capped[0]);
    for (const c of capped) {
      const m = magnitudeOf(rows, c);
      const comparable =
        c === capped[0] ||
        primaryMagnitude === 0 ||
        m === 0 ||
        (m / primaryMagnitude <= MAX_SCALE_RATIO && primaryMagnitude / m <= MAX_SCALE_RATIO);
      if (comparable) activeCols.push(c);
      else scaleDropped.push(columns[c]);
    }
  }

  const granularity = cls.hasTimeAxis && labelColIdx !== null
    ? detectGranularity(rows.map((r) => r[labelColIdx]))
    : "day";

  // Row index → {label, values}
  let items = rows.map((r, i) => {
    const raw = labelColIdx !== null ? r[labelColIdx] : null;
    const full = labelColIdx === null
      ? `Row ${i + 1}`
      : cls.hasTimeAxis
        ? formatTemporal(raw, granularity)
        : String(raw ?? "—");
    return {
      full,
      sortKey: cls.hasTimeAxis ? (parseTemporal(raw)?.getTime() ?? i) : i,
      values: activeCols.map((c) => toNumber(r[c])),
    };
  });

  let sorted = false;
  if (cls.hasTimeAxis) {
    items = items.slice().sort((a, b) => a.sortKey - b.sortKey);
  } else if (chartType !== "line" && chartType !== "area") {
    // Rank by the primary metric; nulls sink to the bottom.
    items = items.slice().sort((a, b) => (b.values[0] ?? -Infinity) - (a.values[0] ?? -Infinity));
    sorted = true;
  }

  const totalCategories = items.length;
  const cap =
    opts?.maxCategories ??
    (chartType === "pie" ? MAX_PIE_SLICES : chartType === "bar" ? MAX_BAR_CATEGORIES : Infinity);

  let foldedCount = 0;
  // Only fold when the tail is worth folding, and never on a time axis
  // (dropping the middle of a trend would misstate it).
  if (!cls.hasTimeAxis && items.length > cap + 1) {
    const head = items.slice(0, cap);
    const tail = items.slice(cap);
    const allNonNegative = tail.every((it) => it.values.every((v) => v === null || v >= 0));
    if (allNonNegative) {
      foldedCount = tail.length;
      head.push({
        full: `${OTHER_LABEL} (${tail.length})`,
        sortKey: cap,
        values: activeCols.map((_, si) =>
          tail.reduce<number | null>((sum, it) => {
            const v = it.values[si];
            return v === null ? sum : (sum ?? 0) + v;
          }, null)
        ),
      });
      items = head;
    } else {
      // Mixed signs can't be summed into a meaningful bucket — just truncate.
      foldedCount = items.length - cap;
      items = items.slice(0, cap);
    }
  }

  const labelChars = opts?.labelChars ?? 20;
  return {
    labels: items.map((it) => truncateLabel(it.full, labelChars)),
    fullLabels: items.map((it) => it.full),
    series: activeCols.map((colIdx, si) => ({
      name: humanizeColumn(columns[colIdx]),
      colIdx,
      values: items.map((it) => it.values[si]),
    })),
    labelName: labelColIdx !== null ? humanizeColumn(columns[labelColIdx]) : "",
    isTemporal: cls.hasTimeAxis,
    droppedSeries,
    scaleDropped,
    unit:
      activeCols.length > 0
        ? detectUnit(
            columns[activeCols[0]],
            rows.map((r) => toNumber(r[activeCols[0]])).filter((v): v is number => v !== null)
          )
        : "count",
    foldedCount,
    totalCategories,
    sorted,
    hasData: activeCols.length > 0 && items.length > 0,
  };
}

// ── Option building ───────────────────────────────────────────

export interface BuildOptions {
  /** Compact tiles get smaller type and tighter padding. */
  density?: "comfortable" | "compact";
  /** Explicit palette from a widget's chart_config. */
  colorsOverride?: string[];
  /** Force bar orientation; defaults to auto (horizontal for long labels). */
  horizontal?: boolean;
  /** Suppress value labels on marks. */
  hideValueLabels?: boolean;
  /** Theme the caller is rendering for — see getChartTheme(). */
  themeHint?: "light" | "dark";
}

interface TooltipParam {
  axisValueLabel?: string;
  name?: string;
  seriesName?: string;
  dataIndex?: number;
  value?: unknown;
  marker?: string;
  percent?: number;
}

function tooltipShell(t: ChartTheme) {
  return {
    backgroundColor: t.tooltipBg,
    borderColor: t.axisLine,
    borderWidth: 1,
    padding: [8, 10] as [number, number],
    extraCssText: "box-shadow: 0 4px 20px rgba(0,0,0,0.25); border-radius: 6px;",
    textStyle: { color: t.text, fontSize: 12 },
    // The hit target must be forgiving — a 2px line is not a mouse target.
    confine: true,
  };
}

// Tooltip rows: a colour swatch carries identity, the text stays in ink.
function axisTooltipFormatter(prep: PreparedChart, t: ChartTheme) {
  const unit = prep.unit;
  return (params: TooltipParam[] | TooltipParam) => {
    const list = Array.isArray(params) ? params : [params];
    if (list.length === 0) return "";
    const idx = list[0].dataIndex ?? 0;
    const head = prep.fullLabels[idx] ?? list[0].axisValueLabel ?? "";
    const rows = list
      .map((p) => {
        const n = toNumber(p.value);
        return `<div style="display:flex;align-items:center;gap:8px;margin-top:3px">
          ${p.marker ?? ""}
          <span style="color:${t.textMuted}">${escapeHtml(p.seriesName ?? "")}</span>
          <span style="margin-left:auto;font-variant-numeric:tabular-nums;font-weight:600">${
            n === null ? "—" : formatMetricValue(n, unit)
          }</span>
        </div>`;
      })
      .join("");
    return `<div style="font-weight:600;max-width:260px;white-space:normal">${escapeHtml(head)}</div>${rows}`;
  };
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c] ?? c)
  );
}

/**
 * Build the echarts option for a prepared dataset. Returns null when there is
 * nothing plottable.
 */
export function buildChartOption(
  type: "bar" | "line" | "area" | "pie",
  prep: PreparedChart,
  opts: BuildOptions = {}
): Record<string, unknown> | null {
  if (!prep.hasData) return null;
  const t = getChartTheme(opts.themeHint);
  const compact = opts.density === "compact";
  const fs = compact ? 10 : 11;
  const palette =
    opts.colorsOverride && opts.colorsOverride.length > 0 ? opts.colorsOverride : t.series;

  const base = {
    backgroundColor: "transparent",
    animation: true,
    animationDuration: 700,
    animationEasing: "cubicOut",
    textStyle: { fontFamily: "Inter, system-ui, sans-serif" },
  };

  // ── Pie / donut ─────────────────────────────────────────────
  if (type === "pie") {
    const s = prep.series[0];
    const values = s.values.map((v) => v ?? 0);
    const total = values.reduce((a, b) => a + b, 0);
    const ramp = opts.colorsOverride?.length ? opts.colorsOverride : t.ramp;
    return {
      ...base,
      tooltip: {
        trigger: "item",
        ...tooltipShell(t),
        formatter: (p: TooltipParam) =>
          `<div style="font-weight:600">${escapeHtml(String(p.name ?? ""))}</div>
           <div style="margin-top:3px;color:${t.textMuted}">${escapeHtml(s.name)}
             <span style="color:${t.text};font-weight:600;margin-left:8px;font-variant-numeric:tabular-nums">${formatFull(
               toNumber(p.value) ?? 0
             )}</span>
             <span style="margin-left:6px">(${(p.percent ?? 0).toFixed(1)}%)</span>
           </div>`,
      },
      legend: {
        type: "scroll",
        orient: "vertical",
        right: 8,
        top: "middle",
        itemWidth: 8,
        itemHeight: 8,
        icon: "roundRect",
        textStyle: { color: t.textMuted, fontSize: fs },
        formatter: (name: string) => truncateLabel(name, 18),
      },
      series: [
        {
          type: "pie",
          radius: ["46%", "72%"],
          center: ["36%", "50%"],
          minAngle: 2,
          avoidLabelOverlap: true,
          data: prep.fullLabels.map((label, i) => ({ name: label, value: values[i] })),
          // The 2px surface ring IS the gap between slices — not a decorative
          // stroke. Slices never touch, so neighbouring greys stay separable.
          itemStyle: { borderRadius: 2, borderColor: t.surface, borderWidth: 2 },
          label: {
            show: !compact,
            color: t.textMuted,
            fontSize: fs,
            // Label only slices big enough to be worth naming; the rest are in
            // the legend and the tooltip.
            formatter: (p: TooltipParam) =>
              (p.percent ?? 0) >= 5 ? truncateLabel(String(p.name ?? ""), 16) : "",
          },
          labelLine: { show: !compact, length: 8, length2: 8, lineStyle: { color: t.axisLine } },
          emphasis: {
            scaleSize: 4,
            itemStyle: { shadowBlur: 10, shadowColor: t.accentFade(0.18) },
          },
          color: ramp,
        },
      ],
      // Centre readout: the total the slices add up to.
      graphic: compact
        ? undefined
        : {
            type: "group",
            left: "36%",
            top: "middle",
            children: [
              {
                type: "text",
                style: {
                  text: formatCompact(total),
                  fill: t.text,
                  font: "600 18px Inter, system-ui, sans-serif",
                  textAlign: "center",
                  textVerticalAlign: "bottom",
                },
              },
              {
                type: "text",
                y: 4,
                style: {
                  text: "total",
                  fill: t.axis,
                  font: `400 ${fs}px Inter, system-ui, sans-serif`,
                  textAlign: "center",
                  textVerticalAlign: "top",
                },
              },
            ],
          },
    };
  }

  const multi = prep.series.length > 1;
  // A legend is always present for two or more series — identity must never
  // rest on the grey alone. Bars get a block swatch; lines keep the default
  // icon so the dash pattern (the second identity channel) shows in the key.
  const legend = multi
    ? {
        type: "scroll" as const,
        top: 0,
        left: "center",
        itemWidth: type === "bar" ? 10 : 18,
        itemHeight: type === "bar" ? 10 : 2,
        itemGap: 14,
        ...(type === "bar" ? { icon: "roundRect" } : {}),
        textStyle: { color: t.textMuted, fontSize: fs },
      }
    : undefined;

  // Horizontal when labels are long or numerous — a rotated 45° axis of
  // 12 department names is unreadable in a tile. Computed here because the
  // value-axis title placement depends on it.
  const longestLabel = prep.labels.reduce((m, l) => Math.max(m, l.length), 0);
  const barIsHorizontal =
    opts.horizontal ?? (!prep.isTemporal && (prep.labels.length > 6 || longestLabel > 10));

  // ── Axis titles ─────────────────────────────────────────────
  // An axis of bare numbers makes the reader guess what is being measured.
  // The value axis is titled with the metric (and its unit); the category
  // axis with the dimension — but only when that adds something the title
  // doesn't already say.
  const valueAxisName = multi ? "" : prep.series[0]?.name ?? "";
  const unit = prep.unit;
  // Only annotate the unit when we actually know it. Guessing "(amount)" for
  // an unknown currency adds a word and no information.
  const valueAxisTitle = valueAxisName
    ? unit === "percent"
      ? `${valueAxisName} (%)`
      : unit === "currency" && prep.currencyHint
        ? `${valueAxisName} (${prep.currencyHint})`
        : valueAxisName
    : "";

  const nameTextStyle = {
    color: t.axis,
    fontSize: fs,
    fontWeight: 500 as const,
  };

  // Where the value-axis title can sit without being clipped depends on which
  // axis carries it. On a vertical axis "end" puts it above the plot, which is
  // conventional and always fits. On a horizontal axis "end" pushes it past
  // the right edge of the card ("Headcou…"), so it goes centred underneath.
  const valueAxisIsHorizontal = type === "bar" && barIsHorizontal;
  const valueAxis = {
    type: "value" as const,
    name: valueAxisTitle,
    nameLocation: valueAxisIsHorizontal ? ("middle" as const) : ("end" as const),
    nameGap: valueAxisIsHorizontal ? 26 : 12,
    nameTextStyle: {
      ...nameTextStyle,
      align: valueAxisIsHorizontal ? ("center" as const) : ("left" as const),
    },
    axisLabel: {
      color: t.axis,
      fontSize: fs,
      hideOverlap: true,
      formatter: (v: number) => formatAxisValue(v, unit),
    },
    axisLine: { show: false },
    axisTick: { show: false },
    // Round ticks to clean numbers — they carry the values that aren't
    // directly labelled.
    splitNumber: compact ? 4 : 5,
    // Bar length encodes magnitude, so its scale must start at zero.
    ...(type === "bar" ? { min: 0 } : {}),
    // Solid hairline one step off the surface — never dashed.
    splitLine: { lineStyle: { color: t.grid, width: 1, type: "solid" as const } },
  };

  // ── Line / area ─────────────────────────────────────────────
  if (type === "line" || type === "area") {
    const isArea = type === "area" || !multi;
    const dense = prep.labels.length > 40;
    const series = prep.series.map((s, si) => ({
      name: s.name,
      type: "line",
      data: s.values,
      smooth: 0.18,
      showSymbol: !dense && prep.labels.length <= 24,
      symbol: "circle",
      symbolSize: 8,
      connectNulls: false,
      lineStyle: {
        color: palette[si % palette.length],
        width: 2,
        type: (LINE_DASH[si] ?? undefined) as number[] | undefined,
        cap: "round" as const,
      },
      // 2px surface ring keeps markers legible where lines cross.
      itemStyle: {
        color: palette[si % palette.length],
        borderColor: t.surface,
        borderWidth: 2,
      },
      emphasis: { focus: "series" as const, scale: 1.4 },
      areaStyle: isArea
        ? {
            // A ~10% wash of the series' own hue, never a saturated block.
            color: {
              type: "linear", x: 0, y: 0, x2: 0, y2: 1,
              colorStops: [
                { offset: 0, color: withAlpha(palette[si % palette.length], 0.16) },
                { offset: 1, color: withAlpha(palette[si % palette.length], 0.01) },
              ],
            },
          }
        : undefined,
      // Direct-label the endpoint only — the one label that reads.
      endLabel: !multi && !dense
        ? {
            show: true,
            color: t.textMuted,
            fontSize: fs,
            distance: 6,
            formatter: (p: TooltipParam) => {
              const n = toNumber(p.value);
              return n === null ? "" : formatMetricValue(n, prep.unit, true);
            },
          }
        : undefined,
    }));

    const rotate = prep.labels.length > 12 ? 40 : 0;
    return {
      ...base,
      tooltip: {
        trigger: "axis",
        ...tooltipShell(t),
        axisPointer: {
          type: "line",
          lineStyle: { color: t.axisLine, width: 1 },
          snap: true,
        },
        formatter: axisTooltipFormatter(prep, t),
      },
      legend,
      grid: {
        left: rotate ? 20 : 8,
        right: !multi && !dense ? 52 : 16,
        bottom: rotate ? 4 : 2,
        // Headroom for the legend and the value-axis title above the plot.
        top: multi ? 40 : 26,
        containLabel: true,
      },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: prep.labels,
        // A date axis names itself ("May 2019"); anything else gets a title.
        name: !prep.isTemporal && prep.labelName ? prep.labelName : "",
        nameLocation: "middle" as const,
        nameGap: rotate ? 46 : 26,
        nameTextStyle: { ...nameTextStyle, align: "center" as const },
        axisLabel: {
          color: t.axis,
          fontSize: fs,
          rotate,
          hideOverlap: true,
          margin: 10,
        },
        axisLine: { lineStyle: { color: t.axisLine } },
        axisTick: { show: false },
      },
      yAxis: valueAxis,
      series,
    };
  }

  // ── Bar ─────────────────────────────────────────────────────
  const horizontal = barIsHorizontal;
  const showValueLabels =
    !opts.hideValueLabels && !multi && prep.labels.length <= (compact ? 10 : 14);

  const series = prep.series.map((s, si) => ({
    name: s.name,
    type: "bar",
    data: s.values,
    // Cap the mark: a bar that fills its whole band reads as a block.
    barMaxWidth: compact ? 18 : 24,
    barCategoryGap: "38%",
    barGap: multi ? "12%" : undefined,
    itemStyle: {
      // One series → one colour. Colouring each bar by its own value would
      // double-encode length as lightness and burn the identity channel.
      color: palette[si % palette.length],
      borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
    },
    emphasis: { focus: multi ? ("series" as const) : ("none" as const) },
    label: showValueLabels
      ? {
          show: true,
          // Outside the bar end — an inside label clips on short bars.
          position: horizontal ? ("right" as const) : ("top" as const),
          color: t.textMuted,
          fontSize: fs,
          distance: 6,
          formatter: (p: TooltipParam) => {
            const n = toNumber(p.value);
            return n === null ? "" : formatMetricValue(n, prep.unit, true);
          },
        }
      : undefined,
    labelLayout: { hideOverlap: true },
  }));

  const categoryAxis = {
    type: "category" as const,
    data: prep.labels,
    inverse: horizontal, // highest bar at the top for a ranked list
    axisLabel: {
      color: t.axis,
      fontSize: fs,
      hideOverlap: true,
      rotate: horizontal ? 0 : prep.labels.length > 6 ? 35 : 0,
      margin: 10,
      // Bound the label band so containLabel can reserve room for it —
      // without this the longest name gets shaved off at the left edge.
      ...(horizontal
        ? { width: compact ? 96 : 132, overflow: "truncate" as const }
        : {}),
    },
    axisLine: { lineStyle: { color: t.axisLine } },
    axisTick: { show: false },
  };

  return {
    ...base,
    tooltip: {
      trigger: "axis",
      ...tooltipShell(t),
      axisPointer: { type: "shadow", shadowStyle: { color: t.accentFade(0.05) } },
      formatter: axisTooltipFormatter(prep, t),
    },
    legend,
    grid: {
      left: 8,
      // Room for the value labels sitting past the bar end.
      right: horizontal ? (showValueLabels ? 48 : 20) : 16,
      // containLabel reserves space for axis LABELS but not for axis NAMES,
      // so a title under a horizontal value axis needs its own allowance or
      // it renders outside the card and is clipped away.
      bottom: horizontal && valueAxisTitle ? 22 : 2,
      top: multi ? 40 : 26,
      containLabel: true,
    },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? categoryAxis : valueAxis,
    series,
  };
}

/**
 * Convenience wrapper: classify → prepare → build, for callers that just have
 * columns and rows.
 */
export function buildWidgetChartOption(
  type: "bar" | "line" | "area" | "pie",
  columns: string[],
  rows: unknown[][],
  cls: ColumnClassification,
  colorsOverride?: string[],
  opts?: BuildOptions
): Record<string, unknown> | null {
  const prep = prepareChart(columns, rows, cls, type, { labelChars: 18 });
  return buildChartOption(type, prep, { density: "compact", colorsOverride, ...opts });
}

/** Single-value gauge (monochrome progress arc, theme-aware). */
export function buildGaugeOption(
  value: number,
  max: number,
  themeHint?: "light" | "dark"
): Record<string, unknown> {
  const t = getChartTheme(themeHint);
  const safeMax = max > 0 ? max : Math.max(100, value);
  return {
    backgroundColor: "transparent",
    series: [
      {
        type: "gauge",
        min: 0,
        max: safeMax,
        startAngle: 210,
        endAngle: -30,
        progress: { show: true, width: 10, roundCap: true, itemStyle: { color: t.series[0] } },
        axisLine: { roundCap: true, lineStyle: { width: 10, color: [[1, t.grid]] } },
        axisTick: { show: false },
        splitLine: { length: 6, lineStyle: { color: t.axisLine, width: 1 } },
        axisLabel: {
          color: t.axis,
          fontSize: 9,
          distance: 12,
          formatter: (v: number) => formatCompact(v),
        },
        pointer: { show: false },
        anchor: { show: false },
        detail: {
          valueAnimation: true,
          color: t.text,
          fontSize: 24,
          fontWeight: 600,
          offsetCenter: [0, "10%"],
          formatter: (v: number) => formatCompact(v),
        },
        data: [{ value }],
      },
    ],
  };
}
