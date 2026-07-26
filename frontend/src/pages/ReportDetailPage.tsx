import React, { useState, useEffect, useCallback } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import { Light as SyntaxHighlighter } from "react-syntax-highlighter";
import sql from "react-syntax-highlighter/dist/esm/languages/hljs/sql";
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs";
import { reportsApi } from "../api/client";
import { streamAIOperation, SSEConnectionError } from "../api/sse";
import type {
  Report,
  ReportSheet,
  CreateSheetPayload,
  AIReportResponse,
  AIProgressEvent,
  AIProgressEventData,
  DroppedSheet,
} from "../types";
import GenerationProgress from "../components/GenerationProgress";
import { Button, Modal, Banner, ConfirmDialog, SkeletonGrid } from "../components/ui";
import { useToast } from "../components/ui/Toast";
import { Icon } from "../components/ui/Icon";

SyntaxHighlighter.registerLanguage("sql", sql);

interface SheetFormState {
  title: string;
  description: string;
  query_sql: string;
  max_rows: number;
}

const DEFAULT_SHEET_FORM: SheetFormState = {
  title: "",
  description: "",
  query_sql: "",
  max_rows: 5000,
};

export default function ReportDetailPage() {
  const toast = useToast();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const location = useLocation();
  const reportId = id ? Number(id) : null;

  const [report, setReport] = useState<Report | null>(null);
  const [sheets, setSheets] = useState<ReportSheet[]>([]);
  const [loading, setLoading] = useState(true);
  const [showSheetModal, setShowSheetModal] = useState(false);
  const [sheetForm, setSheetForm] = useState<SheetFormState>(DEFAULT_SHEET_FORM);
  const [savingSheet, setSavingSheet] = useState(false);
  const [editingSheet, setEditingSheet] = useState<ReportSheet | null>(null);
  const [reportName, setReportName] = useState("");
  const [editingName, setEditingName] = useState(false);
  const [reportDesc, setReportDesc] = useState("");
  const [editingDesc, setEditingDesc] = useState(false);
  const [savingMeta, setSavingMeta] = useState(false);
  const [expandedSql, setExpandedSql] = useState<Set<number>>(new Set());
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [showRefineModal, setShowRefineModal] = useState(false);
  const [refineInstruction, setRefineInstruction] = useState("");
  const [refining, setRefining] = useState(false);
  const [refineError, setRefineError] = useState<string | null>(null);
  const [refineProgress, setRefineProgress] = useState<AIProgressEvent[]>([]);
  const [aiSummary, setAiSummary] = useState<string | null>(null);
  // Sheets the AI proposed but couldn't build — each carries the real reason
  // (e.g. the Trino execution error). Surfaced so a partial result is never
  // reported as a clean success.
  const [aiDropped, setAiDropped] = useState<DroppedSheet[]>([]);
  const [confirmDeleteSheet, setConfirmDeleteSheet] = useState<number | null>(null);
  const abortControllerRef = React.useRef<AbortController | null>(null);

  const loadReport = useCallback(async () => {
    if (!reportId) return;
    try {
      setLoading(true);
      const rep = await reportsApi.get(reportId);
      setReport(rep);
      setReportName(rep.name);
      setReportDesc(rep.description);
      // Sheets render in workbook order.
      setSheets(
        [...(rep.sheets ?? [])].sort(
          (a, b) => a.position - b.position || a.id - b.id
        )
      );
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [reportId]);

  useEffect(() => {
    loadReport();
  }, [loadReport]);

  // Pick up the one-time AI result handed over from the generate flow, which
  // navigates here right after creating the report. Without this, generated
  // reports silently lose any dropped-sheet errors on navigation. Cleared
  // from history so a refresh doesn't resurrect a stale banner.
  useEffect(() => {
    const navState = location.state as
      | { aiSummary?: string; droppedSheets?: DroppedSheet[] }
      | null;
    if (navState?.aiSummary || navState?.droppedSheets?.length) {
      setAiSummary(navState.aiSummary ?? "Report created.");
      setAiDropped(navState.droppedSheets ?? []);
      navigate(location.pathname, { replace: true, state: null });
    }
  }, [location, navigate]);

  const handleSaveName = async () => {
    if (!reportId || !reportName.trim()) return;
    setSavingMeta(true);
    try {
      await reportsApi.update(reportId, { name: reportName });
      setEditingName(false);
      await loadReport();
    } finally {
      setSavingMeta(false);
    }
  };

  const handleSaveDesc = async () => {
    if (!reportId) return;
    setSavingMeta(true);
    try {
      await reportsApi.update(reportId, { description: reportDesc });
      setEditingDesc(false);
      await loadReport();
    } finally {
      setSavingMeta(false);
    }
  };

  const handleDownload = async () => {
    if (!reportId) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      await reportsApi.download(reportId);
    } catch (err) {
      // In blob mode error bodies arrive as a Blob, so surface the generic
      // axios message rather than trying to parse them.
      setDownloadError(
        (err as Error).message || "Download failed. Please try again."
      );
    } finally {
      setDownloading(false);
    }
  };

  const toggleSql = (sheetId: number) => {
    setExpandedSql((prev) => {
      const next = new Set(prev);
      if (next.has(sheetId)) {
        next.delete(sheetId);
      } else {
        next.add(sheetId);
      }
      return next;
    });
  };

  const openAddSheet = () => {
    setEditingSheet(null);
    setSheetForm(DEFAULT_SHEET_FORM);
    setShowSheetModal(true);
  };

  const openEditSheet = (s: ReportSheet) => {
    setEditingSheet(s);
    setSheetForm({
      title: s.title,
      description: s.description,
      query_sql: s.query_sql,
      max_rows: s.max_rows,
    });
    setShowSheetModal(true);
  };

  const handleSaveSheet = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!reportId) return;
    setSavingSheet(true);
    try {
      if (editingSheet) {
        await reportsApi.updateSheet(reportId, editingSheet.id, {
          title: sheetForm.title,
          description: sheetForm.description,
          query_sql: sheetForm.query_sql,
          max_rows: sheetForm.max_rows,
        });
      } else {
        const newSheet: CreateSheetPayload = {
          title: sheetForm.title,
          description: sheetForm.description,
          query_sql: sheetForm.query_sql,
          max_rows: sheetForm.max_rows,
          position: sheets.length,
        };
        await reportsApi.createSheet(reportId, newSheet);
      }
      setShowSheetModal(false);
      await loadReport();
    } finally {
      setSavingSheet(false);
    }
  };

  const handleRefine = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!reportId) return;
    setRefining(true);
    setRefineError(null);
    setRefineProgress([]);
    try {
      let result: AIReportResponse;

      // Preferred path: stream progress events while the AI edits the
      // report. Falls back to the blocking endpoint if the stream
      // cannot be established.
      try {
        abortControllerRef.current = new AbortController();
        const terminal = await streamAIOperation(
          `/api/reports/${reportId}/refine/stream`,
          { instruction: refineInstruction },
          ["report"],
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

        // Terminal `report` event carries the same JSON as the
        // non-streaming response; tolerate a bare report object too.
        const payload = terminal.data as AIReportResponse | Report;
        result =
          (payload as AIReportResponse).report != null
            ? (payload as AIReportResponse)
            : {
                report: payload as Report,
                explanation: "",
                confidence: 1,
                dropped_sheets: null,
              };
      } catch (err) {
        if (!(err instanceof SSEConnectionError)) throw err;
        // Stream endpoint unreachable — use the non-streaming API.
        result = await reportsApi.refine(reportId, refineInstruction);
      }

      if (!refineError) {
        setShowRefineModal(false);
        setRefineInstruction("");
      }
      setAiSummary(result.explanation || "Report updated.");
      setAiDropped(result.dropped_sheets ?? []);
      await loadReport();
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

  const handleCancelRefine = () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
  };

  const handleDeleteSheet = async (sheetId: number) => {
    setConfirmDeleteSheet(sheetId);
  };

  const handleDeleteSheetConfirmed = async () => {
    if (!reportId || confirmDeleteSheet === null) return;
    try {
      await reportsApi.deleteSheet(reportId, confirmDeleteSheet);
      await loadReport();
      toast.success("Sheet removed");
    } catch {
      toast.error("Failed to remove sheet");
    } finally {
      setConfirmDeleteSheet(null);
    }
  };

  if (loading) {
    return (
      <div className="ds-page">
        <SkeletonGrid count={3} />
      </div>
    );
  }


  if (!report) {
    return (
      <div className="db-not-found">
        <h2>Report not found</h2>
        <Button variant="primary" onClick={() => navigate("/reports")}>
          <Icon name="arrow-right" size={14} className="icon-flip" /> Back to Reports
        </Button>
      </div>
    );
  }

  return (
    <div className="db-builder">
      {/* Header */}
      <div className="db-builder-header">
        <Button variant="ghost" size="sm" onClick={() => navigate("/reports")}>
          <Icon name="arrow-right" size={14} className="icon-flip" /> Reports
        </Button>
        <div className="db-title-wrap">
          {editingName ? (
            <div className="db-title-edit">
              <input
                className="form-input db-name-input"
                value={reportName}
                onChange={(e) => setReportName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSaveName()}
                autoFocus
              />
              <Button size="sm" variant="primary" onClick={handleSaveName} busy={savingMeta} busyLabel="Saving…">
                Save
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEditingName(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <h1 className="db-builder-title" onClick={() => setEditingName(true)} title="Click to rename">
              {report.name}
              <span className="edit-icon"><Icon name="edit" size={14} /></span>
            </h1>
          )}
          {editingDesc ? (
            <div className="db-title-edit">
              <input
                className="form-input"
                value={reportDesc}
                onChange={(e) => setReportDesc(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSaveDesc()}
                autoFocus
              />
              <Button size="sm" variant="primary" onClick={handleSaveDesc} busy={savingMeta} busyLabel="Saving…">
                Save
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setEditingDesc(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <p
              className="db-builder-desc rd-desc-edit"
              onClick={() => setEditingDesc(true)}
              title="Click to edit description"
            >
              {report.description || "Add a description"}
              <span className="edit-icon"><Icon name="edit" size={14} /></span>
            </p>
          )}
        </div>
        <Button variant="primary" onClick={handleDownload} busy={downloading} busyLabel="Preparing Excel…">
          <Icon name="download" size={14} /> Download Excel
        </Button>
        <Button variant="secondary" onClick={() => { setRefineError(null); setShowRefineModal(true); }}>
          <Icon name="sparkles" size={14} /> Refine with AI
        </Button>
        <Button variant="ghost" onClick={openAddSheet}>
          + Add Sheet
        </Button>
      </div>

      {downloadError && (
        <Banner kind="error" message={downloadError} onDismiss={() => setDownloadError(null)} />
      )}

      {(aiSummary || aiDropped.length > 0) && (
        <div className={`ai-summary-banner${aiDropped.length > 0 ? " ai-summary-banner-warning" : ""}`}>
          <div className="ai-summary-content">
            {aiSummary && (
              <span className="ai-summary-line"><Icon name={aiDropped.length > 0 ? "alert" : "sparkles"} size={14} />{aiSummary}</span>
            )}
            {aiDropped.length > 0 && (
              <div className="ai-dropped-list">
                <div className="ai-dropped-title">
                  {aiDropped.length} sheet{aiDropped.length !== 1 ? "s" : ""} couldn't be built —
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
            <Icon name="close" size={14} />
          </Button>
        </div>
      )}

      {/* Sheet list */}
      {sheets.length === 0 ? (
        <div className="db-empty-canvas">
          <div className="db-empty-icon"><Icon name="report" size={28} /></div>
          <h3>Report is empty</h3>
          <p>Add your first sheet to define what goes into the workbook.</p>
          <Button variant="primary" onClick={openAddSheet}>
            Add First Sheet
          </Button>
        </div>
      ) : (
        <div className="rd-sheets">
          {sheets.map((sheet, i) => (
            <div key={sheet.id} className="card rd-sheet-card">
              <div className="card-header">
                <div className="card-title">
                  <span className="stat-chip">Sheet {i + 1}</span>
                  <span className="section-title">{sheet.title}</span>
                </div>
                <div className="plan-stats">
                  <span className="stat-chip">max {sheet.max_rows.toLocaleString()} rows</span>
                  <Button variant="ghost" size="sm" onClick={() => openEditSheet(sheet)}>
                    Edit
                  </Button>
                  <Button variant="danger" size="sm" onClick={() => handleDeleteSheet(sheet.id)}>
                    Delete
                  </Button>
                </div>
              </div>
              <div className="card-body">
                {sheet.description && (
                  <p className="rd-sheet-desc">{sheet.description}</p>
                )}
                <button className="ds-schema-toggle" onClick={() => toggleSql(sheet.id)}>
                  <Icon name={expandedSql.has(sheet.id) ? "chevron-down" : "chevron-right"} size={12} />
                  <span>{expandedSql.has(sheet.id) ? "Hide SQL" : "Show SQL"}</span>
                </button>
                {expandedSql.has(sheet.id) && (
                  <div className="code-block rd-sheet-sql">
                    <SyntaxHighlighter
                      language="sql"
                      style={atomOneDark}
                      customStyle={{
                        background: "var(--bg-code)",
                        borderRadius: "8px",
                        padding: "16px",
                        margin: 0,
                        fontSize: "13px",
                        lineHeight: "1.6",
                      }}
                    >
                      {sheet.query_sql}
                    </SyntaxHighlighter>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal
        open={showRefineModal}
        onClose={() => !refining && setShowRefineModal(false)}
        persistent={refining}
        boxClass="modal-lg"
      >
        <div className="modal-header">
          <h2><Icon name="sparkles" size={16} /> Refine Report with AI</h2>
          <button className="modal-close" onClick={() => setShowRefineModal(false)} disabled={refining} aria-label="Close"><Icon name="close" size={16} /></button>
        </div>
        <form onSubmit={handleRefine} className="modal-form">
          <div className="form-group">
            <label>What should change? *</label>
            <textarea
              className="form-input"
              placeholder={
                "e.g. Add a sheet of contracts by kind, format the count columns, " +
                "and remove the detail sheet."
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
            The AI edits this report in place: sheets you don't mention stay as they are.
          </p>
          {refineProgress.length > 0 && (
            <GenerationProgress
              events={refineProgress}
              active={refining}
              mode="report"
              title="Refining report"
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

      <Modal
        open={showSheetModal}
        onClose={() => setShowSheetModal(false)}
        boxClass="modal-lg"
      >
        <div className="modal-header">
          <h2>{editingSheet ? "Edit Sheet" : "Add Sheet"}</h2>
          <button className="modal-close" onClick={() => setShowSheetModal(false)} aria-label="Close"><Icon name="close" size={16} /></button>
        </div>

        <form onSubmit={handleSaveSheet} className="modal-form">
          <div className="form-group">
            <label>Sheet Title *</label>
            <input
              type="text"
              className="form-input"
              placeholder="e.g. Contracts by Status"
              value={sheetForm.title}
              onChange={(e) => setSheetForm((f) => ({ ...f, title: e.target.value }))}
              required
            />
          </div>

          <div className="form-group">
            <label>Description</label>
            <input
              type="text"
              className="form-input"
              placeholder="Optional — shown on the workbook cover sheet"
              value={sheetForm.description}
              onChange={(e) => setSheetForm((f) => ({ ...f, description: e.target.value }))}
            />
          </div>

          <div className="form-group">
            <label>SQL Query *</label>
            <textarea
              className="form-input code-textarea"
              placeholder={`SELECT status AS "Status", COUNT(*) AS "Contracts"\nFROM elasticsearch.default."contracts-v2.40"\nGROUP BY status\nORDER BY "Contracts" DESC`}
              value={sheetForm.query_sql}
              onChange={(e) => setSheetForm((f) => ({ ...f, query_sql: e.target.value }))}
              rows={6}
              required
            />
          </div>

          <div className="form-group">
            <label>Max Rows</label>
            <input
              type="number"
              className="form-input"
              min={1}
              value={sheetForm.max_rows}
              onChange={(e) => setSheetForm((f) => ({ ...f, max_rows: Number(e.target.value) }))}
            />
          </div>

          <div className="modal-footer">
            <Button type="button" variant="ghost" onClick={() => setShowSheetModal(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              busy={savingSheet}
              busyLabel="Saving…"
            >
              {editingSheet ? "Update Sheet" : "Add Sheet"}
            </Button>
          </div>
        </form>
      </Modal>

      {/* Sheet Delete Confirm */}
      <ConfirmDialog
        open={confirmDeleteSheet !== null}
        title="Remove sheet?"
        message="Remove this sheet from the report? This cannot be undone."
        confirmLabel="Remove"
        onConfirm={handleDeleteSheetConfirmed}
        onCancel={() => setConfirmDeleteSheet(null)}
      />
    </div>
  );
}
