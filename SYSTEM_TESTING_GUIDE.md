# SYSTEM_TESTING_GUIDE

This guide provides technical reference documentation, API details, database structures, role-based matrices, and validation scripts for the multi-user, multi-domain RAG system.

---

## 1. System Architecture & Workflows

### 1.1 Microservice Component Map
The system uses a microservices model split by responsibility boundaries:

1. **API Gateway (Traefik):** Serves as the outer front door, routing HTTP requests to internal endpoints and managing edge security.
2. **Identity Provider (Keycloak):** Performs authentication, role allocation, and JWT token issuance.
3. **Domain Service (Port 8001):** Manages domain creation, domain-level configurations, and membership rosters. Exposes a private inter-service access verification route.
4. **Ingestion Service (Port 8002):** Accepts multipart PDF file uploads, writes metadata to the database, and pushes documents to the async processing queue.
5. **Worker Service (Celery):** Runs in the background, polling the Redis queue to process documents (PyMuPDF text extraction, Tesseract OCR, E5 semantic chunking, indexing).
6. **Retrieval Service (Port 8003):** Implements a three-signal retrieval pipeline (dense vector search, sparse keyword search, Fusion, and Reranking).
7. **Generation Service (Port 8004):** Performs context compilation, prompt assembly, and routes LLM calls (Groq Cloud vs Local Ollama) to stream replies.
8. **Evaluation Service (Port 8005):** Runs a LLM-as-judge scoring script to evaluate the semantic accuracy of generation responses.

```text
                       +-------------------+
                       |    Keycloak OIDC  |
                       +---------+---------+
                                 | JWT
                                 ▼
User / Client ----> Traefik Gateway (:80) 
                         |
      +------------------+------------------+------------------+
      |                  |                  |                  |
      ▼                  ▼                  ▼                  ▼
Domain Service     Ingestion Service   Retrieval Service  Generation Service
  (Port 8001)        (Port 8002)         (Port 8003)        (Port 8004)
      |                  |                  |                  |
      | PostgreSQL       | Redis Queue      | Qdrant DB        | LLM Route
      | (Domains Table)  v                  | & Postgres FTS   | (Groq/Ollama)
      |            Worker Service           |                  |
      |            (Celery Worker)          |                  |
      +------------------+------------------+------------------+
```

### 1.2 Ingestion & Retrieval Pipeline
* **Ingestion Pipeline:**
  1. Frontend uploads a PDF file to `/ingest` passing the domain ID.
  2. Ingestion service writes a status record (`pending`) to the `documents` database table.
  3. Job is pushed to Redis. The Celery worker picks it up and runs `process_document_sync`.
  4. Worker parses the file using PyMuPDF (with Tesseract OCR fallback for scanned pages).
  5. The extracted page text is split into sentences and embedded using `intfloat/multilingual-e5-small` to determine semantic boundaries.
  6. Generated text chunks are embedded as dense vectors with a `"passage: "` prefix and indexed into Qdrant.
  7. The same text chunks are stored in the PostgreSQL `document_chunks` table, generating a `TSVECTOR` index for keyword searching.
  8. Ingestion status is updated to `done` or `failed`.
* **Retrieval Pipeline:**
  1. Generation service calls the Retrieval service (`POST /api/v1/retrieve`).
  2. The input query is embedded with a `"query: "` prefix.
  3. **Dense Search:** Hits the domain's collection in Qdrant (cosine similarity).
  4. **Sparse Search:** Hits the `document_chunks` table in PostgreSQL using a BM25 Full-Text Search rank query.
  5. **Fusion:** Combines result lists using Reciprocal Rank Fusion (RRF) with a decay value of $k=60$.
  6. **Reranking:** Scores the fused list against `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`.
  7. Returns the top-5 reranked text segments as context to the generation engine.

---

## 2. Project Directory Structure

