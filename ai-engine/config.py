from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Model for the two top-level deep agents only (query planner orchestrator,
    # dashboard designer). Every other LLM call in this service (fast path,
    # schema-analyst, sql-generator, widget repair, batched widget SQL) uses
    # the cheaper *_model settings below — a single dashboard brief can fan
    # out into a dozen+ calls, and running all of them on the frontier model
    # is what was blowing through OpenAI's per-minute rate limit.
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
        default="gpt-4o-mini",
        description=(
            "OpenAI model used for the fast-path single-shot attempt and for "
            "widget SQL repair. gpt-4o answers ~1s faster on this workload "
            "(~2s vs ~3s), but gpt-4o-mini has a much higher rate-limit tier "
            "for the same account — worth the latency to avoid 429s."
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
    sql_generator_model: str = Field(
        default="openai:gpt-4o",
        description=(
            "deepagents model string for the sql-generator subagent (query "
            "planner's /api/plan full path). Kept on the frontier model — "
            "unlike schema-analyst, this step does real reasoning (e.g. "
            "matching a Trino CROSS JOIN UNNEST alias list to a nested "
            "Elasticsearch row type), and gpt-4o-mini measurably produced "
            "invalid SQL on deeply-nested array fields."
        ),
    )
    dashboard_widget_sql_model: str = Field(
        default="gpt-4o",
        description=(
            "Plain OpenAI model id (not a deepagents string) for the batched "
            "per-dashboard widget-SQL call and widget-SQL repair — same "
            "reasoning-quality tradeoff as sql_generator_model above."
        ),
    )

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = Settings()
