"""
Pydantic models for the AI Engine.

These models define the contract between:
  - Input:  Natural language question + dataset metadata from Core API
  - Output: Validated QueryPlan returned to Core API

The Core API validates this output again before sending to the Query Service.
"""

import re
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

# ──────────────────────────────────────────────────────────────
# Input Models (received from Core API)
# ──────────────────────────────────────────────────────────────


class DatasetColumn(BaseModel):
    column_name: str
    data_type: str
    description: str = ""
    is_joinable: bool = False
    sample_values: str = ""
    semantic_type: Optional[str] = None
    pattern: Optional[str] = None


class DatasetMeta(BaseModel):
    id: int
    name: str
    description: str = ""
    source_type: str
    trino_path: str  # catalog.schema.table
    columns: List[DatasetColumn] = []


class ChatMessage(BaseModel):
    """Structured transcript unit — assembled by Core API from conversation_turns.

    The AI Engine renders these to text for prompt injection; it never persists
    them. The frontend never sends or sees these — it keeps sending
    {question, mode, conversation_id}.
    """

    role: str  # "user" | "assistant"
    kind: str  # "question" | "answer" | "plan" | "clarification"
    content: str
    payload: Optional[dict] = None


class PlanRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    datasets: List[DatasetMeta] = []
    # Structured prior turns of the same conversation (Core API builds these
    # from conversation_turns). Present only for multi-turn follow-ups; the
    # AI Engine renders them to text and injects into the planner prompt so
    # references like "break that down" resolve.
    messages: List[ChatMessage] = Field(default_factory=list, max_length=24)


# ──────────────────────────────────────────────────────────────
# Output Models (returned to Core API)
# ──────────────────────────────────────────────────────────────


class QueryStep(BaseModel):
    step_id: int
    description: str
    catalog: str
    schema_name: str
    table: str


class QueryPlan(BaseModel):
    """
    The central artifact of the AI Engine.

    Architecture boundary:
      This is a DATA structure only — no methods that execute code,
      touch databases, or call any infrastructure. The Core API
      is responsible for executing the SQL contained in this plan.
    """

    question: str
    sql: str = Field(..., description="Trino-compatible SQL SELECT statement")
    steps: List[QueryStep] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, description="LLM confidence score")
    explanation: str = Field(..., description="Human-readable explanation of the plan")

    @field_validator("sql")
    @classmethod
    def validate_sql_is_select(cls, v: str) -> str:
        """First-line validation inside the AI Engine itself."""
        normalized = v.strip().upper()
        if not (normalized.startswith("SELECT") or normalized.startswith("WITH")):
            raise ValueError(
                f"SQL must be a SELECT or CTE query, got: {normalized[:50]}"
            )
        # Word-boundary match, NOT substring — a bare `in` check rejected any
        # SQL touching columns like updated_at / deleted_at / created_by.
        forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "GRANT"]
        pattern = r"\b(" + "|".join(forbidden) + r")\b"
        match = re.search(pattern, normalized)
        if match:
            raise ValueError(f"Forbidden keyword in SQL: {match.group(1)}")
        return v


# ──────────────────────────────────────────────────────────────
# Clarification (PR5) — alternative terminal outcome of /api/plan
# ──────────────────────────────────────────────────────────────

CLARIFICATION_KINDS = {
    "ambiguous_entity",
    "unresolved_value",
    "missing_data",
    "repair_exhausted",
    "ambiguous",
}


class Clarification(BaseModel):
    """The pipeline wants to ask the user a question instead of guessing.

    Clarifications are terminal — the AI Engine returns one instead of a
    QueryPlan, the Core API records it as a clarification turn, and the
    frontend renders it as a card with option buttons.
    """

    question: str
    options: List[str] = Field(default_factory=list)
    kind: str = "ambiguous"

    @field_validator("kind")
    @classmethod
    def validate_kind(cls, v: str) -> str:
        if v not in CLARIFICATION_KINDS:
            return "ambiguous"  # graceful fallback for unknown kinds
        return v


class PlanOutcome(BaseModel):
    """Envelope for /api/plan — exactly one of plan or clarification is set.

    The Core API inspects this to decide whether to execute SQL or record a
    clarification turn. The frontend never sees this directly — the Core API
    re-emits the clarification as its own terminal SSE event.
    """

    plan: Optional[QueryPlan] = None
    clarification: Optional[Clarification] = None
    path: str = "full"


# ──────────────────────────────────────────────────────────────
# Dashboard Planning (input from Core API, output back to it)
# ──────────────────────────────────────────────────────────────

ALLOWED_CHART_TYPES = {"table", "bar", "line", "pie", "area", "scatter", "number", "gauge"}


class DashboardPlanRequest(BaseModel):
    """A natural-language dashboard brief (generate) or instruction (refine)."""

    prompt: str = Field(..., min_length=3, max_length=2000)
    datasets: List[DatasetMeta] = []
    # Present only when refining: the dashboard as it exists right now
    # (name, description, widgets with title/sql/chart_type/grid_position).
    current_dashboard: Optional[dict] = None


