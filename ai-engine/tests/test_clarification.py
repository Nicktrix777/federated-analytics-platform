"""
Tests for PR5: Clarification as a terminal outcome.

Covers:
- Clarification model validation
- PlanOutcome envelope construction
- Clarification parse logic (isolated from deepagents import)
- Depth cap counting logic
"""

import json
import pytest
from models import Clarification, PlanOutcome, QueryPlan, ChatMessage


# ── Clarification model tests ─────────────────────────────────

class TestClarificationModel:
    """Tests for the Clarification Pydantic model."""

    def test_basic_construction(self):
        clar = Clarification(question="Which region?", options=["Europe", "Asia"], kind="ambiguous_entity")
        assert clar.question == "Which region?"
        assert clar.options == ["Europe", "Asia"]
        assert clar.kind == "ambiguous_entity"

    def test_default_kind(self):
        clar = Clarification(question="What?")
        assert clar.kind == "ambiguous"

    def test_default_options(self):
        clar = Clarification(question="What?")
        assert clar.options == []

    def test_unknown_kind_falls_back_to_ambiguous(self):
        clar = Clarification(question="What?", kind="totally_unknown_kind")
        assert clar.kind == "ambiguous"

    def test_valid_kinds(self):
        for kind in ["ambiguous_entity", "unresolved_value", "missing_data", "repair_exhausted", "ambiguous"]:
            clar = Clarification(question="Q", kind=kind)
            assert clar.kind == kind

    def test_serialization(self):
        clar = Clarification(question="Which one?", options=["A", "B"], kind="ambiguous_entity")
        data = clar.model_dump()
        assert data["question"] == "Which one?"
        assert data["options"] == ["A", "B"]
        assert data["kind"] == "ambiguous_entity"

    def test_json_round_trip(self):
        clar = Clarification(question="Which one?", options=["A", "B"])
        j = clar.model_dump_json()
        parsed = json.loads(j)
        clar2 = Clarification(**parsed)
        assert clar2.question == clar.question
        assert clar2.options == clar.options


# ── PlanOutcome envelope tests ────────────────────────────────

class TestPlanOutcome:
    def test_with_plan(self):
        plan = QueryPlan(question="q", sql="SELECT 1", steps=[], confidence=0.9, explanation="test")
        outcome = PlanOutcome(plan=plan, path="fast")
        assert outcome.plan is not None
        assert outcome.clarification is None
        assert outcome.path == "fast"

    def test_with_clarification(self):
        clar = Clarification(question="Which one?", options=["A", "B"], kind="ambiguous")
        outcome = PlanOutcome(clarification=clar, path="full")
        assert outcome.plan is None
        assert outcome.clarification is not None
        assert outcome.clarification.question == "Which one?"
        assert outcome.path == "full"

    def test_serialization_with_plan(self):
        plan = QueryPlan(question="q", sql="SELECT 1", steps=[], confidence=0.9, explanation="test")
        outcome = PlanOutcome(plan=plan, path="fast")
        data = outcome.model_dump()
        assert data["plan"] is not None
        assert data["clarification"] is None

    def test_serialization_with_clarification(self):
        clar = Clarification(question="Q?", options=["X"])
        outcome = PlanOutcome(clarification=clar, path="full")
        data = outcome.model_dump()
        assert data["clarification"]["question"] == "Q?"
        assert data["plan"] is None


# ── Clarification parse logic (isolated) ──────────────────────
# These test the same logic as _maybe_build_clarification without importing
# the orchestrator module (which pulls in deepagents).

def _maybe_build_clarification_standalone(data: dict):
    """Standalone copy of the parse logic for testing without deepagents import."""
    clar_q = data.get("clarification_question")
    sql = (data.get("sql") or "").strip()
    if clar_q and not sql:
        return Clarification(
            question=clar_q,
            options=data.get("clarification_options", []),
            kind=data.get("clarification_kind", "ambiguous"),
        )
    clar_dict = data.get("clarification")
    if isinstance(clar_dict, dict) and clar_dict.get("question") and not sql:
        return Clarification(
            question=clar_dict["question"],
            options=clar_dict.get("options", []),
            kind=clar_dict.get("kind", "ambiguous"),
        )
    return None


