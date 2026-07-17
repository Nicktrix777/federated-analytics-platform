"""
Metadata Tools — query postgres-meta for registered dataset descriptions, column descriptions,
sample values, and join key hints.

These tools enrich the schema information with curated human descriptions so the LLM
generates better, more accurate SQL queries.

All tools are async — see schema_tools.py for the invocation model.
"""

import json
import logging

from langchain_core.tools import tool

from agents.tools._cache import async_ttl_cache
from agents.tools._common import build_trino_path, meta_connection
from config import settings

logger = logging.getLogger(__name__)


@tool
async def get_dataset_descriptions() -> str:
    """
    Fetch all registered dataset descriptions from the metadata database.
    Returns detailed information about each dataset including descriptions,
    source type, Trino path, and column metadata with sample values.
    Use this to understand what data is available and what each table contains.
    """
    return await _async_get_datasets()


@async_ttl_cache(settings.schema_tool_cache_ttl_seconds)
async def _async_get_datasets() -> str:
    """Load datasets and columns from postgres-meta (single joined query, no N+1)."""
    try:
        async with meta_connection() as conn:
            rows = await conn.fetch("""
                SELECT d.id, d.name, d.description, d.source_type,
                       d.trino_catalog, d.trino_schema, d.trino_table,
                       dc.column_name, dc.data_type,
                       dc.description AS column_description,
                       dc.is_joinable, dc.sample_values
                FROM datasets d
                LEFT JOIN dataset_columns dc ON dc.dataset_id = d.id
                WHERE d.is_active = true
                ORDER BY d.name, dc.id
            """)

        datasets: dict[int, dict] = {}
        for row in rows:
            ds = datasets.get(row["id"])
            if ds is None:
                ds = datasets[row["id"]] = {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row["description"] or "",
                    "source_type": row["source_type"],
                    "trino_path": build_trino_path(
                        row["trino_catalog"], row["trino_schema"], row["trino_table"]
                    ),
                    "columns": [],
                }
            if row["column_name"] is not None:
                ds["columns"].append({
                    "column_name": row["column_name"],
                    "data_type": row["data_type"],
                    "description": row["column_description"] or "",
                    "is_joinable": row["is_joinable"],
                    "sample_values": row["sample_values"] or "",
                })

        return json.dumps(list(datasets.values()), indent=2)
    except Exception as e:
        logger.error(f"Failed to fetch dataset metadata: {e}")
        return json.dumps({"error": str(e)})


@tool
async def get_table_relationships() -> str:
    """
    Get known join relationships between tables across all data sources.
    Returns a list of join hints showing how tables can be linked,
    including cross-source joins (e.g., PostgreSQL ↔ Elasticsearch).
    Use this to determine the correct join keys when writing multi-table queries.
    """
    return await _async_get_relationships()


@async_ttl_cache(settings.schema_tool_cache_ttl_seconds)
async def _async_get_relationships() -> str:
    """Load table relationships from postgres-meta."""
    try:
        async with meta_connection() as conn:
            rows = await conn.fetch("""
                SELECT from_trino_path, from_column, to_trino_path, to_column,
                       join_type, cast_expression, description
                FROM table_relationships
                ORDER BY from_trino_path
            """)
        return json.dumps([dict(r) for r in rows], indent=2)
    except Exception as e:
        logger.warning(f"Could not load relationships: {e}")
        return json.dumps([])


@tool
async def get_query_history_patterns() -> str:
    """
    Get recent successful query patterns from the audit log.
    Returns the last 10 successful AI-generated queries with their SQL.
    Use this to understand what kinds of queries have worked before and
    follow proven patterns for the current data sources.
    """
    return await _async_get_patterns()


@async_ttl_cache(settings.schema_tool_cache_ttl_seconds)
async def _async_get_patterns() -> str:
    """Load recent successful query patterns."""
    try:
        async with meta_connection() as conn:
            rows = await conn.fetch("""
                SELECT question, sql_executed, row_count
                FROM audit_logs
                WHERE status = 'success' AND mode = 'ai'
                ORDER BY created_at DESC
                LIMIT 10
            """)
        return json.dumps([dict(r) for r in rows], indent=2)
    except Exception as e:
        logger.warning(f"Could not load query patterns: {e}")
        return json.dumps([])
