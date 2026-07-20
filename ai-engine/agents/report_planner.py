"""
Report Designer — deepagents pipeline for AI Excel-report generation.

The dashboard designer's sibling, built on the same plan-then-batch-SQL
pattern: given a natural-language brief ("monthly contract revenue report by
agency"), the designer
  1. analyzes the data landscape (delegating to schema-analyst when the
     pre-loaded context doesn't cover it),
  2. decides which worksheets tell the story (summary first, detail
     breakdowns after),
  3. describes each sheet's data requirements (no SQL yet),
  4. returns one ReportPlan JSON.

The Trino SQL for every sheet — plus its per-column Excel formats — is then
written in ONE follow-up call (_generate_sheet_sql_batch, same batching that
fixed the dashboard pipeline's per-widget 429 storms).

The same agent also REFINES an existing report: when the request carries the
current report state, the instruction is applied to it and the full updated
plan is returned.

Latency note: a report is generated once and refined occasionally, so this
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
from models import ReportPlan, SheetPlan
from llm.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


# Passed to create_deep_agent as response_format= — same rationale as the
# dashboard designer: with response_format set, the framework runs a dedicated
# structured-output step after the ReAct loop finishes, enforced by the API
# itself, instead of trusting the model to voluntarily end its chat reply with
# clean JSON.
class SheetDesign(BaseModel):
    title: str
    description: str = ""
    data_requirements: str = Field(
        default="",
        description=(
            "Precise spec of tables/columns/joins/filters/grouping/ordering "
            "needed to produce this sheet's SQL — written for a SQL author who "
            "has the schema but not your reasoning."
        ),
    )


class ReportDesign(BaseModel):
    name: str
    description: str = ""
    sheets: list[SheetDesign]
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    explanation: str = ""

REPORT_DESIGNER_SYSTEM_PROMPT = """You are the Report Designer for a Federated Analytics Platform.

You turn a natural-language brief into a complete Excel report: a set of worksheets,
each backed by a Trino SQL query over the platform's federated data sources.

## Workflow

**Step 1 — Understand the data (delegate to schema-analyst, CONDITIONAL)**
Check the "Additional context" block first — the Core API pre-loads dataset names, Trino
paths, and columns there. If it already covers what the brief needs, skip schema-analyst.
Otherwise delegate to schema-analyst to discover the relevant tables and joins.

**Step 2 — Design the sheets**
Pick 1-6 sheets that best answer the brief. Compose a document, not a random set:
- Sheet 1 is an executive summary: headline aggregates / KPI rows
- Later sheets are detail breakdowns (per-entity, per-period, top-N lists)
- Each sheet is ONE flat tabular query — a reader opens the workbook and reads it top down

**Step 3 — Describe each sheet's data requirements (do NOT write SQL yourself)**
SQL is written afterward, in one separate batched pass outside this conversation — you
only specify what each sheet needs. For every sheet, write a precise "data_requirements"
string: which table(s)/index(es), which exact columns (verbatim from the schema context),
any joins/unnests, filters, grouping, and the ordering that makes the sheet readable.
Be as specific as you'd be briefing a SQL author who has the schema but not your reasoning.
Sheet conventions the SQL pass will follow:
- Every output column gets a human-readable header (quoted SQL alias) — name them
- Every sheet has a meaningful ORDER BY
- No row limits unless a top-N is the analytical intent — the Excel exporter caps rows itself

## Refinement mode

If the user message contains a "Current report" block, you are EDITING that report,
not creating one. Apply the instruction (add/remove/change sheets, retitle, reorder),
keep everything the user didn't ask to change, and return the FULL updated report.

## Output

Your final answer is captured as structured data (name, description, sheets, confidence,
explanation) — you don't need to format JSON yourself. Do NOT repeat the schema context
in your answer — the SQL-writing pass that follows already has it. Keep each sheet's
data_requirements to what's specific to THAT sheet.

## Rules
- Use ONLY column names that appear verbatim in the schema context or schema-analyst output.
  NEVER invent plausible-sounding names — a real column is often shorter or differently named
  than you'd guess (e.g. a table may have "name" rather than "<entity>_name"). If a column you
  need isn't listed, delegate to schema-analyst to verify before describing the sheet's
  data_requirements.
- If the brief cannot be served by the available data, return your best partial report
  with confidence below 0.3 and say what's missing in the explanation
