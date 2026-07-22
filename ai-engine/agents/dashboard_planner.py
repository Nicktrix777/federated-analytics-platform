"""
Dashboard Designer — deterministic two-call pipeline for AI dashboard generation.

Given a natural-language brief ("build me a hiring overview dashboard"), the
designer:
  1. makes ONE structured design call — decides which widgets tell the story
     (KPI tiles, trends, breakdowns) and describes each widget's data
     requirements (no SQL yet) — against the schema context the Core API/
     context_bundle already pre-loaded (no schema-analyst discovery step;
     that subagent only remains on the chat query-planner path).
  2. writes the Trino SQL for every widget in ONE follow-up batched call
     (_generate_widget_sql_batch) instead of one sql-generator subagent round
     trip per widget.

No LangGraph, no tools, no ReAct loop on this path — a 6-widget dashboard is
exactly 2 LLM calls, and GraphRecursionError is structurally impossible here.
Grid layout is no longer decided by the model; Core API assigns it
deterministically after verification (see docs/ux-workflow-overhaul-plan.md).

The same function also REFINES an existing dashboard: when the request carries
the current dashboard state, the instruction is applied to it and the full
updated plan is returned ("alter it in natural language").

Latency note: a dashboard is generated once and refined occasionally, so this
path deliberately favors quality over the fast-path's single-call brevity —
there is no fast path here, just fewer/cheaper calls than the old ReAct loop.
"""

import json
import logging
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from events import EventEmitter, NullEmitter
from agents.subagents.sql_generator import SQL_GENERATOR_SYSTEM_PROMPT
from models import DashboardPlan, WidgetPlan
from llm.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


# Validates the design call's JSON before it's trusted — a ValidationError here
# is the trigger to retry via _structure_widget_design (see generate_dashboard_plan).
# grid_position is intentionally NOT requested from the model (see
# DASHBOARD_DESIGN_SYSTEM_PROMPT) — Core API assigns layout deterministically
# after verification, so it's optional-ignored if a stray value shows up anyway.
class WidgetDesign(BaseModel):
    title: str
    chart_type: str = "table"
    grid_position: Optional[dict] = None
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

DASHBOARD_DESIGN_SYSTEM_PROMPT = """You are the Dashboard Designer for a Federated Analytics Platform.

You turn a natural-language brief into a complete analytics dashboard: a set of widgets,
each backed by a Trino SQL query over the platform's federated data sources.

## Data available to you

The "Additional context" block in the user message is the full pre-loaded schema —
registered datasets, columns, sample values, and curated join relationships. Use ONLY
what's there; there is no further schema discovery step in this pipeline.

## Design the widgets

Pick 4-6 widgets that best answer the brief. Compose a story, not a random set:
- 2-4 "number" KPI tiles for headline metrics (single row, single numeric column)
- 1-2 trend charts ("line"/"area") if any time/date column exists
- 1-2 breakdowns ("bar"/"pie": one category column + one value column, ≤ 12 rows)
- optionally one detail "table" (LIMIT ≤ 50)

## Describe each widget's data requirements (do NOT write SQL yourself)

SQL is written afterward, in one separate batched pass outside this conversation — you
only specify what each widget needs. For every widget, write a precise "data_requirements"
string: which table(s)/index(es), which exact columns (verbatim from the schema context),
any joins/unnests, filters, grouping, and ordering needed to produce that widget's shape.
Be as specific as you'd be briefing a SQL author who has the schema but not your reasoning.

Grid layout is assigned automatically after this step — do not describe positions.

## Refinement mode

If the user message contains a "Current dashboard" block, you are EDITING that dashboard,
not creating one. Apply the instruction (add/remove/change widgets, retitle), keep
everything the user didn't ask to change, and return the FULL updated dashboard.

## Rules
- Use ONLY column names that appear verbatim in the schema context. NEVER invent
  plausible-sounding names — a real column is often shorter or differently named than
  you'd guess (e.g. a table may have "name" rather than "<entity>_name").
- Shape each widget's data_requirements to its chart type (a "number" tile must yield
  exactly one value)
- If the brief cannot be served by the available data, return your best partial dashboard
  with confidence below 0.3 and say what's missing in the explanation

## Response Format

Respond ONLY with raw JSON matching this EXACT schema — no markdown, no code fences, no
prose before or after:
{
  "name": "<short dashboard name>",
  "description": "<one-sentence description>",
  "widgets": [
    {
      "title": "<widget title>",
      "chart_type": "<table|bar|line|pie|area|scatter|number|gauge>",
      "data_requirements": "<precise spec of tables/columns/joins/filters/grouping needed>",
      "explanation": "<what this widget shows>"
    }
  ],
  "confidence": <0.0 to 1.0>,
  "explanation": "<how the dashboard answers the brief>"
}"""


