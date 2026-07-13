"""
SSE event plumbing for the AI Engine.

An EventEmitter is threaded through a plan/dashboard pipeline run. Pipeline
stages emit structured progress events into it; the streaming endpoints in
main.py drain it as an SSE response while the pipeline runs as a background
task. The non-streaming endpoints pass a NullEmitter, so pipeline code never
branches on "is someone listening".

Every event payload carries elapsed_ms (since the request started) so the
stream doubles as a per-stage latency trace — persisting these events gives
replayable observability for free.

Event types (the contract consumed by Core API's SSE proxy — keep in sync
with docs/sse-events.md):
  stage          {stage, detail?}         pipeline checkpoints
  llm            {phase, agent, model?, duration_ms?, input_tokens?, output_tokens?}
  tool           {phase, tool, agent, args?, duration_ms?}
  plan           {plan, path}             terminal for /api/plan/stream
  dashboard_plan {plan}                   terminal for /api/dashboard-plan/stream
  error          {detail, status_code}    terminal
"""

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)

_STREAM_END = object()


class EventEmitter:
    """Async queue of SSE events for one pipeline run."""

    def __init__(self, request_id: str = ""):
        self.request_id = request_id
        self._queue: asyncio.Queue = asyncio.Queue()
        self._start = time.monotonic()

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)

    async def emit(self, event_type: str, **data) -> None:
        data["elapsed_ms"] = self.elapsed_ms
        if self.request_id:
            data["request_id"] = self.request_id
        self._queue.put_nowait((event_type, data))

    async def close(self) -> None:
        self._queue.put_nowait(_STREAM_END)

    async def iter_sse(self) -> AsyncIterator[str]:
        """Yield SSE-formatted frames until close(). Heartbeats every 15s keep
        proxies from idling out the connection during long LLM calls."""
        while True:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=15.0)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if item is _STREAM_END:
                return
            event_type, data = item
            yield f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"


class NullEmitter(EventEmitter):
    """Emitter that drops everything — used by the non-streaming endpoints."""

    async def emit(self, event_type: str, **data) -> None:
        return

    async def close(self) -> None:
        return


def _summarize(value, limit: int = 200) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


class AgentEventRelay:
    """Maps LangGraph astream_events(v2) to emitter events.

    Tracks which (sub)agent is active: deepagents runs subagents through its
    built-in `task` tool, so a task-tool start pushes that subagent's name and
    the matching end pops it. Attribution is per-run-id so interleaved events
    stay correct. Also counts LLM/tool calls per agent — the depth profile the
    caller can log at the end of a run.
    """

    def __init__(self, emitter: EventEmitter, root_agent: str):
        self.emitter = emitter
        self.root_agent = root_agent
        self._agent_by_run: dict[str, str] = {}  # task-tool run_id -> subagent name
        self._active_subagents: list[str] = []
        self._call_starts: dict[str, float] = {}
        self.llm_calls: dict[str, int] = {}
        self.tool_calls: dict[str, int] = {}

    def _current_agent(self) -> str:
        return self._active_subagents[-1] if self._active_subagents else self.root_agent

    async def handle(self, event: dict) -> None:
        kind = event.get("event", "")
        run_id = str(event.get("run_id", ""))
        name = event.get("name", "")
        data = event.get("data", {}) or {}
        now = time.monotonic()

        if kind == "on_chat_model_start":
            agent = self._current_agent()
            self._call_starts[run_id] = now
            self.llm_calls[agent] = self.llm_calls.get(agent, 0) + 1
            model = (event.get("metadata") or {}).get("ls_model_name", "") or name
            await self.emitter.emit("llm", phase="start", agent=agent, model=model)

        elif kind == "on_chat_model_end":
            agent = self._current_agent()
            started = self._call_starts.pop(run_id, None)
            duration_ms = int((now - started) * 1000) if started else None
            usage = {}
            output = data.get("output")
            usage_meta = getattr(output, "usage_metadata", None)
            if usage_meta:
                usage = {
                    "input_tokens": usage_meta.get("input_tokens"),
                    "output_tokens": usage_meta.get("output_tokens"),
                }
            await self.emitter.emit(
                "llm", phase="end", agent=agent, duration_ms=duration_ms, **usage
            )

        elif kind == "on_tool_start":
            tool_input = data.get("input") or {}
            if name == "task":
                # deepagents subagent hand-off
                subagent = str(
                    tool_input.get("subagent_type") or tool_input.get("name") or "subagent"
                )
                self._agent_by_run[run_id] = subagent
                self._active_subagents.append(subagent)
                await self.emitter.emit(
                    "stage",
                    stage="subagent_started",
                    detail=f"{self._current_agent()} ← delegated by {self.root_agent}",
                )
                return
            agent = self._current_agent()
            self._call_starts[run_id] = now
            self.tool_calls[agent] = self.tool_calls.get(agent, 0) + 1
            await self.emitter.emit(
                "tool", phase="start", tool=name, agent=agent, args=_summarize(tool_input)
            )

        elif kind == "on_tool_end":
            if run_id in self._agent_by_run:
                finished = self._agent_by_run.pop(run_id)
                if finished in self._active_subagents:
                    self._active_subagents.remove(finished)
                await self.emitter.emit("stage", stage="subagent_finished", detail=finished)
                return
            started = self._call_starts.pop(run_id, None)
            duration_ms = int((now - started) * 1000) if started else None
            await self.emitter.emit(
                "tool",
                phase="end",
                tool=name,
                agent=self._current_agent(),
                duration_ms=duration_ms,
            )

    def depth_profile(self) -> dict:
        """Per-agent LLM/tool call counts for the run — log this to see depth."""
        return {"llm_calls": self.llm_calls, "tool_calls": self.tool_calls}
