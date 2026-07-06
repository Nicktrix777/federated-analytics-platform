import React, { useState, useEffect, useRef } from "react";
import ReactECharts from "echarts-for-react";
import { api } from "../api/client";
import type { DashboardWidget, ChartType } from "../types";

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

export default function DashboardWidgetCard({ widget, onEdit, onDelete }: Props) {
  const [result, setResult] = useState<QueryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const runQuery = async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api.query(widget.query_sql, "sql");
      setResult({
        columns: resp.columns,
        rows: resp.rows,
        row_count: resp.row_count,
      });
    } catch (e: unknown) {
      const err = e as { response?: { data?: { error?: string } }; message?: string };
      setError(err.response?.data?.error ?? err.message ?? "Query failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    runQuery();

    if (widget.refresh_rate_ms > 0) {
      intervalRef.current = setInterval(runQuery, widget.refresh_rate_ms);
    }

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [widget.query_sql, widget.refresh_rate_ms]);

  const getChartOption = (type: ChartType, cols: string[], rows: unknown[][]) => {
    if (rows.length === 0) return null;

    const labels = rows.map((r) => String(r[0]));
    const values = rows.map((r) => Number(r[1]) || 0);

    const baseStyle = {
      backgroundColor: "transparent",
      textStyle: { color: "#94a3b8" },
    };

    if (type === "number") {
      return null; // handled separately
    }

    if (type === "pie") {
      return {
        ...baseStyle,
        tooltip: { trigger: "item" },
        series: [{
          type: "pie",
          radius: ["40%", "70%"],
          data: rows.map((r) => ({ name: String(r[0]), value: Number(r[1]) || 0 })),
          itemStyle: { borderRadius: 4 },
          label: { color: "#94a3b8" },
        }],
      };
    }

    const isLine = type === "line" || type === "area";
    const isArea = type === "area";

    return {
      ...baseStyle,
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { color: "#64748b" },
        axisLine: { lineStyle: { color: "#334155" } },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: "#64748b" },
        splitLine: { lineStyle: { color: "#1e293b" } },
      },
      series: [{
        type: isLine ? "line" : "bar",
        data: values,
        smooth: isLine,
        areaStyle: isArea ? { opacity: 0.3 } : undefined,
        itemStyle: { color: "#6366f1", borderRadius: type === "bar" ? [4, 4, 0, 0] : 0 },
        lineStyle: isLine ? { color: "#6366f1" } : undefined,
        symbol: "circle",
        symbolSize: 6,
      }],
    };
  };

  const renderContent = () => {
    if (loading) {
      return (
        <div className="widget-loading">
          <div className="spinner-sm" />
        </div>
      );
    }

    if (error) {
      return <div className="widget-error">⚠️ {error}</div>;
    }

    if (!result) return null;

    const { columns, rows, row_count } = result;

    if (widget.chart_type === "number" && rows.length > 0) {
      return (
        <div className="widget-number">
          <div className="widget-number-value">{String(rows[0][0])}</div>
          {columns[0] && <div className="widget-number-label">{columns[0]}</div>}
        </div>
      );
    }

    if (widget.chart_type === "table") {
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

    const option = getChartOption(widget.chart_type, columns, rows);
    if (!option) return <div className="widget-empty">No data</div>;

    return (
      <ReactECharts
        option={option}
        style={{ height: "100%", minHeight: "180px" }}
        opts={{ renderer: "canvas" }}
      />
    );
  };

  return (
    <div className="widget-card">
      <div className="widget-header">
        <div className="widget-drag-handle" title="Drag to reposition">⠿</div>
        <div className="widget-title">{widget.title}</div>
        <div className="widget-actions">
          <button className="widget-btn" onClick={runQuery} title="Refresh">⟳</button>
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
