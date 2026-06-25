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

-- 3. Drop all tables (explicitly qualifying both public and ag_catalog schemas)
DROP TABLE IF EXISTS public.audit_logs CASCADE;
DROP TABLE IF EXISTS public.eval_cursor CASCADE;
DROP TABLE IF EXISTS public.live_evaluation_cache CASCADE;
DROP TABLE IF EXISTS public.moderation_queue CASCADE;
DROP TABLE IF EXISTS public.evaluation_logs CASCADE;
DROP TABLE IF EXISTS public.rag_query_logs CASCADE;
DROP TABLE IF EXISTS public.document_chunks CASCADE;
DROP TABLE IF EXISTS public.documents CASCADE;
DROP TABLE IF EXISTS public.domain_configs CASCADE;
DROP TABLE IF EXISTS public.domain_roles CASCADE;
DROP TABLE IF EXISTS public.domains CASCADE;
DROP TABLE IF EXISTS public.users CASCADE;

DROP TABLE IF EXISTS ag_catalog.audit_logs CASCADE;
DROP TABLE IF EXISTS ag_catalog.eval_cursor CASCADE;
DROP TABLE IF EXISTS ag_catalog.live_evaluation_cache CASCADE;
DROP TABLE IF EXISTS ag_catalog.moderation_queue CASCADE;
DROP TABLE IF EXISTS ag_catalog.evaluation_logs CASCADE;
DROP TABLE IF EXISTS ag_catalog.rag_query_logs CASCADE;
DROP TABLE IF EXISTS ag_catalog.document_chunks CASCADE;
DROP TABLE IF EXISTS ag_catalog.documents CASCADE;
DROP TABLE IF EXISTS ag_catalog.domain_configs CASCADE;
DROP TABLE IF EXISTS ag_catalog.domain_roles CASCADE;
DROP TABLE IF EXISTS ag_catalog.domains CASCADE;
DROP TABLE IF EXISTS ag_catalog.users CASCADE;

-- 4. Drop custom enums
DROP TYPE IF EXISTS domain_status CASCADE;
DROP TYPE IF EXISTS domain_role_enum CASCADE;

-- 5. Drop the AGE extension completely to ensure a clean slate
DROP EXTENSION IF EXISTS age CASCADE;

-- ═══════════════════════════════════════════════════════════════
-- DONE! The database is completely cleared.
-- ═══════════════════════════════════════════════════════════════
