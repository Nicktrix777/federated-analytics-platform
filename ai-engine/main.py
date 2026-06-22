"""
AI Engine — FastAPI Application

This service is the ONLY place where LLM calls happen.
It accepts a natural language question + schema context,
calls the configured LLM, and returns a validated QueryPlan.

Architecture boundaries enforced here:
  1. This service has NO database write access (read-only metadata)
  2. It NEVER executes SQL or connects to Trino
  3. It NEVER calls the Core API or Query Service
  4. It is stateless — no sessions, no persistent state

The Core API is responsible for:
  - Deciding WHEN to call this service (AI feature flag)
  - Validating the returned QueryPlan BEFORE execution
  - Routing to the Query Service
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from models import PlanRequest, QueryPlan, ErrorResponse
from prompt_builder import build_system_prompt, build_user_prompt
from metadata_client import get_datasets, invalidate_metadata_cache

from llm.base import BaseLLMProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── LLM Provider Factory ──────────────────────────────────────
def create_llm_provider() -> BaseLLMProvider:
    """Create the configured LLM provider."""
    provider = settings.llm_provider.lower()
    logger.info(f"Initializing LLM provider: {provider}")

    if provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not set but LLM_PROVIDER=openai")
        from llm.openai_provider import OpenAIProvider

        return OpenAIProvider(
            api_key=settings.openai_api_key, model=settings.openai_model
        )

    elif provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is not set but LLM_PROVIDER=anthropic")
        from llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=settings.anthropic_api_key, model=settings.anthropic_model
        )

    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER: {provider}. Must be 'openai' or 'anthropic'"
        )


# ── App Lifecycle ─────────────────────────────────────────────
llm_provider: Optional[BaseLLMProvider] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global llm_provider
    try:
        llm_provider = create_llm_provider()
        logger.info("AI Engine ready")
    except ValueError as e:
        logger.warning(f"LLM provider init failed: {e}. AI mode will return errors.")
        llm_provider = None
    yield
    logger.info("AI Engine shutting down")


# ── FastAPI App ───────────────────────────────────────────────
app = FastAPI(
    title="Federated Analytics Platform — AI Engine",
    description="Converts natural language questions into structured Trino query plans",
    version="1.0.0-poc",
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
        "llm_provider": settings.llm_provider,
        "llm_ready": llm_provider is not None,
        "model": (
            settings.openai_model
            if settings.llm_provider == "openai"
            else settings.anthropic_model
        ),
    }


# ── Plan Generation ────────────────────────────────────────────
@app.post("/api/plan", response_model=QueryPlan)
async def generate_plan(request: PlanRequest) -> QueryPlan:
    """
    Convert a natural language question into a structured Trino query plan.

    The response is a validated QueryPlan containing:
    - sql: A Trino-compatible SELECT statement
    - steps: Reasoning steps (which tables are involved)
    - confidence: How confident the LLM is
    - explanation: Human-readable explanation

    The Core API validates this response AGAIN before executing it.
    """
    global llm_provider

    if llm_provider is None:
        raise HTTPException(
            status_code=503,
            detail="LLM provider is not configured or failed to initialize. Check API keys.",
        )

    # If the request includes datasets, use those; otherwise fetch from metadata DB
    datasets = request.datasets
    if not datasets:
        logger.info("No datasets in request, fetching from metadata DB")
        datasets = await get_datasets()

    if not datasets:
        logger.warning("No dataset metadata available — LLM will have limited context")

    # Build prompts
    system_prompt = build_system_prompt(datasets)
    user_prompt = build_user_prompt(request)

    logger.info(f"Generating query plan for: {request.question[:100]}")

    try:
        plan = await llm_provider.generate_plan(
            system_prompt, user_prompt, request.question
        )
        logger.info(
            f"Generated plan with confidence={plan.confidence:.2f}, sql_len={len(plan.sql)}"
        )
        return plan

    except ValueError as e:
        # Validation errors from Pydantic or our checks
        logger.error(f"Plan validation failed: {e}")
        raise HTTPException(status_code=422, detail=str(e))

    except Exception as e:
        logger.error(f"LLM call failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"LLM provider call failed: {str(e)}",
        )



# ── Cache Invalidation ─────────────────────────────────────────
@app.post("/api/invalidate-cache")
async def invalidate_cache_endpoint():
    """
    Force metadata cache refresh.
    Called by Core API after a CSV/Excel upload creates a new table
    so the AI Engine immediately knows about the new data source.
    """
    invalidate_metadata_cache()
    return {"status": "cache invalidated", "message": "Next plan request will re-fetch all schemas"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port)
