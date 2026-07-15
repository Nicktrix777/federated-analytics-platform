"""
AI Engine — FastAPI Application (Production)

This service is the ONLY place where LLM calls happen.
Two paths produce a QueryPlan:
  - Fast path: one cheap single-shot LLM call using the schema context Core
    API already sent (prompt_builder + llm/openai_provider). Used for the
    common case — a question the pre-loaded context can already answer.
  - Full path: the deepagents multi-agent pipeline (schema-analyst +
    sql-generator), used only when the fast path is unavailable, fails
    validation, or comes back with low confidence. The fast path's rejected
    draft (SQL + why it failed) is handed off to the full pipeline so the
    escalation repairs the draft instead of starting from scratch.

Both paths run their SQL through the same deterministic safety/Trino-
compatibility gate (agents.tools.validation_tools.validate_and_fix_sql)
before it's returned — there's no separate "validator" LLM call.

Streaming: every pipeline emits structured progress events (stage changes,
every LLM call, every tool call, subagent hand-offs — see events.py). The
/api/plan/stream and /api/dashboard-plan/stream endpoints expose those as SSE
for the Core API to proxy to the frontend; the non-streaming endpoints still
exist and return the same final JSON. Event contract: docs/sse-events.md.

Architecture boundaries enforced here:
  1. This service has NO database write access (read-only metadata)
  2. It NEVER executes SQL or connects to Trino for queries
  3. It NEVER calls the Core API or Query Service
  4. It is stateless — no sessions, no persistent state per request
  5. All tool calls go through isolated subagent context windows

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
    DashboardPlan,
    DashboardPlanRequest,
    PlanRequest,
    QueryPlan,
    RepairWidgetRequest,
    RepairWidgetResponse,
)
from agents.orchestrator import create_query_planner, generate_query_plan
from agents.dashboard_planner import create_dashboard_designer, generate_dashboard_plan
from agents.subagents.sql_generator import SQL_GENERATOR_SYSTEM_PROMPT
from agents.tools.validation_tools import validate_and_fix_sql
from agents.tools._cache import clear_all as clear_tool_cache
from agents.tools._common import close_shared_clients
from agents.tools.metadata_tools import (
    _async_get_relationships,
    _async_get_patterns,
)
from events import EventEmitter, NullEmitter
from prompt_builder import build_system_prompt, build_user_prompt
from schema_render import (
    categorical_values_block,
    column_sample_suffix,
    describe_column,
    parse_samples,
)
from llm.openai_provider import OpenAIProvider
from embeddings import (
    reindex_datasets,
    reindex_examples,
    retrieve_relevant_dataset_ids,
    retrieve_similar_examples,
)
from sampling import reindex_samples

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Global Agent Instances ────────────────────────────────────
_agent = None
_dashboard_agent = None

# Fast-path provider is created ONCE at startup and reused for every request.
# The AsyncOpenAI client inside it keeps a persistent connection pool to
# api.openai.com — constructing it per request forced a fresh TCP+TLS
# handshake each time, which alone added several hundred ms per plan.
_fast_provider: Optional[OpenAIProvider] = None

# Separate provider (frontier model) for widget SQL work — the batched
# per-dashboard widget-SQL call and widget-SQL repair. This is the one step
# that does real reasoning (matching a Trino CROSS JOIN UNNEST alias list to
# a nested row type); gpt-4o-mini measurably produced invalid SQL on deeply
# nested Elasticsearch array fields, so it stays on gpt-4o even though
# fast-path/schema-analyst use the cheaper model.
_widget_sql_provider: Optional[OpenAIProvider] = None

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


async def _reindex_schemas_safe() -> None:
    """Refresh sample values, then re-embed dataset schemas for RAG. Best-effort.

    Runs as a background task (startup, and after every cache invalidation) so a
    hiccup never fails a request. Sampling runs FIRST and unconditionally (it
    feeds prompts regardless of RAG) so the freshly-sampled values are baked into
    the embeddings that follow.
    """
    try:
        await reindex_samples()
    except Exception as e:
        logger.warning(f"Sample-value discovery failed: {e}")

    if not settings.schema_rag_enabled or _fast_provider is None:
        return
    try:
        await reindex_datasets(_fast_provider)
    except Exception as e:
        logger.warning(f"Schema embedding reindex failed (RAG will use full catalog): {e}")


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
    if not settings.few_shot_rag_enabled or _fast_provider is None:
        return
    if _examples_reindex_lock.locked():
        return
    async with _examples_reindex_lock:
        try:
            await reindex_examples(_fast_provider, settings.few_shot_reindex_limit)
        except Exception as e:
            logger.warning(f"Example embedding reindex failed (few-shots will use recency): {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent, _dashboard_agent, _fast_provider, _widget_sql_provider
    try:
        # Set API keys as environment variables for langchain providers
        if settings.openai_api_key:
            os.environ["OPENAI_API_KEY"] = settings.openai_api_key
            _fast_provider = OpenAIProvider(
                api_key=settings.openai_api_key,
                model=settings.fast_path_model,
                max_retries=settings.llm_max_retries,
                timeout=settings.llm_timeout_seconds,
            )
            _widget_sql_provider = OpenAIProvider(
                api_key=settings.openai_api_key,
                model=settings.dashboard_widget_sql_model,
                max_retries=settings.llm_max_retries,
                timeout=settings.llm_timeout_seconds,
            )
        if settings.anthropic_api_key:
            os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

        _agent = create_query_planner(model=settings.llm_model)
        _dashboard_agent = create_dashboard_designer(model=settings.llm_model)
        logger.info(f"Query planner and dashboard designer ready (model={settings.llm_model})")

        # Warm the RAG indexes in the background so the first request can already
        # retrieve relevant datasets and similar few-shots. Non-blocking: startup
        # doesn't wait, and a failure just leaves the pipelines on their fallbacks
        # (full catalog / recency few-shots).
        if settings.schema_rag_enabled:
            _spawn_stream_task(_reindex_schemas_safe())
        if settings.few_shot_rag_enabled:
            _spawn_stream_task(_reindex_examples_safe())
    except Exception as e:
        logger.warning(f"Agent init failed: {e}. AI mode will return errors.")
        _agent = None
        _dashboard_agent = None
    yield
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


# ── Helpers ────────────────────────────────────────────────────
async def _load_relationships() -> list:
    """Curated join relationships from postgres-meta (TTL-cached, deployment-global)."""
    try:
        data = json.loads(await _async_get_relationships())
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Could not load relationships for prompt context: {e}")
        return []


async def _load_examples(question: str) -> list:
    """Few-shot examples for the prompt: semantically similar past queries.

    Prefers the top-k past successful queries most SIMILAR to `question`
    (pgvector). Falls back to the most RECENT successful queries (TTL-cached
    audit-log read) when the example index is empty/unavailable — so a fresh
    deployment behaves exactly as before Phase 2.
    """
    if settings.few_shot_rag_enabled and _fast_provider is not None:
        examples = await retrieve_similar_examples(_fast_provider, question, settings.few_shot_top_k)
        if examples is not None:
            return examples
    try:
        data = json.loads(await _async_get_patterns())
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Could not load query examples for prompt context: {e}")
        return []


def _render_relationships_lines(relationships: list) -> list:
    """Compact one-line-per-join rendering of curated relationships for extra_context."""
    lines = []
    for r in relationships:
        frm = f"{r.get('from_trino_path', '')}.{r.get('from_column', '')}"
        to = f"{r.get('to_trino_path', '')}.{r.get('to_column', '')}"
        join_type = (r.get("join_type") or "INNER").upper()
        line = f"  - {frm} = {to} ({join_type} JOIN)"
        if r.get("cast_expression"):
            line += f" [cast: {r['cast_expression']}]"
        if r.get("description"):
            line += f" — {r['description']}"
        lines.append(line)
    return lines


async def _build_extra_context(datasets) -> Optional[str]:
    """Render pre-loaded dataset metadata + live join relationships for a pipeline prompt.

    Relationships are pulled from postgres-meta (TTL-cached) so a full-pipeline
    run that SKIPS schema-analyst still sees the curated join map instead of
    having to rediscover it. Nothing schema-specific is hardcoded here.
    """
    if not datasets:
        return None
    schema_lines = []
    for ds in datasets:
        schema_lines.append(f"Dataset: {ds.name} ({ds.trino_path})")
        schema_lines.append(f"  Description: {ds.description}")
        for col in ds.columns[:40]:  # generous cap — a missing column invites the LLM to invent one
            # Deeply-nested ROW/ARRAY(ROW) columns are flattened into explicit
            # dotted paths + UNNEST recipes so the model navigates them correctly
            # instead of guessing at the opaque type string. Sample values (real
            # stored values) are attached per leaf so filters use actual literals.
            samples = parse_samples(col.sample_values)
            type_summary, nested = describe_column(col.column_name, col.data_type, samples, max_leaves=24)
            desc = f": {col.description}" if col.description else ""
            schema_lines.append(
                f"  - {col.column_name} ({type_summary}){desc}"
                + column_sample_suffix(col.column_name, samples)
            )
            schema_lines.extend(nested)
        # Truncation-proof list of real values for nested categorical leaves.
        vals_block = categorical_values_block((c.column_name, c.sample_values) for c in ds.columns)
        if vals_block:
            schema_lines.append("  " + vals_block.replace("\n", "\n  "))

    parts = ["Pre-loaded schema context:\n" + "\n".join(schema_lines)]
    relationships = await _load_relationships()
    if relationships:
        parts.append("Known join relationships:\n" + "\n".join(_render_relationships_lines(relationships)))
    return "\n\n".join(parts)


async def _select_relevant_datasets(request, question: str, emitter: EventEmitter):
    """Trim the Core API's full catalog to the datasets relevant to the question.

    Returns `request` unchanged when schema-RAG can't help — disabled, no
    provider, no datasets sent, empty/unavailable index (retrieval returns
    None), nothing matched, or everything matched. Only when retrieval yields a
    strict, non-empty subset do we return a copy with the reduced dataset list,
    which shrinks every downstream prompt (fast path + extra_context). The full
    deepagents pipeline can still reach the whole catalog through its tools, so
    this never hard-limits what a hard question can see.
    """
    if not settings.schema_rag_enabled or _fast_provider is None or not request.datasets:
        return request
    ids = await retrieve_relevant_dataset_ids(_fast_provider, question, settings.schema_rag_top_k)
    if ids is None:
        return request
    filtered = [ds for ds in request.datasets if ds.id in ids]
    if not filtered or len(filtered) == len(request.datasets):
        return request
    await emitter.emit(
        "stage",
        stage="schema_retrieval",
        detail=f"selected {len(filtered)}/{len(request.datasets)} datasets by relevance",
    )
    logger.info(
        f"Schema-RAG selected {len(filtered)}/{len(request.datasets)} datasets: "
        f"{[ds.name for ds in filtered]}"
    )
    return request.model_copy(update={"datasets": filtered})


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
    request: PlanRequest, emitter: EventEmitter
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
        # Relationships + few-shot examples are LIVE metadata — the prompt
        # reshapes itself per deployment. Examples are now the past queries most
        # similar to THIS question (pgvector), with a recency fallback.
        relationships, examples = await asyncio.gather(
            _load_relationships(), _load_examples(request.question)
        )
        system_prompt = build_system_prompt(request.datasets, relationships, examples)
        user_prompt = build_user_prompt(request)
        plan = await _fast_provider.generate_plan(system_prompt, user_prompt, request.question)
    except Exception as e:
        logger.info(f"Fast path call failed, escalating with no draft: {e}")
        await emitter.emit("stage", stage="fast_path_rejected", detail=f"call failed: {e}")
        return None, None
    duration_ms = int((time.monotonic() - call_start) * 1000)
    await emitter.emit("llm", phase="end", agent="fast-path", duration_ms=duration_ms)

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
async def _run_plan_pipeline(request: PlanRequest, emitter: EventEmitter) -> Tuple[QueryPlan, str]:
    """Full plan flow: semaphore → fast path → (escalate w/ hint) → validation.

    Returns (plan, path_used). Raises HTTPException on every failure mode.
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

        # Schema RAG: prompt with only the datasets relevant to the question.
        request = await _select_relevant_datasets(request, request.question, emitter)

        plan = None
        fastpath_hint = None
        path_used = "full"
        if settings.fast_path_enabled and request.datasets:
            plan, fastpath_hint = await _try_fast_path(request, emitter)
            if plan is not None:
                path_used = "fast"

        if plan is None:
            if _agent is None:
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Query planner agent is not initialized. "
                        "Check OPENAI_API_KEY and LLM_MODEL environment variables."
                    ),
                )
            await emitter.emit("stage", stage="pipeline_started", detail="full deepagents pipeline")
            extra_context = await _build_extra_context(request.datasets)
            if request.conversation_context:
                convo = (
                    f"Conversation so far:\n{request.conversation_context}\n"
                    "(The question may be a follow-up referring to these prior turns.)"
                )
                extra_context = f"{convo}\n\n{extra_context}" if extra_context else convo
            if fastpath_hint:
                extra_context = f"{extra_context}\n\n{fastpath_hint}" if extra_context else fastpath_hint
            try:
                plan = await generate_query_plan(_agent, request.question, extra_context, emitter=emitter)
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
@app.post("/api/plan", response_model=QueryPlan)
async def generate_plan(request: PlanRequest) -> QueryPlan:
    """
    Convert a natural language question into a structured Trino query plan.

    Tries the fast single-shot path first (one cheap LLM call using Core
    API's pre-loaded schema context). Only escalates to the full deepagents
    multi-agent pipeline — schema-analyst + sql-generator — when the fast
    path is unavailable, invalid, or low-confidence; the rejected draft is
    handed off as a repair hint.

    The Core API validates this response AGAIN before executing it.
    """
    plan, _ = await _run_plan_pipeline(request, NullEmitter())
    return plan


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
            plan, path_used = await _run_plan_pipeline(request, emitter)
            await emitter.emit("plan", plan=plan.model_dump(), path=path_used)
        except HTTPException as e:
            await emitter.emit("error", detail=e.detail, status_code=e.status_code)
        except Exception as e:
            logger.error(f"Streaming plan pipeline crashed: {e}", exc_info=True)
            await emitter.emit("error", detail=str(e), status_code=500)
        finally:
            await emitter.close()

    _spawn_stream_task(run())
    return StreamingResponse(emitter.iter_sse(), media_type="text/event-stream", headers=_SSE_HEADERS)


