from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # LLM provider — always gpt-4o via deepagents model string
    llm_model: str = Field(
        default="openai:gpt-4o",
        description="deepagents model string e.g. 'openai:gpt-4o' or 'anthropic:claude-sonnet-4-6'"
    )

    # Still needed for the underlying langchain provider
    openai_api_key: str = Field(default="", description="OpenAI API key")
    anthropic_api_key: str = Field(default="", description="Anthropic API key")

    # PostgreSQL metadata DB
    postgres_meta_host: str = Field(default="localhost")
    postgres_meta_port: int = Field(default=5432)
    postgres_meta_db: str = Field(default="analytics_meta")
    postgres_meta_user: str = Field(default="meta_user")
    postgres_meta_password: str = Field(default="meta_pass_2024")

    # Trino connection (for schema introspection)
    trino_host: str = Field(default="localhost", description="Trino coordinator hostname")
    trino_port: int = Field(default=8080, description="Trino HTTP port")

    # Service port
    port: int = Field(default=8082)

    # ── Fast path (Phase 3) ──────────────────────────────────
    # Try a single cheap LLM call using the schema context Core API already
    # sent before falling back to the full multi-agent pipeline.
    fast_path_enabled: bool = Field(
        default=True,
        description="Try a single-shot LLM call for simple questions before the full deepagents pipeline",
    )
    fast_path_model: str = Field(
        default="gpt-4o",
        description=(
            "OpenAI model used for the fast-path single-shot attempt. "
            "Measured on this workload gpt-4o answers ~1s faster than gpt-4o-mini "
            "(~2s vs ~3s); use gpt-4o-mini only to cut cost, not latency."
        ),
    )
    fast_path_confidence_threshold: float = Field(
        default=0.55,
        description="Fast-path plans below this confidence escalate to the full pipeline",
    )

    # ── LLM resilience (Phase 1) ─────────────────────────────
    llm_max_retries: int = Field(
        default=5,
        description="Max retries with exponential backoff on 429/5xx from the LLM provider",
    )
    llm_timeout_seconds: float = Field(
        default=60.0,
        description="Per-call timeout (seconds) for LLM requests",
    )

    # ── Concurrency control (Phase 1) ────────────────────────
    # Caps how many /api/plan pipelines run at once so bursts of requests
    # don't all hit OpenAI's rate limits simultaneously.
    ai_max_concurrent_plans: int = Field(
        default=4,
        description="Max concurrent /api/plan pipelines (fast or full) running at once",
    )
    ai_queue_timeout_seconds: float = Field(
        default=45.0,
        description="Max time a request waits for a concurrency slot before failing fast with 503",
    )

    # ── Schema/metadata tool cache (Phase 1) ─────────────────
    schema_tool_cache_ttl_seconds: float = Field(
        default=60.0,
        description="TTL for Trino/postgres-meta lookups used by agent tools (schema-analyst, etc.)",
    )

    # ── Model tiering (Phase 4) ───────────────────────────────
    schema_analyst_model: str = Field(
        default="openai:gpt-4o-mini",
        description="deepagents model string for the schema-analyst subagent (cheap; mostly tool-calling, not reasoning-heavy)",
    )

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = Settings()
