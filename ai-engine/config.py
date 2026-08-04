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

    # Optional pool of Gemini keys for round-robin + rate-limit failover across
    # google_genai: models (llm_model, sql_generator_model, fast_path_model,
    # schema_analyst_model, dashboard_widget_sql_model, embedding_model). ONLY
    # helps if each key is from a SEPARATE Google Cloud project — Gemini's rate
    # limits are enforced per project, not per key, so multiple keys in the same
    # project share one quota bucket and give zero benefit. Comma-separated;
    # falls back to google_api_key (single) when empty.
    google_api_keys: str = Field(
        default="",
        description="Comma-separated Gemini keys from SEPARATE Google Cloud projects, for round-robin/failover. Falls back to google_api_key if empty.",
    )

    # Endpoint + key for the `openai_compat:` and `ollama:` provider prefixes
    # ONLY (a generic self-hosted / OpenAI-compatible gateway or local runtime).
    # These are NO LONGER a global default for the custom path — every model
    # string now carries a provider prefix, and the prefix decides the endpoint
    # and key (see llm/providers.py). Leave blank unless you use openai_compat/ollama.
    llm_base_url: str = Field(
        default="",
        description="Base URL for the openai_compat:/ollama: providers (blank = api.openai.com for openai_compat)",
    )
    llm_api_key: str = Field(
        default="",
        description="API key for the openai_compat:/ollama: providers",
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
        default="google_genai:gemini-flash-lite-latest",
        description=(
            "Provider-prefixed model string for the fast-path single-shot attempt "
            "(custom path). Cheap tier — high-volume, latency-sensitive; escalates "
            "to the full deepagents pipeline when rejected/low-confidence. "
            "(e.g. openai:gpt-4o-mini, anthropic:claude-haiku-4-5, "
            "google_genai:gemini-flash-lite-latest)."
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

    # ── Per-tier rate limits (requests per minute; 0 = unlimited) ────────
    # A token-bucket limiter per tier, shared across BOTH LLM paths (deepagents
    # + custom single-shot), so load is managed by config instead of code. This
    # complements the SDK's 429 retry (reactive) by proactively pacing calls.
    # NOTE: limiters are per-tier, not per-API-key — if two tiers use the same
    # provider key, set their RPMs to sum under that provider's real quota.
    llm_frontier_rpm: int = Field(
        default=0,
        description="RPM cap for the frontier tier (llm_model, sql_generator_model, dashboard_widget_sql_model). 0 = unlimited.",
    )
    llm_fast_rpm: int = Field(
        default=0,
        description="RPM cap for the cheap/fast tier (fast_path_model, schema_analyst_model). 0 = unlimited.",
    )
    llm_embed_rpm: int = Field(
        default=0,
        description="RPM cap for embedding calls (embedding_model). 0 = unlimited.",
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
    # Only the chat query planner still runs as a deepagents ReAct loop (the
    # report/dashboard designers were converted to a deterministic two-call
    # pipeline — see agents/report_planner.py, agents/dashboard_planner.py).
    # A chat turn resolves in a handful of hops, so the cap is tight enough
    # that a wandering run fails fast instead of spinning for minutes.
    agent_recursion_limit: int = Field(
        default=12,
        description=(
            "LangGraph recursion_limit for the chat query-planner deepagent — "
            "caps how many graph steps (LLM turns + tool executions) one run "
            "can take before erroring, so a wandering agent can't spin for minutes"
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
        default="google_genai:gemini-embedding-001",
        description=(
            "Provider-prefixed embeddings model for dataset/question vectors (custom "
            "path). May use a DIFFERENT provider than reasoning — e.g. anthropic: has "
            "no embeddings API, so pair Claude reasoning with openai:text-embedding-3-small "
            "or google_genai:gemini-embedding-001. "
            "gemini-embedding-001 supports a configurable output "
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
    design_low_confidence_retry_threshold: float = Field(
        default=0.4,
        description=(
            "Report/dashboard design confidence below this triggers ONE retry against "
            "the full (untrimmed) catalog instead of the schema-RAG top-k selection — "
            "recovers briefs where the trim excluded a dataset the brief actually needed. "
            "The retry is kept only if it scores a higher confidence than the original."
        ),
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
        default="google_genai:gemini-flash-latest",
        description=(
            "Provider-prefixed model string for the batched per-dashboard widget-SQL "
            "call, batched report-sheet SQL, and SQL repair (custom path) — same "
            "reasoning-quality tradeoff as sql_generator_model. "
            "(e.g. openai:gpt-4o, anthropic:claude-sonnet-5)."
        ),
    )

    model_config = {"env_file": ".env", "case_sensitive": False}


settings = Settings()
