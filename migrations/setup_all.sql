-- ═══════════════════════════════════════════════════════════════
-- UNIFIED DATABASE INITIALIZATION & SEED SCRIPT
-- ═══════════════════════════════════════════════════════════════
-- This script sets up the entire database for Sprints 1, 2, and 3,
-- including the Apache AGE Graph Extension, relational tables,
-- indexes, constraints, user permissions, and seed data.
--
-- Target Database Server: PostgreSQL 17 + Apache AGE (WSL2 Port 5434)
-- Database Name:          domain_db
-- ═══════════════════════════════════════════════════════════════

-- 1. Load Apache AGE Extension (Requires superuser)
CREATE EXTENSION IF NOT EXISTS age;
LOAD 'age';
SET search_path = ag_catalog, "$user", public;

-- 2. Create the AGE Graph
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM ag_catalog.ag_graph WHERE name = 'rag_graph'
    ) THEN
        PERFORM ag_catalog.create_graph('rag_graph');
    END IF;
END $$;

-- 3. Define Graph Ontology Labels (idempotent DO block)
DO $$
BEGIN
    -- Vertex labels
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Person') THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Person');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Project') THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Project');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Department') THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Department');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Policy') THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Policy');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Role') THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Role');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Location') THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Location');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'Skill') THEN
        PERFORM ag_catalog.create_vlabel('rag_graph', 'Skill');
    END IF;

    -- Edge labels
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'MANAGES') THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'MANAGES');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'BELONGS_TO') THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'BELONGS_TO');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'REPORTS_TO') THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'REPORTS_TO');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'OWNS') THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'OWNS');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'HAS_ROLE') THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'HAS_ROLE');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'WORKS_ON') THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'WORKS_ON');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'HAS_SKILL') THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'HAS_SKILL');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_label WHERE name = 'BASED_AT') THEN
        PERFORM ag_catalog.create_elabel('rag_graph', 'BASED_AT');
    END IF;
END $$;

-- 4. Create Graph Indexes
CREATE INDEX IF NOT EXISTS person_domain_id_idx ON rag_graph."Person" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS project_domain_id_idx ON rag_graph."Project" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS department_domain_id_idx ON rag_graph."Department" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS policy_domain_id_idx ON rag_graph."Policy" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS role_domain_id_idx ON rag_graph."Role" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS location_domain_id_idx ON rag_graph."Location" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));
CREATE INDEX IF NOT EXISTS skill_domain_id_idx ON rag_graph."Skill" (ag_catalog.agtype_access_operator(properties, '"domain_id"'::agtype));

-- 5. Custom Enums
DO $$ BEGIN
    CREATE TYPE domain_status AS ENUM ('active', 'archived');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE domain_role_enum AS ENUM ('domain_admin', 'contributor', 'reader');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 6. Core Relational Tables
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    role VARCHAR(255) NOT NULL DEFAULT 'reader'
);

