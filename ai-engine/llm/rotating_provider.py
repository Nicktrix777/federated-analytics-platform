"""Round-robins across N equivalent LLM providers (one per API key) behind
the single-provider interface, failing over to the next key when the current
one is rate-limited.

Only useful when the wrapped providers draw from SEPARATE quota pools —
Gemini (and most providers) rate-limit per project/account, not per key, so
wrapping multiple keys from the same project gives zero benefit.
"""

import logging

import openai

logger = logging.getLogger(__name__)


def _is_rate_limited(exc: Exception) -> bool:
    if isinstance(exc, openai.RateLimitError):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in ("429", "rate limit", "resource_exhausted", "quota"))


class RotatingProvider:
    """Duck-types the OpenAIProvider/AnthropicProvider interface
    (generate_plan/repair_sql/generate_widgets_sql/generate_sheets_sql/
    embed/generate_json) so callers don't need to know rotation is
    happening — pass a RotatingProvider anywhere a single provider is
    expected.
    """

    def __init__(self, providers: list):
        if not providers:
            raise ValueError("RotatingProvider needs at least one provider")
        self._providers = providers
        self._next = 0

    def __len__(self) -> int:
        return len(self._providers)

    def _rotation_order(self) -> list:
        """This call's provider, then every other one as a rate-limit fallback."""
        start = self._next
        self._next = (self._next + 1) % len(self._providers)
        n = len(self._providers)
        return [self._providers[(start + i) % n] for i in range(n)]

    async def _call(self, method_name: str, *args, **kwargs):
        providers = self._rotation_order()
        logger.info("%s -> key %d/%d", method_name, self._providers.index(providers[0]) + 1, len(self._providers))
        for i, provider in enumerate(providers):
            try:
                return await getattr(provider, method_name)(*args, **kwargs)
            except Exception as e:
                is_last = i == len(providers) - 1
                if not _is_rate_limited(e) or is_last:
                    raise
                logger.warning(
                    "Key %d/%d rate-limited on %s, failing over to next key: %s",
                    i + 1, len(providers), method_name, e,
                )

    async def generate_plan(self, *args, **kwargs):
        return await self._call("generate_plan", *args, **kwargs)

    async def repair_sql(self, *args, **kwargs):
        return await self._call("repair_sql", *args, **kwargs)

    async def generate_widgets_sql(self, *args, **kwargs):
        return await self._call("generate_widgets_sql", *args, **kwargs)

    async def generate_sheets_sql(self, *args, **kwargs):
        return await self._call("generate_sheets_sql", *args, **kwargs)

    async def embed(self, *args, **kwargs):
        return await self._call("embed", *args, **kwargs)

    async def generate_json(self, *args, **kwargs):
        return await self._call("generate_json", *args, **kwargs)
