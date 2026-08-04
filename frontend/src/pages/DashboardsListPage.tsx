import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { dashboardsApi } from "../api/client";
import { streamAIOperation, SSEConnectionError } from "../api/sse";
import GenerationProgress from "../components/GenerationProgress";
import { Button, Modal, EmptyState, SkeletonGrid, ConfirmDialog } from "../components/ui";
import { useToast } from "../components/ui/Toast";
import { Icon } from "../components/ui/Icon";
import type {
  Dashboard,
  CreateDashboardPayload,
  AIDashboardResponse,
  AIProgressEvent,
  AIProgressEventData,
} from "../types";

export default function DashboardsListPage() {
  const navigate = useNavigate();
  const toast = useToast();
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
  const abortControllerRef = React.useRef<AbortController | null>(null);
  // ConfirmDialog state
  const [confirmDelete, setConfirmDelete] = useState<{ id: number; name: string } | null>(null);

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
        abortControllerRef.current = new AbortController();
        const terminal = await streamAIOperation(
          "/api/dashboards/generate/stream",
          { prompt: aiPrompt },
          ["dashboard"],
          (type, data) =>
            setAiProgress((prev) => [
              ...prev,
              { type, data: (data ?? {}) as AIProgressEventData, ts: Date.now() },
            ]),
          abortControllerRef.current.signal
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
        const aiResp = payload as AIDashboardResponse;
        const dashboardId = aiResp?.dashboard?.id ?? (payload as Dashboard)?.id;
        if (dashboardId) {
          // Carry the AI summary + dropped-widget errors to the builder so a
          // partial result isn't presented as a clean success.
          navigate(`/dashboards/${dashboardId}`, {
            state: {
              aiSummary: aiResp?.explanation,
              droppedWidgets: aiResp?.dropped_widgets ?? undefined,
            },
          });
        } else {
          setAiError("Dashboard generation returned an unexpected response.");
        }
        return;
      } catch (err) {
        if (!(err instanceof SSEConnectionError)) throw err;
        // Stream endpoint unreachable — use the non-streaming API.
      }

      const result = await dashboardsApi.generate(aiPrompt);
      navigate(`/dashboards/${result.dashboard.id}`, {
        state: {
          aiSummary: result.explanation,
          droppedWidgets: result.dropped_widgets ?? undefined,
        },
      });
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setAiError("Dashboard generation cancelled.");
        return;
      }
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

  const handleCancelGenerate = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  };

  const handleDeleteConfirmed = async () => {
    if (!confirmDelete) return;
    try {
      await dashboardsApi.delete(confirmDelete.id);
      await loadDashboards();
      toast.success(`"${confirmDelete.name}" deleted`);
    } catch {
      toast.error("Failed to delete dashboard");
    } finally {
      setConfirmDelete(null);
    }
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
          <Button variant="primary" onClick={() => setShowAIModal(true)}>
            <Icon name="sparkles" size={14} /> Generate with AI
          </Button>
          <Button variant="ghost" onClick={() => setShowModal(true)}>
            + New Dashboard
          </Button>
        </div>
      </div>

      {loading ? (
        <SkeletonGrid count={4} />
      ) : dashboards.length === 0 ? (
        <EmptyState
          icon={<Icon name="dashboard" size={26} />}
          title="No dashboards yet"
          description="Create your first dashboard to start building custom analytics views."
          action={{ label: "Create Dashboard", onClick: () => setShowModal(true) }}
        />
      ) : (
        <div className="dl-grid">
          {dashboards.map((d, index) => (
            <div
              key={d.id}
              className="dl-card"
              style={{ animationDelay: `${index * 30}ms` }}
              onClick={() => navigate(`/dashboards/${d.id}`)}
            >
              <div className="dl-card-icon">
                <Icon name="dashboard" size={18} />
              </div>
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
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/dashboards/${d.id}`);
                  }}
                >
                  Open <Icon name="arrow-right" size={13} />
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    setConfirmDelete({ id: d.id, name: d.name });
                  }}
                >
                  Delete
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Generate AI Dashboard Modal ────────────────────────── */}
      <Modal
        open={showAIModal}
        onClose={() => !generating && setShowAIModal(false)}
        persistent={generating}
        boxClass="modal-lg"
      >
        <div className="modal-header">
          <h2><Icon name="sparkles" size={16} /> Generate Dashboard with AI</h2>
          <button className="modal-close" onClick={() => setShowAIModal(false)} disabled={generating} aria-label="Close"><Icon name="close" size={16} /></button>
        </div>
        <form onSubmit={handleGenerate} className="modal-form">
          <div className="form-group">
            <label>Describe the dashboard you want *</label>
            <textarea
              className="form-input"
              placeholder={
                "e.g. A contracts overview from the Elasticsearch contracts index: " +
                "total contracts and active-contract KPIs, contracts by status, " +
                "contracts by kind, and contracts created per month."
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
          {aiProgress.length > 0 && (
            <GenerationProgress
              events={aiProgress}
              active={generating}
              mode="dashboard"
              title="Designing dashboard"
              onCancel={handleCancelGenerate}
            />
          )}
          {aiError && <div className="form-error">{aiError}</div>}
          <div className="modal-footer">
            <Button variant="ghost" onClick={() => setShowAIModal(false)} disabled={generating}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              busy={generating}
              busyLabel="Designing dashboard…"
              disabled={generating || !aiPrompt.trim()}
            >
              Generate Dashboard
            </Button>
          </div>
        </form>
      </Modal>

      {/* ── New Dashboard Modal ────────────────────────────────── */}
      <Modal open={showModal} onClose={() => setShowModal(false)} boxClass="">
        <div className="modal-header">
          <h2>New Dashboard</h2>
          <button className="modal-close" onClick={() => setShowModal(false)} aria-label="Close"><Icon name="close" size={16} /></button>
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
            <Button variant="ghost" onClick={() => setShowModal(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" busy={creating} busyLabel="Creating…">
              Create Dashboard
            </Button>
          </div>
        </form>
      </Modal>

      {/* ── Delete Confirm ─────────────────────────────────────── */}
      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete dashboard?"
        message={`Delete "${confirmDelete?.name ?? ""}"? This will remove all widgets.`}
        confirmLabel="Delete"
        onConfirm={handleDeleteConfirmed}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  );
}
