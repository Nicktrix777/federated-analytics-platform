"""Unit tests for context_bundle's pure renderers.

These test the deterministic rendering only (no DB/Trino/LLM needed):
  - the shared column-line hint: real samples > [format: pattern] > nothing
  - the pattern hint appears in BOTH the fast-path system prompt and extra_context
  - render_extra_context returns None for an empty bundle
  - relationships render into both renderers

Run from the ai-engine directory:
  python -m pytest tests/test_context_bundle.py -v
"""

from context_bundle import (
    ContextBundle,
    _column_hint_suffix,
    _lookup_bound_columns,
    _render_value_lookups,
    render_extra_context,
    render_fast_path_system_prompt,
    render_fast_path_user_prompt,
)
from models import DatasetColumn, DatasetMeta, PlanRequest


def _col(name, dtype="VARCHAR", *, samples="", pattern=None, desc="", joinable=False, semantic_type=None):
    return DatasetColumn(
        column_name=name,
        data_type=dtype,
        description=desc,
        is_joinable=joinable,
        sample_values=samples,
        pattern=pattern,
        semantic_type=semantic_type,
    )


def _bundle(columns, relationships=None, examples=None, lookups=None):
    ds = DatasetMeta(
        id=1,
        name="drivers",
        description="F1 drivers",
        source_type="postgres",
        trino_path='postgresql.public."drivers"',
        columns=columns,
    )
    return ContextBundle(
        datasets=[ds],
        relationships=relationships or [],
        examples=examples or [],
        lookups=lookups or [],
    )


# ── _column_hint_suffix (the shared new behavior) ────────────────


class TestColumnHintSuffix:
    def test_real_samples_win_over_pattern(self):
        col = _col("nationality", samples='{"nationality": ["IND", "GBR"]}', pattern="alpha3_code")
        samples = {"nationality": ["IND", "GBR"]}
        # samples present -> render literals, NOT the pattern hint
        out = _column_hint_suffix(col, samples)
        assert out == " [e.g. IND, GBR]"
        assert "format:" not in out

    def test_pattern_hint_when_samples_absent(self):
        col = _col("nationality", samples="", pattern="alpha3_code")
        out = _column_hint_suffix(col, {})
        assert out == " [format: alpha3_code]"

    def test_nothing_when_neither(self):
        col = _col("code", samples="", pattern=None)
        assert _column_hint_suffix(col, {}) == ""


# ── pattern hint lands in BOTH renderers, once each ──────────────


class TestPatternHintInRenderers:
    def test_system_prompt_shows_format_hint(self):
        bundle = _bundle([_col("nationality", pattern="alpha3_code", desc="ISO code")])
        out = render_fast_path_system_prompt(bundle)
        assert "- nationality (varchar): ISO code [format: alpha3_code]" in out

    def test_extra_context_shows_format_hint(self):
        bundle = _bundle([_col("nationality", pattern="alpha3_code", desc="ISO code")])
        out = render_extra_context(bundle)
        assert "- nationality (varchar): ISO code [format: alpha3_code]" in out

    def test_gated_column_with_pattern_still_gets_shape_hint(self):
        # A sensitive column: profiler wrote pattern but NULLed sample_values.
        bundle = _bundle([_col("ssn", samples="", pattern="numeric_code")])
        assert "[format: numeric_code]" in render_extra_context(bundle)
        assert "[format: numeric_code]" in render_fast_path_system_prompt(bundle)

    def test_samples_suppress_pattern_hint(self):
        bundle = _bundle([_col("nationality", samples='{"nationality": ["IND"]}', pattern="alpha3_code")])
        out = render_extra_context(bundle)
        assert "[e.g. IND]" in out
        assert "format:" not in out


# ── structural guards ────────────────────────────────────────────


class TestStructure:
    def test_empty_bundle_extra_context_is_none(self):
        assert render_extra_context(ContextBundle()) is None

    def test_relationships_render_in_both(self):
        rels = [{
            "from_trino_path": 'postgresql.public."drivers"',
            "from_column": "driver_id",
            "to_trino_path": 'elasticsearch.default."races"',
            "to_column": "race_id",
            "join_type": "inner",
            "cast_expression": "CAST(driver_id AS VARCHAR)",
            "description": "drivers to races",
        }]
        bundle = _bundle([_col("driver_id", "INTEGER", joinable=True)], relationships=rels)
        sysp = render_fast_path_system_prompt(bundle)
        extra = render_extra_context(bundle)
        assert "CAST(driver_id AS VARCHAR)" in sysp
        assert "CAST(driver_id AS VARCHAR)" in extra
        assert "[JOIN KEY]" in sysp

    def test_examples_render_in_both(self):
        bundle = _bundle(
            [_col("driver_id", "INTEGER")],
            examples=[{"question": "count?", "sql": "SELECT count(*) FROM drivers"}],
        )
        assert "Proven Query Examples" in render_fast_path_system_prompt(bundle)
        # extra_context renders few-shot examples too (PR-A1) — the report/
        # dashboard designers and widget repair get the same quality signal
        # the fast path always had.
        assert "Proven Query Examples" in (render_extra_context(bundle) or "")

    def test_user_prompt_includes_conversation_block(self):
        from models import ChatMessage

        req = PlanRequest(
            question="break it down by region",
            messages=[
                ChatMessage(role="user", kind="question", content="show sales"),
                ChatMessage(
                    role="assistant",
                    kind="plan",
                    content="SQL: SELECT * FROM sales",
                    payload={"sql": "SELECT * FROM sales", "row_count": 10},
                ),
            ],
        )
        out = render_fast_path_user_prompt(req)
        assert "break it down by region" in out
        assert "Conversation so far:" in out