```text
/Chatbot-Fixed-Team2
├── data/                          # local DB volume mount & ML model caches
│   ├── dev/                       # local generated RSA keys for dev JWT token minting
│   ├── qdrant/                    # local embedded vector database storage
│   └── uploads/                   # uploaded raw document storage (partitioned by doc UUID)
├── rag-ui/                        # React Frontend (Vite, TypeScript, Tailwind)
│   ├── src/
│   │   ├── components/            # UI components (sidebar, header, message bubbles)
│   │   ├── pages/                 # View pages (Login, Chat, Documents, Domains, Admin)
│   │   ├── store/                 # Zustand state stores (authentication, domains)
│   │   └── lib/                   # API clients and helpers
│   └── vite.config.ts             # local dev proxies mapping
├── scripts/                       # shared utilities and E2E tests
│   ├── dev_auth.py                # helper to generate custom JWT tokens locally
│   ├── infra_manager.py           # checks and starts local Redis & Keycloak binaries
│   └── qdrant_client_factory.py   # shared Qdrant connection factory
├── services/                      # microservices implementations
│   ├── domain-service/            # domains database, configs, and access controls
│   ├── ingestion-service/         # file uploads and queue triggers
│   ├── worker-service/            # Celery processing pipelines (parsing, embedding, indexing)
│   ├── retrieval-service/         # hybrid search pipelines
│   └── generation-service/        # RAG prompt generation, LLM routing, and caching
└── run_services.py                # developer stack orchestrator
```

---

## 3. Discovered API Registry

All requests passing through the API gateway must include the authorization header:
`Authorization: Bearer <JWT_ACCESS_TOKEN>`

### 3.1 Domain Service (`domain-service` port 8001)

#### `POST /domains/auth/login`
* **Description:** Exposes a public authentication route. Verifies a unique User ID from the database, retrieves the user's role, and returns a signed access token.
* **Payload:** `{"user_id": "string"}`
* **Response (200):** `{"token": "string", "user_id": "string", "username": "string", "role": "string", "roles": ["string"]}`
* **Example curl:**
  ```bash
  curl -X POST http://localhost:8001/domains/auth/login \
    -H "Content-Type: application/json" \
    -d '{"user_id": "admin"}'
  ```

#### `POST /domains`
* **Description:** Creates a new knowledge domain (requires system administrator role).
* **Payload:** `{"name": "string", "description": "string"}`
* **Response (201):** `{"id": "uuid", "name": "string", "description": "string", "status": "active", "created_by": "string", "created_at": "datetime"}`

#### `GET /domains`
* **Description:** Lists domains. Admins see all domains; normal users only see domains where they have an assigned role.
* **Response (200):** Array of domain metadata.

#### `PATCH /domains/{domain_id}/config`
* **Description:** Updates the RAG settings configuration for a domain (requires domain administrator or higher).
* **Payload:** `{"llm_route": "api" | "local", "chunk_size": int, "chunk_overlap": int, "confidence_threshold": float}`
* **Response (200):** Updated domain configuration record.

#### `POST /domains/{domain_id}/members`
* **Description:** Assigns a user role inside a domain.
* **Payload:** `{"user_id": "string", "role": "domain_admin" | "contributor" | "reader"}`
* **Response (201):** Roster membership assignment details.

---

### 3.2 Ingestion Service (`ingestion-service` port 8002)

#### `POST /ingest`
* **Description:** Uploads a PDF document to a domain (requires contributor or higher).
* **Payload (Multipart Form):**
  * `file`: (binary PDF data)
  * `domain_id`: (string UUID)
* **Response (202):** `{"document_id": "uuid", "status": "pending", "message": "Document accepted"}`
* **Example curl:**
  ```bash
  curl -X POST http://localhost:8002/ingest \
    -H "Authorization: Bearer <token>" \
    -F "domain_id=88888888-8888-8888-8888-888888888888" \
    -F "file=@document.pdf"
  ```

#### `GET /ingest/{document_id}`
* **Description:** Polls the queue processing status of a document.
* **Response (200):** `{"document_id": "uuid", "filename": "string", "status": "pending"|"processing"|"done"|"failed", "error_msg": "string", "updated_at": "datetime"}`

---

### 3.3 Generation Service (`generation-service` port 8004)

#### `POST /generate/query`
* **Description:** Submits a query to the RAG system to generate an answer.
* **Payload:**
  ```json
  {
    "query": "What is the policy?",
    "domain_id": "88888888-8888-8888-8888-888888888888",
    "stream": false,
    "top_k_retrieve": 20,
    "top_k_rerank": 5,
    "temperature": 0.2,
    "max_tokens": 512
  }
  ```
