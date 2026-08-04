// Deterministic result analysis — the "what does this actually say?" layer.
//
// Everything here is computed from the rows already on screen: no LLM call, no
// extra round-trip, no latency, and nothing that can hallucinate a number the
// data doesn't contain. Each insight is a plain sentence plus the figures that
// back it, so the UI can render it as text or as a stat line.
//
// Used in two places:
//   · Query Studio — a reading of the result plus suggested follow-ups.
//   · Dashboards   — a few headline findings across all the tiles.

import {
  classifyColumns,
  toNumber,
  type ColumnClassification,
} from "./chartTheme";
import {
  formatFull,
  formatCompact,
  formatPercent,
  formatSignedPercent,
  humanizeColumn,
  parseTemporal,
} from "./format";
import type { DatasetMeta, QueryPlan } from "../types";

export type InsightKind =
  | "leader"
  | "concentration"
  | "trend"
  | "spread"
  | "outlier"
  | "gaps"
  | "single"
  | "scale";

export interface Insight {
  kind: InsightKind;
  /** One sentence, already formatted for display. */
  text: string;
  /** Headline figure, when the insight has one worth showing large. */
  figure?: string;
  /** Direction for trend-style insights, drives the icon. */
  direction?: "up" | "down" | "flat";
  /** Where it came from — set when the insight spans dashboard widgets. */
  source?: string;
}

export interface ResultReading {
  /** A sentence describing what the result set contains. */
  summary: string;
  insights: Insight[];
}

// ── Helpers ───────────────────────────────────────────────────

function numericValues(rows: unknown[][], colIdx: number): number[] {
  return rows
    .map((r) => toNumber(r[colIdx]))
    .filter((v): v is number => v !== null);
}

function mean(xs: number[]): number {
  return xs.length === 0 ? 0 : xs.reduce((a, b) => a + b, 0) / xs.length;
}

function stdev(xs: number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  return Math.sqrt(xs.reduce((s, x) => s + (x - m) ** 2, 0) / (xs.length - 1));
}

function labelAt(rows: unknown[][], cls: ColumnClassification, i: number): string {
  if (cls.labelColIdx === null) return `row ${i + 1}`;
  return String(rows[i][cls.labelColIdx] ?? "—");
}

// A column literally named `count` / `total` / `value` carries no meaning in a
// sentence — "Marketing leads on count" reads like a typo. For those we switch
// to a phrasing that doesn't need the metric's name.
const GENERIC_METRIC = /^(count|total|value|num|n|rows|amount|sum|qty|quantity)$/;

function isGenericMetric(columnName: string): boolean {
  return GENERIC_METRIC.test(columnName.trim().toLowerCase().replace(/^(total|num)_/, ""));
}

/**
 * What can honestly be said about a result with no metric column — the shape
 * a `SELECT DISTINCT` or lookup query returns. Cardinality, repetition and
 * blanks are countable; nothing here infers a measurement that isn't there.
 */
function describeCategorical(columns: string[], rows: unknown[][]): Insight[] {
  const out: Insight[] = [];
  // Only the first couple of columns — a wide result would produce a wall of
  // near-identical sentences.
  for (let c = 0; c < Math.min(columns.length, 2); c++) {
    const name = humanizeColumn(columns[c]);
    const values = rows.map((r) => (r[c] === null || r[c] === undefined ? "" : String(r[c])));
    const nonEmpty = values.filter((v) => v !== "");
    if (nonEmpty.length === 0) {
      out.push({ kind: "gaps", text: `${name} is empty on every row.` });
      continue;
    }

    const counts = new Map<string, number>();
    for (const v of nonEmpty) counts.set(v, (counts.get(v) ?? 0) + 1);
    const distinct = counts.size;

    if (distinct === nonEmpty.length) {
      // Don't phrase this as a discovery — for a DISTINCT query uniqueness is
      // guaranteed by construction. The count is the fact worth stating.
      out.push({
        kind: "spread",
        text: `${distinct.toLocaleString()} distinct ${name.toLowerCase()} value${
          distinct === 1 ? "" : "s"
        }, with no repeats.`,
        figure: distinct.toLocaleString(),
      });
    } else {
      const [topValue, topCount] = Array.from(counts.entries()).sort((a, b) => b[1] - a[1])[0];
      out.push({
        kind: "leader",
        text: `${name} has ${distinct.toLocaleString()} distinct value${
          distinct === 1 ? "" : "s"
        } across ${rows.length.toLocaleString()} rows — ${topValue} is the most frequent, on ${topCount}.`,
        figure: distinct.toLocaleString(),
      });
    }

    const blanks = values.length - nonEmpty.length;
    if (blanks > 0) {
      out.push({
        kind: "gaps",
        text: `${blanks} of ${rows.length} rows have no ${name.toLowerCase()} value.`,
      });
    }
  }
  return out.slice(0, 4);
}

