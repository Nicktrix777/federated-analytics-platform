"""Runtime-editable LLM settings (DB overlay + hot reload).

The non-secret, UI-editable slice of the LLM configuration lives in the
`llm_settings` singleton row (migration 0002) as a `config` JSONB. This module:

  * reads that config and OVERLAYS it onto the env-derived `settings` singleton
    (env = defaults/fallback; DB row = live overrides),
  * validates a proposed config before it is written,
  * writes it and bumps `version` (the AI Engine's settings watcher polls that
    counter and rebuilds the provider/agent stack on change),
  * reports which provider API keys are configured — as booleans only. API keys
    are NEVER stored in the DB nor returned here; they stay in the environment.
"""

import json
import logging
from typing import Any, Optional

from config import settings
from agents.tools._common import meta_connection
from llm.providers import PROVIDERS, parse_model

logger = logging.getLogger(__name__)

# The whitelist of settings a UI/operator may change at runtime. MUST match the
# keys documented on the llm_settings.config JSONB (migration 0002). Anything not
# listed here is env-only (keys, DB connection, timeouts, etc.).
STRING_FIELDS = (
    "llm_model",
    "sql_generator_model",
    "schema_analyst_model",
    "fast_path_model",
    "dashboard_widget_sql_model",
    "embedding_model",
    "llm_base_url",
)
MODEL_STRING_FIELDS = (
    "llm_model",
    "sql_generator_model",
    "schema_analyst_model",
    "fast_path_model",
    "dashboard_widget_sql_model",
    "embedding_model",
)
INT_FIELDS = ("llm_frontier_rpm", "llm_fast_rpm", "llm_embed_rpm")
BOOL_FIELDS = ("fast_path_enabled",)
FLOAT_FIELDS = ("fast_path_confidence_threshold",)

EDITABLE_FIELDS = STRING_FIELDS + INT_FIELDS + BOOL_FIELDS + FLOAT_FIELDS

# Which settings field holds each provider's API key (for the "is it set?" panel).
_KEY_FIELDS = {
    "openai": "openai_api_key",
    "anthropic": "anthropic_api_key",
    "google": "google_api_key",
    "openai_compat/ollama": "llm_api_key",
}


class SettingsValidationError(ValueError):
    """A proposed config failed validation (bad type / unknown provider prefix)."""


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate + coerce a proposed config to the editable fields only.

    Ignores unknown keys (so a slightly-stale UI can't inject arbitrary fields).
    Raises SettingsValidationError on a bad value so the PUT fails loudly instead
    of persisting a config that would break the next reload.
    """
    if not isinstance(config, dict):
        raise SettingsValidationError("config must be an object")

    clean: dict[str, Any] = {}
    for field in EDITABLE_FIELDS:
        if field not in config or config[field] is None:
            continue
        value = config[field]
        try:
            if field in INT_FIELDS:
                value = int(value)
                if value < 0:
                    raise ValueError("must be >= 0")
            elif field in BOOL_FIELDS:
                if not isinstance(value, bool):
                    raise ValueError("must be a boolean")
            elif field in FLOAT_FIELDS:
                value = float(value)
                if not (0.0 <= value <= 1.0):
                    raise ValueError("must be between 0.0 and 1.0")
            else:  # string fields
                value = str(value).strip()
        except (TypeError, ValueError) as e:
            raise SettingsValidationError(f"{field}: {e}") from e

        # Model strings must parse to a known provider prefix so we never persist
        # a config that routes to nowhere.
        if field in MODEL_STRING_FIELDS and value:
            try:
                parse_model(value)
            except ValueError as e:
                raise SettingsValidationError(str(e)) from e

        clean[field] = value
    return clean


def apply_config_overlay(config: dict[str, Any]) -> None:
    """Overlay a (validated) config onto the live `settings` singleton.

    Only the editable fields are touched; everything else keeps its env value.
    Called at startup and on every hot reload BEFORE rebuilding the LLM stack.
    """
    clean = validate_config(config) if config else {}
    for field, value in clean.items():
        setattr(settings, field, value)
    if clean:
        logger.info("Applied LLM settings overlay from DB: %s", sorted(clean))


def effective_config() -> dict[str, Any]:
    """The current effective value of every editable field (for GET)."""
    return {field: getattr(settings, field) for field in EDITABLE_FIELDS}


def provider_keys_present() -> dict[str, bool]:
    """Which provider API keys are configured (booleans only — never the values).

    Lets the UI show "OpenAI key: set / missing" so an operator knows why a
    provider is unavailable, without ever exposing the secret.
    """
    return {
        label: bool((getattr(settings, field, "") or "").strip())
        for label, field in _KEY_FIELDS.items()
    }


def known_providers() -> list[str]:
    """Provider prefixes the UI can offer (for a dropdown / hint)."""
    return sorted(PROVIDERS)


async def read_llm_settings_config() -> dict[str, Any]:
    """Read the config JSONB from the singleton row (empty dict if unavailable).

    Tolerates a missing table (migration 0002 not yet run) by returning {} so
    startup falls back to the env-derived defaults instead of crashing.
    """
    try:
        async with meta_connection() as conn:
            row = await conn.fetchrow("SELECT config FROM llm_settings WHERE id = TRUE")
        if not row or row["config"] is None:
            return {}
        cfg = row["config"]
        # asyncpg returns JSONB as text unless a codec is registered.
        return json.loads(cfg) if isinstance(cfg, str) else dict(cfg)
    except Exception as e:
        logger.debug("llm_settings config read unavailable (table missing?): %s", e)
        return {}


async def get_llm_settings_version() -> Optional[int]:
    """Current settings version, or None if the table is unavailable."""
    try:
        async with meta_connection() as conn:
            row = await conn.fetchrow("SELECT version FROM llm_settings WHERE id = TRUE")
        return int(row["version"]) if row else None
    except Exception as e:
        logger.debug("llm_settings version read unavailable (table missing?): %s", e)
        return None


async def write_llm_settings_config(config: dict[str, Any]) -> int:
    """Validate + persist a new config and bump the version. Returns new version.

    The bump is in the same UPDATE so the settings watcher notices within one
    poll interval and hot-reloads the stack. Raises SettingsValidationError on a
    bad value (nothing is written).
    """
    clean = validate_config(config)
    async with meta_connection() as conn:
        row = await conn.fetchrow(
            """
            UPDATE llm_settings
               SET config = $1::jsonb, version = version + 1, updated_at = NOW()
             WHERE id = TRUE
            RETURNING version
            """,
            json.dumps(clean),
        )
    version = int(row["version"]) if row else 0
    logger.info("llm_settings updated (version=%s): %s", version, sorted(clean))
    return version
