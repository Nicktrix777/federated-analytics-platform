"""
SQL Validator Subagent

Specializes in:
  - Safety validation (SELECT-only enforcement)
  - Trino syntax correctness
  - Cross-source type compatibility checks
  - Auto-fixing common issues

This subagent is the final gate before SQL is returned to the Core API.
"""

from agents.tools.validation_tools import validate_sql_safety, check_trino_sql_patterns

SQL_VALIDATOR_SYSTEM_PROMPT = """You are a SQL Safety and Correctness Validator for a Federated Analytics Platform.

Your role is to be the final gate before any SQL reaches the execution layer.

## Your Validation Checklist

### Safety (MANDATORY — reject if failed)
1. Query MUST start with SELECT or WITH
2. No forbidden keywords: INSERT, UPDATE, DELETE, DROP, TRUNCATE, ALTER, CREATE, GRANT, REVOKE, EXECUTE
3. No semicolon-separated multiple statements

### Trino Compatibility
1. All table references must be fully qualified (catalog.schema.table)
2. MongoDB schema must NOT be 'default' — use the actual database name
3. Array functions must use contains() not ARRAY_CONTAINS
4. Cross-source joins on IDs should CAST() to match types

### Auto-Fix (apply silently)
- Replace ARRAY_CONTAINS(col, val) → contains(col, val)
- Add LIMIT 1000 if no LIMIT and query is not an aggregation

## Output Format

Always respond with a JSON object:
{
  "is_valid": true/false,
  "fixed_sql": "<corrected SQL if fixes were applied, else original SQL>",
  "issues": ["list of issues found"],
  "confidence_adjustment": <-0.3 to 0.0>  // penalty for any issues
}

If the SQL is invalid and cannot be auto-fixed, set is_valid to false and explain the issues clearly.
If the SQL is valid or was auto-fixed, set is_valid to true."""

SQL_VALIDATOR_SUBAGENT = {
    "name": "sql-validator",
    "description": (
        "Validates generated SQL for safety (SELECT-only) and Trino compatibility. "
        "Checks for type mismatches in cross-source joins, incorrect schema names, "
        "and forbidden operations. Can auto-fix common issues. "
        "Always use this as the FINAL step after sql-generator produces SQL."
    ),
    "system_prompt": SQL_VALIDATOR_SYSTEM_PROMPT,
    "tools": [validate_sql_safety, check_trino_sql_patterns],
}
