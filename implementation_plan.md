# RAG System — Complete Table Fix + UI Overhaul

This plan addresses **6 major areas**: table extraction using Camelot (maximum accuracy), all-format document table handling without double-chunking, a fully functional Documents page UI with multi-view chunk inspector, real user management in the Admin page linked to the database, and real-data Monitoring.

---

## Core Problem: Tables Still Fail

The previous plan correctly diagnosed why tables fail (flat markdown → truncated chunks → wrong cell retrieval). The core fix remains: **convert every table row into a natural-language (NL) description**. The extraction approach uses **Camelot** for maximum table detection accuracy.

**Old approach:** PyMuPDF's `page.find_tables()` + `t.extract()`
**New approach:** **Camelot** (`camelot-py[base]` + Ghostscript) with lattice+stream flavors — reads the **whole table** accurately with row/column structure intact, provides accuracy scores, and exports to DataFrames directly.

Example transformation:
```
Before (Markdown row):
| $200,000 - $249,999 | $17,900 | $19,200 | ... 

After (NL description):
Table: Single or Married Filing Separately (page 4)
Row: Higher Paying Job $200,000–$249,999
- Lower Paying Job $70,000–$79,999: $17,900
- Lower Paying Job $80,000–$89,999: $19,200
```

---

## Decisions Made (from user feedback)

> [!IMPORTANT]
> **Camelot + Ghostscript (Option A)** selected for maximum table accuracy. Requires:
> 1. `pip install camelot-py[base]` added to `requirements.txt`
> 2. Ghostscript system binary installed on the machine (`gswin64c` on Windows)
> This is the most accurate option for ruled/lattice tables like W-4 forms.

> [!IMPORTANT]
> **No document size limits.** CSV/Excel files of any size will be processed via streaming chunked reads (`pd.read_csv(chunksize=500)`). Pages in CSV are tracked via row-range metadata (e.g., "rows 1–500", "rows 501–1000").

> [!IMPORTANT]
> **Real user management** linked to the PostgreSQL `users` table. Full CRUD: create users with ID, name, and role; delete users with cascade to `domain_roles`.

> [!IMPORTANT]
> **Re-indexing required**: All previously ingested documents with tables must be re-processed after these changes. The old `[TABLE]` markdown chunks in Qdrant/PostgreSQL will NOT work with the new retrieval logic.

> [!WARNING]
> **Documents page role access**: `system_admin`, `domain_admin`, and `contributor` (with edit access on the domain) can view all documents and chunks, delete them, and cancel in-progress uploads. `reader` role users will NOT see the Documents page.

---

## Proposed Changes

### Component 1: Table Extraction — Camelot (Max Accuracy) + NL Row Descriptions

The extraction layer is the most critical fix. We use Camelot with Ghostscript for the highest accuracy table detection, and add a shared utility that converts any table into NL row descriptions.

---

#### [NEW] [table_utils.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/worker-service/tasks/table_utils.py)

Shared utility for all extractors. Contains:

1. **`table_to_nl_rows(data, table_title, page_num, source_type) → list[str]`**
   - Takes raw table data (list of lists, where `data[0]` is the header row)
   - For each data row, generates a self-contained NL description:
     ```
     Table: {table_title} (page {page_num})
     Row: {row_header}
     - {col_header_1}: {cell_value_1}
     - {col_header_2}: {cell_value_2}
     ```
   - If the table has no clear row header (first column is data, not a label), uses `Row {i}` as the row identifier
   - Handles merged/empty cells gracefully (skips empty, marks merged as `(same as above)`)

2. **`group_nl_rows(nl_rows, max_tokens=400) → list[str]`**
   - Groups consecutive NL rows into chunks that fit within the embedding model's token window (~512 tokens, target 400)
   - For simple lookup tables: 1 row per chunk
   - For wide data tables: groups until approaching the token budget

3. **`detect_table_title(text_blocks, table_bbox) → str`**
   - Given text blocks on a page and a table's bounding box, finds the nearest heading above the table
   - Falls back to `"Table"` if nothing found

