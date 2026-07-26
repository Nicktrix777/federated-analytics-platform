// Shared value formatting for charts, stat tiles, insights and tables.
//
// Every number the UI shows passes through here so an axis tick, a tooltip, a
// big-number tile and an insight sentence all render the same value the same
// way. Previously each call site rolled its own `toLocaleString()` and they
// disagreed (axis showed `1583812.5`, the tile showed `1,583,812.5`).

// ── Numbers ───────────────────────────────────────────────────

/** Compact form for axis ticks and tiles: 1,284 · 12.9K · 4.2M · 1.1B. */
export function formatCompact(n: number): string {
  const abs = Math.abs(n);
  if (!Number.isFinite(n)) return "—";
  if (abs >= 1e12) return trimZeros((n / 1e12).toFixed(1)) + "T";
  if (abs >= 1e9) return trimZeros((n / 1e9).toFixed(1)) + "B";
  if (abs >= 1e6) return trimZeros((n / 1e6).toFixed(1)) + "M";
  if (abs >= 10_000) return trimZeros((n / 1e3).toFixed(1)) + "K";
  if (Number.isInteger(n)) return n.toLocaleString();
  if (abs >= 100) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (abs >= 1) return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (abs === 0) return "0";
  return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

/** Full precision with separators — tooltips, table cells, insight sentences. */
export function formatFull(n: number): string {
  if (!Number.isFinite(n)) return "—";
  if (Number.isInteger(n)) return n.toLocaleString();
  const abs = Math.abs(n);
  const decimals = abs >= 1000 ? 0 : abs >= 1 ? 2 : 4;
  return n.toLocaleString(undefined, { maximumFractionDigits: decimals });
}

/** Percentage with one decimal only when it changes the reading. */
export function formatPercent(fraction: number): string {
  const pct = fraction * 100;
  if (!Number.isFinite(pct)) return "—";
  return `${trimZeros(pct.toFixed(pct >= 10 ? 0 : 1))}%`;
}

/** Signed delta for trend text: +12.4% / −3% / no change. */
export function formatSignedPercent(fraction: number): string {
  const pct = fraction * 100;
  if (!Number.isFinite(pct)) return "—";
  if (Math.abs(pct) < 0.05) return "no change";
  const sign = pct > 0 ? "+" : "−";
  return `${sign}${trimZeros(Math.abs(pct).toFixed(Math.abs(pct) >= 10 ? 0 : 1))}%`;
}

function trimZeros(s: string): string {
  return s.replace(/\.0+$/, "").replace(/(\.\d*[1-9])0+$/, "$1");
}

// ── Column names ──────────────────────────────────────────────

/** `total_headcount` → `Total headcount`; `avgSalary` → `Avg salary`. */
export function humanizeColumn(name: string): string {
  if (!name) return "";
  const spaced = name
    .replace(/[_\-.]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .trim();
  if (!spaced) return name;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

/** Truncate a category label for an axis, keeping the head readable. */
export function truncateLabel(label: string, max: number): string {
  if (label.length <= max) return label;
  return label.slice(0, Math.max(1, max - 1)) + "…";
}

// ── Temporal values ───────────────────────────────────────────

const ISO_DATE = /^\d{4}-\d{2}(-\d{2})?([ T]\d{2}:\d{2}(:\d{2})?(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/;

/**
 * True when a raw cell reads as a date/timestamp. Deliberately strict: a bare
 * `2023` must NOT parse as a date, or every year-valued metric column would be
 * misread as a time axis.
 */
export function looksTemporal(v: unknown): boolean {
  if (v instanceof Date) return true;
  if (typeof v !== "string") return false;
  const s = v.trim();
  if (!ISO_DATE.test(s)) return false;
  return !Number.isNaN(Date.parse(s.replace(" ", "T")));
}

export function parseTemporal(v: unknown): Date | null {
  if (v instanceof Date) return v;
  if (typeof v !== "string") return null;
  const t = Date.parse(v.trim().replace(" ", "T"));
  return Number.isNaN(t) ? null : new Date(t);
}

/**
 * Axis label for a temporal value, at the coarsest granularity the series
 * actually varies by — a month-truncated series reads `May 2023`, not
 * `2023-05-01 00:00:00.000`.
 */
export function formatTemporal(
  v: unknown,
  granularity: "year" | "month" | "day" | "time"
): string {
  const d = parseTemporal(v);
  if (!d) return String(v ?? "");
  switch (granularity) {
    case "year":
      return String(d.getUTCFullYear());
    case "month":
      return d.toLocaleDateString(undefined, {
        month: "short",
        year: "numeric",
        timeZone: "UTC",
      });
    case "day":
      return d.toLocaleDateString(undefined, {
        month: "short",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      });
    default:
      return d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        timeZone: "UTC",
      });
  }
}

/** Coarsest granularity that still distinguishes every value in the column. */
export function detectGranularity(values: unknown[]): "year" | "month" | "day" | "time" {
  const dates = values.map(parseTemporal).filter((d): d is Date => d !== null);
  if (dates.length === 0) return "day";
  const allMidnight = dates.every(
    (d) => d.getUTCHours() === 0 && d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0
  );
  if (!allMidnight) return "time";
  const allFirstOfMonth = dates.every((d) => d.getUTCDate() === 1);
  if (!allFirstOfMonth) return "day";
  const allJanuary = dates.every((d) => d.getUTCMonth() === 0);
  return allJanuary ? "year" : "month";
}