"""


def create_report_designer(model: str = "anthropic:claude-sonnet-5", limiters: dict | None = None) -> object:
    """Create the report designer deepagent."""
    limiters = limiters or {}
    resolved_model = make_langchain_model(model, limiters.get("frontier"))
    return create_deep_agent(
        model=resolved_model,
        system_prompt=REPORT_DESIGNER_SYSTEM_PROMPT,
        tools=[list_available_sources],
        subagents=[
            build_schema_analyst_subagent(limiters.get("fast")),
        ],
        response_format=ReportDesign,
    )


# Fallback for when the designer's final chat message isn't parseable JSON —
# same guaranteed-JSON reformat pass as the dashboard designer's
# STRUCTURE_WIDGET_DESIGN_PROMPT (see that comment for the full rationale).
STRUCTURE_SHEET_DESIGN_PROMPT = """You are given a report designer's raw output, which
describes a set of Excel report sheets for a natural-language brief. It may include stray
prose before/after the structured plan, or may have been cut off before finishing.
Reconstruct it into this EXACT JSON schema — infer sheet details from what's clearly
described; if the output was cut off, keep only the sheets you can confidently reconstruct
and lower confidence.

Respond ONLY with:
{
  "name": "<short report name>",
  "description": "<one-sentence description>",
  "sheets": [
    {
      "title": "<sheet title>",
      "description": "<what this sheet shows>",
      "data_requirements": "<precise spec of tables/columns/joins/filters/grouping/ordering needed>"
    }
  ],
  "confidence": <0.0 to 1.0>,
  "explanation": "<how the report answers the brief>"
}"""


async def _structure_report_design(provider: OpenAIProvider, prompt: str, raw_output: str) -> dict:
    """Guaranteed-JSON fallback: reformat the designer's raw chat output into the sheet schema."""
    user_prompt = f"Original report brief:\n{prompt}\n\nDesigner's raw output to structure:\n{raw_output}"
    return await provider.generate_json(STRUCTURE_SHEET_DESIGN_PROMPT, user_prompt)


# The designer only decides WHAT each sheet needs (see REPORT_DESIGNER_SYSTEM_PROMPT
# Step 3) — this prompt drives the single follow-up call that writes ALL of the sheets'
# SQL (and per-column Excel formats) at once.
BATCH_SHEET_SQL_SYSTEM_PROMPT = SQL_GENERATOR_SYSTEM_PROMPT + """

## Batch Mode — Report Sheets
You will be given a schema context and a JSON list of report sheets that each need ONE
Trino SQL query. Write all of them in this single response — do not skip any, do not ask
follow-up questions.

These queries fill Excel worksheets, not charts:
- Give every output column a human-readable header via a quoted SQL alias
  (e.g. SELECT sum(amount) AS "Total Revenue").
- Give every sheet a meaningful ORDER BY.
- Do NOT add a LIMIT unless the sheet is explicitly a top-N — the Excel exporter caps
  rows itself.

For each sheet also return "column_formats": one entry per output column, keyed by the
column's alias EXACTLY as it appears in the SELECT list, with one of:
text | integer | number | currency | percent | date | datetime
Omit columns where plain text is right — anything absent renders as text.

Respond ONLY with:
{"sheets": [
  {"title": "<same title as given>", "sql": "<Trino SELECT SQL>",
   "column_formats": {"<column alias>": "<format>"}, "confidence": <0.0-1.0>},
  ...
]}
The "sheets" array must have exactly the same number of entries, in the same order, as the
sheets you were given."""


async def generate_report_plan(
    agent,
    prompt: str,
    sql_provider: OpenAIProvider,
    extra_context: Optional[str] = None,
    current_report: Optional[dict] = None,
    emitter: Optional[EventEmitter] = None,
) -> ReportPlan:
    """
    Invoke the report designer and extract a structured ReportPlan.

    Args:
        agent: The compiled deepagent from create_report_designer()
        prompt: The report brief (generate) or instruction (refine)
        sql_provider: OpenAI client used for the one batched SQL call
        extra_context: Pre-loaded schema context from the Core API
        current_report: Existing report state when refining
        emitter: Progress event sink for SSE streaming (NullEmitter if absent)

    Raises:
        ValueError: If the agent output cannot be parsed into a ReportPlan
    """
    user_message = prompt
    if current_report:
        user_message += (
            "\n\nCurrent report (apply the instruction above to this):\n"
            + json.dumps(current_report, indent=2)
        )
    if extra_context:
        user_message += f"\n\nAdditional context:\n{extra_context}"

    logger.info(f"Invoking report designer for: {prompt[:100]}")

    try:
        raw_content, files, structured = await run_agent(
            agent, user_message, emitter=emitter, agent_name="report-designer"
        )
        content = _normalize_content(raw_content)
        logger.debug(f"Designer output (last 500 chars): {content[-500:]}")

        # response_format=ReportDesign (see create_report_designer) makes this
        # the normal path — the framework enforces the schema via a dedicated
        # structured-output step, so this doesn't depend on the model voluntarily
        # ending its chat reply with clean JSON. The extraction attempts below are
        # a defensive fallback for the rare case structured_response comes back None.
        if structured is not None:
            data = structured.model_dump() if hasattr(structured, "model_dump") else structured
        else:
            logger.warning("Designer returned no structured_response — falling back to text extraction")
            try:
                data = _extract_json_plan(content, required_key="sheets")
            except ValueError:
                data = _extract_json_from_files(files, "sheets")
            if data is None:
                logger.warning(
                    "Designer's final message and virtual files had no parseable "
                    "JSON — falling back to a guaranteed-JSON structuring pass"
                )
                data = await _structure_report_design(sql_provider, prompt, content)

        sheet_designs = data.get("sheets") or []
        if not sheet_designs:
            raise ValueError("Designer plan contains no sheets")

        # Reuse the same schema text the designer was given rather than asking
        # the model to transcribe it into its own output (same truncation risk
        # the dashboard pipeline hit).
        if emitter is not None:
            await emitter.emit(
                "stage",
                stage="sheet_sql_started",
                detail=f"writing SQL for {len(sheet_designs)} sheets in one batched call",
            )
        sql_by_index = await _generate_sheet_sql_batch(sql_provider, extra_context or "", sheet_designs)
        if emitter is not None:
            await emitter.emit("stage", stage="sheet_sql_done")

        return _build_report_plan(data, prompt, sql_by_index)
    except Exception as e:
        logger.error(f"Report designer failed: {e}", exc_info=True)
        raise ValueError(f"Report planning failed: {e}")


