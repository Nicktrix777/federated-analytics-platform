import React, { useState, useEffect, useRef } from "react";
import ReactECharts from "echarts-for-react";
import { api } from "../api/client";
import type { DashboardWidget } from "../types";
import {
  classifyColumns,
  recommendChart,
  buildWidgetChartOption,
  buildGaugeOption,
  toNumber,
} from "../lib/chartTheme";
import { formatCompact, formatFull, humanizeColumn } from "../lib/format";
import { useTheme } from "../theme";
import { Icon } from "./ui/Icon";

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

    const duration = 900;
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

export interface WidgetResult {
  columns: string[];
  rows: unknown[][];
  row_count: number;
}

interface Props {
  widget: DashboardWidget;
  onEdit: () => void;
  onDelete: () => void;
  /** Publishes each successful load so the page can derive dashboard insights. */
  onResult?: (widgetId: number, result: WidgetResult | null) => void;
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

export default function DashboardWidgetCard({ widget, onEdit, onDelete, onResult }: Props) {
  const [result, setResult] = useState<WidgetResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { theme } = useTheme();
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Whether the first successful/attempted load has completed. Interval
  // refreshes run silently (keep the last data on screen) so the tile never
  // blanks to a spinner every cycle.
  const loadedOnceRef = useRef(false);
  // Held in a ref so a parent that re-creates the callback each render can't
  // retrigger the query effect.
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;

  const runQuery = async (opts?: { silent?: boolean }) => {
    // Full-tile spinner only before the first load; refreshes/retries after
    // that hold the previous render (dimmed) so there's no skeleton flash.
    if (!opts?.silent && !loadedOnceRef.current) setLoading(true);
    else setRefreshing(true);
    setError(null);
    try {
      const resp = await api.query(widget.query_sql, "sql");
      const next: WidgetResult = {
        columns: resp.columns,
        rows: resp.rows,
        row_count: resp.row_count,
      };
      setResult(next);
      loadedOnceRef.current = true;
      onResultRef.current?.(widget.id, next);
    } catch (e: unknown) {
      const err = e as { response?: { data?: { error?: string } }; message?: string };
      setError(err.response?.data?.error ?? err.message ?? "Query failed");
      onResultRef.current?.(widget.id, null);
    } finally {
      setLoading(false);
      setRefreshing(false);
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

  // Stop publishing this widget's data once it unmounts, so a deleted tile
  // can't keep contributing to the insight strip.
  useEffect(
    () => () => onResultRef.current?.(widget.id, null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [widget.id]
  );

  const config = parseChartConfig(widget.chart_config);
  // Undefined palette → the builder falls back to the active theme's series
  // ramp, so tiles recolor on light/dark toggle.
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
          <Icon name="alert" size={16} />
          <span className="widget-error-text">{error}</span>
          <button className="widget-retry" onClick={() => runQuery()}>
            <Icon name="refresh" size={12} /> Retry
          </button>
        </div>
      );
    }

    if (!result) return null;

    const { columns, rows, row_count } = result;
    if (rows.length === 0) {
      return (
        <div className="widget-empty">
          <Icon name="chart-area-off" size={18} />
          <span>No rows returned</span>
        </div>
      );
    }

    const type = widget.chart_type;

    // ── Big Number ────────────────────────────────────────────
    if (type === "number") {
      const raw = toNumber(rows[0][0]);
      const body = raw === null ? String(rows[0][0] ?? "—") : formatCompact(raw);
      const display = `${config.prefix ?? ""}${body}${config.suffix ?? config.unit ?? ""}`;
      return (
        <div className="widget-number">
          <div
            className="widget-number-value"
            title={raw === null ? undefined : formatFull(raw)}
          >
            <AnimatedNumber value={display} />
          </div>
          {columns[0] && (
            <div className="widget-number-label">{humanizeColumn(columns[0])}</div>
          )}
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
          option={buildGaugeOption(val, max, theme)}
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
                {columns.map((c) => <th key={c}>{humanizeColumn(c)}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 50).map((row, i) => (
                <tr key={i}>
                  {(row as unknown[]).map((cell, j) => {
                    const n = toNumber(cell);
                    return (
                      <td key={j} className={n === null ? undefined : "widget-table-num"}>
                        {n === null ? String(cell ?? "—") : formatFull(n)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          {row_count > 50 && (
            <div className="widget-table-more">Showing 50 of {row_count.toLocaleString()} rows</div>
          )}
        </div>
      );
    }

    // ── Charts (bar / line / area / pie; scatter falls back to bar) ──
    const cls = classifyColumns(columns, rows);
    const chartType = type === "scatter" ? "bar" : (type as "bar" | "line" | "area" | "pie");

    // The configured chart type is honoured except where the data makes it
    // meaningless — one aggregate row drawn as a single bar, or a metric that
    // is identical across every category. Those get the number they actually
    // are. Anything with a real distribution renders as configured.
    const recommendation = recommendChart(columns, rows, cls);
    if (recommendation.form === "stat") {
      const statValue = toNumber(rows[0][cls.hasData ? cls.firstNumIdx : 0]);
      if (statValue !== null) {
        return (
          <div className="widget-number">
            <div className="widget-number-value" title={formatFull(statValue)}>
              <AnimatedNumber value={formatCompact(statValue)} />
            </div>
            <div className="widget-number-label">
              {humanizeColumn(columns[cls.hasData ? cls.firstNumIdx : 0])}
            </div>
            <div className="widget-number-note">{recommendation.reason}</div>
          </div>
        );
      }
    }

    const option = buildWidgetChartOption(chartType, columns, rows, cls, palette, {
      themeHint: theme,
    });
    if (!option) {
      return (
        <div className="widget-empty">
          <Icon name="chart-area-off" size={18} />
          <span>No numeric column to plot</span>
        </div>
      );
    }

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
        <div className="widget-drag-handle" title="Drag to reposition">
          <Icon name="grip" size={14} />
        </div>
        <div className="widget-title" title={widget.title}>{widget.title}</div>
        <div className="widget-actions">
          <button className="widget-btn" onClick={() => runQuery()} title="Refresh" aria-label="Refresh widget">
            <Icon name="refresh" size={14} />
          </button>
          <button className="widget-btn" onClick={onEdit} title="Edit" aria-label="Edit widget">
            <Icon name="edit" size={14} />
          </button>
          <button
            className="widget-btn widget-btn-danger"
            onClick={onDelete}
            title="Delete"
            aria-label="Delete widget"
          >
            <Icon name="close" size={14} />
          </button>
        </div>
      </div>
      <div className={`widget-body${refreshing ? " widget-body--refreshing" : ""}`}>
        {renderContent()}
      </div>
      {widget.refresh_rate_ms > 0 && (
        <div className="widget-refresh-indicator">
          Auto-refresh: {widget.refresh_rate_ms / 1000}s
        </div>
      )}
    </div>
  );
}
