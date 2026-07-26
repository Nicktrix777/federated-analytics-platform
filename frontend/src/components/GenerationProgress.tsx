import { useEffect, useState } from "react";
import type { AIProgressEvent } from "../types";
import AIProgressTimeline from "./AIProgressTimeline";
import { Icon } from "./ui/Icon";

interface GenerationProgressProps {
  events: AIProgressEvent[];
  active: boolean;
  mode: "dashboard" | "report" | "query";
  title?: string;
  onCancel?: () => void;
}

interface StageDef {
  label: string;
  subLabel?: string;
}

interface StageSignals {
  defs: StageDef[];
  /** Furthest 1-based stage the real events indicate we've reached. */
  target: number;
  hasError: boolean;
}

// Minimum time each stage stays visible so a burst of events (the fast path
// finishes planning, checks the SQL, and starts executing within ~200ms) still
// ticks through the stages ONE BY ONE instead of flipping them all to done at once.
const DWELL_EVENT = 480; // catching up to where real events already are
const DWELL_IDLE = 1100; // creeping forward before any events arrive (blocking path)
const DWELL_FINISH = 320; // marching the last stages to done once the run completes

export default function GenerationProgress({
  events,
  active,
  mode,
  title,
  onCancel,
}: GenerationProgressProps) {
  const [showTechnical, setShowTechnical] = useState(false);

  const sig: StageSignals =
    mode === "query" ? querySignals(events) : designSignals(events, mode);
  const N = sig.defs.length;
  const hasEvents = events.length > 0;
  const finished = !active;

  // The indicator position advances toward `target`, but never faster than the
  // dwell time — so stages you'd otherwise never see (because their events
  // clustered) get a visible moment. It starts at 1 the instant we mount, so a
  // streaming query shows "Understanding your question" immediately rather than
  // just a spinning button.
  const [display, setDisplay] = useState(1);

  let target: number;
  if (finished && sig.hasError) target = Math.max(1, sig.target || 1);
  else if (finished) target = N;
  else if (hasEvents) target = Math.min(sig.target, sig.target >= N ? N : N - 1);
  else target = N - 1; // no events yet — creep forward, but hold before the last stage

  useEffect(() => {
    if (display >= target) return;
    const dwell = finished ? DWELL_FINISH : hasEvents ? DWELL_EVENT : DWELL_IDLE;
    const id = setTimeout(() => setDisplay((d) => Math.min(d + 1, target)), dwell);
    return () => clearTimeout(id);
  }, [display, target, finished, hasEvents]);

  const allDone = finished && !sig.hasError && display >= N;

  const stages = sig.defs.map((def, i) => {
    const idx = i + 1;
    let state: "pending" | "active" | "done" | "error";
    if (sig.hasError && finished) {
      state = idx < display ? "done" : idx === display ? "error" : "pending";
    } else if (allDone) {
      state = "done";
    } else {
      state = idx < display ? "done" : idx === display ? "active" : "pending";
    }
    return { ...def, state };
  });

  const totalMs = events.reduce((max, ev) => Math.max(max, ev.data?.elapsed_ms ?? 0), 0);

  return (
    <div className="generation-progress">
      <div className="gp-header">
        <h3>{title || (mode === "query" ? "Processing Query" : `Generating ${mode === "dashboard" ? "Dashboard" : "Report"}`)}</h3>
        {totalMs > 0 && <span className="stat-chip">{(totalMs / 1000).toFixed(1)}s</span>}
      </div>

      <div className="gp-stages">
        {stages.map((stage, idx) => (
          <div key={idx} className={`gp-stage gp-stage-${stage.state}`}>
            <div className="gp-stage-icon">
              {stage.state === "pending" && <span className="gp-circle-empty" />}
              {stage.state === "active" && <span className="spinner-sm" />}
              {stage.state === "done" && <Icon name="check" size={13} />}
              {stage.state === "error" && <span className="error-icon"><Icon name="alert" size={13} /></span>}
            </div>
            <div className="gp-stage-text">
              <div className="gp-stage-label">{stage.label}</div>
              {stage.subLabel && stage.state !== "pending" && (
                <div className="gp-stage-sub">{stage.subLabel}</div>
              )}
            </div>
          </div>
        ))}
      </div>

      <div className="gp-footer">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => setShowTechnical((t) => !t)}
        >
          {showTechnical ? "Hide technical details" : "Show technical details"}
        </button>
        {active && onCancel && (
          <button type="button" className="btn btn-danger btn-sm" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>

      {showTechnical && (
        <div className="gp-technical">
          <AIProgressTimeline events={events} active={active} title="Technical details" />
        </div>
      )}
    </div>
  );
}

// ── Signal extraction: turn the raw SSE events into "how far did we get" ──────
// These no longer bake in pending/active/done — the component's smoothed
// `display` cursor does that — they only report the furthest real stage reached.

function querySignals(events: AIProgressEvent[]): StageSignals {
  // Fast path emits only fast_path_started → llm start/end → executing_sql; the
  // full pipeline adds pipeline_started/validating. Derive progression from
  // whichever signals arrive so BOTH tick 1→2→3→4.
  let hasError = false;
  let fastPathRejected = false;
  let repairCount = 0;
  let planningStarted = false;
  let planningDone = false;
  let executing = false;

  for (const ev of events) {
    if (ev.type === "error" || ev.data?.status === "dropped") hasError = true;
    if (ev.type === "llm") {
      if (ev.data?.phase === "start") planningStarted = true;
      if (ev.data?.phase === "end") planningDone = true;
    }
    if (ev.type === "stage") {
      const st = ev.data.stage;
      if (st === "fast_path_rejected") fastPathRejected = true;
      if (st === "pipeline_started") planningStarted = true;
      if (st === "validating") planningDone = true;
      if (st === "repairing_sql") { planningDone = true; repairCount++; }
      if (st === "executing_sql") { planningStarted = true; planningDone = true; executing = true; }
    }
  }

  let target = 1;
  if (planningStarted) target = 2;
  if (planningDone) target = 3;
  if (executing) target = 4;

  const defs: StageDef[] = [
    { label: "Understanding your question" },
    { label: "Planning the query", subLabel: fastPathRejected ? "Thinking harder about this one…" : undefined },
    { label: "Checking the SQL", subLabel: repairCount > 0 ? `Fixing a query issue (attempt ${repairCount})` : undefined },
    { label: "Running your query" },
  ];

  return { defs, target, hasError };
}

function designSignals(events: AIProgressEvent[], mode: "dashboard" | "report"): StageSignals {
  const itemType = mode === "dashboard" ? "dashboard" : "report";
  let hasError = false;
  let target = 1;
  let llmActive = "";
  let okItems = 0;
  let droppedItems = 0;
  const itemsSeen = new Set<string>();

  for (const ev of events) {
    if (ev.type === "error") hasError = true;
    if (ev.type === "stage" && ev.data.stage === "pipeline_started") target = Math.max(target, 2);
    if (ev.type === "stage" && (ev.data.stage === "widget_sql_started" || ev.data.stage === "sheet_sql_started")) target = Math.max(target, 3);
    if (ev.type === "stage" && (ev.data.stage === "widget_sql_done" || ev.data.stage === "sheet_sql_done")) target = Math.max(target, 3);
    if (ev.type === "stage" && ev.data.stage === "validating") target = Math.max(target, 4);
    if (ev.type === "stage" && ev.data.stage === "persisting") target = Math.max(target, 5);
    if (ev.type === "dashboard" || ev.type === "report") target = 5;

    if ((ev.type === "llm" || ev.type === "tool") && ev.data.phase === "start") {
      llmActive = `Running ${ev.data.agent || ev.data.tool || "agent"}…`;
    } else if (ev.type === "llm" || ev.type === "tool") {
      llmActive = "";
    }

    if (ev.type === "widget" || ev.type === "sheet") {
      target = Math.max(target, 4);
      if (ev.data.status === "ok") okItems++;
      if (ev.data.status === "dropped") droppedItems++;
      if (ev.data.title) itemsSeen.add(ev.data.title);
    }
  }

  const realTotal = itemsSeen.size;
  const checked = okItems + droppedItems;
  const checkSub = realTotal > 0 ? `Checking ${Math.min(checked, realTotal)} of ${realTotal}` : undefined;

  const defs: StageDef[] = [
    { label: "Understanding your data" },
    { label: `Designing your ${itemType}`, subLabel: llmActive || undefined },
    { label: "Writing the queries" },
    { label: "Checking everything against your data", subLabel: checkSub },
    { label: "Saving…" },
  ];

  return { defs, target, hasError };
}
