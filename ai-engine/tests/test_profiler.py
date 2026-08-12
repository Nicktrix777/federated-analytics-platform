"""Unit tests for the column profiler's pure logic.

These test the deterministic functions only (no DB/Trino needed):
  - _detect_pattern: all 8 pattern types + edge cases
  - _suggest_semantic_type: pattern+name heuristics
  - sensitivity gating decisions (_keep, _collect helpers)
  - _count_nulls
  - _profile_content_hash stability
"""

import pytest

# Import the profiler's pure functions directly.
# sys.path manipulation not needed when run from the ai-engine directory:
#   python -m pytest tests/test_profiler.py -v
from profiler import (
    _collect,
    _count_nulls,
    _detect_pattern,
    _keep,
    _leaf_stats,
    _profile_content_hash,
    _suggest_semantic_type,
    DISTINCT_TRACK_CAP,
    MIN_OBSERVATIONS,
    REPEAT_RATIO,
)


# ── _detect_pattern ──────────────────────────────────────────────


class TestDetectPattern:
    def test_uuid(self):
        vals = [
            "550e8400-e29b-41d4-a716-446655440000",
            "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        ]
        assert _detect_pattern(vals) == "uuid"

    def test_alpha3_code(self):
        vals = ["IND", "USA", "GBR", "AUS", "CAN", "IND", "USA"]
        assert _detect_pattern(vals) == "alpha3_code"

    def test_alpha2_code(self):
        vals = ["IN", "US", "GB", "AU", "CA", "IN"]
        assert _detect_pattern(vals) == "alpha2_code"

    def test_numeric_code_with_repeats(self):
        # Needs repeats to qualify as numeric_code, not just random numbers
        vals = ["101", "202", "101", "303", "202", "101"]
        assert _detect_pattern(vals) == "numeric_code"

    def test_numeric_code_no_repeats(self):
        # All unique → not numeric_code
        vals = ["100", "200", "300", "400", "500"]
        assert _detect_pattern(vals) is None

    def test_iso_date(self):
        vals = ["2024-01-15", "2024-02-28", "2024-03-01"]
        assert _detect_pattern(vals) == "iso_date"

    def test_iso_datetime(self):
        vals = ["2024-01-15T10:30", "2024-02-28T14:00", "2024-03-01T09:15"]
        assert _detect_pattern(vals) == "iso_date"

    def test_email(self):
        vals = ["alice@example.com", "bob@test.org", "carol@company.io"]
        assert _detect_pattern(vals) == "email"

    def test_url(self):
        vals = ["https://example.com/page", "http://test.org/api", "https://foo.bar/baz"]
        assert _detect_pattern(vals) == "url"

    def test_internal_enum_uppercase(self):
        vals = ["ACTIVE", "INACTIVE", "PENDING", "ACTIVE", "PENDING", "ACTIVE"]
        assert _detect_pattern(vals) == "internal_enum"

    def test_internal_enum_lowercase(self):
        vals = ["active", "inactive", "pending", "active", "pending", "active"]
        assert _detect_pattern(vals) == "internal_enum"

    def test_mixed_case_not_enum(self):
        # Mixed case values should NOT match internal_enum
        vals = ["Active", "Inactive", "Pending", "Active"]
        assert _detect_pattern(vals) != "internal_enum"

    def test_empty_input(self):
        assert _detect_pattern([]) is None

    def test_single_value(self):
        # Needs >= 2 non-empty values
        assert _detect_pattern(["IND"]) is None

    def test_mixed_patterns_no_match(self):
        vals = ["IND", "alice@example.com", "12345", "Hello World"]
        assert _detect_pattern(vals) is None

    def test_free_text_no_match(self):
        vals = ["This is a sentence", "Another piece of text", "Some more words"]
        assert _detect_pattern(vals) is None


# ── _suggest_semantic_type ────────────────────────────────────────


class TestSuggestSemanticType:
    def test_uuid_pattern(self):
        assert _suggest_semantic_type("uuid", "some_id") == "uuid"

    def test_email_pattern(self):
        assert _suggest_semantic_type("email", "contact") == "email"

    def test_url_pattern(self):
        assert _suggest_semantic_type("url", "website") == "url"

    def test_alpha3_country(self):
        # Default for alpha3 is country
        assert _suggest_semantic_type("alpha3_code", "nationality") == "country_code_alpha3"

    def test_alpha3_currency(self):
        # Currency keyword in name overrides
        assert _suggest_semantic_type("alpha3_code", "currency_code") == "currency_code"
        assert _suggest_semantic_type("alpha3_code", "curr_type") == "currency_code"

    def test_alpha2_code(self):
        assert _suggest_semantic_type("alpha2_code", "country") == "country_code_alpha2"

    def test_internal_enum(self):
        assert _suggest_semantic_type("internal_enum", "status") == "internal_enum"

    def test_iso_date_returns_none(self):
        # Dates are structural, not a semantic type
        assert _suggest_semantic_type("iso_date", "created_at") is None

    def test_no_pattern_name_heuristic_country(self):
        assert _suggest_semantic_type(None, "driver_nationality") == "country_code"

    def test_no_pattern_name_heuristic_email(self):
        assert _suggest_semantic_type(None, "user_email") == "email"

    def test_no_pattern_name_heuristic_url(self):
        assert _suggest_semantic_type(None, "profile_url") == "url"

    def test_no_pattern_no_heuristic(self):
        assert _suggest_semantic_type(None, "some_random_column") is None


# ── _keep (cardinality filter) ────────────────────────────────────


class TestKeep:
    def test_enum_like_kept(self):
        st = {"vals": ["A", "B", "C"], "distinct": {"A", "B", "C"}, "obs": 10, "over": False}
        assert _keep(st) is True

    def test_high_cardinality_dropped(self):
        st = {"vals": ["A"], "distinct": {"A"}, "obs": 5, "over": True}
        assert _keep(st) is False

    def test_too_few_observations(self):
        st = {"vals": ["A"], "distinct": {"A"}, "obs": 1, "over": False}
        assert _keep(st) is False

    def test_no_repeats_dropped(self):
        # obs < distinct * REPEAT_RATIO → not enum-like
        st = {"vals": ["A", "B", "C"], "distinct": {"A", "B", "C"}, "obs": 3, "over": False}
        assert _keep(st) is False  # 3 < 3 * 1.5 = 4.5

    def test_sufficient_repeats(self):
        st = {"vals": ["A", "B"], "distinct": {"A", "B"}, "obs": 6, "over": False}
        assert _keep(st) is True  # 6 >= 2 * 1.5 = 3.0

    def test_empty_distinct(self):
        st = {"vals": [], "distinct": set(), "obs": 5, "over": False}
        assert _keep(st) is False


# ── _count_nulls ──────────────────────────────────────────────────


class TestCountNulls:
    def test_no_nulls(self):
        rows = [[1, "a"], [2, "b"], [3, "c"]]
        assert _count_nulls(rows, 0, 3) == 0.0

    def test_all_nulls(self):
        rows = [[None], [None], [None]]
        assert _count_nulls(rows, 0, 3) == 1.0

    def test_some_nulls(self):
        rows = [[1], [None], [3], [None]]
        assert _count_nulls(rows, 0, 4) == 0.5

    def test_missing_column(self):
        # Row shorter than col_index → counts as null
        rows = [[1, 2], [1]]
        assert _count_nulls(rows, 1, 2) == 0.5

    def test_empty_rows(self):
        assert _count_nulls([], 0, 0) == 0.0


# ── _profile_content_hash ────────────────────────────────────────


class TestProfileContentHash:
    def test_deterministic(self):
        h1 = _profile_content_hash('{"a": [1]}', "uuid", 5, 0.1)
        h2 = _profile_content_hash('{"a": [1]}', "uuid", 5, 0.1)
        assert h1 == h2

    def test_changes_on_different_input(self):
        h1 = _profile_content_hash('{"a": [1]}', "uuid", 5, 0.1)
        h2 = _profile_content_hash('{"a": [2]}', "uuid", 5, 0.1)
        assert h1 != h2

    def test_none_values(self):
        h = _profile_content_hash(None, None, None, None)
        assert isinstance(h, str) and len(h) == 64


# ── _leaf_stats (per-leaf derived signal, never a literal value) ─


class TestLeafStats:
    def test_derives_pattern_and_semantic_type_per_leaf(self):
        payload = {"details.nationality": ["IND", "GBR", "USA"]}
        out = _leaf_stats(payload, "details")
        assert out == {
            "details.nationality": {"pattern": "alpha3_code", "semantic_type": "country_code_alpha3"}
        }
        # No literal value anywhere in the output.
        assert "IND" not in str(out)

    def test_excludes_top_level_own_leaf(self):
        # The column's own flat leaf is already covered by the dedicated
        # pattern/suggested_semantic_type columns — not duplicated into stats.
        payload = {"status": ["ACTIVE", "CLOSED"]}
        assert _leaf_stats(payload, "status") is None

    def test_none_for_empty_payload(self):
        assert _leaf_stats(None, "col") is None
        assert _leaf_stats({}, "col") is None

    def test_none_when_no_leaf_has_a_pattern(self):
        payload = {"details.notes": ["some free text here", "other text"]}
        assert _leaf_stats(payload, "details") is None


# ── _collect (scalar walk) ────────────────────────────────────────


class TestCollect:
    def test_scalar_varchar(self):
        node = {"kind": "scalar", "type": "varchar"}
        acc = {}
        _collect("hello", node, "col", acc)
        assert "col" in acc
        assert acc["col"]["vals"] == ["hello"]
        assert acc["col"]["obs"] == 1

    def test_scalar_skips_timestamps(self):
        node = {"kind": "scalar", "type": "timestamp"}
        acc = {}
        _collect("2024-01-01", node, "col", acc)
        assert "col" not in acc

    def test_none_value_skipped(self):
        node = {"kind": "scalar", "type": "varchar"}
        acc = {}
        _collect(None, node, "col", acc)
        assert "col" not in acc

    def test_row_walk(self):
        node = {
            "kind": "row",
            "fields": [
                ("name", {"kind": "scalar", "type": "varchar"}),
                ("code", {"kind": "scalar", "type": "varchar"}),
            ],
        }
        acc = {}
        _collect(["Alice", "IND"], node, "person", acc)
        assert "person.name" in acc
        assert "person.code" in acc

    def test_array_walk(self):
        node = {
            "kind": "array",
            "element": {"kind": "scalar", "type": "varchar"},
        }
        acc = {}
        _collect(["a", "b", "c"], node, "tags", acc)
        assert "tags" in acc
        assert acc["tags"]["obs"] == 3

    def test_long_value_skipped(self):
        node = {"kind": "scalar", "type": "varchar"}
        acc = {}
        _collect("x" * 100, node, "col", acc)
        assert "col" not in acc
