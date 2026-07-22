import React, { useMemo, useState } from "react";
import type { AIProgressEvent, AIProgressEventData } from "../types";
import AIProgressTimeline from "./AIProgressTimeline";

interface GenerationProgressProps {
  events: AIProgressEvent[];
  active: boolean;
  mode: "dashboard" | "report" | "query";
  title?: string;
  onCancel?: () => void;
}

interface StageStatus {
  state: "pending" | "active" | "done" | "error";
  label: string;
  subLabel?: string;
}

export default function GenerationProgress({
  events,
  active,
  mode,
  title,
  onCancel,
}: GenerationProgressProps) {
  const [showTechnical, setShowTechnical] = useState(false);

  const stages = useMemo(() => {
    if (mode === "query") {
      return buildQueryStages(events, active);
    } else {
      return buildDesignStages(events, active, mode);
    }
  }, [events, active, mode]);

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
              {stage.state === "done" && <span>✓</span>}
              {stage.state === "error" && <span className="error-icon">⚠</span>}
            </div>
            <div className="gp-stage-text">
              <div className="gp-stage-label">{stage.label}</div>
              {stage.subLabel && <div className="gp-stage-sub">{stage.subLabel}</div>}
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

function buildQueryStages(events: AIProgressEvent[], active: boolean): StageStatus[] {
  const s1: StageStatus = { state: "pending", label: "Understanding your question" };
  const s2: StageStatus = { state: "pending", label: "Planning the query" };
  const s3: StageStatus = { state: "pending", label: "Checking the SQL" };
  const s4: StageStatus = { state: "pending", label: "Running your query" };

  let currentStage = 1;
  let hasError = false;
  let fastPathRejected = false;
  let repairCount = 0;

  for (const ev of events) {
    if (ev.type === "error" || ev.data?.status === "dropped") hasError = true;
    if (ev.type === "stage") {
      const st = ev.data.stage;
      if (st === "fast_path_started") currentStage = Math.max(currentStage, 1);
      if (st === "fast_path_rejected") fastPathRejected = true;
      if (st === "pipeline_started") currentStage = Math.max(currentStage, 2);
      if (st === "validating" || st === "repairing_sql") currentStage = Math.max(currentStage, 3);
      if (st === "executing_sql") currentStage = Math.max(currentStage, 4);
      if (st === "repairing_sql") repairCount++;
    }
  }

  // Determine s1
  if (currentStage > 1) s1.state = "done";
  else if (events.length > 0) s1.state = active ? "active" : "done";

  // Determine s2
  if (currentStage > 2) s2.state = "done";
  else if (currentStage === 2) {
    s2.state = active ? "active" : "done";
    if (fastPathRejected) s2.subLabel = "Thinking harder about this one…";
  }

  // Determine s3
  if (currentStage > 3) s3.state = "done";
  else if (currentStage === 3) {
    s3.state = active ? "active" : "done";
    if (repairCount > 0) s3.subLabel = `Fixing a query issue (attempt ${repairCount})`;
  }

  // Determine s4
  if (currentStage > 4 || (!active && !hasError && currentStage >= 4)) s4.state = "done";
  else if (currentStage === 4) s4.state = active ? "active" : "done";

  if (hasError && active === false) {
    if (s4.state === "active") s4.state = "error";
    else if (s3.state === "active") s3.state = "error";
    else if (s2.state === "active") s2.state = "error";
    else if (s1.state === "active") s1.state = "error";
  }

  return [s1, s2, s3, s4];
}

function buildDesignStages(events: AIProgressEvent[], active: boolean, mode: "dashboard" | "report"): StageStatus[] {
  const itemType = mode === "dashboard" ? "dashboard" : "report";
  const s1: StageStatus = { state: "pending", label: "Understanding your data" };
  const s2: StageStatus = { state: "pending", label: `Designing your ${itemType}` };
  const s3: StageStatus = { state: "pending", label: "Writing the queries" };
  const s4: StageStatus = { state: "pending", label: "Checking everything against your data" };
  const s5: StageStatus = { state: "pending", label: "Saving…" };

  let currentStage = 0;
  let hasError = false;
  let llmActive = "";
  let totalItems = 0;
  let okItems = 0;
  let droppedItems = 0;

  for (const ev of events) {
    if (ev.type === "error") hasError = true;
    if (currentStage === 0) currentStage = 1;
    
    if (ev.type === "stage" && ev.data.stage === "pipeline_started") {
      currentStage = Math.max(currentStage, 2);
    }
    if (ev.type === "stage" && (ev.data.stage === "widget_sql_started" || ev.data.stage === "sheet_sql_started")) {
      currentStage = Math.max(currentStage, 3);
    }
    if (ev.type === "stage" && (ev.data.stage === "widget_sql_done" || ev.data.stage === "sheet_sql_done")) {
      currentStage = Math.max(currentStage, 3); // Will be 4 when validating starts
    }
    if (ev.type === "stage" && ev.data.stage === "validating") {
      currentStage = Math.max(currentStage, 4);
    }
    if (ev.type === "stage" && ev.data.stage === "persisting") {
      currentStage = Math.max(currentStage, 5);
    }
    if (ev.type === "dashboard" || ev.type === "report") {
      // Terminal event
      currentStage = 6;
    }

    if (ev.type === "llm" || ev.type === "tool") {
      if (ev.data.phase === "start") {
        llmActive = `Running ${ev.data.agent || ev.data.tool || "agent"}…`;
      } else {
        llmActive = "";
      }
    }

    // fallback counting if plan_summary is missing
    if (ev.type === "widget" || ev.type === "sheet") {
      currentStage = Math.max(currentStage, 4);
      if (ev.data.status === "verifying") totalItems++;
      if (ev.data.status === "ok") okItems++;
      if (ev.data.status === "dropped") droppedItems++;
    }
  }

  // Deduplicate total items using a Set of titles if plan_summary is absent
  const itemsSeen = new Set<string>();
  let realTotal = 0;
  for (const ev of events) {
    if ((ev.type === "widget" || ev.type === "sheet") && ev.data.title) {
      itemsSeen.add(ev.data.title);
    }
  }
  realTotal = itemsSeen.size;

  if (currentStage > 1) s1.state = "done";
  else if (currentStage === 1) s1.state = active ? "active" : "done";

  if (currentStage > 2) s2.state = "done";
  else if (currentStage === 2) {
    s2.state = active ? "active" : "done";
    if (active && llmActive) s2.subLabel = llmActive;
  }

  if (currentStage > 3) s3.state = "done";
  else if (currentStage === 3) s3.state = active ? "active" : "done";

  if (currentStage > 4) s4.state = "done";
  else if (currentStage === 4) {
    s4.state = active ? "active" : "done";
    const checked = okItems + droppedItems;
    if (realTotal > 0) {
      s4.subLabel = `Checking ${Math.min(checked, realTotal)} of ${realTotal}`;
    } else {
      s4.subLabel = `Checking items…`;
    }
  }

  if (currentStage > 5) s5.state = "done";
  else if (currentStage === 5) s5.state = active ? "active" : "done";
  
  if (!active && currentStage < 5 && !hasError) {
      // synthesize saving if it ended without explicit saving event
      s1.state = s2.state = s3.state = s4.state = s5.state = "done";
  }

  if (hasError && active === false) {
    if (s5.state === "active") s5.state = "error";
    else if (s4.state === "active") s4.state = "error";
    else if (s3.state === "active") s3.state = "error";
    else if (s2.state === "active") s2.state = "error";
    else if (s1.state === "active") s1.state = "error";
  }

  return [s1, s2, s3, s4, s5];
}
