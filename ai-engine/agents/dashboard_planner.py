"""
Dashboard Designer — deepagents pipeline for AI dashboard generation.

This is the workload the multi-agent architecture exists for: given a
natural-language brief ("build me a hiring overview dashboard"), the designer
  1. analyzes the data landscape (delegating to schema-analyst when the
     pre-loaded context doesn't cover it),
  2. decides which widgets tell the story (KPI tiles, trends, breakdowns),
  3. describes each widget's data requirements (no SQL yet),
  4. returns one DashboardPlan JSON.

The Trino SQL for every widget is then written in ONE follow-up call
(_generate_widget_sql_batch, cheap model) instead of one sql-generator subagent
round trip per widget — a 7+ widget dashboard used to fan out into 15-30+ gpt-4o
calls in a couple minutes, which is what was triggering OpenAI 429s.

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
from pydantic import BaseModel, Field

from llm.providers import make_langchain_model
from agents.orchestrator import _extract_json_from_files, _extract_json_plan, _normalize_content, run_agent
from events import EventEmitter
from agents.subagents.schema_analyst import build_schema_analyst_subagent
from agents.subagents.sql_generator import SQL_GENERATOR_SYSTEM_PROMPT
from agents.tools.schema_tools import list_available_sources
from config import settings
from models import DashboardPlan, WidgetPlan
from llm.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


# Passed to create_deep_agent as response_format=. This is the actual fix for
# the designer's final answer sometimes being prose instead of JSON (or, per
# deepagents' own filesystem-eviction behavior, a virtual file) — with
# response_format set, the framework runs a dedicated structured-output step
# after the ReAct loop finishes, enforced by the API itself, instead of
# trusting the model to voluntarily end its chat reply with clean JSON.
class GridPosition(BaseModel):
    x: int = 0
    y: int = 0
    w: int = 6
    h: int = 4


class WidgetDesign(BaseModel):
    title: str
    chart_type: str = "table"
    grid_position: GridPosition = Field(default_factory=GridPosition)
    data_requirements: str = Field(
        default="",
        description=(
            "Precise spec of tables/columns/joins/filters/grouping needed to "
            "produce this widget's SQL — written for a SQL author who has the "
            "schema but not your reasoning."
        ),
    )
    explanation: str = ""


class DashboardDesign(BaseModel):
    name: str
    description: str = ""
    widgets: list[WidgetDesign]
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    explanation: str = ""

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

**Step 3 — Describe each widget's data requirements (do NOT write SQL yourself)**
SQL is written afterward, in one separate batched pass outside this conversation — you
only specify what each widget needs. For every widget, write a precise "data_requirements"
string: which table(s)/index(es), which exact columns (verbatim from the schema context),
any joins/unnests, filters, grouping, and ordering needed to produce that widget's shape.
Be as specific as you'd be briefing a SQL author who has the schema but not your reasoning.

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

Your final answer is captured as structured data (name, description, widgets, confidence,
explanation) — you don't need to format JSON yourself. Do NOT repeat the schema context
in your answer — the SQL-writing pass that follows already has it. Keep each widget's
data_requirements to what's specific to THAT widget.

## Rules
- Use ONLY column names that appear verbatim in the schema context or schema-analyst output.
  NEVER invent plausible-sounding names — a real column is often shorter or differently named
  than you'd guess (e.g. a table may have "name" rather than "<entity>_name"). If a column you
  need isn't listed, delegate to schema-analyst to verify before describing the widget's
  data_requirements.
- Shape each widget's data_requirements to its chart type (a "number" tile must yield exactly
  one value)
- If the brief cannot be served by the available data, return your best partial dashboard
  with confidence below 0.3 and say what's missing in the explanation
"""


