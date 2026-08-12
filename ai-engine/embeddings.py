"""
Schema RAG — pgvector-backed retrieval of the datasets relevant to a question.

The Core API sends the AI Engine its FULL dataset catalog on every request.
For a large catalog that bloats the prompt, slows the model down, and invites
column hallucinations from unrelated tables. This module embeds each dataset
once (name + description + columns) into postgres-meta's dataset_embeddings
table, then at request time embeds the question and returns the ids of the
most similar datasets so the pipeline can prompt with only those.

Ownership note: the AI Engine is otherwise read-only on postgres-meta. Writing
this ONE derived-cache table is a deliberate, narrow exception — embeddings are
recomputable from the datasets it already reads, not user or business data.

Everything degrades gracefully:
  - pgvector missing / table empty  → retrieve_relevant_dataset_ids returns
    None, and callers fall back to the full catalog they were given.
  - embedding call fails             → same fallback, logged as a warning.
So a fresh deployment behaves exactly as before until the first reindex runs.
"""

import hashlib
import logging

from agents.tools._common import meta_connection
from config import settings
from llm.openai_provider import OpenAIProvider
from schema_render import leaf_paths, summarize_type

logger = logging.getLogger(__name__)


def _build_embed_text(name: str, description: str, columns: list[dict]) -> str:
    """The text embedded for a dataset — what a question is matched against.

    For nested columns the opaque ROW/ARRAY(ROW) blob is replaced with a short
    type label plus the dotted leaf-field paths, so a question about a deeply
    nested field ("borrower nationality") still retrieves the dataset that holds
    it instead of drowning in boilerplate 'row varchar' tokens.
    """
    lines = [f"Dataset: {name}"]
    if description:
        lines.append(f"Description: {description}")
    for col in columns:
        col_name, data_type = col["column_name"], col["data_type"]
        col_line = f"- {col_name} ({summarize_type(data_type)})"
        if col.get("description"):
            col_line += f": {col['description']}"
        lines.append(col_line)
        paths = leaf_paths(col_name, data_type, max_leaves=30)
        if paths:
            lines.append("    fields: " + ", ".join(paths))
    return "\n".join(lines)


def _content_hash(embed_text: str, model: str) -> str:
    """Hash of (text, model) so a reindex skips datasets whose text is unchanged."""
    return hashlib.sha256(f"{model}\n{embed_text}".encode("utf-8")).hexdigest()


async def _load_dataset_texts(conn) -> dict[int, tuple[str, str]]:
    """Return {dataset_id: (embed_text, content_hash)} for every ACTIVE dataset."""
    rows = await conn.fetch(
        """
        SELECT d.id, d.name, d.description,
               dc.column_name, dc.data_type,
               dc.description AS column_description
        FROM datasets d
        LEFT JOIN dataset_columns dc ON dc.dataset_id = d.id
        WHERE d.is_active = true
        ORDER BY d.id, dc.id
        """
    )
    grouped: dict[int, dict] = {}
    for row in rows:
        ds = grouped.get(row["id"])
        if ds is None:
            ds = grouped[row["id"]] = {
                "name": row["name"],
                "description": row["description"] or "",
                "columns": [],
            }
        if row["column_name"] is not None:
            ds["columns"].append({
                "column_name": row["column_name"],
                "data_type": row["data_type"],
                "description": row["column_description"] or "",
            })

    out: dict[int, tuple[str, str]] = {}
    for ds_id, ds in grouped.items():
        text = _build_embed_text(ds["name"], ds["description"], ds["columns"])
        out[ds_id] = (text, _content_hash(text, settings.embedding_model))
    return out


async def reindex_datasets(provider: OpenAIProvider) -> dict:
    """(Re)embed active datasets whose text changed; prune stale rows.

    Idempotent and cheap on repeat runs — only datasets whose embed_text hash
    changed are re-embedded (one batched OpenAI call for all of them). Returns
    a small summary dict for logging.
    """
    async with meta_connection() as conn:
        current = await _load_dataset_texts(conn)
        existing = {
            r["dataset_id"]: r["content_hash"]
            for r in await conn.fetch("SELECT dataset_id, content_hash FROM dataset_embeddings")
        }

        stale_ids = [ds_id for ds_id in existing if ds_id not in current]
        changed = {
            ds_id: text_hash
            for ds_id, text_hash in current.items()
            if existing.get(ds_id) != text_hash[1]
        }

        if stale_ids:
            await conn.execute(
                "DELETE FROM dataset_embeddings WHERE dataset_id = ANY($1::int[])", stale_ids
            )

        if changed:
            ids = list(changed.keys())
            texts = [changed[i][0] for i in ids]
            vectors = await provider.embed(texts, settings.embedding_model, dimensions=settings.embedding_dim)
            await conn.executemany(
                """
                INSERT INTO dataset_embeddings (dataset_id, embedding, embed_text, content_hash, model, updated_at)
                VALUES ($1, $2, $3, $4, $5, now())
                ON CONFLICT (dataset_id) DO UPDATE
                SET embedding = EXCLUDED.embedding,
                    embed_text = EXCLUDED.embed_text,
                    content_hash = EXCLUDED.content_hash,
                    model = EXCLUDED.model,
                    updated_at = now()
                """,
                [
                    (ids[i], vectors[i], changed[ids[i]][0], changed[ids[i]][1], settings.embedding_model)
                    for i in range(len(ids))
                ],
            )

    summary = {"active": len(current), "embedded": len(changed), "pruned": len(stale_ids)}
    logger.info(f"Schema embeddings reindexed: {summary}")
    return summary