// ── Single result reading ─────────────────────────────────────

/**
 * Read a query result: what it contains, and the two-to-four things about it
 * worth saying out loud. Returns an empty reading when there is nothing
 * defensible to say (no rows, no metric column).
 */
export function readResult(columns: string[], rows: unknown[][]): ResultReading {
  if (rows.length === 0) {
    return {
      summary: "The query ran successfully but matched no rows.",
      insights: [],
    };
  }

  const cls = classifyColumns(columns, rows);
  const insights: Insight[] = [];

  if (!cls.hasData) {
    // No metric column — a DISTINCT / lookup result. There is nothing to
    // average, but cardinality and repetition are real, checkable facts about
    // the rows in front of us, so say those rather than nothing.
    return {
      summary: `${rows.length.toLocaleString()} row${rows.length === 1 ? "" : "s"} across ${
        columns.length
      } column${columns.length === 1 ? "" : "s"}, none of them numeric — a list rather than a measurement.`,
      insights: describeCategorical(columns, rows),
    };
  }

  const metricIdx = cls.firstNumIdx;
  const metricName = humanizeColumn(columns[metricIdx]);
  const values = numericValues(rows, metricIdx);
  const total = values.reduce((a, b) => a + b, 0);
  const allNonNegative = values.every((v) => v >= 0);

  // ── Single-value result ───────────────────────────────────
  if (rows.length === 1 && cls.numericCols.length >= 1) {
    const parts = cls.numericCols
      .slice(0, 3)
      .map((i) => `${humanizeColumn(columns[i])} is ${formatFull(toNumber(rows[0][i]) ?? 0)}`);
    return {
      summary: `A single aggregate row: ${parts.join(", ")}.`,
      insights: [
        {
          kind: "single",
          text: `${metricName} is ${formatFull(values[0] ?? 0)}.`,
          figure: formatCompact(values[0] ?? 0),
        },
      ],
    };
  }

  const dimension = cls.labelColIdx !== null ? humanizeColumn(columns[cls.labelColIdx]) : null;
  const summary = dimension
    ? `${rows.length.toLocaleString()} ${dimension.toLowerCase()} value${
        rows.length === 1 ? "" : "s"
      }, measured by ${metricName.toLowerCase()}${
        cls.numericCols.length > 1 ? ` and ${cls.numericCols.length - 1} other metric${cls.numericCols.length > 2 ? "s" : ""}` : ""
      }.`
    : `${rows.length.toLocaleString()} rows with ${cls.numericCols.length} numeric column${
        cls.numericCols.length === 1 ? "" : "s"
      }.`;

  // ── Trend (time axis) ─────────────────────────────────────
  if (cls.hasTimeAxis && cls.labelColIdx !== null && rows.length >= 3) {
    const points = rows
      .map((r) => ({
        t: parseTemporal(r[cls.labelColIdx!])?.getTime() ?? null,
        v: toNumber(r[metricIdx]),
      }))
      .filter((p): p is { t: number; v: number } => p.t !== null && p.v !== null)
      .sort((a, b) => a.t - b.t);

    if (points.length >= 3) {
      // On a noisy series the first and last single points are a coin flip —
      // one quiet month at either end invents a trend. Compare the mean of the
      // opening third against the closing third instead, and say so.
      const useThirds = points.length >= 6;
      const third = Math.max(1, Math.floor(points.length / 3));
      const first = useThirds
        ? mean(points.slice(0, third).map((p) => p.v))
        : points[0].v;
      const last = useThirds
        ? mean(points.slice(-third).map((p) => p.v))
        : points[points.length - 1].v;
      const window = useThirds
        ? `comparing the first and last ${third} period${third === 1 ? "" : "s"}`
        : "start to end";
      const change = first === 0 ? null : (last - first) / Math.abs(first);
      const peak = points.reduce((m, p) => (p.v > m.v ? p : m), points[0]);
      const peakLabel = new Date(peak.t).toLocaleDateString(undefined, {
        month: "short",
        year: "numeric",
        timeZone: "UTC",
      });
      if (change !== null && Math.abs(change) >= 0.05) {
        insights.push({
          kind: "trend",
          direction: change > 0 ? "up" : "down",
          // "trended" keeps the sentence grammatical whether the metric name
          // reads singular (Revenue) or plural (Hires).
          text: `${metricName} trended ${change > 0 ? "up" : "down"} ${formatSignedPercent(
            change
          ).replace(/^[+−]/, "")} across the period (${formatFull(first)} → ${formatFull(
            last
          )}, ${window}).`,
          figure: formatSignedPercent(change),
        });
      } else {
        insights.push({
          kind: "trend",
          direction: "flat",
          text: `${metricName} held roughly flat across the period, near ${formatFull(mean(points.map((p) => p.v)))}.`,
        });
      }
      insights.push({
        kind: "outlier",
        text: `The high point was ${peakLabel} at ${formatFull(peak.v)}.`,
        figure: formatCompact(peak.v),
      });
    }
  }

  // ── Leader + concentration (categorical) ──────────────────
  if (!cls.hasTimeAxis && cls.labelColIdx !== null && rows.length >= 2) {
    const ranked = rows
      .map((_, i) => ({ i, v: toNumber(rows[i][metricIdx]) }))
      .filter((x): x is { i: number; v: number } => x.v !== null)
      .sort((a, b) => b.v - a.v);

    if (ranked.length >= 2) {
      const top = ranked[0];
      const share = allNonNegative && total > 0 ? top.v / total : null;
      const generic = isGenericMetric(columns[metricIdx]);
      const label = labelAt(rows, cls, top.i);
      // Nothing "leads" when the top value is tied — calling one of them the
      // leader is just whichever row the sort happened to keep first.
      const tied = ranked.filter((r) => r.v === top.v).length;

      if (tied > 1) {
        insights.push({
          kind: "leader",
          text:
            tied === ranked.length
              ? `Every ${dimension ? dimension.toLowerCase() : "row"} is level on ${metricName.toLowerCase()} at ${formatFull(
                  top.v
                )} — nothing separates them.`
              : `${tied} of ${ranked.length} tie for the highest ${metricName.toLowerCase()}, at ${formatFull(
                  top.v
                )} each (${ranked
                  .filter((r) => r.v === top.v)
                  .slice(0, 3)
                  .map((r) => labelAt(rows, cls, r.i))
                  .join(", ")}${tied > 3 ? ", …" : ""}).`,
          figure: formatCompact(top.v),
        });
      } else {
        insights.push({
          kind: "leader",
          text:
            share !== null
              ? generic
                ? `${label} is the largest ${
                    dimension ? dimension.toLowerCase() : "group"
                  } at ${formatFull(top.v)} — ${formatPercent(share)} of the total.`
                : `${label} leads on ${metricName.toLowerCase()} with ${formatFull(
                    top.v
                  )} — ${formatPercent(share)} of the total.`
              : `${label} is highest on ${metricName.toLowerCase()} at ${formatFull(top.v)}.`,
          figure: formatCompact(top.v),
        });
      }

      if (allNonNegative && total > 0 && ranked.length >= 5) {
        const top3 = ranked.slice(0, 3).reduce((s, r) => s + r.v, 0) / total;
        if (top3 >= 0.6) {
          insights.push({
            kind: "concentration",
            text: `Concentrated: the top 3 of ${ranked.length} account for ${formatPercent(
              top3
            )} of all ${metricName.toLowerCase()}.`,
            figure: formatPercent(top3),
          });
        } else if (top3 <= 0.45) {
          insights.push({
            kind: "concentration",
            text: `Evenly spread: the top 3 of ${ranked.length} hold only ${formatPercent(
              top3
            )} of the total.`,
            figure: formatPercent(top3),
          });
        }
      }

      // Spread — how far apart the extremes are.
      const bottom = ranked[ranked.length - 1];
      if (ranked.length >= 3 && bottom.v > 0 && top.v / bottom.v >= 1.15) {
        insights.push({
          kind: "spread",
          text: `${metricName} ranges from ${formatFull(bottom.v)} (${labelAt(
            rows,
            cls,
            bottom.i
          )}) to ${formatFull(top.v)} — a ${(top.v / bottom.v).toFixed(1)}× gap.`,
        });
      }

      // Outliers — values beyond 2σ, which a bar chart alone doesn't flag.
      const sd = stdev(values);
      const m = mean(values);
      if (sd > 0 && ranked.length >= 5) {
        const outliers = ranked.filter((r) => Math.abs(r.v - m) > 2 * sd);
        if (outliers.length > 0 && outliers.length <= 3) {
          insights.push({
            kind: "outlier",
            text: `${outliers
              .map((o) => labelAt(rows, cls, o.i))
              .join(", ")} ${outliers.length === 1 ? "sits" : "sit"} more than 2σ from the average of ${formatFull(m)}.`,
          });
        }
      }
    }
  }

  // ── Data-quality gaps ─────────────────────────────────────
  const nullCount = rows.filter((r) => {
    const v = r[metricIdx];
    return v === null || v === undefined || v === "";
  }).length;
  if (nullCount > 0) {
    insights.push({
      kind: "gaps",
      text: `${nullCount} of ${rows.length} row${rows.length === 1 ? "" : "s"} have no ${metricName.toLowerCase()} value and are excluded from the chart.`,
    });
  }

  // ── Scale note ────────────────────────────────────────────
  if (allNonNegative && total > 0 && rows.length >= 2 && !cls.hasTimeAxis) {
    insights.push({
      kind: "scale",
      text: `${metricName} totals ${formatFull(total)} across ${rows.length} ${
        dimension ? dimension.toLowerCase() : "row"
      } value${rows.length === 1 ? "" : "s"}, averaging ${formatFull(total / rows.length)}.`,
      figure: formatCompact(total),
    });
  }

  return { summary, insights };
}