def create_dashboard_designer(model: str = "anthropic:claude-sonnet-5", limiters: dict | None = None) -> object:
    """Create the dashboard designer deepagent."""
    limiters = limiters or {}
    resolved_model = make_langchain_model(model, limiters.get("frontier"))
    return create_deep_agent(
        model=resolved_model,
        system_prompt=DASHBOARD_DESIGNER_SYSTEM_PROMPT,
        tools=[list_available_sources],
        subagents=[
            build_schema_analyst_subagent(limiters.get("fast")),
        ],
        response_format=DashboardDesign,
    )


# Fallback for when the designer's final chat message isn't parseable JSON (a
# multi-turn tool-calling agent doesn't reliably obey "JSON only, nothing before
# or after" — it may add a prose preamble/summary regardless). response_format=
# json_object is enforced by the OpenAI API itself, so this reformatting call
# can't fail the way parsing free-form chat text can. If the raw output was cut
# off mid-thought, build the best dashboard supportable by what IS there and
# lower confidence accordingly.
STRUCTURE_WIDGET_DESIGN_PROMPT = """You are given a dashboard designer's raw output, which
describes a set of analytics widgets for a natural-language brief. It may include stray prose
before/after the structured plan, or may have been cut off before finishing. Reconstruct it
into this EXACT JSON schema — infer widget details from what's clearly described; if the
output was cut off, keep only the widgets you can confidently reconstruct and lower confidence.

Respond ONLY with:
{
  "name": "<short dashboard name>",
  "description": "<one-sentence description>",
  "widgets": [
    {
      "title": "<widget title>",
      "chart_type": "<table|bar|line|pie|area|scatter|number|gauge>",
      "grid_position": {"x": 0, "y": 0, "w": 6, "h": 4},
      "data_requirements": "<precise spec of tables/columns/joins/filters/grouping needed>",
      "explanation": "<what this widget shows>"
    }
  ],
  "confidence": <0.0 to 1.0>,
  "explanation": "<how the dashboard answers the brief>"
}"""


async def _structure_widget_design(provider: OpenAIProvider, prompt: str, raw_output: str) -> dict:
    """Guaranteed-JSON fallback: reformat the designer's raw chat output into the widget schema."""
    user_prompt = f"Original dashboard brief:\n{prompt}\n\nDesigner's raw output to structure:\n{raw_output}"
    return await provider.generate_json(STRUCTURE_WIDGET_DESIGN_PROMPT, user_prompt)


# The designer only decides WHAT each widget needs (see DASHBOARD_DESIGNER_SYSTEM_PROMPT
# Step 3) — this prompt drives the single follow-up call that writes ALL of the widgets'
# SQL at once, replacing what used to be one sql-generator subagent round trip per widget.
BATCH_SQL_SYSTEM_PROMPT = SQL_GENERATOR_SYSTEM_PROMPT + """

## Batch Mode
You will be given a schema context and a JSON list of widgets that each need ONE Trino SQL
query. Write all of them in this single response — do not skip any, do not ask follow-up
questions.

Respond ONLY with:
{"widgets": [
  {"title": "<same title as given>", "sql": "<Trino SELECT SQL>", "confidence": <0.0-1.0>},
  ...
]}
The "widgets" array must have exactly the same number of entries, in the same order, as the
widgets you were given."""


