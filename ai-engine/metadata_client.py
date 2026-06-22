"""
Metadata client for the AI Engine.

Fetches dataset/column metadata from postgres-meta so the AI Engine
can build rich, accurate prompts without requiring full schema dumps
in every LLM call.

Strategy (merged approach):
  1. Fetch manually registered datasets from postgres-meta (rich descriptions, sample values)
  2. Fetch live schema from Trino's information_schema (auto-discovers new tables/uploads)
  3. Merge: registered metadata takes priority over auto-discovered
     → A table in postgres-meta gets its curated descriptions
     → A new upload or unregistered table still appears with auto-generated descriptions

This means new CSV uploads or new database tables become immediately
queryable without any manual metadata registration step.
"""

import logging
import time
from typing import List, Optional

import asyncpg

from config import settings
from models import DatasetMeta, DatasetColumn
from schema_introspector import get_introspected_datasets, invalidate_cache

logger = logging.getLogger(__name__)

# Simple in-memory cache — refresh every 5 minutes
_cache: Optional[List[DatasetMeta]] = None
_cache_ttl: float = 0.0
CACHE_DURATION_SECONDS = 300.0


async def get_datasets() -> List[DatasetMeta]:
    """
    Fetch all active datasets from both postgres-meta and Trino introspection.
    Merges the two sources with manual registrations taking priority.
    Results are cached to avoid repeated calls on every LLM request.
    """
    global _cache, _cache_ttl

    now = time.monotonic()
    if _cache is not None and now < _cache_ttl:
        logger.debug("Returning cached merged metadata")
        return _cache

    logger.info("Refreshing dataset metadata...")

    # Fetch from both sources concurrently
    registered = await _fetch_registered_datasets()
    introspected = await _fetch_introspected_datasets_safe()

    # Merge: registered takes priority
    merged = _merge_datasets(registered, introspected)

    _cache = merged
    _cache_ttl = now + CACHE_DURATION_SECONDS
    logger.info(
        f"Metadata refreshed: {len(registered)} registered + "
        f"{len(introspected)} introspected = {len(merged)} total datasets"
    )
    return merged


async def _fetch_registered_datasets() -> List[DatasetMeta]:
    """Fetch manually registered datasets from postgres-meta."""
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
                col_rows = await conn.fetch(
                    """
                    SELECT column_name, data_type,
                           COALESCE(description, '') as description,
                           is_joinable,
                           COALESCE(sample_values, '') as sample_values
                    FROM dataset_columns
                    WHERE dataset_id = $1
                    ORDER BY id
                """,
                    row["id"],
                )

                columns = [
                    DatasetColumn(
                        column_name=c["column_name"],
                        data_type=c["data_type"],
                        description=c["description"],
                        is_joinable=c["is_joinable"],
                        sample_values=c["sample_values"],
                    )
                    for c in col_rows
                ]

                datasets.append(
                    DatasetMeta(
                        id=row["id"],
                        name=row["name"],
                        description=row["description"] or "",
                        source_type=row["source_type"],
                        trino_path=row["trino_path"],
                        columns=columns,
                    )
                )

            logger.info(f"Loaded {len(datasets)} registered datasets from postgres-meta")
            return datasets

        finally:
            await conn.close()

    except Exception as e:
        logger.error(f"Failed to fetch registered metadata: {e}")
        return []


async def _fetch_introspected_datasets_safe() -> List[DatasetMeta]:
    """Fetch introspected datasets, returning empty list on failure (non-critical)."""
    try:
        return await get_introspected_datasets()
    except Exception as e:
        logger.warning(f"Schema introspection failed (non-critical): {e}")
        return []


def _merge_datasets(
    registered: List[DatasetMeta],
    introspected: List[DatasetMeta],
) -> List[DatasetMeta]:
    """
    Merge registered and introspected datasets.

    Rules:
    - If a table exists in both: use the registered version (richer descriptions)
      but supplement any missing columns from introspection
    - If a table only exists in introspection (e.g., new CSV upload): include it
    - If a table only exists in registration: include it
    """
    # Build lookup by trino_path
    registered_by_path = {ds.trino_path: ds for ds in registered}
    registered_by_name = {ds.name: ds for ds in registered}

    result = list(registered)  # Start with all registered datasets

    for introspected_ds in introspected:
        # Check if already registered (by trino path or name)
        is_registered = (
            introspected_ds.trino_path in registered_by_path
            or introspected_ds.name in registered_by_name
        )

        if is_registered:
            # Already have this table registered — skip introspected version
            # (registered has better descriptions/sample_values)
            continue

        # New table not in registry (e.g., freshly uploaded CSV)
        logger.info(f"Adding auto-discovered dataset: {introspected_ds.name} ({introspected_ds.trino_path})")
        result.append(introspected_ds)

    return result


def invalidate_metadata_cache() -> None:
    """
    Force a full cache refresh on next request.
    Call this after a CSV/Excel upload creates a new table.
    """
    global _cache, _cache_ttl
    _cache = None
    _cache_ttl = 0.0
    invalidate_cache()  # Also invalidate the introspector's cache
    logger.info("Full metadata cache invalidated — next request will re-fetch")
