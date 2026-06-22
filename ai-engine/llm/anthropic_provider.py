"""
Anthropic Claude LLM provider for the AI Engine.

Uses Claude's tool_use feature to guarantee structured JSON output.
Tool-use forces Claude to populate a specific schema, making it
more reliable than asking for JSON in the prompt directly.
"""

import json
import logging
from anthropic import AsyncAnthropic

from llm.base import BaseLLMProvider
from models import QueryPlan, QueryStep

logger = logging.getLogger(__name__)

# Tool definition — Claude must "call" this tool with a valid QueryPlan
QUERY_PLAN_TOOL = {
    "name": "submit_query_plan",
    "description": "Submit the structured query plan as a response to the user's question.",
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "The original user question"},
            "sql": {
                "type": "string",
                "description": "Valid Trino SQL SELECT statement to answer the question",
            },
            "steps": {
                "type": "array",
                "description": "Reasoning steps for the query plan",
                "items": {
                    "type": "object",
                    "properties": {
                        "step_id": {"type": "integer"},
                        "description": {"type": "string"},
                        "catalog": {"type": "string"},
                        "schema_name": {"type": "string"},
                        "table": {"type": "string"},
                    },
                    "required": [
                        "step_id",
                        "description",
                        "catalog",
                        "schema_name",
                        "table",
                    ],
                },
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score between 0.0 and 1.0",
            },
            "explanation": {
                "type": "string",
                "description": "Human-readable explanation of the query plan",
            },
        },
        "required": ["question", "sql", "steps", "confidence", "explanation"],
    },
}


class AnthropicProvider(BaseLLMProvider):
    def __init__(self, api_key: str, model: str):
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model

    async def generate_plan(
        self, system_prompt: str, user_prompt: str, question: str
    ) -> QueryPlan:
        logger.info(f"Calling Anthropic model: {self.model}")

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=system_prompt,
            tools=[QUERY_PLAN_TOOL],
            tool_choice={"type": "tool", "name": "submit_query_plan"},  # Force tool use
            messages=[{"role": "user", "content": user_prompt}],
        )

        # Extract tool use block
        tool_use_block = None
        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_query_plan":
                tool_use_block = block
                break

        if not tool_use_block:
            raise ValueError(
                f"Claude did not return a tool_use block. Content: {response.content}"
            )

        data = tool_use_block.input
        logger.debug(f"Anthropic tool input: {json.dumps(data)[:200]}...")

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
