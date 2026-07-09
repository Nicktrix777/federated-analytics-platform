-- ======================================================
-- Migration 003 — widen datasets.source_type to match data_sources
--
-- Environments that predate the v2 schema still have the original
-- datasets_source_type_check constraint (postgresql, mongodb, trino only).
-- data_sources already allows 'elasticsearch'/'mysql', but any attempt to
-- register a curated (or auto-discovered) `datasets` row for an
-- Elasticsearch/MySQL source fails the CHECK on those environments —
-- this is what silently blocked describing the Elasticsearch `contracts`
-- indices even after the source itself was registered.
--
-- Idempotent: safe to run multiple times.
-- ======================================================

ALTER TABLE datasets DROP CONSTRAINT IF EXISTS datasets_source_type_check;
ALTER TABLE datasets ADD CONSTRAINT datasets_source_type_check
    CHECK (source_type IN ('postgresql', 'mongodb', 'elasticsearch', 'mysql', 'trino'));
