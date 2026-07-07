"""
AI Engine — FastAPI Application (Production)

This service is the ONLY place where LLM calls happen.
Two paths produce a QueryPlan:
  - Fast path: one cheap single-shot LLM call using the schema context Core
    API already sent (prompt_builder + llm/openai_provider). Used for the
    common case — a question the pre-loaded context can already answer.
  - Full path: the deepagents multi-agent pipeline (schema-analyst +
    sql-generator), used only when the fast path is unavailable, fails
    validation, or comes back with low confidence.

Both paths run their SQL through the same deterministic safety/Trino-
compatibility gate (agents.tools.validation_tools.validate_and_fix_sql)
before it's returned — there's no separate "validator" LLM call.

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
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from models import (
    DashboardPlan,
    DashboardPlanRequest,
    ErrorResponse,
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
from prompt_builder import build_system_prompt, build_user_prompt
from llm.openai_provider import OpenAIProvider

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

# Caps how many /api/plan pipelines (fast or full) run at once so bursts of
# concurrent requests don't all hit OpenAI's rate limits simultaneously.
_plan_semaphore = asyncio.Semaphore(settings.ai_max_concurrent_plans)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent, _dashboard_agent, _fast_provider
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
        if settings.anthropic_api_key:
            os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

        _agent = create_query_planner(model=settings.llm_model)
        _dashboard_agent = create_dashboard_designer(model=settings.llm_model)
        logger.info(f"Query planner and dashboard designer ready (model={settings.llm_model})")
    except Exception as e:
        logger.warning(f"Agent init failed: {e}. AI mode will return errors.")
        _agent = None
        _dashboard_agent = None
    yield
    logger.info("AI Engine shutting down")


# ── FastAPI App ───────────────────────────────────────────────
app = FastAPI(
    title="Federated Analytics Platform — AI Engine",
    description=(
        "Converts natural language questions into structured Trino query plans "
        "using a deepagents multi-agent architecture."
    ),
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health Check ──────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "ai-engine",
        "version": "2.0.0",
        "agent": "deepagents",
        "model": settings.llm_model,
        "agent_ready": _agent is not None,
        "fast_path_enabled": settings.fast_path_enabled,
        "fast_path_model": settings.fast_path_model,
    }


# ── Helpers ────────────────────────────────────────────────────
def _build_extra_context(datasets) -> Optional[str]:
    """Render Core API's pre-loaded dataset metadata for an agent pipeline's prompt."""
    if not datasets:
        return None
    schema_lines = []
    for ds in datasets:
        schema_lines.append(f"Dataset: {ds.name} ({ds.trino_path})")
        schema_lines.append(f"  Description: {ds.description}")
        for col in ds.columns[:40]:  # generous cap — a missing column invites the LLM to invent one
            schema_lines.append(
                f"  - {col.column_name} ({col.data_type}): {col.description}"
            )
    return "Pre-loaded schema context:\n" + "\n".join(schema_lines)


def _apply_validation(plan: QueryPlan) -> QueryPlan:
    """Deterministic safety/auto-fix gate — the shared final step for both paths."""
    result = validate_and_fix_sql(plan.sql)
    if not result["is_valid"]:
        raise ValueError(f"Generated SQL failed validation: {result['issues']}")
    adjusted_confidence = max(0.0, min(1.0, plan.confidence + result["confidence_adjustment"]))
    return plan.model_copy(update={"sql": result["fixed_sql"], "confidence": adjusted_confidence})


async def _try_fast_path(request: PlanRequest) -> Optional[QueryPlan]:
    """
    One cheap single-shot LLM call for questions the pre-loaded schema context
    can already answer. Returns None (caller escalates to the full pipeline)
    when there's no API key, the call errors, the SQL fails validation, or
    confidence comes back below settings.fast_path_confidence_threshold.
    """
    if _fast_provider is None:
        return None

    try:
        system_prompt = build_system_prompt(request.datasets)
        user_prompt = build_user_prompt(request)
        plan = await _fast_provider.generate_plan(system_prompt, user_prompt, request.question)
        plan = _apply_validation(plan)
    except Exception as e:
        logger.info(f"Fast path did not produce a usable plan, escalating: {e}")
        return None

    if plan.confidence < settings.fast_path_confidence_threshold:
        logger.info(
            f"Fast path confidence {plan.confidence:.2f} below threshold "
            f"{settings.fast_path_confidence_threshold}, escalating"
        )
        return None

    return plan


# ── Plan Generation ────────────────────────────────────────────
@app.post("/api/plan", response_model=QueryPlan)
async def generate_plan(request: PlanRequest) -> QueryPlan:
    """
    Convert a natural language question into a structured Trino query plan.

    Tries the fast single-shot path first (one cheap LLM call using Core
    API's pre-loaded schema context). Only escalates to the full deepagents
    multi-agent pipeline — schema-analyst + sql-generator — when the fast
    path is unavailable, invalid, or low-confidence.

    The Core API validates this response AGAIN before executing it.
    """
    global _agent

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

        plan = None
        path_used = "full"
        if settings.fast_path_enabled and request.datasets:
            plan = await _try_fast_path(request)
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
            extra_context = _build_extra_context(request.datasets)
            try:
                plan = await generate_query_plan(_agent, request.question, extra_context)
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
    if _dashboard_agent is None:
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
        extra_context = _build_extra_context(request.datasets)

        try:
            plan = await generate_dashboard_plan(
                _dashboard_agent,
                request.prompt,
                extra_context=extra_context,
                current_dashboard=request.current_dashboard,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            logger.error(f"Dashboard designer invocation failed: {e}", exc_info=True)
            raise HTTPException(status_code=502, detail=f"Dashboard designer failed: {str(e)}")

        # Deterministic safety gate per widget; drop the ones that fail.
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
    if _fast_provider is None:
        raise HTTPException(
            status_code=503,
            detail="SQL repair unavailable — no OpenAI provider configured (set OPENAI_API_KEY)",
        )

    schema_context = _build_extra_context(request.datasets) or "(no schema context provided)"
    user_prompt = (
        f"Widget title: {request.title or '(untitled)'}\n"
        f"Chart type: {request.chart_type}\n\n"
        f"Broken SQL:\n{request.sql}\n\n"
        f"Execution error from Trino:\n{request.error or '(no error message provided)'}\n\n"
        f"{schema_context}"
    )

    try:
        fixed_sql = await _fast_provider.repair_sql(REPAIR_SYSTEM_PROMPT, user_prompt)
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
    so the AI Engine immediately picks up the new schema.
    """
    clear_tool_cache()
    return {
        "status": "cache invalidated",
        "message": "Next plan request will re-fetch all schemas from all sources",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
