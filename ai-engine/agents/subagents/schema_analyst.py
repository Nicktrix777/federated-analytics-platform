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
    list_available_sources,
    get_tables_in_source,
    get_column_details,
    get_source_schema_summary,
)
from agents.tools.metadata_tools import (
    get_dataset_descriptions,
    get_table_relationships,
)
from config import settings
from langchain.chat_models import init_chat_model

# Resolved with the same retry/timeout settings as the orchestrator's main
# model — a bare model string here would fall back to langchain's default
# max_retries (2), undermining the Phase 1 rate-limit resilience work.
_SCHEMA_ANALYST_MODEL = init_chat_model(
    settings.schema_analyst_model,
    max_retries=settings.llm_max_retries,
    timeout=settings.llm_timeout_seconds,
)

SCHEMA_ANALYST_SYSTEM_PROMPT = """You are a Schema Analyst for a Federated Analytics Platform.

Your role is to understand the data landscape across ALL registered data sources
(PostgreSQL databases, MongoDB collections, Elasticsearch indices, and any others)
and provide precise, actionable schema context for query planning.

## Your Workflow

1. **Discover Sources**: Use list_available_sources() to see all registered data connections.

2. **Get Enriched Descriptions**: Use get_dataset_descriptions() first to get any manually 
   curated metadata (descriptions, sample values, known join keys).

3. **Explore Tables**: For each relevant source, use get_source_schema_summary() or
   get_tables_in_source() to see what tables/indices are available.

4. **Get Column Details**: For tables relevant to the question, use get_column_details()
   to understand the exact schema including data types.

5. **Find Relationships**: Use get_table_relationships() to identify how tables connect
   across sources (e.g., PostgreSQL employee_id → Elasticsearch employee document).

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

SCHEMA_ANALYST_SUBAGENT = {
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
    "model": _SCHEMA_ANALYST_MODEL,
    "tools": [
        list_available_sources,
        get_tables_in_source,
        get_column_details,
        get_source_schema_summary,
        get_dataset_descriptions,
        get_table_relationships,
    ],
}
