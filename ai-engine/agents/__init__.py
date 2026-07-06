"""
Agents package for the Federated Analytics Platform AI Engine.

Architecture:
    Orchestrator (deepagent)
      ├── schema-analyst subagent   → identifies relevant tables from all registered sources
      ├── sql-generator subagent    → writes Trino-compatible SQL
      └── sql-validator subagent    → validates safety and syntax
"""
