"""
Schema Tools — used by subagents to discover tables and columns from all registered data sources.

These tools query:
  1. Trino's information_schema for live table/column discovery
  2. postgres-meta data_sources table for registered connections
  3. Each source type (Elasticsearch, Postgres, MongoDB) for native mappings

This is the core of the dynamic schema-awareness feature:
  New data sources registered through the UI are immediately available
  to the AI agents without any code changes.
"""

import logging
import json
from typing import Any
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def list_available_sources() -> str:
    """
    List all registered data sources (databases, Elasticsearch clusters, etc.)
    and their connection metadata. Returns a JSON string with source details
    including type, host, and available schemas/indices.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_async_list_sources())
        return result
    except RuntimeError:
        # Already inside an event loop — use thread executor
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _async_list_sources())
            return future.result()


async def _async_list_sources() -> str:
    """Async implementation of list_available_sources."""
    import asyncpg
    from config import settings

    dsn = (
        f"postgresql://{settings.postgres_meta_user}:{settings.postgres_meta_password}"
        f"@{settings.postgres_meta_host}:{settings.postgres_meta_port}/{settings.postgres_meta_db}"
    )
    try:
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch("""
                SELECT id, name, source_type, host, port, database_name,
                       trino_catalog, is_active, last_schema_refresh
                FROM data_sources
                WHERE is_active = true
                ORDER BY source_type, name
            """)
            sources = [dict(r) for r in rows]
            # Convert datetime to string for JSON serialization
            for s in sources:
                if s.get("last_schema_refresh"):
                    s["last_schema_refresh"] = str(s["last_schema_refresh"])
            return json.dumps(sources, indent=2)
        finally:
            await conn.close()
    except Exception as e:
        logger.warning(f"Could not load data_sources from postgres-meta: {e}")
        # Return default built-in sources (backward compat)
        return json.dumps([
            {"name": "postgres_source", "source_type": "postgresql", "trino_catalog": "postgres_source"},
            {"name": "mongodb", "source_type": "mongodb", "trino_catalog": "mongodb"},
        ])


@tool
def get_tables_in_source(trino_catalog: str) -> str:
    """
    Get all tables/collections/indices in a given Trino catalog.
    Returns a JSON array of objects with schema and table names.
    
    Args:
        trino_catalog: The Trino catalog name (e.g., 'postgres_source', 'mongodb', 'elasticsearch')
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_async_get_tables(trino_catalog))
        return result
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _async_get_tables(trino_catalog))
            return future.result()


async def _async_get_tables(catalog: str) -> str:
    """List tables in a catalog via Trino's information_schema."""
    from config import settings
    import httpx

    SKIP_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast", "_schema"}

    query = f"""
    SELECT DISTINCT table_schema, table_name
    FROM {catalog}.information_schema.tables
    WHERE table_schema NOT IN ({', '.join(f"'{s}'" for s in SKIP_SCHEMAS)})
      AND table_name NOT LIKE '\\_%'
    ORDER BY table_schema, table_name
    """
    url = f"http://{settings.trino_host}:{settings.trino_port}/v1/statement"
    headers = {
        "X-Trino-User": "ai-engine",
        "X-Trino-Source": "fap-ai-schema-tool",
        "Content-Type": "application/json",
    }

    try:
        rows = await _run_trino_query(query, url, headers)
        tables = [{"schema": str(r[0]), "table": str(r[1]), "trino_path": f"{catalog}.{r[0]}.{r[1]}"} for r in rows]
        return json.dumps(tables, indent=2)
    except Exception as e:
        logger.warning(f"Could not list tables in {catalog}: {e}")
        return json.dumps({"error": str(e), "catalog": catalog})