* **Response (200, stream=false):**
  ```json
  {
    "answer": "Summarized text...",
    "citations": [
      {
        "chunk_id": "chunk_uuid",
        "document_id": "doc_uuid",
        "page": 2,
        "score": 0.82,
        "text": "Extracted context..."
      }
    ],
    "cache_hit": false,
    "llm_route": "api",
    "model": "llama-3.3-70b-versatile"
  }
  ```

---

## 4. Database Schema & Models

The system database runs on PostgreSQL. The database maps entity models across microservice boundaries.

```text
┌────────────────┐      1:1      ┌─────────────────┐
│    domains     ├───────────────►│ domain_configs  │
└───────┬────────┘               └─────────────────┘
        │
        │ 1:N
        ▼
┌───────────────┐
│ domain_roles  │
└───────────────┘

┌───────────────┐
│     users     │
└───────────────┘

┌───────────────┐      1:N       ┌─────────────────┐
│   documents   ├───────────────►│ document_chunks │
└───────────────┘               └─────────────────┘

┌──────────────────┐
│  rag_query_logs  │
└──────────────────┘
```

### 4.1 Table Descriptions

#### 1. `users`
* **Purpose:** Stores user profiles, unique login IDs, and global permissions roles.
* **Columns:**
  * `id` (`VARCHAR(255)`, PK): Unique login ID.
  * `name` (`VARCHAR(255)`): Full display name.
  * `role` (`VARCHAR(255)`): Global system role (`system_admin`, `domain_admin`, `contributor`, `reader`, `unauthorized`).

#### 2. `domains`
* **Purpose:** Isolation workspaces for indexing documents.
* **Columns:**
  * `id` (`UUID`, PK)
  * `name` (`VARCHAR(255)`, Unique, Index)
  * `description` (`TEXT`)
  * `status` (`domain_status` Enum: `'active'`, `'archived'`)
  * `created_by` (`VARCHAR(255)`)
  * `created_at` (`TIMESTAMPTZ`)

#### 3. `domain_configs`
* **Purpose:** Custom RAG settings for each domain.
* **Columns:**
  * `id` (`UUID`, PK)
  * `domain_id` (`UUID`, FK -> `domains.id` ON DELETE CASCADE, Unique, Index)
  * `llm_route` (`VARCHAR(255)`): Cloud (`'api'`) or local (`'local'`).
  * `chunk_size` (`INTEGER`): Defaults to `512`.
  * `chunk_overlap` (`INTEGER`): Defaults to `64`.
  * `confidence_threshold` (`FLOAT`): Minimum relevance score filter.

#### 4. `domain_roles`
* **Purpose:** Domain-level permission mappings.
* **Columns:**
  * `id` (`UUID`, PK)
  * `domain_id` (`UUID`, FK -> `domains.id` ON DELETE CASCADE, Index)
  * `user_id` (`VARCHAR(255)`, Index): References user mapping.
  * `role` (`domain_role_enum` Enum: `'domain_admin'`, `'contributor'`, `'reader'`)
  * **Constraints:** Unique index `uq_domain_user` on (`domain_id`, `user_id`).

#### 5. `documents`
* **Purpose:** Tracks raw uploaded files.
* **Columns:**
  * `id` (`VARCHAR(255)`, PK): Document UUID.
  * `domain_id` (`VARCHAR(255)`): Target workspace context.
  * `user_id` (`VARCHAR(255)`): Uploading user identifier.
  * `filename` (`VARCHAR(255)`)
  * `file_path` (`VARCHAR(255)`): Disk location.
  * `status` (`VARCHAR(255)`): Processing state (`'pending'`, `'processing'`, `'done'`, `'failed'`).
  * `error_msg` (`TEXT`): Error log if parsing failed.

#### 6. `document_chunks`
* **Purpose:** Searchable segments extracted from documents.
* **Columns:**
  * `id` (`TEXT`, PK): Segment UUID.
  * `document_id` (`TEXT`, Index): Associated document.
  * `domain_id` (`TEXT`, Index): Associated domain.
  * `page_num` (`INTEGER`): PDF source page index.
  * `chunk_index` (`INTEGER`): Extraction counter.
  * `text` (`TEXT`): Text content.
  * `search_vec` (`TSVECTOR`, Index GIN): Text search index for BM25.

#### 7. `rag_query_logs`
* **Purpose:** Audits queries and responses.
* **Columns:**
  * `id` (`BIGSERIAL`, PK)
  * `domain_id` (`TEXT`)
  * `user_id` (`TEXT`)
  * `query` (`TEXT`)
  * `answer` (`TEXT`)
  * `llm_route` (`TEXT`)
  * `model` (`TEXT`)
  * `created_at` (`TIMESTAMPTZ`)

