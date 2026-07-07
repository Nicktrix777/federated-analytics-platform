"""
Dashboard Designer — deepagents pipeline for AI dashboard generation.

This is the workload the multi-agent architecture exists for: given a
natural-language brief ("build me a hiring overview dashboard"), the designer
  1. analyzes the data landscape (delegating to schema-analyst when the
     pre-loaded context doesn't cover it),
  2. decides which widgets tell the story (KPI tiles, trends, breakdowns),
  3. delegates the Trino SQL for each widget to sql-generator,
  4. returns one DashboardPlan JSON.

The same agent also REFINES an existing dashboard: when the request carries
the current dashboard state, the instruction is applied to it and the full
updated plan is returned ("alter it in natural language").

Latency note: a dashboard is generated once and refined occasionally, so this
path deliberately uses the full pipeline — there is no fast path here.
"""

import json
import logging
from typing import Optional

from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model

from agents.orchestrator import _extract_json_plan, _normalize_content, run_agent
from agents.subagents.schema_analyst import SCHEMA_ANALYST_SUBAGENT
from agents.subagents.sql_generator import SQL_GENERATOR_SUBAGENT
from agents.tools.schema_tools import list_available_sources
from config import settings
from models import DashboardPlan, WidgetPlan

logger = logging.getLogger(__name__)

DASHBOARD_DESIGNER_SYSTEM_PROMPT = """You are the Dashboard Designer for a Federated Analytics Platform.

You turn a natural-language brief into a complete analytics dashboard: a set of widgets,
each backed by a Trino SQL query over the platform's federated data sources.

## Workflow

**Step 1 — Understand the data (delegate to schema-analyst, CONDITIONAL)**
Check the "Additional context" block first — the Core API pre-loads dataset names, Trino
paths, and columns there. If it already covers what the brief needs, skip schema-analyst.
Otherwise delegate to schema-analyst to discover the relevant tables and joins.

**Step 2 — Design the widgets**
Pick 4-6 widgets that best answer the brief. Compose a story, not a random set:
- 2-4 "number" KPI tiles for headline metrics (single row, single numeric column)
- 1-2 trend charts ("line"/"area") if any time/date column exists
- 1-2 breakdowns ("bar"/"pie": one category column + one value column, ≤ 12 rows)
- optionally one detail "table" (LIMIT ≤ 50)

**Step 3 — Write the SQL (delegate to sql-generator)**
Send sql-generator the schema context plus ALL widget specs in ONE message and ask for
one query per widget. Every query must be a SELECT with fully-qualified
catalog.schema.table names and Trino syntax.

**Step 4 — Lay out the grid**
The grid is 12 columns wide. No overlaps. Convention:
- "number" tiles: w=3, h=2, placed left-to-right on the top row (y=0)
- charts: w=6, h=4, two per row below the tiles
- "table": w=12, h=4, full width at the bottom

## Refinement mode

If the user message contains a "Current dashboard" block, you are EDITING that dashboard,
not creating one. Apply the instruction (add/remove/change widgets, retitle, re-layout),
keep everything the user didn't ask to change, and return the FULL updated dashboard.

## Output

Return a final JSON object in EXACTLY this format, as the LAST thing you output:
{
  "name": "<short dashboard name>",
  "description": "<one-sentence description>",
  "widgets": [
    {
      "title": "<widget title>",
      "sql": "<Trino SELECT SQL>",
      "chart_type": "<table|bar|line|pie|area|scatter|number|gauge>",
      "grid_position": {"x": 0, "y": 0, "w": 6, "h": 4},
      "explanation": "<what this widget shows>"
    }
  ],
  "confidence": <0.0 to 1.0>,
  "explanation": "<how the dashboard answers the brief>"
}

## Rules
- NEVER generate INSERT, UPDATE, DELETE, or DDL statements
- Use ONLY column names that appear verbatim in the schema context or schema-analyst output.
  NEVER invent plausible-sounding names (e.g. departments has "name", NOT "department_name";
  performance_reviews has "score", NOT "review_score"). If a column you need isn't listed,
  delegate to schema-analyst to verify before writing SQL.
- Shape each query to its chart type (a "number" tile must return exactly one value)
- If the brief cannot be served by the available data, return your best partial dashboard
  with confidence below 0.3 and say what's missing in the explanation
"""


def create_dashboard_designer(model: str = "openai:gpt-4o") -> object:
    """Create the dashboard designer deepagent."""
    resolved_model = init_chat_model(
        model,
        max_retries=settings.llm_max_retries,
        timeout=settings.llm_timeout_seconds,
    )
    return create_deep_agent(
        model=resolved_model,
        system_prompt=DASHBOARD_DESIGNER_SYSTEM_PROMPT,
        tools=[list_available_sources],
        subagents=[
            SCHEMA_ANALYST_SUBAGENT,
            SQL_GENERATOR_SUBAGENT,
        ],
    )


async def generate_dashboard_plan(
    agent,
    prompt: str,
    extra_context: Optional[str] = None,
    current_dashboard: Optional[dict] = None,
) -> DashboardPlan:
    """
    Invoke the dashboard designer and extract a structured DashboardPlan.

    Args:
        agent: The compiled deepagent from create_dashboard_designer()
        prompt: The dashboard brief (generate) or instruction (refine)
        extra_context: Pre-loaded schema context from the Core API
        current_dashboard: Existing dashboard state when refining

    Raises:
        ValueError: If the agent output cannot be parsed into a DashboardPlan
    """
    user_message = prompt
    if current_dashboard:
        user_message += (
            "\n\nCurrent dashboard (apply the instruction above to this):\n"
            + json.dumps(current_dashboard, indent=2)
        )
    if extra_context:
        user_message += f"\n\nAdditional context:\n{extra_context}"

    logger.info(f"Invoking dashboard designer for: {prompt[:100]}")

    try:
        content = _normalize_content(await run_agent(agent, user_message))
        logger.debug(f"Designer output (last 500 chars): {content[-500:]}")
        data = _extract_json_plan(content, required_key="widgets")
        return _build_dashboard_plan(data, prompt)
    except Exception as e:
        logger.error(f"Dashboard designer failed: {e}", exc_info=True)
        raise ValueError(f"Dashboard planning failed: {e}")


def _build_dashboard_plan(data: dict, prompt: str) -> DashboardPlan:
    """Build and validate a DashboardPlan from the extracted JSON data."""
    raw_widgets = data.get("widgets") or []
    if not raw_widgets:
        raise ValueError("Designer plan contains no widgets")

    widgets = []
    for i, w in enumerate(raw_widgets):
        sql = (w.get("sql") or w.get("query_sql") or "").strip()
        if not sql:
            continue
        grid = w.get("grid_position")
        if not isinstance(grid, dict) or not {"x", "y", "w", "h"} <= set(grid):
            # Fallback layout: two 6-wide charts per row
            grid = {"x": (i % 2) * 6, "y": (i // 2) * 4, "w": 6, "h": 4}
        widgets.append(WidgetPlan(
            title=w.get("title") or f"Widget {i + 1}",
            sql=sql,
            chart_type=w.get("chart_type") or "table",
            grid_position=grid,
            explanation=w.get("explanation") or "",
        ))

    if not widgets:
        raise ValueError("Designer plan contains no widgets with SQL")

    confidence = max(0.0, min(1.0, float(data.get("confidence", 0.7))))

    return DashboardPlan(
        name=data.get("name") or prompt[:60],
        description=data.get("description") or "",
        widgets=widgets,
        confidence=confidence,
        explanation=data.get("explanation") or "",
    )
