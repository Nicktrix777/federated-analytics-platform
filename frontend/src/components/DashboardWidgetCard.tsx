import React, { useState, useEffect, useRef } from "react";
import ReactECharts from "echarts-for-react";
import { api } from "../api/client";
import type { DashboardWidget } from "../types";
import {
  classifyColumns,
  buildWidgetChartOption,
  buildGaugeOption,
  toNumber,
} from "../lib/chartTheme";
import { useTheme } from "../theme";

const AnimatedNumber = ({ value }: { value: string }) => {
  const [displayValue, setDisplayValue] = useState("0");

  useEffect(() => {
    const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReducedMotion) {
      setDisplayValue(value);
      return;
    }
    const match = value.match(/^([^0-9\.\-]*)([\-0-9\.,]+)([^0-9]*)$/);
    if (!match) {
      setDisplayValue(value);
      return;
    }
    const prefix = match[1];
    const numStr = match[2].replace(/,/g, "");
    const suffix = match[3];
    const target = parseFloat(numStr);

    if (isNaN(target)) {
      setDisplayValue(value);
      return;
    }

    const duration = 1000;
    let startTimestamp: number | null = null;
    let animationFrame: number;

    const step = (timestamp: number) => {
      if (!startTimestamp) startTimestamp = timestamp;
      const progress = Math.min((timestamp - startTimestamp) / duration, 1);
      const ease = progress === 1 ? 1 : 1 - Math.pow(2, -10 * progress);
      const currentNum = target * ease;

      const hasDecimals = numStr.includes(".");
      const decimals = hasDecimals ? numStr.split(".")[1].length : 0;
      const formattedNum = currentNum.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      });

      setDisplayValue(`${prefix}${formattedNum}${suffix}`);

      if (progress < 1) {
        animationFrame = window.requestAnimationFrame(step);
      } else {
        setDisplayValue(value);
      }
    };
    animationFrame = window.requestAnimationFrame(step);

    return () => window.cancelAnimationFrame(animationFrame);
  }, [value]);

  return <>{displayValue}</>;
};

interface Props {
  widget: DashboardWidget;
  onEdit: () => void;
  onDelete: () => void;
}

interface QueryResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
}

// A widget's persisted chart_config (best-effort — never defined by a strict
// schema). We honor a few well-known keys when present.
interface ChartConfig {
  colors?: string[];
  max?: number;
  unit?: string;
  prefix?: string;
  suffix?: string;
}

function parseChartConfig(raw?: string): ChartConfig {
  if (!raw) return {};
  try {
    const o = JSON.parse(raw);
    return o && typeof o === "object" ? (o as ChartConfig) : {};
  } catch {
    return {};
  }
}

function formatBigNumber(raw: unknown, config: ChartConfig): string {
  const n = toNumber(raw);
  if (n === null) return String(raw ?? "");
  const body = Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return `${config.prefix ?? ""}${body}${config.suffix ?? config.unit ?? ""}`;
}

