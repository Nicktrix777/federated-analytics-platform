"""
Prompt builder for the AI Engine.

Constructs structured system and user prompts from dataset metadata.
The richer the metadata in postgres-meta, the better the AI output.
"""

from typing import List
from models import DatasetMeta, PlanRequest


def build_system_prompt(datasets: List[DatasetMeta]) -> str:
    """Build the system prompt with schema context for the LLM."""

    schema_sections = []
    for ds in datasets:
        parts = ds.trino_path.split(".")
        catalog = parts[0] if len(parts) > 0 else "unknown"
        schema = parts[1] if len(parts) > 1 else "public"
        table = parts[2] if len(parts) > 2 else ds.name

        col_lines = []
        for col in ds.columns:
            line = f"  - {col.column_name} ({col.data_type})"
            if col.description:
                line += f": {col.description}"
            if col.sample_values:
                line += f" [e.g., {col.sample_values}]"
            if col.is_joinable:
                line += " [JOIN KEY]"
            col_lines.append(line)

        cols_str = (
            "\n".join(col_lines) if col_lines else "  (no column metadata registered)"
        )

        schema_sections.append(f"""### {ds.name}
Description: {ds.description}
Source type: {ds.source_type}
Trino reference: {catalog}.{schema}.{table}
Columns:
{cols_str}""")

    schema_context = (
        "\n\n".join(schema_sections) if schema_sections else "(no datasets registered)"
    )

    return f"""You are a query planning assistant for a Federated Analytics Platform.
Your job is to convert natural language questions into structured query plans with valid Trino SQL.

## Available Data Sources

{schema_context}

## Trino SQL Rules

1. Always use fully qualified table names: catalog.schema.table
   - PostgreSQL tables: postgres_source.public.table_name
   - MongoDB collections: mongodb.default.collection_name

2. Only generate SELECT statements (no INSERT, UPDATE, DELETE, DROP, etc.)

3. For cross-source queries, use standard SQL JOINs — Trino handles federation automatically.

4. MongoDB field names are case-sensitive. Use exact names as listed above.

5. Use LIMIT clauses when appropriate (avoid returning millions of rows).

6. For date arithmetic, use Trino date functions: date_trunc, date_add, current_date, etc.

7. For string operations, use Trino functions: LOWER, UPPER, CONCAT, LIKE, etc.

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

- If the question cannot be answered from the available datasets, set confidence to 0.1 and explain why in the explanation field.
- If the question requires a cross-source JOIN, identify the join key from the [JOIN KEY] marked columns.
- The confidence score should reflect how well the available data matches the question.
"""


def build_user_prompt(request: PlanRequest) -> str:
    """Build the user message for the LLM."""
    return f"""Convert the following question into a Trino SQL query plan:

Question: {request.question}

Remember:
- Use fully qualified table names (catalog.schema.table)
- Only generate SELECT queries
- Respond with valid JSON only — no markdown, no code blocks, just raw JSON
"""
