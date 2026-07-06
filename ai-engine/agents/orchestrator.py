"""
Query Planner Orchestrator

The main deepagent that coordinates schema analysis, SQL generation, and validation
using specialized subagents. Built with deepagents' create_deep_agent.

Architecture:
    Orchestrator (deepagent, gpt-4o)
      ├── schema-analyst subagent   → discovers tables from ALL registered sources
      ├── sql-generator subagent    → writes Trino SQL
      └── sql-validator subagent    → safety + syntax validation

The orchestrator maintains a clean context window by delegating heavy work
to isolated subagents. Only final outputs flow back to the orchestrator.
"""

import asyncio
import functools
import json
import logging
import re
from typing import Optional

from deepagents import create_deep_agent

from agents.subagents.schema_analyst import SCHEMA_ANALYST_SUBAGENT
from agents.subagents.sql_generator import SQL_GENERATOR_SUBAGENT
from agents.subagents.sql_validator import SQL_VALIDATOR_SUBAGENT
from agents.tools.schema_tools import list_available_sources
from agents.tools.validation_tools import validate_sql_safety
from models import QueryPlan, QueryStep

logger = logging.getLogger(__name__)

ORCHESTRATOR_SYSTEM_PROMPT = """You are the Query Planner for a Federated Analytics Platform.

Your goal is to convert a natural language question into a validated, executable Trino SQL query plan.

## Workflow (ALWAYS follow this order)

**Step 1 — Schema Discovery (delegate to schema-analyst)**
Use the schema-analyst subagent to:
- Discover all registered data sources
- Identify which tables/indices are relevant to the question
- Get column details and join relationships

**Step 2 — SQL Generation (delegate to sql-generator)**
Provide the schema context from step 1 to the sql-generator subagent.
The generator will produce a complete, Trino-compatible SQL query.

**Step 3 — Validation (delegate to sql-validator)**
Pass the generated SQL to the sql-validator subagent.
If it finds issues, revise and re-validate once.

**Step 4 — Return the QueryPlan**
Return a final JSON object in EXACTLY this format:
{
  "question": "<original question>",
  "sql": "<validated Trino SQL>",
  "steps": [
    {"step_id": 1, "description": "...", "catalog": "...", "schema_name": "...", "table": "..."}
  ],
  "confidence": <0.0 to 1.0>,
  "explanation": "<human-readable explanation of what the query does>"
}

## Important Rules
- NEVER skip the schema-analyst step — data sources may have changed
- NEVER return SQL that was not validated by sql-validator
- NEVER generate INSERT, UPDATE, DELETE, or DDL statements
- If confidence < 0.3, still return the plan with a clear explanation of limitations
- The response JSON must be the LAST thing you output, with no text after it
"""


def create_query_planner(model: str = "openai:gpt-4o") -> object:
    """
    Create the main query planner deepagent.
    
    Args:
        model: deepagents model string (e.g., 'openai:gpt-4o')
    
    Returns:
        A compiled deepagent graph ready to invoke
    """
    return create_deep_agent(
        model=model,
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        tools=[list_available_sources, validate_sql_safety],
        subagents=[
            SCHEMA_ANALYST_SUBAGENT,
            SQL_GENERATOR_SUBAGENT,
            SQL_VALIDATOR_SUBAGENT,
        ],
    )


async def generate_query_plan(
    agent,
    question: str,
    extra_context: Optional[str] = None,
) -> QueryPlan:
    """
    Invoke the query planner and extract a structured QueryPlan.
    
    Args:
        agent: The compiled deepagent from create_query_planner()
        question: Natural language question from the user
        extra_context: Optional additional context (e.g., pre-loaded schema)
    
    Returns:
        A validated QueryPlan Pydantic model
    
    Raises:
        ValueError: If the agent output cannot be parsed into a QueryPlan
    """
    user_message = question
    if extra_context:
        user_message = f"{question}\n\nAdditional context:\n{extra_context}"

    logger.info(f"Invoking query planner for: {question[:100]}")

    try:
        # deepagents/LangGraph agent.invoke() is synchronous — run it in a thread
        # pool so it doesn't block the uvicorn event loop (which would make the
        # /health endpoint unresponsive during long LLM pipelines).
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            functools.partial(
                agent.invoke,
                {"messages": [{"role": "user", "content": user_message}]},
            ),
        )

        # Extract the final message content
        messages = result.get("messages", [])
        if not messages:
            raise ValueError("Agent returned no messages")

        last_message = messages[-1]
        raw_content = (
            last_message.content
            if hasattr(last_message, "content")
            else str(last_message)
        )

        # Normalize content: the OpenAI Responses API (used by deepagents) returns
        # content as a list of content blocks [{"type": "text", "text": "..."}]
        # rather than a plain string. Flatten to a single string before parsing.
        content = _normalize_content(raw_content)

        logger.debug(f"Agent output (last 500 chars): {content[-500:]}")

        # Extract the JSON plan from the output
        plan_data = _extract_json_plan(content)
        return _build_query_plan(plan_data, question)

    except Exception as e:
        logger.error(f"Query planner failed: {e}", exc_info=True)
        raise ValueError(f"Query planning failed: {e}")


def _normalize_content(content) -> str:
    """Normalize LLM message content to a plain string.

    The OpenAI Responses API and some deepagents versions return content as a
    list of content blocks, e.g.:
        [{"type": "text", "text": "..."}, {"type": "text", "text": "..."}]

    This function handles both plain strings and list formats, returning a
    single concatenated string suitable for JSON extraction.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                # OpenAI content block: {"type": "text", "text": "..."}
                parts.append(block.get("text", ""))
            elif hasattr(block, "text"):
                # LangChain / deepagents content block object
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts)
    # Fallback for any other type
    return str(content)


def _extract_json_plan(content: str) -> dict:
    """Extract the JSON query plan from the agent's text output."""
    # Try to find a JSON block in the content
    # The orchestrator is instructed to end with a raw JSON object

    # First, try direct JSON parse
    stripped = content.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # Try to find JSON in code blocks
    code_block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if code_block_match:
        try:
            return json.loads(code_block_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find the last JSON object in the content
    json_matches = list(re.finditer(r'\{[^{}]*"sql"[^{}]*\}', content, re.DOTALL))
    if not json_matches:
        # Try a broader match
        json_matches = list(re.finditer(r'\{.*?"sql".*?\}', content, re.DOTALL))

    if json_matches:
        last_match = json_matches[-1]
        try:
            return json.loads(last_match.group())
        except json.JSONDecodeError:
            pass

    # Last resort: look for the last { ... } block
    brace_start = content.rfind("{")
    if brace_start != -1:
        candidate = content[brace_start:]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"Could not extract JSON query plan from agent output. "
        f"Last 200 chars: {content[-200:]}"
    )


def _build_query_plan(data: dict, question: str) -> QueryPlan:
    """Build and validate a QueryPlan from the extracted JSON data."""
    sql = data.get("sql", "").strip()
    if not sql:
        raise ValueError("Agent plan contains no SQL")

    steps = []
    for i, s in enumerate(data.get("steps", []), 1):
        steps.append(QueryStep(
            step_id=s.get("step_id", i),
            description=s.get("description", ""),
            catalog=s.get("catalog", ""),
            schema_name=s.get("schema_name", "public"),
            table=s.get("table", ""),
        ))

    confidence = float(data.get("confidence", 0.7))
    confidence = max(0.0, min(1.0, confidence))

    return QueryPlan(
        question=data.get("question", question),
        sql=sql,
        steps=steps,
        confidence=confidence,
        explanation=data.get("explanation", ""),
    )
