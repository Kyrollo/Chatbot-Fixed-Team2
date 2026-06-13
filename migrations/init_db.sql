-- ═══════════════════════════════════════════════════════════════
-- FULL SCHEMA, MIGRATION & SEED DATA — Database Initialization
-- ═══════════════════════════════════════════════════════════════
-- Run this script against the domain_db to initialize the database
-- schema, set up permissions/users, run Sprint 2 migrations,
-- and seed test data.
--
-- How to run:
--   $env:PGPASSWORD="postgres_password"
--   psql -U postgres -d domain_db -f migrations/init_db.sql
-- ═══════════════════════════════════════════════════════════════

-- 1. Custom Enums (Used by domain-service)
DO $$ BEGIN
    CREATE TYPE domain_status AS ENUM ('active', 'archived');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE domain_role_enum AS ENUM ('domain_admin', 'contributor', 'reader');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;


-- 2. Users Table (Dev auth mode — ID-based login)
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255),
    role VARCHAR(255) DEFAULT 'reader'
);


-- 3. Domains Table (Isolated knowledge bases)
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


-- 4. Domain Roles Table (Memberships & Permissions)
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


-- 5. Domain Configurations Table (RAG Settings per Domain)
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


-- 6. Documents Table (Uploaded file metadata)
CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR PRIMARY KEY,
    domain_id VARCHAR NOT NULL,
    user_id VARCHAR NOT NULL,
    filename VARCHAR NOT NULL,
    file_path VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'pending',
    error_msg TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW() NOT NULL
);


-- 7. Document Chunks Table (BM25 Full-Text Search and provenance tracking)
CREATE TABLE IF NOT EXISTS document_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    domain_id TEXT NOT NULL,
    page_num INTEGER,
    chunk_index INTEGER,
    text TEXT NOT NULL,
    source_type VARCHAR(50) DEFAULT 'pdf',
    search_vec TSVECTOR,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chunks_domain ON document_chunks(domain_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_fts ON document_chunks USING GIN(search_vec);


-- 8. RAG Query Log Table (Generation audit logs)
CREATE TABLE IF NOT EXISTS rag_query_logs (
    id BIGSERIAL PRIMARY KEY,
    domain_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    query TEXT NOT NULL,
    answer TEXT NOT NULL,
    llm_route TEXT NOT NULL,
    model TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);


-- ═══════════════════════════════════════════════════════════════
-- SPRINT 2 MIGRATION UPDATES (For Existing databases)
-- ═══════════════════════════════════════════════════════════════
-- 1. Safely add source_type column to track file format provenance
ALTER TABLE document_chunks
  ADD COLUMN IF NOT EXISTS source_type VARCHAR(50) DEFAULT 'pdf';

-- 2. Ensure chunk_index has the correct integer type
ALTER TABLE document_chunks
  ALTER COLUMN chunk_index TYPE INTEGER;

-- 3. Backfill any existing document chunks with null source type
UPDATE document_chunks
  SET source_type = 'pdf'
  WHERE source_type IS NULL;


-- ═══════════════════════════════════════════════════════════════
-- RESTRICTED DB USER & INITIAL PERMISSIONS SETUP
-- ═══════════════════════════════════════════════════════════════
-- 1. Create a dedicated database user for the application (dev/prod isolation)
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'rag_app_user') THEN
        CREATE USER rag_app_user WITH PASSWORD 'rag_secure_password_2026';
    END IF;
END
$$;

-- 2. Grant permissions to the application user
GRANT CONNECT ON DATABASE domain_db TO rag_app_user;
GRANT USAGE ON SCHEMA public TO rag_app_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO rag_app_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO rag_app_user;

-- 3. Set default permissions so future tables are automatically accessible
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO rag_app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO rag_app_user;


-- ═══════════════════════════════════════════════════════════════
-- SEED DATA INSERTION
-- ═══════════════════════════════════════════════════════════════

-- Seed: Users
INSERT INTO users (id, name, role) VALUES
    ('admin',                                    'System Admin User',         'system_admin'),
    ('652ec45e-1b68-478c-9bd3-81cc46fb24a9',    'System Admin User (UUID)',  'system_admin'),
    ('manager',                                  'Domain Manager User',       'domain_admin'),
    ('user',                                     'Regular Contributor User',  'contributor'),
    ('viewer',                                   'Read-Only Viewer',          'reader'),
    ('unauth',                                   'Unauthorized Hacker',       'unauthorized')
ON CONFLICT (id) DO NOTHING;