# ── Dashboard Pipeline (shared by sync + stream endpoints) ────
async def _run_dashboard_pipeline(request: DashboardPlanRequest, emitter: EventEmitter) -> DashboardPlan:
    if _dashboard_agent is None or _widget_sql_provider is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Dashboard designer agent is not initialized. "
                "Check OPENAI_API_KEY and LLM_MODEL environment variables."
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
        request = await _select_relevant_datasets(request, retrieval_query, emitter)
        extra_context = await _build_extra_context(request.datasets)

        try:
            plan = await generate_dashboard_plan(
                _dashboard_agent,
                request.prompt,
                _widget_sql_provider,
                extra_context=extra_context,
                current_dashboard=request.current_dashboard,
                emitter=emitter,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            logger.error(f"Dashboard designer invocation failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail=f"Dashboard designer failed: {str(e)}")

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


@app.post("/api/repair-widget", response_model=RepairWidgetResponse)
async def repair_widget(request: RepairWidgetRequest) -> RepairWidgetResponse:
    """
    Repair one widget's SQL that failed to execute against Trino.

    The Core API verifies every generated widget against the Query Service
    before persisting it; when one fails (typically a hallucinated column or
    table name) it calls this endpoint with the failing SQL and the exact
    engine error. We do a single focused LLM pass to correct the identifier(s)
    using the schema context, then re-apply the same deterministic safety gate
    the generated SQL goes through.

    Architecture boundary unchanged: this NEVER executes SQL. The Core API
    re-verifies the returned SQL against Trino before persisting it.
    """
    if _widget_sql_provider is None:
        raise HTTPException(
            status_code=503,
            detail="SQL repair unavailable — no OpenAI provider configured (set OPENAI_API_KEY)",
        )

    schema_context = await _build_extra_context(request.datasets) or "(no schema context provided)"
    user_prompt = (
        f"Widget title: {request.title or '(untitled)'}\n"
        f"Chart type: {request.chart_type}\n\n"
        f"Broken SQL:\n{request.sql}\n\n"
        f"Execution error from Trino:\n{request.error or '(no error message provided)'}\n\n"
        f"{schema_context}"
    )

    try:
        fixed_sql = await _widget_sql_provider.repair_sql(REPAIR_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        logger.error(f"Widget SQL repair failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"SQL repair failed: {e}")

    # Re-apply the deterministic safety/Trino-compat gate to the repaired SQL.
    result = validate_and_fix_sql(fixed_sql)
    if not result["is_valid"]:
        raise HTTPException(
            status_code=422,
            detail=f"Repaired SQL failed safety validation: {result['issues']}",
        )

    final_sql = result["fixed_sql"]
    logger.info(
        f"Repaired widget '{request.title or '(untitled)'}' "
        f"(changed={final_sql.strip() != request.sql.strip()})"
    )
    return RepairWidgetResponse(
        sql=final_sql,
        changed=final_sql.strip() != request.sql.strip(),
        explanation="",
    )


# ── Cache Invalidation ─────────────────────────────────────────
@app.post("/api/invalidate-cache")
async def invalidate_cache_endpoint():
    """
    Force metadata cache refresh.
    Called by Core API after a CSV/Excel upload or new data source registration
    so the AI Engine immediately picks up the new schema. Also kicks off a
    background schema-embedding reindex so RAG retrieval reflects the new/changed
    datasets (only rows whose text changed are re-embedded).
    """
    clear_tool_cache()
    if settings.schema_rag_enabled:
        _spawn_stream_task(_reindex_schemas_safe())
    return {
        "status": "cache invalidated",
        "message": "Next plan request will re-fetch all schemas; schema embeddings reindexing in background",
    }


@app.post("/api/reindex-schemas")
async def reindex_schemas_endpoint():
    """
    (Re)embed dataset schemas AND few-shot query examples for RAG, synchronously.

    Idempotent — only datasets whose embed text changed and successful queries
    not yet embedded are processed. Use this to force a rebuild (e.g. after a
    manual DB migration); routine schema refreshes also happen via
    /api/invalidate-cache, and examples are folded in opportunistically per plan.
    """
    if _fast_provider is None:
        raise HTTPException(
            status_code=503,
            detail="Reindex unavailable — no OpenAI provider configured (set OPENAI_API_KEY)",
        )
    result = {"status": "ok"}
    try:
        if settings.schema_rag_enabled:
            result["schemas"] = await reindex_datasets(_fast_provider)
        if settings.few_shot_rag_enabled:
            result["examples"] = await reindex_examples(_fast_provider, settings.few_shot_reindex_limit)
    except Exception as e:
        logger.error(f"Reindex failed: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail=f"Reindex failed: {e}")
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
