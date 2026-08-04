"""
Tests for agents/tools/_common.py's run_trino_query.

Covers the PR-A2 prerequisite bug fix: the poll loop used to hit
`if not next_uri: break` BEFORE checking `stats.state in ("FAILED", "CANCELED")`,
so a terminal error response (which carries no nextUri) returned `[]` instead
of raising — any Trino-backed validation would then treat invalid SQL as
"passed". The fix reorders the checks so a FAILED/CANCELED state always raises,
regardless of whether nextUri is present.

Mocks httpx by monkeypatching the module's `_get_trino_client()` (per the
module's own doc comment: the client is a pooled singleton created lazily on
the running event loop) with an httpx.AsyncClient wired to an httpx.MockTransport
— no real network/Trino needed.

Run from the ai-engine directory:
  python -m pytest tests/test_common.py -v
"""

import httpx
import pytest

from agents.tools import _common


def _client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
class TestRunTrinoQueryTerminalStates:
    async def test_failed_with_no_next_uri_raises(self, monkeypatch):
        """The exact regression scenario: a terminal FAILED response with no
        nextUri must raise, not silently return []."""

        def handler(request):
            return httpx.Response(
                200,
                json={
                    "stats": {"state": "FAILED"},
                    "error": {"message": "Column 'stat' cannot be resolved"},
                    # Deliberately no "nextUri" — this is what let the bug
                    # slip past the old ordering (break fired first).
                },
            )

        client = _client_for(handler)
        monkeypatch.setattr(_common, "_get_trino_client", lambda: client)

        with pytest.raises(RuntimeError, match="Column 'stat' cannot be resolved"):
            await _common.run_trino_query("SELECT stat FROM t")

        await client.aclose()

    async def test_canceled_with_no_next_uri_raises(self, monkeypatch):
        def handler(request):
            return httpx.Response(
                200,
                json={
                    "stats": {"state": "CANCELED"},
                    "error": {"message": "Query was canceled"},
                },
            )

        client = _client_for(handler)
        monkeypatch.setattr(_common, "_get_trino_client", lambda: client)

        with pytest.raises(RuntimeError, match="Query was canceled"):
            await _common.run_trino_query("SELECT 1")

        await client.aclose()

    async def test_failed_mid_poll_raises_even_with_next_uri_absent_on_final_page(self, monkeypatch):
        """A multi-page response where the FIRST page has data + a nextUri,
        and the SECOND (final) page is a terminal FAILED with no nextUri —
        guards against a regression that only checks the first page."""

        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    200,
                    json={
                        "stats": {"state": "RUNNING"},
                        "data": [[1]],
                        "nextUri": "http://trino.local/v1/statement/next",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "stats": {"state": "FAILED"},
                    "error": {"message": "Trino ran out of memory"},
                },
            )

        client = _client_for(handler)
        monkeypatch.setattr(_common, "_get_trino_client", lambda: client)

        with pytest.raises(RuntimeError, match="Trino ran out of memory"):
            await _common.run_trino_query("SELECT 1")

        await client.aclose()

    async def test_missing_error_message_falls_back_to_default_text(self, monkeypatch):
        def handler(request):
            return httpx.Response(200, json={"stats": {"state": "FAILED"}})

        client = _client_for(handler)
        monkeypatch.setattr(_common, "_get_trino_client", lambda: client)

        with pytest.raises(RuntimeError, match="Unknown Trino error"):
            await _common.run_trino_query("SELECT 1")

        await client.aclose()


@pytest.mark.asyncio
class TestRunTrinoQuerySuccess:
    """Regression guard: reordering the FAILED/CANCELED check ahead of the
    nextUri break must not break the normal successful-pagination path."""

    async def test_single_page_success_returns_rows(self, monkeypatch):
        def handler(request):
            return httpx.Response(
                200,
                json={"stats": {"state": "FINISHED"}, "data": [[1, "a"], [2, "b"]]},
            )

        client = _client_for(handler)
        monkeypatch.setattr(_common, "_get_trino_client", lambda: client)

        rows = await _common.run_trino_query("SELECT * FROM t")
        assert rows == [[1, "a"], [2, "b"]]

        await client.aclose()

    async def test_multi_page_success_accumulates_all_rows(self, monkeypatch):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    200,
                    json={
                        "stats": {"state": "RUNNING"},
                        "data": [[1]],
                        "nextUri": "http://trino.local/v1/statement/next",
                    },
                )
            return httpx.Response(
                200,
                json={"stats": {"state": "FINISHED"}, "data": [[2]]},
            )

        client = _client_for(handler)
        monkeypatch.setattr(_common, "_get_trino_client", lambda: client)

        rows = await _common.run_trino_query("SELECT 1")
        assert rows == [[1], [2]]

        await client.aclose()
