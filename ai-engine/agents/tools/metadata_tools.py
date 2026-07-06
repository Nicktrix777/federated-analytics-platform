"""
Metadata Tools — query postgres-meta for registered dataset descriptions, column descriptions,
sample values, and join key hints.

These tools enrich the schema information with curated human descriptions so the LLM
generates better, more accurate SQL queries.
"""

import json
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def get_dataset_descriptions() -> str:
    """
    Fetch all registered dataset descriptions from the metadata database.
    Returns detailed information about each dataset including descriptions,
    source type, Trino path, and column metadata with sample values.
    Use this to understand what data is available and what each table contains.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(_async_get_datasets())
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _async_get_datasets())
            return future.result()


async def _async_get_datasets() -> str:
    """Load datasets and columns from postgres-meta."""
    import asyncpg
    from config import settings

    dsn = (
        f"postgresql://{settings.postgres_meta_user}:{settings.postgres_meta_password}"
        f"@{settings.postgres_meta_host}:{settings.postgres_meta_port}/{settings.postgres_meta_db}"
    )

    try:
        conn = await asyncpg.connect(dsn)
        try:
            dataset_rows = await conn.fetch("""
                SELECT id, name, description, source_type,
                       trino_catalog || '.' || trino_schema || '.' || trino_table AS trino_path
                FROM datasets
                WHERE is_active = true
                ORDER BY name
            """)

            datasets = []
            for row in dataset_rows:
                col_rows = await conn.fetch("""
                    SELECT column_name, data_type, description, is_joinable, sample_values
                    FROM dataset_columns
                    WHERE dataset_id = $1
                    ORDER BY id
                """, row["id"])

                datasets.append({
                    "name": row["name"],
                    "description": row["description"] or "",
                    "source_type": row["source_type"],
                    "trino_path": row["trino_path"],
                    "columns": [
                        {
                            "column_name": c["column_name"],
                            "data_type": c["data_type"],
                            "description": c["description"] or "",
                            "is_joinable": c["is_joinable"],
                            "sample_values": c["sample_values"] or "",
                        }
                        for c in col_rows
                    ],
                })

            return json.dumps(datasets, indent=2)
        finally:
            await conn.close()
    except Exception as e:
        logger.error(f"Failed to fetch dataset metadata: {e}")
        return json.dumps({"error": str(e)})


@tool
def get_table_relationships() -> str:
    """
    Get known join relationships between tables across all data sources.
    Returns a list of join hints showing how tables can be linked,
    including cross-source joins (e.g., PostgreSQL ↔ Elasticsearch).
    Use this to determine the correct join keys when writing multi-table queries.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(_async_get_relationships())
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _async_get_relationships())
            return future.result()


async def _async_get_relationships() -> str:
    """Load table relationships from postgres-meta."""
    import asyncpg
    from config import settings

    dsn = (
        f"postgresql://{settings.postgres_meta_user}:{settings.postgres_meta_password}"
        f"@{settings.postgres_meta_host}:{settings.postgres_meta_port}/{settings.postgres_meta_db}"
    )

    try:
        conn = await asyncpg.connect(dsn)
        try:
            # Check if the table_relationships table exists
            exists = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'table_relationships'
                )
            """)

            if not exists:
                return json.dumps([])

            rows = await conn.fetch("""
                SELECT from_trino_path, from_column, to_trino_path, to_column,
                       join_type, cast_expression, description
                FROM table_relationships
                ORDER BY from_trino_path
            """)
            return json.dumps([dict(r) for r in rows], indent=2)
        finally:
            await conn.close()
    except Exception as e:
        logger.warning(f"Could not load relationships: {e}")
        return json.dumps([])


@tool
def get_query_history_patterns() -> str:
    """
    Get recent successful query patterns from the audit log.
    Returns the last 10 successful AI-generated queries with their SQL.
    Use this to understand what kinds of queries have worked before and
    follow proven patterns for the current data sources.
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(_async_get_patterns())
    except RuntimeError:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _async_get_patterns())
            return future.result()


async def _async_get_patterns() -> str:
    """Load recent successful query patterns."""
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
                SELECT question, sql_executed, row_count
                FROM audit_logs
                WHERE status = 'success' AND mode = 'ai'
                ORDER BY created_at DESC
                LIMIT 10
            """)
            return json.dumps([dict(r) for r in rows], indent=2)
        finally:
            await conn.close()
    except Exception as e:
        logger.warning(f"Could not load query patterns: {e}")
        return json.dumps([])