@tool
def get_column_details(trino_path: str) -> str:
    """
    Get detailed column information for a specific table.
    Returns column names, data types, and any available descriptions.
    
    Args:
        trino_path: Fully qualified path like 'catalog.schema.table'
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_async_get_columns(trino_path))
        return result
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _async_get_columns(trino_path))
            return future.result()


async def _async_get_columns(trino_path: str) -> str:
    """Get columns from Trino's information_schema and enrich with postgres-meta descriptions."""
    from config import settings
    import asyncpg
    import httpx

    parts = trino_path.split(".")
    if len(parts) != 3:
        return json.dumps({"error": f"Invalid trino_path: {trino_path}. Expected catalog.schema.table"})

    catalog, schema, table = parts

    # Fetch live schema from Trino
    query = f"""
    SELECT column_name, data_type, ordinal_position
    FROM {catalog}.information_schema.columns
    WHERE table_schema = '{schema}' AND table_name = '{table}'
    ORDER BY ordinal_position
    """
    url = f"http://{settings.trino_host}:{settings.trino_port}/v1/statement"
    headers = {
        "X-Trino-User": "ai-engine",
        "X-Trino-Source": "fap-ai-column-tool",
        "Content-Type": "application/json",
    }

    trino_cols = {}
    try:
        rows = await _run_trino_query(query, url, headers)
        for row in rows:
            col_name = str(row[0])
            trino_cols[col_name] = {
                "column_name": col_name,
                "data_type": str(row[1]).upper(),
                "description": "",
                "is_joinable": col_name.endswith("_id") or col_name == "id",
                "sample_values": "",
            }
    except Exception as e:
        logger.warning(f"Trino column fetch failed for {trino_path}: {e}")

    # Enrich with postgres-meta descriptions
    dsn = (
        f"postgresql://{settings.postgres_meta_user}:{settings.postgres_meta_password}"
        f"@{settings.postgres_meta_host}:{settings.postgres_meta_port}/{settings.postgres_meta_db}"
    )
    try:
        conn = await asyncpg.connect(dsn)
        try:
            meta_cols = await conn.fetch("""
                SELECT dc.column_name, dc.description, dc.is_joinable, dc.sample_values
                FROM dataset_columns dc
                JOIN datasets d ON d.id = dc.dataset_id
                WHERE d.trino_table = $1 AND d.is_active = true
            """, table)
            for mc in meta_cols:
                col_name = mc["column_name"]
                if col_name in trino_cols:
                    trino_cols[col_name]["description"] = mc["description"] or ""
                    trino_cols[col_name]["is_joinable"] = mc["is_joinable"]
                    trino_cols[col_name]["sample_values"] = mc["sample_values"] or ""
        finally:
            await conn.close()
    except Exception as e:
        logger.warning(f"Could not enrich column metadata from postgres-meta: {e}")

    return json.dumps(list(trino_cols.values()), indent=2)


@tool
def get_source_schema_summary(trino_catalog: str) -> str:
    """
    Get a complete schema summary for a data source — all tables and their columns.
    Use this to understand what data is available in a source before planning a query.
    
    Args:
        trino_catalog: The Trino catalog name (e.g., 'postgres_source', 'elasticsearch')
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        result = loop.run_until_complete(_async_schema_summary(trino_catalog))
        return result
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _async_schema_summary(trino_catalog))
            return future.result()


async def _async_schema_summary(catalog: str) -> str:
    """Build a compact schema summary for a full catalog."""
    from config import settings

    SKIP_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast", "_schema"}

    query = f"""
    SELECT table_schema, table_name, column_name, data_type
    FROM {catalog}.information_schema.columns
    WHERE table_schema NOT IN ({', '.join(f"'{s}'" for s in SKIP_SCHEMAS)})
      AND table_name NOT LIKE '\\_%'
    ORDER BY table_schema, table_name, ordinal_position
    """
    url = f"http://{settings.trino_host}:{settings.trino_port}/v1/statement"
    headers = {
        "X-Trino-User": "ai-engine",
        "X-Trino-Source": "fap-ai-summary-tool",
        "Content-Type": "application/json",
    }

    try:
        rows = await _run_trino_query(query, url, headers)
    except Exception as e:
        return json.dumps({"error": str(e), "catalog": catalog})

    # Group by table
    tables: dict[str, dict] = {}
    for row in rows:
        schema, table, col, dtype = str(row[0]), str(row[1]), str(row[2]), str(row[3])
        key = f"{catalog}.{schema}.{table}"
        if key not in tables:
            tables[key] = {"trino_path": key, "columns": []}
        tables[key]["columns"].append({"name": col, "type": dtype.upper()})

    return json.dumps(list(tables.values()), indent=2)


async def _run_trino_query(sql: str, url: str, headers: dict) -> list:
    """Execute a Trino REST API query and collect all rows."""
    import httpx

    all_rows = []
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
