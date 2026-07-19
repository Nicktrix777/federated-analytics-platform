import React, { useEffect, useRef } from "react";
import type { TranscriptTurn, Clarification } from "../types";
import QueryPlanView from "./QueryPlan";
import ResultsTable from "./ResultsTable";
import ResultsChart from "./ResultsChart";
import AIProgressTimeline from "./AIProgressTimeline";

// ── Clarification card (PR7) ──────────────────────────────────
// The AI asked a question instead of guessing. Option buttons answer on the
// same conversation; once answered the card locks (disabled) so the thread
// reads as history.
interface ClarificationMessageProps {
  clarification: Clarification;
  answered: boolean;
  onSelect: (option: string) => void;
}

const ClarificationMessage: React.FC<ClarificationMessageProps> = ({
  clarification,
  answered,
  onSelect,
}) => (
  <div className={`clarification-card ${answered ? "clarification-card--answered" : ""}`}>
    <div className="clarification-icon">💬</div>
    <h3 className="clarification-question">{clarification.question}</h3>
    {clarification.options.length > 0 && (
      <div className="clarification-options">
        {clarification.options.map((option, i) => (
          <button
            key={i}
            className="clarification-option-btn"
            onClick={() => !answered && onSelect(option)}
            disabled={answered}
          >
            {option}
          </button>
        ))}
      </div>
    )}
    {!answered && (
      <p className="clarification-hint">
        Pick an option above, or type your own answer below
      </p>
    )}
  </div>
);

interface TranscriptProps {
  turns: TranscriptTurn[];
  /** Answer a clarification (starts the next turn on the same conversation). */
  onClarify: (option: string) => void;
}

const Transcript: React.FC<TranscriptProps> = ({ turns, onClarify }) => {
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Only auto-scroll when the user is already near the bottom, so scrolling up
  // to read an earlier turn isn't yanked back down mid-stream.
  const stickRef = useRef(true);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };

  useEffect(() => {
    if (stickRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [turns]);

  return (
    <div className="transcript" ref={scrollRef} onScroll={onScroll}>
      {turns.map((turn) => {
        const outcome = turn.outcome;
        return (
          <div key={turn.id} className="chat-turn">
            {/* User bubble */}
            <div className="chat-bubble chat-bubble--user">
              {turn.userKind === "answer" && (
                <span className="chat-answer-tag">↳ answer</span>
              )}
              <span className="chat-bubble-text">{turn.userText}</span>
            </div>

            {/* Pipeline timeline (auto-collapses when the turn is done) */}
            {turn.progress.length > 0 && (
              <AIProgressTimeline
                events={turn.progress}
                active={turn.status === "streaming"}
              />
            )}

            {/* Outcome */}
            {outcome?.type === "result" && (
              <div className="results-container">
                {outcome.result.plan && (
                  <QueryPlanView
                    plan={outcome.result.plan}
                    executionTimeMs={outcome.result.execution_time_ms}
                    rowCount={outcome.result.row_count}
                    mode={outcome.result.mode}
                  />
                )}
                {outcome.result.columns && outcome.result.columns.length > 0 ? (
                  <>
                    <div className="results-meta">
                      <span>
                        {outcome.result.row_count} row
                        {outcome.result.row_count !== 1 ? "s" : ""}
                      </span>
                      <span>{outcome.result.execution_time_ms}ms</span>
                    </div>
                    <ResultsChart
                      columns={outcome.result.columns}
                      rows={outcome.result.rows as string[][]}
                    />
                    <ResultsTable
                      columns={outcome.result.columns}
                      rows={outcome.result.rows as string[][]}
                      rowCount={outcome.result.row_count}
                    />
                  </>
                ) : outcome.hydrated ? (
                  <div className="transcript-hydrated-note">
                    {outcome.result.row_count} row
                    {outcome.result.row_count !== 1 ? "s" : ""} — run again to
                    view the data
                  </div>
                ) : null}
              </div>
            )}

            {outcome?.type === "clarification" && (
              <ClarificationMessage
                clarification={outcome.clarification}
                answered={outcome.answered}
                onSelect={onClarify}
              />
            )}

            {outcome?.type === "error" && (
              <div className="chat-error">
                <span className="error-icon">⚠</span>
                <div>
                  <strong>Query failed</strong>
                  <p>{outcome.message}</p>
                </div>
              </div>
            )}
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
};

export default Transcript;
