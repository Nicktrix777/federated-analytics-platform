"""
Validation Tools — deterministic SQL safety and Trino-compatibility checks.

There is intentionally NO LLM involved here. The checklist (SELECT-only,
forbidden keywords, ARRAY_CONTAINS auto-fix, default LIMIT) is deterministic,
so it runs as plain Python on every generated plan instead of costing model
round-trips — this replaced the old "sql-validator" subagent.
"""

import re


def _check_trino_patterns(sql: str) -> list[str]:
    """Internal helper for common Trino issues."""
    issues = []

    if "ARRAY_CONTAINS" in sql.upper():
        issues.append("WARNING: Use contains(col, val) instead of ARRAY_CONTAINS in Trino")

    if re.search(r'\bSHOW\s+TABLES\b', sql, re.IGNORECASE):
        issues.append("WARNING: SHOW TABLES is not valid in this context. Use information_schema.tables instead.")

    return issues


def validate_and_fix_sql(sql: str) -> dict:
    """
    Deterministic safety + auto-fix gate for any generated SQL plan.

    Both the fast single-shot path and the full deepagents pipeline run
    their output through this before it's returned to the Core API.

    Returns:
        {
          "is_valid": bool,
          "fixed_sql": str,
          "issues": list[str],
          "confidence_adjustment": float,  # <= 0.0, apply to the plan's confidence
        }
    """
    normalized = sql.strip().upper()

    if not (normalized.startswith("SELECT") or normalized.startswith("WITH")):
        return {
            "is_valid": False,
            "fixed_sql": sql,
            "issues": [f"SQL must start with SELECT or WITH (CTE). Got: {normalized[:50]}"],
            "confidence_adjustment": -0.3,
        }

    forbidden = [
        "INSERT ", "UPDATE ", "DELETE ", "DROP ", "TRUNCATE ",
        "ALTER ", "CREATE ", "GRANT ", "REVOKE ", "EXECUTE ",
    ]
    forbidden_hits = [f"Forbidden keyword found: {kw.strip()}" for kw in forbidden if kw in normalized]
    if forbidden_hits:
        return {
            "is_valid": False,
            "fixed_sql": sql,
            "issues": forbidden_hits,
            "confidence_adjustment": -0.3,
        }

    if ";" in sql.strip().rstrip(";"):
        return {
            "is_valid": False,
            "fixed_sql": sql,
            "issues": ["Multiple semicolon-separated statements are not allowed"],
            "confidence_adjustment": -0.3,
        }

    issues = list(_check_trino_patterns(sql))
    # Trino's REST API rejects trailing semicolons ("mismatched input ';'")
    fixed_sql = sql.strip().rstrip(";").rstrip()
    fixed_sql = re.sub(r"\bARRAY_CONTAINS\s*\(", "contains(", fixed_sql, flags=re.IGNORECASE)

    fixed_normalized = fixed_sql.upper()
    is_aggregation = any(
        kw in fixed_normalized for kw in ("COUNT(", "SUM(", "AVG(", "GROUP BY", "MIN(", "MAX(")
    )
    if "LIMIT" not in fixed_normalized and not is_aggregation:
        fixed_sql = fixed_sql.rstrip().rstrip(";") + "\nLIMIT 1000"
        issues.append("INFO: Added default LIMIT 1000 (none was specified)")

    confidence_adjustment = -0.1 if any(i.startswith("WARNING") for i in issues) else 0.0

    return {
        "is_valid": True,
        "fixed_sql": fixed_sql,
        "issues": issues,
        "confidence_adjustment": confidence_adjustment,
    }
