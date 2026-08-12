"""Column profiler — deterministic pattern detection, no literal values ever persisted.

Replaces sampling.py.  Carries over the core Trino row-walk logic (_collect,
_keep, cardinality constants, LIMIT 25 sample) and adds:

  - null-fraction tallying
  - _detect_pattern():  deterministic regex classification (uuid, alpha3_code,
    alpha2_code, numeric_code, iso_date, email, url, internal_enum)
  - _suggest_semantic_type():  pattern + column-name heuristics mapping to the
    doc taxonomy (country_code_alpha3, currency_code, internal_enum, uuid, …).
    **Suggestion only** — persisted to column_profiles.suggested_semantic_type;
    humans promote to dataset_columns.semantic_type via SQL (one-writer rule).
  - real sampled values are used ONLY in-memory to compute the above — a
    literal customer value is never written anywhere. Nested/leaf paths
    (Mongo/ES documents, nested Trino ROW/ARRAY) get their own derived
    pattern/semantic_type too, stored in column_profiles.stats keyed by leaf
    path, so per-leaf filter-format guidance survives without literals.
  - sensitivity still gates PROFILING ITSELF: forbidden → DELETE + skip
    entirely; sensitive/public/unclassified all get the same derived stats
    (no per-tier difference now that literals are never stored regardless).
  - content-hash upsert to column_profiles:  skip unchanged datasets.

Schema-agnostic: works purely off parsed Trino types — any nested source
(Elasticsearch, Mongo, relational) profiles the same way.  Best-effort: a
table that fails to profile is skipped.
"""

import hashlib
import json
import logging
import re
from typing import Optional

from agents.tools._common import (
    build_trino_path,
    meta_connection,
    quote_trino_ident,
    run_trino_query,
)
from schema_render import parse_trino_type

logger = logging.getLogger(__name__)

# ── Sampling constants (carried from sampling.py) ────────────────
SAMPLE_ROWS = 25          # whole rows pulled per table
MAX_VALUES_PER_LEAF = 12  # distinct example values kept per categorical leaf
MAX_ARRAY_ELEMS = 40      # array elements walked per row (bounds deep fan-out)
MAX_VALUE_LEN = 60        # skip longer values — free text, not enum-like

# Categorical filter: only keep a leaf if it looks enum-like.
DISTINCT_TRACK_CAP = 60   # too many distinct → high-cardinality → drop
MIN_OBSERVATIONS = 3      # need a few observations before judging cardinality
REPEAT_RATIO = 1.5        # observations must be >= distinct * this

# Scalar types worth sampling for value-guessing are the categorical ones.
_SKIP_SCALAR_PREFIXES = ("timestamp", "date", "time", "double", "real", "decimal")

# ── Pattern detection regexes ────────────────────────────────────
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_ALPHA3_RE = re.compile(r"^[A-Z]{3}$")
_ALPHA2_RE = re.compile(r"^[A-Z]{2}$")
_NUMERIC_CODE_RE = re.compile(r"^\d{1,5}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# Name-based heuristics for semantic type suggestion
_NAME_SEMANTIC_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)(country|nationality|nation)"), "country_code"),
    (re.compile(r"(?i)currency"), "currency_code"),
    (re.compile(r"(?i)(email|e_mail)"), "email"),
    (re.compile(r"(?i)(url|link|href|website)"), "url"),
    (re.compile(r"(?i)(uuid|guid)"), "uuid"),
]


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


# ── Pattern detection (deterministic, no LLM) ────────────────────

def _detect_pattern(values: list[str]) -> Optional[str]:
    """Classify a list of sample values into a known pattern, or None.

    Tests are ordered by specificity.  All values in the sample must match the
    same pattern for a positive classification (majority-vote invites
    misclassification on mixed-content columns).
    """
    if not values:
        return None

    # Pre-filter: take up to 20 non-empty values
    test_values = [v for v in values[:20] if v.strip()]
    if len(test_values) < 2:
        return None

    # UUID
    if all(_UUID_RE.match(v) for v in test_values):
        return "uuid"

    # Email
    if all(_EMAIL_RE.match(v) for v in test_values):
        return "email"

    # URL
    if all(_URL_RE.match(v) for v in test_values):
        return "url"

    # ISO date
    if all(_ISO_DATE_RE.match(v) for v in test_values):
        return "iso_date"

    # Alpha-3 codes (country/currency codes like IND, USD)
    if all(_ALPHA3_RE.match(v) for v in test_values):
        return "alpha3_code"

    # Alpha-2 codes (country codes like IN, US)
    if all(_ALPHA2_RE.match(v) for v in test_values):
        return "alpha2_code"

    # Numeric codes (short numeric identifiers)
    if all(_NUMERIC_CODE_RE.match(v) for v in test_values):
        # Only classify as numeric_code if values are short and few-distinct
        # (otherwise it's just a regular integer column)
        distinct = len(set(test_values))
        if distinct <= DISTINCT_TRACK_CAP and len(test_values) >= distinct * REPEAT_RATIO:
            return "numeric_code"

    # Internal enum: all uppercase or all lowercase, limited alphabet, repeating
    if len(set(test_values)) <= 30:
        # Check if values look like enum constants (all-caps with underscores, or simple lowercase)
        all_upper_enum = all(re.match(r"^[A-Z][A-Z0-9_]*$", v) for v in test_values)
        all_lower_enum = all(re.match(r"^[a-z][a-z0-9_]*$", v) for v in test_values)
        if (all_upper_enum or all_lower_enum) and len(test_values) >= len(set(test_values)) * REPEAT_RATIO:
            return "internal_enum"

    return None


