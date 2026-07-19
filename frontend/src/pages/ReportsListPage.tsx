import React, { useState, useEffect, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { reportsApi } from "../api/client";
import { streamAIOperation, SSEConnectionError } from "../api/sse";
import AIProgressTimeline from "../components/AIProgressTimeline";
import type {
  Report,
  CreateReportPayload,
  AIReportResponse,
  AIProgressEvent,
  AIProgressEventData,
} from "../types";

export default function ReportsListPage() {
  const navigate = useNavigate();
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
  const [downloadingId, setDownloadingId] = useState<number | null>(null);
  const [downloadError, setDownloadError] = useState<string | null>(null);

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
        const terminal = await streamAIOperation(
          "/api/reports/generate/stream",
          { prompt: aiPrompt },
          ["report"],
          (type, data) =>
            setAiProgress((prev) => [
              ...prev,
              { type, data: (data ?? {}) as AIProgressEventData, ts: Date.now() },
            ])
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

  const handleDelete = async (id: number, name: string) => {
    if (!confirm(`Delete report "${name}"? This will remove all sheets.`)) return;
    await reportsApi.delete(id);
    await loadReports();
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
          <button className="btn btn-primary" onClick={() => setShowAIModal(true)}>
            ✨ Generate with AI
          </button>
          <button className="btn btn-ghost" onClick={() => setShowModal(true)}>
            + New Report
          </button>
        </div>
      </div>

      {downloadError && (
        <div className="ds-error">
          <span>⚠️ {downloadError}</span>
          <button onClick={() => setDownloadError(null)}>✕</button>
        </div>
      )}

      {loading ? (
        <div className="ds-loading">
          <div className="spinner" />
          <span>Loading reports...</span>
        </div>
      ) : reports.length === 0 ? (
        <div className="ds-empty">
          <div className="ds-empty-icon">📑</div>
          <h3>No reports yet</h3>
          <p>Create your first report to start delivering formatted Excel workbooks.</p>
          <button className="btn btn-primary" onClick={() => setShowModal(true)}>
            Create Report
          </button>
        </div>
      ) : (
        <div className="dl-grid">
          {reports.map((r) => (
            <div
              key={r.id}
              className="dl-card"
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
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    navigate(`/reports/${r.id}`);
                  }}
                >
                  Open →
                </button>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={downloadingId === r.id}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDownload(r.id);
                  }}
                >
                  {downloadingId === r.id ? (
                    <><span className="spinner-sm" /> Preparing…</>
                  ) : (
                    "⬇ Download"
                  )}
                </button>
                <button
                  className="btn btn-danger btn-sm"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(r.id, r.name);
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
              <h2>✨ Generate Report with AI</h2>
              <button className="modal-close" onClick={() => setShowAIModal(false)} disabled={generating}>✕</button>
            </div>
            <form onSubmit={handleGenerate} className="modal-form">
              <div className="form-group">
                <label>Describe the report you want *</label>
                <textarea
                  className="form-input"
                  placeholder={
                    "e.g. A monthly workforce report: a summary sheet with headcount " +
                    "and salary KPIs, salary detail by department, and task completion " +
                    "per employee."
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
              {generating && (
                <AIProgressTimeline
                  events={aiProgress}
                  active
                  title="Designing report"
                />
              )}
              {aiError && <div className="form-error">{aiError}</div>}
              <div className="modal-footer">
                <button type="button" className="btn btn-ghost" onClick={() => setShowAIModal(false)} disabled={generating}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={generating || !aiPrompt.trim()}>
                  {generating ? (
                    <><span className="spinner-sm" /> Designing report…</>
                  ) : (
                    "Generate Report"
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
                <button type="button" className="btn btn-ghost" onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn btn-primary" disabled={creating}>
                  {creating ? "Creating..." : "Create Report"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
