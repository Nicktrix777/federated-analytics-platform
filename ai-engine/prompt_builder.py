"""
Prompt builder for the AI Engine.

Constructs structured system and user prompts from dataset metadata.
The richer the metadata in postgres-meta, the better the AI output.

Key improvements over v1:
- Explicit relationship/FK mapping between tables and cross-source joins
- Few-shot SQL examples covering hard query patterns (window functions, CTEs, cross-source JOINs)
- Trino-specific syntax gotchas to prevent common LLM mistakes
- Relationship context that the LLM uses to pick join keys automatically
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

## Table Relationships

These are the known relationships between tables — use them to determine join keys:

SAME-SOURCE JOINS (PostgreSQL):
- employees.department_id → departments.id  (FK, always available for dept lookups)
- employees.employee_id → performance_reviews.employee_id  (one employee, many reviews)
- employees.employee_id → employees.manager_id  (self-join for org hierarchy)

CROSS-SOURCE JOINS (PostgreSQL ↔ MongoDB — Trino federates automatically):
- postgres_source.public.employees.employee_id = mongodb.employee_db.tasks.employee_id
- postgres_source.public.employees.employee_id = mongodb.employee_db.employee_profiles.employee_id

JOIN KEY RULE: employee_id is an INTEGER in PostgreSQL but may be a VARCHAR in MongoDB collections like employee_profiles.
You MUST ALWAYS cast the MongoDB side when joining on employee_id to avoid type mismatches:
e.employee_id = CAST(ep.employee_id AS INTEGER)

## Trino SQL Rules

1. Always use fully qualified table names: catalog.schema.table
   - PostgreSQL tables: postgres_source.public.table_name
   - MongoDB collections: mongodb.employee_db.collection_name  (schema is "employee_db", NOT "default")

2. Only generate SELECT statements (no INSERT, UPDATE, DELETE, DROP, etc.)

3. For cross-source queries, use standard SQL JOINs — Trino handles federation automatically.

4. MongoDB field names are case-sensitive. Use exact names as listed above.

5. Use LIMIT clauses when appropriate (avoid returning millions of rows).

6. For date arithmetic, use Trino date functions: date_trunc, date_add, current_date, etc.

7. For string operations, use Trino functions: LOWER, UPPER, CONCAT, LIKE, etc.

8. NUMERIC/DECIMAL columns (like salary, score): use CAST(x AS DOUBLE) if doing math.

9. For window functions use: RANK(), DENSE_RANK(), ROW_NUMBER(), OVER (PARTITION BY ... ORDER BY ...)

10. For NTH item per group, use DENSE_RANK() in a CTE then filter WHERE rank = N.

11. For percentage/ratio calculations: CAST(numerator AS DOUBLE) / NULLIF(denominator, 0) * 100

12. Array columns (skills, tags): use contains(array_col, 'value') to check if an item exists. Do NOT use ARRAY_CONTAINS.

## Trino-Specific Gotchas

- MongoDB schema name is "employee_db" — NEVER use "default" for MongoDB tables
- Boolean columns (goals_met): compare with TRUE/FALSE, not 1/0
- TIMESTAMP columns from MongoDB: use AT TIME ZONE or cast if needed
- NULL handling: use COALESCE(col, 0) for numeric aggregations, IS NULL / IS NOT NULL for checks
- For DECIMAL salary field: AVG(CAST(salary AS DOUBLE)) works; plain AVG(salary) may error on some Trino versions

## Few-Shot Examples

The following examples show correct Trino SQL for various question types. Use these patterns:

---
Q: "What is the average salary per department?"
SQL:
SELECT department, ROUND(AVG(CAST(salary AS DOUBLE)), 2) AS avg_salary_inr
FROM postgres_source.public.employees
GROUP BY department
ORDER BY avg_salary_inr DESC

---
Q: "Show total tasks per department with completion rate"
SQL:
SELECT
  e.department,
  COUNT(t.task_id) AS total_tasks,
  COUNT(CASE WHEN t.status = 'completed' THEN 1 END) AS completed_tasks,
  ROUND(
    CAST(COUNT(CASE WHEN t.status = 'completed' THEN 1 END) AS DOUBLE)
    / NULLIF(COUNT(t.task_id), 0) * 100,
    1
  ) AS completion_rate_pct
FROM postgres_source.public.employees e
JOIN mongodb.employee_db.tasks t ON CAST(t.employee_id AS INTEGER) = e.employee_id
GROUP BY e.department
ORDER BY completion_rate_pct DESC

---
Q: "Which employees have the highest performance scores and how many open tasks do they have?"
SQL:
SELECT
  e.first_name,
  e.last_name,
  e.department,
  ROUND(AVG(CAST(pr.score AS DOUBLE)), 2) AS avg_review_score,
  COUNT(CASE WHEN t.status IN ('open', 'in_progress') THEN 1 END) AS open_tasks
FROM postgres_source.public.employees e
JOIN postgres_source.public.performance_reviews pr ON pr.employee_id = e.employee_id
LEFT JOIN mongodb.employee_db.tasks t ON CAST(t.employee_id AS INTEGER) = e.employee_id
GROUP BY e.employee_id, e.first_name, e.last_name, e.department
ORDER BY avg_review_score DESC
LIMIT 20

---
Q: "Who are the top 3 highest paid employees in each department?" (or "Nth highest salary per department")
SQL:
WITH ranked AS (
  SELECT
    first_name,
    last_name,
    department,
    salary,
    DENSE_RANK() OVER (PARTITION BY department ORDER BY salary DESC) AS salary_rank
  FROM postgres_source.public.employees
  WHERE status = 'active'
)
SELECT first_name, last_name, department, salary, salary_rank
FROM ranked
WHERE salary_rank <= 3
ORDER BY department, salary_rank

---
Q: "Show employees hired in the last 2 years with their task count and average performance score"
SQL:
WITH recent_hires AS (
  SELECT employee_id, first_name, last_name, department, hire_date
  FROM postgres_source.public.employees
  WHERE hire_date >= date_add('year', -2, current_date)
    AND status = 'active'
),
task_counts AS (
  SELECT employee_id, COUNT(*) AS task_count
  FROM mongodb.employee_db.tasks
  GROUP BY employee_id
),
avg_scores AS (
  SELECT employee_id, ROUND(AVG(CAST(score AS DOUBLE)), 2) AS avg_score
  FROM postgres_source.public.performance_reviews
  GROUP BY employee_id
)
SELECT
  rh.first_name,
  rh.last_name,
  rh.department,
  rh.hire_date,
  COALESCE(tc.task_count, 0) AS task_count,
  COALESCE(s.avg_score, 0.0) AS avg_performance_score
FROM recent_hires rh
LEFT JOIN task_counts tc ON CAST(tc.employee_id AS INTEGER) = rh.employee_id
LEFT JOIN avg_scores s ON s.employee_id = rh.employee_id
ORDER BY rh.hire_date DESC

---
Q: "What percentage of employees in each department prefer remote work?"
SQL:
SELECT
  e.department,
  COUNT(*) AS total_employees,
  COUNT(CASE WHEN ep.remote_preference = 'fully_remote' THEN 1 END) AS fully_remote,
  ROUND(
    CAST(COUNT(CASE WHEN ep.remote_preference IN ('fully_remote', 'hybrid_2_days', 'hybrid_3_days') THEN 1 END) AS DOUBLE)
    / NULLIF(COUNT(*), 0) * 100,
    1
  ) AS remote_or_hybrid_pct
FROM postgres_source.public.employees e
LEFT JOIN mongodb.employee_db.employee_profiles ep ON CAST(ep.employee_id AS INTEGER) = e.employee_id
GROUP BY e.department
ORDER BY remote_or_hybrid_pct DESC

---
Q: "Show headcount and average salary per department location"
SQL:
SELECT
  d.location,
  d.name AS department,
  d.headcount,
  ROUND(AVG(CAST(e.salary AS DOUBLE)), 0) AS avg_salary_inr
FROM postgres_source.public.departments d
JOIN postgres_source.public.employees e ON e.department_id = d.id
WHERE e.status = 'active'
GROUP BY d.location, d.name, d.headcount
ORDER BY d.location, avg_salary_inr DESC

---
Q: "Which projects have the most overdue tasks and which department owns them?"
SQL:
SELECT
  t.project,
  e.department,
  COUNT(*) AS overdue_task_count,
  COUNT(DISTINCT t.employee_id) AS employees_affected
FROM mongodb.employee_db.tasks t
JOIN postgres_source.public.employees e ON e.employee_id = CAST(t.employee_id AS INTEGER)
WHERE t.status != 'completed'
  AND t.due_date < current_timestamp
GROUP BY t.project, e.department
ORDER BY overdue_task_count DESC
LIMIT 15

---
Q: "List all employees that know Elasticsearch"
SQL:
SELECT
  e.first_name,
  e.last_name,
  e.department,
  t.tags
FROM postgres_source.public.employees e
JOIN mongodb.employee_db.tasks t ON CAST(t.employee_id AS INTEGER) = e.employee_id
WHERE contains(t.tags, 'Elasticsearch')

---

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
- If the question requires a cross-source JOIN, identify the join key from the [JOIN KEY] marked columns or the relationship map above.
- The confidence score should reflect how well the available data matches the question.
- For "Nth highest" or "top N per group" questions, ALWAYS use DENSE_RANK() in a CTE.
- Never use "default" as the MongoDB schema — always use "employee_db".
"""


def build_user_prompt(request: PlanRequest) -> str:
    """Build the user message for the LLM."""
    return f"""Convert the following question into a Trino SQL query plan:

Question: {request.question}

Remember:
- Use fully qualified table names (catalog.schema.table)
- MongoDB schema is "employee_db", never "default"
- Only generate SELECT queries
- For "Nth highest per group" use DENSE_RANK() in a CTE
- Respond with valid JSON only — no markdown, no code blocks, just raw JSON
"""
