"""Metadata-version watcher + enrichment pipeline (Step 1b).

The AI Engine is a stateless planner, but it maintains a few derived caches and
indexes over postgres-meta: the TTL tool caches (schema/dataset lookups), the
per-column sample values, and the pgvector dataset embeddings for schema-RAG.
Those must refresh when the underlying metadata changes.

Before Step 1b that refresh was push-driven: Core API POSTed /api/invalidate-cache
after every upload / schema refresh / catalog sync. That "poke web" missed the
one write path that has no HTTP entrypoint at all — SQL-only curation (editing
`dataset_columns.description`, inserting a `table_relationships` row by hand) —
and coupled the two services tightly.

This module replaces it with a pull: `metadata_state.version` (bumped by DB
triggers on datasets/dataset_columns/table_relationships + one explicit bump in
Core API's RefreshSchema) is polled here, and any change clears the tool caches
and re-runs the enrichment pipeline. Staleness drops from "whenever someone
remembers to poke" to at most one poll interval.
"""

import asyncio
import logging
import time
from typing import Optional

from config import settings
from agents.tools._cache import clear_all as clear_tool_cache
from agents.tools._common import meta_connection
from embeddings import reindex_datasets
from profiler import reindex_profiles

logger = logging.getLogger(__name__)

# Serializes enrichment runs. The watcher is a single loop so runs are already
# sequential, but the lock makes that explicit and pairs with the post-run
# version re-read that coalesces bumps arriving mid-run.
_enrichment_lock = asyncio.Lock()


async def get_metadata_version() -> Optional[int]:
    """Return the current metadata version, or None if unavailable.

    Tolerates a missing `metadata_state` table (a deployment whose 0002 migration
    hasn't run yet) by returning None instead of raising — the watcher then keeps
    polling harmlessly rather than crash-looping.
    """
    try:
        async with meta_connection() as conn:
            row = await conn.fetchrow("SELECT version FROM metadata_state WHERE id = TRUE")
        return int(row["version"]) if row else None
    except Exception as e:
        logger.debug(f"metadata_state version read unavailable (table missing?): {e}")
        return None


async def run_enrichment_pipeline(provider) -> None:
    """Profile columns, then re-embed dataset schemas for RAG. Best-effort.

    v1 body: profiling runs FIRST and unconditionally (it feeds every prompt,
    RAG or not) so the freshly-profiled values are baked into the embeddings
    that follow. Each stage swallows its own errors so a flaky Trino source
    or embeddings hiccup never kills the watcher.
    """
    try:
        await reindex_profiles()
    except Exception as e:
        logger.warning(f"Column profiling failed: {e}")

    if not settings.schema_rag_enabled or provider is None:
        return
    try:
        await reindex_datasets(provider)
    except Exception as e:
        logger.warning(f"Schema embedding reindex failed (RAG will use full catalog): {e}")


async def watch_metadata_version(provider_getter) -> None:
    """Poll metadata_state.version; on change, clear caches + run enrichment.

    `provider_getter` is a zero-arg callable returning the CURRENT embeddings
    provider (read fresh each run) so a live settings hot-reload that swaps the
    provider is picked up without restarting the watcher.

    This is the whole replacement for the poke web. The first iteration always
    runs the pipeline so startup still warms the RAG indexes exactly as
    `_reindex_schemas_safe` did on boot. Thereafter it runs only when the version
    moves.

    Coalescing keeps a burst of writes from fanning out into many runs:
      - `enrichment_min_interval_seconds` floors how often the pipeline runs; a
        change seen too soon after the last run is left un-consumed (last_version
        stays stale) so it's re-detected once the floor clears.
      - after each run the version is re-read inside the lock, so any bump that
        landed while the pipeline was running is absorbed rather than replayed.

    Cancelled cleanly on shutdown (CancelledError is re-raised).
    """
    poll = settings.metadata_version_poll_seconds
    min_interval = settings.enrichment_min_interval_seconds
    last_version: Optional[int] = None
    last_run_at: Optional[float] = None
    first = True

    while True:
        try:
            version = await get_metadata_version()
            changed = version is not None and version != last_version
            if first or changed:
                now = time.monotonic()
                if last_run_at is not None and (now - last_run_at) < min_interval:
                    # Debounce floor: too soon since the last run. Leave
                    # last_version stale so the change re-triggers next poll.
                    logger.debug(
                        f"metadata change (version={version}) within debounce floor; deferring"
                    )
                else:
                    async with _enrichment_lock:
                        if changed:
                            clear_tool_cache()
                        logger.info(
                            f"Running enrichment pipeline (version={version}, "
                            f"reason={'startup warm' if first else 'metadata changed'})"
                        )
                        await run_enrichment_pipeline(provider_getter())
                        # Re-read so bumps during the run are absorbed, not replayed.
                        last_version = await get_metadata_version()
                    last_run_at = time.monotonic()
                    first = False
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"metadata watcher iteration failed: {e}")
        await asyncio.sleep(poll)
