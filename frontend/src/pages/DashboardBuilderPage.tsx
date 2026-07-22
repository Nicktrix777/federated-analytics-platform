import React, { useState, useEffect, useCallback, useRef, useMemo } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { Responsive, WidthProvider, Layout, Layouts } from "react-grid-layout";
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
import GenerationProgress from "../components/GenerationProgress";
import { Button, Modal, ConfirmDialog, SkeletonGrid } from "../components/ui";
import { useToast } from "../components/ui/Toast";

// WidthProvider measures the container so tiles fill the viewport (no dead
// space on the right); Responsive gives us breakpoint-aware columns.
const ResponsiveGridLayout = WidthProvider(Responsive);

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
  const toast = useToast();
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
  // Active RGL breakpoint. Only `lg` maps to the single persisted
  // grid_position — edits at md/sm/xs must NOT overwrite it.
  const [currentBreakpoint, setCurrentBreakpoint] = useState<string>("lg");
  // Toggles the canvas dot-grid while a tile is being dragged/resized.
  const [isInteracting, setIsInteracting] = useState(false);
  const [showRefineModal, setShowRefineModal] = useState(false);
  const [refineInstruction, setRefineInstruction] = useState("");
  const [refining, setRefining] = useState(false);
  const [confirmDeleteWidget, setConfirmDeleteWidget] = useState<number | null>(null);
  const [refineError, setRefineError] = useState<string | null>(null);
  const [refineProgress, setRefineProgress] = useState<AIProgressEvent[]>([]);
  const [aiSummary, setAiSummary] = useState<string | null>(null);
  // Widgets the AI proposed but couldn't build — each carries the real reason
  // (e.g. the Trino execution error). Surfaced so a partial result is never
  // reported as a clean success.
  const [aiDropped, setAiDropped] = useState<DroppedWidget[]>([]);
  const abortControllerRef = useRef<AbortController | null>(null);

  // Positions already persisted to the server, keyed by widget id. Lets us
  // persist ONLY the widgets a drag/resize actually moved instead of PUTing
  // every widget on every layout change (react-grid-layout fires
  // onLayoutChange on mount and mid-interaction too).
  const savedPositionsRef = useRef<Record<string, string>>({});

  const loadDashboard = useCallback(async (opts?: { soft?: boolean }) => {
    if (!dashboardId) return;
    try {
      // A soft reload refreshes widget/layout state without the full-page
      // skeleton — used after edit/refine so unaffected tiles keep their data
      // (each card only re-queries when its own query_sql changes).
      if (!opts?.soft) setLoading(true);
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
      if (!opts?.soft) setLoading(false);
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

  // Stable per-breakpoint layouts object for the controlled Responsive grid.
  // Memoized so a same-content render doesn't hand RGL a new reference.
  const gridLayouts = useMemo<Layouts>(() => ({ lg: layout }), [layout]);

  // Place a new manual widget below everything else instead of the old
  // fixed {x:0, y:len*4} — kills the left-column pile-up and never overlaps.
  const nextWidgetPosition = () => {
    const maxBottom = layout.reduce((m, it) => Math.max(m, it.y + it.h), 0);
    return { x: 0, y: maxBottom, w: 6, h: 4 };
  };

  // Keep the controlled layout in sync with react-grid-layout. Responsive
  // passes (currentLayout, allLayouts); we only track the `lg` layout as the
  // source of truth, so md/sm/xs interactions never mutate it. Fires on mount
  // and throughout an interaction, so it must NOT hit the network —
  // persistence happens once, on drag/resize STOP, in persistLayout().
  const handleLayoutChange = (_current: Layout[], allLayouts: Layouts) => {
    if (allLayouts.lg) setLayout(allLayouts.lg);
  };

  // Persist only the widgets whose position actually changed, in parallel.
  // Gated to the `lg` breakpoint: the schema stores a single grid_position, so
  // writing md/sm/xs coordinates would corrupt the saved (lg) layout.
  const persistLayout = (newLayout: Layout[]) => {
    if (!dashboardId) return;
    if (currentBreakpoint !== "lg") return;
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
          grid_position: JSON.stringify(nextWidgetPosition()),
        };
        await dashboardsApi.createWidget(dashboardId, newWidget);
      }
      setShowWidgetModal(false);
      // Soft reload — only the created/edited tile re-queries; the rest keep
      // their rendered data (no full-page skeleton flash).
      await loadDashboard({ soft: true });
      toast.success(editingWidget ? "Widget updated" : "Widget added");
    } catch {
      toast.error("Failed to save widget");
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
        abortControllerRef.current = new AbortController();
        const terminal = await streamAIOperation(
          `/api/dashboards/${dashboardId}/refine/stream`,
          { instruction: refineInstruction },
          ["dashboard"],
          (type, data) =>
            setRefineProgress((prev) => [
              ...prev,
              { type, data: (data ?? {}) as AIProgressEventData, ts: Date.now() },
            ]),
          abortControllerRef.current.signal
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

      if (!refineError) {
        setShowRefineModal(false);
        setRefineInstruction("");
      }
      setAiSummary(result.explanation || "Dashboard updated.");
      setAiDropped(result.dropped_widgets ?? []);
      await loadDashboard({ soft: true });
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setRefineError("Refinement cancelled.");
        return;
      }
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
    setConfirmDeleteWidget(widgetId);
  };
  const handleDeleteWidgetConfirmed = async () => {
    if (!dashboardId || confirmDeleteWidget === null) return;
    const widgetId = confirmDeleteWidget;
    try {
      await dashboardsApi.deleteWidget(dashboardId, widgetId);
      // Surgical state update — no whole-page reload, so surviving tiles never
      // re-run their queries.
      setWidgets((prev) => prev.filter((w) => w.id !== widgetId));
      setLayout((prev) => prev.filter((l) => l.i !== String(widgetId)));
      delete savedPositionsRef.current[String(widgetId)];
      toast.success("Widget removed");
    } catch {
      toast.error("Failed to remove widget");
    } finally {
      setConfirmDeleteWidget(null);
    }
  };


  const handleCancelRefine = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  };

  if (loading) {
    return (
      <div className="db-builder">
        <div className="db-canvas" style={{ padding: '60px' }}>
          <SkeletonGrid count={3} />
        </div>
      </div>
    );
  }

  if (!dashboard) {
    return (
      <div className="db-not-found">
        <h2>Dashboard not found</h2>
        <Button variant="primary" onClick={() => navigate("/dashboards")}>
          ← Back to Dashboards
        </Button>
      </div>
    );
  }

  return (
    <div className="db-builder">
      {/* Header */}
      <div className="db-builder-header">
        <Button variant="ghost" size="sm" onClick={() => navigate("/dashboards")}>
          ← Dashboards
        </Button>
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
              <Button size="sm" variant="primary" onClick={handleSaveName} busy={savingLayout} busyLabel="Saving…">
                Save
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEditingName(false)}>
                Cancel
              </Button>
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
        <Button variant="secondary" onClick={() => { setRefineError(null); setShowRefineModal(true); }}>
          ✨ Refine with AI
        </Button>
        <Button variant="ghost" onClick={openAddWidget}>
          + Add Widget
        </Button>
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
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setAiSummary(null);
              setAiDropped([]);
            }}
          >
            ✕
          </Button>
        </div>
      )}

      {/* Canvas */}
      {widgets.length === 0 ? (
        <div className="db-empty-canvas">
          <div className="db-empty-icon">📊</div>
          <h3>Dashboard is empty</h3>
          <p>Add your first widget to start building your analytics view.</p>
          <Button variant="primary" onClick={openAddWidget}>
            Add First Widget
          </Button>
        </div>
      ) : (
        <div className={`db-canvas${isInteracting ? " is-interacting" : ""}`}>
          <ResponsiveGridLayout
            className="layout"
            layouts={gridLayouts}
            breakpoints={{ lg: 1200, md: 996, sm: 768, xs: 480 }}
            cols={{ lg: 12, md: 12, sm: 6, xs: 2 }}
            rowHeight={90}
            margin={[16, 16]}
            onLayoutChange={handleLayoutChange}
            onBreakpointChange={(bp: string) => setCurrentBreakpoint(bp)}
            onDragStart={() => setIsInteracting(true)}
            onResizeStart={() => setIsInteracting(true)}
            onDragStop={(l: Layout[]) => { setIsInteracting(false); persistLayout(l); }}
            onResizeStop={(l: Layout[]) => { setIsInteracting(false); persistLayout(l); }}
            isDraggable
            isResizable
            draggableCancel=".widget-actions,.widget-btn,.react-resizable-handle,.widget-table-wrap"
            resizeHandles={["se", "e", "s"]}
          >
            {widgets.map((widget, index) => (
              <div 
                key={String(widget.id)} 
                className="db-widget-wrapper"
                style={{ animationDelay: `${index * 30}ms` }}
              >
                <DashboardWidgetCard
                  widget={widget}
                  onEdit={() => openEditWidget(widget)}
                  onDelete={() => handleDeleteWidget(widget.id)}
                />
              </div>
            ))}
          </ResponsiveGridLayout>
        </div>
      )}

      {/* AI Refine Modal */}
      <Modal
        open={showRefineModal}
        onClose={() => !refining && setShowRefineModal(false)}
        persistent={refining}
        boxClass="modal-lg"
      >
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
                "e.g. Add a pie chart of contracts by status, turn the monthly chart " +
                "into a line trend, and remove the detail table."
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
          {refineProgress.length > 0 && (
            <GenerationProgress
              events={refineProgress}
              active={refining}
              mode="dashboard"
              title="Refining dashboard"
              onCancel={handleCancelRefine}
            />
          )}
          {refineError && <div className="form-error">{refineError}</div>}
          <div className="modal-footer">
            <Button type="button" variant="ghost" onClick={() => setShowRefineModal(false)} disabled={refining}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              busy={refining}
              busyLabel="Applying changes…"
              disabled={refining || !refineInstruction.trim()}
            >
              Apply Changes
            </Button>
          </div>
        </form>
      </Modal>

      {/* Widget Modal */}
      <Modal
        open={showWidgetModal}
        onClose={() => setShowWidgetModal(false)}
        boxClass="modal-lg"
      >
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
                  placeholder="e.g. Contracts by Status"
                  value={widgetForm.title}
                  onChange={(e) => setWidgetForm((f) => ({ ...f, title: e.target.value }))}
                  required
                />
              </div>

              <div className="form-group">
                <label>SQL Query *</label>
                <textarea
                  className="form-input code-textarea"
                  placeholder={`SELECT status, COUNT(*) AS contracts\nFROM elasticsearch.default."contracts-v2.40"\nGROUP BY status\nORDER BY contracts DESC\nLIMIT 10`}
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
                <Button type="button" variant="ghost" onClick={() => setShowWidgetModal(false)}>
                  Cancel
                </Button>
                <Button
                  type="submit"
                  variant="primary"
                  busy={savingWidget}
                  busyLabel="Saving…"
                >
                  {editingWidget ? "Update Widget" : "Add Widget"}
                </Button>
              </div>
            </form>
          </Modal>

      {/* Widget Delete Confirm */}
      <ConfirmDialog
        open={confirmDeleteWidget !== null}
        title="Remove widget?"
        message="Remove this widget from the dashboard? This cannot be undone."
        confirmLabel="Remove"
        onConfirm={handleDeleteWidgetConfirmed}
        onCancel={() => setConfirmDeleteWidget(null)}
      />
    </div>
  );
}
