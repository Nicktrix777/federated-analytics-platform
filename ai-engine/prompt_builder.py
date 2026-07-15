"""
Prompt builder for the AI Engine fast path.

Builds the single-shot system/user prompts entirely from LIVE metadata — the
registered datasets/columns, the curated join relationships in postgres-meta's
table_relationships, and recent successful queries from the audit log. There is
NOTHING schema-specific baked in here: point the platform at a different set of
data sources and the prompt reshapes itself. The only fixed content is
source-agnostic Trino syntax guidance.

`build_system_prompt` is a pure function of (datasets, relationships, examples)
— the caller (agents.main._try_fast_path) fetches those three from the cached
metadata helpers and passes them in.
"""

from typing import List, Optional

from models import DatasetMeta, PlanRequest
from schema_render import (
    categorical_values_block,
    column_sample_suffix,
    describe_column,
    parse_samples,
)


def _render_schema_sections(datasets: List[DatasetMeta]) -> str:
    """One block per registered dataset: description, source, Trino path, columns."""
    schema_sections = []
    for ds in datasets:
        col_lines = []
        for col in ds.columns:
            # Nested ROW/ARRAY(ROW) columns expand into explicit dotted paths and
            # UNNEST recipes instead of an opaque type blob the model can't navigate.
            # Sample values (real stored values) are attached to each leaf.
            samples = parse_samples(col.sample_values)
            type_summary, nested = describe_column(
                col.column_name, col.data_type, samples, max_leaves=20
            )
            line = f"  - {col.column_name} ({type_summary})"
            if col.description:
                line += f": {col.description}"
            line += column_sample_suffix(col.column_name, samples)
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
        vals_block = categorical_values_block((c.column_name, c.sample_values) for c in ds.columns)
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


def build_system_prompt(
    datasets: List[DatasetMeta],
    relationships: Optional[List[dict]] = None,
    examples: Optional[List[dict]] = None,
) -> str:
    """Build the fast-path system prompt from live metadata only."""

    schema_context = _render_schema_sections(datasets)
    relationships_context = _render_relationships(relationships)
    examples_context = _render_examples(examples)

    examples_section = f"\n\n{examples_context}" if examples_context else ""

    return f"""You are a query planning assistant for a Federated Analytics Platform.
Your job is to convert natural language questions into structured query plans with valid Trino SQL.

## Available Data Sources

{schema_context}

## Table Relationships

{relationships_context}

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

## Important

- Use ONLY tables and columns that appear in the "Available Data Sources" section above. If the
  question needs data that isn't registered, set confidence below 0.3 and explain what's missing
  in the explanation — do NOT invent plausible-sounding table or column names.
- confidence should reflect how well the available data matches the question.
- If the question requires a cross-source JOIN, pick the join key from the [JOIN KEY] columns or
  the relationship map above.{examples_section}
"""


def build_user_prompt(request: PlanRequest) -> str:
    """Build the user message for the fast-path LLM call."""
    conversation_block = ""
    if request.conversation_context:
        conversation_block = (
            f"\nConversation so far:\n{request.conversation_context}\n"
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