---

## 5. Complete Testing Seed Data (SQL Inserts)

Execute the following queries on your local PostgreSQL database (`domain_db`) to create test records:

```sql
-- 5.1 Clear Existing Entries
DELETE FROM rag_query_logs;
DELETE FROM document_chunks;
DELETE FROM documents;
DELETE FROM domain_roles;
DELETE FROM domain_configs;
DELETE FROM domains;
DELETE FROM users;

-- 5.2 Insert Default Users
-- Supports ID-based login and maps user IDs directly to system roles.
INSERT INTO users (id, name, role) VALUES
('admin', 'System Admin User', 'system_admin'),
('652ec45e-1b68-478c-9bd3-81cc46fb24a9', 'System Admin User (UUID)', 'system_admin'),
('manager', 'Domain Manager User', 'domain_admin'),
('user', 'Regular Contributor User', 'contributor'),
('viewer', 'Read-Only Viewer', 'reader'),
('unauth', 'Unauthorized Hacker', 'unauthorized');

-- 5.3 Insert Isolated Knowledge Domains
INSERT INTO domains (id, name, description, status, created_by, created_at, updated_at) VALUES
('11111111-1111-1111-1111-111111111111', 'HR Policies Portal', 'Internal employee handbooks, travel guidance, and benefits details.', 'active', 'admin', NOW(), NOW()),
('22222222-2222-2222-2222-222222222222', 'Technical Support Guides', 'Hardware specs, troubleshooting steps, and customer support manuals.', 'active', 'admin', NOW(), NOW()),
('33333333-3333-3333-3333-333333333333', 'Legal Compliance Archive', 'Contracts, NDAs, and platform terms of service.', 'active', 'admin', NOW(), NOW());

-- 5.4 Insert Domain Configurations
INSERT INTO domain_configs (id, domain_id, llm_route, chunk_size, chunk_overlap, confidence_threshold, extra_settings, updated_at) VALUES
('11111111-1111-1111-1111-111111111100', '11111111-1111-1111-1111-111111111111', 'api', 512, 64, 0.50, '{}', NOW()),
('22222222-2222-2222-2222-222222222200', '22222222-2222-2222-2222-222222222222', 'api', 512, 64, 0.40, '{}', NOW()),
('33333333-3333-3333-3333-333333333300', '33333333-3333-3333-3333-333333333333', 'local', 256, 32, 0.60, '{}', NOW());

-- 5.5 Insert Domain Roster Permissions (RBAC)
INSERT INTO domain_roles (id, domain_id, user_id, role, assigned_by, assigned_at) VALUES
('11111111-1111-1111-1111-111111111101', '11111111-1111-1111-1111-111111111111', 'manager', 'domain_admin', 'admin', NOW()),
('11111111-1111-1111-1111-111111111102', '11111111-1111-1111-1111-111111111111', 'user', 'contributor', 'manager', NOW()),
('22222222-2222-2222-2222-222222222201', '22222222-2222-2222-2222-222222222222', 'viewer', 'reader', 'admin', NOW());

-- 5.6 Insert Mock Uploaded Documents
INSERT INTO documents (id, domain_id, user_id, filename, file_path, status, error_msg, created_at, updated_at) VALUES
('aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 'user', 'benefits_guide_2026.pdf', 'data/uploads/aaaa-bbbb-cccc-0001/benefits_guide_2026.pdf', 'done', NULL, NOW(), NOW()),
('aaaa-bbbb-cccc-0002', '22222222-2222-2222-2222-222222222222', 'manager', 'printer_setup.pdf', 'data/uploads/aaaa-bbbb-cccc-0002/printer_setup.pdf', 'done', NULL, NOW(), NOW()),
('aaaa-bbbb-cccc-0003', '33333333-3333-3333-3333-333333333333', 'admin', 'nda_template.pdf', 'data/uploads/aaaa-bbbb-cccc-0003/nda_template.pdf', 'failed', 'Tesseract OCR failed to parse scanned text.', NOW(), NOW());

-- 5.7 Insert Indexed Document Chunks (Enables immediate search queries)
INSERT INTO document_chunks (id, document_id, domain_id, page_num, chunk_index, text, search_vec, created_at) VALUES
('chunk-uuid-001', 'aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 1, 0, 'Employees receive 25 days of paid annual vacation leave, matching state mandates.', to_tsvector('simple', 'Employees receive 25 days of paid annual vacation leave, matching state mandates.'), NOW()),
('chunk-uuid-002', 'aaaa-bbbb-cccc-0001', '11111111-1111-1111-1111-111111111111', 2, 1, 'Medical insurance cover includes full dental services and optical checkups up to $1000.', to_tsvector('simple', 'Medical insurance cover includes full dental services and optical checkups up to $1000.'), NOW()),
('chunk-uuid-003', 'aaaa-bbbb-cccc-0002', '22222222-2222-2222-2222-222222222222', 1, 0, 'To resolve network issues on LaserJet printers, restore factory settings in admin panel.', to_tsvector('simple', 'To resolve network issues on LaserJet printers, restore factory settings in admin panel.'), NOW());

-- 5.8 Insert Audit Query Logs
INSERT INTO rag_query_logs (domain_id, user_id, query, answer, llm_route, model, created_at) VALUES
('11111111-1111-1111-1111-111111111111', 'user', 'How many leave days do we get?', 'Employees are entitled to 25 days of paid annual leave.', 'api', 'llama-3.3-70b-versatile', NOW());
```

