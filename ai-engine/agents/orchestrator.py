"""
Query Planner Orchestrator

The main deepagent that coordinates schema analysis and SQL generation using
specialized subagents. Built with deepagents' create_deep_agent.

Architecture:
    Orchestrator (deepagent, gpt-4o)
      ├── schema-analyst subagent   → discovers tables from ALL registered sources
      │                               (skipped when pre-loaded context already suffices)
      └── sql-generator subagent    → writes Trino SQL

SQL safety/Trino-compatibility validation is applied deterministically in
Python (see agents.tools.validation_tools.validate_and_fix_sql) rather than by
a dedicated LLM subagent — that checklist doesn't need a model call, and
dropping it removes 2-3 LLM round trips from every request.

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
from langchain.chat_models import init_chat_model

from agents.subagents.schema_analyst import SCHEMA_ANALYST_SUBAGENT
from agents.subagents.sql_generator import SQL_GENERATOR_SUBAGENT
from agents.tools.schema_tools import list_available_sources
from config import settings
from models import QueryPlan, QueryStep

logger = logging.getLogger(__name__)

ORCHESTRATOR_SYSTEM_PROMPT = """You are the Query Planner for a Federated Analytics Platform.

Your goal is to convert a natural language question into a validated, executable Trino SQL query plan.

## Workflow

**Step 1 — Schema Discovery (delegate to schema-analyst, CONDITIONAL)**
Check the "Additional context" block in the user message first — the Core API often pre-loads
dataset names, Trino paths, and columns there.
- If that context already covers every table/column the question needs, SKIP schema-analyst
  and go straight to Step 2 using the pre-loaded context.
- If the context is missing, empty, or doesn't cover a data source the question clearly needs,
  delegate to schema-analyst to discover it before continuing.

**Step 2 — SQL Generation (delegate to sql-generator)**
Provide the schema context (pre-loaded or from schema-analyst) to the sql-generator subagent.
The generator will produce a complete, Trino-compatible SQL query.

**Step 3 — Return the QueryPlan**
SQL safety and Trino-compatibility checks run automatically after you respond — you do NOT need
to call a validator subagent. Just return a final JSON object in EXACTLY this format:
{
  "question": "<original question>",
  "sql": "<Trino SQL from sql-generator>",
  "steps": [
    {"step_id": 1, "description": "...", "catalog": "...", "schema_name": "...", "table": "..."}
  ],
  "confidence": <0.0 to 1.0>,
  "explanation": "<human-readable explanation of what the query does>"
}

## Important Rules
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
    resolved_model = init_chat_model(
        model,
        max_retries=settings.llm_max_retries,
        timeout=settings.llm_timeout_seconds,
    )
    return create_deep_agent(
        model=resolved_model,
        system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
        tools=[list_available_sources],
        subagents=[
            SCHEMA_ANALYST_SUBAGENT,
            SQL_GENERATOR_SUBAGENT,
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
        # Normalize content: the OpenAI Responses API (used by deepagents) returns
        # content as a list of content blocks [{"type": "text", "text": "..."}]
        # rather than a plain string. Flatten to a single string before parsing.
        content = _normalize_content(await run_agent(agent, user_message))

        logger.debug(f"Agent output (last 500 chars): {content[-500:]}")

        # Extract the JSON plan from the output
        plan_data = _extract_json_plan(content)
        return _build_query_plan(plan_data, question)

    except Exception as e:
        logger.error(f"Query planner failed: {e}", exc_info=True)
        raise ValueError(f"Query planning failed: {e}")


async def run_agent(agent, user_message: str):
    """Invoke a deepagent and return the raw content of its final message.

    deepagents/LangGraph agent.invoke() is synchronous — run it in a thread
    pool so it doesn't block the uvicorn event loop (which would make the
    /health endpoint unresponsive during long LLM pipelines).
    """
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None,
        functools.partial(
            agent.invoke,
            {"messages": [{"role": "user", "content": user_message}]},
        ),
    )

    messages = result.get("messages", [])
    if not messages:
        raise ValueError("Agent returned no messages")

    last_message = messages[-1]
    return (
        last_message.content
        if hasattr(last_message, "content")
        else str(last_message)
    )


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


def _extract_json_plan(content: str, required_key: str = "sql") -> dict:
    """Extract the last JSON object containing required_key from agent output."""
    # Try to find a JSON block in the content
    # The agent is instructed to end with a raw JSON object

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
    key_pattern = re.escape(f'"{required_key}"')
    json_matches = list(re.finditer(r'\{[^{}]*' + key_pattern + r'[^{}]*\}', content, re.DOTALL))
    if not json_matches:
        # Try a broader match
        json_matches = list(re.finditer(r'\{.*?' + key_pattern + r'.*?\}', content, re.DOTALL))

    if json_matches:
        last_match = json_matches[-1]
        try:
            return json.loads(last_match.group())
        except json.JSONDecodeError:
            pass

    # Nested-object fallback: everything from the first "{" to the last "}"
    # (regex approaches above can't balance braces, e.g. a plan with nested widgets)
    first, last = content.find("{"), content.rfind("}")
    if first != -1 and last > first:
        try:
            return json.loads(content[first:last + 1])
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
