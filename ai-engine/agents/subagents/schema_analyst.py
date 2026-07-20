"""
Schema Analyst Subagent

Specializes in:
  - Understanding all available data sources
  - Identifying which tables/indices are relevant to a question
  - Discovering column details and relationships
  - Providing structured schema context for the SQL generator

This agent has isolated context so its schema exploration work
does not bloat the orchestrator's context window.
"""

from agents.tools.schema_tools import (
    get_schema_context,
    get_column_details,
    get_source_schema_summary,
)
from config import settings
from llm.providers import make_langchain_model

SCHEMA_ANALYST_SYSTEM_PROMPT = """You are a Schema Analyst for a Federated Analytics Platform.

Your role is to understand the data landscape across ALL registered data sources
(PostgreSQL databases, MongoDB collections, Elasticsearch indices, and any others)
and provide precise, actionable schema context for query planning.

## Your Workflow — keep it to as FEW tool calls as possible

1. **Call get_schema_context() ONCE.** It returns everything registered on the platform in
   one shot: all data sources, all curated datasets with columns/descriptions/sample values,
   and all known join relationships (including cross-source joins). For most questions this
   is the ONLY tool call you need — go straight to writing your answer from it.

2. **Drill down ONLY if something is missing.** If the question needs a table that has no
   curated dataset entry, use get_source_schema_summary(catalog) for a live listing of that
   source, or get_column_details(trino_path) for one table's exact live schema. Do not
   re-verify tables that get_schema_context already described.

## Output Format

Always respond with a structured schema context containing:
- Which tables are relevant to the question (with fully-qualified Trino paths)
- Key columns and their types
- Join relationships to use
- Any important caveats (type mismatches, naming conventions, etc.)
- Source-specific rules (e.g., MongoDB schema name, Elasticsearch field dot notation)

## Trino Path Format
- PostgreSQL: postgres_source.public.table_name
- MongoDB: mongodb.database_name.collection_name  
- Elasticsearch: elasticsearch.default.index_name

Be thorough but concise. The SQL generator will use your output to write precise SQL."""

def build_schema_analyst_subagent(rate_limiter=None) -> dict:
    """Build the schema-analyst subagent definition.

    A builder (not a module constant) so the model is resolved from the CURRENT
    settings with this tier's rate limiter — lets the deepagents pipeline be
    rebuilt on a live settings change (Part B hot-reload) and share the 'fast'
    tier's RPM cap. Uses the same retry/timeout as the orchestrator's model — a
    bare init_chat_model would fall back to langchain's default max_retries (2),
    undermining the rate-limit resilience work.
    """
    return {
        "name": "schema-analyst",
        "description": (
            "Analyzes the data landscape across all registered sources "
            "(PostgreSQL, MongoDB, Elasticsearch, etc.) and identifies relevant tables, "
            "columns, and join relationships for a given question. "
            "Use this FIRST before generating any SQL."
        ),
        "system_prompt": SCHEMA_ANALYST_SYSTEM_PROMPT,
        # Cheap model — this subagent mostly calls tools and summarizes their
        # output, it doesn't need frontier-model reasoning like sql-generator does.
        "model": make_langchain_model(settings.schema_analyst_model, rate_limiter),
        # Deliberately small tool surface: one consolidated overview tool plus two
        # drill-downs. The old six-tool list, paired with a five-step workflow
        # prompt, made every schema-analyst run cost 5-10 LLM round trips.
        "tools": [
            get_schema_context,
            get_source_schema_summary,
            get_column_details,
        ],
    }
