"""
OpenAI LLM provider for the AI Engine.

Uses OpenAI's structured output (JSON mode) to guarantee valid JSON responses.
The response is then parsed and validated by Pydantic.
"""

import json
import logging
from openai import AsyncOpenAI

from llm.base import BaseLLMProvider
from models import QueryPlan, QueryStep

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str, max_retries: int = 5, timeout: float = 30.0):
        # The OpenAI SDK retries 429/5xx internally with exponential backoff
        # up to max_retries — this is what actually protects the fast path
        # from rate-limit errors surfacing to the caller.
        self.client = AsyncOpenAI(api_key=api_key, max_retries=max_retries, timeout=timeout)
        self.model = model

    async def generate_plan(
        self, system_prompt: str, user_prompt: str, question: str
    ) -> QueryPlan:
        logger.info(f"Calling OpenAI model: {self.model}")

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},  # Structured JSON output
            temperature=0.1,  # Low temperature for deterministic SQL generation
            max_tokens=4000,

        )

        raw_content = response.choices[0].message.content
        if not raw_content:
            raise ValueError("OpenAI returned empty response")

        logger.debug(f"OpenAI raw response: {raw_content[:200]}...")

        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"OpenAI response is not valid JSON: {e}\nRaw: {raw_content[:500]}"
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

        Returns just the corrected SQL string (JSON mode guarantees a parseable
        response). Temperature is 0 — repairing a known error is a deterministic
        correction, not a creative task.
        """
        logger.info(f"Calling OpenAI model for SQL repair: {self.model}")

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=2000,
        )

        raw_content = response.choices[0].message.content
        if not raw_content:
            raise ValueError("OpenAI returned empty repair response")

        try:
            data = json.loads(raw_content)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"OpenAI repair response is not valid JSON: {e}\nRaw: {raw_content[:500]}"
            )

        sql = (data.get("sql") or "").strip()
        if not sql:
            raise ValueError("Repair response contained no SQL")
        return sql
