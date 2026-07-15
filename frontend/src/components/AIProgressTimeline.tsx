import React, { useEffect, useMemo, useState } from "react";
import type { AIProgressEvent, AIProgressEventData } from "../types";

// ── AI pipeline progress timeline ─────────────────────────────
// Renders the SSE progress events of a streaming AI operation
// (query planning, dashboard generate/refine) as an ordered,
// live-updating checklist. Reused by the query page and both
// dashboard modals.

interface TimelineItem {
  key: string;
  kind: "stage" | "llm" | "tool" | "widget" | "other";
  label: string;
  sub?: string;
  /** Still running — resolved by a later event or by the stream ending. */
  pending: boolean;
  /** Something went wrong on this step (e.g. widget dropped). */
  warning?: boolean;
  /** Server-reported ms since the operation started. */
  elapsedMs?: number;
  /** Duration of a finished llm/tool call. */
  durationMs?: number;
}

const STAGE_LABELS: Record<string, (d: AIProgressEventData) => string> = {
  fast_path_started: () => "Trying fast path…",
  fast_path_rejected: (d) =>
    d.detail ? `Fast path rejected: ${d.detail}` : "Fast path rejected",
  pipeline_started: () => "Running full pipeline…",
  subagent_started: (d) => `${d.detail || d.agent || "Sub-agent"} working…`,
  subagent_finished: (d) => `${d.detail || d.agent || "Sub-agent"} finished`,
  widget_sql_started: (d) =>
    d.detail ? `Generating SQL — ${d.detail}…` : "Generating SQL…",
  widget_sql_done: (d) =>
    d.detail ? `SQL generated — ${d.detail}` : "SQL generated",
  validating: () => "Validating…",
  executing_sql: () => "Executing SQL…",
};

/** "some_unknown_stage" → "Some unknown stage…" (forward-compat). */
function prettifyStage(stage: string): string {
  const words = stage.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1) + "…";
}

function widgetLabel(d: AIProgressEventData): { label: string; warning: boolean } {
  const title = d.title ?? "widget";
  switch (d.status) {
    case "verifying":
      return { label: `Verifying widget '${title}'…`, warning: false };
    case "repairing":
      return {
        label: `Repairing widget '${title}'${d.attempt ? ` (attempt ${d.attempt})` : ""}…`,
        warning: false,
      };
    case "ok":
      return { label: `Widget '${title}' verified`, warning: false };
    case "dropped":
      return {
        label: `Widget '${title}' dropped${d.detail ? `: ${d.detail}` : ""}`,
        warning: true,
      };
    default:
      return { label: `Widget '${title}': ${d.status ?? "update"}`, warning: false };
  }
}

/** Fold the raw event list into display rows, pairing start/end events. */
function buildItems(events: AIProgressEvent[]): TimelineItem[] {
  const items: TimelineItem[] = [];

  const resolveLast = (match: (it: TimelineItem) => boolean): TimelineItem | null => {
    for (let i = items.length - 1; i >= 0; i--) {
      if (items[i].pending && match(items[i])) return items[i];
    }
    return null;
  };

  events.forEach((ev, i) => {
    const d = ev.data ?? {};
    const key = `${i}-${ev.type}`;

    switch (ev.type) {
      case "stage": {
        // A new stage checkpoint finishes the previous in-flight one.
        const prev = resolveLast((it) => it.kind === "stage");
        if (prev) prev.pending = false;
        const stage = d.stage ?? "";
        const toLabel = STAGE_LABELS[stage];
        items.push({
          key,
          kind: "stage",
          label: toLabel ? toLabel(d) : prettifyStage(stage || ev.type),
          pending: stage !== "fast_path_rejected" && stage !== "subagent_finished",
          elapsedMs: d.elapsed_ms,
        });
        break;
      }

      case "llm": {
        if (d.phase === "end") {
          const open = resolveLast(
            (it) => it.kind === "llm" && it.sub === (d.agent ?? "")
          );
          if (open) {
            open.pending = false;
            open.durationMs = d.duration_ms;
            open.elapsedMs = d.elapsed_ms ?? open.elapsedMs;
            if (d.input_tokens != null || d.output_tokens != null) {
              open.label = `LLM call — ${d.agent ?? "agent"} (${d.input_tokens ?? "?"}→${d.output_tokens ?? "?"} tokens)`;
            }
            break;
          }
        }
        items.push({
          key,
          kind: "llm",
          label: `LLM call — ${d.agent ?? "agent"}${d.model ? ` (${d.model})` : ""}`,
          sub: d.agent ?? "",
          pending: d.phase !== "end",
          durationMs: d.phase === "end" ? d.duration_ms : undefined,
          elapsedMs: d.elapsed_ms,
        });
        break;
      }

      case "tool": {
        if (d.phase === "end") {
          const open = resolveLast(
            (it) => it.kind === "tool" && it.sub === `${d.agent ?? ""}/${d.tool ?? ""}`
          );
          if (open) {
            open.pending = false;
            open.durationMs = d.duration_ms;
            open.elapsedMs = d.elapsed_ms ?? open.elapsedMs;
            break;
          }
        }
        items.push({
          key,
          kind: "tool",
          label: `Tool call — ${d.tool ?? "tool"}${d.agent ? ` (${d.agent})` : ""}`,
          sub: `${d.agent ?? ""}/${d.tool ?? ""}`,
          pending: d.phase !== "end",
          durationMs: d.phase === "end" ? d.duration_ms : undefined,
          elapsedMs: d.elapsed_ms,
        });
        break;
      }

      case "widget": {
        // A new event for the same widget resolves its previous in-flight row.
        const prev = resolveLast(
          (it) => it.kind === "widget" && it.sub === (d.title ?? "")
        );
        if (prev) prev.pending = false;
        const { label, warning } = widgetLabel(d);
        items.push({
          key,
          kind: "widget",
          label,
          sub: d.title ?? "",
          warning,
          pending: d.status === "verifying" || d.status === "repairing",
          elapsedMs: d.elapsed_ms,
        });
        break;
      }

      default: {
        // Unknown event types still show up (forward-compat).
        items.push({
          key,
          kind: "other",
          label: d.detail || prettifyStage(ev.type),
          pending: false,
          elapsedMs: d.elapsed_ms,
        });
      }
    }
  });

  return items;
}

