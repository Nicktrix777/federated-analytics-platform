"""
Provider registry + factory — the single source of truth for turning a
provider-prefixed model string into a working LLM client.

Both LLM paths in this service go through here, so switching provider is a
config-only change (no code edits):

  * deepagents / LangChain path  -> `make_langchain_model(model_string)`
  * custom single-shot path      -> `make_custom_provider(model_string)`

A model string is `"<provider>:<model_id>"`, e.g. `openai:gpt-4o`,
`anthropic:claude-sonnet-5`, `google_genai:gemini-flash-latest`,
`ollama:llama3.1`, or `openai_compat:my-model` (a generic self-hosted
OpenAI-compatible endpoint pointed at by `settings.llm_base_url`).

The provider prefix — NOT a base_url default — decides which endpoint and which
API key each call uses. This is what fixes the old bug where the custom path
silently fell back to Gemini regardless of `LLM_MODEL`.
"""

import logging
from dataclasses import dataclass

from langchain.chat_models import init_chat_model
from langchain_core.rate_limiters import InMemoryRateLimiter

from config import settings
from llm.openai_provider import OpenAIProvider
from llm.anthropic_provider import AnthropicProvider

logger = logging.getLogger(__name__)

# Gemini's OpenAI-compatible endpoint (used by the custom path when the model
# string is google_genai:...). The deepagents path uses Gemini's *native*
# integration instead (google_genai: prefix -> ChatGoogleGenerativeAI).
GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


@dataclass(frozen=True)
class ProviderConf:
    # LangChain init_chat_model prefix (the deepagents path).
    langchain_prefix: str
    # Client kind for the custom single-shot path.
    #   "openai"    -> OpenAIProvider (any OpenAI-compatible wire format)
    #   "anthropic" -> AnthropicProvider (native Messages API)
    kind: str
    # settings.<field> name holding this provider's API key (None = keyless,
    # e.g. a local runtime). "_base_url" means base_url comes from settings.
    key_setting: str | None
    # Fixed base_url for the custom (OpenAI-compatible) path, or None to use the
    # SDK default (api.openai.com), or "_settings" to read settings.llm_base_url.
    base_url: str | None


# Registry. Add a provider here and it is switchable by config alone.
PROVIDERS: dict[str, ProviderConf] = {
    "openai": ProviderConf("openai", "openai", "openai_api_key", None),
    "anthropic": ProviderConf("anthropic", "anthropic", "anthropic_api_key", None),
    "google_genai": ProviderConf("google_genai", "openai", "google_api_key", GEMINI_OPENAI_URL),
    # Generic self-hosted / OpenAI-compatible endpoint (vLLM, LM Studio, a
    # gateway, etc.). base_url + key come from LLM_BASE_URL / LLM_API_KEY.
    "openai_compat": ProviderConf("openai", "openai", "llm_api_key", "_settings"),
    # Local Ollama (its OpenAI-compatible endpoint for the custom path; native
    # ollama: integration for the deepagents path). Keyless by default.
    "ollama": ProviderConf("ollama", "openai", None, "http://localhost:11434/v1"),
}

# Aliases for ergonomics.
_ALIASES = {"gemini": "google_genai", "google": "google_genai", "claude": "anthropic", "gpt": "openai"}

# Fallback provider for a BARE model id (no prefix). Historically the custom
# path took bare ids through the OpenAI-compatible client at llm_base_url, so a
# bare id keeps that behavior.
_DEFAULT_PROVIDER = "openai_compat"


def parse_model(model_string: str) -> tuple[str, str]:
    """Split `"provider:model_id"` into `(provider, model_id)`.

    A bare id (no `:`) resolves to the default provider. An unknown provider
    prefix raises so a typo fails loudly at startup instead of silently routing
    to the wrong endpoint.
    """
    s = (model_string or "").strip()
    if ":" in s:
        prefix, model_id = s.split(":", 1)
        provider = _ALIASES.get(prefix, prefix)
        if provider not in PROVIDERS:
            raise ValueError(
                f"Unknown LLM provider prefix '{prefix}' in '{model_string}'. "
                f"Known: {sorted(PROVIDERS) + sorted(_ALIASES)}"
            )
        return provider, model_id.strip()
    # Bare id.
    return _DEFAULT_PROVIDER, s


def _resolve_base_url(conf: ProviderConf) -> str | None:
    if conf.base_url == "_settings":
        return (settings.llm_base_url or "").strip() or None
    return conf.base_url


