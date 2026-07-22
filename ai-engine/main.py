"""
AI Engine — FastAPI Application (Production)

This service has two roles with distinct boundaries:

  **Planner** — stateless request handler. Never reads user data, never
  touches Trino for queries, never calls the Core API or Query Service.
  Two paths produce a QueryPlan:
    - Fast path: one cheap single-shot LLM call using the schema context
      the AI Engine self-loads from postgres-meta.
    - Full path: the deepagents multi-agent pipeline (schema-analyst +
      sql-generator), used when fast path is unavailable/invalid/low-conf.
  Both run SQL through a deterministic safety/Trino-compat gate.

  **Enrichment worker** — background loop triggered by metadata_version
  changes. May read sampled rows from Trino to compute profiles; may write
  ONLY column_profiles + embedding tables; never executes user queries.
  Runs one ordered pipeline: profile → embed.

Streaming: every pipeline emits structured progress events (stage changes,
every LLM call, every tool call, subagent hand-offs — see events.py). The
/api/plan/stream and /api/dashboard-plan/stream endpoints expose those as SSE
for the Core API to proxy to the frontend. Event contract: docs/sse-events.md.

The Core API is responsible for:
  - Deciding WHEN to call this service (AI feature flag)
  - Validating the returned QueryPlan BEFORE execution
  - Routing to the Query Service
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional, Tuple

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from config import settings
from models import (
    Clarification,
    DashboardPlan,
    DashboardPlanRequest,
    PlanOutcome,
    PlanRequest,
    QueryPlan,
    RepairBatchItem,
    RepairBatchResult,
    RepairWidgetRequest,
    RepairWidgetResponse,
    RepairWidgetsBatchRequest,
    RepairWidgetsBatchResponse,
    ReportPlan,
    ReportPlanRequest,
)
from agents.orchestrator import create_query_planner, generate_query_plan
from agents.dashboard_planner import generate_dashboard_plan
from agents.report_planner import generate_report_plan
from agents.subagents.sql_generator import SQL_GENERATOR_SYSTEM_PROMPT
from agents.tools.validation_tools import validate_and_fix_sql
from agents.tools._common import close_shared_clients
from context_bundle import (
    build_context_bundle,
    render_extra_context,
    render_fast_path_system_prompt,
    render_fast_path_user_prompt,
    render_transcript,
)
from events import EventEmitter, NullEmitter
from llm.openai_provider import OpenAIProvider
from llm.anthropic_provider import AnthropicProvider
from llm.providers import build_tier_limiters, make_custom_provider
from llm_settings import (
    SettingsValidationError,
    apply_config_overlay,
    effective_config,
    get_llm_settings_version,
    known_providers,
    provider_keys_present,
    read_llm_settings_config,
    write_llm_settings_config,
)
from embeddings import reindex_examples
from enrichment import watch_metadata_version

# A custom single-shot provider is either OpenAI-compatible or Anthropic.
CustomProvider = OpenAIProvider | AnthropicProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Global Agent Instances ────────────────────────────────────
# The dashboard/report designers are no longer deepagents graphs (PR-A1) — they
# run as a deterministic two-call pipeline through _widget_sql_provider below,
# so only the chat query planner needs a compiled agent.
_agent = None

# Fast-path provider is created ONCE at startup and reused for every request.
# The Async client inside it keeps a persistent connection pool to the LLM
# API — constructing it per request forced a fresh TCP+TLS handshake each time,
# which alone added several hundred ms per plan. Cheap tier — high-volume,
# single-shot plan attempts. Provider is derived from fast_path_model's prefix.
_fast_provider: Optional[CustomProvider] = None

# Separate provider (frontier tier) for widget SQL work — the batched
# per-dashboard widget-SQL call and widget-SQL repair. This is the one step
# that does real reasoning (matching a Trino CROSS JOIN UNNEST alias list to
# a nested row type); the cheap tier measurably produced invalid SQL on deeply
# nested array fields, so it stays on the frontier tier even though
# fast-path/schema-analyst use the cheaper tier.
_widget_sql_provider: Optional[CustomProvider] = None

# Embeddings provider (schema-RAG / few-shot RAG). Derived from embedding_model's
# prefix — MAY be a different provider than the reasoning models (e.g. OpenAI
# embeddings alongside Claude reasoning, since Anthropic has no embeddings API).
# If embeddings are unavailable (no key/credit, or an output-dimension mismatch
# with the pgvector column) RAG degrades gracefully to the full catalog + recency
# few-shots (see enrichment.run_enrichment_pipeline / embeddings.retrieve_*).
_embed_provider: Optional[CustomProvider] = None

# Caps how many /api/plan pipelines (fast or full) run at once so bursts of
# concurrent requests don't all hit OpenAI's rate limits simultaneously.
_plan_semaphore = asyncio.Semaphore(settings.ai_max_concurrent_plans)

# Strong references to in-flight streaming pipeline tasks — asyncio only
# keeps weak references to tasks, so without this a streaming run could be
# garbage-collected mid-pipeline.
_stream_tasks: set = set()


def _spawn_stream_task(coro) -> None:
    task = asyncio.create_task(coro)
    _stream_tasks.add(task)
    task.add_done_callback(_stream_tasks.discard)


# Background metadata-version watcher (Step 1b). Held as a module global so
# lifespan can cancel it cleanly on shutdown. See enrichment.watch_metadata_version.
_watcher_task: Optional[asyncio.Task] = None

# Background llm_settings-version watcher — hot-reloads the provider/agent stack
# when the UI settings change. Cancelled on shutdown alongside _watcher_task.
_settings_watcher_task: Optional[asyncio.Task] = None


# Serializes background example-reindex runs so per-request opportunistic
# triggers don't pile up — a run in progress means the next trigger is a no-op.
_examples_reindex_lock = asyncio.Lock()


async def _reindex_examples_safe() -> None:
    """Embed newly-successful AI queries for semantic few-shots, swallowing errors.

    The AI Engine never learns of a query's success directly (Core API writes
    audit_logs after execution), so this runs opportunistically at the start of
    each plan request and on startup/invalidation. The lock + incremental
    anti-join keep it cheap: usually a no-op, never concurrent, never blocking.
    """
    if not settings.few_shot_rag_enabled or _embed_provider is None:
        return
    if _examples_reindex_lock.locked():
        return
    async with _examples_reindex_lock:
        try:
            await reindex_examples(_embed_provider, settings.few_shot_reindex_limit)
        except Exception as e:
            logger.warning(f"Example embedding reindex failed (few-shots will use recency): {e}")


# Guards a rebuild of the LLM stack (providers + agents) so an in-flight request
# never observes a half-swapped set of globals — see _build_llm_stack / Part B reload.
_llm_reload_lock = asyncio.Lock()


def _export_provider_keys() -> None:
    """Export provider keys to the environment so LangChain's native integrations
    (openai/anthropic/google_genai) pick them up. The deepagents path resolves the
    client from the model-string prefix and reads the matching env key."""
    if settings.google_api_key:
        os.environ["GOOGLE_API_KEY"] = settings.google_api_key
    if settings.openai_api_key:
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key
    if settings.anthropic_api_key:
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key


def _build_llm_stack() -> None:
    """(Re)build tier limiters, custom single-shot providers, and the three
    deepagents into the module globals from the CURRENT settings.

    Called once at startup and again on a live settings change (Part B hot-reload).
    Provider + endpoint + key for EVERY call derive from each model string's
    provider prefix via llm/providers.py — there is no Gemini/base_url default, so
    switching providers is a config-only change. A custom provider is disabled
    (set to None, logged) when its required cloud key is missing, which degrades
    only that feature (e.g. no embeddings key → RAG falls back to full catalog).
    """
    global _agent
    global _fast_provider, _widget_sql_provider, _embed_provider

    _export_provider_keys()
    limiters = build_tier_limiters()

    # Custom single-shot path (fast plan / widget+sheet SQL / repair / embeddings /
    # dashboard+report design calls — see agents/dashboard_planner.py, agents/report_planner.py).
    _fast_provider = make_custom_provider(settings.fast_path_model, limiters["fast"])
    _widget_sql_provider = make_custom_provider(settings.dashboard_widget_sql_model, limiters["frontier"])
    _embed_provider = make_custom_provider(settings.embedding_model, limiters["embed"])

    # deepagents path — only the chat query planner (schema-analyst + sql-generator)
    # still runs as a ReAct loop; report/dashboard generation is deterministic (PR-A1).
    _agent = create_query_planner(model=settings.llm_model, limiters=limiters)

    logger.info(
        "LLM stack ready — deepagents=%s | fast=%s | widget/sheet=%s | embed=%s | "
        "rpm(frontier/fast/embed)=%s/%s/%s",
        settings.llm_model,
        settings.fast_path_model if _fast_provider else "DISABLED(no key)",
        settings.dashboard_widget_sql_model if _widget_sql_provider else "DISABLED(no key)",
        settings.embedding_model if _embed_provider else "DISABLED(no key)",
        settings.llm_frontier_rpm, settings.llm_fast_rpm, settings.llm_embed_rpm,
    )


async def _reload_llm_stack_from_db() -> None:
    """Re-read the DB config overlay and rebuild the whole LLM stack, atomically.

    Under _llm_reload_lock so an in-flight request never observes a half-swapped
    set of globals — a request that already captured the old provider/agent ref
    finishes on it; the next request picks up the new one.
    """
    async with _llm_reload_lock:
        apply_config_overlay(await read_llm_settings_config())
        _build_llm_stack()


async def watch_llm_settings_version() -> None:
    """Poll llm_settings.version; on change, hot-reload the LLM stack.

    Mirrors watch_metadata_version but for the (cheap) provider/agent rebuild —
    it does NOT touch the RAG indexes, so switching provider/model/RPM from the
    UI takes effect within one poll interval without a container restart. The
    first iteration is a no-op (startup already built the stack via the overlay).
    Cancelled cleanly on shutdown.
    """
    poll = settings.metadata_version_poll_seconds
    last_version = await get_llm_settings_version()
    while True:
        try:
            version = await get_llm_settings_version()
            if version is not None and version != last_version:
                logger.info("LLM settings changed (version=%s) — hot-reloading stack", version)
                await _reload_llm_stack_from_db()
                last_version = version
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"llm_settings watcher iteration failed: {e}")
        await asyncio.sleep(poll)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _watcher_task, _settings_watcher_task, _agent
    try:
        # Apply the DB config overlay (non-secret UI-editable fields) on top of
        # the env defaults BEFORE building the stack, so a saved config survives
        # restarts. Empty/absent row → env defaults verbatim.
        apply_config_overlay(await read_llm_settings_config())
        _build_llm_stack()

        # Start the metadata-version watcher. Its first iteration always runs the
        # enrichment pipeline, which warms the RAG indexes on boot exactly as the
        # old startup reindex did; thereafter it re-runs only when metadata_state
        # .version moves. It supersedes the /api/invalidate-cache poke web.
        # Pass a getter (not the value) so a settings hot-reload that swaps the
        # embeddings provider is picked up without restarting the watcher.
        _watcher_task = asyncio.create_task(watch_metadata_version(lambda: _embed_provider))
        # Hot-reload watcher: applies UI settings changes to the provider/agent
        # stack live (no restart). Cheap — no RAG re-embedding.
        _settings_watcher_task = asyncio.create_task(watch_llm_settings_version())
        # Few-shot examples are audit-log-driven (not metadata-driven), so they
        # keep their own opportunistic startup warm rather than riding the watcher.
        if settings.few_shot_rag_enabled:
            _spawn_stream_task(_reindex_examples_safe())
    except Exception as e:
        logger.warning(f"Agent init failed: {e}. AI mode will return errors.")
        _agent = None
    yield
    for task in (_watcher_task, _settings_watcher_task):
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    await close_shared_clients()
    logger.info("AI Engine shutting down")


# ── FastAPI App ───────────────────────────────────────────────
app = FastAPI(
    title="Federated Analytics Platform — AI Engine",
    description=(
        "Converts natural language questions into structured Trino query plans "
        "using a deepagents multi-agent architecture."
    ),
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # tell nginx/proxies not to buffer the stream
}


# ── Health Check ──────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "ai-engine",
        "version": "2.1.0",
        "agent": "deepagents",
        "model": settings.llm_model,
        "agent_ready": _agent is not None,
        "fast_path_enabled": settings.fast_path_enabled,
        "fast_path_model": settings.fast_path_model,
    }


# ── LLM settings (runtime-editable, non-secret) ───────────────
# Internal endpoints — reached only via the Core API proxy (which enforces auth).
# API keys are NEVER read or written here; they stay in the environment.
@app.get("/api/llm-settings")
async def get_llm_settings():
    """Effective LLM config + which provider keys are set (booleans, not values)."""
    return {
        "config": effective_config(),
        "provider_keys_present": provider_keys_present(),
        "known_providers": known_providers(),
        "version": await get_llm_settings_version(),
    }


@app.put("/api/llm-settings")
async def put_llm_settings(payload: dict):
    """Validate + persist the editable LLM config, then hot-reload the stack.

    Accepts either the flat editable fields or `{"config": {...}}`. Unknown keys
    are ignored; a bad value returns 400 and nothing is written. The reload runs
    inline so the response reflects the newly-applied state.
    """
    config = payload.get("config", payload) if isinstance(payload, dict) else {}
    try:
        version = await write_llm_settings_config(config)
    except SettingsValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _reload_llm_stack_from_db()
    return {
        "status": "ok",
        "version": version,
        "config": effective_config(),
        "provider_keys_present": provider_keys_present(),
    }


# ── Helpers ────────────────────────────────────────────────────
def _apply_validation(plan: QueryPlan) -> QueryPlan:
    """Deterministic safety/auto-fix gate — the shared final step for both paths."""
    result = validate_and_fix_sql(plan.sql)
    if not result["is_valid"]:
        raise ValueError(f"Generated SQL failed validation: {result['issues']}")
    adjusted_confidence = max(0.0, min(1.0, plan.confidence + result["confidence_adjustment"]))
    return plan.model_copy(update={"sql": result["fixed_sql"], "confidence": adjusted_confidence})


def _build_fastpath_hint(draft_sql: str, reason: str) -> str:
    """Package a rejected fast-path attempt as a repair hint for the full pipeline.

    The escalation used to discard the fast path's work entirely — hard
    questions paid for the fast attempt AND a from-scratch full pipeline.
    Handing the draft over lets sql-generator repair instead of rewrite.
    """
    return (
        "First-pass draft (from a quick single-shot attempt — NOT validated, do not trust blindly):\n"
        f"Draft SQL:\n{draft_sql}\n"
        f"Why it was rejected: {reason}\n"
        "If the draft's approach fits the question, REPAIR it (fix exactly what the rejection "
        "describes) instead of starting from scratch. If the approach is wrong, ignore it."
    )


async def _try_fast_path(
    request: PlanRequest, bundle, emitter: EventEmitter
) -> Tuple[Optional[QueryPlan], Optional[str]]:
    """
    One cheap single-shot LLM call for questions the pre-loaded schema context
    can already answer.

    Returns (plan, None) on success. On failure returns (None, hint) where
    hint carries the rejected draft + rejection reason for the full pipeline
    (or None when there is no useful draft to hand off).
    """
    if _fast_provider is None:
        return None, None

    await emitter.emit("stage", stage="fast_path_started", detail=settings.fast_path_model)
    await emitter.emit("llm", phase="start", agent="fast-path", model=settings.fast_path_model)
    call_start = time.monotonic()
    try:
        # The bundle already carries the (RAG-trimmed) datasets, the curated join
        # map, and the few-shot examples most similar to THIS question — all LIVE
        # metadata, so the prompt reshapes itself per deployment.
        system_prompt = render_fast_path_system_prompt(bundle)
        user_prompt = render_fast_path_user_prompt(request)
        result = await _fast_provider.generate_plan(system_prompt, user_prompt, request.question)
    except Exception as e:
        logger.info(f"Fast path call failed, escalating with no draft: {e}")
        await emitter.emit("stage", stage="fast_path_rejected", detail=f"call failed: {e}")
        return None, None
    duration_ms = int((time.monotonic() - call_start) * 1000)
    await emitter.emit("llm", phase="end", agent="fast-path", duration_ms=duration_ms)

    # PR5: fast-path clarification escalates to the full pipeline — the cheap
    # model shouldn't be the one deciding to ask questions; only the full
    # pipeline's clarification is terminal.
    if isinstance(result, Clarification):
        hint = (
            f"The fast-path wanted to clarify: \"{result.question}\" "
            f"(options: {result.options}). Use the full pipeline to decide "
            "whether to clarify or plan."
        )
        logger.info(f"Fast path returned clarification, escalating: {result.question[:80]}")
        await emitter.emit("stage", stage="fast_path_rejected", detail="returned clarification, escalating")
        return None, hint

    plan = result
    try:
        validated = _apply_validation(plan)
    except ValueError as e:
        reason = f"failed deterministic SQL validation: {e}"
        logger.info(f"Fast path rejected ({reason}), escalating with draft hand-off")
        await emitter.emit("stage", stage="fast_path_rejected", detail=reason)
        return None, _build_fastpath_hint(plan.sql, reason)

    if validated.confidence < settings.fast_path_confidence_threshold:
        reason = (
            f"self-reported confidence {validated.confidence:.2f} below threshold "
            f"{settings.fast_path_confidence_threshold}"
        )
        logger.info(f"Fast path rejected ({reason}), escalating with draft hand-off")
        await emitter.emit("stage", stage="fast_path_rejected", detail=reason)
        return None, _build_fastpath_hint(validated.sql, reason)

    return validated, None


# ── Plan Pipeline (shared by /api/plan and /api/plan/stream) ──
async def _run_plan_pipeline(request: PlanRequest, emitter: EventEmitter) -> Tuple["QueryPlan | Clarification", str]:
    """Full plan flow: semaphore → fast path → (escalate w/ hint) → validation.

    Returns (plan_or_clarification, path_used). Raises HTTPException on every failure mode.
    """
    try:
        await asyncio.wait_for(_plan_semaphore.acquire(), timeout=settings.ai_queue_timeout_seconds)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="AI Engine is at capacity, please retry shortly",
        )

    start = time.monotonic()
    try:
        logger.info(f"Generating plan for: {request.question[:100]}")

        # Opportunistically fold any newly-succeeded queries into the few-shot
        # index (fire-and-forget, lock-guarded — usually a cheap no-op).
        _spawn_stream_task(_reindex_examples_safe())

        # Assemble planning context once: self-load the catalog (Core API no
        # longer pushes it), schema-RAG-trim it to the question, and fetch the
        # curated join map + few-shot examples. Both paths render from this
        # single bundle. Examples are only rendered by the fast-path system
        # prompt, so skip fetching them when fast path is off.
        bundle = await build_context_bundle(
            request.question,
            provider=_embed_provider,
            datasets=request.datasets or None,
            include_examples=settings.fast_path_enabled,
            emitter=emitter,
        )

        plan = None
        fastpath_hint = None
        path_used = "full"
        if settings.fast_path_enabled and bundle.datasets:
            result, fastpath_hint = await _try_fast_path(request, bundle, emitter)
            if result is not None:
                if isinstance(result, Clarification):
                    # Fast-path clarification was already escalated in _try_fast_path;
                    # this branch shouldn't happen, but handle defensively.
                    pass
                else:
                    plan = result
                    path_used = "fast"

        if plan is None:
            if _agent is None:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Query planner agent is not initialized. "
                        "Check the LLM provider key (LLM_API_KEY / GOOGLE_API_KEY) and LLM_MODEL."
                    ),
                )
            await emitter.emit("stage", stage="pipeline_started", detail="full deepagents pipeline")
            extra_context = render_extra_context(bundle)
            transcript_text = render_transcript(request.messages)

            # PR5 depth cap: count clarification messages in the replayed window;
            # at ≥ cap, append a prompt instruction forcing a best-effort plan.
            clar_count = sum(
                1 for m in request.messages
                if (getattr(m, "kind", None) or (m.get("kind") if isinstance(m, dict) else "")) == "clarification"
            )
            depth_cap_instruction = ""
            if clar_count >= settings.clarification_depth_cap:
                depth_cap_instruction = (
                    "\n\nIMPORTANT: The user has already answered multiple clarifying questions in this "
                    "conversation. Do NOT ask another clarifying question — produce a best-effort "
                    "SQL plan and state your assumptions in the explanation. If you are unsure, "
                    "use the most common interpretation and set confidence accordingly."
                )
                logger.info(f"Depth cap reached ({clar_count} >= {settings.clarification_depth_cap}), forcing plan")

            if transcript_text:
                convo = (
                    f"Conversation so far:\n{transcript_text}\n"
                    "(The question may be a follow-up referring to these prior turns.)"
                )
                extra_context = f"{convo}\n\n{extra_context}" if extra_context else convo
            if depth_cap_instruction:
                extra_context = f"{extra_context}\n{depth_cap_instruction}" if extra_context else depth_cap_instruction
            if fastpath_hint:
                extra_context = f"{extra_context}\n\n{fastpath_hint}" if extra_context else fastpath_hint
            try:
                result = await generate_query_plan(_agent, request.question, extra_context, emitter=emitter)
                # PR5: the orchestrator may return a Clarification.
                if isinstance(result, Clarification):
                    elapsed = time.monotonic() - start
                    logger.info(
                        f"Clarification returned via {path_used} path in {elapsed:.1f}s: "
                        f"question={result.question[:80]}"
                    )
                    return result, path_used
                plan = result
                await emitter.emit("stage", stage="validating")
                plan = _apply_validation(plan)
            except ValueError as e:
                logger.error(f"Plan validation failed: {e}")
                raise HTTPException(status_code=422, detail=str(e))
            except Exception as e:
                logger.error(f"Agent invocation failed: {e}", exc_info=True)
                raise HTTPException(
                    status_code=502,
                    detail=f"Query planner failed: {str(e)}",
                )

        elapsed = time.monotonic() - start
        logger.info(
            f"Plan generated via {path_used} path in {elapsed:.1f}s: "
            f"confidence={plan.confidence:.2f}, sql_len={len(plan.sql)}, steps={len(plan.steps)}"
        )
        return plan, path_used
    finally:
        _plan_semaphore.release()


# ── Plan Generation ────────────────────────────────────────────
@app.post("/api/plan", response_model=PlanOutcome)
async def generate_plan(request: PlanRequest) -> PlanOutcome:
    """
    Convert a natural language question into a structured Trino query plan,
    or return a clarification question when the input is ambiguous.

    Tries the fast single-shot path first (one cheap LLM call using Core
    API's pre-loaded schema context). Only escalates to the full deepagents
    multi-agent pipeline — schema-analyst + sql-generator — when the fast
    path is unavailable, invalid, or low-confidence; the rejected draft is
    handed off as a repair hint.

    The Core API validates this response AGAIN before executing it.
    """
    result, path_used = await _run_plan_pipeline(request, NullEmitter())
    if isinstance(result, Clarification):
        return PlanOutcome(clarification=result, path=path_used)
    return PlanOutcome(plan=result, path=path_used)


@app.post("/api/plan/stream")
async def generate_plan_stream(request: PlanRequest, x_request_id: str = Header(default="")):
    """
    Same pipeline as /api/plan, but returns Server-Sent Events: stage
    checkpoints, every LLM/tool call with durations and token usage, subagent
    hand-offs, then a terminal `plan` (or `error`) event. See docs/sse-events.md.
    """
    emitter = EventEmitter(request_id=x_request_id)

    async def run() -> None:
        try:
            result, path_used = await _run_plan_pipeline(request, emitter)
            if isinstance(result, Clarification):
                # PR5: clarification is a separate terminal event.
                await emitter.emit(
                    "clarification",
                    question=result.question,
                    options=result.options,
                    kind=result.kind,
                )
            else:
                await emitter.emit("plan", plan=result.model_dump(), path=path_used)
        except HTTPException as e:
            await emitter.emit("error", detail=e.detail, status_code=e.status_code)
        except Exception as e:
            logger.error(f"Streaming plan pipeline crashed: {e}", exc_info=True)
            await emitter.emit("error", detail=str(e), status_code=500)
        finally:
            await emitter.close()

    _spawn_stream_task(run())
    return StreamingResponse(emitter.iter_sse(), media_type="text/event-stream", headers=_SSE_HEADERS)


async def _retry_low_confidence_design(plan, redesign, request_datasets, emitter, label: str):
    """If a report/dashboard design came back below confidence threshold, retry ONCE
    against the full (untrimmed) catalog instead of the schema-RAG top-k selection —
    this is what rescues a brief whose relevant dataset didn't make the top-k cut.
    `redesign` is an async callable(extra_context) -> plan (same shape as `plan`).

    Keeps the retry only if it scores a higher confidence than the original; any
    retry failure is logged and swallowed since the original plan is still usable —
    this is a best-effort quality pass, not a required step.
    """
    if plan.confidence >= settings.design_low_confidence_retry_threshold:
        return plan

    logger.info(
        f"{label} confidence {plan.confidence:.2f} below "
        f"{settings.design_low_confidence_retry_threshold} — retrying against the full catalog"
    )
    await emitter.emit(
        "stage",
        stage="low_confidence_retry",
        detail=f"confidence {plan.confidence:.2f} was low — retrying with the full data catalog",
    )
    try:
        # question=None skips the schema-RAG trim (and few-shot fetch) — same
        # full-catalog pattern /api/repair-widget uses when it needs every
        # identifier in scope rather than just the brief-relevant subset.
        full_bundle = await build_context_bundle(
            None,
            provider=_embed_provider,
            datasets=request_datasets or None,
            include_examples=False,
        )
        retry_plan = await redesign(render_extra_context(full_bundle))
    except Exception as e:
        logger.warning(f"{label} low-confidence retry failed, keeping original plan: {e}")
        return plan

    if retry_plan.confidence > plan.confidence:
        logger.info(f"{label} retry improved confidence {plan.confidence:.2f} -> {retry_plan.confidence:.2f}")
        return retry_plan
    return plan


# ── Dashboard Pipeline (shared by sync + stream endpoints) ────
async def _run_dashboard_pipeline(request: DashboardPlanRequest, emitter: EventEmitter) -> DashboardPlan:
    if _widget_sql_provider is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Dashboard designer is not initialized — no LLM provider configured for "
                "DASHBOARD_WIDGET_SQL_MODEL. Check its provider's API key "
                "(LLM_API_KEY / GOOGLE_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY, "
                "whichever matches the configured provider) and the model setting itself."
            ),
        )

    try:
        await asyncio.wait_for(_plan_semaphore.acquire(), timeout=settings.ai_queue_timeout_seconds)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="AI Engine is at capacity, please retry shortly",
        )

    start = time.monotonic()
    try:
        logger.info(f"Generating dashboard for: {request.prompt[:100]}")
        await emitter.emit("stage", stage="pipeline_started", detail="dashboard designer")

        # Schema RAG: design against only the datasets relevant to the brief.
        # When REFINING, the designer regenerates SQL for EVERY existing widget
        # too, so dataset selection must cover their tables — not just whatever
        # the instruction happens to mention. Folding the current widgets'
        # titles + SQL into the retrieval query keeps their datasets in scope;
        # otherwise a narrow instruction ("add a pie chart of X") can filter out
        # the context the existing widgets need, and they come back broken or
        # get dropped — the dashboard looks unchanged and the added widget is
        # the only casualty the user notices.
        retrieval_query = request.prompt
        if request.current_dashboard:
            widget_texts = [
                f"{w.get('title', '')} {w.get('sql', '')}"
                for w in (request.current_dashboard.get("widgets") or [])
            ]
            if widget_texts:
                retrieval_query = request.prompt + "\n" + "\n".join(widget_texts)
        # Self-load the catalog (Core API no longer pushes datasets for
        # dashboards either — correction #2) then schema-RAG-trim it to the
        # brief before rendering the designer's schema context.
        bundle = await build_context_bundle(
            retrieval_query,
            provider=_embed_provider,
            datasets=request.datasets or None,
            include_examples=True,
            emitter=emitter,
        )
        extra_context = render_extra_context(bundle)

        def _redesign(ctx: Optional[str]):
            return generate_dashboard_plan(
                request.prompt,
                _widget_sql_provider,
                extra_context=ctx,
                current_dashboard=request.current_dashboard,
                emitter=emitter,
            )

        try:
            plan = await _redesign(extra_context)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            logger.error(f"Dashboard designer invocation failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail=f"Dashboard designer failed: {str(e)}")

        plan = await _retry_low_confidence_design(
            plan, _redesign, request.datasets, emitter, "Dashboard design"
        )

        # Deterministic safety gate per widget; drop the ones that fail.
        await emitter.emit("stage", stage="validating", detail=f"{len(plan.widgets)} widgets")
        valid_widgets = []
        for w in plan.widgets:
            result = validate_and_fix_sql(w.sql)
            if result["is_valid"]:
                valid_widgets.append(w.model_copy(update={"sql": result["fixed_sql"]}))
            else:
                logger.warning(f"Dropping widget '{w.title}' — invalid SQL: {result['issues']}")
        if not valid_widgets:
            raise HTTPException(
                status_code=422,
                detail="All generated widgets failed SQL validation",
            )
        dropped = len(plan.widgets) - len(valid_widgets)
        plan = plan.model_copy(update={"widgets": valid_widgets})

        elapsed = time.monotonic() - start
        logger.info(
            f"Dashboard plan generated in {elapsed:.1f}s: "
            f"'{plan.name}', widgets={len(plan.widgets)} (dropped={dropped}), "
            f"confidence={plan.confidence:.2f}"
        )
        return plan
    finally:
        _plan_semaphore.release()


# ── Dashboard Plan Generation ──────────────────────────────────
@app.post("/api/dashboard-plan", response_model=DashboardPlan)
async def generate_dashboard(request: DashboardPlanRequest) -> DashboardPlan:
    """
    Design (or refine) a full dashboard from a natural-language brief.

    Always runs the full deepagents pipeline — dashboard generation is a
    one-time, multi-widget task where quality matters more than latency.
    When request.current_dashboard is present the instruction is applied to
    it and the complete updated plan is returned.

    The Core API validates every widget's SQL AGAIN before persisting.
    """
    return await _run_dashboard_pipeline(request, NullEmitter())


@app.post("/api/dashboard-plan/stream")
async def generate_dashboard_stream(
    request: DashboardPlanRequest, x_request_id: str = Header(default="")
):
    """
    Same pipeline as /api/dashboard-plan, but returns Server-Sent Events,
    ending with a terminal `dashboard_plan` (or `error`) event.
    """
    emitter = EventEmitter(request_id=x_request_id)

    async def run() -> None:
        try:
            plan = await _run_dashboard_pipeline(request, emitter)
            await emitter.emit("dashboard_plan", plan=plan.model_dump())
        except HTTPException as e:
            await emitter.emit("error", detail=e.detail, status_code=e.status_code)
        except Exception as e:
            logger.error(f"Streaming dashboard pipeline crashed: {e}", exc_info=True)
            await emitter.emit("error", detail=str(e), status_code=500)
        finally:
            await emitter.close()

    _spawn_stream_task(run())
    return StreamingResponse(emitter.iter_sse(), media_type="text/event-stream", headers=_SSE_HEADERS)


# ── Report Pipeline (shared by sync + stream endpoints) ───────
async def _run_report_pipeline(request: ReportPlanRequest, emitter: EventEmitter) -> ReportPlan:
    if _widget_sql_provider is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Report designer is not initialized — no LLM provider configured for "
                "DASHBOARD_WIDGET_SQL_MODEL. Check its provider's API key "
                "(LLM_API_KEY / GOOGLE_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY, "
                "whichever matches the configured provider) and the model setting itself."
            ),
        )

    try:
        await asyncio.wait_for(_plan_semaphore.acquire(), timeout=settings.ai_queue_timeout_seconds)
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=503,
            detail="AI Engine is at capacity, please retry shortly",
        )

    start = time.monotonic()
    try:
        logger.info(f"Generating report for: {request.prompt[:100]}")
        await emitter.emit("stage", stage="pipeline_started", detail="report designer")

        # Schema RAG: design against only the datasets relevant to the brief.
        # Same refinement caveat as the dashboard pipeline: when REFINING, the
        # designer regenerates SQL for EVERY existing sheet too, so folding the
        # current sheets' titles + SQL into the retrieval query keeps their
        # datasets in scope — otherwise a narrow instruction can filter out the
        # context the existing sheets need, and they come back broken or get
        # dropped.
        retrieval_query = request.prompt
        if request.current_report:
            sheet_texts = [
                f"{s.get('title', '')} {s.get('sql', '')}"
                for s in (request.current_report.get("sheets") or [])
            ]
            if sheet_texts:
                retrieval_query = request.prompt + "\n" + "\n".join(sheet_texts)
        bundle = await build_context_bundle(
            retrieval_query,
            provider=_embed_provider,
            datasets=request.datasets or None,
            include_examples=True,
            emitter=emitter,
        )
        extra_context = render_extra_context(bundle)

        def _redesign(ctx: Optional[str]):
            return generate_report_plan(
                request.prompt,
                _widget_sql_provider,
                extra_context=ctx,
                current_report=request.current_report,
                emitter=emitter,
            )

        try:
            plan = await _redesign(extra_context)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            logger.error(f"Report designer invocation failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail=f"Report designer failed: {str(e)}")

        plan = await _retry_low_confidence_design(
            plan, _redesign, request.datasets, emitter, "Report design"
        )

        # Deterministic safety gate per sheet; drop the ones that fail. Sheets
        # feed a row-capped Excel export, not a results grid, so the injected
        # default LIMIT is raised to 10000 (the Go exporter enforces each
        # sheet's max_rows on top).
        await emitter.emit("stage", stage="validating", detail=f"{len(plan.sheets)} sheets")
        valid_sheets = []
        for s in plan.sheets:
            result = validate_and_fix_sql(s.sql, default_limit=10000)
            if result["is_valid"]:
                # Re-sequence positions so the surviving sheets stay 0..n-1.
                valid_sheets.append(
                    s.model_copy(update={"sql": result["fixed_sql"], "position": len(valid_sheets)})
                )
            else:
                logger.warning(f"Dropping sheet '{s.title}' — invalid SQL: {result['issues']}")
        if not valid_sheets:
            raise HTTPException(
                status_code=422,
                detail="All generated sheets failed SQL validation",
            )
        dropped = len(plan.sheets) - len(valid_sheets)
        plan = plan.model_copy(update={"sheets": valid_sheets})

        elapsed = time.monotonic() - start
        logger.info(
            f"Report plan generated in {elapsed:.1f}s: "
            f"'{plan.name}', sheets={len(plan.sheets)} (dropped={dropped}), "
            f"confidence={plan.confidence:.2f}"
        )
        return plan
    finally:
        _plan_semaphore.release()


# ── Report Plan Generation ─────────────────────────────────────
@app.post("/api/report-plan", response_model=ReportPlan)
async def generate_report(request: ReportPlanRequest) -> ReportPlan:
    """
    Design (or refine) a full Excel report from a natural-language brief.

    Always runs the full deepagents pipeline — report generation is a
    one-time, multi-sheet task where quality matters more than latency.
    When request.current_report is present the instruction is applied to
    it and the complete updated plan is returned.

    The Core API validates every sheet's SQL AGAIN before persisting.
    """
    return await _run_report_pipeline(request, NullEmitter())


@app.post("/api/report-plan/stream")
async def generate_report_stream(
    request: ReportPlanRequest, x_request_id: str = Header(default="")
):
    """
    Same pipeline as /api/report-plan, but returns Server-Sent Events,
    ending with a terminal `report_plan` (or `error`) event.
    """
    emitter = EventEmitter(request_id=x_request_id)

    async def run() -> None:
        try:
            plan = await _run_report_pipeline(request, emitter)
            await emitter.emit("report_plan", plan=plan.model_dump())
        except HTTPException as e:
            await emitter.emit("error", detail=e.detail, status_code=e.status_code)
        except Exception as e:
            logger.error(f"Streaming report pipeline crashed: {e}", exc_info=True)
            await emitter.emit("error", detail=str(e), status_code=500)
        finally:
            await emitter.close()

    _spawn_stream_task(run())
    return StreamingResponse(emitter.iter_sse(), media_type="text/event-stream", headers=_SSE_HEADERS)


# ── Widget SQL Repair ──────────────────────────────────────────
REPAIR_SYSTEM_PROMPT = SQL_GENERATOR_SYSTEM_PROMPT + """

## Repair Mode
You are FIXING one Trino SQL query that FAILED to execute. You are given the
broken SQL, the EXACT execution error Trino returned, and the schema context.

Return a corrected query that resolves the error while preserving the query's
original intent and result SHAPE — same number/kind of output columns, same
aggregation, same ordering — so it still fits its chart type. A "number" tile
must still return exactly one value; a bar/pie must still return one category
column plus one value column.

The most common cause is a column or table name that does not exist. Map it to
the correct name that appears VERBATIM in the schema context (for example, a
"state" column that should be "status"). Only use names present in the schema
context — never invent one. If the error genuinely cannot be fixed from the
available schema, return your closest attempt and set confidence below 0.3.

Respond ONLY with JSON:
{"sql": "<corrected Trino SELECT SQL>", "explanation": "<what you changed>", "confidence": <0.0-1.0>}
"""

ZERO_ROWS_REPAIR_SYSTEM_PROMPT = SQL_GENERATOR_SYSTEM_PROMPT + """

## Zero-Row Repair Mode
You are REVIEWING one Trino SQL query that EXECUTED SUCCESSFULLY but returned
zero rows. This may be because a filter literal does not match how values are
actually stored (e.g. 'US' vs 'United States', 'ACTIVE' vs 'active').

If you can identify a filter literal that likely mismatches stored values based
on the schema context (sample values, column names, patterns), correct it.

If zero rows is GENUINELY CORRECT for this query (e.g. "show me orders from
next year" — none exist yet), return the SQL UNCHANGED so the caller knows no
correction is needed.

Preserve the query's original intent and result SHAPE exactly.

Respond ONLY with JSON:
{"sql": "<corrected or unchanged Trino SELECT SQL>", "explanation": "<what you changed or 'unchanged — zero rows is correct'>", "confidence": <0.0-1.0>}
"""



@app.post("/api/repair-widget", response_model=RepairWidgetResponse)
async def repair_widget(request: RepairWidgetRequest) -> RepairWidgetResponse:
    """
    Repair or review one query's SQL — two modes:

    "error"     (default): SQL failed to execute. Fix the broken identifier(s).
    "zero_rows": SQL ran fine but returned no rows. Correct filter literals if
                 they likely mismatch stored values; return UNCHANGED if zero
                 rows is genuinely correct so the caller skips the curation row.

    Architecture boundary unchanged: this NEVER executes SQL. The Core API
    re-verifies the returned SQL against Trino before using it.
    """
    if _widget_sql_provider is None:
        raise HTTPException(
            status_code=503,
            detail="SQL repair unavailable — no LLM provider configured (set LLM_API_KEY / GOOGLE_API_KEY)",
        )

    # Repair/review wants the FULL catalog (no schema-RAG trim, no few-shot
    # examples) so the correct identifier is always in scope — question=None.
    bundle = await build_context_bundle(
        None,
        provider=_embed_provider,
        datasets=request.datasets or None,
        include_examples=False,
    )
    schema_context = render_extra_context(bundle) or "(no schema context provided)"

    # Select prompt and user message based on the repair mode.
    if request.mode == "zero_rows":
        system_prompt = ZERO_ROWS_REPAIR_SYSTEM_PROMPT
        user_prompt = (
            f"Question / title: {request.title or '(untitled)'}\n\n"
            f"SQL that returned zero rows:\n{request.sql}\n\n"
            f"{schema_context}"
        )
    else:
        system_prompt = REPAIR_SYSTEM_PROMPT
        user_prompt = (
            f"Widget title: {request.title or '(untitled)'}\n"
            f"Chart type: {request.chart_type}\n\n"
            f"Broken SQL:\n{request.sql}\n\n"
            f"Execution error from Trino:\n{request.error or '(no error message provided)'}\n\n"
            f"{schema_context}"
        )

    try:
        fixed_sql = await _widget_sql_provider.repair_sql(system_prompt, user_prompt)
    except Exception as e:
        logger.error(f"Widget SQL repair failed (mode={request.mode}): {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"SQL repair failed: {e}")

    # Re-apply the deterministic safety/Trino-compat gate to the repaired SQL.
    result = validate_and_fix_sql(fixed_sql)
    if not result["is_valid"]:
        raise HTTPException(
            status_code=422,
            detail=f"Repaired SQL failed safety validation: {result['issues']}",
        )

    final_sql = result["fixed_sql"]
    changed = final_sql.strip() != request.sql.strip()
    logger.info(
        f"Repair-widget '{request.title or '(untitled)'}' mode={request.mode} "
        f"changed={changed}"
    )
    return RepairWidgetResponse(
        sql=final_sql,
        changed=changed,
        explanation="",
    )


# ── Batched Widget/Sheet SQL Repair (PR-A2) ───────────────────
# Adapts REPAIR_SYSTEM_PROMPT/ZERO_ROWS_REPAIR_SYSTEM_PROMPT for a whole list
# in one call, mirroring the exact batch pattern BATCH_SQL_SYSTEM_PROMPT /
# BATCH_SHEET_SQL_SYSTEM_PROMPT already use in dashboard_planner.py /
# report_planner.py (numbered JSON list in, title-keyed JSON list out).
BATCH_REPAIR_SYSTEM_PROMPT = SQL_GENERATOR_SYSTEM_PROMPT + """

## Batch Repair Mode
You are given a JSON list of Trino SQL queries that each need fixing or reviewing. Every
item carries a "mode":

- "error": the SQL FAILED to execute — the "error" field carries the EXACT Trino error.
  Fix the broken identifier(s) (most often a column/table name that doesn't exist). Map it
  to the correct name that appears VERBATIM in the schema context. Only use names present
  in the schema context — never invent one.
- "zero_rows": the SQL EXECUTED SUCCESSFULLY but returned zero rows. Look for a filter
  literal that likely mismatches how values are actually stored (e.g. 'US' vs
  'United States', 'ACTIVE' vs 'active') and correct it based on the schema context (sample
  values, column names, patterns). If zero rows is GENUINELY CORRECT for this query (e.g.
  "orders from next year" — none exist yet), return the SQL UNCHANGED.

For every item, preserve the query's original intent and result SHAPE exactly — same
number/kind of output columns, same aggregation, same ordering — so it still fits its
original chart type. Fix/review ALL items in this single response — do not skip any, do
not ask follow-up questions. If an item genuinely cannot be fixed from the available
schema, return your closest attempt and a low confidence explanation.

Respond ONLY with JSON:
{"repairs": [
  {"title": "<same title as given>", "sql": "<corrected (or unchanged) Trino SELECT SQL>",
   "explanation": "<what you changed, or 'unchanged' if nothing needed fixing>"},
  ...
]}
The "repairs" array must have exactly the same number of entries, in the same order, as the
items you were given.
"""


async def _repair_widgets_batch(
    provider, schema_context: str, items: list[RepairBatchItem]
) -> list[dict]:
    """One LLM call that repairs/reviews every item in the batch.

    Returns a list of {"sql", "explanation"} dicts aligned to `items` by title
    first (the batch prompt asks for the same "title" back), falling back to
    position for any entry with no/duplicate title match — the exact
    tie-breaking logic _generate_widget_sql_batch (dashboard_planner.py) /
    _generate_sheet_sql_batch (report_planner.py) already use.
    """
    briefs = [
        {
            "title": it.title or f"Item {i + 1}",
            "chart_type": it.chart_type or "table",
            "mode": it.mode,
            "sql": it.sql,
            "error": it.error or "(no error message provided)",
        }
        for i, it in enumerate(items)
    ]
    user_prompt = (
        f"Schema context:\n{schema_context or '(none provided)'}\n\n"
        f"Items needing repair (return exactly {len(briefs)} entries, same order):\n"
        + json.dumps(briefs, indent=2)
    )

    result = await provider.generate_json(BATCH_REPAIR_SYSTEM_PROMPT, user_prompt, max_tokens=8000)
    repairs = result.get("repairs") or []

    if len(repairs) != len(briefs):
        logger.warning(
            f"Batched repair returned {len(repairs)} entries for {len(briefs)} items "
            "requested — aligning by title/position."
        )

    by_title: dict[str, dict] = {}
    for r in repairs:
        t = (r.get("title") or "").strip()
        if t and t not in by_title:
            by_title[t] = r

    out = []
    for i, brief in enumerate(briefs):
        entry = by_title.get(brief["title"]) or (repairs[i] if i < len(repairs) else {})
        out.append({
            "sql": (entry.get("sql") or "").strip(),
            "explanation": entry.get("explanation") or "",
        })
    return out


@app.post("/api/repair-widgets-batch", response_model=RepairWidgetsBatchResponse)
async def repair_widgets_batch(request: RepairWidgetsBatchRequest) -> RepairWidgetsBatchResponse:
    """
    Repair/review a whole batch of failing widgets/sheets in ONE context-bundle
    build + ONE LLM call — the batched sibling of /api/repair-widget. Core API
    collects every probe failure across a plan (dashboard or report) and calls
    this ONCE instead of one /api/repair-widget round trip per failing item,
    each of which today rebuilds a full-catalog context bundle.

    Same two per-item modes as /api/repair-widget ("error" / "zero_rows"); a
    batch may mix modes across items. If an individual item's repaired SQL
    fails the deterministic safety gate, that item's ORIGINAL sql is returned
    unchanged (changed=False) instead of failing the whole batch — the caller's
    re-probe naturally treats it as still-broken and can fall back to
    /api/repair-widget for just that one item.

    Architecture boundary unchanged: this NEVER executes SQL. The Core API
    re-verifies every returned SQL against Trino before using it.
    """
    if not request.items:
        return RepairWidgetsBatchResponse(results=[])

    if _widget_sql_provider is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "SQL repair unavailable — no LLM provider configured "
                "(LLM_API_KEY / GOOGLE_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY, "
                "whichever matches the configured provider)"
            ),
        )

    # Same full-catalog approach as /api/repair-widget: no schema-RAG trim, no
    # few-shot examples (question=None) — the correct identifier must always
    # be in scope, and this is ONE bundle build shared across the whole batch
    # (the entire point of batching).
    bundle = await build_context_bundle(
        None,
        provider=_embed_provider,
        datasets=request.datasets or None,
        include_examples=False,
    )
    schema_context = render_extra_context(bundle) or "(no schema context provided)"

    try:
        repairs = await _repair_widgets_batch(_widget_sql_provider, schema_context, request.items)
    except Exception as e:
        logger.error(f"Batched widget SQL repair failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Batched SQL repair failed: {e}")

    results = []
    for item, repair in zip(request.items, repairs):
        candidate_sql = repair["sql"]
        if candidate_sql:
            check = validate_and_fix_sql(candidate_sql)
        else:
            check = {"is_valid": False, "fixed_sql": item.sql, "issues": ["repair produced no SQL"]}

        if not check["is_valid"]:
            logger.warning(
                f"Batched repair for '{item.title or '(untitled)'}' failed safety validation "
                f"({check['issues']}) — keeping original SQL for the caller's single-item fallback"
            )
            results.append(RepairBatchResult(
                title=item.title, sql=item.sql, changed=False, explanation="",
            ))
            continue

        final_sql = check["fixed_sql"]
        results.append(RepairBatchResult(
            title=item.title,
            sql=final_sql,
            changed=final_sql.strip() != item.sql.strip(),
            explanation=repair.get("explanation") or "",
        ))

    logger.info(
        f"Batched repair: {len(results)} item(s), {sum(1 for r in results if r.changed)} changed"
    )
    return RepairWidgetsBatchResponse(results=results)


# /api/invalidate-cache removed in Step 1b — the metadata-version watcher
# (enrichment.watch_metadata_version) now clears caches and re-runs enrichment
# on its own whenever metadata_state.version moves.
# /api/reindex-schemas removed in PR2 — ops escape hatch is now:
#   UPDATE metadata_state SET version = version + 1;
# which triggers the enrichment watcher within the poll interval.


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
