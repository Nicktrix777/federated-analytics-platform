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

Invocation is fully async (astream_events) so:
  - tools run as coroutines on the service event loop (pooled connections),
  - every LLM/tool call is surfaced as a progress event (SSE) and counted
    into a per-agent depth profile that is logged for every request,
  - a recursion_limit caps how long a wandering agent can spin.

The final answer is captured with response_format= structured output — the
framework runs a dedicated schema-enforced step after the ReAct loop, instead
of trusting the model to end its chat reply with clean JSON. The regex
extraction below survives only as a fallback.
"""

import json
import logging
import re
from typing import Optional

from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field

from agents.subagents.schema_analyst import SCHEMA_ANALYST_SUBAGENT
from agents.subagents.sql_generator import SQL_GENERATOR_SUBAGENT
from agents.tools.schema_tools import list_available_sources
from config import settings
from events import AgentEventRelay, EventEmitter, NullEmitter
from models import Clarification, QueryPlan, QueryStep

logger = logging.getLogger(__name__)


# Enforced by response_format= — see create_query_planner.
class QueryStepDesign(BaseModel):
    step_id: int = 1
    description: str = ""
    catalog: str = ""
    schema_name: str = "public"
    table: str = ""


class QueryPlanDesign(BaseModel):
    question: str = ""
    sql: Optional[str] = Field(default=None, description="Complete Trino SELECT SQL from sql-generator (omit when clarifying)")
    steps: list[QueryStepDesign] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    explanation: str = ""
    # PR5: flat optional clarification fields — NOT a Pydantic Union
    # (response_format reliability through deepagents structured output).
    clarification_question: Optional[str] = Field(default=None, description="A clarifying question to ask the user instead of guessing")
    clarification_options: list[str] = Field(default_factory=list, description="Suggested answer options for the clarification")
    clarification_kind: str = Field(default="ambiguous", description="Kind of clarification: ambiguous_entity, unresolved_value, missing_data, ambiguous")


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
Provide the schema context to the sql-generator subagent VERBATIM — do NOT summarize or trim it.
You MUST forward, unchanged:
  - the exact Trino table paths (with quotes),
  - the nested-field access paths and CROSS JOIN UNNEST recipes,
  - every "Known ... field values" list and per-field sample values ([e.g. ...]) from the context,
  - the entire "Coded-Column Lookups" block (if present) — the generator needs it to write
    subqueries instead of guessing coded literals.
Those sample values are how the generator picks correct filter literals — e.g. that nationality
is stored as the code 'IND', not the word 'Indian'. If you drop them, the query runs but returns
wrong/zero results. When unsure how much to pass, pass MORE, not less.
If the user message contains a "first-pass draft" block (a failed quick attempt with its
validation issues), pass the draft AND its issues to sql-generator so it can repair the draft
instead of starting from scratch — unless the draft is clearly the wrong approach.

**Step 3 — Return the plan**
SQL safety and Trino-compatibility checks run automatically after you respond — you do NOT
need to call a validator subagent. Your final answer is captured as structured data with these
fields: question (the original question), sql (the Trino SQL from sql-generator, verbatim),
steps (one entry per table read: step_id, description, catalog, schema_name, table),
confidence (0.0-1.0), explanation (human-readable description of what the query does).

## Clarify Instead of Guessing (PR5)
When the question is genuinely ambiguous, unclear, or references data that doesn't exist:
- If you can't determine WHICH table/column the user means (e.g. "show data for India" and
  multiple datasets have country columns), set clarification_question and clarification_options
  instead of sql. Leave sql empty.
- If a filter value doesn't match anything in the known sample values, set clarification_question
  with kind "unresolved_value".
- If no dataset covers what the user is asking about, set kind "missing_data".
- Provide 2-4 concise options when possible (the user will click one).
- Do NOT clarify trivial ambiguities — only when the choice would materially change the query.
- If you're unsure whether to clarify or plan, prefer planning with a lower confidence and
  clear explanation.

## Important Rules
- NEVER generate INSERT, UPDATE, DELETE, or DDL statements
- If confidence < 0.3, still return the plan with a clear explanation of limitations
"""


