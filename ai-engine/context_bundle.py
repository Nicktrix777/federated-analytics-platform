"""
Context bundle — the ONE place the AI Engine assembles planning context.

Before this module, three call paths (fast path, full pipeline, dashboard,
widget repair) each stitched together their own view of the metadata: one built
a system prompt from datasets+relationships+examples, another built an
"extra_context" blob from datasets+relationships, and each separately self-loaded
the catalog, ran the schema-RAG trim, and fetched relationships. The logic drifted.

`build_context_bundle` now does that assembly once — self-load the catalog when
the caller sent none, trim it to the question via schema-RAG, and fetch the
curated join map (+ few-shot examples for the fast path) — and returns a
`ContextBundle`. Two pure renderers turn a bundle into prompt text:

  - `render_fast_path_system_prompt` / `render_fast_path_user_prompt` — the
    single-shot fast-path prompts (absorbed from the old prompt_builder.py).
  - `render_extra_context` — the schema+relationships block folded into the full
    deepagents pipeline / dashboard designer / widget-repair prompts (absorbed
    from the old main._build_extra_context).

Both renderers share `schema_render.py` and now emit the SAME new hint in one
place: a column whose sample values are absent or gated but which carries a
detected `pattern` renders `[format: <pattern>]` so the model still knows the
value shape (e.g. an ISO alpha-3 country code) without seeing real literals.

Nothing here is schema-specific — point the platform at different sources and the
bundle reshapes itself from live metadata.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from config import settings
from events import EventEmitter, NullEmitter
from models import ChatMessage, DatasetColumn, DatasetMeta
from schema_render import (
    categorical_values_block,
    column_sample_suffix,
    describe_column,
    parse_samples,
)
from agents.tools.metadata_tools import (
    _async_get_datasets,
    _async_get_patterns,
    _async_get_relationships,
    _async_get_value_lookups,
)
from embeddings import retrieve_relevant_dataset_ids, retrieve_similar_examples

logger = logging.getLogger(__name__)


@dataclass
class ContextBundle:
    """Everything a planning prompt needs, assembled once from live metadata.

    `datasets` is already trimmed by schema-RAG when a question was given.
    `lookups` maps coded columns to Trino-reachable reference tables so the
    model writes subqueries instead of guessing literal values.
    """

    datasets: List[DatasetMeta] = field(default_factory=list)
    relationships: List[dict] = field(default_factory=list)
    lookups: List[dict] = field(default_factory=list)
    examples: List[dict] = field(default_factory=list)


# ── Loaders (absorbed from main.py) ───────────────────────────────
async def _load_datasets() -> List[DatasetMeta]:
    """Load the full registered catalog from postgres-meta as DatasetMeta objects.

    The AI Engine is authoritative for reading metadata: callers no longer push
    the catalog in the request body, so the pipeline sources it here (from the
    same TTL-cached postgres-meta read the schema-analyst tool uses). Returns an
    empty list on failure — the pipeline then reports missing data / low
    confidence rather than crashing.
    """
    try:
        data = json.loads(await _async_get_datasets())
    except Exception as e:
        logger.warning(f"Could not load catalog for planning: {e}")
        return []
    if not isinstance(data, list):  # {"error": ...} sentinel from the loader
        logger.warning(f"Could not load catalog for planning: {data}")
        return []
    datasets: List[DatasetMeta] = []
    for d in data:
        try:
            datasets.append(DatasetMeta(**d))
        except Exception as e:
            logger.warning(f"Skipping malformed dataset metadata {d.get('name', '?')}: {e}")
    return datasets


async def _load_relationships() -> List[dict]:
    """Curated join relationships from postgres-meta (TTL-cached, deployment-global)."""
    try:
        data = json.loads(await _async_get_relationships())
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Could not load relationships for prompt context: {e}")
        return []


async def _load_lookups() -> List[dict]:
    """Coded-column value lookups from postgres-meta (TTL-cached, deployment-global)."""
    try:
        data = json.loads(await _async_get_value_lookups())
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Could not load value lookups for prompt context: {e}")
        return []


async def _load_examples(question: str, provider) -> List[dict]:
    """Few-shot examples for the prompt: semantically similar past queries.

    Prefers the top-k past successful queries most SIMILAR to `question`
    (pgvector). Falls back to the most RECENT successful queries (TTL-cached
    audit-log read) when the example index is empty/unavailable — so a fresh
    deployment behaves exactly as before Phase 2.
    """
    if settings.few_shot_rag_enabled and provider is not None:
        examples = await retrieve_similar_examples(provider, question, settings.few_shot_top_k)
        if examples is not None:
            return examples
    try:
        data = json.loads(await _async_get_patterns())
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"Could not load query examples for prompt context: {e}")
        return []


async def _select_relevant_datasets(
    datasets: List[DatasetMeta], question: str, provider, emitter: EventEmitter
) -> List[DatasetMeta]:
    """Trim the full catalog to the datasets relevant to the question.

    Returns `datasets` unchanged when schema-RAG can't help — disabled, no
    provider, no datasets, empty/unavailable index (retrieval returns None),
    nothing matched, or everything matched. Only a strict, non-empty subset is
    applied, which shrinks every downstream prompt. The full deepagents pipeline
    can still reach the whole catalog through its tools, so this never
    hard-limits what a hard question can see.
    """
    if not settings.schema_rag_enabled or provider is None or not datasets:
        return datasets
    ids = await retrieve_relevant_dataset_ids(provider, question, settings.schema_rag_top_k)
    if ids is None:
        return datasets
    filtered = [ds for ds in datasets if ds.id in ids]
    if not filtered or len(filtered) == len(datasets):
        return datasets
    await emitter.emit(
        "stage",
        stage="schema_retrieval",
        detail=f"selected {len(filtered)}/{len(datasets)} datasets by relevance",
    )
    logger.info(
        f"Schema-RAG selected {len(filtered)}/{len(datasets)} datasets: "
        f"{[ds.name for ds in filtered]}"
    )
    return filtered


async def build_context_bundle(
    question: Optional[str],
    *,
    provider,
    datasets: Optional[List[DatasetMeta]] = None,
    include_examples: bool = True,
    emitter: Optional[EventEmitter] = None,
) -> ContextBundle:
    """Assemble the planning context once, from live metadata.

    - `datasets`: when the caller already has a dataset list, it's used as-is;
      otherwise the catalog is self-loaded from postgres-meta (correction #2:
      the dashboard pipeline now self-loads through this path too).
    - `question`: drives the schema-RAG trim and few-shot retrieval. Pass None
      to skip both (widget repair wants the full catalog and no examples).
    - `include_examples`: fetch few-shot examples (only the fast-path system
      prompt renders them; the full pipeline / dashboard / repair don't).
    """
    emitter = emitter or NullEmitter()

    ds = list(datasets) if datasets else await _load_datasets()
    if question and ds:
        ds = await _select_relevant_datasets(ds, question, provider, emitter)

    relationships = await _load_relationships()
    lookups = await _load_lookups()
    examples: List[dict] = []
    if include_examples and question:
        examples = await _load_examples(question, provider)

    return ContextBundle(datasets=ds, relationships=relationships, lookups=lookups, examples=examples)


# ── Shared column-line hint ───────────────────────────────────────
def _column_hint_suffix(col: DatasetColumn, samples: dict) -> str:
    """Trailing hint for a top-level column line: real sample literals if we
    have them, else a `[format: <pattern>]` shape hint when the profiler
    detected a pattern but the values are absent or gated. Empty when neither.
    """
    sample_sfx = column_sample_suffix(col.column_name, samples)
    if sample_sfx:
        return sample_sfx
    if col.pattern:
        return f" [format: {col.pattern}]"
    return ""


# ── Lookup helpers ────────────────────────────────────────────────


def _lookup_bound_columns(
    lookups: List[dict], datasets: List[DatasetMeta]
) -> set:
    """Return a set of (trino_path, column_name) pairs that are lookup-bound.

    A column is lookup-bound if:
      - an explicit lookup names its (column_trino_path, column_name), OR
      - a semantic_type-based lookup exists and the column's semantic_type matches.

    Lookup-bound columns suppress their inline sample literals in the prompt
    because showing real coded values invites the model to guess a literal
    instead of using the required subquery pattern.
    """
    if not lookups:
        return set()

    bound: set = set()
    semantic_lookups = [lk for lk in lookups if lk.get("semantic_type")]

    for lk in lookups:
        if lk.get("column_trino_path") and lk.get("column_name"):
            bound.add((lk["column_trino_path"], lk["column_name"]))

    if semantic_lookups:
        for ds in datasets:
            for col in ds.columns:
                if col.semantic_type:
                    for lk in semantic_lookups:
                        if col.semantic_type == lk["semantic_type"]:
                            bound.add((ds.trino_path, col.column_name))
    return bound


def _render_value_lookups(
    lookups: List[dict], datasets: List[DatasetMeta]
) -> str:
    """Render the "Coded-Column Lookups" prompt block.

    Each lookup binds a coded column to a Trino-reachable reference table.
    semantic_type-based lookups are expanded against all matching columns in
    the current dataset bundle so the model sees every concrete binding.

    The rendered block tells the model to NEVER guess a literal for these
    columns — always use a subquery against the reference table.
    """
    if not lookups:
        return ""

    # Expand semantic_type bindings into concrete (trino_path, column_name) pairs.
    expanded: list[dict] = []
    for lk in lookups:
        if lk.get("column_trino_path") and lk.get("column_name"):
            # Explicit binding — render as-is.
            expanded.append(lk)
        if lk.get("semantic_type"):
            # Auto-bind: find every column in the bundle with this semantic_type.
            for ds in datasets:
                for col in ds.columns:
                    if col.semantic_type == lk["semantic_type"]:
                        expanded.append({
                            **lk,
                            "column_trino_path": ds.trino_path,
                            "column_name": col.column_name,
                            "_auto": True,
                        })

    if not expanded:
        return ""

    lines = [
        "## Coded-Column Lookups\n",
        "These columns store CODES, not human-readable values. NEVER guess a literal "
        "for them — always resolve the user's term via a subquery against the reference "
        "table. If the term cannot be resolved, lower your confidence or ask the user "
        "to clarify (kind: unresolved_value).\n",
    ]
    for entry in expanded:
        col_path = f"{entry['column_trino_path']}.{entry['column_name']}"
        lookup_path = entry["lookup_trino_path"]
        key_col = entry["key_column"]
        match_cols = entry.get("match_columns") or []
        desc = entry.get("description") or ""

        match_clause = " OR ".join(
            f"lower({mc}) = lower('<term>')" for mc in match_cols
        ) if match_cols else f"lower({key_col}) = lower('<term>')"

        lines.append(
            f"- {col_path} → lookup {lookup_path}\n"
            f"  Pattern: WHERE {entry['column_name']} IN "
            f"(SELECT {key_col} FROM {lookup_path} WHERE {match_clause})"
        )
        if desc:
            lines[-1] += f"\n  — {desc}"

    return "\n".join(lines)


# ── Fast-path renderers (absorbed from prompt_builder.py) ─────────
def _render_schema_sections(
    datasets: List[DatasetMeta],
    lookup_bound: Optional[set] = None,
) -> str:
    """One block per registered dataset: description, source, Trino path, columns.

    `lookup_bound` is a set of (trino_path, column_name) pairs whose inline
    sample literals are suppressed — showing coded values invites the model to
    guess a literal instead of using the required subquery.
    """
    lookup_bound = lookup_bound or set()
    schema_sections = []
    for ds in datasets:
        col_lines = []
        for col in ds.columns:
            # Nested ROW/ARRAY(ROW) columns expand into explicit dotted paths and
            # UNNEST recipes instead of an opaque type blob the model can't navigate.
            # Sample values (real stored values) are attached to each leaf.
            is_bound = (ds.trino_path, col.column_name) in lookup_bound
            samples = {} if is_bound else parse_samples(col.sample_values)
            type_summary, nested = describe_column(
                col.column_name, col.data_type, samples, max_leaves=20
            )
            line = f"  - {col.column_name} ({type_summary})"
            if col.description:
                line += f": {col.description}"
            if is_bound:
                line += " [LOOKUP-BOUND]"
            else:
                line += _column_hint_suffix(col, samples)
            if col.is_joinable:
                line += " [JOIN KEY]"
            col_lines.append(line)
            col_lines.extend(nested)

        cols_str = "\n".join(col_lines) if col_lines else "  (no column metadata registered)"
        section = (
            f"### {ds.name}\n"
            f"Description: {ds.description}\n"
            f"Source type: {ds.source_type}\n"
            f"Trino reference: {ds.trino_path}\n"
            f"Columns:\n{cols_str}"
        )
        # Exclude lookup-bound columns from categorical values block
        vals_block = categorical_values_block(
            (c.column_name, c.sample_values)
            for c in ds.columns
            if (ds.trino_path, c.column_name) not in lookup_bound
        )
        if vals_block:
            section += f"\n{vals_block}"
        schema_sections.append(section)

    return "\n\n".join(schema_sections) if schema_sections else "(no datasets registered)"


def _render_relationships(relationships: Optional[List[dict]]) -> str:
    """Render the curated join map from postgres-meta.table_relationships.

    Each row already carries the exact join columns and (for cross-source joins)
    the CAST expression that reconciles a type mismatch, so the model never has
    to guess a join key or invent a cast.
    """
    if not relationships:
        return (
            "No curated relationships are registered. Infer joins from columns marked "
            "[JOIN KEY] and from matching names (e.g. an `*_id` column referencing another "
            "table's `id`). When joining ACROSS sources, the same logical id may have "
            "different types (INTEGER vs VARCHAR) — CAST explicitly to reconcile them."
        )

    lines = [
        "Use these known join relationships to pick join keys. When a cast_expression is "
        "given, use it verbatim — it reconciles a cross-source type mismatch:"
    ]
    for r in relationships:
        frm = f"{r.get('from_trino_path', '')}.{r.get('from_column', '')}"
        to = f"{r.get('to_trino_path', '')}.{r.get('to_column', '')}"
        join_type = (r.get("join_type") or "INNER").upper()
        line = f"- {frm} = {to}  ({join_type} JOIN)"
        if r.get("cast_expression"):
            line += f"  [cast: {r['cast_expression']}]"
        if r.get("description"):
            line += f"  — {r['description']}"
        lines.append(line)
    return "\n".join(lines)


def _render_examples(examples: Optional[List[dict]]) -> str:
    """Render few-shot examples from recent SUCCESSFUL queries (audit_logs).

    These are dynamic: they reflect what has actually worked against the
    currently-registered sources and improve as the platform is used. A fresh
    deployment simply has none — the schema + relationships + Trino rules carry
    it until real query history accumulates.
    """
    usable = [
        e for e in (examples or [])
        if (e.get("question") and (e.get("sql") or e.get("sql_executed")))
    ]
    if not usable:
        return ""

    blocks = ["## Proven Query Examples (from this platform's successful query history)\n"
              "These queries ran successfully against the CURRENT data sources — follow their patterns:\n"]
    for e in usable:
        sql = (e.get("sql") or e.get("sql_executed") or "").strip()
        blocks.append(f"---\nQ: \"{e['question']}\"\nSQL:\n{sql}")
    blocks.append("---")
    return "\n".join(blocks)


def render_fast_path_system_prompt(bundle: ContextBundle) -> str:
    """Build the fast-path system prompt from a context bundle (live metadata only)."""

    lookup_bound = _lookup_bound_columns(bundle.lookups, bundle.datasets)
    schema_context = _render_schema_sections(bundle.datasets, lookup_bound)
    relationships_context = _render_relationships(bundle.relationships)
    lookups_context = _render_value_lookups(bundle.lookups, bundle.datasets)
    examples_context = _render_examples(bundle.examples)

    examples_section = f"\n\n{examples_context}" if examples_context else ""
    lookups_section = f"\n\n{lookups_context}" if lookups_context else ""

    return f"""You are a query planning assistant for a Federated Analytics Platform.
Your job is to convert natural language questions into structured query plans with valid Trino SQL.

## Available Data Sources

{schema_context}

## Table Relationships

{relationships_context}
{lookups_section}

## Trino SQL Rules

1. Always use fully qualified table names: catalog.schema.table — copy the exact "Trino
   reference" value shown for each dataset above VERBATIM, including any double quotes and
   the exact schema segment. Never strip quotes, never substitute the schema (e.g. do not
   replace a real schema with "default"), never drop the catalog prefix — even if a table
   segment looks unusual.

2. Column names, not just table names, need double-quoting if they contain anything other
   than letters/digits/underscores. Elasticsearch's standard timestamp field is literally
   named "@timestamp" — the "@" is invalid in a bare identifier and Trino will fail to parse
   it. ALWAYS quote it: ORDER BY "@timestamp" DESC, not ORDER BY @timestamp DESC. The same
   applies to any column starting with a special character, and to index/table names with
   hyphens or dots (e.g. elasticsearch.default."orders-2024.01").

3. Only generate SELECT statements (no INSERT, UPDATE, DELETE, DROP, etc.).

4. For cross-source queries, use standard SQL JOINs — Trino federates automatically. When
   joining on an id that differs in type between sources, CAST to reconcile (see the
   relationships above; use the cast_expression when one is given).

5. Field names are case-sensitive — use the exact names listed in the schema above.

6. Use LIMIT for non-aggregated queries (avoid returning millions of rows).

7. Date/time: use Trino functions — date_trunc, date_add, date_diff, current_date, current_timestamp.

8. NUMERIC/DECIMAL columns used in math: CAST(x AS DOUBLE) — e.g. AVG(CAST(col AS DOUBLE)),
   because plain AVG on DECIMAL errors on some Trino versions.

9. Window functions: RANK(), DENSE_RANK(), ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...).
   For "Nth highest / top-N per group", use DENSE_RANK() in a CTE then filter WHERE rank <= N.

