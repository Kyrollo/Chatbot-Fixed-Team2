-- ═══════════════════════════════════════════════════════════════
-- Sprint 2 Migration — source_type column + chunk_index type fix
-- ═══════════════════════════════════════════════════════════════
-- Run against domain_db:
--   $env:PGPASSWORD="postgres_password"
--   psql -U postgres -d domain_db -f migrations/sprint2_migration.sql
-- ═══════════════════════════════════════════════════════════════

-- 1. Add source_type column to track file format provenance (pdf, docx, csv, png, etc.)
ALTER TABLE document_chunks
  ADD COLUMN IF NOT EXISTS source_type VARCHAR(50) DEFAULT 'pdf';

-- 2. Ensure chunk_index is INTEGER type (idempotent — no-op if already correct)
ALTER TABLE document_chunks
  ALTER COLUMN chunk_index TYPE INTEGER;

-- 3. Backfill existing rows that have NULL source_type
UPDATE document_chunks
  SET source_type = 'pdf'
  WHERE source_type IS NULL;

-- ═══════════════════════════════════════════════════════════════
-- DONE! Verify with:
--   \d document_chunks
-- source_type should appear as varchar(50) with default 'pdf'
-- ═══════════════════════════════════════════════════════════════
