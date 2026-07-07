"""
Agents package for the Federated Analytics Platform AI Engine.

Architecture:
    Orchestrator (deepagent)
      ├── schema-analyst subagent   → identifies relevant tables from all registered sources
      └── sql-generator subagent    → writes Trino-compatible SQL

SQL safety/Trino validation is deterministic Python
(agents.tools.validation_tools.validate_and_fix_sql), not a subagent.
"""
