-- ═══════════════════════════════════════════════════════════════
-- Phase 4 Migration — Ontology Expansion
--   New entity types and relation types for the knowledge graph
-- ═══════════════════════════════════════════════════════════════
-- Run from Windows PowerShell against domain_db on the WSL2 instance:
--   psql -h localhost -p 5434 -U postgres -d domain_db -f sprint4_ontology_expansion.sql
-- ═══════════════════════════════════════════════════════════════

LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- ───────────────────────────────────────────────────────────────
-- New Entity Types (vertex labels) — Phase 4 expansion
-- ───────────────────────────────────────────────────────────────

-- Document: official documents, reports, publications
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Document' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Document');
    END IF;
END $$;

-- Form: tax forms, official forms (W-2, 1099, etc.)
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Form' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Form');
    END IF;
END $$;

-- Organization: companies, corporations, NGOs
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Organization' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Organization');
    END IF;
END $$;

-- Agency: government agencies, regulatory bodies
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Agency' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Agency');
    END IF;
END $$;

-- Regulation: laws, legal rules, regulatory codes
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Regulation' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Regulation');
    END IF;
END $$;

-- TaxTerm: tax/payroll/financial concepts
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'TaxTerm' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'TaxTerm');
    END IF;
END $$;

-- Date: dates and time periods
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Date' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Date');
    END IF;
END $$;

-- Identifier: codes, reference numbers, IDs
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Identifier' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Identifier');
    END IF;
END $$;

-- Requirement: legal or policy requirements
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Requirement' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Requirement');
    END IF;
END $$;

-- Procedure: processes, procedures, workflows
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Procedure' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Procedure');
    END IF;
END $$;


-- ───────────────────────────────────────────────────────────────
-- New Relation Types (edge labels) — Phase 4 expansion
-- ───────────────────────────────────────────────────────────────

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'DEFINED_BY' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'DEFINED_BY');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'REQUIRES' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'REQUIRES');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'APPLIES_TO' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'APPLIES_TO');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'PART_OF' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'PART_OF');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'REFERENCES' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'REFERENCES');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'ISSUED_BY' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'ISSUED_BY');
    END IF;
END $$;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'HAS_SECTION' AND graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')) THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'HAS_SECTION');
    END IF;
END $$;


-- ───────────────────────────────────────────────────────────────
-- domain_id indexes for new vertex labels
-- ───────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS document_domain_id_idx
    ON rag_graph."Document" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS form_domain_id_idx
    ON rag_graph."Form" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS organization_domain_id_idx
    ON rag_graph."Organization" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS agency_domain_id_idx
    ON rag_graph."Agency" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS regulation_domain_id_idx
    ON rag_graph."Regulation" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS taxterm_domain_id_idx
    ON rag_graph."TaxTerm" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS date_domain_id_idx
    ON rag_graph."Date" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS identifier_domain_id_idx
    ON rag_graph."Identifier" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS requirement_domain_id_idx
    ON rag_graph."Requirement" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS procedure_domain_id_idx
    ON rag_graph."Procedure" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));

-- ═══════════════════════════════════════════════════════════════
-- DONE! Verify with:
--   SELECT name, kind FROM ag_catalog.ag_label
--   WHERE graph = (SELECT graphid FROM ag_catalog.ag_graph WHERE name = 'rag_graph')
--   ORDER BY kind, name;
-- ═══════════════════════════════════════════════════════════════
