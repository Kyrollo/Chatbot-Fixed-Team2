-- ═══════════════════════════════════════════════════════════════
-- DATABASE RESET & CLEANUP SCRIPT
-- ═══════════════════════════════════════════════════════════════
-- This script completely drops all tables, custom types, enums,
-- and the Apache AGE graph schema, resetting the database.
--
-- Run this against the domain_db database:
--   psql -U postgres -d domain_db -f migrations/clear_db.sql
-- ═══════════════════════════════════════════════════════════════

-- 1. Load Apache AGE if present to drop graph cleanly
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- 2. Drop the AGE Graph safely
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'rag_graph') THEN
        PERFORM ag_catalog.drop_graph('rag_graph', true);
    END IF;
END $$;

-- 3. Drop all tables
DROP TABLE IF EXISTS audit_logs CASCADE;
DROP TABLE IF EXISTS eval_cursor CASCADE;
DROP TABLE IF EXISTS live_evaluation_cache CASCADE;
DROP TABLE IF EXISTS moderation_queue CASCADE;
DROP TABLE IF EXISTS evaluation_logs CASCADE;
DROP TABLE IF EXISTS rag_query_logs CASCADE;
DROP TABLE IF EXISTS document_chunks CASCADE;
DROP TABLE IF EXISTS documents CASCADE;
DROP TABLE IF EXISTS domain_configs CASCADE;
DROP TABLE IF EXISTS domain_roles CASCADE;
DROP TABLE IF EXISTS domains CASCADE;
DROP TABLE IF EXISTS users CASCADE;

-- 4. Drop custom enums
DROP TYPE IF EXISTS domain_status CASCADE;
DROP TYPE IF EXISTS domain_role_enum CASCADE;

-- 5. Drop the AGE extension completely to ensure a clean slate
DROP EXTENSION IF EXISTS age CASCADE;

-- ═══════════════════════════════════════════════════════════════
-- DONE! The database is completely cleared.
-- ═══════════════════════════════════════════════════════════════