4. **`markdown_table_to_data(md_text) → list[list[str]]`**
   - Parses an existing `[TABLE]...[/TABLE]` markdown block back into a list-of-lists
   - Used by the DOCX extractor which produces markdown tables

5. **`df_to_data(df) → list[list[str]]`**
   - Converts a pandas DataFrame (from Camelot or CSV/Excel) into list-of-lists with headers as first row

---

#### [MODIFY] [extract.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/worker-service/tasks/extract.py)

**PDF extractor (`_extract_pdf`)** — Lines 146–244:

- **Replace `page.find_tables()` + `t.extract()` with Camelot** (max accuracy):
  ```python
  import camelot
  
  # Try lattice first (ruled tables — highest accuracy for tables with borders)
  tables = camelot.read_pdf(file_path, pages=str(page_num+1), flavor='lattice')
  
  # If no tables found or low accuracy, try stream (borderless tables)
  if not tables or all(t.parsing_report['accuracy'] < 50 for t in tables):
      stream_tables = camelot.read_pdf(file_path, pages=str(page_num+1), flavor='stream')
      if stream_tables:
          # Keep whichever set has better accuracy
          if not tables or (stream_tables[0].parsing_report['accuracy'] > 
                           max(t.parsing_report['accuracy'] for t in tables)):
              tables = stream_tables
  ```
- For each Camelot table:
  - Get the DataFrame (`tables[i].df`) and convert to list-of-lists via `df_to_data()`
  - Log accuracy: `tables[i].parsing_report['accuracy']`
  - Skip tables with accuracy < 30% (likely false positives)
  - Call `detect_table_title()` to find the heading above the table using PyMuPDF text blocks
  - Call `table_to_nl_rows()` → `group_nl_rows()` to produce chunking-friendly NL blocks
  - Each NL group becomes a `[TABLE_NL]...[/TABLE_NL]` block
  - **Also keep** the original markdown table as `[TABLE_MD]...[/TABLE_MD]` for BM25 keyword search
  - Remove the old `[TABLE]...[/TABLE]` markers
- **Keep PyMuPDF for text extraction** — only tables switch to Camelot
- **Handle Camelot failure gracefully** — if Ghostscript is unavailable or Camelot crashes, fall back to PyMuPDF `find_tables()` with NL conversion

**DOCX extractor (`_extract_docx`)** — Lines 279–333:

- After `format_docx_table()` produces markdown, parse it back via `markdown_table_to_data()`
- Generate NL descriptions via `table_to_nl_rows()`
- Use the preceding paragraph's text as the table title (if it looks like a heading)
- Same dual output: `[TABLE_NL]` for semantic search + `[TABLE_MD]` for BM25

**CSV extractor (`_extract_csv`)** — Lines 455–486:

- **No size limits — handle any document size** via streaming:
  ```python
  for chunk_df in pd.read_csv(file_path, chunksize=500):
      # Process each 500-row batch
  ```
- **NL descriptions**: For each batch, generate NL row descriptions
- **Adaptive row grouping** (no more hardcoded 25-row pages):
  - Narrow tables (≤ 5 columns): 10 rows per NL chunk
  - Medium tables (6–15 columns): 3 rows per NL chunk
  - Wide tables (> 15 columns): 1 row per NL chunk
- **Page tracking via row ranges**: Each chunk gets metadata like `page: N` where N maps to the row range. The prompt builder already converts this to `rows={start}-{end}` labels for citation.
- Keep the markdown version for BM25

**Excel extractor (`_extract_excel`)** — Lines 409–448:

- Same fixes as CSV: NL descriptions + adaptive row grouping + no size limits
- Process large sheets in 500-row batches via `df.iloc[i:i+500]`
- Streaming approach for large sheets

---

### Component 2: Chunking — Table-Aware Chunk Preservation (No Double-Chunking)

---

#### [MODIFY] [chunk.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/worker-service/tasks/chunk.py)