def _suggest_semantic_type(
    pattern: Optional[str], column_name: str
) -> Optional[str]:
    """Map a detected pattern + column name to the doc's semantic type taxonomy.

    This is a **suggestion only** — it goes into
    column_profiles.suggested_semantic_type. Humans promote to
    dataset_columns.semantic_type via SQL.
    """
    # Pattern-first (most reliable)
    if pattern == "uuid":
        return "uuid"
    if pattern == "email":
        return "email"
    if pattern == "url":
        return "url"
    if pattern == "alpha3_code":
        # Disambiguate country vs currency via column name
        name_lower = column_name.lower()
        if any(kw in name_lower for kw in ("currency", "curr")):
            return "currency_code"
        return "country_code_alpha3"
    if pattern == "alpha2_code":
        return "country_code_alpha2"
    if pattern == "internal_enum":
        return "internal_enum"
    if pattern == "iso_date":
        return None  # dates are structural, not a semantic type
    if pattern == "numeric_code":
        # Could be many things — let name heuristics decide
        pass

    # Name-based fallback
    for regex, sem_type in _NAME_SEMANTIC_HINTS:
        if regex.search(column_name):
            return sem_type

    return None


# ── Null-fraction tallying ────────────────────────────────────────

def _count_nulls(rows: list, col_index: int, total: int) -> float:
    """Compute the null fraction for a column across sampled rows."""
    if total == 0:
        return 0.0
    nulls = sum(1 for row in rows if col_index >= len(row) or row[col_index] is None)
    return round(nulls / total, 4)


# ── Per-leaf derived stats (replaces literal sample_values) ──────

def _leaf_stats(payload: Optional[dict], top_level_name: str) -> Optional[dict]:
    """Derive {leaf_path: {pattern, semantic_type}} for every NESTED leaf
    under a column, from its real sampled values — used only transiently
    here to classify, never persisted. Excludes the column's own top-level
    leaf (payload[top_level_name]), since that's already covered by the
    dedicated pattern/suggested_semantic_type columns. Returns None rather
    than {} when there's nothing worth recording, matching every other
    "nothing to report" convention in this module.
    """
    if not payload:
        return None
    stats: dict = {}
    for leaf_path, vals in payload.items():
        if leaf_path == top_level_name:
            continue
        leaf_pattern = _detect_pattern(vals)
        leaf_semantic = _suggest_semantic_type(leaf_pattern, leaf_path)
        if leaf_pattern or leaf_semantic:
            stats[leaf_path] = {"pattern": leaf_pattern, "semantic_type": leaf_semantic}
    return stats or None


# ── Content hash ──────────────────────────────────────────────────

def _profile_content_hash(
    stats_json: Optional[str],
    pattern: Optional[str],
    distinct_count: Optional[int],
    null_fraction: Optional[float],
) -> str:
    """Hash of the profile content to detect changes across runs."""
    parts = [
        stats_json or "",
        pattern or "",
        str(distinct_count or 0),
        str(null_fraction or 0.0),
    ]
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


# ── Per-dataset profiling ─────────────────────────────────────────

