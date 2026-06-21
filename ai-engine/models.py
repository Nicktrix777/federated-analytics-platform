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
# Error Response
# ──────────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    error: str
    details: Optional[str] = None