async def generate_dashboard_plan(
    agent,
    prompt: str,
    sql_provider: OpenAIProvider,
    extra_context: Optional[str] = None,
    current_dashboard: Optional[dict] = None,
    emitter: Optional[EventEmitter] = None,
) -> DashboardPlan:
    """
    Invoke the dashboard designer and extract a structured DashboardPlan.

    Args:
        agent: The compiled deepagent from create_dashboard_designer()
        prompt: The dashboard brief (generate) or instruction (refine)
        sql_provider: Cheap-model OpenAI client used for the one batched SQL call
        extra_context: Pre-loaded schema context from the Core API
        current_dashboard: Existing dashboard state when refining
        emitter: Progress event sink for SSE streaming (NullEmitter if absent)

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
        raw_content, files, structured = await run_agent(
            agent, user_message, emitter=emitter, agent_name="dashboard-designer"
        )
        content = _normalize_content(raw_content)
        logger.debug(f"Designer output (last 500 chars): {content[-500:]}")

        # response_format=DashboardDesign (see create_dashboard_designer) makes this
        # the normal path — the framework enforces the schema via a dedicated
        # structured-output step, so this doesn't depend on the model voluntarily
        # ending its chat reply with clean JSON. The extraction attempts below are
        # a defensive fallback for the rare case structured_response comes back None.
        if structured is not None:
            data = structured.model_dump() if hasattr(structured, "model_dump") else structured
        else:
            logger.warning("Designer returned no structured_response — falling back to text extraction")
            try:
                data = _extract_json_plan(content, required_key="widgets")
            except ValueError:
                data = _extract_json_from_files(files, "widgets")
            if data is None:
                logger.warning(
                    "Designer's final message and virtual files had no parseable "
                    "JSON — falling back to a guaranteed-JSON structuring pass"
                )
                data = await _structure_widget_design(sql_provider, prompt, content)

        widget_designs = data.get("widgets") or []
        if not widget_designs:
            raise ValueError("Designer plan contains no widgets")

        # Reuse the same schema text the designer was given rather than asking
        # the model to transcribe it into its own output — with every registered
        # dataset pre-loaded (Core API sends the full catalog, not just the
        # relevant one), asking the model to echo that back risked truncating
        # the response before the JSON closed.
        if emitter is not None:
            await emitter.emit(
                "stage",
                stage="widget_sql_started",
                detail=f"writing SQL for {len(widget_designs)} widgets in one batched call",
            )
        sql_by_index = await _generate_widget_sql_batch(sql_provider, extra_context or "", widget_designs)
        if emitter is not None:
            await emitter.emit("stage", stage="widget_sql_done")

        return _build_dashboard_plan(data, prompt, sql_by_index)
    except Exception as e:
        logger.error(f"Dashboard designer failed: {e}", exc_info=True)
        raise ValueError(f"Dashboard planning failed: {e}")


async def _generate_widget_sql_batch(
    provider: OpenAIProvider, schema_context: str, widget_designs: list
) -> list:
    """One LLM call that writes SQL for every widget in the plan.

    Returns a list of SQL strings aligned by position with widget_designs.
    If the model returns fewer/more entries than requested, this pads/truncates
    defensively rather than raising — a missing widget is dropped downstream
    instead of failing the whole dashboard.
    """
    briefs = [
        {
            "title": w.get("title") or f"Widget {i + 1}",
            "chart_type": w.get("chart_type") or "table",
            "data_requirements": w.get("data_requirements") or w.get("explanation") or "",
        }
        for i, w in enumerate(widget_designs)
    ]
    user_prompt = (
        f"Schema context:\n{schema_context or '(none provided)'}\n\n"
        f"Widgets needing SQL (return exactly {len(briefs)} entries, same order):\n"
        + json.dumps(briefs, indent=2)
    )

    result = await provider.generate_widgets_sql(BATCH_SQL_SYSTEM_PROMPT, user_prompt)
    sql_entries = result.get("widgets") or []

    if len(sql_entries) != len(briefs):
        logger.warning(
            f"Batched widget SQL returned {len(sql_entries)} entries for "
            f"{len(briefs)} widgets requested — aligning by position."
        )

    sql_list = [(e.get("sql") or "").strip() for e in sql_entries]
    sql_list += [""] * (len(briefs) - len(sql_list))  # pad missing entries
    return sql_list[: len(briefs)]  # truncate any extras


def _build_dashboard_plan(data: dict, prompt: str, sql_by_index: list) -> DashboardPlan:
    """Build and validate a DashboardPlan from the extracted JSON data."""
    raw_widgets = data.get("widgets") or []
    if not raw_widgets:
        raise ValueError("Designer plan contains no widgets")

    widgets = []
    for i, w in enumerate(raw_widgets):
        sql = (sql_by_index[i] if i < len(sql_by_index) else "").strip()
        if not sql:
            logger.warning(f"Dropping widget '{w.get('title')}' — no SQL produced")
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
