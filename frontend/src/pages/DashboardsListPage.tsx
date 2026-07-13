import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { dashboardsApi } from "../api/client";
import { streamAIOperation, SSEConnectionError } from "../api/sse";
import AIProgressTimeline from "../components/AIProgressTimeline";
import type {
  Dashboard,
  CreateDashboardPayload,
  AIDashboardResponse,
  AIProgressEvent,
  AIProgressEventData,
} from "../types";

export default function DashboardsListPage() {
  const navigate = useNavigate();
  const [dashboards, setDashboards] = useState<Dashboard[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [formName, setFormName] = useState("");
  const [formDesc, setFormDesc] = useState("");
  const [creating, setCreating] = useState(false);
  const [showAIModal, setShowAIModal] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [generating, setGenerating] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiProgress, setAiProgress] = useState<AIProgressEvent[]>([]);

  const loadDashboards = useCallback(async () => {
    try {
      setLoading(true);
      const data = await dashboardsApi.list();
      setDashboards(data ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadDashboards();
  }, [loadDashboards]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      const payload: CreateDashboardPayload = {
        name: formName,
        description: formDesc,
      };
      const created = await dashboardsApi.create(payload);
      navigate(`/dashboards/${created.id}`);
    } finally {
      setCreating(false);
    }
  };

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    setGenerating(true);
    setAiError(null);
    setAiProgress([]);
    try {
      // Preferred path: stream progress events while the AI designs the
      // dashboard. Falls back to the blocking endpoint if the stream
      // cannot be established.
      try {
        const terminal = await streamAIOperation(
          "/api/dashboards/generate/stream",
          { prompt: aiPrompt },
          ["dashboard"],
          (type, data) =>
            setAiProgress((prev) => [
              ...prev,
              { type, data: (data ?? {}) as AIProgressEventData, ts: Date.now() },
            ])
        );

        if (terminal.type === "error") {
          const data = terminal.data as { detail?: string } | undefined;
          setAiError(data?.detail || "Dashboard generation failed. Please try again.");
          return;
        }

        // Terminal `dashboard` event carries the same JSON as the
        // non-streaming response; tolerate a bare dashboard object too.
        const payload = terminal.data as
          | AIDashboardResponse
          | Dashboard
          | undefined;
        const dashboardId =
          (payload as AIDashboardResponse)?.dashboard?.id ??
          (payload as Dashboard)?.id;
        if (dashboardId) {
          navigate(`/dashboards/${dashboardId}`);
        } else {
          setAiError("Dashboard generation returned an unexpected response.");
        }
        return;
      } catch (err) {
        if (!(err instanceof SSEConnectionError)) throw err;
        // Stream endpoint unreachable — use the non-streaming API.
      }

      const result = await dashboardsApi.generate(aiPrompt);
      navigate(`/dashboards/${result.dashboard.id}`);
    } catch (err) {
      const detail =
        (err as { response?: { data?: { error?: string; details?: string } } })
          .response?.data;
      setAiError(
        detail?.details ||
          detail?.error ||
          (err as Error).message ||
          "Dashboard generation failed. Please try again."
      );
    } finally {
      setGenerating(false);
    }
  };

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`Delete dashboard "${name}"? This will remove all widgets.`)) return;
    await dashboardsApi.delete(id);
    await loadDashboards();
  };

  const getWidgetCount = (d: Dashboard) => d.widgets?.length ?? 0;

  return (
    <div className="dl-page">
      <div className="dl-header">
        <div>
          <h1 className="dl-title">Dashboards</h1>
          <p className="dl-subtitle">
            Build custom analytics views from your federated data.
          </p>
        </div>
        <div className="dl-header-actions">
          <button className="btn btn-primary" onClick={() => setShowAIModal(true)}>
            ✨ Generate with AI
          </button>
          <button className="btn btn-ghost" onClick={() => setShowModal(true)}>
            + New Dashboard
          </button>
        </div>
      </div>

      {loading ? (
        <div className="ds-loading">
          <div className="spinner" />
          <span>Loading dashboards...</span>
        </div>
      ) : dashboards.length === 0 ? (
        <div className="ds-empty">
          <div className="ds-empty-icon">📊</div>
          <h3>No dashboards yet</h3>
          <p>Create your first dashboard to start building custom analytics views.</p>
          <button className="btn btn-primary" onClick={() => setShowModal(true)}>
            Create Dashboard
          </button>
        </div>
      ) : (
        <div className="dl-grid">
          {dashboards.map((d) => (
            <div
              key={d.id}
              className="dl-card"
              onClick={() => navigate(`/dashboards/${d.id}`)}
            >
              <div className="dl-card-icon">📊</div>
              <div className="dl-card-body">
                <div className="dl-card-name">{d.name}</div>
                {d.description && (
                  <div className="dl-card-desc">{d.description}</div>
                )}
                <div className="dl-card-meta">
                  <span>{getWidgetCount(d)} widgets</span>
                  <span>Updated {new Date(d.updated_at).toLocaleDateString()}</span>
                </div>
              </div>
              <div className="dl-card-actions">
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/dashboards/${d.id}`);
                  }}
                >
                  Open →
                </button>
                <button
                  className="btn btn-danger btn-sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(d.id, d.name);
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showAIModal && (
        <div className="modal-overlay" onClick={() => !generating && setShowAIModal(false)}>
          <div className="modal-box modal-lg" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>✨ Generate Dashboard with AI</h2>
              <button className="modal-close" onClick={() => setShowAIModal(false)} disabled={generating}>✕</button>
            </div>
            <form onSubmit={handleGenerate} className="modal-form">
              <div className="form-group">
                <label>Describe the dashboard you want *</label>
                <textarea
                  className="form-input"
                  placeholder={
                    "e.g. A workforce overview: headcount and average salary KPIs, " +
                    "salary by department, task completion trends, and top performers."
                  }
                  value={aiPrompt}
                  onChange={(e) => setAiPrompt(e.target.value)}
                  rows={4}
                  required
                  autoFocus
                  disabled={generating}
                />
              </div>
              <p className="form-hint">
                The AI analyzes all registered data sources and schemas, designs the widgets,
                and writes the queries. You can rearrange everything or refine it with further
                instructions afterwards.
              </p>
              {generating && (
                <AIProgressTimeline
                  events={aiProgress}
                  active
                  title="Designing dashboard"
                />
              )}
              {aiError && <div className="form-error">{aiError}</div>}
              <div className="modal-footer">
                <button type="button" className="btn btn-ghost" onClick={() => setShowAIModal(false)} disabled={generating}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={generating || !aiPrompt.trim()}>
                  {generating ? (
                    <><span className="spinner-sm" /> Designing dashboard…</>
                  ) : (
                    "Generate Dashboard"
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>New Dashboard</h2>
              <button className="modal-close" onClick={() => setShowModal(false)}>✕</button>
            </div>
            <form onSubmit={handleCreate} className="modal-form">
              <div className="form-group">
                <label>Dashboard Name *</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="e.g. Sales Overview"
                  value={formName}
                  onChange={(e) => setFormName(e.target.value)}
                  required
                  autoFocus
                />
              </div>
              <div className="form-group">
                <label>Description</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="Optional description"
                  value={formDesc}
                  onChange={(e) => setFormDesc(e.target.value)}
                />
              </div>
              <div className="modal-footer">
                <button type="button" className="btn btn-ghost" onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={creating}>
                  {creating ? "Creating..." : "Create Dashboard"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