Current behavior (line 66–85): `re.split` on `[TABLE]...[/TABLE]` → whole table becomes one chunk. **This breaks on large tables AND causes double-chunking if the extractor already pre-chunked.**

**Changes**:

1. **Replace `[TABLE]` regex** with three-way detection:
   - `[TABLE_NL]...[/TABLE_NL]` — NL description blocks → each becomes its **own chunk** (already pre-sized by `group_nl_rows()`). **No semantic splitting** — the extractor already sized these correctly. This prevents double-chunking.
   - `[TABLE_MD]...[/TABLE_MD]` — Raw markdown table → chunked into segments of ~25 rows, **with headers replicated** in every chunk
   - `[TABLE]...[/TABLE]` — Legacy marker (backward compat) → treated as `[TABLE_MD]`

2. **Header replication for markdown chunks**: When splitting a markdown table at row boundaries, prepend the header row + separator to each chunk so every chunk is self-contained.

3. **Add `chunk_type` metadata** to each chunk dict:
   - `"table_nl"` — NL description chunk (primary for vector search)
   - `"table_md"` — Raw markdown chunk (primary for BM25 search)
   - `"text"` — Normal prose text (existing behavior, unchanged)

---

### Component 3: Indexing — Store `chunk_type` Metadata

---

#### [MODIFY] [index.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/worker-service/tasks/index.py)

- Add `chunk_type` to the Qdrant payload (line 128–130): `"chunk_type": chunk.get("chunk_type", "text")`
- Add `chunk_type` column to PostgreSQL `document_chunks` table schema
- Add `chunk_type` to the INSERT statement
- Add `filename` column to PostgreSQL `document_chunks` table (currently missing — only in Qdrant)

#### PostgreSQL Migration

```sql
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS chunk_type TEXT DEFAULT 'text';
ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS filename TEXT DEFAULT '';
```

---

### Component 4: Retrieval — Table-Query Specialized Path

---

#### [MODIFY] [retrieval_pipeline.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/retrieval-service/services/retrieval_pipeline.py)

**Enhanced table query detection** (replace `_is_table_query` / `_TABLE_KEYWORDS`):

- Add salary/currency patterns: `$`, `amount`, `withholding`, `filing`, `earns`, `wage`, `salary`, `rate`, `range`, `bracket`
- Add lookup patterns: `when ... earns`, `for ... and ...`, `intersection`, `cross-reference`, `lookup`
- Keep existing structural keywords: `table`, `row`, `column`, etc.

**Table-query retrieval boost** (new logic in `run()`):

- When `is_table_query` is True:
  - Increase `top_k_retrieve` to `max(top_k_retrieve, 40)` (up from 30)
  - In the table fallback (Stage 5), search for `[TABLE_NL]` instead of `[TABLE]`

#### [MODIFY] [rrf_fusion.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/retrieval-service/services/rrf_fusion.py)

- Update `_is_table_chunk()` to detect new markers: `[TABLE_NL]` or `[TABLE_MD]` or `source_type in ("csv", "xls", "xlsx")`
- **Differentiated boost**: `table_nl` chunks get +0.08, `table_md` chunks get +0.03

#### [MODIFY] [bm25_search.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/retrieval-service/services/bm25_search.py)

- Add `chunk_type` to the SELECT query and the returned `ChunkResult`

#### [MODIFY] [qdrant_search.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/retrieval-service/services/qdrant_search.py)

- Read `chunk_type` from Qdrant payload and include in `ChunkResult`

#### [MODIFY] [retrieval.py (schema)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/retrieval-service/schemas/retrieval.py)

- Add `chunk_type: str = "text"` to `ChunkResult`

---

### Component 5: Generation — Table-Aware Prompting

---

#### [MODIFY] [prompt_builder.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/generation-service/prompt_builder.py)

- Detect when citations contain `[TABLE_NL]` or `[TABLE_MD]` content
- Add a **table-specific instruction** to the system prompt:
  ```
  "Some context includes structured table data. When answering questions
   about table values, carefully match the row header AND column header
   to find the exact cell value. Do not approximate or interpolate."
  ```