export default function DashboardWidgetCard({ widget, onEdit, onDelete }: Props) {
  const [result, setResult] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { theme } = useTheme();
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Whether the first successful/attempted load has completed. Interval
  // refreshes run silently (keep the last data on screen) so the tile never
  // blanks to a spinner every cycle.
  const loadedOnceRef = useRef(false);

  const runQuery = async (opts?: { silent?: boolean }) => {
    // Full-tile spinner only before the first load; refreshes/retries after
    // that keep whatever's already rendered.
    if (!opts?.silent && !loadedOnceRef.current) setLoading(true);
    setError(null);
    try {
      const resp = await api.query(widget.query_sql, "sql");
      setResult({
        columns: resp.columns,
        rows: resp.rows,
        row_count: resp.row_count,
      });
      loadedOnceRef.current = true;
    } catch (e: unknown) {
      const err = e as { response?: { data?: { error?: string } }; message?: string };
      setError(err.response?.data?.error ?? err.message ?? "Query failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // A changed query/refresh-rate is a genuine reload — allow the initial
    // spinner again for the new query.
    loadedOnceRef.current = false;
    runQuery();

    if (widget.refresh_rate_ms > 0) {
      intervalRef.current = setInterval(() => runQuery({ silent: true }), widget.refresh_rate_ms);
    }

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widget.query_sql, widget.refresh_rate_ms]);

  const config = parseChartConfig(widget.chart_config);
  // Undefined palette → buildWidgetChartOption falls back to the active theme's
  // greyscale ramp, so tiles recolor on light/dark toggle.
  const palette = Array.isArray(config.colors) && config.colors.length > 0 ? config.colors : undefined;

  const renderContent = () => {
    if (loading) {
      return (
        <div className="widget-loading">
          <div className="spinner-sm" />
        </div>
      );
    }

    if (error) {
      return (
        <div className="widget-error">
          <span>⚠️ {error}</span>
          <button className="widget-retry" onClick={() => runQuery()}>
            ↻ Retry
          </button>
        </div>
      );
    }

    if (!result) return null;

    const { columns, rows, row_count } = result;
    if (rows.length === 0) return <div className="widget-empty">No data</div>;

    const type = widget.chart_type;

    // ── Big Number ────────────────────────────────────────────
    if (type === "number") {
      return (
        <div className="widget-number">
          <div className="widget-number-value">
            <AnimatedNumber value={formatBigNumber(rows[0][0], config)} />
          </div>
          {columns[0] && <div className="widget-number-label">{columns[0]}</div>}
        </div>
      );
    }

    // ── Gauge ─────────────────────────────────────────────────
    if (type === "gauge") {
      const val = toNumber(rows[0][0]) ?? 0;
      const max = config.max ?? (val <= 1 ? 1 : Math.max(100, Math.ceil(val)));
      return (
        <ReactECharts
          key={`gauge-${theme}`}
          option={buildGaugeOption(val, max)}
          style={{ height: "100%", minHeight: "180px" }}
          opts={{ renderer: "canvas" }}
          notMerge
        />
      );
    }

    // ── Table ─────────────────────────────────────────────────
    if (type === "table") {
      return (
        <div className="widget-table-wrap">
          <table className="widget-table">
            <thead>
              <tr>
                {columns.map((c) => <th key={c}>{c}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 50).map((row, i) => (
                <tr key={i}>
                  {(row as unknown[]).map((cell, j) => (
                    <td key={j}>{String(cell ?? "")}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {row_count > 50 && (
            <div className="widget-table-more">Showing 50 of {row_count} rows</div>
          )}
        </div>
      );
    }

    // ── Charts (bar / line / area / pie; scatter falls back to bar) ──
    const cls = classifyColumns(columns, rows);
    const chartType = type === "scatter" ? "bar" : (type as "bar" | "line" | "area" | "pie");
    const option = buildWidgetChartOption(chartType, columns, rows, cls, palette);
    if (!option) return <div className="widget-empty">No numeric data to visualize</div>;

    return (
      <ReactECharts
        key={`${chartType}-${theme}`}
        option={option}
        style={{ height: "100%", minHeight: "180px" }}
        opts={{ renderer: "canvas" }}
        notMerge
      />
    );
  };

  return (
    <div className="widget-card">
      <div className="widget-header">
        <div className="widget-drag-handle" title="Drag to reposition">⠿</div>
        <div className="widget-title">{widget.title}</div>
        <div className="widget-actions">
          <button className="widget-btn" onClick={() => runQuery()} title="Refresh">⟳</button>
          <button className="widget-btn" onClick={onEdit} title="Edit">✏️</button>
          <button className="widget-btn widget-btn-danger" onClick={onDelete} title="Delete">✕</button>
        </div>
      </div>
      <div className="widget-body">{renderContent()}</div>
      {widget.refresh_rate_ms > 0 && (
        <div className="widget-refresh-indicator">
          Auto-refresh: {widget.refresh_rate_ms / 1000}s
        </div>
      )}
    </div>
  );
}
