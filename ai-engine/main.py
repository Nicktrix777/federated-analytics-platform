"""
AI Engine — FastAPI Application (Production)

This service is the ONLY place where LLM calls happen.
Now powered by deepagents with specialized subagents for:
  - Schema analysis (discovers ALL registered data sources dynamically)
  - SQL generation (writes Trino-compatible federated SQL)
  - SQL validation (safety + syntax enforcement)

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

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from models import PlanRequest, QueryPlan, ErrorResponse
from metadata_client import get_datasets, invalidate_metadata_cache
from agents.orchestrator import create_query_planner, generate_query_plan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── Global Agent Instance ─────────────────────────────────────
_agent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _agent
    try:
        # Set API keys as environment variables for langchain providers
        if settings.openai_api_key:
            os.environ["OPENAI_API_KEY"] = settings.openai_api_key
        if settings.anthropic_api_key:
            os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key

        _agent = create_query_planner(model=settings.llm_model)
        logger.info(f"Query planner ready (model={settings.llm_model})")
    except Exception as e:
        logger.warning(f"Agent init failed: {e}. AI mode will return errors.")
        _agent = None
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
    }


# ── Plan Generation ────────────────────────────────────────────
@app.post("/api/plan", response_model=QueryPlan)
async def generate_plan(request: PlanRequest) -> QueryPlan:
    """
    Convert a natural language question into a structured Trino query plan
    using the deepagents multi-agent orchestration system.

    The agent pipeline:
      1. schema-analyst discovers relevant tables from all registered sources
      2. sql-generator writes Trino SQL using the schema context
      3. sql-validator validates safety and syntax

    The Core API validates this response AGAIN before executing it.
    """
    global _agent

    if _agent is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Query planner agent is not initialized. "
                "Check OPENAI_API_KEY and LLM_MODEL environment variables."
            ),
        )

    # Build extra context from explicitly provided datasets (backward compat
    # with Core API sending schema context in the request body)
    extra_context = None
    if request.datasets:
        schema_lines = []
        for ds in request.datasets:
            schema_lines.append(f"Dataset: {ds.name} ({ds.trino_path})")
            schema_lines.append(f"  Description: {ds.description}")
            for col in ds.columns[:10]:  # limit to avoid bloat
                schema_lines.append(
                    f"  - {col.column_name} ({col.data_type}): {col.description}"
                )
        extra_context = "Pre-loaded schema context:\n" + "\n".join(schema_lines)

    logger.info(f"Generating plan for: {request.question[:100]}")

    try:
        plan = await generate_query_plan(_agent, request.question, extra_context)
        logger.info(
            f"Plan generated: confidence={plan.confidence:.2f}, "
            f"sql_len={len(plan.sql)}, steps={len(plan.steps)}"
        )
        return plan

    except ValueError as e:
        logger.error(f"Plan validation failed: {e}")
        raise HTTPException(status_code=422, detail=str(e))

    except Exception as e:
        logger.error(f"Agent invocation failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Query planner failed: {str(e)}",
        )


# ── Cache Invalidation ─────────────────────────────────────────
@app.post("/api/invalidate-cache")
async def invalidate_cache_endpoint():
    """
    Force metadata cache refresh.
    Called by Core API after a CSV/Excel upload or new data source registration
    so the AI Engine immediately picks up the new schema.
    """
    invalidate_metadata_cache()
    return {
        "status": "cache invalidated",
        "message": "Next plan request will re-fetch all schemas from all sources",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.port)
