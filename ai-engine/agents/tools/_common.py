"""
Shared helpers for agent tools.

Every tool in schema_tools.py / metadata_tools.py needs the same three
things: a way to run its async implementation from deepagents' synchronous
tool-calling context, a postgres-meta connection, and a Trino REST query
runner. These used to be copy-pasted into every tool — they live here once.
"""

import asyncio
import concurrent.futures
import contextlib
import re

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


def run_sync(coro):
    """Run an async tool implementation from a synchronous @tool function.

    deepagents calls tools synchronously — usually from a worker thread with
    no event loop (asyncio.run works), but occasionally from a thread that
    already has a running loop. In that case the coroutine is handed to a
    fresh thread so the running loop is never touched.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


@contextlib.asynccontextmanager
async def meta_connection():
    """Async context manager yielding a connection to postgres-meta."""
    dsn = (
        f"postgresql://{settings.postgres_meta_user}:{settings.postgres_meta_password}"
        f"@{settings.postgres_meta_host}:{settings.postgres_meta_port}/{settings.postgres_meta_db}"
    )
    conn = await asyncpg.connect(dsn)
    try:
        yield conn
    finally:
        await conn.close()


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
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, content=sql)
        resp.raise_for_status()
        data = resp.json()

        max_polls = 60
        polls = 0
        while polls < max_polls:
            rows = data.get("data", [])
            if rows:
                all_rows.extend(rows)

            next_uri = data.get("nextUri")
            if not next_uri:
                break

            state = data.get("stats", {}).get("state", "")
            if state in ("FAILED", "CANCELED"):
                error = data.get("error", {}).get("message", "Unknown Trino error")
                raise RuntimeError(f"Trino query failed: {error}")

            resp = await client.get(next_uri, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            polls += 1

    return all_rows