---

## 6. Detailed RBAC Matrix

The system validates authorization checks on every service-to-service call. A user must hold both the required global role and domain permission.

| User Role (Primary Context) | Gateway Endpoint / Operation | HTTP Method | Expected Status Code | Access State |
|:---|:---|:---:|:---:|:---|
| **System Admin** (`admin`) | `POST /domains` (Create Domain) | POST | **201 Created** | Allowed |
| **System Admin** (`admin`) | `PATCH /domains/{id}/config` (Change Config) | PATCH | **200 OK** | Allowed (bypasses check) |
| **Domain Admin** (`manager`) | `POST /domains` (Create Domain) | POST | **403 Forbidden** | Denied |
| **Domain Admin** (`manager`) | `PATCH /domains/{id}/config` (Change Config) | PATCH | **200 OK** | Allowed on assigned domain |
| **Contributor** (`user`) | `POST /ingest` (Upload Document) | POST | **202 Accepted** | Allowed on assigned domain |
| **Contributor** (`user`) | `PATCH /domains/{id}/config` (Change Config) | PATCH | **403 Forbidden** | Denied |
| **Viewer** (`viewer`) | `POST /generate/query` (Query Domain) | POST | **200 OK** | Allowed on assigned domain |
| **Viewer** (`viewer`) | `POST /ingest` (Upload Document) | POST | **403 Forbidden** | Denied |
| **Unauthorized** (`unauth`) | `POST /generate/query` (Query Domain) | POST | **401 Unauthorized** | Denied (no valid claims) |

---

## 7. End-to-End Testing Workflows

Follow these E2E testing workflows to verify that the platform functions correctly.

### 7.1: Authenticate with User ID
Authenticate using the custom login endpoint to retrieve a token:
```bash
curl -X POST http://localhost:8001/domains/auth/login \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user"}'
```
* **Expected Output:**
  ```json
  {"token": "eyJhbGci...", "user_id": "user", "username": "regular_user", "role": "contributor", "roles": ["contributor", "reader"]}
  ```

### 7.2: Create a New Domain
Submit a request using the admin token:
```bash
curl -X POST http://localhost:8001/domains \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Customer Support KB", "description": "Customer policy resources"}'
```
* **Expected Output:** Status code `201 Created`, returning the new domain UUID.

### 7.3: Upload and Process a PDF Document
Upload a PDF file using the contributor token:
```bash
curl -X POST http://localhost:8002/ingest \
  -H "Authorization: Bearer <contributor_token>" \
  -F "domain_id=11111111-1111-1111-1111-111111111111" \
  -F "file=@benefits_guide.pdf"
```
* **Expected Output:** Status code `202 Accepted`, returning `document_id`.
* **Database Verification Query:**
  ```sql
  SELECT status, error_msg FROM documents WHERE id = 'YOUR_DOCUMENT_ID';
  ```
  Wait until the status transitions to `done`.