-- Seed: Domains
INSERT INTO domains (id, name, description, status, created_by, created_at, updated_at) VALUES
    ('11111111-1111-1111-1111-111111111111', 'HR Policies Portal',         'Internal employee handbooks, travel guidance, and benefits details.',         'active', 'admin', NOW(), NOW()),
    ('22222222-2222-2222-2222-222222222222', 'Technical Support Guides',   'Hardware specs, troubleshooting steps, and customer support manuals.',        'active', 'admin', NOW(), NOW()),
    ('33333333-3333-3333-3333-333333333333', 'Legal Compliance Archive',   'Contracts, NDAs, and platform terms of service.',                            'active', 'admin', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

-- Seed: Domain Configs
INSERT INTO domain_configs (id, domain_id, llm_route, chunk_size, chunk_overlap, confidence_threshold, extra_settings, updated_at) VALUES
    ('11111111-1111-1111-1111-111111111100', '11111111-1111-1111-1111-111111111111', 'api',   512, 64, 0.50, '{}', NOW()),
    ('22222222-2222-2222-2222-222222222200', '22222222-2222-2222-2222-222222222222', 'api',   512, 64, 0.40, '{}', NOW()),
    ('33333333-3333-3333-3333-333333333300', '33333333-3333-3333-3333-333333333333', 'local', 256, 32, 0.60, '{}', NOW())
ON CONFLICT (id) DO NOTHING;

-- Seed: Domain Roles (RBAC)
INSERT INTO domain_roles (id, domain_id, user_id, role, assigned_by, assigned_at) VALUES
    ('11111111-1111-1111-1111-111111111101', '11111111-1111-1111-1111-111111111111', 'manager', 'domain_admin', 'admin',   NOW()),
    ('11111111-1111-1111-1111-111111111102', '11111111-1111-1111-1111-111111111111', 'user',    'contributor',  'manager', NOW()),
    ('22222222-2222-2222-2222-222222222201', '22222222-2222-2222-2222-222222222222', 'viewer',  'reader',       'admin',   NOW())
ON CONFLICT (id) DO NOTHING;

-- Seed: Documents
INSERT INTO documents (id, domain_id, user_id, filename, file_path, status, error_msg, created_at, updated_at) VALUES
    ('aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 'user',    'benefits_guide_2026.pdf',  'data/uploads/aaaa-bbbb-cccc-0001/benefits_guide_2026.pdf',  'done',   NULL,                                        NOW(), NOW()),
    ('aaaa-bbbb-cccc-0002', '22222222-2222-2222-2222-222222222222', 'manager', 'printer_setup.pdf',        'data/uploads/aaaa-bbbb-cccc-0002/printer_setup.pdf',        'done',   NULL,                                        NOW(), NOW()),
    ('aaaa-bbbb-cccc-0003', '33333333-3333-3333-3333-333333333333', 'admin',   'nda_template.pdf',         'data/uploads/aaaa-bbbb-cccc-0003/nda_template.pdf',         'failed', 'Tesseract OCR failed to parse scanned text.', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;

-- Seed: Document Chunks
INSERT INTO document_chunks (id, document_id, domain_id, page_num, chunk_index, text, source_type, search_vec, created_at) VALUES
    ('chunk-uuid-001', 'aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 1, 0,
     'Employees receive 25 days of paid annual vacation leave, matching state mandates.', 'pdf',
     to_tsvector('simple', 'Employees receive 25 days of paid annual vacation leave, matching state mandates.'), NOW()),
    ('chunk-uuid-002', 'aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 2, 1,
     'Medical insurance cover includes full dental services and optical checkups up to $1000.', 'pdf',
     to_tsvector('simple', 'Medical insurance cover includes full dental services and optical checkups up to $1000.'), NOW()),
    ('chunk-uuid-003', 'aaaa-bbbb-cccc-0002', '22222222-2222-2222-2222-222222222222', 1, 0,
     'To resolve network issues on LaserJet printers, restore factory settings in admin panel.', 'pdf',
     to_tsvector('simple', 'To resolve network issues on LaserJet printers, restore factory settings in admin panel.'), NOW())
ON CONFLICT (id) DO NOTHING;

-- Seed: Query Logs
INSERT INTO rag_query_logs (domain_id, user_id, query, answer, llm_route, model, created_at) VALUES
    ('11111111-1111-1111-1111-111111111111', 'user', 'How many leave days do we get?',
     'Employees are entitled to 25 days of paid annual leave.', 'api', 'llama-3.3-70b-versatile', NOW())
ON CONFLICT (id) DO NOTHING;

-- ═══════════════════════════════════════════════════════════════
-- DONE! All tables created, migrated, seeded, and permissions configured.
-- ═══════════════════════════════════════════════════════════════