10. Percentage/ratio: CAST(numerator AS DOUBLE) / NULLIF(denominator, 0) * 100  (NULLIF avoids
    division by zero).

11. Array columns (tags, skills, etc.): use contains(array_col, 'value'). Do NOT use ARRAY_CONTAINS.

12. Boolean columns: compare with TRUE/FALSE, not 1/0.

13. Arrays of objects — ARRAY(ROW): UNNEST is valid ONLY in the FROM clause via
    `CROSS JOIN UNNEST(column) AS t` — NEVER in the SELECT list or a scalar expression
    (Trino rejects that with "mismatched input 'UNNEST'"). After unnesting, reference fields
    by name (`t.<field>`); Trino auto-names the unnested columns after the row's fields, so
    do not list them (a wrong count fails with "Column alias list has N entries..."). Unnest
    a nested `array(row(...))` field again in a second CROSS JOIN: `CROSS JOIN UNNEST(t.sub) AS s`.

14. ROW (struct) vs ARRAY(ROW): the schema lists nested columns as explicit paths. A ROW is
    read with dot notation exactly as shown (e.g. transactions.kind); an ARRAY(ROW) MUST be
    CROSS JOIN UNNEST-ed (rule 13) before its fields are reachable. Dot-accessing an array
    fails with "Expression X is not of type ROW"; UNNEST-ing a ROW fails with "Cannot unnest
    type: row(...)". Never infer the shape from the column name — trust the type/paths shown.