# ── Value lookups (PR8) ──────────────────────────────────────────


class TestRenderValueLookups:
    """Tests for _render_value_lookups — the prompt block that maps coded columns
    to reference tables with subquery patterns."""

    def test_explicit_binding_renders_subquery_pattern(self):
        lookups = [{
            "column_trino_path": 'postgresql.public."drivers"',
            "column_name": "nationality",
            "semantic_type": None,
            "lookup_trino_path": "postgresql.reference.countries",
            "key_column": "code",
            "match_columns": ["name", "demonym"],
            "description": "ISO country codes",
        }]
        out = _render_value_lookups(lookups, [])
        assert "Coded-Column Lookups" in out
        assert "nationality" in out
        assert "SELECT code FROM postgresql.reference.countries" in out
        assert "lower(name)" in out
        assert "lower(demonym)" in out
        assert "ISO country codes" in out

    def test_semantic_type_auto_expands_to_matching_columns(self):
        cols = [
            _col("nationality", semantic_type="country_code_alpha3"),
            _col("birth_country", semantic_type="country_code_alpha3"),
            _col("driver_id", "INTEGER"),  # no semantic_type — not bound
        ]
        ds = DatasetMeta(
            id=1, name="drivers", description="", source_type="postgres",
            trino_path='postgresql.public."drivers"', columns=cols,
        )
        lookups = [{
            "column_trino_path": None,
            "column_name": None,
            "semantic_type": "country_code_alpha3",
            "lookup_trino_path": "postgresql.reference.countries",
            "key_column": "alpha3_code",
            "match_columns": ["name"],
            "description": "auto-bound country lookup",
        }]
        out = _render_value_lookups(lookups, [ds])
        # Both nationality and birth_country should appear
        assert "nationality" in out
        assert "birth_country" in out
        # driver_id should NOT appear
        assert "driver_id" not in out

    def test_empty_lookups_renders_nothing(self):
        assert _render_value_lookups([], []) == ""

    def test_no_match_columns_falls_back_to_key_column(self):
        lookups = [{
            "column_trino_path": 'pg.public."t"',
            "column_name": "code",
            "semantic_type": None,
            "lookup_trino_path": "pg.ref.codes",
            "key_column": "id",
            "match_columns": [],
            "description": "",
        }]
        out = _render_value_lookups(lookups, [])
        assert "lower(id) = lower('<term>')" in out


class TestLookupBoundColumns:
    """Tests for _lookup_bound_columns — identifies which columns should suppress samples."""

    def test_explicit_binding(self):
        lookups = [{
            "column_trino_path": 'pg.public."drivers"',
            "column_name": "nationality",
        }]
        bound = _lookup_bound_columns(lookups, [])
        assert ('pg.public."drivers"', "nationality") in bound

    def test_semantic_type_binding(self):
        cols = [_col("nationality", semantic_type="country_code")]
        ds = DatasetMeta(
            id=1, name="d", description="", source_type="pg",
            trino_path="pg.public.d", columns=cols,
        )
        lookups = [{"semantic_type": "country_code"}]
        bound = _lookup_bound_columns(lookups, [ds])
        assert ("pg.public.d", "nationality") in bound

    def test_empty_lookups(self):
        assert _lookup_bound_columns([], []) == set()


class TestLookupSampleSuppression:
    """Lookup-bound columns suppress their inline sample literals in prompts."""

    def test_system_prompt_suppresses_samples_for_bound_column(self):
        lookups = [{
            "column_trino_path": 'postgresql.public."drivers"',
            "column_name": "nationality",
            "semantic_type": None,
            "lookup_trino_path": "postgresql.reference.countries",
            "key_column": "code",
            "match_columns": ["name"],
            "description": "",
        }]
        cols = [_col("nationality", samples='{"nationality": ["IND", "GBR"]}')]
        bundle = _bundle(cols, lookups=lookups)
        out = render_fast_path_system_prompt(bundle)
        # The inline samples should be suppressed
        assert "[e.g. IND, GBR]" not in out
        # Instead, the column should be marked as LOOKUP-BOUND
        assert "[LOOKUP-BOUND]" in out
        # The lookups block should be present
        assert "Coded-Column Lookups" in out

    def test_extra_context_suppresses_samples_for_bound_column(self):
        lookups = [{
            "column_trino_path": 'postgresql.public."drivers"',
            "column_name": "nationality",
            "semantic_type": None,
            "lookup_trino_path": "postgresql.reference.countries",
            "key_column": "code",
            "match_columns": ["name"],
            "description": "",
        }]
        cols = [_col("nationality", samples='{"nationality": ["IND", "GBR"]}')]
        bundle = _bundle(cols, lookups=lookups)
        out = render_extra_context(bundle)
        assert "[e.g. IND, GBR]" not in out
        assert "[LOOKUP-BOUND]" in out
        assert "Coded-Column Lookups" in out

    def test_unbound_column_still_shows_samples(self):
        lookups = [{
            "column_trino_path": 'postgresql.public."OTHER"',
            "column_name": "other_col",
            "semantic_type": None,
            "lookup_trino_path": "pg.ref.t",
            "key_column": "k",
            "match_columns": [],
            "description": "",
        }]
        cols = [_col("nationality", samples='{"nationality": ["IND", "GBR"]}')]
        bundle = _bundle(cols, lookups=lookups)
        out = render_fast_path_system_prompt(bundle)
        # nationality is NOT bound by this lookup, so samples should be visible
        assert "[e.g. IND, GBR]" in out
        assert "[LOOKUP-BOUND]" not in out


