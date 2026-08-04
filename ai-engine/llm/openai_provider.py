"""
OpenAI LLM provider for the AI Engine.

Uses OpenAI's structured output (JSON mode) to guarantee valid JSON responses.
The response is then parsed and validated by Pydantic.
"""

import json
import logging
from openai import AsyncOpenAI

from models import Clarification, QueryPlan, QueryStep

logger = logging.getLogger(__name__)


class OpenAIProvider:
    """OpenAI-COMPATIBLE provider.

    Despite the name this talks to any OpenAI-compatible endpoint via `base_url`
    — real OpenAI (base_url=None), Google Gemini's OpenAI-compat layer, Groq,
    OpenRouter, a local Ollama/vLLM, etc. The wire format is identical; only the
    base_url, api_key, and model ids change. This is what makes the custom
    single-shot path (fast plan, widget SQL, repair) and embeddings model-agnostic.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        max_retries: int = 5,
        timeout: float = 30.0,
        base_url: str | None = None,
        rate_limiter=None,
    ):
        # The OpenAI SDK retries 429/5xx internally with exponential backoff
        # up to max_retries — this is what actually protects the fast path
        # from rate-limit errors surfacing to the caller. base_url=None uses
        # api.openai.com; set it to point at any OpenAI-compatible provider.
        self.client = AsyncOpenAI(
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
            base_url=base_url or None,
        )
        self.model = model
        # Optional langchain InMemoryRateLimiter, shared with this tier's
        # deepagents model so the per-tier RPM cap covers both paths.
        self._rate_limiter = rate_limiter

    async def _acquire(self) -> None:
        """Block until the tier's rate limiter grants a slot (no-op if unset)."""
        if self._rate_limiter is not None:
            await self._rate_limiter.aacquire()

    async def generate_plan(
        self, system_prompt: str, user_prompt: str, question: str
    ) -> "QueryPlan | Clarification":
        logger.info(f"Calling OpenAI model: {self.model}")

        await self._acquire()
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

        Returns just the corrected SQL string (JSON mode guarantees a parseable
        response). Temperature is 0 — repairing a known error is a deterministic
        correction, not a creative task.
        """
        logger.info(f"Calling OpenAI model for SQL repair: {self.model}")

        await self._acquire()
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

    async def generate_widgets_sql(self, system_prompt: str, user_prompt: str) -> dict:
        """
        One-shot batched SQL generation for every widget in a dashboard.

        Replaces N separate per-widget subagent calls with a single request —
        this is the fix for the OpenAI 429 storm a multi-widget dashboard
        brief used to cause (one deepagents tool-call round trip per widget).
        """
        logger.info(f"Calling OpenAI model for batched widget SQL: {self.model}")

        await self._acquire()
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=8000,  # up to ~10 widgets' worth of SQL in one response
        )

        raw_content = response.choices[0].message.content
        if not raw_content:
            raise ValueError("OpenAI returned empty response for widget SQL batch")

        try:
            return json.loads(raw_content)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"OpenAI batch widget response is not valid JSON: {e}\nRaw: {raw_content[:500]}"
            )

    async def generate_sheets_sql(self, system_prompt: str, user_prompt: str) -> dict:
        """
        One-shot batched SQL generation for every sheet in a report.

        Same single-request pattern as generate_widgets_sql — one call writes
        the SQL (plus per-column Excel formats) for all sheets instead of
        fanning out one round trip per sheet.
        """
        logger.info(f"Calling OpenAI model for batched sheet SQL: {self.model}")

        await self._acquire()
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=8000,  # up to 6 sheets' worth of SQL + column formats in one response
        )

        raw_content = response.choices[0].message.content
        if not raw_content:
            raise ValueError("OpenAI returned empty response for sheet SQL batch")

        try:
            return json.loads(raw_content)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"OpenAI batch sheet response is not valid JSON: {e}\nRaw: {raw_content[:500]}"
            )

    async def embed(
        self, texts: list[str], model: str, dimensions: int | None = None
    ) -> list[list[float]]:
        """Embed a batch of texts with the given embeddings model.

        Returns one vector (list[float]) per input text, in order. Used by the
        schema-RAG index (dataset text → vector) and per-question retrieval
        (question → vector). Batched in one request — the embeddings endpoint
        accepts a list of inputs, so a full-catalog reindex is a single call.

        `dimensions` requests a specific output size (supported by OpenAI
        text-embedding-3-* and Gemini gemini-embedding-001) so the vectors match
        the fixed-width pgvector column across providers. Omitted when None.
        """
        if not texts:
            return []
        # Callers pass the provider-prefixed config string (e.g.
        # "google_genai:gemini-embedding-001"); this client talks to one
        # OpenAI-compatible endpoint, which wants the bare model id.
        model_id = model.split(":", 1)[1] if ":" in model else model
        logger.info(f"Embedding {len(texts)} text(s) with {model_id} (dims={dimensions or 'default'})")
        kwargs: dict = {"model": model_id, "input": texts}
        if dimensions:
            kwargs["dimensions"] = dimensions
        await self._acquire()
        response = await self.client.embeddings.create(**kwargs)
        # data is returned in request order. OpenAI sets `index` on each item;
        # some OpenAI-compatible providers (Gemini) return index=None, so fall
        # back to the response order (which already matches input order) rather
        # than crashing on a None-vs-int comparison.
        ordered = sorted(response.data, key=lambda d: d.index if d.index is not None else 0)
        return [d.embedding for d in ordered]

    async def generate_json(self, system_prompt: str, user_prompt: str, max_tokens: int = 4000) -> dict:
        """
        Generic JSON-mode completion.

        Used when a caller needs guaranteed-parseable JSON regardless of a
        free-form conversational model's output discipline — e.g. reformatting
        a deepagents chat agent's final answer (which may include a prose
        preamble/summary despite instructions not to) into a strict schema.
        response_format=json_object is enforced by the API itself, not just
        prompted, so this can't fail the way parsing a raw chat message can.
        """
        await self._acquire()
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=max_tokens,
        )

        raw_content = response.choices[0].message.content
        if not raw_content:
            raise ValueError("OpenAI returned empty response")

        return json.loads(raw_content)
