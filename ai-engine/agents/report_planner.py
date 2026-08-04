"""
Report Designer — deterministic two-call pipeline for AI Excel-report generation.

The dashboard designer's sibling, built on the same design-then-batch-SQL
pattern: given a natural-language brief ("monthly contract revenue report by
agency"), the designer
  1. makes ONE structured design call — decides which worksheets tell the
     story (summary first, detail breakdowns after) and describes each
     sheet's data requirements (no SQL yet) — against the schema context the
     Core API/context_bundle already pre-loaded (no schema-analyst discovery
     step; that subagent only remains on the chat query-planner path).
  2. writes the Trino SQL for every sheet — plus its per-column Excel
     formats — in ONE follow-up call (_generate_sheet_sql_batch).

No LangGraph, no tools, no ReAct loop on this path — a 5-sheet report is
exactly 2 LLM calls, and GraphRecursionError is structurally impossible here.

The same function also REFINES an existing report: when the request carries the
current report state, the instruction is applied to it and the full updated
plan is returned.

Latency note: a report is generated once and refined occasionally, so this
path deliberately favors quality over the fast-path's single-call brevity —
there is no fast path here, just fewer/cheaper calls than the old ReAct loop.
"""

import json
import logging
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from events import EventEmitter, NullEmitter
from agents.subagents.sql_generator import SQL_GENERATOR_SYSTEM_PROMPT
from models import ReportPlan, SheetPlan
from llm.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


# Validates the design call's JSON before it's trusted — a ValidationError here
# is the trigger to retry via _structure_report_design (see generate_report_plan).
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

REPORT_DESIGN_SYSTEM_PROMPT = """You are the Report Designer for a Federated Analytics Platform.

You turn a natural-language brief into a complete Excel report: a set of worksheets,
each backed by a Trino SQL query over the platform's federated data sources.

## Data available to you

The "Additional context" block in the user message is the full pre-loaded schema —
registered datasets, columns, sample values, and curated join relationships. Use ONLY
what's there; there is no further schema discovery step in this pipeline.

## Design the sheets

Pick 1-6 sheets that best answer the brief. Compose a document, not a random set:
- Sheet 1 is an executive summary: headline aggregates / KPI rows
- Later sheets are detail breakdowns (per-entity, per-period, top-N lists)
- Each sheet is ONE flat tabular query — a reader opens the workbook and reads it top down

## Describe each sheet's data requirements (do NOT write SQL yourself)

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

## Rules
- Use ONLY column names that appear verbatim in the schema context. NEVER invent
  plausible-sounding names — a real column is often shorter or differently named than
  you'd guess (e.g. a table may have "name" rather than "<entity>_name").
- If the brief cannot be served by the available data, return your best partial report
  with confidence below 0.3 and say what's missing in the explanation

## Response Format

Respond ONLY with raw JSON matching this EXACT schema — no markdown, no code fences, no
prose before or after:
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


# Fallback for when the design call's JSON doesn't validate against
# ReportDesign (missing/malformed fields) — a second, guaranteed-JSON-mode call
# reformats whatever came back into the exact schema. If the raw output was
# cut off mid-thought, build the best report supportable by what IS there and
# lower confidence accordingly.
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
    """Guaranteed-JSON fallback: reformat the designer's raw output into the sheet schema."""
    user_prompt = f"Original report brief:\n{prompt}\n\nDesigner's raw output to structure:\n{raw_output}"
    return await provider.generate_json(STRUCTURE_SHEET_DESIGN_PROMPT, user_prompt, max_tokens=8000)


# The design call only decides WHAT each sheet needs (see REPORT_DESIGN_SYSTEM_PROMPT)
# — this prompt drives the single follow-up call that writes ALL of the sheets'
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
    prompt: str,
    sql_provider: OpenAIProvider,
    extra_context: Optional[str] = None,
    current_report: Optional[dict] = None,
    emitter: Optional[EventEmitter] = None,
) -> ReportPlan:
    """
    Design (or refine) a report as a deterministic two-call pipeline.

    Args:
        prompt: The report brief (generate) or instruction (refine)
        sql_provider: OpenAI-compatible client used for BOTH the structured
            design call and the batched sheet-SQL call
        extra_context: Pre-loaded schema context from the Core API
        current_report: Existing report state when refining
        emitter: Progress event sink for SSE streaming (NullEmitter if absent)

    Raises:
        ValueError: If the design/SQL calls fail or produce no usable sheets
    """
    emitter = emitter or NullEmitter()
    user_message = prompt
    if current_report:
        user_message += (
            "\n\nCurrent report (apply the instruction above to this):\n"
            + json.dumps(current_report, indent=2)
        )
    if extra_context:
        user_message += f"\n\nAdditional context:\n{extra_context}"

    logger.info(f"Designing report for: {prompt[:100]}")

    try:
        # max_tokens=8000 matches the batched SQL call's headroom (default is
        # 4000) — a brief that legitimately needs many sheets, each with a
        # detailed data_requirements spec, can otherwise get cut off mid-JSON.
        raw = await sql_provider.generate_json(REPORT_DESIGN_SYSTEM_PROMPT, user_message, max_tokens=8000)
        try:
            design = ReportDesign(**raw)
        except ValidationError as e:
            logger.warning(f"Report design call didn't match the schema ({e}) — reformatting")
            reformatted = await _structure_report_design(sql_provider, prompt, json.dumps(raw))
            design = ReportDesign(**reformatted)
        data = design.model_dump()

        sheet_designs = data.get("sheets") or []
        if not sheet_designs:
            raise ValueError("Designer plan contains no sheets")

        # Reuse the same schema text the designer was given rather than asking
        # the model to transcribe it into its own output (same truncation risk
        # the dashboard pipeline hit).
        await emitter.emit(
            "stage",
            stage="sheet_sql_started",
            detail=f"writing SQL for {len(sheet_designs)} sheets in one batched call",
        )
        sql_by_index = await _generate_sheet_sql_batch(sql_provider, extra_context or "", sheet_designs)
        await emitter.emit("stage", stage="sheet_sql_done")

        return _build_report_plan(data, prompt, sql_by_index)
    except Exception as e:
        logger.error(f"Report designer failed: {e}", exc_info=True)
        raise ValueError(f"Report planning failed: {e}")


async def _generate_sheet_sql_batch(
    provider: OpenAIProvider, schema_context: str, sheet_designs: list
) -> list:
    """One LLM call that writes SQL + column formats for every sheet in the plan.

    Returns a list of {"sql", "column_formats", "confidence"} dicts aligned to
    sheet_designs by title first (the batch prompt asks for the same "title"
    back), falling back to position for any entry with no/duplicate title
    match — this survives the model reordering or dropping an entry instead of
    misattributing SQL to the wrong sheet.
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
            f"{len(briefs)} sheets requested — aligning by title/position."
        )

    by_title: dict[str, dict] = {}
    for e in sql_entries:
        t = (e.get("title") or "").strip()
        if t and t not in by_title:
            by_title[t] = e

    entries = []
    for i, brief in enumerate(briefs):
        e = by_title.get(brief["title"]) or (sql_entries[i] if i < len(sql_entries) else {})
        entries.append({
            "sql": (e.get("sql") or "").strip(),
            "column_formats": e.get("column_formats") if isinstance(e.get("column_formats"), dict) else {},
            "confidence": e.get("confidence", 0.7),
        })
    return entries


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
