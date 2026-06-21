"""
Metadata client for the AI Engine.

Fetches dataset/column metadata from postgres-meta so the AI Engine
can build rich, accurate prompts without requiring full schema dumps
in every LLM call.

This follows the principle: metadata lives in the registry,
not in environment variables or hardcoded prompts.
"""

import logging
from typing import List, Optional
import asyncpg
from config import settings
from models import DatasetMeta, DatasetColumn

logger = logging.getLogger(__name__)

# Simple in-memory cache — refresh every 5 minutes
_cache: Optional[List[DatasetMeta]] = None
_cache_ttl: float = 0.0
CACHE_DURATION_SECONDS = 300.0


async def get_datasets() -> List[DatasetMeta]:
    """
    Fetch all active datasets and their column metadata from postgres-meta.
    Results are cached to avoid repeated DB calls for every LLM request.
    """
    import time

    global _cache, _cache_ttl

    now = time.monotonic()
    if _cache is not None and now < _cache_ttl:
        logger.debug("Returning cached metadata")
        return _cache

    logger.info("Fetching metadata from postgres-meta database")

    dsn = f"postgresql://{settings.postgres_meta_user}:{settings.postgres_meta_password}@{settings.postgres_meta_host}:{settings.postgres_meta_port}/{settings.postgres_meta_db}"

    try:
        conn = await asyncpg.connect(dsn)
        try:
            # Fetch all active datasets
            dataset_rows = await conn.fetch("""
                SELECT id, name, description, source_type,
                       trino_catalog || '.' || trino_schema || '.' || trino_table AS trino_path
                FROM datasets
                WHERE is_active = true
                ORDER BY name
            """)

            datasets = []
            for row in dataset_rows:
                # Fetch columns for this dataset
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

            _cache = datasets
            _cache_ttl = now + CACHE_DURATION_SECONDS
            logger.info(f"Loaded {len(datasets)} datasets from metadata DB")
            return datasets

        finally:
            await conn.close()

    except Exception as e:
        logger.error(f"Failed to fetch metadata: {e}")
        # Return cached data if available, even if stale
        if _cache is not None:
            logger.warning("Returning stale metadata cache due to DB error")
            return _cache
        return []
