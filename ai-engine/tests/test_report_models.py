"""
Tests for the Reports feature (AI Engine side).

Covers:
- SheetPlan column_formats validator (vocabulary enforcement)
- SheetPlan / ReportPlan / ReportPlanRequest model validation
- validate_and_fix_sql's default_limit parameter (reports raise the cap)
- Batched sheet-SQL pad/truncate alignment logic (isolated from deepagents import)

Run from the ai-engine directory:
  python -m pytest tests/test_report_models.py -v
"""

import json
import pytest
from pydantic import ValidationError

from models import ALLOWED_COLUMN_FORMATS, ReportPlan, ReportPlanRequest, SheetPlan
from agents.tools.validation_tools import validate_and_fix_sql


# ── SheetPlan model tests ─────────────────────────────────────

class TestSheetPlanModel:
    def test_basic_construction(self):
        sheet = SheetPlan(
            title="Summary",
            description="Headline KPIs",
            sql='SELECT sum(amount) AS "Total Revenue" FROM t',
            column_formats={"Total Revenue": "currency"},
            position=0,
            confidence=0.85,
        )
        assert sheet.title == "Summary"
        assert sheet.column_formats == {"Total Revenue": "currency"}
        assert sheet.position == 0
        assert sheet.confidence == 0.85

    def test_defaults(self):
        sheet = SheetPlan(title="Detail", sql="SELECT 1")
        assert sheet.description == ""
        assert sheet.column_formats == {}
        assert sheet.position == 0
        assert sheet.confidence == 0.7

    def test_unknown_format_dropped(self):
        sheet = SheetPlan(
            title="S",
            sql="SELECT 1",
            column_formats={"Month": "date", "Total": "money", "Share": "percent"},
        )
        assert sheet.column_formats == {"Month": "date", "Share": "percent"}

    def test_format_normalized_to_lowercase(self):
        sheet = SheetPlan(title="S", sql="SELECT 1", column_formats={"Total": " Currency "})
        assert sheet.column_formats == {"Total": "currency"}

    def test_non_string_format_dropped(self):
        sheet = SheetPlan(title="S", sql="SELECT 1", column_formats={"Total": 42, "Count": None})
        assert sheet.column_formats == {}

    def test_all_vocabulary_formats_accepted(self):
        formats = {f"col_{fmt}": fmt for fmt in ALLOWED_COLUMN_FORMATS}
        sheet = SheetPlan(title="S", sql="SELECT 1", column_formats=formats)
        assert sheet.column_formats == formats

    def test_confidence_out_of_bounds_rejected(self):
        with pytest.raises(ValidationError):
            SheetPlan(title="S", sql="SELECT 1", confidence=1.5)
        with pytest.raises(ValidationError):
            SheetPlan(title="S", sql="SELECT 1", confidence=-0.1)


# ── ReportPlan model tests ────────────────────────────────────

class TestReportPlanModel:
    def test_basic_construction(self):
        plan = ReportPlan(
            name="Contract Revenue Report",
            description="Monthly revenue by agency",
            sheets=[SheetPlan(title="Summary", sql="SELECT 1", position=0)],
            confidence=0.8,
            explanation="One summary sheet",
        )
        assert plan.name == "Contract Revenue Report"
        assert len(plan.sheets) == 1

    def test_serialization_matches_contract(self):
        plan = ReportPlan(
            name="R",
            sheets=[
                SheetPlan(
                    title="Summary",
                    sql="SELECT 1",
                    column_formats={"Month": "date"},
                    position=0,
                    confidence=0.85,
                )
            ],
        )
        data = json.loads(plan.model_dump_json())
        assert set(data.keys()) == {"name", "description", "sheets", "confidence", "explanation"}
        assert set(data["sheets"][0].keys()) == {
            "title", "description", "sql", "column_formats", "position", "confidence",
        }

    def test_confidence_out_of_bounds_rejected(self):
        with pytest.raises(ValidationError):
            ReportPlan(name="R", sheets=[], confidence=2.0)


