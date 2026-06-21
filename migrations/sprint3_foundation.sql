-- ═══════════════════════════════════════════════════════════════
-- Sprint 3 Migration — Task 0.1 + Task 1.1: Foundation
--   Apache AGE Setup + Ontology Definition
-- ═══════════════════════════════════════════════════════════════
-- Prerequisite: PostgreSQL 17 + Apache AGE already running (via WSL2 —
--               see wsl2_setup_v2.sh) and domain_db already created.
--
-- Run from Windows PowerShell against domain_db on the WSL2 instance:
--   psql -h localhost -p 5434 -U postgres -d domain_db -f sprint3_foundation.sql
-- ═══════════════════════════════════════════════════════════════

-- ───────────────────────────────────────────────────────────────
-- TASK 1.1 — Apache AGE Setup
-- ───────────────────────────────────────────────────────────────

-- 1. Load the extension into this database (idempotent)
CREATE EXTENSION IF NOT EXISTS age;

-- 2. Make AGE's catalog functions (cypher(), create_graph(), etc.)
--    visible without schema-qualifying every call.
--    NOTE: this only affects the current session/migration run.
--    Each service connection must also run this once — see
--    services/graph-service/db.py for the runtime equivalent.
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- 3. Create the single graph used by the whole RAG system.
--    All domains share this one graph; isolation between domains
--    is enforced by the mandatory domain_id property on every
--    vertex and edge (see Ontology section below) — not by
--    separate graphs. One graph keeps cross-domain admin queries
--    and backups simple.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'rag_graph'
    ) THEN
        PERFORM ag_catalog.create_graph('rag_graph');
    END IF;
END $$;

-- ───────────────────────────────────────────────────────────────
-- TASK 0.1 — Ontology Definition
-- ───────────────────────────────────────────────────────────────
-- Apache AGE is schema-flexible (vertex/edge labels are created on
-- first use, not declared up front like SQL tables). So "defining"
-- the ontology means two things:
--   a) Documenting the allowed labels (this file, source of truth)
--   b) Enforcing them in code (services/graph-service/ontology.py)
--      and ner-service config — NOT at the database level.
--
-- This migration creates each vertex label explicitly anyway
-- (via create_vlabel) so that:
--   - Indexes can be attached per-label from day one (see below)
--   - `MATCH (n:Person)` fails loudly if NER ever produces a label
--     outside the ontology, instead of silently creating a new one
--
-- Entity Types (vertex labels)
SELECT ag_catalog.create_vlabel('rag_graph', 'Person');
SELECT ag_catalog.create_vlabel('rag_graph', 'Project');
SELECT ag_catalog.create_vlabel('rag_graph', 'Department');
SELECT ag_catalog.create_vlabel('rag_graph', 'Policy');
SELECT ag_catalog.create_vlabel('rag_graph', 'Role');
SELECT ag_catalog.create_vlabel('rag_graph', 'Location');
SELECT ag_catalog.create_vlabel('rag_graph', 'Skill');

-- Relation Types (edge labels)
SELECT ag_catalog.create_elabel('rag_graph', 'MANAGES');
SELECT ag_catalog.create_elabel('rag_graph', 'BELONGS_TO');
SELECT ag_catalog.create_elabel('rag_graph', 'REPORTS_TO');
SELECT ag_catalog.create_elabel('rag_graph', 'OWNS');
SELECT ag_catalog.create_elabel('rag_graph', 'HAS_ROLE');
SELECT ag_catalog.create_elabel('rag_graph', 'WORKS_ON');
SELECT ag_catalog.create_elabel('rag_graph', 'HAS_SKILL');
SELECT ag_catalog.create_elabel('rag_graph', 'BASED_AT');

-- ───────────────────────────────────────────────────────────────
-- domain_id enforcement — RBAC starts here, not in Task 8
-- ───────────────────────────────────────────────────────────────
-- AGE has no CHECK constraints on vertex/edge properties, so
-- domain_id presence is enforced at the application layer
-- (graph_writer.py rejects writes without domain_id). This index
-- makes every domain-scoped MATCH fast from the first row written,
-- which matters because every retrieval-time graph query will
-- filter by domain_id.
--
-- AGE stores vertex/edge properties in a single `properties` agtype
-- column. We index the domain_id key inside that column per label.
CREATE INDEX IF NOT EXISTS person_domain_id_idx
    ON rag_graph."Person" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS project_domain_id_idx
    ON rag_graph."Project" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS department_domain_id_idx
    ON rag_graph."Department" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS policy_domain_id_idx
    ON rag_graph."Policy" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS role_domain_id_idx
    ON rag_graph."Role" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS location_domain_id_idx
    ON rag_graph."Location" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS skill_domain_id_idx
    ON rag_graph."Skill" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));

-- ═══════════════════════════════════════════════════════════════
-- DONE! Verify with:
--   SELECT * FROM ag_catalog.ag_graph;
--   SELECT * FROM ag_catalog.ag_label WHERE graph = 'rag_graph'::regnamespace::oid;
--
-- Smoke test — create one test node and read it back:
--   SELECT * FROM cypher('rag_graph', $$
--       CREATE (p:Person {name: 'test', domain_id: 'smoke-test'})
--       RETURN p
--   $$) AS (p agtype);
--
--   SELECT * FROM cypher('rag_graph', $$
--       MATCH (p:Person {domain_id: 'smoke-test'}) RETURN p
--   $$) AS (p agtype);
--
-- Clean up the smoke test node afterwards:
--   SELECT * FROM cypher('rag_graph', $$
--       MATCH (p:Person {domain_id: 'smoke-test'}) DETACH DELETE p
--   $$) AS (p agtype);
-- ═══════════════════════════════════════════════════════════════