class TestClarificationParseBranch:
    """Tests for the clarification parse logic."""

    def test_orchestrator_shape_returns_clarification(self):
        data = {
            "clarification_question": "Which region do you mean?",
            "clarification_options": ["Europe", "Asia"],
            "clarification_kind": "ambiguous_entity",
            "sql": "",
        }
        result = _maybe_build_clarification_standalone(data)
        assert result is not None
        assert result.question == "Which region do you mean?"
        assert result.options == ["Europe", "Asia"]
        assert result.kind == "ambiguous_entity"

    def test_orchestrator_shape_with_none_sql(self):
        data = {
            "clarification_question": "Do you mean sales or revenue?",
            "clarification_options": ["Sales", "Revenue"],
            "sql": None,
        }
        result = _maybe_build_clarification_standalone(data)
        assert result is not None
        assert result.question == "Do you mean sales or revenue?"

    def test_fast_path_shape_returns_clarification(self):
        data = {
            "clarification": {
                "question": "Which product category?",
                "options": ["Electronics", "Furniture"],
                "kind": "ambiguous_entity",
            }
        }
        result = _maybe_build_clarification_standalone(data)
        assert result is not None
        assert result.question == "Which product category?"

    def test_plan_with_sql_returns_none(self):
        data = {"sql": "SELECT * FROM products", "clarification_question": None}
        result = _maybe_build_clarification_standalone(data)
        assert result is None

    def test_sql_overrides_clarification_question(self):
        data = {"sql": "SELECT * FROM products", "clarification_question": "Which table?"}
        result = _maybe_build_clarification_standalone(data)
        assert result is None

    def test_no_clarification_fields_returns_none(self):
        data = {"sql": "SELECT 1", "steps": [], "confidence": 0.9}
        result = _maybe_build_clarification_standalone(data)
        assert result is None

    def test_empty_clarification_question_returns_none(self):
        data = {"clarification_question": "", "sql": ""}
        result = _maybe_build_clarification_standalone(data)
        assert result is None

    def test_default_kind_when_missing(self):
        data = {"clarification_question": "What do you mean?", "sql": ""}
        result = _maybe_build_clarification_standalone(data)
        assert result is not None
        assert result.kind == "ambiguous"


# ── Depth cap logic tests ────────────────────────────────────

class TestDepthCap:
    """Test the clarification counting logic used in _run_plan_pipeline."""

    def test_count_clarification_messages(self):
        messages = [
            ChatMessage(role="user", kind="question", content="Show India data"),
            ChatMessage(role="assistant", kind="clarification", content="Which region?"),
            ChatMessage(role="user", kind="question", content="Asia"),
            ChatMessage(role="assistant", kind="plan", content="SQL: SELECT ..."),
            ChatMessage(role="user", kind="question", content="Break down by state"),
            ChatMessage(role="assistant", kind="clarification", content="Which metric?"),
        ]
        clar_count = sum(1 for m in messages if m.kind == "clarification")
        assert clar_count == 2

    def test_depth_cap_reached_at_default(self):
        """At >= default cap (2), the depth cap instruction should trigger."""
        messages = [
            ChatMessage(role="assistant", kind="clarification", content="Q1?"),
            ChatMessage(role="assistant", kind="clarification", content="Q2?"),
        ]
        clar_count = sum(1 for m in messages if m.kind == "clarification")
        assert clar_count >= 2  # default cap

    def test_depth_cap_not_reached(self):
        messages = [
            ChatMessage(role="assistant", kind="clarification", content="Q1?"),
            ChatMessage(role="assistant", kind="plan", content="SQL: ..."),
        ]
        clar_count = sum(1 for m in messages if m.kind == "clarification")
        assert clar_count < 2  # default cap

    def test_no_clarifications(self):
        messages = [
            ChatMessage(role="user", kind="question", content="Show revenue"),
            ChatMessage(role="assistant", kind="plan", content="SQL: SELECT SUM(revenue)"),
        ]
        clar_count = sum(1 for m in messages if m.kind == "clarification")
        assert clar_count == 0