CREATE TABLE IF NOT EXISTS domains (
    id UUID PRIMARY KEY,
    name VARCHAR(255) UNIQUE NOT NULL,
    description TEXT,
    status domain_status NOT NULL DEFAULT 'active',
    created_by VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_domains_name ON domains(name);

CREATE TABLE IF NOT EXISTS domain_roles (
    id UUID PRIMARY KEY,
    domain_id UUID NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    user_id VARCHAR(255) NOT NULL,
    role domain_role_enum NOT NULL,
    assigned_by VARCHAR(255) NOT NULL,
    assigned_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL,
    CONSTRAINT uq_domain_user UNIQUE (domain_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_domain_roles_domain_id ON domain_roles(domain_id);
CREATE INDEX IF NOT EXISTS idx_domain_roles_user_id ON domain_roles(user_id);

CREATE TABLE IF NOT EXISTS domain_configs (
    id UUID PRIMARY KEY,
    domain_id UUID NOT NULL UNIQUE REFERENCES domains(id) ON DELETE CASCADE,
    llm_route VARCHAR(255) NOT NULL DEFAULT 'default',
    chunk_size INTEGER NOT NULL DEFAULT 512,
    chunk_overlap INTEGER NOT NULL DEFAULT 64,
    confidence_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    extra_settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_domain_configs_domain_id ON domain_configs(domain_id);

CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR PRIMARY KEY,
    domain_id VARCHAR NOT NULL,
    user_id VARCHAR NOT NULL,
    filename VARCHAR NOT NULL,
    file_path VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'pending',
    error_msg TEXT,
    task_id VARCHAR,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL
);

CREATE TABLE IF NOT EXISTS document_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    domain_id TEXT NOT NULL,
    page_num INTEGER,
    chunk_index INTEGER,
    text TEXT NOT NULL,
    source_type VARCHAR(50) DEFAULT 'pdf',
    chunk_type VARCHAR(50) DEFAULT 'text',
    filename TEXT DEFAULT '',
    search_vec TSVECTOR,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chunks_domain ON document_chunks(domain_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_fts ON document_chunks USING GIN(search_vec);

CREATE TABLE IF NOT EXISTS rag_query_logs (
    id BIGSERIAL PRIMARY KEY,
    domain_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    query TEXT NOT NULL,
    answer TEXT NOT NULL,
    llm_route TEXT NOT NULL,
    model TEXT NOT NULL,
    cache_hit BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 7. Evaluation, Moderation, and Audit Tables
CREATE TABLE IF NOT EXISTS evaluation_logs (
    id UUID PRIMARY KEY,
    query_id BIGINT NOT NULL,
    model_used VARCHAR(255) NOT NULL,
    faithfulness_score FLOAT,
    relevance_score FLOAT,
    completeness_score FLOAT,
    overall_score FLOAT,
    ragas_context_precision FLOAT,
    ragas_context_recall FLOAT,
    ragas_context_entity_recall FLOAT,
    ragas_answer_correctness FLOAT,
    ragas_answer_similarity FLOAT,
    raw_judge_response TEXT,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_evaluation_logs_query_judge UNIQUE (query_id, model_used)
);
CREATE INDEX IF NOT EXISTS ix_evaluation_logs_query_model ON evaluation_logs (query_id, model_used);

CREATE TABLE IF NOT EXISTS moderation_queue (
    id UUID PRIMARY KEY,
    query_id BIGINT NOT NULL,
    evaluation_log_id UUID NOT NULL REFERENCES evaluation_logs(id) ON DELETE CASCADE,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    reviewer VARCHAR(255),
    decision_notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at TIMESTAMPTZ,
    CONSTRAINT uq_moderation_queue_query_id UNIQUE (query_id)
);
CREATE INDEX IF NOT EXISTS idx_moderation_queue_query ON moderation_queue(query_id);

CREATE TABLE IF NOT EXISTS live_evaluation_cache (
    id UUID PRIMARY KEY,
    cache_key VARCHAR(64) NOT NULL,
    query TEXT NOT NULL,
    answer TEXT NOT NULL,
    context_chunks TEXT, -- JSON array
    reference TEXT,
    consumed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_live_evaluation_cache_key UNIQUE (cache_key)
);
CREATE INDEX IF NOT EXISTS idx_live_eval_cache_key ON live_evaluation_cache(cache_key);

CREATE TABLE IF NOT EXISTS eval_cursor (
    name VARCHAR(64) PRIMARY KEY,
    last_query_id BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type VARCHAR(64) NOT NULL,
    actor VARCHAR(255),
    query_id BIGINT,
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_audit_logs_event_type ON audit_logs(event_type);
CREATE INDEX IF NOT EXISTS ix_audit_logs_created_at ON audit_logs(created_at DESC);

-- 8. Restricted Application User Setup
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rag_app_user') THEN
        CREATE USER rag_app_user WITH PASSWORD 'rag_secure_password_2026';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE domain_db TO rag_app_user;
GRANT USAGE ON SCHEMA public TO rag_app_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO rag_app_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO rag_app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO rag_app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO rag_app_user;

-- 9. Seed Core Initial Data
INSERT INTO users (id, name, role) VALUES
    ('admin',                                    'System Admin User',         'system_admin'),
    ('652ec45e-1b68-478c-9bd3-81cc46fb24a9',    'System Admin User (UUID)',  'system_admin'),
    ('manager',                                  'Domain Manager User',       'domain_admin'),
    ('user',                                     'Regular Contributor User',  'contributor'),
    ('viewer',                                   'Read-Only Viewer',          'reader'),
    ('unauth',                                   'Unauthorized Hacker',       'unauthorized')
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, role = EXCLUDED.role;

INSERT INTO domains (id, name, description, status, created_by, created_at, updated_at) VALUES
    ('11111111-1111-1111-1111-111111111111', 'HR Policies Portal',         'Internal employee handbooks, travel guidance, and benefits details.',         'active', 'admin', NOW(), NOW()),
    ('22222222-2222-2222-2222-222222222222', 'Technical Support Guides',   'Hardware specs, troubleshooting steps, and customer support manuals.',        'active', 'admin', NOW(), NOW()),
    ('33333333-3333-3333-3333-333333333333', 'Legal Compliance Archive',   'Contracts, NDAs, and platform terms of service.',                            'active', 'admin', NOW(), NOW())
ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;

INSERT INTO domain_configs (id, domain_id, llm_route, chunk_size, chunk_overlap, confidence_threshold, extra_settings, updated_at) VALUES
    ('11111111-1111-1111-1111-111111111100', '11111111-1111-1111-1111-111111111111', 'api',   512, 64, 0.50, '{}', NOW()),
    ('22222222-2222-2222-2222-222222222200', '22222222-2222-2222-2222-222222222222', 'api',   512, 64, 0.40, '{}', NOW()),
    ('33333333-3333-3333-3333-333333333300', '33333333-3333-3333-3333-333333333333', 'local', 256, 32, 0.60, '{}', NOW())
ON CONFLICT (id) DO UPDATE SET llm_route = EXCLUDED.llm_route, chunk_size = EXCLUDED.chunk_size;

INSERT INTO domain_roles (id, domain_id, user_id, role, assigned_by, assigned_at) VALUES
    ('11111111-1111-1111-1111-111111111101', '11111111-1111-1111-1111-111111111111', 'manager', 'domain_admin', 'admin',   NOW()),
    ('11111111-1111-1111-1111-111111111102', '11111111-1111-1111-1111-111111111111', 'user',    'contributor',  'manager', NOW()),
    ('22222222-2222-2222-2222-222222222201', '22222222-2222-2222-2222-222222222222', 'viewer',  'reader',       'admin',   NOW())
ON CONFLICT (id) DO UPDATE SET role = EXCLUDED.role;

INSERT INTO documents (id, domain_id, user_id, filename, file_path, status, error_msg, created_at, updated_at) VALUES
    ('aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 'user',    'benefits_guide_2026.pdf',  'data/uploads/aaaa-bbbb-cccc-0001/benefits_guide_2026.pdf',  'done',   NULL,                                        NOW(), NOW()),
    ('aaaa-bbbb-cccc-0002', '22222222-2222-2222-2222-222222222222', 'manager', 'printer_setup.pdf',        'data/uploads/aaaa-bbbb-cccc-0002/printer_setup.pdf',        'done',   NULL,                                        NOW(), NOW()),
    ('aaaa-bbbb-cccc-0003', '33333333-3333-3333-3333-333333333333', 'admin',   'nda_template.pdf',         'data/uploads/aaaa-bbbb-cccc-0003/nda_template.pdf',         'failed', 'Tesseract OCR failed to parse scanned text.', NOW(), NOW())
ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status;

INSERT INTO document_chunks (id, document_id, domain_id, page_num, chunk_index, text, source_type, chunk_type, search_vec, created_at) VALUES
    ('chunk-uuid-001', 'aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 1, 0,
     'Employees receive 25 days of paid annual vacation leave, matching state mandates.', 'pdf', 'text',
     to_tsvector('simple', 'Employees receive 25 days of paid annual vacation leave, matching state mandates.'), NOW()),
    ('chunk-uuid-002', 'aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 2, 1,
     'Medical insurance cover includes full dental services and optical checkups up to $1000.', 'pdf', 'text',
     to_tsvector('simple', 'Medical insurance cover includes full dental services and optical checkups up to $1000.'), NOW()),
    ('chunk-uuid-003', 'aaaa-bbbb-cccc-0002', '22222222-2222-2222-2222-222222222222', 1, 0,
     'To resolve network issues on LaserJet printers, restore factory settings in admin panel.', 'pdf', 'text',
     to_tsvector('simple', 'To resolve network issues on LaserJet printers, restore factory settings in admin panel.'), NOW())
ON CONFLICT (id) DO UPDATE SET text = EXCLUDED.text;

INSERT INTO rag_query_logs (id, domain_id, user_id, query, answer, llm_route, model, cache_hit, created_at) VALUES
    (1, '11111111-1111-1111-1111-111111111111', 'user', 'How many leave days do we get?',
     'Employees are entitled to 25 days of paid annual leave.', 'api', 'llama-3.3-70b-versatile', FALSE, NOW())
ON CONFLICT (id) DO NOTHING;

INSERT INTO eval_cursor (name, last_query_id, updated_at) VALUES
    ('default', 0, NOW())
ON CONFLICT (name) DO NOTHING;