- Strip `[TABLE_NL]`, `[/TABLE_NL]`, `[TABLE_MD]`, `[/TABLE_MD]` markers before sending to the LLM

#### [MODIFY] [schemas.py (generation)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/generation-service/schemas.py)

- Add `chunk_type: str = "text"` to `Citation`

---

### Component 6: Documents Page — Full Document & Chunk Management UI

The current [DocumentsPage.tsx](file:///d:/Personal/Fixed Solutions/Project Files/v3/rag-ui/src/pages/DocumentsPage.tsx) only tracks uploads in `sessionStorage`. We need a complete overhaul.

---

#### [MODIFY] [DocumentsPage.tsx](file:///d:/Personal/Fixed Solutions/Project Files/v3/rag-ui/src/pages/DocumentsPage.tsx)

**New features (all roles: system_admin, domain_admin, contributor with domain edit access):**

1. **View all documents in the domain** — Fetch from `GET /domains/{id}/documents` (new endpoint). Shows table with: filename, status, upload date, page count, chunk count, user who uploaded
2. **View chunks for any document — Multi-View Chunk Inspector** — Click the 👁️ (eye) icon to open a chunk inspection panel/modal with **three view modes**:

   | View Mode | What It Shows |
   |-----------|---------------|
   | **Formatted** | Pretty-printed chunk text with syntax highlighting, chunk_type badges (🟢 text, 🔵 table_nl, 🟠 table_md), page numbers, and chunk index |
   | **JSON** | The full chunk object as JSON (exactly what's stored in PostgreSQL), syntax-highlighted and collapsible |
   | **Raw DB** | A table view mimicking the actual `document_chunks` PostgreSQL table columns: `id`, `document_id`, `domain_id`, `page_num`, `chunk_index`, `text`, `source_type`, `chunk_type`, `created_at` |
   | **Plain Text** | Raw text content only — no formatting, no metadata. Just the chunk text as-is for easy copy/paste |
   | **Markdown Preview** | If the chunk contains markdown tables (`[TABLE_MD]`), renders them as actual HTML tables. NL descriptions rendered with structured formatting |

   The view mode is toggled via tabs at the top of the panel. Users can switch between views instantly. All available view modes are shown.

3. **Delete documents** — `DELETE /domains/{id}/documents/{doc_id}` removes the document, its file, all its chunks from Qdrant + PostgreSQL. Confirmation dialog before deletion.
4. **Cancel in-progress uploads** — `POST /ingest/{doc_id}/cancel` stops the Celery task and sets status to `cancelled`. Cancel button (⏹️) appears next to any document in `pending` or `processing` state.
5. **Upload multiple documents** — Already works (the `multiple` attribute is set). Improve UX: show per-file progress, allow queuing multiple files at once for the same domain.
6. **Processed History from DB** — Replace `sessionStorage` with actual API data from `GET /domains/{id}/documents`. Show **all documents ever uploaded** to this domain, not just this session. Paginated if > 50 documents.
7. **Search/filter** — Filter documents by filename, status, or date range.

**Layout redesign:**
```
┌──────────────────────────────────────────────────────────────┐
│  Upload Zone (drag & drop, multi-file)                       │
├──────────────────────────────────────────────────────────────┤
│  Active Queue  [cancel ⏹️ buttons per doc]                   │
├──────────────────────────────────────────────────────────────┤
│  All Documents in Domain  [search 🔍] [filter ▾]            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ doc.pdf    ✅ done   12 chunks  Jan 15  [👁️ View][🗑️] │  │
│  │ data.csv   ⏳ proc   —          Jan 16  [⏹️ Cancel]    │  │
│  │ form.docx  ✅ done   8 chunks   Jan 17  [👁️ View][🗑️] │  │
│  └────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────┤
│  Chunk Inspector (when 👁️ clicked)                           │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ [Formatted] [JSON] [Raw DB]     ← view mode tabs      │  │
│  │                                                        │  │
│  │  Chunk 1/12  🟢 text  Page 1                           │  │
│  │  ┌──────────────────────────────────────────────┐      │  │
│  │  │ The W-4 form is used to determine the...    │      │  │
│  │  └──────────────────────────────────────────────┘      │  │
│  │                                                        │  │
│  │  Chunk 2/12  🔵 table_nl  Page 4                       │  │
│  │  ┌──────────────────────────────────────────────┐      │  │
│  │  │ Table: Single or Married Filing Separately   │      │  │
│  │  │ Row: Higher Paying Job $200,000–$249,999     │      │  │
│  │  │ - Lower Paying Job $70,000–$79,999: $17,900  │      │  │
│  │  │ - Lower Paying Job $80,000–$89,999: $19,200  │      │  │
│  │  └──────────────────────────────────────────────┘      │  │
│  └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

---

#### [MODIFY] [api.ts](file:///d:/Personal/Fixed Solutions/Project Files/v3/rag-ui/src/lib/api.ts)

Add new API methods:

```typescript
export const domainApi = {
  // ... existing methods ...
  documents: (domainId: string) => api.get<any[]>(`/domains/${domainId}/documents`),
  deleteDocument: (domainId: string, docId: string) => 
    api.delete(`/domains/${domainId}/documents/${docId}`),
  documentChunks: (domainId: string, docId: string) => 
    api.get<any[]>(`/domains/${domainId}/documents/${docId}/chunks`),
}

export const ingestApi = {
  // ... existing methods ...
  cancel: (documentId: string) => api.post(`/ingest/${documentId}/cancel`),
}

export const adminApi = {
  listUsers: () => api.get<any[]>('/domains/admin/users'),
  createUser: (data: { id: string; name: string; role: string }) => 
    api.post('/domains/admin/users', data),
  deleteUser: (userId: string) => api.delete(`/domains/admin/users/${userId}`),
}

export const monitoringApi = {
  metrics: () => api.get<any>('/monitoring/metrics'),
}
```

---

### Component 7: Backend APIs for Document & Chunk Management

---

#### [MODIFY] [router.py (domain-service)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/domain-service/router.py)

Add 6 new endpoints:

```python
# --- Documents in a domain ---
@router.get("/{domain_id}/documents")
async def list_documents(domain_id: uuid.UUID, db: DBSession, user: CurrentUser):
    """Lists all documents uploaded to this domain with chunk counts."""
    
@router.delete("/{domain_id}/documents/{document_id}")
async def delete_document(domain_id: uuid.UUID, document_id: str, 
                          db: DBSession, user: CurrentUser):
    """Deletes a document and all its chunks from Qdrant + PostgreSQL + disk."""

@router.get("/{domain_id}/documents/{document_id}/chunks")
async def list_document_chunks(domain_id: uuid.UUID, document_id: str, 
                                db: DBSession, user: CurrentUser):
    """Lists all chunks for a specific document (for the multi-view inspector)."""

# --- Admin user management (real database CRUD) ---
@router.get("/admin/users")
async def list_users(db: DBSession, admin: SystemAdmin):
    """Lists all users from the users table in PostgreSQL."""

@router.post("/admin/users", status_code=201)
async def create_user(payload: UserCreate, db: DBSession, admin: SystemAdmin):
    """Creates a new user in the users table. Linked to real database."""

@router.delete("/admin/users/{user_id}", status_code=204)
async def delete_user(user_id: str, db: DBSession, admin: SystemAdmin):
    """Deletes a user. Cascades to domain_roles (removes all domain memberships)."""
```

#### [MODIFY] [service.py (domain-service)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/domain-service/service.py)

Add service functions:

- **`list_documents(db, domain_id, user)`** — Queries the `documents` table filtered by `domain_id`. Joins with a subquery on `document_chunks` to get `chunk_count` per document. Requires `reader+` role.
- **`delete_document(db, domain_id, document_id, user)`** — Requires `contributor+` role. Deletes:
  1. All chunks from `document_chunks` table (PostgreSQL)
  2. All vectors from Qdrant collection using `document_id` filter
  3. Document row from `documents` table
  4. File from disk (using `file_path` from document record)
- **`list_document_chunks(db, domain_id, document_id, user)`** — Queries `document_chunks` filtered by `document_id`. Returns all columns for the multi-view inspector (JSON/Raw DB views need all fields).
- **`list_users(db)`** — Queries `users` table, returns all users
- **`create_user(db, payload)`** — Inserts into `users` table. Validates that `id` doesn't already exist, `role` is valid.
- **`delete_user(db, user_id)`** — Deletes from `users` table. **Cascades**: also deletes all `domain_roles` entries for this user (so they lose access to all domains).

#### [MODIFY] [schemas.py (domain-service)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/domain-service/schemas.py)

Add schemas:

```python
class DocumentResponse(BaseModel):
    id: str
    domain_id: str
    user_id: str
    filename: str
    status: str
    error_msg: str | None = None
    created_at: datetime
    updated_at: datetime
    chunk_count: int = 0

class ChunkResponse(BaseModel):
    """Full chunk data for the multi-view inspector."""
    id: str
    document_id: str
    domain_id: str
    page_num: int | None = None
    chunk_index: int
    text: str
    chunk_type: str = "text"
    source_type: str = "pdf"
    filename: str = ""
    created_at: datetime | None = None

class UserCreate(BaseModel):
    id: str
    name: str
    role: str  # system_admin | domain_admin | contributor | reader

class UserResponse(BaseModel):
    id: str
    name: str
    role: str
```

---

#### [MODIFY] [ingest.py (ingestion routes)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/ingestion-service/routes/ingest.py)

Add cancel endpoint:

```python
@router.post("/ingest/{document_id}/cancel")
async def cancel_processing(document_id: str, user: CurrentUser):
    """Cancels an in-progress document processing job."""
    doc = await get_document_status(document_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    if doc["status"] not in ("pending", "processing"):
        raise HTTPException(400, f"Cannot cancel — document is already '{doc['status']}'")
    
    # Revoke the Celery task
    task_id = doc.get("task_id")
    if task_id:
        celery_app.control.revoke(task_id, terminate=True, signal='SIGTERM')
    
    # If using subprocess mode, kill via stored PID
    pid = doc.get("pid")
    if pid:
        import signal
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    
    await update_status(document_id, "cancelled")
    return {"document_id": document_id, "status": "cancelled"}
```

This requires storing the Celery `task_id` (or subprocess PID) when enqueuing. Modify `_enqueue_processing()` to return and store the task ID.

#### [MODIFY] [storage.py (ingestion)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/ingestion-service/storage.py)

- Add `task_id` column to `documents` table for Celery task tracking
- Add `update_task_id()` helper function

---

### Component 8: Admin Page — Real User Management (Database-Linked)

---

#### [MODIFY] [AdminPage.tsx](file:///d:/Personal/Fixed Solutions/Project Files/v3/rag-ui/src/pages/AdminPage.tsx)

**Remove ALL mock data.** Replace with real API calls to the PostgreSQL `users` table:

1. **List users** from `GET /domains/admin/users` — shows all users from the real `users` table in the database
2. **Add user** via modal — fields:
   - **User ID** (text input — this is the primary key in the `users` table)
   - **Name** (text input — display name)
   - **Role** (dropdown: `system_admin`, `domain_admin`, `contributor`, `reader`)
   - Creates the user in PostgreSQL via `POST /domains/admin/users`
3. **Delete user** — confirmation dialog ("This will remove the user and revoke all domain memberships"), then `DELETE /domains/admin/users/{user_id}`
   - Backend cascades: deletes all `domain_roles` entries for the user
4. **Keep the existing Domain Catalog section** (create/archive domains)
5. **Show user count** badge next to "User Registry" heading

---

### Component 9: Monitoring Page — Real Data

---

#### [NEW] [monitoring_router.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/gateway/monitoring_router.py)

New FastAPI router that aggregates real metrics from live services:

```python
@router.get("/monitoring/metrics")
async def get_metrics():
    """Aggregates real metrics from all services."""
    return {
        "queue": {
            "depth": await _get_celery_queue_depth(),       # Redis LLEN on celery queue
            "active_workers": await _get_active_workers(),   # Celery inspect.active()
        },
        "retrieval": {
            "vector_latency_ms": await _get_avg_latency("vector"),  # Redis counter
            "bm25_latency_ms": await _get_avg_latency("bm25"),
            "avg_fusion_score": await _get_avg_fusion_score(),
        },
        "cache": {
            "hits": await _get_redis_info("keyspace_hits"),
            "misses": await _get_redis_info("keyspace_misses"),
            "memory_mb": await _get_redis_memory_mb(),
        },
        "llm": {
            "api_requests": await _get_counter("llm:api"),
            "local_requests": await _get_counter("llm:local"),
        },
        "services": {
            "domain": await _health_check("/domains/health"),
            "ingestion": await _health_check("/ingest/health"),
            "generation": await _health_check("/generate/health"),
            "evaluation": await _health_check("/evaluate/health"),
        },
        "documents": {
            "total": await _count_documents(),
            "processing": await _count_documents_by_status("processing"),
            "failed": await _count_documents_by_status("failed"),
        }
    }
```

**Implementation approach**:
- **Queue depth**: `redis.llen("celery")` — reads the actual Celery task queue length
- **Active workers**: `celery_app.control.inspect().active()` — counts active Celery workers
- **Cache stats**: `redis.info("stats")` — reads `keyspace_hits`, `keyspace_misses` from Redis INFO
- **Latency**: Each retrieval call increments `INCR rag:metrics:vector:count` and `INCRBYFLOAT rag:metrics:vector:total_ms` in Redis. The monitoring endpoint computes the average.
- **LLM distribution**: Generation service increments `INCR rag:metrics:llm:api` or `rag:metrics:llm:local` on each request
- **Document counts**: Direct PostgreSQL `SELECT COUNT(*)` on `documents` table

#### [MODIFY] [MonitoringPage.tsx](file:///d:/Personal/Fixed Solutions/Project Files/v3/rag-ui/src/pages/MonitoringPage.tsx)

- Remove **all** `MOCK_METRICS` constants
- Fetch real data from `GET /monitoring/metrics` every 15 seconds via `useQuery` with `refetchInterval`
- Display real values with loading states and error handling
- Add document processing statistics panel (total docs, in-progress, failed)
- Keep the same card-based layout but with live data

---

## Summary of All File Changes

| File | Action | What Changes |
|------|--------|-------------|
| [table_utils.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/worker-service/tasks/table_utils.py) | **NEW** | NL row generator, row grouper, title detector, MD parser, df converter |
| [extract.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/worker-service/tasks/extract.py) | MODIFY | Replace PyMuPDF tables with Camelot+Ghostscript, produce NL + MD dual output, no size limits |
| [chunk.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/worker-service/tasks/chunk.py) | MODIFY | Handle `[TABLE_NL]`/`[TABLE_MD]` markers, header replication, `chunk_type`, no double-chunking |
| [index.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/worker-service/tasks/index.py) | MODIFY | Store `chunk_type` + `filename` in Qdrant + PostgreSQL |
| [retrieval_pipeline.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/retrieval-service/services/retrieval_pipeline.py) | MODIFY | Enhanced table detection, wider retrieval, NL chunk priority |
| [rrf_fusion.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/retrieval-service/services/rrf_fusion.py) | MODIFY | Differentiated boost for `table_nl` vs `table_md` |
| [bm25_search.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/retrieval-service/services/bm25_search.py) | MODIFY | Add `chunk_type` to query results |
| [qdrant_search.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/retrieval-service/services/qdrant_search.py) | MODIFY | Read `chunk_type` from payload |
| [retrieval.py (schema)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/retrieval-service/schemas/retrieval.py) | MODIFY | Add `chunk_type` field to `ChunkResult` |
| [prompt_builder.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/generation-service/prompt_builder.py) | MODIFY | Table-aware system prompt, strip markers |
| [schemas.py (generation)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/generation-service/schemas.py) | MODIFY | Add `chunk_type` to `Citation` |
| [DocumentsPage.tsx](file:///d:/Personal/Fixed Solutions/Project Files/v3/rag-ui/src/pages/DocumentsPage.tsx) | MODIFY | Full document/chunk management UI with multi-view inspector |
| [AdminPage.tsx](file:///d:/Personal/Fixed Solutions/Project Files/v3/rag-ui/src/pages/AdminPage.tsx) | MODIFY | Real user CRUD from database (add/delete), remove all mock data |
| [MonitoringPage.tsx](file:///d:/Personal/Fixed Solutions/Project Files/v3/rag-ui/src/pages/MonitoringPage.tsx) | MODIFY | Replace all mock metrics with real API data |
| [api.ts](file:///d:/Personal/Fixed Solutions/Project Files/v3/rag-ui/src/lib/api.ts) | MODIFY | Add document, chunk, user, monitoring, cancel API methods |
| [router.py (domain)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/domain-service/router.py) | MODIFY | Add document list/delete, chunk list, user CRUD endpoints |
| [service.py (domain)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/domain-service/service.py) | MODIFY | Add document/chunk/user service functions with DB queries |
| [schemas.py (domain)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/domain-service/schemas.py) | MODIFY | Add Document, Chunk, User request/response schemas |
| [ingest.py (routes)](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/ingestion-service/routes/ingest.py) | MODIFY | Add cancel endpoint, store task_id/PID |
| [storage.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/ingestion-service/storage.py) | MODIFY | Add task_id column to documents table |
| [monitoring_router.py](file:///d:/Personal/Fixed Solutions/Project Files/v3/services/gateway/monitoring_router.py) | **NEW** | Real metrics aggregation from Redis/Celery/PostgreSQL |
| [requirements.txt](file:///d:/Personal/Fixed Solutions/Project Files/v3/requirements.txt) | MODIFY | Add `camelot-py[base]` |

---

## Verification Plan

> [!NOTE]
> **Do NOT run these commands** — they are listed here for you to run manually.

### Setup Commands

```bash
# 1. Install Ghostscript (download from https://www.ghostscript.com/releases/gsdnld.html)
# After install, verify:
gswin64c --version

# 2. Install new Python dependency:
pip install camelot-py[base]

# 3. Run database migration (add chunk_type, filename, task_id columns):
python -c "from services.worker_service.tasks.index import _ensure_chunk_table; _ensure_chunk_table()"

# 4. Start the dev server:
cd rag-ui && npm run dev
```

### Manual Verification Steps

1. **Re-index the W-4 PDF** — delete existing document and re-upload via the Documents page
2. **Ask the original question** in Chat: *"Using the Single or Married Filing Separately table, what is the additional withholding amount when the Higher Paying Job earns $200,000–249,999 and the Lower Paying Job earns $70,000–79,999?"*
3. **Test Documents page**:
   - Upload 3 files at once → verify all appear in Active Queue
   - Cancel one mid-processing → verify status changes to `cancelled`
   - View chunks in all 5 modes (Formatted → JSON → Raw DB → Plain Text → Markdown Preview)
   - Delete a document → verify chunks removed from list
4. **Test Admin page**:
   - Verify real users appear from database (no mock data)
   - Create a new user → verify they appear in the list
   - Delete a user → verify they disappear and lose domain access
5. **Test Monitoring page**: Verify real metrics appear (queue depth, cache stats, latencies)
6. **Test with a DOCX** containing tables
7. **Test with a large CSV** (10,000+ rows)
8. **Test with an Excel file** with multiple sheets
