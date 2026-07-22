import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { reportsApi } from "../api/client";
import { streamAIOperation, SSEConnectionError } from "../api/sse";
import GenerationProgress from "../components/GenerationProgress";
import { Button, Modal, Banner, EmptyState, SkeletonGrid, ConfirmDialog } from "../components/ui";
import { useToast } from "../components/ui/Toast";
import type {
  Report,
  CreateReportPayload,
  AIReportResponse,
  AIProgressEvent,
  AIProgressEventData,
} from "../types";

export default function ReportsListPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [reports, setReports] = useState<Report[]>([]);
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
  const [downloadingId, setDownloadingId] = useState<number | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  // ConfirmDialog state
  const [confirmDelete, setConfirmDelete] = useState<{ id: number; name: string } | null>(null);

  const loadReports = useCallback(async () => {
    try {
      setLoading(true);
      const data = await reportsApi.list();
      setReports(data ?? []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadReports();
  }, [loadReports]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreating(true);
    try {
      const payload: CreateReportPayload = {
        name: formName,
        description: formDesc,
      };
      const created = await reportsApi.create(payload);
      navigate(`/reports/${created.id}`);
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
      // report. Falls back to the blocking endpoint if the stream
      // cannot be established.
      try {
        abortControllerRef.current = new AbortController();
        const terminal = await streamAIOperation(
          "/api/reports/generate/stream",
          { prompt: aiPrompt },
          ["report"],
          (type, data) =>
            setAiProgress((prev) => [
              ...prev,
              { type, data: (data ?? {}) as AIProgressEventData, ts: Date.now() },
            ]),
          abortControllerRef.current.signal
        );

        if (terminal.type === "error") {
          const data = terminal.data as { detail?: string } | undefined;
          setAiError(data?.detail || "Report generation failed. Please try again.");
          return;
        }

        // Terminal `report` event carries the same JSON as the
        // non-streaming response; tolerate a bare report object too.
        const payload = terminal.data as
          | AIReportResponse
          | Report
          | undefined;
        const aiResp = payload as AIReportResponse;
        const reportId = aiResp?.report?.id ?? (payload as Report)?.id;
        if (reportId) {
          // Carry the AI summary + dropped-sheet errors to the detail page so
          // a partial result isn't presented as a clean success.
          navigate(`/reports/${reportId}`, {
            state: {
              aiSummary: aiResp?.explanation,
              droppedSheets: aiResp?.dropped_sheets ?? undefined,
            },
          });
        } else {
          setAiError("Report generation returned an unexpected response.");
        }
        return;
      } catch (err) {
        if (!(err instanceof SSEConnectionError)) throw err;
        // Stream endpoint unreachable — use the non-streaming API.
      }

      const result = await reportsApi.generate(aiPrompt);
      navigate(`/reports/${result.report.id}`, {
        state: {
          aiSummary: result.explanation,
          droppedSheets: result.dropped_sheets ?? undefined,
        },
      });
    } catch (err) {
      if ((err as Error).name === "AbortError") {
        setAiError("Report generation cancelled.");
        return;
      }
      const detail =
        (err as { response?: { data?: { error?: string; details?: string } } })
          .response?.data;
      setAiError(
        detail?.details ||
          detail?.error ||
          (err as Error).message ||
          "Report generation failed. Please try again."
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

  const handleDownload = async (id: number) => {
    setDownloadingId(id);
    setDownloadError(null);
    try {
      await reportsApi.download(id);
    } catch (err) {
      // In blob mode error bodies arrive as a Blob, so surface the generic
      // axios message rather than trying to parse them.
      setDownloadError(
        (err as Error).message || "Download failed. Please try again."
      );
    } finally {
      setDownloadingId(null);
    }
  };

  const handleDeleteConfirmed = async () => {
    if (!confirmDelete) return;
    try {
      await reportsApi.delete(confirmDelete.id);
      await loadReports();
      toast.success(`"${confirmDelete.name}" deleted`);
    } catch {
      toast.error("Failed to delete report");
    } finally {
      setConfirmDelete(null);
    }
  };

  return (
    <div className="dl-page">
      <div className="dl-header">
        <div>
          <h1 className="dl-title">Reports</h1>
          <p className="dl-subtitle">
            On-demand formatted Excel reports from your federated data.
          </p>
        </div>
        <div className="dl-header-actions">
          <Button variant="primary" onClick={() => setShowAIModal(true)}>
            ✨ Generate with AI
          </Button>
          <Button variant="ghost" onClick={() => setShowModal(true)}>
            + New Report
          </Button>
        </div>
      </div>

      {downloadError && (
        <Banner
          kind="error"
          message={downloadError}
          onDismiss={() => setDownloadError(null)}
        />
      )}

      {loading ? (
        <SkeletonGrid count={4} />
      ) : reports.length === 0 ? (
        <EmptyState
          icon="📑"
          title="No reports yet"
          description="Create your first report to start delivering formatted Excel workbooks."
          action={{ label: "Create Report", onClick: () => setShowModal(true) }}
        />
      ) : (
        <div className="dl-grid">
          {reports.map((r, index) => (
            <div
              key={r.id}
              className="dl-card"
              style={{ animationDelay: `${index * 30}ms` }}
              onClick={() => navigate(`/reports/${r.id}`)}
            >
              <div className="dl-card-icon">📑</div>
              <div className="dl-card-body">
                <div className="dl-card-name">{r.name}</div>
                {r.description && (
                  <div className="dl-card-desc">{r.description}</div>
                )}
                <div className="dl-card-meta">
                  <span>Updated {new Date(r.updated_at).toLocaleDateString()}</span>
                </div>
              </div>
              <div className="dl-card-actions">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={(e: React.MouseEvent) => {
                    e.stopPropagation();
                    navigate(`/reports/${r.id}`);
                  }}
                >
                  Open →
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  busy={downloadingId === r.id}
                  busyLabel="Preparing…"
                  onClick={(e: React.MouseEvent) => {
                    e.stopPropagation();
                    handleDownload(r.id);
                  }}
                >
                  ⬇ Download
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={(e: React.MouseEvent) => {
                    e.stopPropagation();
                    setConfirmDelete({ id: r.id, name: r.name });
                  }}
                >
                  Delete
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── Generate AI Report Modal ───────────────────────────── */}
      <Modal
        open={showAIModal}
        onClose={() => !generating && setShowAIModal(false)}
        persistent={generating}
        boxClass="modal-lg"
      >
        <div className="modal-header">
          <h2>✨ Generate Report with AI</h2>
          <button className="modal-close" onClick={() => setShowAIModal(false)} disabled={generating}>✕</button>
        </div>
        <form onSubmit={handleGenerate} className="modal-form">
          <div className="form-group">
            <label>Describe the report you want *</label>
            <textarea
              className="form-input"
              placeholder={
                "e.g. A monthly contracts report from the Elasticsearch contracts " +
                "index: a summary sheet of contract counts by status, contracts by " +
                "kind, and a detail sheet of recent contracts with document number."
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
            The AI analyzes all registered data sources and schemas, designs the sheets,
            and writes the queries. You can edit every sheet or refine the report with
            further instructions afterwards.
          </p>
          {aiProgress.length > 0 && (
            <GenerationProgress
              events={aiProgress}
              active={generating}
              mode="report"
              title="Designing report"
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
              busyLabel="Designing report…"
              disabled={generating || !aiPrompt.trim()}
            >
              Generate Report
            </Button>
          </div>
        </form>
      </Modal>

      {/* ── New Report Modal ───────────────────────────────────── */}
      <Modal open={showModal} onClose={() => setShowModal(false)}>
        <div className="modal-header">
          <h2>New Report</h2>
          <button className="modal-close" onClick={() => setShowModal(false)}>✕</button>
        </div>
        <form onSubmit={handleCreate} className="modal-form">
          <div className="form-group">
            <label>Report Name *</label>
            <input
              type="text"
              className="form-input"
              placeholder="e.g. Monthly Revenue Report"
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
              Create Report
            </Button>
          </div>
        </form>
      </Modal>

      {/* ── Delete Confirm ─────────────────────────────────────── */}
      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete report?"
        message={`Delete "${confirmDelete?.name ?? ""}"? This will remove all sheets.`}
        confirmLabel="Delete"
        onConfirm={handleDeleteConfirmed}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  );
}
