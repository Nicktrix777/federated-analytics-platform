import React, { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import GridLayout, { Layout } from "react-grid-layout";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import { dashboardsApi } from "../api/client";
import { streamAIOperation, SSEConnectionError } from "../api/sse";
import type {
  Dashboard,
  DashboardWidget,
  CreateWidgetPayload,
  ChartType,
  AIDashboardResponse,
  AIProgressEvent,
  AIProgressEventData,
  DroppedWidget,
} from "../types";
import DashboardWidgetCard from "../components/DashboardWidgetCard";
import AIProgressTimeline from "../components/AIProgressTimeline";

const CHART_TYPES: { type: ChartType; label: string; icon: string }[] = [
  { type: "table", label: "Table", icon: "📋" },
  { type: "bar", label: "Bar Chart", icon: "📊" },
  { type: "line", label: "Line Chart", icon: "📈" },
  { type: "pie", label: "Pie Chart", icon: "🥧" },
  { type: "area", label: "Area Chart", icon: "⛰️" },
  { type: "number", label: "Big Number", icon: "🔢" },
  { type: "gauge", label: "Gauge", icon: "⏱️" },
];

interface WidgetFormState {
  title: string;
  query_sql: string;
  chart_type: ChartType;
  refresh_rate_ms: number;
}

const DEFAULT_WIDGET_FORM: WidgetFormState = {
  title: "",
  query_sql: "",
  chart_type: "table",
  refresh_rate_ms: 0,
};

export default function DashboardBuilderPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const dashboardId = id ? Number(id) : null;

  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [widgets, setWidgets] = useState<DashboardWidget[]>([]);
  const [layout, setLayout] = useState<Layout[]>([]);
  const [loading, setLoading] = useState(true);
  const [showWidgetModal, setShowWidgetModal] = useState(false);
  const [widgetForm, setWidgetForm] = useState<WidgetFormState>(DEFAULT_WIDGET_FORM);
  const [savingWidget, setSavingWidget] = useState(false);
  const [editingWidget, setEditingWidget] = useState<DashboardWidget | null>(null);
  const [dashboardName, setDashboardName] = useState("");
  const [editingName, setEditingName] = useState(false);
  const [savingLayout, setSavingLayout] = useState(false);
  const [showRefineModal, setShowRefineModal] = useState(false);
  const [refineInstruction, setRefineInstruction] = useState("");
  const [refining, setRefining] = useState(false);
  const [refineError, setRefineError] = useState<string | null>(null);
  const [refineProgress, setRefineProgress] = useState<AIProgressEvent[]>([]);
  const [aiSummary, setAiSummary] = useState<string | null>(null);
  // Widgets the AI proposed but couldn't build — each carries the real reason
  // (e.g. the Trino execution error). Surfaced so a partial result is never
  // reported as a clean success.
  const [aiDropped, setAiDropped] = useState<DroppedWidget[]>([]);

  // Positions already persisted to the server, keyed by widget id. Lets us
  // persist ONLY the widgets a drag/resize actually moved instead of PUTing
  // every widget on every layout change (react-grid-layout fires
  // onLayoutChange on mount and mid-interaction too).
  const savedPositionsRef = useRef<Record<string, string>>({});

  const loadDashboard = useCallback(async () => {
    if (!dashboardId) return;
    try {
      setLoading(true);
      const dash = await dashboardsApi.get(dashboardId);
      setDashboard(dash);
      setDashboardName(dash.name);
      setWidgets(dash.widgets ?? []);

      // Build react-grid-layout from widget grid_positions
      const saved: Record<string, string> = {};
      const layouts: Layout[] = (dash.widgets ?? []).map((w) => {
        const pos = parseGridPos(w.grid_position);
        saved[String(w.id)] = JSON.stringify({ x: pos.x, y: pos.y, w: pos.w, h: pos.h });
        return {
          i: String(w.id),
          x: pos.x,
          y: pos.y,
          w: pos.w,
          h: pos.h,
        };
      });
      savedPositionsRef.current = saved;
      setLayout(layouts);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [dashboardId]);

  useEffect(() => {
    loadDashboard();
  }, [loadDashboard]);

  // Pick up the one-time AI result handed over from the generate flow, which
  // navigates here right after creating the dashboard. Without this, generated
  // dashboards silently lose any dropped-widget errors on navigation. Cleared
  // from history so a refresh doesn't resurrect a stale banner.
  useEffect(() => {
    const navState = location.state as
      | { aiSummary?: string; droppedWidgets?: DroppedWidget[] }
      | null;
    if (navState?.aiSummary || navState?.droppedWidgets?.length) {
      setAiSummary(navState.aiSummary ?? "Dashboard created.");
      setAiDropped(navState.droppedWidgets ?? []);
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location, navigate]);

  const parseGridPos = (raw: string) => {
    try {
      return JSON.parse(raw) as { x: number; y: number; w: number; h: number };
    } catch {
      return { x: 0, y: 0, w: 6, h: 4 };
    }
  };

  // Keep the controlled layout in sync with react-grid-layout. This fires on
  // mount and throughout an interaction, so it must NOT hit the network —
  // persistence happens once, on drag/resize STOP, in persistLayout().
  const handleLayoutChange = (newLayout: Layout[]) => {
    setLayout(newLayout);
  };

  // Persist only the widgets whose position actually changed, in parallel.
  const persistLayout = (newLayout: Layout[]) => {
    if (!dashboardId) return;
    const updates = newLayout
      .map((item) => {
        const gridPosition = JSON.stringify({ x: item.x, y: item.y, w: item.w, h: item.h });
        return { id: item.i, gridPosition };
      })
      .filter(({ id, gridPosition }) => savedPositionsRef.current[id] !== gridPosition);

    if (updates.length === 0) return;

    updates.forEach(({ id, gridPosition }) => {
      savedPositionsRef.current[id] = gridPosition;
      dashboardsApi
        .updateWidget(dashboardId, Number(id), { grid_position: gridPosition })
        .catch(() => {
          // non-critical; drop from the saved cache so a later change retries it
          delete savedPositionsRef.current[id];
        });
    });
  };

  const handleSaveName = async () => {
    if (!dashboardId || !dashboardName.trim()) return;
    setSavingLayout(true);
    try {
      await dashboardsApi.update(dashboardId, { name: dashboardName });
      setEditingName(false);
    } finally {
      setSavingLayout(false);
    }
  };

  const openAddWidget = () => {
    setEditingWidget(null);
    setWidgetForm(DEFAULT_WIDGET_FORM);
    setShowWidgetModal(true);
  };

  const openEditWidget = (w: DashboardWidget) => {
    setEditingWidget(w);
    setWidgetForm({
      title: w.title,
      query_sql: w.query_sql,
      chart_type: w.chart_type,
      refresh_rate_ms: w.refresh_rate_ms,
    });
    setShowWidgetModal(true);
  };

  const handleSaveWidget = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!dashboardId) return;
    setSavingWidget(true);
    try {
      if (editingWidget) {
        await dashboardsApi.updateWidget(dashboardId, editingWidget.id, {
          title: widgetForm.title,
          query_sql: widgetForm.query_sql,
          chart_type: widgetForm.chart_type,
          refresh_rate_ms: widgetForm.refresh_rate_ms,
        });
      } else {
        const newWidget: CreateWidgetPayload = {
          title: widgetForm.title,
          query_sql: widgetForm.query_sql,
          chart_type: widgetForm.chart_type,
          refresh_rate_ms: widgetForm.refresh_rate_ms,
          grid_position: JSON.stringify({
            x: 0,
            y: widgets.length * 4,
            w: 6,
            h: 4,
          }),
        };
        await dashboardsApi.createWidget(dashboardId, newWidget);
      }
      setShowWidgetModal(false);
      await loadDashboard();
    } finally {
      setSavingWidget(false);
    }
  };

  const handleRefine = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!dashboardId) return;
    setRefining(true);
    setRefineError(null);
    setRefineProgress([]);
    try {
      let result: AIDashboardResponse;

      // Preferred path: stream progress events while the AI edits the
      // dashboard. Falls back to the blocking endpoint if the stream
      // cannot be established.
      try {
        const terminal = await streamAIOperation(
          `/api/dashboards/${dashboardId}/refine/stream`,
          { instruction: refineInstruction },
          ["dashboard"],
          (type, data) =>
            setRefineProgress((prev) => [
              ...prev,
              { type, data: (data ?? {}) as AIProgressEventData, ts: Date.now() },
            ])
        );

        if (terminal.type === "error") {
          const data = terminal.data as { detail?: string } | undefined;
          setRefineError(data?.detail || "Refinement failed. Please try again.");
          return;
        }

        // Terminal `dashboard` event carries the same JSON as the
        // non-streaming response; tolerate a bare dashboard object too.
        const payload = terminal.data as AIDashboardResponse | Dashboard;
        result =
          (payload as AIDashboardResponse).dashboard != null
            ? (payload as AIDashboardResponse)
            : {
                dashboard: payload as Dashboard,
                explanation: "",
                confidence: 1,
                dropped_widgets: null,
              };
      } catch (err) {
        if (!(err instanceof SSEConnectionError)) throw err;
        // Stream endpoint unreachable — use the non-streaming API.
        result = await dashboardsApi.refine(dashboardId, refineInstruction);
      }

      setShowRefineModal(false);
      setRefineInstruction("");
      setAiSummary(result.explanation || "Dashboard updated.");
      setAiDropped(result.dropped_widgets ?? []);
      await loadDashboard();
    } catch (err) {
      const detail =
        (err as { response?: { data?: { error?: string; details?: string } } })
          .response?.data;
      setRefineError(
        detail?.details ||
          detail?.error ||
          (err as Error).message ||
          "Refinement failed. Please try again."
      );
    } finally {
      setRefining(false);
    }
  };

  const handleDeleteWidget = async (widgetId: number) => {
    if (!dashboardId || !confirm("Remove this widget?")) return;
    await dashboardsApi.deleteWidget(dashboardId, widgetId);
    await loadDashboard();
  };

  if (loading) {
    return (
      <div className="db-loading">
        <div className="spinner" />
        <span>Loading dashboard...</span>
      </div>
    );
  }

  if (!dashboard) {
    return (
      <div className="db-not-found">
        <h2>Dashboard not found</h2>
        <button className="btn btn-primary" onClick={() => navigate("/dashboards")}>
          ← Back to Dashboards
        </button>
      </div>
    );
  }

  return (
    <div className="db-builder">
      {/* Header */}
      <div className="db-builder-header">
        <button className="btn btn-ghost btn-sm" onClick={() => navigate("/dashboards")}>
          ← Dashboards
        </button>
        <div className="db-title-wrap">
          {editingName ? (
            <div className="db-title-edit">
              <input
                className="form-input db-name-input"
                value={dashboardName}
                onChange={(e) => setDashboardName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSaveName()}
                autoFocus
              />
              <button className="btn btn-primary btn-sm" onClick={handleSaveName} disabled={savingLayout}>
                Save
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => setEditingName(false)}>
                Cancel
              </button>
            </div>
          ) : (
            <h1 className="db-builder-title" onClick={() => setEditingName(true)} title="Click to rename">
              {dashboard.name}
              <span className="edit-icon">✏️</span>
            </h1>
          )}
          {dashboard.description && (
            <p className="db-builder-desc">{dashboard.description}</p>
          )}
        </div>
        <button className="btn btn-primary" onClick={() => { setRefineError(null); setShowRefineModal(true); }}>
          ✨ Refine with AI
        </button>
        <button className="btn btn-ghost" onClick={openAddWidget}>
          + Add Widget
        </button>
      </div>

      {(aiSummary || aiDropped.length > 0) && (
        <div className={`ai-summary-banner${aiDropped.length > 0 ? " ai-summary-banner-warning" : ""}`}>
          <div className="ai-summary-content">
            {aiSummary && (
              <span>{aiDropped.length > 0 ? "⚠️" : "✨"} {aiSummary}</span>
            )}
            {aiDropped.length > 0 && (
              <div className="ai-dropped-list">
                <div className="ai-dropped-title">
                  {aiDropped.length} widget{aiDropped.length !== 1 ? "s" : ""} couldn't be built —
                  here's why:
                </div>
                <ul>
                  {aiDropped.map((d, i) => (
                    <li key={i}>
                      <strong>{d.title}</strong> — {d.reason}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => {
              setAiSummary(null);
              setAiDropped([]);
            }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Canvas */}
      {widgets.length === 0 ? (
        <div className="db-empty-canvas">
          <div className="db-empty-icon">📊</div>
          <h3>Dashboard is empty</h3>
          <p>Add your first widget to start building your analytics view.</p>
          <button className="btn btn-primary" onClick={openAddWidget}>
            Add First Widget
          </button>
        </div>
      ) : (
        <div className="db-canvas">
          <GridLayout
            className="layout"
            layout={layout}
            cols={12}
            rowHeight={80}
            width={1200}
            onLayoutChange={handleLayoutChange}
            onDragStop={persistLayout}
            onResizeStop={persistLayout}
            draggableHandle=".widget-drag-handle"
            resizeHandles={["se"]}
          >
            {widgets.map((widget) => (
              <div key={String(widget.id)} className="db-widget-wrapper">
                <DashboardWidgetCard
                  widget={widget}
                  onEdit={() => openEditWidget(widget)}
                  onDelete={() => handleDeleteWidget(widget.id)}
                />
              </div>
            ))}
          </GridLayout>
        </div>
      )}

      {/* AI Refine Modal */}
      {showRefineModal && (
        <div className="modal-overlay" onClick={() => !refining && setShowRefineModal(false)}>
          <div className="modal-box modal-lg" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>✨ Refine Dashboard with AI</h2>
              <button className="modal-close" onClick={() => setShowRefineModal(false)} disabled={refining}>✕</button>
            </div>
            <form onSubmit={handleRefine} className="modal-form">
              <div className="form-group">
                <label>What should change? *</label>
                <textarea
                  className="form-input"
                  placeholder={
                    "e.g. Add a pie chart of employees by location, turn the salary chart " +
                    "into a line trend, and remove the tasks table."
                  }
                  value={refineInstruction}
                  onChange={(e) => setRefineInstruction(e.target.value)}
                  rows={4}
                  required
                  autoFocus
                  disabled={refining}
                />
              </div>
              <p className="form-hint">
                The AI edits this dashboard in place: widgets you don't mention stay as they are.
              </p>
              {refining && (
                <AIProgressTimeline
                  events={refineProgress}
                  active
                  title="Refining dashboard"
                />
              )}
              {refineError && <div className="form-error">{refineError}</div>}
              <div className="modal-footer">
                <button type="button" className="btn btn-ghost" onClick={() => setShowRefineModal(false)} disabled={refining}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={refining || !refineInstruction.trim()}>
                  {refining ? (
                    <><span className="spinner-sm" /> Applying changes…</>
                  ) : (
                    "Apply Changes"
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Widget Modal */}
      {showWidgetModal && (
        <div className="modal-overlay" onClick={() => setShowWidgetModal(false)}>
          <div className="modal-box modal-lg" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>{editingWidget ? "Edit Widget" : "Add Widget"}</h2>
              <button className="modal-close" onClick={() => setShowWidgetModal(false)}>✕</button>
            </div>

            <form onSubmit={handleSaveWidget} className="modal-form">
              <div className="form-group">
                <label>Widget Title *</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="e.g. Total Revenue by Region"
                  value={widgetForm.title}
                  onChange={(e) => setWidgetForm((f) => ({ ...f, title: e.target.value }))}
                  required
                />
              </div>

              <div className="form-group">
                <label>SQL Query *</label>
                <textarea
                  className="form-input code-textarea"
                  placeholder={`SELECT region, SUM(amount) as revenue\nFROM postgres_source.public.orders\nGROUP BY region\nORDER BY revenue DESC\nLIMIT 10`}
                  value={widgetForm.query_sql}
                  onChange={(e) => setWidgetForm((f) => ({ ...f, query_sql: e.target.value }))}
                  rows={6}
                  required
                />
              </div>

              <div className="form-group">
                <label>Chart Type</label>
                <div className="chart-type-grid">
                  {CHART_TYPES.map(({ type, label, icon }) => (
                    <button
                      key={type}
                      type="button"
                      className={`chart-type-btn ${widgetForm.chart_type === type ? "selected" : ""}`}
                      onClick={() => setWidgetForm((f) => ({ ...f, chart_type: type }))}
                    >
                      <span>{icon}</span>
                      <span>{label}</span>
                    </button>
                  ))}
                </div>
              </div>

              <div className="form-group">
                <label>Auto-Refresh</label>
                <select
                  className="form-input"
                  value={widgetForm.refresh_rate_ms}
                  onChange={(e) => setWidgetForm((f) => ({ ...f, refresh_rate_ms: Number(e.target.value) }))}
                >
                  <option value={0}>Manual only</option>
                  <option value={30000}>Every 30 seconds</option>
                  <option value={60000}>Every 1 minute</option>
                  <option value={300000}>Every 5 minutes</option>
                  <option value={600000}>Every 10 minutes</option>
                </select>
              </div>

              <div className="modal-footer">
                <button type="button" className="btn btn-ghost" onClick={() => setShowWidgetModal(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={savingWidget}>
                  {savingWidget ? "Saving..." : editingWidget ? "Update Widget" : "Add Widget"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
