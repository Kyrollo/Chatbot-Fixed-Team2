# Database & Infrastructure Setup Guide

Complete from-scratch setup guide for the RAG system. Follow this guide to set up everything the project needs on a fresh machine.

> **Where to run commands:** This guide uses two tools. Every command block is labeled with where to run it:
> - 🟦 **PowerShell** — Windows PowerShell or Terminal. Open from Start Menu → "PowerShell" or "Terminal".
> - 🟩 **psql** — The PostgreSQL interactive shell. You enter it by running `psql -U postgres -d domain_db` from PowerShell. Type `\q` to exit back to PowerShell.
>
> **How to tell where you are:**
> - If your prompt shows `PS C:\...>` → you are in **PowerShell**
> - If your prompt shows `domain_db=#` or `postgres=#` → you are in **psql**

---

## Table of Contents

1. [PostgreSQL Installation](#1-postgresql-installation)
2. [Create Database](#2-create-database)
3. [Schema DDL (Table Creation)](#3-schema-ddl-table-creation)
4. [Seed Data — Users](#4-seed-data--users)
5. [Seed Data — Domains, Configs & Roles](#5-seed-data--domains-configs--roles)
6. [Seed Data — Documents & Chunks](#6-seed-data--documents--chunks)
7. [Seed Data — Query Logs](#7-seed-data--query-logs)
8. [One-Command Full Seed (Copy & Paste)](#8-one-command-full-seed-copy--paste)
9. [PostgreSQL User (Production/Restricted)](#9-postgresql-user-productionrestricted)
10. [Java Installation (Required for Keycloak)](#10-java-installation-required-for-keycloak)
11. [Keycloak Setup](#11-keycloak-setup)
12. [Keycloak Users & Roles](#12-keycloak-users--roles)
13. [Redis Setup](#13-redis-setup)
14. [Qdrant Setup](#14-qdrant-setup)
15. [React Frontend Setup](#15-react-frontend-setup)
16. [Full System Reset](#16-full-system-reset)
17. [Verification Queries](#17-verification-queries)

---

## 1. PostgreSQL Installation

1. Download PostgreSQL 16 from https://www.postgresql.org/download/windows/
2. Run the installer. **Remember the password** you set for the `postgres` user.
3. Keep the default port **5432**.
4. After installation, verify:

🟦 **Run in PowerShell:**
```powershell
psql -U postgres -V
```

If the command is not found, add PostgreSQL to your PATH:

🟦 **Run in PowerShell:**
```powershell
# Typical path — adjust version number if different
$env:Path += ";C:\Program Files\PostgreSQL\16\bin"
```

If PostgreSQL service is not running:

🟦 **Run in PowerShell (as Administrator):**
```powershell
net start postgresql-x64-16
```

### ✅ Check: PostgreSQL is installed correctly

🟦 **Run in PowerShell:**
```powershell
psql -U postgres -c "SELECT version();"
```
**Expected:** Shows PostgreSQL version number (e.g., `PostgreSQL 16.x ...`). If you get "password authentication failed", your password in `.env` doesn't match the one you set during installation.

---

## 2. Create Database

🟦 **Run in PowerShell:**
```powershell
# Set your PostgreSQL password (the one you chose during installation)
$env:PGPASSWORD="postgres_password"

# Create the database
psql -U postgres -c "CREATE DATABASE domain_db;"
```

### ✅ Check: Database was created

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="postgres_password"
psql -U postgres -c "\l" | Select-String "domain_db"
```
**Expected:** You see a line containing `domain_db`. If not, run the CREATE DATABASE command again.

---

## 3. Schema DDL (Table Creation)

> **Note:** The `domain-service` automatically creates all tables on startup via `Base.metadata.create_all`. Running these manually is **optional** — only needed if you want to pre-create the schema before starting services, or for troubleshooting.

**Step 1:** Open the psql shell connected to `domain_db`:

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="postgres_password"
psql -U postgres -d domain_db
```

Your prompt will change to `domain_db=#` — you are now inside **psql**.

**Step 2:** Copy and paste the entire SQL block below into the psql shell:

🟩 **Run inside psql (domain_db=#):**
```sql
-- 1. Custom Enums (Used by domain-service)
DO $$ BEGIN
    CREATE TYPE domain_status AS ENUM ('active', 'archived');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE domain_role_enum AS ENUM ('domain_admin', 'contributor', 'reader');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- 2. Domains Table
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

-- 3. Domain Roles Table (Memberships & Permissions)
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

-- 4. Domain Configurations Table (RAG Settings per Domain)
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

-- 5. Documents Table (Uploaded file metadata)
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

-- 6. Document Chunks Table (BM25 Full-Text Search)
CREATE TABLE IF NOT EXISTS document_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    domain_id TEXT NOT NULL,
    page_num INTEGER,
    chunk_index INTEGER,
    text TEXT NOT NULL,
    search_vec TSVECTOR,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chunks_domain ON document_chunks(domain_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_fts ON document_chunks USING GIN(search_vec);

-- 7. RAG Query Log Table (Generation audit logs)
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

-- 8. Users Table (Dev auth mode — ID-based login)
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(255) PRIMARY KEY,
    name VARCHAR(255),
    role VARCHAR(255) DEFAULT 'reader'
);
```

### ✅ Check: Tables were created

🟩 **Run inside psql (domain_db=#):**
```sql
\dt
```
**Expected:** You see a list of 7 tables: `document_chunks`, `documents`, `domain_configs`, `domain_roles`, `domains`, `rag_query_logs`, `users`.

If you see `Did not find any relations`, the SQL did not execute. Try pasting it again.

---

## 4. Seed Data — Users

> You must already be inside psql connected to `domain_db`. If not, reconnect:
>
> 🟦 **PowerShell:** `$env:PGPASSWORD="postgres_password"; psql -U postgres -d domain_db`

🟩 **Run inside psql (domain_db=#):**
```sql
INSERT INTO users (id, name, role) VALUES
    ('admin',                                    'System Admin User',         'system_admin'),
    ('652ec45e-1b68-478c-9bd3-81cc46fb24a9',    'System Admin User (UUID)',  'system_admin'),
    ('manager',                                  'Domain Manager User',       'domain_admin'),
    ('user',                                     'Regular Contributor User',  'contributor'),
    ('viewer',                                   'Read-Only Viewer',          'reader'),
    ('unauth',                                   'Unauthorized Hacker',       'unauthorized')
ON CONFLICT (id) DO NOTHING;
```

| User ID | Role | Can Do |
|---|---|---|
| `admin` | `system_admin` | Everything — create domains, manage all settings |
| `manager` | `domain_admin` | Manage assigned domains, add members, change config |
| `user` | `contributor` | Upload PDFs to assigned domains |
| `viewer` | `reader` | Query and read within assigned domains |
| `unauth` | `unauthorized` | Nothing — used for testing access denial |

### ✅ Check: Users were inserted

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT id, name, role FROM users;
```
**Expected:** 6 rows with the users listed above. If you see `0 rows`, the INSERT failed — check that the `users` table exists (`\dt`).

---

## 5. Seed Data — Domains, Configs & Roles

> You must already be inside psql connected to `domain_db`.

### 5.1 Create Test Domains

🟩 **Run inside psql (domain_db=#):**
```sql
INSERT INTO domains (id, name, description, status, created_by, created_at, updated_at) VALUES
    ('11111111-1111-1111-1111-111111111111', 'HR Policies Portal',         'Internal employee handbooks, travel guidance, and benefits details.',         'active', 'admin', NOW(), NOW()),
    ('22222222-2222-2222-2222-222222222222', 'Technical Support Guides',   'Hardware specs, troubleshooting steps, and customer support manuals.',        'active', 'admin', NOW(), NOW()),
    ('33333333-3333-3333-3333-333333333333', 'Legal Compliance Archive',   'Contracts, NDAs, and platform terms of service.',                            'active', 'admin', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
```

### ✅ Check: Domains were created

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT name, status FROM domains;
```
**Expected:** 3 rows — HR Policies Portal, Technical Support Guides, Legal Compliance Archive. All `active`.

### 5.2 Create Domain Configurations

🟩 **Run inside psql (domain_db=#):**
```sql
INSERT INTO domain_configs (id, domain_id, llm_route, chunk_size, chunk_overlap, confidence_threshold, extra_settings, updated_at) VALUES
    ('11111111-1111-1111-1111-111111111100', '11111111-1111-1111-1111-111111111111', 'api',   512, 64, 0.50, '{}', NOW()),
    ('22222222-2222-2222-2222-222222222200', '22222222-2222-2222-2222-222222222222', 'api',   512, 64, 0.40, '{}', NOW()),
    ('33333333-3333-3333-3333-333333333300', '33333333-3333-3333-3333-333333333333', 'local', 256, 32, 0.60, '{}', NOW())
ON CONFLICT (id) DO NOTHING;
```

### ✅ Check: Configs were created

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT domain_id, llm_route, chunk_size FROM domain_configs;
```
**Expected:** 3 rows. Two with `llm_route=api`, one with `llm_route=local`.

### 5.3 Assign Domain Memberships (RBAC)

🟩 **Run inside psql (domain_db=#):**
```sql
INSERT INTO domain_roles (id, domain_id, user_id, role, assigned_by, assigned_at) VALUES
    ('11111111-1111-1111-1111-111111111101', '11111111-1111-1111-1111-111111111111', 'manager', 'domain_admin', 'admin',   NOW()),
    ('11111111-1111-1111-1111-111111111102', '11111111-1111-1111-1111-111111111111', 'user',    'contributor',  'manager', NOW()),
    ('22222222-2222-2222-2222-222222222201', '22222222-2222-2222-2222-222222222222', 'viewer',  'reader',       'admin',   NOW())
ON CONFLICT (id) DO NOTHING;
```

### ✅ Check: RBAC memberships were assigned

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT dr.user_id, dr.role, d.name AS domain_name
FROM domain_roles dr JOIN domains d ON dr.domain_id = d.id;
```
**Expected:** 3 rows — manager=domain_admin on HR, user=contributor on HR, viewer=reader on Technical Support.

---

## 6. Seed Data — Documents & Chunks

> You must already be inside psql connected to `domain_db`.

### 6.1 Mock Uploaded Documents

🟩 **Run inside psql (domain_db=#):**
```sql
INSERT INTO documents (id, domain_id, user_id, filename, file_path, status, error_msg, created_at, updated_at) VALUES
    ('aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 'user',    'benefits_guide_2026.pdf',  'data/uploads/aaaa-bbbb-cccc-0001/benefits_guide_2026.pdf',  'done',   NULL,                                        NOW(), NOW()),
    ('aaaa-bbbb-cccc-0002', '22222222-2222-2222-2222-222222222222', 'manager', 'printer_setup.pdf',        'data/uploads/aaaa-bbbb-cccc-0002/printer_setup.pdf',        'done',   NULL,                                        NOW(), NOW()),
    ('aaaa-bbbb-cccc-0003', '33333333-3333-3333-3333-333333333333', 'admin',   'nda_template.pdf',         'data/uploads/aaaa-bbbb-cccc-0003/nda_template.pdf',         'failed', 'Tesseract OCR failed to parse scanned text.', NOW(), NOW())
ON CONFLICT (id) DO NOTHING;
```

### ✅ Check: Documents were inserted

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT id, filename, status FROM documents;
```
**Expected:** 3 rows — 2 with `status=done`, 1 with `status=failed`.

### 6.2 Indexed Document Chunks (Enables Immediate Search)

🟩 **Run inside psql (domain_db=#):**
```sql
INSERT INTO document_chunks (id, document_id, domain_id, page_num, chunk_index, text, search_vec, created_at) VALUES
    ('chunk-uuid-001', 'aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 1, 0,
     'Employees receive 25 days of paid annual vacation leave, matching state mandates.',
     to_tsvector('simple', 'Employees receive 25 days of paid annual vacation leave, matching state mandates.'), NOW()),
    ('chunk-uuid-002', 'aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 2, 1,
     'Medical insurance cover includes full dental services and optical checkups up to $1000.',
     to_tsvector('simple', 'Medical insurance cover includes full dental services and optical checkups up to $1000.'), NOW()),
    ('chunk-uuid-003', 'aaaa-bbbb-cccc-0002', '22222222-2222-2222-2222-222222222222', 1, 0,
     'To resolve network issues on LaserJet printers, restore factory settings in admin panel.',
     to_tsvector('simple', 'To resolve network issues on LaserJet printers, restore factory settings in admin panel.'), NOW())
ON CONFLICT (id) DO NOTHING;
```

### ✅ Check: Chunks were inserted with search vectors

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT COUNT(*) AS total_chunks, COUNT(search_vec) AS with_search_vec FROM document_chunks;
```
**Expected:** `total_chunks=3`, `with_search_vec=3`. If `with_search_vec` is 0, the `to_tsvector()` call failed.

---

## 7. Seed Data — Query Logs

> You must already be inside psql connected to `domain_db`.

🟩 **Run inside psql (domain_db=#):**
```sql
INSERT INTO rag_query_logs (domain_id, user_id, query, answer, llm_route, model, created_at) VALUES
    ('11111111-1111-1111-1111-111111111111', 'user', 'How many leave days do we get?',
     'Employees are entitled to 25 days of paid annual leave.', 'api', 'llama-3.3-70b-versatile', NOW());
```

### ✅ Check: Query log was inserted

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT query, llm_route, model FROM rag_query_logs;
```
**Expected:** 1 row showing the vacation leave query.

**You can now exit psql:**

🟩 **Run inside psql (domain_db=#):**
```sql
\q
```
This returns you to PowerShell.

---

## 8. One-Command Full Seed (Copy & Paste)

> **Use this section if you want to skip steps 3-7 and do everything at once.**
> This creates all tables AND inserts all seed data in a single copy-paste.

**Step 1:** Open psql connected to `domain_db`:

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="postgres_password"
psql -U postgres -d domain_db
```

**Step 2:** Copy and paste the ENTIRE block below into the psql shell:

🟩 **Run inside psql (domain_db=#) — paste everything at once:**
```sql
-- ═══════════════════════════════════════════════════════════════
-- FULL SCHEMA + SEED DATA — One-Command Setup
-- ═══════════════════════════════════════════════════════════════

-- Enums
DO $$ BEGIN CREATE TYPE domain_status AS ENUM ('active', 'archived'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE domain_role_enum AS ENUM ('domain_admin', 'contributor', 'reader'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Tables
CREATE TABLE IF NOT EXISTS users (id VARCHAR(255) PRIMARY KEY, name VARCHAR(255), role VARCHAR(255) DEFAULT 'reader');
CREATE TABLE IF NOT EXISTS domains (id UUID PRIMARY KEY, name VARCHAR(255) UNIQUE NOT NULL, description TEXT, status domain_status NOT NULL DEFAULT 'active', created_by VARCHAR(255) NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL, updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL);
CREATE TABLE IF NOT EXISTS domain_roles (id UUID PRIMARY KEY, domain_id UUID NOT NULL REFERENCES domains(id) ON DELETE CASCADE, user_id VARCHAR(255) NOT NULL, role domain_role_enum NOT NULL, assigned_by VARCHAR(255) NOT NULL, assigned_at TIMESTAMPTZ DEFAULT NOW() NOT NULL, CONSTRAINT uq_domain_user UNIQUE (domain_id, user_id));
CREATE TABLE IF NOT EXISTS domain_configs (id UUID PRIMARY KEY, domain_id UUID NOT NULL UNIQUE REFERENCES domains(id) ON DELETE CASCADE, llm_route VARCHAR(255) NOT NULL DEFAULT 'default', chunk_size INTEGER NOT NULL DEFAULT 512, chunk_overlap INTEGER NOT NULL DEFAULT 64, confidence_threshold DOUBLE PRECISION NOT NULL DEFAULT 0.5, extra_settings JSONB NOT NULL DEFAULT '{}'::jsonb, updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL);
CREATE TABLE IF NOT EXISTS documents (id VARCHAR PRIMARY KEY, domain_id VARCHAR NOT NULL, user_id VARCHAR NOT NULL, filename VARCHAR NOT NULL, file_path VARCHAR NOT NULL, status VARCHAR NOT NULL DEFAULT 'pending', error_msg TEXT, created_at TIMESTAMP DEFAULT NOW() NOT NULL, updated_at TIMESTAMP DEFAULT NOW() NOT NULL);
CREATE TABLE IF NOT EXISTS document_chunks (id TEXT PRIMARY KEY, document_id TEXT NOT NULL, domain_id TEXT NOT NULL, page_num INTEGER, chunk_index INTEGER, text TEXT NOT NULL, search_vec TSVECTOR, created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS rag_query_logs (id BIGSERIAL PRIMARY KEY, domain_id TEXT NOT NULL, user_id TEXT NOT NULL, query TEXT NOT NULL, answer TEXT NOT NULL, llm_route TEXT NOT NULL, model TEXT NOT NULL, created_at TIMESTAMPTZ DEFAULT NOW());

-- Indexes
CREATE INDEX IF NOT EXISTS idx_domains_name ON domains(name);
CREATE INDEX IF NOT EXISTS idx_domain_roles_domain_id ON domain_roles(domain_id);
CREATE INDEX IF NOT EXISTS idx_domain_roles_user_id ON domain_roles(user_id);
CREATE INDEX IF NOT EXISTS idx_domain_configs_domain_id ON domain_configs(domain_id);
CREATE INDEX IF NOT EXISTS idx_chunks_domain ON document_chunks(domain_id);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_fts ON document_chunks USING GIN(search_vec);

-- Seed: Users
INSERT INTO users (id, name, role) VALUES
    ('admin', 'System Admin User', 'system_admin'),
    ('652ec45e-1b68-478c-9bd3-81cc46fb24a9', 'System Admin User (UUID)', 'system_admin'),
    ('manager', 'Domain Manager User', 'domain_admin'),
    ('user', 'Regular Contributor User', 'contributor'),
    ('viewer', 'Read-Only Viewer', 'reader'),
    ('unauth', 'Unauthorized Hacker', 'unauthorized')
ON CONFLICT (id) DO NOTHING;

-- Seed: Domains
INSERT INTO domains (id, name, description, status, created_by) VALUES
    ('11111111-1111-1111-1111-111111111111', 'HR Policies Portal', 'Internal employee handbooks, travel guidance, and benefits details.', 'active', 'admin'),
    ('22222222-2222-2222-2222-222222222222', 'Technical Support Guides', 'Hardware specs, troubleshooting steps, and customer support manuals.', 'active', 'admin'),
    ('33333333-3333-3333-3333-333333333333', 'Legal Compliance Archive', 'Contracts, NDAs, and platform terms of service.', 'active', 'admin')
ON CONFLICT (id) DO NOTHING;

-- Seed: Domain Configs
INSERT INTO domain_configs (id, domain_id, llm_route, chunk_size, chunk_overlap, confidence_threshold, extra_settings) VALUES
    ('11111111-1111-1111-1111-111111111100', '11111111-1111-1111-1111-111111111111', 'api', 512, 64, 0.50, '{}'),
    ('22222222-2222-2222-2222-222222222200', '22222222-2222-2222-2222-222222222222', 'api', 512, 64, 0.40, '{}'),
    ('33333333-3333-3333-3333-333333333300', '33333333-3333-3333-3333-333333333333', 'local', 256, 32, 0.60, '{}')
ON CONFLICT (id) DO NOTHING;

-- Seed: Domain Roles (RBAC)
INSERT INTO domain_roles (id, domain_id, user_id, role, assigned_by) VALUES
    ('11111111-1111-1111-1111-111111111101', '11111111-1111-1111-1111-111111111111', 'manager', 'domain_admin', 'admin'),
    ('11111111-1111-1111-1111-111111111102', '11111111-1111-1111-1111-111111111111', 'user', 'contributor', 'manager'),
    ('22222222-2222-2222-2222-222222222201', '22222222-2222-2222-2222-222222222222', 'viewer', 'reader', 'admin')
ON CONFLICT (id) DO NOTHING;

-- Seed: Documents
INSERT INTO documents (id, domain_id, user_id, filename, file_path, status, error_msg) VALUES
    ('aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 'user', 'benefits_guide_2026.pdf', 'data/uploads/aaaa-bbbb-cccc-0001/benefits_guide_2026.pdf', 'done', NULL),
    ('aaaa-bbbb-cccc-0002', '22222222-2222-2222-2222-222222222222', 'manager', 'printer_setup.pdf', 'data/uploads/aaaa-bbbb-cccc-0002/printer_setup.pdf', 'done', NULL),
    ('aaaa-bbbb-cccc-0003', '33333333-3333-3333-3333-333333333333', 'admin', 'nda_template.pdf', 'data/uploads/aaaa-bbbb-cccc-0003/nda_template.pdf', 'failed', 'Tesseract OCR failed.')
ON CONFLICT (id) DO NOTHING;

-- Seed: Document Chunks
INSERT INTO document_chunks (id, document_id, domain_id, page_num, chunk_index, text, search_vec) VALUES
    ('chunk-uuid-001', 'aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 1, 0, 'Employees receive 25 days of paid annual vacation leave, matching state mandates.', to_tsvector('simple', 'Employees receive 25 days of paid annual vacation leave, matching state mandates.')),
    ('chunk-uuid-002', 'aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 2, 1, 'Medical insurance cover includes full dental services and optical checkups up to $1000.', to_tsvector('simple', 'Medical insurance cover includes full dental services and optical checkups up to $1000.')),
    ('chunk-uuid-003', 'aaaa-bbbb-cccc-0002', '22222222-2222-2222-2222-222222222222', 1, 0, 'To resolve network issues on LaserJet printers, restore factory settings in admin panel.', to_tsvector('simple', 'To resolve network issues on LaserJet printers, restore factory settings in admin panel.'))
ON CONFLICT (id) DO NOTHING;

-- Seed: Query Logs
INSERT INTO rag_query_logs (domain_id, user_id, query, answer, llm_route, model) VALUES
    ('11111111-1111-1111-1111-111111111111', 'user', 'How many leave days do we get?', 'Employees are entitled to 25 days of paid annual leave.', 'api', 'llama-3.3-70b-versatile');

-- ═══════════════════════════════════════════════════════════════
-- DONE! All tables created and seed data inserted.
-- ═══════════════════════════════════════════════════════════════
```

**Step 3:** Exit psql:

🟩 **Run inside psql (domain_db=#):**
```sql
\q
```

### ✅ Check: Everything was created (quick validation from PowerShell)

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="postgres_password"
psql -U postgres -d domain_db -c "SELECT 'users=' || count(*) FROM users UNION ALL SELECT 'domains=' || count(*) FROM domains UNION ALL SELECT 'configs=' || count(*) FROM domain_configs UNION ALL SELECT 'roles=' || count(*) FROM domain_roles UNION ALL SELECT 'documents=' || count(*) FROM documents UNION ALL SELECT 'chunks=' || count(*) FROM document_chunks UNION ALL SELECT 'logs=' || count(*) FROM rag_query_logs;"
```
**Expected output:**
```
 users=6
 domains=3
 configs=3
 roles=3
 documents=3
 chunks=3
 logs=1
```

---

## 9. PostgreSQL User (Production/Restricted)

> This section is **optional**. Only needed for production or isolated environments where you want a dedicated app user instead of the `postgres` superuser.

**Step 1:** Open psql connected to the default `postgres` database:

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="postgres_password"
psql -U postgres
```

**Step 2:** Run these SQL commands:

🟩 **Run inside psql (postgres=#):**
```sql
-- 1. Create a dedicated application user
CREATE USER rag_app_user WITH PASSWORD 'rag_secure_password_2026';

-- 2. Grant connection permission
GRANT CONNECT ON DATABASE domain_db TO rag_app_user;

-- 3. Connect to domain_db and grant schema usage
\c domain_db;
GRANT USAGE ON SCHEMA public TO rag_app_user;

-- 4. Grant table and sequence permissions
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO rag_app_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO rag_app_user;

-- 5. Set default permissions for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO rag_app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO rag_app_user;
```

**Step 3:** Exit psql and update your `.env`:

🟩 **Run inside psql:**
```sql
\q
```

Then edit your `.env` file:
```env
POSTGRES_USER=rag_app_user
POSTGRES_PASSWORD=rag_secure_password_2026
DATABASE_URL=postgresql+asyncpg://rag_app_user:rag_secure_password_2026@localhost:5432/domain_db
SYNC_DATABASE_URL=postgresql://rag_app_user:rag_secure_password_2026@localhost:5432/domain_db
```

### ✅ Check: New user can connect

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="rag_secure_password_2026"
psql -U rag_app_user -d domain_db -c "SELECT count(*) FROM users;"
```
**Expected:** Returns the count of users (6).

---

## 10. Java Installation (Required for Keycloak)

Keycloak requires Java 17+:

1. Download from https://adoptium.net/ (Temurin JDK 17+)
2. Install and verify:

🟦 **Run in PowerShell:**
```powershell
java -version
```

### ✅ Check: Java is installed

**Expected:** Output starts with `openjdk version "17...` or higher. If you get "command not found", restart PowerShell or add Java to PATH.

---

## 11. Keycloak Setup

### Option A — Automatic (Recommended)

`run_services.py` downloads Keycloak 26.5.0 automatically on first run to `tools/keycloak/`.

> First download is ~150 MB. If the download fails due to SSL errors, use Option B.

### Option B — Manual Download

1. Download from https://github.com/keycloak/keycloak/releases/download/26.5.0/keycloak-26.5.0.zip
2. Extract to `tools/keycloak/` (the folder should contain `bin/kc.bat`)
3. Copy the realm config and start:

🟦 **Run in PowerShell:**
```powershell
mkdir "tools\keycloak\data\import" -Force
copy "services\auth\realm-export.json" "tools\keycloak\data\import\realm-export.json"

$env:KC_BOOTSTRAP_ADMIN_USERNAME="admin"
$env:KC_BOOTSTRAP_ADMIN_PASSWORD="admin"
.\tools\keycloak\bin\kc.bat start-dev --http-port=8180 --import-realm
```

### ✅ Check: Keycloak is running (wait 30–60 seconds after starting)

🟦 **Run in PowerShell:**
```powershell
curl http://localhost:8180/realms/rag-system
```
**Expected:** Returns a JSON object with realm info. If connection refused, wait longer and retry.

### Keycloak Admin Console

- URL: http://localhost:8180
- Username: `admin`
- Password: `admin`

---

## 12. Keycloak Users & Roles

### Seeded Accounts (Auto-Imported)

These users are automatically created from `services/auth/realm-export.json`:

| Username | Password | Realm Role |
|---|---|---|
| `admin` | `admin` | `system_admin` |
| `reader1` | `reader1` | `reader` |

### Option A: Create Users via Keycloak Admin CLI (kcadm)

> Keycloak must be running on port 8180 before executing these commands.

🟦 **Run in PowerShell (from the project root directory):**
```powershell
# 1. Login to Keycloak Admin CLI
.\tools\keycloak\bin\kcadm.bat config credentials --server http://localhost:8180 --realm master --user admin --password admin

# 2. Create users under the 'rag-system' realm
.\tools\keycloak\bin\kcadm.bat create users -r rag-system -s username=manager -s enabled=true
.\tools\keycloak\bin\kcadm.bat create users -r rag-system -s username=user1 -s enabled=true
.\tools\keycloak\bin\kcadm.bat create users -r rag-system -s username=viewer1 -s enabled=true

# 3. Set passwords (non-temporary)
.\tools\keycloak\bin\kcadm.bat set-password -r rag-system --username manager --new-password manager
.\tools\keycloak\bin\kcadm.bat set-password -r rag-system --username user1 --new-password user1
.\tools\keycloak\bin\kcadm.bat set-password -r rag-system --username viewer1 --new-password viewer1

# 4. Assign roles (--uusername = double u for lookup by username)
.\tools\keycloak\bin\kcadm.bat add-roles -r rag-system --uusername admin --rolename system_admin
.\tools\keycloak\bin\kcadm.bat add-roles -r rag-system --uusername viewer1 --rolename reader
```

### ✅ Check: Users were created in Keycloak

🟦 **Run in PowerShell:**
```powershell
.\tools\keycloak\bin\kcadm.bat get users -r rag-system --fields username
```
**Expected:** List of usernames including `admin`, `reader1`, `manager`, `user1`, `viewer1`.

### Option B: Create Users via Keycloak Admin Console UI

1. Open http://localhost:8180 (Admin: `admin` / `admin`)
2. Switch realm to **rag-system** (top-left dropdown)
3. Navigate to **Users** → **Add user**
4. Set Username (e.g., `user1`), click **Create**
5. Go to **Credentials** tab → **Set password** (disable *Temporary*)
6. Go to **Role mapping** tab → **Assign role** → select `system_admin`, `reader`, etc.

---

## 13. Redis Setup

### Option A — Automatic (Recommended)

`run_services.py` downloads portable Redis for Windows automatically on first run to `tools/redis/`.

> If Redis is unavailable, the system gracefully degrades to in-memory cache and synchronous ingestion. Redis is **not required** for local development.

### Option B — Manual

🟦 **Run in PowerShell:**
```powershell
# Install via winget
winget install Redis.Redis
redis-server

# Or start the auto-downloaded portable version
tools\redis\redis-server.exe tools\redis\redis.windows.conf
```

### ✅ Check: Redis is running

🟦 **Run in PowerShell:**
```powershell
.venv\Scripts\python.exe -c "import redis; r=redis.Redis(host='localhost', port=6379, protocol=2); print('Redis OK:', r.ping())"
```
**Expected:** `Redis OK: True`. If connection refused, Redis isn't running — this is OK, the system works without it.

> This project uses Redis 5.x. The Python `redis` library defaults to RESP3 — we use `protocol=2` for compatibility.

---

## 14. Qdrant Setup

**No install needed.** Qdrant runs in embedded mode — storage is created automatically at `data/qdrant/` on first use.

### ✅ Check: Nothing to check — it's automatic!

---

## 15. React Frontend Setup

🟦 **Run in PowerShell:**
```powershell
cd rag-ui
npm install
npm run dev
```

The frontend will be accessible at: http://localhost:5173/

> In dev auth mode (no Keycloak), you can sign in by typing the User ID directly (e.g., `admin`, `user`, `viewer`).

### ✅ Check: Frontend is running

Open http://localhost:5173/ in your browser. You should see the login page.

---

## 16. Full System Reset

### Option A: Using `delete_chunks.py` (Recommended)

🟦 **Run in PowerShell (from the project root directory):**
```powershell
# Interactive — asks for confirmation before deleting
.venv\Scripts\python.exe delete_chunks.py

# Non-interactive — skips confirmation (for automation)
.venv\Scripts\python.exe delete_chunks.py --yes
```

This truncates all PostgreSQL tables and removes all Qdrant collections and storage.

### Option B: Manual PostgreSQL Reset

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="postgres_password"
psql -U postgres -d domain_db -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
```

Then either restart `run_services.py` (tables recreate automatically) or re-run the [One-Command Full Seed](#8-one-command-full-seed-copy--paste).

### ✅ Check: Database is clean

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="postgres_password"
psql -U postgres -d domain_db -c "SELECT count(*) FROM domains;"
```
**Expected:** `count = 0` (or an error saying "relation does not exist" if you used Option B without re-seeding).

---

## 17. Verification Queries

> **Where to run these:** All queries in this section run inside the **psql** shell.

**Step 1:** Open psql connected to `domain_db`:

🟦 **Run in PowerShell:**
```powershell
$env:PGPASSWORD="postgres_password"
psql -U postgres -d domain_db
```

Your prompt changes to `domain_db=#`. Now run each query below one at a time:

---

### 17.1 Check users exist

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT id, name, role FROM users;
```
**Expected:** 6 rows:

| id | name | role |
|---|---|---|
| admin | System Admin User | system_admin |
| 652ec45e-... | System Admin User (UUID) | system_admin |
| manager | Domain Manager User | domain_admin |
| user | Regular Contributor User | contributor |
| viewer | Read-Only Viewer | reader |
| unauth | Unauthorized Hacker | unauthorized |

---

### 17.2 Check domains exist

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT id, name, status FROM domains;
```
**Expected:** 3 rows — all with `status=active`:

| name | status |
|---|---|
| HR Policies Portal | active |
| Technical Support Guides | active |
| Legal Compliance Archive | active |

---

### 17.3 Check domain configs

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT domain_id, llm_route, chunk_size FROM domain_configs;
```
**Expected:** 3 rows — two `api` routes and one `local`:

| llm_route | chunk_size |
|---|---|
| api | 512 |
| api | 512 |
| local | 256 |

---

### 17.4 Check RBAC memberships

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT dr.user_id, dr.role, d.name AS domain_name
FROM domain_roles dr JOIN domains d ON dr.domain_id = d.id;
```
**Expected:** 3 rows:

| user_id | role | domain_name |
|---|---|---|
| manager | domain_admin | HR Policies Portal |
| user | contributor | HR Policies Portal |
| viewer | reader | Technical Support Guides |

---

### 17.5 Check documents

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT id, filename, status FROM documents;
```
**Expected:** 3 rows — 2 with `done`, 1 with `failed`:

| filename | status |
|---|---|
| benefits_guide_2026.pdf | done |
| printer_setup.pdf | done |
| nda_template.pdf | failed |

---

### 17.6 Check chunks have search vectors

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT COUNT(*) AS total_chunks, COUNT(search_vec) AS with_search_vec FROM document_chunks;
```
**Expected:** `total_chunks=3`, `with_search_vec=3`. Both numbers should be equal.

---

### 17.7 Check query logs

🟩 **Run inside psql (domain_db=#):**
```sql
SELECT query, llm_route, model FROM rag_query_logs;
```
**Expected:** 1 row:

| query | llm_route | model |
|---|---|---|
| How many leave days do we get? | api | llama-3.3-70b-versatile |

---

### 17.8 Check all tables exist

🟩 **Run inside psql (domain_db=#):**
```sql
\dt
```
**Expected:** 7 tables listed:

| Name |
|---|
| document_chunks |
| documents |
| domain_configs |
| domain_roles |
| domains |
| rag_query_logs |
| users |

---

### Done! Exit psql

🟩 **Run inside psql (domain_db=#):**
```sql
\q
```

You are back in PowerShell. If all 8 checks passed, your database is fully set up and ready to use.

---

*Last updated: June 2026 — Chatbot-Fixed-Team2 / Fixed Solutions AI Internship*