def create_query_planner(model: str = "anthropic:claude-sonnet-5") -> object:
    """
    Create the main query planner deepagent.

    Args:
        model: deepagents model string (e.g., 'anthropic:claude-sonnet-5')

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
        response_format=QueryPlanDesign,
    )


async def generate_query_plan(
    agent,
    question: str,
    extra_context: Optional[str] = None,
    emitter: Optional[EventEmitter] = None,
) -> "QueryPlan | Clarification":
    """
    Invoke the query planner and extract a structured QueryPlan or Clarification.

    Args:
        agent: The compiled deepagent from create_query_planner()
        question: Natural language question from the user
        extra_context: Optional additional context (e.g., pre-loaded schema,
            fast-path draft hand-off)
        emitter: Progress event sink for SSE streaming (NullEmitter if absent)

    Returns:
        A validated QueryPlan or Clarification Pydantic model

    Raises:
        ValueError: If the agent output cannot be parsed
    """
    user_message = question
    if extra_context:
        user_message = f"{question}\n\nAdditional context:\n{extra_context}"

    logger.info(f"Invoking query planner for: {question[:100]}")

    try:
        raw_content, files, structured = await run_agent(
            agent, user_message, emitter=emitter, agent_name="query-planner"
        )

        if structured is not None:
            plan_data = structured.model_dump() if hasattr(structured, "model_dump") else structured
        else:
            logger.warning("Planner returned no structured_response — falling back to text extraction")
            content = _normalize_content(raw_content)
            logger.debug(f"Agent output (last 500 chars): {content[-500:]}")
            try:
                plan_data = _extract_json_plan(content)
            except ValueError:
                plan_data = _extract_json_from_files(files, "sql")
                if plan_data is None:
                    raise

        # PR5: check if the planner returned a clarification instead of SQL.
        clarification = _maybe_build_clarification(plan_data)
        if clarification is not None:
            return clarification
        return _build_query_plan(plan_data, question)

    except Exception as e:
        logger.error(f"Query planner failed: {e}", exc_info=True)
        raise ValueError(f"Query planning failed: {e}")


async def run_agent(
    agent,
    user_message: str,
    emitter: Optional[EventEmitter] = None,
    agent_name: str = "agent",
):
    """Invoke a deepagent and return (final message content, virtual files, structured_response).

    Streams LangGraph events so every inner LLM call, tool call, and subagent
    hand-off is (a) forwarded to the emitter for SSE and (b) counted into a
    depth profile that is logged for every request — this is how we see what
    the pipeline actually did instead of guessing. With a NullEmitter the
    events still drive the logged depth profile.

    structured_response is populated only for agents built with a
    response_format= schema — the framework forces a dedicated
    structured-output step for it, which is far more reliable than trusting
    the model to end its chat reply with clean JSON. Callers fall back to
    parsing `content` (and, failing that, `files` — deepagents gives every
    agent built-in filesystem tools and will sometimes write a large
    structured output there instead of inlining it in the chat reply, or
    auto-evict a large tool result to a file).
    """
    if emitter is None:
        emitter = NullEmitter()
    relay = AgentEventRelay(emitter, agent_name)
    config = {"recursion_limit": settings.agent_recursion_limit}
    inputs = {"messages": [{"role": "user", "content": user_message}]}

    result = None
    root_run_id = None
    async for event in agent.astream_events(inputs, config=config, version="v2"):
        if root_run_id is None:
            root_run_id = str(event.get("run_id", ""))
        kind = event.get("event", "")
        if kind in ("on_chat_model_start", "on_chat_model_end", "on_tool_start", "on_tool_end"):
            await relay.handle(event)
        elif kind == "on_chain_end" and str(event.get("run_id", "")) == root_run_id:
            result = (event.get("data") or {}).get("output")

    logger.info(f"{agent_name} depth profile: {relay.depth_profile()}")

    if not isinstance(result, dict):
        raise ValueError(f"Agent stream ended without a final state (got {type(result).__name__})")

    messages = result.get("messages", [])
    if not messages:
        raise ValueError("Agent returned no messages")

    last_message = messages[-1]
    content = (
        last_message.content
        if hasattr(last_message, "content")
        else str(last_message)
    )
    return content, (result.get("files") or {}), result.get("structured_response")


def _extract_json_from_files(files: dict, required_key: str) -> Optional[dict]:
    """Scan deepagents virtual-filesystem writes for a JSON object containing required_key.

    Fallback for when the model wrote its structured output to a file (via the
    write_file tool, or the framework's own large-tool-result eviction) instead
    of inlining it in the final chat message.
    """
    for path, file_data in files.items():
        raw = file_data.get("content") if isinstance(file_data, dict) else getattr(file_data, "content", None)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and required_key in data:
            logger.info(f"Recovered JSON plan from agent-written file: {path}")
            return data
    return None


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


def _maybe_build_clarification(data: dict) -> "Clarification | None":
    """Check if the planner returned a clarification instead of SQL.

    The structured output has flat optional clarification fields
    (clarification_question, clarification_options, clarification_kind).
    When clarification_question is set and sql is absent, it's a clarification.
    Also handles the fast-path JSON shape: {"clarification": {...}}.
    """
    # Structured output shape from the deepagents orchestrator.
    clar_q = data.get("clarification_question")
    sql = (data.get("sql") or "").strip()
    if clar_q and not sql:
        return Clarification(
            question=clar_q,
            options=data.get("clarification_options", []),
            kind=data.get("clarification_kind", "ambiguous"),
        )
    # Fast-path JSON shape: {"clarification": {"question": ..., ...}}
    clar_dict = data.get("clarification")
    if isinstance(clar_dict, dict) and clar_dict.get("question") and not sql:
        return Clarification(
            question=clar_dict["question"],
            options=clar_dict.get("options", []),
            kind=clar_dict.get("kind", "ambiguous"),
        )
    return None


def _build_query_plan(data: dict, question: str) -> QueryPlan:
    """Build and validate a QueryPlan from the extracted JSON data."""
    sql = (data.get("sql") or "").strip()
    if not sql:
        raise ValueError("Agent plan contains no SQL and no clarification question")

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
        question=data.get("question", question) or question,
        sql=sql,
        steps=steps,
        confidence=confidence,
        explanation=data.get("explanation", ""),
    )

