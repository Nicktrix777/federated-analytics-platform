"""
Pydantic models for the AI Engine.

These models define the contract between:
  - Input:  Natural language question + dataset metadata from Core API
  - Output: Validated QueryPlan returned to Core API

The Core API validates this output again before sending to the Query Service.
"""

from typing import List, Optional
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


class DatasetMeta(BaseModel):
    id: int
    name: str
    description: str = ""
    source_type: str
    trino_path: str  # catalog.schema.table
    columns: List[DatasetColumn] = []


class PlanRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=2000)
    datasets: List[DatasetMeta] = []


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
        forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "GRANT"]
        for kw in forbidden:
            if kw in normalized:
                raise ValueError(f"Forbidden keyword in SQL: {kw}")
        return v


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


class RepairWidgetResponse(BaseModel):
    sql: str
    changed: bool = False
    explanation: str = ""


# ──────────────────────────────────────────────────────────────
# Error Response
# ──────────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    error: str
    details: Optional[str] = None