// ── Follow-up suggestions ─────────────────────────────────────

// Dimensions worth offering as a breakdown, in preference order. A follow-up
// that names a column the data doesn't have is worse than no follow-up.
const DIMENSION_HINTS = [
  "department", "status", "category", "type", "region", "location",
  "priority", "project", "job_title", "manager", "source", "team", "role",
];

const TIME_HINTS = ["date", "month", "year", "created_at", "hire_date", "due_date", "review_date"];

function datasetsForPlan(plan: QueryPlan | undefined, datasets: DatasetMeta[]): DatasetMeta[] {
  if (!plan || datasets.length === 0) return [];
  const sql = (plan.sql || "").toLowerCase();
  const fromSteps = new Set(
    (plan.steps || []).map((s) => `${s.catalog}.${s.schema_name}.${s.table}`.toLowerCase())
  );
  return datasets.filter((d) => {
    const path = d.trino_path.toLowerCase();
    if (fromSteps.has(path.replace(/"/g, ""))) return true;
    // Fall back to a literal match on the SQL — plan.steps is best-effort.
    return sql.includes(path) || sql.includes(d.name.toLowerCase());
  });
}

/**
 * Suggest the next questions to ask. Grounded in the columns the query
 * actually returned plus the schema of the tables it read, so every suggestion
 * refers to something real.
 */
export function suggestFollowUps(args: {
  columns: string[];
  rows: unknown[][];
  plan?: QueryPlan;
  datasets?: DatasetMeta[];
}): string[] {
  const { columns, rows, plan, datasets = [] } = args;
  if (rows.length === 0) {
    return [
      "Remove the filters and show everything",
      "Show me what values that column actually contains",
    ];
  }

  const cls = classifyColumns(columns, rows);
  const out: string[] = [];
  const metricName = cls.hasData ? humanizeColumn(columns[cls.firstNumIdx]).toLowerCase() : null;
  const dimension =
    cls.labelColIdx !== null ? humanizeColumn(columns[cls.labelColIdx]).toLowerCase() : null;

  const resultCols = new Set(columns.map((c) => c.toLowerCase()));
  const related = datasetsForPlan(plan, datasets);
  const schemaCols = related.flatMap((d) => d.columns || []);

  const otherDimensions = schemaCols
    .filter((c) => !resultCols.has(c.column_name.toLowerCase()))
    .filter((c) => {
      const n = c.column_name.toLowerCase();
      const t = (c.data_type || "").toLowerCase();
      return (
        DIMENSION_HINTS.some((h) => n.includes(h)) &&
        (t.includes("varchar") || t.includes("text") || t.includes("char"))
      );
    })
    .map((c) => c.column_name);

  const timeColumn = schemaCols
    .map((c) => c.column_name)
    .find((n) => TIME_HINTS.some((h) => n.toLowerCase().includes(h)));

  // ── No metric column ──────────────────────────────────────
  // A DISTINCT / lookup result can't be charted, which is exactly when the
  // reader most needs a nudge toward the question that CAN be. Suggestions
  // here are built from the returned columns, so they work even when the
  // schema lookup finds nothing.
  if (!cls.hasData) {
    const catIdx = cls.categoricalCols[0] ?? 0;
    const primary = humanizeColumn(columns[catIdx]).toLowerCase();
    const secondary =
      cls.categoricalCols.length > 1
        ? humanizeColumn(columns[cls.categoricalCols[1]]).toLowerCase()
        : null;

    // Turning the list into a measurement is the single most useful next step.
    out.push(`Count how many rows there are per ${primary}`);
    if (secondary) {
      out.push(`Show the number of distinct ${secondary} values per ${primary}`);
    } else if (otherDimensions.length > 0) {
      out.push(`Break ${primary} down by ${humanizeColumn(otherDimensions[0]).toLowerCase()}`);
    }
    if (timeColumn) {
      out.push(`Show how the number of ${primary} values changed over time`);
    }
    out.push(`Which ${primary} values appear most often?`);
    return Array.from(new Set(out)).slice(0, 4);
  }

  // A time series and a ranked category list want completely different next
  // questions — "show the bottom 5 months" is nonsense.
  if (cls.hasTimeAxis && metricName) {
    out.push(`Show ${metricName} by quarter instead of month`);
    out.push(`Which period had the highest ${metricName}?`);
    if (otherDimensions.length > 0) {
      out.push(`Split the ${metricName} trend by ${humanizeColumn(otherDimensions[0]).toLowerCase()}`);
    }
    out.push(`Compare the last 12 periods against the 12 before them`);
    return Array.from(new Set(out)).slice(0, 4);
  }

  // 1. Rank the other way — the cheapest genuinely useful follow-up.
  if (metricName && dimension && rows.length >= 4) {
    out.push(`Show the bottom 5 by ${metricName} instead`);
  }

  // 2. Break the leader down by another real dimension.
  if (metricName && otherDimensions.length > 0) {
    out.push(`Break ${metricName} down by ${humanizeColumn(otherDimensions[0]).toLowerCase()}`);
  }

  // 3. Add a time dimension when the table has one and the result doesn't.
  if (metricName && !cls.hasTimeAxis && timeColumn) {
    out.push(`Show how ${metricName} changed over time by month`);
  }

  // 4. Second metric for context.
  if (cls.numericCols.length === 1 && metricName && related.length > 0) {
    const otherMetric = schemaCols.find((c) => {
      const n = c.column_name.toLowerCase();
      const t = (c.data_type || "").toLowerCase();
      return (
        !resultCols.has(n) &&
        !/(^|_)(id|key)($|_)/.test(n) &&
        (t.includes("int") || t.includes("decimal") || t.includes("numeric") || t.includes("double") || t.includes("real"))
      );
    });
    if (otherMetric) {
      out.push(`Add average ${humanizeColumn(otherMetric.column_name).toLowerCase()} alongside ${metricName}`);
    }
  }

  // 5. Drill into the leading category.
  if (cls.hasData && cls.labelColIdx !== null && rows.length >= 2) {
    const ranked = rows
      .map((r) => ({ label: String(r[cls.labelColIdx!] ?? ""), v: toNumber(r[cls.firstNumIdx]) }))
      .filter((x) => x.v !== null && x.label)
      .sort((a, b) => (b.v as number) - (a.v as number));
    if (ranked.length > 0) {
      out.push(`Show the detail rows behind ${ranked[0].label}`);
    }
  }

  // 6. Generic fallbacks so the section is never empty.
  if (out.length < 3 && metricName) {
    out.push(`Which rows are above the average ${metricName}?`);
  }
  if (out.length < 3 && dimension) {
    out.push(`How many distinct ${dimension} values are there?`);
  }

  // De-dupe, keep it to four — a wall of suggestions gets ignored.
  return Array.from(new Set(out)).slice(0, 4);
}

// ── Dashboard-level insights ──────────────────────────────────

export interface WidgetData {
  title: string;
  chartType: string;
  columns: string[];
  rows: unknown[][];
}

// Rank kinds so the dashboard strip leads with the most useful reading rather
// than whichever widget happened to load first.
const KIND_PRIORITY: Record<InsightKind, number> = {
  trend: 0,
  leader: 1,
  concentration: 2,
  outlier: 3,
  spread: 4,
  single: 5,
  scale: 6,
  gaps: 7,
};

/**
 * Pick the few most notable findings across a dashboard's widgets — at most
 * one per widget, so a single busy tile can't crowd out the rest.
 */
export function buildDashboardInsights(widgets: WidgetData[], limit = 4): Insight[] {
  const perWidget: Insight[] = [];

  for (const w of widgets) {
    if (!w.rows || w.rows.length === 0) continue;
    const { insights } = readResult(w.columns, w.rows);
    // A big-number tile restates its own value — it adds nothing here.
    const useful = insights.filter(
      (i) => !(w.chartType === "number" && i.kind === "single") && i.kind !== "gaps"
    );
    if (useful.length === 0) continue;
    const best = useful.slice().sort((a, b) => KIND_PRIORITY[a.kind] - KIND_PRIORITY[b.kind])[0];
    perWidget.push({ ...best, source: w.title });
  }

  return perWidget
    .sort((a, b) => KIND_PRIORITY[a.kind] - KIND_PRIORITY[b.kind])
    .slice(0, limit);
}
