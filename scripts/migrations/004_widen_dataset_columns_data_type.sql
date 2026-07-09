-- ======================================================
-- Migration 004 — widen dataset_columns.data_type
--
-- Elasticsearch mappings with nested objects produce Trino ROW(...) type
-- strings that easily exceed VARCHAR(100) (e.g. contracts-v2.40's `broker`,
-- `charges`, `invoicelines`, etc.), silently dropping those columns from the
-- curated schema fed to the AI. TEXT removes the limit — column type
-- strings have no natural bound once nested rows are involved.
--
-- Idempotent: safe to run multiple times.
-- ======================================================

ALTER TABLE dataset_columns ALTER COLUMN data_type TYPE TEXT;