15. Prefer TOP-LEVEL columns for filters. When a filter value (a status/type/category, e.g.
    "rental") appears in the known values of a TOP-LEVEL scalar column, filter on that column
    directly (e.g. `WHERE kind = 'rental'`). Do NOT reach into a nested ROW/ARRAY path for a
    value that a top-level column already provides, even if the same value also shows up under
    a nested field. NEVER use a positional array subscript to filter (`col[1].field = ...`):
    indexing is not filtering, and the intended row may be at any position — UNNEST the array
    (rule 13) and filter the unnested alias instead.

## Response Format

You MUST respond with a valid JSON object matching this exact schema:
{{
  "question": "<the original question>",
  "sql": "<valid Trino SELECT SQL>",
  "steps": [
    {{
      "step_id": 1,
      "description": "<what this step does>",
      "catalog": "<trino catalog name>",
      "schema_name": "<schema name>",
      "table": "<table name>"
    }}
  ],
  "confidence": <float 0.0 to 1.0>,
  "explanation": "<human-readable explanation of the query plan>"
}}

**Alternatively**, if the question is genuinely ambiguous and you cannot confidently determine
what the user wants, respond with:
{{
  "clarification": {{
    "question": "<your clarifying question>",
    "options": ["<option1>", "<option2>", ...],
    "kind": "<ambiguous_entity|unresolved_value|missing_data|ambiguous>"
  }}
}}
Only use this when the choice would materially change the query. Do NOT clarify trivial
ambiguities — prefer planning with lower confidence and a clear explanation.