def resolve_key(provider: str) -> str:
    """Return the API key for `provider` from settings (empty string if unset)."""
    conf = PROVIDERS[provider]
    if conf.key_setting is None:
        # Keyless local runtime; llm_api_key may still be set for a gated proxy.
        return (settings.llm_api_key or "").strip()
    return (getattr(settings, conf.key_setting, "") or "").strip()


def key_present(model_string: str) -> bool:
    """Whether the key needed to use `model_string` is configured.

    Local providers (ollama, openai_compat) are considered available even
    without a key — they front a self-hosted endpoint. Cloud providers require
    their key. Used by the lifespan to decide whether to enable a feature.
    """
    provider, _ = parse_model(model_string)
    conf = PROVIDERS[provider]
    if conf.key_setting in (None, "llm_api_key"):
        return True
    return bool(resolve_key(provider))


def make_custom_provider(
    model_string: str, rate_limiter: InMemoryRateLimiter | None = None
):
    """Build the custom single-shot provider (fast path, widget/sheet SQL,
    repair, embeddings) for `model_string`.

    Returns an OpenAIProvider or AnthropicProvider, or None when the required
    cloud key is missing (caller disables that feature and logs why).
    """
    provider, model_id = parse_model(model_string)
    conf = PROVIDERS[provider]
    key = resolve_key(provider)

    if conf.key_setting not in (None, "llm_api_key") and not key:
        logger.warning(
            "No API key for provider '%s' (set %s) — feature using '%s' disabled.",
            provider, conf.key_setting.upper(), model_string,
        )
        return None

    if conf.kind == "anthropic":
        return AnthropicProvider(
            api_key=key,
            model=model_id,
            max_retries=settings.llm_max_retries,
            timeout=settings.llm_timeout_seconds,
            rate_limiter=rate_limiter,
        )

    # OpenAI-compatible. A local endpoint may need no real key; the OpenAI SDK
    # only requires a non-empty string, so pass a placeholder when keyless.
    return OpenAIProvider(
        api_key=key or "not-needed",
        model=model_id,
        max_retries=settings.llm_max_retries,
        timeout=settings.llm_timeout_seconds,
        base_url=_resolve_base_url(conf),
        rate_limiter=rate_limiter,
    )


def make_langchain_model(
    model_string: str, rate_limiter: InMemoryRateLimiter | None = None
):
    """Build a LangChain chat model (deepagents path) for `model_string`.

    Native integrations (openai/anthropic/google_genai/ollama) read their key
    from the environment, which the lifespan exports. openai_compat and ollama
    also get an explicit base_url so a self-hosted endpoint is reachable.
    """
    provider, model_id = parse_model(model_string)
    conf = PROVIDERS[provider]

    kwargs: dict = {
        "max_retries": settings.llm_max_retries,
        "timeout": settings.llm_timeout_seconds,
        # Provider default is ~1.0 (unset); the batched/single-shot custom-provider
        # calls already pin 0.1 for deterministic SQL/JSON. Match that here so the
        # remaining deepagents caller (chat query planner) isn't the odd one out.
        "temperature": 0.1,
    }
    if rate_limiter is not None:
        kwargs["rate_limiter"] = rate_limiter

    # Only the generic/self-hosted endpoints need base_url + explicit key; the
    # cloud natives resolve their endpoint + env key on their own.
    if provider in ("openai_compat", "ollama"):
        base_url = _resolve_base_url(conf)
        if base_url:
            kwargs["base_url"] = base_url
        key = resolve_key(provider)
        if key:
            kwargs["api_key"] = key

    return init_chat_model(f"{conf.langchain_prefix}:{model_id}", **kwargs)


def _make_limiter(rpm: int) -> InMemoryRateLimiter | None:
    """A token-bucket limiter for `rpm` requests/minute (None = unlimited)."""
    if not rpm or rpm <= 0:
        return None
    return InMemoryRateLimiter(
        requests_per_second=rpm / 60.0,
        check_every_n_seconds=0.1,
        # Allow a small burst (~10s worth) so short bursts aren't over-serialized.
        max_bucket_size=max(1, rpm // 6),
    )


def build_tier_limiters() -> dict[str, InMemoryRateLimiter | None]:
    """Per-tier RPM limiters, shared across BOTH paths.

    Tiers:
      frontier -> llm_model, sql_generator_model, dashboard_widget_sql_model
      fast     -> fast_path_model, schema_analyst_model
      embed    -> embedding_model

    NOTE: limiters are per-tier, not per-API-key. If two tiers share one
    provider's key, set their RPMs to sum under that provider's real quota.
    """
    return {
        "frontier": _make_limiter(settings.llm_frontier_rpm),
        "fast": _make_limiter(settings.llm_fast_rpm),
        "embed": _make_limiter(settings.llm_embed_rpm),
    }
