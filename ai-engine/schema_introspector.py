"""
Schema Introspector for the AI Engine.

Automatically discovers table schemas directly from Trino's INFORMATION_SCHEMA,
eliminating the need to manually register every table in postgres-meta.

Strategy:
  1. Query Trino's system.jdbc.columns (or information_schema) for all catalogs
  2. Build DatasetMeta objects from the discovered schema
  3. Merge with manually registered metadata from postgres-meta:
     - If postgres-meta has a description for a column → use it (richer context)
     - If not → use the auto-discovered type with a generated description
  4. Cache results to avoid hammering Trino on every LLM request

This makes the platform self-configuring: add a new table to postgres-source
or a new collection to MongoDB and it automatically becomes queryable.
"""

import logging
import time
from typing import List, Optional, Dict

import httpx

from config import settings
from models import DatasetMeta, DatasetColumn

logger = logging.getLogger(__name__)

# Known catalogs to introspect (exclude system/internal catalogs)
INTROSPECT_CATALOGS = ["postgres_source", "mongodb"]

# Schemas to skip (system/internal)
SKIP_SCHEMAS = {
    "information_schema",
    "pg_catalog",
    "pg_toast",
    "pg_temp_1",
    "_schema",  # MongoDB _schema collection used by Trino itself
}

# Cache
_introspected_cache: Optional[List[DatasetMeta]] = None
_introspected_cache_ttl: float = 0.0
INTROSPECT_CACHE_SECONDS = 120.0  # 2 min cache — refresh often enough to pick up new uploads


async def get_introspected_datasets() -> List[DatasetMeta]:
    """
    Fetch live schema from Trino's information_schema and return as DatasetMeta list.
    Results are cached for INTROSPECT_CACHE_SECONDS seconds.
    """
    global _introspected_cache, _introspected_cache_ttl

    now = time.monotonic()
    if _introspected_cache is not None and now < _introspected_cache_ttl:
        logger.debug("Returning cached introspected schema")
        return _introspected_cache

    logger.info("Introspecting live schema from Trino...")

    datasets: List[DatasetMeta] = []
    trino_url = f"http://{settings.trino_host}:{settings.trino_port}/v1/statement"
    headers = {
        "X-Trino-User": "ai-engine",
        "X-Trino-Source": "fap-ai-engine",
        "Content-Type": "application/json",
    }

    for catalog in INTROSPECT_CATALOGS:
        try:
            catalog_datasets = await _introspect_catalog(catalog, trino_url, headers)
            datasets.extend(catalog_datasets)
        except Exception as e:
            logger.warning(f"Failed to introspect catalog '{catalog}': {e}")
            continue

    _introspected_cache = datasets
    _introspected_cache_ttl = now + INTROSPECT_CACHE_SECONDS
    logger.info(f"Introspected {len(datasets)} datasets from Trino")
    return datasets


async def _introspect_catalog(
    catalog: str, trino_url: str, headers: dict
) -> List[DatasetMeta]:
    """Introspect all tables in a Trino catalog via information_schema."""
    query = f"""
    SELECT
        table_catalog,
        table_schema,
        table_name,
        column_name,
        data_type,
        ordinal_position
    FROM {catalog}.information_schema.columns
    WHERE table_schema NOT IN ({", ".join(f"'{s}'" for s in SKIP_SCHEMAS)})
    ORDER BY table_schema, table_name, ordinal_position
    """

    rows = await _run_trino_query(query, trino_url, headers)
    if not rows:
        return []

    # Group by (catalog, schema, table)
    tables: Dict[str, List[dict]] = {}
    for row in rows:
        # row: [table_catalog, table_schema, table_name, column_name, data_type, ordinal_position]
        if len(row) < 5:
            continue
        tcat, tschema, tname, col_name, col_type = (
            str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4])
        )

        # Skip internal Trino metadata collections
        if tname.startswith("_"):
            continue

        key = f"{tcat}.{tschema}.{tname}"
        if key not in tables:
            tables[key] = []
        tables[key].append({
            "column_name": col_name,
            "data_type": col_type,
            "catalog": tcat,
            "schema": tschema,
            "table": tname,
        })

    datasets = []
    for trino_path, columns in tables.items():
        if not columns:
            continue
        first = columns[0]
        source_type = "postgresql" if catalog == "postgres_source" else "mongodb"

        # Build dataset with auto-generated descriptions
        dataset_columns = []
        for col in columns:
            # Heuristic descriptions for common column patterns
            desc = _infer_description(col["column_name"], col["data_type"])
            is_joinable = col["column_name"].endswith("_id") or col["column_name"] == "id"

            dataset_columns.append(DatasetColumn(
                column_name=col["column_name"],
                data_type=col["data_type"].upper(),
                description=desc,
                is_joinable=is_joinable,
                sample_values="",
            ))

        table_name = first["table"]
        schema_name = first["schema"]
        datasets.append(DatasetMeta(
            id=-1,  # Sentinel: introspected (not from postgres-meta)
            name=table_name,
            description=f"Auto-discovered {source_type} table: {trino_path}",
            source_type=source_type,
            trino_path=trino_path,
            columns=dataset_columns,
        ))

    return datasets


async def _run_trino_query(sql: str, url: str, headers: dict) -> List[List]:
    """
    Execute a query against Trino's REST API and collect all rows.
    Handles Trino's async pagination protocol.
    """
    all_rows: List[List] = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Submit query
        resp = await client.post(url, headers=headers, content=sql)
        resp.raise_for_status()
        data = resp.json()

        # Poll for results (Trino paginates via nextUri)
        max_polls = 60
        polls = 0
        while polls < max_polls:
            state = data.get("stats", {}).get("state", "")
            rows = data.get("data", [])
            if rows:
                all_rows.extend(rows)

            next_uri = data.get("nextUri")
            if not next_uri:
                break

            if state in ("FAILED", "CANCELED"):
                error = data.get("error", {}).get("message", "Unknown Trino error")
                raise RuntimeError(f"Trino query failed: {error}")

            resp = await client.get(next_uri, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            polls += 1

    return all_rows


def _infer_description(column_name: str, data_type: str) -> str:
    """Generate a human-readable description based on column name patterns."""
    name_lower = column_name.lower()

    # Common patterns
    if name_lower in ("id", "employee_id", "task_id", "review_id", "department_id"):
        return f"Unique identifier for this record"
    if name_lower.endswith("_id"):
        base = name_lower.replace("_id", "").replace("_", " ")
        return f"Foreign key reference to {base}"
    if name_lower in ("created_at", "created_date"):
        return "Timestamp when this record was created"
    if name_lower in ("updated_at", "updated_date"):
        return "Timestamp when this record was last updated"
    if name_lower == "status":
        return "Current status of this record"
    if name_lower == "name":
        return "Name or title of this record"
    if name_lower in ("email",):
        return "Email address"
    if name_lower in ("salary",):
        return "Annual salary amount"
    if name_lower in ("score",):
        return "Numeric score or rating"
    if "date" in name_lower:
        return f"Date field: {column_name.replace('_', ' ')}"
    if data_type.upper() in ("BOOLEAN", "BOOL"):
        return f"Boolean flag: {column_name.replace('_', ' ')}"

    # Fallback: humanize the column name
    return column_name.replace("_", " ").capitalize()


def invalidate_cache() -> None:
    """Force cache invalidation — call after CSV/Excel upload creates new tables."""
    global _introspected_cache, _introspected_cache_ttl
    _introspected_cache = None
    _introspected_cache_ttl = 0.0
    logger.info("Schema introspection cache invalidated")