## Important

- Use ONLY tables and columns that appear in the "Available Data Sources" section above. If the
  question needs data that isn't registered, set confidence below 0.3 and explain what's missing
  in the explanation — do NOT invent plausible-sounding table or column names.
- confidence should reflect how well the available data matches the question.
- If the question requires a cross-source JOIN, pick the join key from the [JOIN KEY] columns or
  the relationship map above.{examples_section}
"""


def render_fast_path_user_prompt(request) -> str:
    """Build the user message for the fast-path LLM call."""
    conversation_block = ""
    transcript_text = render_transcript(request.messages)
    if transcript_text:
        conversation_block = (
            f"\nConversation so far:\n{transcript_text}\n"
            "If the question above is a follow-up, resolve it against these prior turns "
            "(e.g. reuse/extend the previous query's tables, filters, and grouping). "
            "If it is self-contained, ignore them.\n"
        )
    return f"""Convert the following question into a Trino SQL query plan:

Question: {request.question}
{conversation_block}
Remember:
- Use fully qualified table names (catalog.schema.table), copied VERBATIM from the "Trino
  reference" shown for each dataset — including any double quotes and the exact schema segment
- Double-quote any column name with special characters (e.g. "@timestamp", never bare @timestamp)
- Only generate a SELECT query
- Use ONLY tables/columns listed in the schema context — never invent names
- For "Nth highest per group" use DENSE_RANK() in a CTE
- Respond with valid JSON only — no markdown, no code blocks, just raw JSON
"""


# ── extra_context renderer (absorbed from main._build_extra_context) ──
def _render_relationships_lines(relationships: List[dict]) -> List[str]:
    """Compact one-line-per-join rendering of curated relationships for extra_context."""
    lines = []
    for r in relationships:
        frm = f"{r.get('from_trino_path', '')}.{r.get('from_column', '')}"
        to = f"{r.get('to_trino_path', '')}.{r.get('to_column', '')}"
        join_type = (r.get("join_type") or "INNER").upper()
        line = f"  - {frm} = {to} ({join_type} JOIN)"
        if r.get("cast_expression"):
            line += f" [cast: {r['cast_expression']}]"
        if r.get("description"):
            line += f" — {r['description']}"
        lines.append(line)
    return lines


def render_extra_context(bundle: ContextBundle) -> Optional[str]:
    """Render pre-loaded dataset metadata + curated join relationships for a pipeline prompt.

    Used by the full deepagents pipeline, the dashboard designer, and widget
    repair so a run that SKIPS schema-analyst still sees the schema + curated
    join map instead of having to rediscover it. Nothing schema-specific is
    hardcoded here; relationships come pre-loaded on the bundle.
    """
    datasets = bundle.datasets
    if not datasets:
        return None
    lookup_bound = _lookup_bound_columns(bundle.lookups, bundle.datasets)
    schema_lines = []
    for ds in datasets:
        schema_lines.append(f"Dataset: {ds.name} ({ds.trino_path})")
        schema_lines.append(f"  Description: {ds.description}")
        for col in ds.columns[:40]:  # generous cap — a missing column invites the LLM to invent one
            # Deeply-nested ROW/ARRAY(ROW) columns are flattened into explicit
            # dotted paths + UNNEST recipes so the model navigates them correctly
            # instead of guessing at the opaque type string. Sample values (real
            # stored values) are attached per leaf so filters use actual literals.
            is_bound = (ds.trino_path, col.column_name) in lookup_bound
            samples = {} if is_bound else parse_samples(col.sample_values)
            type_summary, nested = describe_column(col.column_name, col.data_type, samples, max_leaves=24)
            desc = f": {col.description}" if col.description else ""
            hint = " [LOOKUP-BOUND]" if is_bound else _column_hint_suffix(col, samples)
            schema_lines.append(
                f"  - {col.column_name} ({type_summary}){desc}{hint}"
            )
            schema_lines.extend(nested)
        # Truncation-proof list of real values for nested categorical leaves.
        # Exclude lookup-bound columns — their samples invite literal guessing.
        vals_block = categorical_values_block(
            (c.column_name, c.sample_values)
            for c in ds.columns
            if (ds.trino_path, c.column_name) not in lookup_bound
        )
        if vals_block:
            schema_lines.append("  " + vals_block.replace("\n", "\n  "))

    parts = ["Pre-loaded schema context:\n" + "\n".join(schema_lines)]
    if bundle.relationships:
        parts.append(
            "Known join relationships:\n" + "\n".join(_render_relationships_lines(bundle.relationships))
        )
    lookups_block = _render_value_lookups(bundle.lookups, bundle.datasets)
    if lookups_block:
        parts.append(lookups_block)
    return "\n\n".join(parts)


# ── Structured transcript renderer (PR4) ──────────────────────

# Max characters of SQL to include per assistant message in the rendered
# transcript. Matches the Go-side maxSQLCharsPerMessage constant.
_MAX_SQL_CHARS = 2000


def render_transcript(messages: list, max_chars: int = 8000) -> str:
    """Render structured ChatMessage objects into prompt text.

    Produces an oldest-first text block like:

        [user] What are the top products?
        [assistant → SQL] SELECT … (returned 42 rows)
        [user] Break that down by region
        [assistant → SQL] SELECT … (returned 108 rows)

    For clarification turns (PR5+):

        [assistant asked] Which region do you mean?
        [user answered] Europe

    SQL is truncated per-message at ~2000 chars. Total output is capped at
    `max_chars`, dropping the oldest lines first and inserting an omission
    marker.

    This is a pure function with no side effects — fully unit-testable.
    Render-to-text (not native chat messages) because both consumption paths
    (fast-path user prompt and full-pipeline extra_context) are
    single-message-shaped; native history is a later optimization.
    """
    if not messages:
        return ""

    lines: list[str] = []
    for msg in messages:
        role = getattr(msg, "role", msg.get("role", "")) if isinstance(msg, dict) else msg.role
        kind = getattr(msg, "kind", msg.get("kind", "")) if isinstance(msg, dict) else msg.kind
        content = getattr(msg, "content", msg.get("content", "")) if isinstance(msg, dict) else msg.content
        payload = getattr(msg, "payload", msg.get("payload")) if isinstance(msg, dict) else msg.payload

        if role == "user":
            if kind == "answer":
                lines.append(f"[user answered] {content}")
            else:
                lines.append(f"[user] {content}")
        elif role == "assistant":
            if kind == "plan":
                # Extract SQL from payload if available, fall back to content.
                sql_text = ""
                row_count = 0
                if isinstance(payload, dict):
                    sql_text = payload.get("sql", "")
                    row_count = payload.get("row_count", 0)
                if sql_text:
                    display = sql_text[:_MAX_SQL_CHARS]
                    if len(sql_text) > _MAX_SQL_CHARS:
                        display += "…"
                    line = f"[assistant → SQL] {display}"
                    if row_count:
                        line += f" (returned {row_count} rows)"
                    lines.append(line)
                else:
                    lines.append(f"[assistant → SQL] {content}")
            elif kind == "clarification":
                lines.append(f"[assistant asked] {content}")
            else:
                lines.append(f"[assistant] {content}")

    if not lines:
        return ""

    # Join and enforce total character cap.
    result = "\n".join(lines)
    if len(result) <= max_chars:
        return result

    # Drop from the oldest end until within budget, adding an omission marker.
    while lines and len("\n".join(lines)) > max_chars - 40:  # reserve space for marker
        lines.pop(0)
    omitted = len(messages) - len(lines)
    if omitted > 0:
        lines.insert(0, f"[...{omitted} earlier messages omitted...]")
    return "\n".join(lines)