# ── Semantic few-shot examples (Phase 2) ─────────────────────────────

async def reindex_examples(provider: OpenAIProvider, limit: int) -> dict:
    """Embed successful AI-query questions not yet in query_example_embeddings.

    Incremental: an anti-join finds unindexed rows, so this is a cheap no-op
    once the backlog is embedded. Capped at `limit` most-recent rows per run to
    bound the embedding cost of a first-time backfill. Returns a summary dict.
    """
    async with meta_connection() as conn:
        rows = await conn.fetch(
            """
            SELECT a.id, a.question, a.sql_executed
            FROM audit_logs a
            LEFT JOIN query_example_embeddings e ON e.audit_log_id = a.id
            WHERE a.status = 'success' AND a.mode = 'ai'
              AND a.question IS NOT NULL
              AND a.sql_executed IS NOT NULL AND a.sql_executed <> ''
              AND e.audit_log_id IS NULL
            ORDER BY a.created_at DESC
            LIMIT $1
            """,
            limit,
        )
        if not rows:
            return {"embedded": 0}

        vectors = await provider.embed([r["question"] for r in rows], settings.embedding_model, dimensions=settings.embedding_dim)
        await conn.executemany(
            """
            INSERT INTO query_example_embeddings (audit_log_id, question, sql_executed, embedding, model)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (audit_log_id) DO NOTHING
            """,
            [
                (rows[i]["id"], rows[i]["question"], rows[i]["sql_executed"], vectors[i], settings.embedding_model)
                for i in range(len(rows))
            ],
        )

    logger.info(f"Query-example embeddings reindexed: embedded={len(rows)}")
    return {"embedded": len(rows)}


async def retrieve_similar_examples(
    provider: OpenAIProvider, question: str, k: int
) -> list[dict] | None:
    """Return up to k past successful queries most similar to `question`.

    Each item is {"question", "sql_executed"} — the shape context_bundle's
    few-shot renderer expects. Returns None (not []) when the example index is
    empty/unavailable, so callers can fall back to recency-based few-shots.
    """
    try:
        async with meta_connection() as conn:
            count = await conn.fetchval("SELECT count(*) FROM query_example_embeddings")
            if not count:
                return None
            (query_vec,) = await provider.embed([question], settings.embedding_model, dimensions=settings.embedding_dim)
            rows = await conn.fetch(
                """
                SELECT question, sql_executed
                FROM query_example_embeddings
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                query_vec,
                k,
            )
        return [{"question": r["question"], "sql_executed": r["sql_executed"]} for r in rows]
    except Exception as e:
        logger.warning(f"Semantic few-shot retrieval unavailable, using recency fallback: {e}")
        return None


async def retrieve_relevant_dataset_ids(
    provider: OpenAIProvider, question: str, k: int
) -> set[int] | None:
    """Return the ids of the top-k datasets most similar to the question.

    Returns None (not an empty set) when retrieval is unavailable — no
    embeddings indexed yet, pgvector missing, or the embedding call failed —
    so callers can distinguish "use everything" from "nothing matched".
    """
    try:
        async with meta_connection() as conn:
            count = await conn.fetchval("SELECT count(*) FROM dataset_embeddings")
            if not count:
                return None
            (query_vec,) = await provider.embed([question], settings.embedding_model, dimensions=settings.embedding_dim)
            # Cast $1 explicitly — asyncpg can't always infer the param type of
            # the <=> operand ("could not determine data type of parameter $1").
            rows = await conn.fetch(
                "SELECT dataset_id FROM dataset_embeddings ORDER BY embedding <=> $1::vector LIMIT $2",
                query_vec,
                k,
            )
        return {r["dataset_id"] for r in rows}
    except Exception as e:
        logger.warning(f"Schema-RAG retrieval unavailable, using full catalog: {e}")
        return None
