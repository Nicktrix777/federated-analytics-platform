"""
Validation Tools — SQL safety and Trino syntax validation.

These tools are used by the SQL Validator subagent to:
  1. Enforce SELECT-only queries (no DDL or DML)
  2. Check for common Trino syntax issues
  3. Validate that catalog/schema/table references look correct
"""

import re
import json
from langchain_core.tools import tool


@tool
def validate_sql_safety(sql: str) -> str:
    """
    Validate that a SQL query is safe to execute.
    Checks for:
      - SELECT-only enforcement (no INSERT, UPDATE, DELETE, DROP, etc.)
      - Forbidden keywords
      - Basic Trino syntax patterns
    
    Returns a JSON object with:
      - is_valid: boolean
      - issues: list of found issues
      - fixed_sql: corrected SQL if auto-fixable, else null
    
    Args:
        sql: The SQL string to validate
    """
    issues = []
    fixed_sql = sql.strip()

    normalized = sql.strip().upper()

    # Must start with SELECT or WITH
    if not (normalized.startswith("SELECT") or normalized.startswith("WITH")):
        issues.append(f"SQL must start with SELECT or WITH (CTE). Got: {normalized[:50]}")
        return json.dumps({"is_valid": False, "issues": issues, "fixed_sql": None})

    # Forbidden keywords
    forbidden = [
        "INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE ",
        "ALTER ", "CREATE ", "GRANT ", "REVOKE ", "EXECUTE ",
    ]
    for kw in forbidden:
        if kw in normalized:
            issues.append(f"Forbidden keyword found: {kw.strip()}")

    # Trino-specific checks
    trino_issues = _check_trino_patterns(sql)
    issues.extend(trino_issues)

    if issues:
        return json.dumps({
            "is_valid": len([i for i in issues if "WARNING" not in i]) == 0,
            "issues": issues,
            "fixed_sql": None
        })

    return json.dumps({"is_valid": True, "issues": [], "fixed_sql": fixed_sql})


@tool
def check_trino_sql_patterns(sql: str) -> str:
    """
    Check SQL for common Trino-specific issues and suggest fixes.
    Returns a JSON object with pattern analysis and suggested corrections.
    
    Common issues detected:
      - MongoDB: using 'default' schema instead of actual schema name
      - Missing fully-qualified table names (catalog.schema.table)
      - Type mismatch patterns in cross-source JOINs
      - Missing CAST for cross-source join keys
    
    Args:
        sql: The SQL to check
    """
    suggestions = []
    fixed = sql

    # Check for 'mongodb.default.' pattern
    if re.search(r'mongodb\.default\.', sql, re.IGNORECASE):
        suggestions.append({
            "issue": "MongoDB schema should not be 'default'. Use the actual database name (e.g., 'employee_db').",
            "severity": "WARNING",
        })

    # Check for missing CAST in cross-source joins
    if "mongodb" in sql.lower() and "= ep.employee_id" in sql.lower():
        if "CAST" not in sql.upper():
            suggestions.append({
                "issue": "Cross-source join on employee_id may need CAST. MongoDB employee_id may be VARCHAR while PostgreSQL is INTEGER. Use: CAST(ep.employee_id AS INTEGER).",
                "severity": "WARNING",
            })

    # Check for non-qualified table names
    tables_in_from = re.findall(
        r'\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)\b', sql, re.IGNORECASE
    )
    for t in tables_in_from:
        if "." not in t and t.upper() not in ("SELECT", "WITH", "WHERE", "JOIN"):
            suggestions.append({
                "issue": f"Table '{t}' appears unqualified. Use fully qualified name: catalog.schema.table",
                "severity": "ERROR",
            })

    # Check for array functions
    if re.search(r'\bARRAY_CONTAINS\b', sql, re.IGNORECASE):
        suggestions.append({
            "issue": "Trino uses contains() not ARRAY_CONTAINS(). Replace with: contains(column, 'value')",
            "severity": "ERROR",
        })

    # Check for missing LIMIT on potentially large queries
    if "LIMIT" not in sql.upper() and "COUNT(" not in sql.upper():
        suggestions.append({
            "issue": "Consider adding LIMIT to avoid fetching large result sets.",
            "severity": "INFO",
        })

    return json.dumps({
        "suggestions": suggestions,
        "has_errors": any(s["severity"] == "ERROR" for s in suggestions),
        "has_warnings": any(s["severity"] == "WARNING" for s in suggestions),
    }, indent=2)


def _check_trino_patterns(sql: str) -> list[str]:
    """Internal helper for common Trino issues."""
    issues = []

    if "ARRAY_CONTAINS" in sql.upper():
        issues.append("WARNING: Use contains(col, val) instead of ARRAY_CONTAINS in Trino")

    if re.search(r'\bSHOW\s+TABLES\b', sql, re.IGNORECASE):
        issues.append("WARNING: SHOW TABLES is not valid in this context. Use information_schema.tables instead.")

    return issues
