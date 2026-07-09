"""
Schema Tools — used by subagents to discover tables and columns from all registered data sources.

These tools query:
  1. Trino's information_schema for live table/column discovery
  2. postgres-meta data_sources table for registered connections

This is the core of the dynamic schema-awareness feature:
  New data sources registered through the UI are immediately available
  to the AI agents without any code changes.
"""

import json
import logging

from langchain_core.tools import tool

from agents.tools._cache import async_ttl_cache
from agents.tools._common import (
    build_trino_path,
    meta_connection,
    run_sync,
    run_trino_query,
    split_trino_path,
)
from config import settings

logger = logging.getLogger(__name__)

# Schemas to skip (system/internal)
SKIP_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast", "_schema", "system"}
_SKIP_SCHEMAS_SQL = ", ".join(f"'{s}'" for s in sorted(SKIP_SCHEMAS))


@tool
def list_available_sources() -> str:
    """
    List all registered data sources (databases, Elasticsearch clusters, etc.)
    and their connection metadata. Returns a JSON string with source details
    including type, host, and available schemas/indices.
    """
    return run_sync(_async_list_sources())


@async_ttl_cache(settings.schema_tool_cache_ttl_seconds)
async def _async_list_sources() -> str:
    """Async implementation of list_available_sources."""
    try:
        async with meta_connection() as conn:
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
    return run_sync(_async_get_tables(trino_catalog))


@async_ttl_cache(settings.schema_tool_cache_ttl_seconds)
async def _async_get_tables(catalog: str) -> str:
    """List tables in a catalog via Trino's information_schema."""
    query = f"""
    SELECT DISTINCT table_schema, table_name
    FROM {catalog}.information_schema.tables
    WHERE table_schema NOT IN ({_SKIP_SCHEMAS_SQL})
      AND table_name NOT LIKE '\\_%'
    ORDER BY table_schema, table_name
    """
    try:
        rows = await run_trino_query(query, source="fap-ai-schema-tool")
        tables = [
            {
                "schema": str(r[0]),
                "table": str(r[1]),
                "trino_path": build_trino_path(catalog, str(r[0]), str(r[1])),
            }
            for r in rows
        ]
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
    return run_sync(_async_get_columns(trino_path))


@async_ttl_cache(settings.schema_tool_cache_ttl_seconds)
async def _async_get_columns(trino_path: str) -> str:
    """Get columns from Trino's information_schema and enrich with postgres-meta descriptions."""
    try:
        catalog, schema, table = split_trino_path(trino_path)
    except ValueError as e:
        return json.dumps({"error": str(e)})

    # Fetch live schema from Trino
    query = f"""
    SELECT column_name, data_type, ordinal_position
    FROM {catalog}.information_schema.columns
    WHERE table_schema = '{schema}' AND table_name = '{table}'
    ORDER BY ordinal_position
    """
    trino_cols = {}
    try:
        rows = await run_trino_query(query, source="fap-ai-column-tool")
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
    try:
        async with meta_connection() as conn:
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
    return run_sync(_async_schema_summary(trino_catalog))


@async_ttl_cache(settings.schema_tool_cache_ttl_seconds)
async def _async_schema_summary(catalog: str) -> str:
    """Build a compact schema summary for a full catalog."""
    query = f"""
    SELECT table_schema, table_name, column_name, data_type
    FROM {catalog}.information_schema.columns
    WHERE table_schema NOT IN ({_SKIP_SCHEMAS_SQL})
      AND table_name NOT LIKE '\\_%'
    ORDER BY table_schema, table_name, ordinal_position
    """
    try:
        rows = await run_trino_query(query, source="fap-ai-summary-tool")
    except Exception as e:
        return json.dumps({"error": str(e), "catalog": catalog})

    # Group by table
    tables: dict[str, dict] = {}
    for row in rows:
        schema, table, col, dtype = str(row[0]), str(row[1]), str(row[2]), str(row[3])
        # Group by the raw schema.table (stable dict key); expose the quoted,
        # directly-runnable path as the "trino_path" value.
        key = f"{schema}.{table}"
        if key not in tables:
            tables[key] = {"trino_path": build_trino_path(catalog, schema, table), "columns": []}
        tables[key]["columns"].append({"name": col, "type": dtype.upper()})

    return json.dumps(list(tables.values()), indent=2)
