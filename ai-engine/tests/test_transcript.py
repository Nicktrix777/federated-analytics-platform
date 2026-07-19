"""Tests for render_transcript — the pure function that converts structured
ChatMessage objects into prompt text for the planner.

Exercises: empty input, single plan turn, long SQL truncation, total cap with
omission marker, clarification/answer rendering (forward-compatible for PR5).
"""

import pytest
from models import ChatMessage
from context_bundle import render_transcript


def _msg(role: str, kind: str, content: str, payload: dict | None = None) -> ChatMessage:
    """Shorthand for building ChatMessage fixtures."""
    return ChatMessage(role=role, kind=kind, content=content, payload=payload)


class TestRenderTranscriptEmpty:
    def test_empty_list(self):
        assert render_transcript([]) == ""

    def test_none_messages(self):
        """Graceful no-op when messages is falsy."""
        assert render_transcript(None) == ""


class TestRenderTranscriptPlanTurns:
    def test_single_plan_turn(self):
        msgs = [
            _msg("user", "question", "What are the top products?"),
            _msg("assistant", "plan", "SQL: SELECT ...", {"sql": "SELECT p.name FROM products p LIMIT 10", "row_count": 10}),
        ]
        result = render_transcript(msgs)
        assert "[user] What are the top products?" in result
        assert "[assistant → SQL] SELECT p.name FROM products p LIMIT 10" in result
        assert "(returned 10 rows)" in result

    def test_two_turn_conversation(self):
        msgs = [
            _msg("user", "question", "Top products?"),
            _msg("assistant", "plan", "", {"sql": "SELECT 1", "row_count": 5}),
            _msg("user", "question", "Break down by region"),
            _msg("assistant", "plan", "", {"sql": "SELECT 2", "row_count": 12}),
        ]
        result = render_transcript(msgs)
        lines = result.strip().split("\n")
        assert len(lines) == 4
        assert lines[0].startswith("[user]")
        assert lines[1].startswith("[assistant → SQL]")
        assert lines[2].startswith("[user]")
        assert lines[3].startswith("[assistant → SQL]")

    def test_plan_without_payload_falls_back_to_content(self):
        msgs = [
            _msg("user", "question", "Test"),
            _msg("assistant", "plan", "SQL: SELECT 1 (returned 3 rows)"),
        ]
        result = render_transcript(msgs)
        assert "[assistant → SQL] SQL: SELECT 1 (returned 3 rows)" in result

    def test_zero_row_count_omitted(self):
        msgs = [
            _msg("user", "question", "Q"),
            _msg("assistant", "plan", "", {"sql": "SELECT 1", "row_count": 0}),
        ]
        result = render_transcript(msgs)
        assert "returned" not in result


class TestRenderTranscriptTruncation:
    def test_long_sql_truncated(self):
        long_sql = "SELECT " + "x, " * 1000  # well over 2000 chars
        msgs = [
            _msg("user", "question", "Q"),
            _msg("assistant", "plan", "", {"sql": long_sql, "row_count": 1}),
        ]
        result = render_transcript(msgs)
        # The rendered SQL should be capped and end with the ellipsis marker
        assert "…" in result
        # Should not contain the full SQL
        assert len(result) < len(long_sql)

    def test_total_cap_drops_oldest(self):
        # Create enough messages to exceed the cap
        msgs = []
        for i in range(20):
            msgs.append(_msg("user", "question", f"Question {i} " + "x" * 500))
            msgs.append(_msg("assistant", "plan", "", {"sql": f"SELECT {i}", "row_count": i}))

        result = render_transcript(msgs, max_chars=2000)
        assert len(result) <= 2100  # some slack for the omission marker
        assert "[..." in result
        assert "omitted...]" in result
        # Latest messages should be preserved
        assert "Question 19" in result


class TestRenderTranscriptClarification:
    """Forward-compatible tests for PR5 clarification/answer kinds."""

    def test_clarification_turn(self):
        msgs = [
            _msg("user", "question", "Show data for India"),
            _msg("assistant", "clarification", "Which region do you mean?"),
        ]
        result = render_transcript(msgs)
        assert "[user] Show data for India" in result
        assert "[assistant asked] Which region do you mean?" in result

    def test_answer_turn(self):
        msgs = [
            _msg("user", "answer", "Europe"),
        ]
        result = render_transcript(msgs)
        assert "[user answered] Europe" in result

    def test_mixed_plan_and_clarification(self):
        msgs = [
            _msg("user", "question", "Revenue by country"),
            _msg("assistant", "plan", "", {"sql": "SELECT 1", "row_count": 5}),
            _msg("user", "question", "Now for Indian drivers"),
            _msg("assistant", "clarification", "Do you mean India the country or the team?"),
            _msg("user", "answer", "The country"),
            _msg("assistant", "plan", "", {"sql": "SELECT 2", "row_count": 10}),
        ]
        result = render_transcript(msgs)
        assert "[user] Revenue by country" in result
        assert "[assistant → SQL]" in result
        assert "[assistant asked]" in result
        assert "[user answered] The country" in result


class TestRenderTranscriptDictInput:
    """Ensure render_transcript works with plain dicts (not just pydantic models)."""

    def test_dict_messages(self):
        msgs = [
            {"role": "user", "kind": "question", "content": "Hello"},
            {"role": "assistant", "kind": "plan", "content": "", "payload": {"sql": "SELECT 1", "row_count": 3}},
        ]
        result = render_transcript(msgs)
        assert "[user] Hello" in result
        assert "[assistant → SQL] SELECT 1" in result