### 7.4: Submit RAG Query
Ask a question about the uploaded document:
```bash
curl -X POST http://localhost:8004/generate/query \
  -H "Authorization: Bearer <contributor_token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "How many dental benefits do employees get?", "domain_id": "11111111-1111-1111-1111-111111111111", "stream": false}'
```
* **Expected Output:** Code `200 OK`, returning the grounded answer, matching citation objects, and the LLM model route used.

---

## 8. Database Validation Queries

Run these SQL verification queries inside `psql` to check database consistency.

### 8.1: Check Database User Profiles
```sql
SELECT id, name, role FROM users;
```
* **Success Criteria:** Returns all seeded users (`admin`, `manager`, `user`, `viewer`, `unauth`) and their matching roles.

### 8.2: Audit Document Statuses
```sql
SELECT id, domain_id, filename, status, error_msg FROM documents;
```
* **Success Criteria:** Shows if uploaded documents successfully finished parsing (`done`) or failed (`failed`).

### 8.3: Inspect Chunk Extraction Results
```sql
SELECT document_id, page_num, chunk_index, SUBSTRING(text, 1, 50) AS snippet FROM document_chunks;
```
* **Success Criteria:** Confirms that documents were split into text chunks.

### 8.4: Verify Full-Text Search Vector Status
```sql
SELECT id, search_vec FROM document_chunks WHERE search_vec IS NULL;
```
* **Success Criteria:** Returns 0 rows. (All chunks should have search vectors).

---

# DATA_INSERTION_GUIDE

Use these statements to seed the RAG database for testing.

### 1. Seed Users (SQL)
```sql
INSERT INTO users (id, name, role) VALUES 
('admin', 'System Admin', 'system_admin'),
('manager', 'Domain Manager', 'domain_admin'),
('user', 'Regular Contributor', 'contributor'),
('viewer', 'Viewer Reader', 'reader');
```

### 2. Create Domain (curl)
```bash
curl -X POST http://localhost:8001/domains \
  -H "Authorization: Bearer <ADMIN_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"name": "Product Development Guidelines", "description": "Core product standards"}'
```

### 3. Assign Domain Member (curl)
```bash
curl -X POST http://localhost:8001/domains/11111111-1111-1111-1111-111111111111/members \
  -H "Authorization: Bearer <MANAGER_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user", "role": "contributor"}'
```

---

# ROLE_TESTING_GUIDE

### User: `admin` (System Administrator)
* **Allowed Actions:** All APIs. (Can manage all settings, view monitoring charts, create domains, upload docs, check queues).
* **Verify Command:**
  ```bash
  # Attempt to create a domain: Should succeed
  curl -I -X POST http://localhost:8001/domains -H "Authorization: Bearer <admin_token>" -H "Content-Type: application/json" -d '{"name": "Test Domain", "description": "Desc"}'
  ```

### User: `manager` (Domain Administrator)
* **Allowed Actions:** Can modify configuration sliders and append members on assigned domains.
* **Denied Actions:** Cannot create new domains globally.
* **Verify Command:**
  ```bash
  # Attempt to create a domain: Should fail with 403
  curl -I -X POST http://localhost:8001/domains -H "Authorization: Bearer <manager_token>" -H "Content-Type: application/json" -d '{"name": "Fail Domain", "description": "Desc"}'
  ```

### User: `user` (Contributor)
* **Allowed Actions:** Can upload PDF files to assigned domains.
* **Denied Actions:** Cannot modify chunk sizes or threshold configuration parameters.
* **Verify Command:**
  ```bash
  # Attempt to modify chunk size configuration: Should fail with 403
  curl -I -X PATCH http://localhost:8001/domains/11111111-1111-1111-1111-111111111111/config -H "Authorization: Bearer <contributor_token>" -H "Content-Type: application/json" -d '{"chunk_size": 256}'
  ```

---

# QUICK_SMOKE_TEST_GUIDE

Verify that the stack is running properly by calling the endpoints below:

```bash
# 1. Verify Domain Service
curl http://localhost:8001/health

# 2. Login as Admin
curl -X POST http://localhost:8001/domains/auth/login -H "Content-Type: application/json" -d '{"user_id": "admin"}'

# 3. Verify Generation Pipeline Response
curl -X POST http://localhost:8004/generate/query \
  -H "Authorization: Bearer <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "vacation leave", "domain_id": "11111111-1111-1111-1111-111111111111", "stream": false}'
```
All calls should return status code `200` with valid JSON payloads.