# Fallback for when the design call's JSON doesn't validate against
# DashboardDesign (missing/malformed fields) — a second, guaranteed-JSON-mode
# call reformats whatever came back into the exact schema. If the raw output
# was cut off mid-thought, build the best dashboard supportable by what IS
# there and lower confidence accordingly.
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
      "data_requirements": "<precise spec of tables/columns/joins/filters/grouping needed>",
      "explanation": "<what this widget shows>"
    }
  ],
  "confidence": <0.0 to 1.0>,
  "explanation": "<how the dashboard answers the brief>"
}"""


async def _structure_widget_design(provider: OpenAIProvider, prompt: str, raw_output: str) -> dict:
    """Guaranteed-JSON fallback: reformat the designer's raw output into the widget schema."""
    user_prompt = f"Original dashboard brief:\n{prompt}\n\nDesigner's raw output to structure:\n{raw_output}"
    return await provider.generate_json(STRUCTURE_WIDGET_DESIGN_PROMPT, user_prompt, max_tokens=8000)


# The design call only decides WHAT each widget needs (see DASHBOARD_DESIGN_SYSTEM_PROMPT)
# — this prompt drives the single follow-up call that writes ALL of the widgets'
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
    prompt: str,
    sql_provider: OpenAIProvider,
    extra_context: Optional[str] = None,
    current_dashboard: Optional[dict] = None,
    emitter: Optional[EventEmitter] = None,
) -> DashboardPlan:
    """
    Design (or refine) a dashboard as a deterministic two-call pipeline.

    Args:
        prompt: The dashboard brief (generate) or instruction (refine)
        sql_provider: OpenAI-compatible client used for BOTH the structured
            design call and the batched widget-SQL call
        extra_context: Pre-loaded schema context from the Core API
        current_dashboard: Existing dashboard state when refining
        emitter: Progress event sink for SSE streaming (NullEmitter if absent)

    Raises:
        ValueError: If the design/SQL calls fail or produce no usable widgets
    """
    emitter = emitter or NullEmitter()
    user_message = prompt
    if current_dashboard:
        user_message += (
            "\n\nCurrent dashboard (apply the instruction above to this):\n"
            + json.dumps(current_dashboard, indent=2)
        )
    if extra_context:
        user_message += f"\n\nAdditional context:\n{extra_context}"

    logger.info(f"Designing dashboard for: {prompt[:100]}")

    try:
        # max_tokens=8000 matches the batched SQL call's headroom (default is
        # 4000) — a brief that legitimately needs many widgets, each with a
        # detailed data_requirements spec, can otherwise get cut off mid-JSON.
        raw = await sql_provider.generate_json(DASHBOARD_DESIGN_SYSTEM_PROMPT, user_message, max_tokens=8000)
        try:
            design = DashboardDesign(**raw)
        except ValidationError as e:
            logger.warning(f"Dashboard design call didn't match the schema ({e}) — reformatting")
            reformatted = await _structure_widget_design(sql_provider, prompt, json.dumps(raw))
            design = DashboardDesign(**reformatted)
        data = design.model_dump()

        widget_designs = data.get("widgets") or []
        if not widget_designs:
            raise ValueError("Designer plan contains no widgets")

        # Reuse the same schema text the designer was given rather than asking
        # the model to transcribe it into its own output — with every registered
        # dataset pre-loaded (Core API sends the full catalog, not just the
        # relevant one), asking the model to echo that back risked truncating
        # the response before the JSON closed.
        await emitter.emit(
            "stage",
            stage="widget_sql_started",
            detail=f"writing SQL for {len(widget_designs)} widgets in one batched call",
        )
        sql_by_index = await _generate_widget_sql_batch(sql_provider, extra_context or "", widget_designs)
        await emitter.emit("stage", stage="widget_sql_done")

        return _build_dashboard_plan(data, prompt, sql_by_index)
    except Exception as e:
        logger.error(f"Dashboard designer failed: {e}", exc_info=True)
        raise ValueError(f"Dashboard planning failed: {e}")


async def _generate_widget_sql_batch(
    provider: OpenAIProvider, schema_context: str, widget_designs: list
) -> list:
    """One LLM call that writes SQL for every widget in the plan.

    Returns a list of SQL strings aligned to widget_designs by title first
    (the batch prompt asks for the same "title" back), falling back to
    position for any entry with no/duplicate title match — this survives the
    model reordering or dropping an entry instead of misattributing SQL to the
    wrong widget.
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
            f"{len(briefs)} widgets requested — aligning by title/position."
        )

    by_title: dict[str, dict] = {}
    for e in sql_entries:
        t = (e.get("title") or "").strip()
        if t and t not in by_title:
            by_title[t] = e

    sql_list = []
    for i, brief in enumerate(briefs):
        entry = by_title.get(brief["title"]) or (sql_entries[i] if i < len(sql_entries) else {})
        sql_list.append((entry.get("sql") or "").strip())
    return sql_list


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