function formatSeconds(ms?: number): string | null {
  if (ms == null || Number.isNaN(ms)) return null;
  return `${(ms / 1000).toFixed(1)}s`;
}

interface AIProgressTimelineProps {
  events: AIProgressEvent[];
  /** True while the stream is still running. */
  active: boolean;
  title?: string;
}

const AIProgressTimeline: React.FC<AIProgressTimelineProps> = ({
  events,
  active,
  title = "AI Pipeline",
}) => {
  const [expanded, setExpanded] = useState(true);

  // Expand while streaming, collapse to a slim summary once finished.
  useEffect(() => {
    setExpanded(active);
  }, [active]);

  const items = useMemo(() => buildItems(events), [events]);

  if (!active && events.length === 0) return null;

  const totalMs = events.reduce(
    (max, ev) => Math.max(max, ev.data?.elapsed_ms ?? 0),
    0
  );
  const lastLabel =
    items.length > 0 ? items[items.length - 1].label : "Connecting…";

  return (
    <div className="card ai-progress-card">
      <div
        className="card-header"
        onClick={() => setExpanded((e) => !e)}
        style={{ cursor: "pointer" }}
      >
        <div className="card-title">
          <span>{expanded ? "▾" : "▸"}</span>
          <span className="section-title">{title}</span>
          <span className="badge badge--ai">AI</span>
          {active && <span className="ai-progress-current">{lastLabel}</span>}
        </div>
        <div className="plan-stats">
          {totalMs > 0 && <span className="stat-chip">{formatSeconds(totalMs)}</span>}
          {active ? (
            <span className="spinner-sm" />
          ) : (
            <span className="stat-chip">
              {items.length} step{items.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>
      </div>

      {expanded && (
        <div className="card-body">
          <ul className="ai-timeline">
            {items.length === 0 && (
              <li className="ai-timeline-item ai-timeline-item--pending">
                <span className="ai-timeline-marker">
                  <span className="spinner-sm" />
                </span>
                <span className="ai-timeline-label">Connecting…</span>
              </li>
            )}
            {items.map((it) => {
              const pending = active && it.pending;
              return (
                <li
                  key={it.key}
                  className={`ai-timeline-item${pending ? " ai-timeline-item--pending" : ""}${it.warning ? " ai-timeline-item--warning" : ""}`}
                >
                  <span className="ai-timeline-marker">
                    {pending ? (
                      <span className="spinner-sm" />
                    ) : it.warning ? (
                      "⚠"
                    ) : (
                      "✓"
                    )}
                  </span>
                  <span className="ai-timeline-label">{it.label}</span>
                  <span className="ai-timeline-time">
                    {it.durationMs != null && (
                      <span className="ai-timeline-duration">
                        {formatSeconds(it.durationMs)}
                      </span>
                    )}
                    {formatSeconds(it.elapsedMs) && (
                      <span title="Elapsed since the request started">
                        @{formatSeconds(it.elapsedMs)}
                      </span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
};

export default AIProgressTimeline;