async def _profile_dataset(conn, ds: dict) -> dict:
    """Profile one dataset: sample rows, detect patterns, gate by sensitivity, upsert.

    Returns a summary dict {columns_profiled, columns_skipped, columns_forbidden}.
    """
    cols = await conn.fetch(
        """SELECT id, column_name, data_type,
                  COALESCE(sensitivity, 'unclassified') AS sensitivity
           FROM dataset_columns WHERE dataset_id = $1 ORDER BY id""",
        ds["id"],
    )
    if not cols:
        return {"columns_profiled": 0, "columns_skipped": 0, "columns_forbidden": 0}

    names = [c["column_name"] for c in cols]
    types = {c["column_name"]: c["data_type"] for c in cols}
    sensitivities = {c["column_name"]: c["sensitivity"] for c in cols}
    col_ids = {c["column_name"]: c["id"] for c in cols}

    # Handle forbidden columns first — DELETE any existing profile rows
    forbidden_ids = [col_ids[n] for n in names if sensitivities[n] == "forbidden"]
    if forbidden_ids:
        await conn.execute(
            "DELETE FROM column_profiles WHERE dataset_column_id = ANY($1::int[])",
            forbidden_ids,
        )

    # Sample rows from Trino
    path = build_trino_path(ds["trino_catalog"], ds["trino_schema"], ds["trino_table"])
    select_list = ", ".join(quote_trino_ident(n) for n in names)
    rows = await run_trino_query(f"SELECT {select_list} FROM {path} LIMIT {SAMPLE_ROWS}")
    total_rows = len(rows)

    # Walk sampled rows to collect values per leaf
    acc: dict = {}
    parsed = {n: parse_trino_type(types[n]) for n in names}
    for row in rows:
        for i, name in enumerate(names):
            if i < len(row):
                _collect(row[i], parsed[name], name, acc)

    # Keep only enum-like leaves and group under their top-level column
    by_col: dict = {}
    for leaf_path, st in acc.items():
        if _keep(st):
            by_col.setdefault(leaf_path.split(".", 1)[0], {})[leaf_path] = st["vals"]

    # Load existing content hashes to skip unchanged profiles
    profileable_ids = [col_ids[n] for n in names if sensitivities[n] not in ("forbidden",)]
    existing_hashes = {}
    if profileable_ids:
        hash_rows = await conn.fetch(
            "SELECT dataset_column_id, content_hash FROM column_profiles WHERE dataset_column_id = ANY($1::int[])",
            profileable_ids,
        )
        existing_hashes = {r["dataset_column_id"]: r["content_hash"] for r in hash_rows}

    profiled = 0
    skipped = 0
    for i, name in enumerate(names):
        sensitivity = sensitivities[name]
        if sensitivity == "forbidden":
            continue  # already handled above

        col_id = col_ids[name]
        payload = by_col.get(name)

        # Compute stats
        null_frac = _count_nulls(rows, i, total_rows)

        # Collect all distinct values for this column (for pattern detection)
        st = acc.get(name)
        all_values = st["vals"] if st else []
        distinct_count = len(st["distinct"]) if st else 0

        # Detect pattern
        pattern = _detect_pattern(all_values)

        # Suggest semantic type
        suggested = _suggest_semantic_type(pattern, name)

        # Derived per-leaf stats for nested paths — real values are used only
        # in-memory above (all_values/payload) to classify; nothing literal
        # is ever serialized here or anywhere below.
        leaf_stats = _leaf_stats(payload, name)
        stats_json = json.dumps(leaf_stats, ensure_ascii=False) if leaf_stats else None

        # Content hash to skip unchanged
        content_hash = _profile_content_hash(stats_json, pattern, distinct_count, null_frac)
        if existing_hashes.get(col_id) == content_hash:
            skipped += 1
            continue

        # Upsert
        await conn.execute(
            """INSERT INTO column_profiles
                   (dataset_column_id, pattern, suggested_semantic_type,
                    distinct_count, null_fraction, stats,
                    content_hash, profiled_at)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, NOW())
               ON CONFLICT (dataset_column_id) DO UPDATE SET
                   pattern = EXCLUDED.pattern,
                   suggested_semantic_type = EXCLUDED.suggested_semantic_type,
                   distinct_count = EXCLUDED.distinct_count,
                   null_fraction = EXCLUDED.null_fraction,
                   stats = EXCLUDED.stats,
                   content_hash = EXCLUDED.content_hash,
                   profiled_at = NOW()
            """,
            col_id, pattern, suggested, distinct_count, null_frac,
            stats_json, content_hash,
        )
        profiled += 1

    return {
        "columns_profiled": profiled,
        "columns_skipped": skipped,
        "columns_forbidden": len(forbidden_ids),
    }


async def reindex_profiles() -> dict:
    """Profile every active dataset and persist to column_profiles.

    Returns a summary dict.  Never raises for a single-dataset failure — it logs
    and moves on, so a flaky source can't stall the rest.
    """
    async with meta_connection() as conn:
        datasets = await conn.fetch(
            "SELECT id, trino_catalog, trino_schema, trino_table FROM datasets WHERE is_active = true"
        )
        totals = {"datasets": len(datasets), "profiled": 0, "columns_profiled": 0,
                  "columns_skipped": 0, "columns_forbidden": 0}
        for ds in datasets:
            try:
                result = await _profile_dataset(conn, dict(ds))
                if result["columns_profiled"] > 0:
                    totals["profiled"] += 1
                totals["columns_profiled"] += result["columns_profiled"]
                totals["columns_skipped"] += result["columns_skipped"]
                totals["columns_forbidden"] += result["columns_forbidden"]
            except Exception as e:
                logger.warning(
                    f"Column profiling failed for {ds['trino_catalog']}."
                    f"{ds['trino_schema']}.{ds['trino_table']}: {e}"
                )
        logger.info(f"Column profiles refreshed: {totals}")
        return totals
