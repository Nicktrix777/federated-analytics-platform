"""Sample-value discovery for schema-RAG prompts.

Auto-discovered schemas carry column TYPES but no example VALUES, which forces
the LLM to guess string literals from the question's wording — e.g. filtering
`nationality = 'Indian'` when the data actually stores the ISO code `'IND'`, so
a syntactically perfect query returns 0 rows. This module fixes that: for each
active dataset it pulls a few whole rows via Trino and walks the nested
ROW/ARRAY(ROW) structure ONCE per table (rows come back as positional arrays
matching each type's field order), collecting a handful of representative scalar
values per leaf path. The values land in dataset_columns.sample_values as JSON
keyed by full leaf path, and the schema renderer surfaces them next to each
field so the model filters on real values.

Schema-agnostic: it works purely off the parsed Trino type, so any nested source
(Elasticsearch, Mongo, relational) samples the same way. Best-effort — a table
that fails to sample is skipped, never blocking anything.
"""

import json
import logging

from agents.tools._common import (
    build_trino_path,
    meta_connection,
    quote_trino_ident,
    run_trino_query,
)
from schema_render import parse_trino_type

logger = logging.getLogger(__name__)

SAMPLE_ROWS = 25          # whole rows pulled per table
MAX_VALUES_PER_LEAF = 12  # distinct example values kept per categorical leaf
MAX_ARRAY_ELEMS = 40      # array elements walked per row (bounds deep fan-out)
MAX_VALUE_LEN = 60        # skip longer values — free text, not enum-like
# Categorical filter: only keep a leaf if it looks enum-like — bounded distinct
# values AND real repetition. This drops the noise (ids, hashes, urls, keys,
# free text) that would otherwise bury the useful codes like nationality='IND'.
DISTINCT_TRACK_CAP = 60   # if a leaf exceeds this many distinct values it's high-cardinality → drop
MIN_OBSERVATIONS = 3      # need a few observations before judging cardinality
REPEAT_RATIO = 1.5        # observations must be >= distinct * this (values recur)

# Scalar types worth sampling for value-guessing are the categorical ones.
# Continuous/temporal types (amounts, dates) don't help the model pick a literal
# and only add noise, so they're skipped.
_SKIP_SCALAR_PREFIXES = ("timestamp", "date", "time", "double", "real", "decimal")


def _sampleable(type_str: str) -> bool:
    t = type_str.strip().lower()
    return not any(t.startswith(p) for p in _SKIP_SCALAR_PREFIXES)


def _keep(st: dict) -> bool:
    """Keep a leaf only if it's enum-like: bounded distinct values that recur.

    Drops high-cardinality noise — ids, hashes, urls, keys, free text — which
    are unique-per-row (distinct ~= observations, or distinct over the cap).
    """
    if st["over"] or st["obs"] < MIN_OBSERVATIONS:
        return False
    distinct = len(st["distinct"])
    return distinct >= 1 and st["obs"] >= distinct * REPEAT_RATIO


def _collect(value, node: dict, path: str, acc: dict) -> None:
    """Walk a Trino value against its parsed type, tallying scalar leaf values.

    acc[path] = {"vals": [...distinct in order...], "distinct": set, "obs": int,
                 "over": bool}. Cardinality is judged later in _keep().
    """
    if value is None:
        return
    kind = node["kind"]
    if kind == "scalar":
        if not _sampleable(node["type"]) or isinstance(value, (list, dict)):
            return
        s = str(value)
        if not s or len(s) > MAX_VALUE_LEN:
            return
        st = acc.setdefault(path, {"vals": [], "distinct": set(), "obs": 0, "over": False})
        st["obs"] += 1
        if st["over"] or s in st["distinct"]:
            return
        if len(st["distinct"]) >= DISTINCT_TRACK_CAP:
            st["over"] = True  # too many distinct values → high-cardinality, drop later
            return
        st["distinct"].add(s)
        if len(st["vals"]) < MAX_VALUES_PER_LEAF:
            st["vals"].append(s)
    elif kind == "row":
        # Trino returns a ROW as a positional array matching the field order.
        if isinstance(value, list):
            for (fname, fnode), v in zip(node["fields"], value):
                _collect(v, fnode, f"{path}.{fname}" if fname else path, acc)
    elif kind == "array":
        if isinstance(value, list):
            for elem in value[:MAX_ARRAY_ELEMS]:
                _collect(elem, node["element"], path, acc)
    # map: skipped — keys/values aren't useful literals to guess


async def _sample_dataset(conn, ds: dict) -> int:
    """Sample one dataset and persist per-column sample_values. Returns #columns updated."""
    cols = await conn.fetch(
        "SELECT column_name, data_type FROM dataset_columns WHERE dataset_id = $1 ORDER BY id",
        ds["id"],
    )
    if not cols:
        return 0

    names = [c["column_name"] for c in cols]
    types = {c["column_name"]: c["data_type"] for c in cols}
    path = build_trino_path(ds["trino_catalog"], ds["trino_schema"], ds["trino_table"])
    select_list = ", ".join(quote_trino_ident(n) for n in names)
    rows = await run_trino_query(f"SELECT {select_list} FROM {path} LIMIT {SAMPLE_ROWS}")

    acc: dict = {}
    parsed = {n: parse_trino_type(types[n]) for n in names}
    for row in rows:
        for i, name in enumerate(names):
            if i < len(row):
                _collect(row[i], parsed[name], name, acc)

    # Keep only enum-like leaves and group them under their top-level column.
    by_col: dict = {}
    for leaf_path, st in acc.items():
        if _keep(st):
            by_col.setdefault(leaf_path.split(".", 1)[0], {})[leaf_path] = st["vals"]

    updated = 0
    for name in names:
        payload = by_col.get(name)
        if not payload:
            continue
        await conn.execute(
            "UPDATE dataset_columns SET sample_values = $1 WHERE dataset_id = $2 AND column_name = $3",
            json.dumps(payload, ensure_ascii=False),
            ds["id"],
            name,
        )
        updated += 1
    return updated


async def reindex_samples() -> dict:
    """Sample every active dataset and refresh dataset_columns.sample_values.

    Returns a summary dict. Never raises for a single-dataset failure — it logs
    and moves on, so a flaky source can't stall the rest.
    """
    async with meta_connection() as conn:
        datasets = await conn.fetch(
            "SELECT id, trino_catalog, trino_schema, trino_table FROM datasets WHERE is_active = true"
        )
        sampled, cols_updated = 0, 0
        for ds in datasets:
            try:
                n = await _sample_dataset(conn, dict(ds))
                if n:
                    sampled += 1
                    cols_updated += n
            except Exception as e:
                logger.warning(
                    f"Sample-value discovery failed for {ds['trino_catalog']}."
                    f"{ds['trino_schema']}.{ds['trino_table']}: {e}"
                )
        summary = {"datasets": len(datasets), "sampled": sampled, "columns_updated": cols_updated}
        logger.info(f"Sample values refreshed: {summary}")
        return summary