# ── ReportPlanRequest model tests ─────────────────────────────

class TestReportPlanRequest:
    def test_generate_shape(self):
        req = ReportPlanRequest(prompt="monthly contract revenue report by agency")
        assert req.current_report is None
        assert req.datasets == []

    def test_refine_shape(self):
        current = {
            "name": "R",
            "description": "",
            "sheets": [
                {"title": "Summary", "description": "", "sql": "SELECT 1",
                 "column_formats": {"Total Revenue": "currency"}, "position": 0}
            ],
        }
        req = ReportPlanRequest(prompt="add a per-agency detail sheet", current_report=current)
        assert req.current_report["sheets"][0]["title"] == "Summary"

    def test_prompt_too_short_rejected(self):
        with pytest.raises(ValidationError):
            ReportPlanRequest(prompt="hi")


# ── validate_and_fix_sql default_limit tests ─────────────────

class TestValidateDefaultLimit:
    def test_default_stays_1000(self):
        result = validate_and_fix_sql("SELECT * FROM t")
        assert result["is_valid"]
        assert result["fixed_sql"].endswith("LIMIT 1000")

    def test_report_limit_10000(self):
        result = validate_and_fix_sql("SELECT * FROM t", default_limit=10000)
        assert result["is_valid"]
        assert result["fixed_sql"].endswith("LIMIT 10000")

    def test_existing_limit_untouched(self):
        result = validate_and_fix_sql("SELECT * FROM t LIMIT 5", default_limit=10000)
        assert result["fixed_sql"].endswith("LIMIT 5")

    def test_aggregation_gets_no_limit(self):
        result = validate_and_fix_sql(
            "SELECT agency, SUM(amount) FROM t GROUP BY agency", default_limit=10000
        )
        assert "LIMIT" not in result["fixed_sql"].upper()

    def test_forbidden_sql_still_rejected(self):
        result = validate_and_fix_sql("DROP TABLE t", default_limit=10000)
        assert not result["is_valid"]


# ── Batched sheet-SQL alignment (isolated) ────────────────────
# Same logic as report_planner._generate_sheet_sql_batch's pad/truncate step,
# copied standalone to avoid importing the module (which pulls in deepagents).

def _align_sheet_entries_standalone(sql_entries: list, brief_count: int) -> list:
    """Standalone copy of the pad/truncate alignment for testing."""
    entries = [
        {
            "sql": (e.get("sql") or "").strip(),
            "column_formats": e.get("column_formats") if isinstance(e.get("column_formats"), dict) else {},
            "confidence": e.get("confidence", 0.7),
        }
        for e in sql_entries
    ]
    entries += [{"sql": "", "column_formats": {}, "confidence": 0.0}] * (brief_count - len(entries))
    return entries[:brief_count]


class TestSheetSQLAlignment:
    def test_exact_count_passes_through(self):
        entries = _align_sheet_entries_standalone(
            [{"sql": "SELECT 1", "column_formats": {"A": "integer"}, "confidence": 0.9}], 1
        )
        assert entries == [{"sql": "SELECT 1", "column_formats": {"A": "integer"}, "confidence": 0.9}]

    def test_missing_entries_padded_empty(self):
        entries = _align_sheet_entries_standalone([{"sql": "SELECT 1"}], 3)
        assert len(entries) == 3
        assert entries[1]["sql"] == ""
        assert entries[2] == {"sql": "", "column_formats": {}, "confidence": 0.0}

    def test_extra_entries_truncated(self):
        entries = _align_sheet_entries_standalone(
            [{"sql": "SELECT 1"}, {"sql": "SELECT 2"}, {"sql": "SELECT 3"}], 2
        )
        assert len(entries) == 2
        assert entries[1]["sql"] == "SELECT 2"

    def test_non_dict_column_formats_dropped(self):
        entries = _align_sheet_entries_standalone([{"sql": "SELECT 1", "column_formats": "currency"}], 1)
        assert entries[0]["column_formats"] == {}
