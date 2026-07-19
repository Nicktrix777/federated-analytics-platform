"""
Anthropic (Claude) LLM provider for the AI Engine.

Drop-in sibling of OpenAIProvider for every reasoning/SQL-generation call —
fast-path plan, single-widget structuring, batched widget SQL, and widget
repair. It deliberately does NOT implement embed(): Anthropic has no
embeddings API, so the schema-RAG / few-shot-RAG index stays on OpenAI (see
config.embedding_model and main._embed_provider).

OpenAI's provider used `response_format={"type": "json_object"}` to guarantee
parseable JSON. Claude has no equivalent one-liner, so we instead:
  - disable extended thinking (these are deterministic JSON tasks — no reasoning
    latency wanted, and disabled keeps the response to a single answer block),
  - append a firm "output ONLY the JSON object" instruction to the system
    prompt (the existing prompts already ask for JSON; this reinforces it), and
  - extract the JSON defensively (strip code fences / surrounding prose) before
    json.loads.

Sampling params (temperature/top_p) are intentionally omitted — Claude Sonnet 5
rejects non-default values with a 400, and omitting them is safe on every model.
"""

import json
import logging
import re

from anthropic import AsyncAnthropic

from models import Clarification, QueryPlan, QueryStep

logger = logging.getLogger(__name__)

# Reinforce strict JSON output. Claude has no `response_format=json_object`, and
# with thinking disabled Sonnet 5 can occasionally prepend reasoning to the
# visible answer — this instruction plus _extract_json keeps parsing robust.
_JSON_ONLY_SUFFIX = (
    "\n\nOutput requirement: respond with ONLY a single valid JSON object. "
    "No prose, no explanation outside the JSON, no markdown code fences."
)

# Thinking is disabled for these fast, deterministic JSON tasks (accepted on
# both Sonnet 5 and Haiku 4.5; only Fable 5 rejects an explicit disable).
_THINKING_DISABLED = {"type": "disabled"}


def _extract_json(raw: str) -> str:
    """Best-effort extraction of a JSON object from a model response.

    Handles a bare object, a ```json fenced block, or a JSON object with minor
    surrounding prose. Returns the substring most likely to be the JSON payload;
    the caller still runs json.loads and raises on genuinely malformed output.
    """
    text = raw.strip()

    # ```json ... ``` or ``` ... ``` fenced block
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    # Fall back to the first '{' .. last '}' span if there's leading/trailing prose.
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    return text


class AnthropicProvider:
    def __init__(self, api_key: str, model: str, max_retries: int = 5, timeout: float = 30.0):
        # The Anthropic SDK retries 429/5xx internally with exponential backoff
        # up to max_retries — same resilience contract as the OpenAI provider.
        self.client = AsyncAnthropic(api_key=api_key, max_retries=max_retries, timeout=timeout)
        self.model = model

    async def _complete_json(self, system_prompt: str, user_prompt: str, max_tokens: int) -> str:
        """One Claude call returning the concatenated text of the response.

        Shared by every JSON-mode method below. Raises ValueError on an empty
        response so callers get the same failure surface as the OpenAI provider.
        """
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            thinking=_THINKING_DISABLED,
            system=system_prompt + _JSON_ONLY_SUFFIX,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        if not text.strip():
            raise ValueError("Anthropic returned empty response")
        return text

    async def generate_plan(
        self, system_prompt: str, user_prompt: str, question: str
    ) -> "QueryPlan | Clarification":
        logger.info(f"Calling Anthropic model: {self.model}")

        raw_content = await self._complete_json(system_prompt, user_prompt, max_tokens=4000)
        logger.debug(f"Anthropic raw response: {raw_content[:200]}...")

        try:
            data = json.loads(_extract_json(raw_content))
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Anthropic response is not valid JSON: {e}\nRaw: {raw_content[:500]}"
            )

        # PR5: check if the model returned a clarification instead of a plan.
        clar_dict = data.get("clarification")
        if isinstance(clar_dict, dict) and clar_dict.get("question") and not (data.get("sql") or "").strip():
            return Clarification(
                question=clar_dict["question"],
                options=clar_dict.get("options", []),
                kind=clar_dict.get("kind", "ambiguous"),
            )

        # Parse steps
        steps = []
        for s in data.get("steps", []):
            steps.append(
                QueryStep(
                    step_id=s.get("step_id", 0),
                    description=s.get("description", ""),
                    catalog=s.get("catalog", ""),
                    schema_name=s.get("schema_name", "public"),
                    table=s.get("table", ""),
                )
            )

        # Build and validate QueryPlan (Pydantic validates SQL)
        plan = QueryPlan(
            question=question,
            sql=data.get("sql", ""),
            steps=steps,
            confidence=float(data.get("confidence", 0.5)),
            explanation=data.get("explanation", ""),
        )

        return plan

    async def repair_sql(self, system_prompt: str, user_prompt: str) -> str:
        """
        Single-shot repair of one Trino SQL query that failed to execute.

        Returns just the corrected SQL string. Repairing a known error is a
        deterministic correction, not a creative task.
        """
        logger.info(f"Calling Anthropic model for SQL repair: {self.model}")

        raw_content = await self._complete_json(system_prompt, user_prompt, max_tokens=2000)

        try:
            data = json.loads(_extract_json(raw_content))
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Anthropic repair response is not valid JSON: {e}\nRaw: {raw_content[:500]}"
            )

        sql = (data.get("sql") or "").strip()
        if not sql:
            raise ValueError("Repair response contained no SQL")
        return sql

    async def generate_widgets_sql(self, system_prompt: str, user_prompt: str) -> dict:
        """
        One-shot batched SQL generation for every widget in a dashboard.

        Replaces N separate per-widget subagent calls with a single request.
        """
        logger.info(f"Calling Anthropic model for batched widget SQL: {self.model}")

        raw_content = await self._complete_json(system_prompt, user_prompt, max_tokens=8000)
        try:
            return json.loads(_extract_json(raw_content))
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Anthropic batch widget response is not valid JSON: {e}\nRaw: {raw_content[:500]}"
            )

    async def generate_json(self, system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> dict:
        """
        Generic JSON completion — reformat a free-form agent answer into strict
        JSON regardless of the source model's output discipline.
        """
        raw_content = await self._complete_json(system_prompt, user_prompt, max_tokens=max_tokens)
        return json.loads(_extract_json(raw_content))
