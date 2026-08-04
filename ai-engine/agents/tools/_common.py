"""
Shared helpers for agent tools.

Every tool in schema_tools.py / metadata_tools.py needs the same things:
a postgres-meta connection and a Trino REST query runner. Both are pooled
module-wide — the agent pipeline is fully async now (agents are invoked with
ainvoke/astream_events, and tools are async), so everything runs on the one
uvicorn event loop and can share a connection pool. The old per-call
run_sync/ThreadPoolExecutor/fresh-asyncpg-connection dance is gone.
"""

import contextlib
import re
from typing import Optional

import asyncpg
import httpx

from config import settings

_SIMPLE_IDENT_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def quote_trino_ident(ident: str) -> str:
    """Double-quote an identifier segment if it needs it.

    Trino identifiers containing anything other than letters, digits, or
    underscores (hyphens, dots, spaces — common in Elasticsearch index names
    like "contracts-v2.37") must be quoted or the parser misreads them (e.g.
    a bare hyphen parses as subtraction).
    """
    if _SIMPLE_IDENT_RE.match(ident):
        return ident
    return '"' + ident.replace('"', '""') + '"'


def build_trino_path(catalog: str, schema: str, table: str) -> str:
    """Join catalog/schema/table into a directly Trino-executable path."""
    return f"{quote_trino_ident(catalog)}.{quote_trino_ident(schema)}.{quote_trino_ident(table)}"


def split_trino_path(trino_path: str) -> tuple:
    """Split a catalog.schema.table path into 3 parts.

    Tolerates a table segment that itself contains dots (e.g. an ES index
    named "contracts-v2.37") by only splitting on the first two dots, and
    strips surrounding double-quotes from the table segment if present.
    """
    parts = trino_path.split(".", 2)
    if len(parts) != 3:
        raise ValueError(f"Invalid trino_path: {trino_path!r}. Expected catalog.schema.table")
    catalog, schema, table = parts
    table = table.strip()
    if table.startswith('"') and table.endswith('"'):
        table = table[1:-1].replace('""', '"')
    return catalog, schema, table


# ── Pooled clients (created lazily on the running event loop) ──

_meta_pool: Optional[asyncpg.Pool] = None
_trino_client: Optional[httpx.AsyncClient] = None


async def _register_vector(conn) -> None:
    """Register the pgvector codec on a new pool connection.

    Lets asyncpg encode/decode `vector` columns as Python lists (used by the
    schema-RAG embeddings table). No-op if the pgvector extension isn't
    installed yet — the DB may predate migration 005, in which case the
    schema-RAG features stay dormant and the rest of the tools work unchanged.
    """
    try:
        from pgvector.asyncpg import register_vector
        await register_vector(conn)
    except Exception:
        # Extension not present (unmigrated DB) or pgvector not installed —
        # leave the connection usable for every non-vector query.
        pass


async def _get_meta_pool() -> asyncpg.Pool:
    global _meta_pool
    if _meta_pool is None:
        dsn = (
            f"postgresql://{settings.postgres_meta_user}:{settings.postgres_meta_password}"
            f"@{settings.postgres_meta_host}:{settings.postgres_meta_port}/{settings.postgres_meta_db}"
        )
        _meta_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, init=_register_vector)
    return _meta_pool


def _get_trino_client() -> httpx.AsyncClient:
    global _trino_client
    if _trino_client is None:
        _trino_client = httpx.AsyncClient(timeout=30.0)
    return _trino_client


async def close_shared_clients() -> None:
    """Release the pooled postgres-meta and Trino clients (app shutdown)."""
    global _meta_pool, _trino_client
    if _meta_pool is not None:
        await _meta_pool.close()
        _meta_pool = None
    if _trino_client is not None:
        await _trino_client.aclose()
        _trino_client = None


@contextlib.asynccontextmanager
async def meta_connection():
    """Async context manager yielding a pooled connection to postgres-meta."""
    pool = await _get_meta_pool()
    async with pool.acquire() as conn:
        yield conn


async def run_trino_query(sql: str, source: str = "fap-ai-tools") -> list:
    """Execute a query via Trino's REST API and collect all rows.

    Handles Trino's async pagination protocol (nextUri polling).
    """
    url = f"http://{settings.trino_host}:{settings.trino_port}/v1/statement"
    headers = {
        "X-Trino-User": "ai-engine",
        "X-Trino-Source": source,
        "Content-Type": "application/json",
    }

    all_rows: list = []
    client = _get_trino_client()
    resp = await client.post(url, headers=headers, content=sql)
    resp.raise_for_status()
    data = resp.json()

    max_polls = 60
    polls = 0
    while polls < max_polls:
        rows = data.get("data", [])
        if rows:
            all_rows.extend(rows)

        state = data.get("stats", {}).get("state", "")
        if state in ("FAILED", "CANCELED"):
            error = data.get("error", {}).get("message", "Unknown Trino error")
            raise RuntimeError(f"Trino query failed: {error}")

        next_uri = data.get("nextUri")
        if not next_uri:
            break

        resp = await client.get(next_uri, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        polls += 1

    return all_rows