async def _generate_sheet_sql_batch(
    provider: OpenAIProvider, schema_context: str, sheet_designs: list
) -> list:
    """One LLM call that writes SQL + column formats for every sheet in the plan.

    Returns a list of {"sql", "column_formats", "confidence"} dicts aligned by
    position with sheet_designs. If the model returns fewer/more entries than
    requested, this pads/truncates defensively rather than raising — a missing
    sheet is dropped downstream instead of failing the whole report.
    """
    briefs = [
        {
            "title": s.get("title") or f"Sheet {i + 1}",
            "description": s.get("description") or "",
            "data_requirements": s.get("data_requirements") or s.get("description") or "",
        }
        for i, s in enumerate(sheet_designs)
    ]
    user_prompt = (
        f"Schema context:\n{schema_context or '(none provided)'}\n\n"
        f"Sheets needing SQL (return exactly {len(briefs)} entries, same order):\n"
        + json.dumps(briefs, indent=2)
    )

    result = await provider.generate_sheets_sql(BATCH_SHEET_SQL_SYSTEM_PROMPT, user_prompt)
    sql_entries = result.get("sheets") or []

    if len(sql_entries) != len(briefs):
        logger.warning(
            f"Batched sheet SQL returned {len(sql_entries)} entries for "
            f"{len(briefs)} sheets requested — aligning by position."
        )

    entries = [
        {
            "sql": (e.get("sql") or "").strip(),
            "column_formats": e.get("column_formats") if isinstance(e.get("column_formats"), dict) else {},
            "confidence": e.get("confidence", 0.7),
        }
        for e in sql_entries
    ]
    entries += [{"sql": "", "column_formats": {}, "confidence": 0.0}] * (len(briefs) - len(entries))  # pad missing entries
    return entries[: len(briefs)]  # truncate any extras


def _build_report_plan(data: dict, prompt: str, sql_by_index: list) -> ReportPlan:
    """Build and validate a ReportPlan from the extracted JSON data."""
    raw_sheets = data.get("sheets") or []
    if not raw_sheets:
        raise ValueError("Designer plan contains no sheets")

    sheets = []
    for i, s in enumerate(raw_sheets):
        entry = sql_by_index[i] if i < len(sql_by_index) else {}
        sql = (entry.get("sql") or "").strip()
        if not sql:
            logger.warning(f"Dropping sheet '{s.get('title')}' — no SQL produced")
            continue
        try:
            sheet_confidence = max(0.0, min(1.0, float(entry.get("confidence", 0.7))))
        except (TypeError, ValueError):
            sheet_confidence = 0.7
        sheets.append(SheetPlan(
            title=s.get("title") or f"Sheet {i + 1}",
            description=s.get("description") or "",
            sql=sql,
            column_formats=entry.get("column_formats") or {},
            position=len(sheets),  # sequential across the sheets that survive
            confidence=sheet_confidence,
        ))

    if not sheets:
        raise ValueError("Designer plan contains no sheets with SQL")

    confidence = max(0.0, min(1.0, float(data.get("confidence", 0.7))))

    return ReportPlan(
        name=data.get("name") or prompt[:60],
        description=data.get("description") or "",
        sheets=sheets,
        confidence=confidence,
        explanation=data.get("explanation") or "",
    )
