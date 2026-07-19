from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # ── Model-agnostic LLM configuration ─────────────────────
    # This service is provider-neutral. Two independent mechanisms, both
    # switchable by env vars alone (no code changes):
    #
    #   1. deepagents pipeline (orchestrator, dashboard designer, and the
    #      schema-analyst / sql-generator subagents) — driven by langchain
    #      model STRINGS with a provider prefix: `google_genai:gemini-flash-latest`,
    #      `openai:gpt-4o`, `anthropic:claude-sonnet-5`, `ollama:llama3.1`, etc.
    #      init_chat_model resolves the right client from the prefix.
    #
    #   2. custom single-shot providers (fast path, widget SQL, repair) +
    #      embeddings — driven by an OpenAI-COMPATIBLE client. Point llm_base_url
    #      at any OpenAI-compatible endpoint (Gemini, Groq, OpenRouter, Ollama,
    #      or real OpenAI when blank) and give it llm_api_key.
    #
    # Default target: Google Gemini free tier (aistudio.google.com — free key,
    # no card). The deepagents path uses the native google_genai integration
    # (GOOGLE_API_KEY); the custom path uses Gemini's OpenAI-compatible endpoint.
    # Switch providers by editing only the *_model strings + the keys/base_url.
    #
    # The cheaper *_model settings exist because a single dashboard brief can fan
    # out into a dozen+ calls — running all of them on the frontier tier is what
    # was blowing through per-minute rate limits.
    llm_model: str = Field(
        default="google_genai:gemini-flash-latest",
        description="deepagents model string, e.g. 'google_genai:gemini-flash-latest', 'openai:gpt-4o', 'anthropic:claude-sonnet-5'",
    )

    # Provider API keys. Only the one(s) matching your chosen models need a value.
    google_api_key: str = Field(default="", description="Google AI Studio (Gemini) key — deepagents google_genai path")
    anthropic_api_key: str = Field(default="", description="Anthropic API key — deepagents anthropic: path")
    openai_api_key: str = Field(default="", description="OpenAI API key — deepagents openai: path / real-OpenAI custom providers")

    # Custom OpenAI-compatible provider (fast path, widget SQL, repair, embeddings).
    # base_url blank → real api.openai.com. For Gemini free tier, set it to
    # https://generativelanguage.googleapis.com/v1beta/openai/ and use the Gemini key.
    llm_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta/openai/",
        description="OpenAI-compatible base URL for the custom providers + embeddings (blank = real OpenAI)",
    )
    llm_api_key: str = Field(
        default="",
        description="API key for the OpenAI-compatible custom providers; falls back to google_api_key then openai_api_key",
    )

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
        default="gemini-flash-lite-latest",
        description=(
            "Plain model id (NO provider prefix) for the fast-path single-shot "
            "attempt, sent via the OpenAI-compatible custom provider. Cheap tier "
            "— high-volume, latency-sensitive. Escalates to the full deepagents "
            "pipeline when rejected/low-confidence. (e.g. gemini-flash-lite-latest, "
            "gpt-4o-mini, llama-3.3-70b-versatile)."
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

    # ── Agent depth control ──────────────────────────────────
    agent_recursion_limit: int = Field(
        default=20,
        description=(
            "LangGraph recursion_limit for deepagent runs — caps how many "
            "graph steps (LLM turns + tool executions) one pipeline can take "
            "before erroring, so a wandering agent can't spin for minutes"
        ),
    )

    # ── Clarification depth cap (PR5) ────────────────────────
    # When the replayed conversation window already contains this many
    # clarification messages, the pipeline appends a prompt instruction
    # forcing a best-effort plan instead of yet another question.
    clarification_depth_cap: int = Field(
        default=2,
        description=(
            "Max clarification messages in the replayed window before forcing "
            "a best-effort plan. Set higher to allow more back-and-forth."
        ),
    )

    # ── Schema/metadata tool cache (Phase 1) ─────────────────
    schema_tool_cache_ttl_seconds: float = Field(
        default=60.0,
        description="TTL for Trino/postgres-meta lookups used by agent tools (schema-analyst, etc.)",
    )

    # ── Metadata-version watcher (Step 1b) ───────────────────
    # The AI Engine polls metadata_state.version instead of being poked. On a
    # change it clears the tool caches and re-runs the enrichment pipeline.
    metadata_version_poll_seconds: float = Field(
        default=5.0,
        description="How often the watcher polls metadata_state.version for changes",
    )
    enrichment_min_interval_seconds: float = Field(
        default=30.0,
        description="Floor between enrichment runs so a burst of metadata writes coalesces into one run",
    )

    # ── Schema RAG (pgvector) ────────────────────────────────
    # Retrieve only the datasets relevant to a question (semantic similarity
    # over dataset embeddings) instead of prompting with the full catalog the
    # Core API sends. Degrades gracefully: if embeddings are missing/empty the
    # pipeline falls back to using every dataset it was given, so a fresh
    # deployment behaves exactly as before until the first reindex runs.
    schema_rag_enabled: bool = Field(
        default=True,
        description="Filter the prompt schema context to the datasets most relevant to the question (pgvector)",
    )
    embedding_model: str = Field(
        default="gemini-embedding-001",
        description=(
            "Embeddings model for dataset/question vectors, via the OpenAI-compatible "
            "custom provider. gemini-embedding-001 supports a configurable output "
            "dimension, so we request embedding_dim (1536) to match the pgvector "
            "column without a migration. (OpenAI equivalent: text-embedding-3-small.) "
            "If the endpoint ignores the dimension request the vectors won't match and "
            "RAG falls back to the full catalog — the app still works."
        ),
    )
    embedding_dim: int = Field(
        default=1536,
        description="Embedding dimensionality — MUST match dataset_embeddings.embedding vector(N)",
    )
    schema_rag_top_k: int = Field(
        default=8,
        description="Max datasets retrieved per question before falling back to the full catalog",
    )

    # ── Semantic few-shots (Phase 2) ─────────────────────────
    # Retrieve the few-shot query examples most SIMILAR to the question (pgvector
    # over past successful AI queries) instead of the most RECENT. Falls back to
    # recency when the example index is empty/unavailable.
    few_shot_rag_enabled: bool = Field(
        default=True,
        description="Pick few-shot examples by semantic similarity to the question, not recency",
    )
    few_shot_top_k: int = Field(
        default=5,
        description="Number of similar past queries injected as few-shot examples",
    )
    few_shot_reindex_limit: int = Field(
        default=200,
        description="Max unindexed successful queries embedded per examples-reindex run (bounds first backfill cost)",
    )

    # ── Model tiering (Phase 4) ───────────────────────────────
    schema_analyst_model: str = Field(
        default="google_genai:gemini-flash-lite-latest",
        description="deepagents model string for the schema-analyst subagent (cheap tier; mostly tool-calling, not reasoning-heavy)",
    )
    sql_generator_model: str = Field(
        default="google_genai:gemini-flash-latest",
        description=(
            "deepagents model string for the sql-generator subagent (query "
            "planner's /api/plan full path). Kept on the frontier tier — "
            "unlike schema-analyst, this step does real reasoning (e.g. "
            "matching a Trino CROSS JOIN UNNEST alias list to a nested "
            "Elasticsearch row type), and the cheap tier measurably produced "
            "invalid SQL on deeply-nested array fields."
        ),
    )
    dashboard_widget_sql_model: str = Field(
        default="gemini-flash-latest",
        description=(
            "Plain model id (NO provider prefix) for the batched per-dashboard "
            "widget-SQL call and widget-SQL repair, sent via the OpenAI-compatible "
            "custom provider — same reasoning-quality tradeoff as sql_generator_model."
        ),
    )

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = Settings()