class WidgetPlan(BaseModel):
    title: str
    sql: str = Field(..., description="Trino-compatible SQL SELECT statement")
    chart_type: str = "table"
    grid_position: dict = Field(default_factory=lambda: {"x": 0, "y": 0, "w": 6, "h": 4})
    explanation: str = ""

    @field_validator("chart_type")
    @classmethod
    def normalize_chart_type(cls, v: str) -> str:
        v = (v or "table").strip().lower()
        return v if v in ALLOWED_CHART_TYPES else "table"


class DashboardPlan(BaseModel):
    """
    A full dashboard proposal. Like QueryPlan this is a DATA structure only —
    the Core API validates every widget's SQL again and does all persistence.
    """

    name: str
    description: str = ""
    widgets: List[WidgetPlan]
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    explanation: str = ""


# ──────────────────────────────────────────────────────────────
# Report Planning (input from Core API, output back to it)
# ──────────────────────────────────────────────────────────────

# Shared column-format vocabulary — the Go Excel exporter maps these to number
# formats; anything else renders as plain text (see docs/reports spec).
ALLOWED_COLUMN_FORMATS = {"text", "integer", "number", "currency", "percent", "date", "datetime"}


class ReportPlanRequest(BaseModel):
    """A natural-language report brief (generate) or instruction (refine)."""

    prompt: str = Field(..., min_length=3, max_length=2000)
    datasets: List[DatasetMeta] = []
    # Present only when refining: the report as it exists right now
    # (name, description, sheets with title/description/sql/column_formats/position).
    current_report: Optional[dict] = None


class SheetPlan(BaseModel):
    title: str
    description: str = ""
    sql: str = Field(..., description="Trino-compatible SQL SELECT statement")
    # {column alias: format} — keys match the sheet's SELECT list verbatim.
    column_formats: dict = Field(default_factory=dict)
    position: int = 0
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)

    @field_validator("column_formats")
    @classmethod
    def normalize_column_formats(cls, v: dict) -> dict:
        # Drop entries outside the shared vocabulary instead of failing the
        # sheet — the Excel exporter falls back to value sniffing for any
        # column without a format.
        cleaned = {}
        for col, fmt in (v or {}).items():
            fmt = fmt.strip().lower() if isinstance(fmt, str) else ""
            if fmt in ALLOWED_COLUMN_FORMATS:
                cleaned[col] = fmt
        return cleaned


class ReportPlan(BaseModel):
    """
    A full report proposal. Like DashboardPlan this is a DATA structure only —
    the Core API validates every sheet's SQL again and does all persistence.
    """

    name: str
    description: str = ""
    sheets: List[SheetPlan]
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    explanation: str = ""


# ──────────────────────────────────────────────────────────────
# Widget SQL Repair (input from Core API, output back to it)
# ──────────────────────────────────────────────────────────────


class RepairWidgetRequest(BaseModel):
    """
    One widget's SQL that failed to execute against Trino, sent by the Core API
    for a focused single-pass fix.

    The Core API runs every generated widget against the Query Service before
    persisting it; a query that references a hallucinated column/table passes
    the static SELECT-only checks but fails at execution. That failing SQL and
    the exact engine error come back here so the identifier can be corrected
    against the real schema.
    """

    sql: str = Field(..., min_length=1, max_length=20000)
    error: str = Field(default="", description="The Trino/execution error the SQL produced")
    chart_type: str = "table"
    title: str = ""
    datasets: List[DatasetMeta] = []
    # "error": SQL failed to execute — correct the SQL.
    # "zero_rows": SQL ran fine but returned no rows — correct filter literals.
    mode: Literal["error", "zero_rows"] = "error"


class RepairWidgetResponse(BaseModel):
    sql: str
    changed: bool = False
    explanation: str = ""


# ──────────────────────────────────────────────────────────────
# Batched Widget/Sheet SQL Repair (PR-A2)
# ──────────────────────────────────────────────────────────────


class RepairBatchItem(BaseModel):
    """One widget/sheet's SQL that failed a Core-API probe, as part of a
    whole-plan repair batch. Same two modes as RepairWidgetRequest — a batch
    may mix "error" and "zero_rows" items freely.
    """

    title: str = ""
    chart_type: str = "table"
    sql: str = Field(..., min_length=1, max_length=20000)
    error: str = Field(default="", description="The Trino/execution error the SQL produced")
    # "error": SQL failed to execute — correct the SQL.
    # "zero_rows": SQL ran fine but returned no rows — correct filter literals.
    mode: Literal["error", "zero_rows"] = "error"


class RepairWidgetsBatchRequest(BaseModel):
    """
    All of a plan's failing widgets/sheets in ONE request, so the AI Engine
    builds ONE context bundle and makes ONE LLM call instead of Core API
    round-tripping /api/repair-widget once per failure (each of which rebuilds
    a full-catalog bundle).

    `datasets` is optional — empty/omitted self-loads the full catalog exactly
    like /api/repair-widget does today.
    """

    items: List[RepairBatchItem] = Field(default_factory=list)
    datasets: List[DatasetMeta] = []


class RepairBatchResult(BaseModel):
    """One item's repair outcome, aligned to the request's `items` by position
    (the endpoint itself resolves title/position alignment against the raw LLM
    output before building this list, so callers just zip against `items`).
    """

    title: str = ""
    sql: str
    changed: bool = False
    explanation: str = ""


class RepairWidgetsBatchResponse(BaseModel):
    results: List[RepairBatchResult] = Field(default_factory=list)
