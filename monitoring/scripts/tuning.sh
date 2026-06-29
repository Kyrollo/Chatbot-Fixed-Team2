#!/usr/bin/env bash
# =============================================================================
# tuning.sh — Bottleneck Fix Commands for the RAG System Load Test
# =============================================================================
#
# PURPOSE
#   Ready-to-run commands for every common bottleneck found during load testing.
#   Do NOT run this entire script blindly — read each section and run only
#   the commands that match the bottleneck you observed in Grafana.
#
# HOW TO USE
#   1. Observe Grafana during the Locust load test
#   2. Identify the bottleneck from the warning signs below
#   3. Jump to the matching section and run those commands
#   4. Re-run the load test and compare against baseline
#
# SECTIONS
#   A. PostgreSQL Connection Pool Exhaustion
#   B. Redis Memory Limit / Eviction
#   C. Worker Process Count (Uvicorn / Celery)
#   D. Slow Queries — EXPLAIN ANALYZE
#   E. Missing Indexes
#
# REQUIREMENTS
#   - psql in PATH (or psql.exe on Windows)
#   - redis-cli in PATH
#   - Python / pip in PATH
#   - Services running: python run_services.py --worker
# =============================================================================

# ── Connection settings (reads from env or uses defaults) ─────────────────────
PG_HOST="${POSTGRES_HOST:-localhost}"
PG_PORT="${POSTGRES_PORT:-5434}"
PG_USER="${POSTGRES_USER:-postgres}"
PG_DB="${POSTGRES_DB:-domain_db}"
PG_PASSWORD="${POSTGRES_PASSWORD:-1234}"
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# Locate redis-cli (prioritize PATH, fallback to local tools directory)
REDIS_CLI_CMD=""
if command -v redis-cli &>/dev/null; then
  REDIS_CLI_CMD="redis-cli"
elif command -v redis-cli.exe &>/dev/null; then
  REDIS_CLI_CMD="redis-cli.exe"
elif [[ -f "${PROJECT_ROOT}/tools/redis/redis-cli.exe" ]]; then
  REDIS_CLI_CMD="${PROJECT_ROOT}/tools/redis/redis-cli.exe"
elif [[ -f "${PROJECT_ROOT}/tools/redis/redis-cli" ]]; then
  REDIS_CLI_CMD="${PROJECT_ROOT}/tools/redis/redis-cli"
fi

export PGPASSWORD="${PG_PASSWORD}"

echo "=============================================="
echo "  RAG System — Tuning & Bottleneck Fix Menu"
echo "=============================================="
echo ""
echo "  WARNING SIGNS (from Grafana):"
echo "  A. pg_stat_activity count near max_connections  → Section A"
echo "  B. Redis used_memory near maxmemory, evictions  → Section B"
echo "  C. p95 response time > 3 s, high CPU on services → Section C"
echo "  D. Specific endpoints slow (generate/query)       → Section D"
echo "  E. Grafana shows sequential scans on DB           → Section E"
echo ""
echo "  Run individual sections with: bash tuning.sh <A|B|C|D|E>"
echo ""

SECTION="${1:-MENU}"

# =============================================================================
# SECTION A — Fix PostgreSQL Connection Pool Exhaustion
# =============================================================================
# WARNING SIGNS IN GRAFANA:
#   - Panel "DB Connections" approaching max_connections (default: 100)
#   - "too many connections" errors in service logs
#   - pg_stat_activity shows many "idle in transaction" rows
# =============================================================================
run_section_A() {
  echo "======================================================"
  echo "  SECTION A — Fix PostgreSQL Connection Pool Exhaustion"
  echo "======================================================"
  echo ""

  # A1 — Diagnose: see current connection state breakdown
  echo "[A1] Current connection breakdown by state:"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    SELECT state,
           count(*)                                      AS connections,
           round(count(*) * 100.0 / current_setting('max_connections')::int, 1) AS pct_of_max,
           max(now() - state_change)                     AS longest_in_state
    FROM pg_stat_activity
    GROUP BY state
    ORDER BY connections DESC;
  "

  # A2 — Diagnose: see max_connections setting
  echo ""
  echo "[A2] Current max_connections:"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" \
    -c "SHOW max_connections;"

  # A3 — Fix: terminate idle connections that have been open > 5 minutes
  echo ""
  echo "[A3] Terminate idle connections older than 5 minutes (safe to run):"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE state = 'idle'
      AND state_change < now() - interval '5 minutes'
      AND pid <> pg_backend_pid();
  "

  # A4 — Fix: terminate connections stuck in 'idle in transaction' > 2 minutes
  echo ""
  echo "[A4] Terminate 'idle in transaction' connections older than 2 minutes:"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    SELECT pg_terminate_backend(pid)
    FROM pg_stat_activity
    WHERE state = 'idle in transaction'
      AND state_change < now() - interval '2 minutes'
      AND pid <> pg_backend_pid();
  "

  # A5 — Fix: increase max_connections in postgresql.conf (persistent)
  echo ""
  echo "[A5] Increase max_connections to 200 (requires PostgreSQL restart):"
  echo "     Run these manually in psql as a superuser:"
  echo ""
  echo "       ALTER SYSTEM SET max_connections = 200;"
  echo "       SELECT pg_reload_conf();"
  echo "       -- OR for full restart on Windows:"
  echo "       -- net stop postgresql-x64-17 && net start postgresql-x64-17"
  echo ""
  echo "     NOTE: Verify with: SHOW max_connections;  after restart"

  # A6 — Fix: reduce SQLAlchemy pool_size in each service's db.py
  echo ""
  echo "[A6] Recommended SQLAlchemy pool settings per service (edit each service's db.py):"
  echo ""
  cat <<'POOL_CONFIG'
  # In services/<service-name>/database.py or db.py, adjust:
  engine = create_async_engine(
      DATABASE_URL,
      pool_size=5,          # was 10+ — reduce to 5 per service
      max_overflow=10,      # allow 10 extra burst connections
      pool_timeout=30,      # wait 30s before failing
      pool_recycle=1800,    # recycle connections every 30 min (prevents stale)
      pool_pre_ping=True,   # verify connection health before use
  )
POOL_CONFIG

  # A7 — Nuclear: reset all non-superuser connections (use in emergency)
  echo ""
  echo "[A7] EMERGENCY: Kill ALL non-superuser connections to domain_db:"
  echo "     (Only run this if the system is completely hung)"
  echo ""
  echo "       psql -h ${PG_HOST} -p ${PG_PORT} -U ${PG_USER} -d postgres -c \\"
  echo "         \"SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
  echo "          WHERE datname = '${PG_DB}' AND pid <> pg_backend_pid();\""
}

# =============================================================================
# SECTION B — Fix Redis Memory Limit / Eviction
# =============================================================================
# WARNING SIGNS IN GRAFANA:
#   - Panel "Redis Memory" near or at maxmemory limit
#   - evicted_keys counter climbing
#   - Cache hit rate dropping (cache misses increase)
#   - Grafana shows "used_memory > maxmemory" alerts
# =============================================================================
run_section_B() {
  echo "======================================================"
  echo "  SECTION B — Fix Redis Memory Limit / Eviction"
  echo "======================================================"
  echo ""

  if [[ -z "${REDIS_CLI_CMD}" ]]; then
    echo "  ERROR: redis-cli not found in PATH or local tools folder."
    echo "  Please install redis-cli or configure PATH before running Section B."
    return 1
  fi

  # B1 — Diagnose: current memory usage
  echo "[B1] Current Redis memory stats:"
  "${REDIS_CLI_CMD}" -h "${REDIS_HOST}" -p "${REDIS_PORT}" INFO memory | \
    grep -E "(used_memory_human|used_memory_peak_human|maxmemory_human|maxmemory_policy|mem_fragmentation_ratio|evicted_keys)"

  # B2 — Diagnose: eviction stats
  echo ""
  echo "[B2] Eviction and hit/miss stats:"
  "${REDIS_CLI_CMD}" -h "${REDIS_HOST}" -p "${REDIS_PORT}" INFO stats | \
    grep -E "(evicted_keys|keyspace_hits|keyspace_misses|expired_keys)"

  # B3 — Diagnose: key count and biggest key consumers
  echo ""
  echo "[B3] Key count and database size:"
  "${REDIS_CLI_CMD}" -h "${REDIS_HOST}" -p "${REDIS_PORT}" DBSIZE

  # B4 — Fix: increase maxmemory to 512 MB (live, no restart needed)
  echo ""
  echo "[B4] Increase Redis maxmemory to 512 MB (live change — no restart):"
  "${REDIS_CLI_CMD}" -h "${REDIS_HOST}" -p "${REDIS_PORT}" CONFIG SET maxmemory 536870912
  echo "     Verify: redis-cli CONFIG GET maxmemory"
  "${REDIS_CLI_CMD}" -h "${REDIS_HOST}" -p "${REDIS_PORT}" CONFIG GET maxmemory

  # B5 — Fix: change eviction policy to allkeys-lru (safer than noeviction)
  echo ""
  echo "[B5] Set eviction policy to allkeys-lru (evict least-recently-used keys when full):"
  "${REDIS_CLI_CMD}" -h "${REDIS_HOST}" -p "${REDIS_PORT}" CONFIG SET maxmemory-policy allkeys-lru
  echo "     Verify: redis-cli CONFIG GET maxmemory-policy"
  "${REDIS_CLI_CMD}" -h "${REDIS_HOST}" -p "${REDIS_PORT}" CONFIG GET maxmemory-policy

  # B6 — Fix: make memory setting persistent across restarts
  echo ""
  echo "[B6] Make memory limit persistent (writes to redis.conf):"
  "${REDIS_CLI_CMD}" -h "${REDIS_HOST}" -p "${REDIS_PORT}" CONFIG REWRITE 2>/dev/null && \
    echo "     CONFIG REWRITE succeeded." || \
    echo "     CONFIG REWRITE failed — add manually to redis.conf:"
  cat <<'REDIS_CONF'
  # Add to redis.conf (usually /etc/redis/redis.conf or tools/redis/redis.conf):
  maxmemory 512mb
  maxmemory-policy allkeys-lru
REDIS_CONF

  # B7 — Fix: flush only retrieval/answer cache keys (preserve Celery queue)
  echo ""
  echo "[B7] Flush only RAG cache keys (pattern-based, preserves Celery tasks):"
  echo "     Retrieval cache keys start with 'retrieval:' — flush them:"
  RETRIEVAL_KEYS=$("${REDIS_CLI_CMD}" -h "${REDIS_HOST}" -p "${REDIS_PORT}" --scan --pattern "retrieval:*" | wc -l)
  echo "     Found ${RETRIEVAL_KEYS} retrieval cache keys."
  echo ""
  echo "     To flush them (run manually after confirming count):"
  echo "       redis-cli --scan --pattern 'retrieval:*' | xargs redis-cli DEL"
  echo "       redis-cli --scan --pattern 'generation:*' | xargs redis-cli DEL"
  echo ""
  echo "     NEVER run FLUSHALL during a load test — it destroys the Celery queue."

  # B8 — Fix: set TTL on keys that lack expiry
  echo ""
  echo "[B8] Check for keys with no TTL (persistent keys):"
  echo "     Run in redis-cli:"
  echo "       redis-cli --scan | xargs -L 1 redis-cli OBJECT ENCODING | head -20"
  echo "     To set TTL on retrieval cache keys (1 hour = 3600 seconds):"
  echo "       redis-cli --scan --pattern 'retrieval:*' | while read k; do"
  echo "         redis-cli EXPIRE \"\$k\" 3600; done"
}

# =============================================================================
# SECTION C — Increase Worker Processes (Uvicorn / Celery)
# =============================================================================
# WARNING SIGNS IN GRAFANA:
#   - p95 latency high but CPU is NOT maxed out (workers are the bottleneck)
#   - Grafana shows requests queuing (concurrent requests > workers)
#   - generation-service or retrieval-service is the slowest endpoint
# =============================================================================
run_section_C() {
  echo "======================================================"
  echo "  SECTION C — Increase Worker Processes"
  echo "======================================================"
  echo ""

  # C1 — Diagnose: how many uvicorn workers are running per service
  echo "[C1] Current Uvicorn processes per service:"
  ps aux 2>/dev/null | grep uvicorn | grep -v grep | awk '{print $0}' || \
    tasklist 2>/dev/null | grep -i python || \
    echo "     Run: ps aux | grep uvicorn"

  # C2 — Fix: restart services with more Uvicorn workers
  echo ""
  echo "[C2] Restart services with increased Uvicorn worker count:"
  echo "     Current default: 1 worker per service (--workers 1)"
  echo ""
  echo "     For generation-service (CPU-bound LLM calls — more workers help):"
  echo "       cd services/generation-service"
  echo "       uvicorn main:app --host 0.0.0.0 --port 8004 --workers 4"
  echo ""
  echo "     For retrieval-service (I/O-bound — async handles concurrency well):"
  echo "       cd services/retrieval-service"
  echo "       uvicorn main:app --host 0.0.0.0 --port 8003 --workers 2"
  echo ""
  echo "     Or via run_services.py flags — edit UVICORN_WORKERS in run_services.py:"
  cat <<'WORKERS_CONFIG'
  # In run_services.py, change the uvicorn launch command:
  # BEFORE:
  #   cmd = ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(port)]
  # AFTER:
  #   cmd = ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", str(port), "--workers", "4"]
  #
  # Rule of thumb: workers = (2 × CPU cores) + 1
  # For 4-core machine: --workers 9
  # For 2-core machine: --workers 5
WORKERS_CONFIG

  # C3 — Fix: increase Celery worker concurrency (for ingestion throughput)
  echo ""
  echo "[C3] Restart Celery worker with higher concurrency:"
  echo "     Current default: --pool=solo (1 task at a time, required on Windows)"
  echo ""
  echo "     On Linux/WSL2 (can use prefork):"
  echo "       cd services/worker-service"
  echo "       celery -A celery_app worker --concurrency=4 --loglevel=info"
  echo ""
  echo "     On Windows (must stay solo — solo is single-threaded by design):"
  echo "       celery -A celery_app worker --pool=solo --loglevel=info"
  echo "     NOTE: On Windows, parallelism requires multiple worker processes."
  echo "       Run multiple terminals each with: celery -A celery_app worker --pool=solo"

  # C4 — Fix: enable async/await properly (check for blocking calls)
  echo ""
  echo "[C4] Check for blocking synchronous calls in async endpoints:"
  echo "     Look for these patterns in service code that block the event loop:"
  cat <<'ASYNC_CHECK'
  # BAD — blocks the event loop:
  time.sleep(x)
  requests.get(...)          # use httpx.AsyncClient instead
  open(file).read()          # use aiofiles instead

  # GOOD — non-blocking:
  await asyncio.sleep(x)
  async with httpx.AsyncClient() as client:
      resp = await client.get(...)
ASYNC_CHECK
}

# =============================================================================
# SECTION D — Find Slow Queries with EXPLAIN ANALYZE
# =============================================================================
# WARNING SIGNS IN GRAFANA:
#   - Panel "Query Duration" shows high p95 for specific DB queries
#   - "slow query log" entries in PostgreSQL logs
#   - generate/query or retrieval endpoints consistently slow
# =============================================================================
run_section_D() {
  echo "======================================================"
  echo "  SECTION D — Find Slow Queries with EXPLAIN ANALYZE"
  echo "======================================================"
  echo ""

  # D1 — Find currently running queries > 5 seconds
  echo "[D1] Queries currently running for more than 5 seconds:"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    SELECT pid,
           now() - pg_stat_activity.query_start AS duration,
           state,
           left(query, 120) AS query_snippet
    FROM pg_stat_activity
    WHERE state != 'idle'
      AND query_start IS NOT NULL
      AND now() - query_start > interval '5 seconds'
    ORDER BY duration DESC;
  "

  # D2 — Find the top 10 slowest queries historically (requires pg_stat_statements)
  echo ""
  echo "[D2] Top 10 slowest queries (requires pg_stat_statements extension):"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    SELECT round(mean_exec_time::numeric, 2) AS avg_ms,
           calls,
           round(total_exec_time::numeric, 2) AS total_ms,
           left(query, 150) AS query_snippet
    FROM pg_stat_statements
    ORDER BY mean_exec_time DESC
    LIMIT 10;
  " 2>/dev/null || echo "     pg_stat_statements not enabled. Enable with:"
  echo "       CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
  echo "       -- Then add to postgresql.conf:"
  echo "       --   shared_preload_libraries = 'pg_stat_statements'"
  echo "       -- Restart PostgreSQL to take effect."

  # D3 — EXPLAIN ANALYZE on the most common RAG query patterns
  echo ""
  echo "[D3] EXPLAIN ANALYZE on BM25 full-text search (most common retrieval query):"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
    SELECT id, domain_id, document_id, content, page_num, chunk_index
    FROM document_chunks
    WHERE domain_id = 'replace-with-real-domain-uuid'
      AND search_vec @@ plainto_tsquery('english', 'replace with test query')
    ORDER BY ts_rank(search_vec, plainto_tsquery('english', 'replace with test query')) DESC
    LIMIT 10;
  " 2>/dev/null || echo "     (replace domain UUID and query string before running)"

  # D4 — EXPLAIN ANALYZE on rag_query_logs lookup
  echo ""
  echo "[D4] EXPLAIN ANALYZE on query log lookup (used for cache check):"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
    SELECT id, query, answer, created_at
    FROM rag_query_logs
    WHERE domain_id = 'replace-with-real-domain-uuid'
    ORDER BY created_at DESC
    LIMIT 20;
  " 2>/dev/null || echo "     (replace domain UUID before running)"

  # D5 — EXPLAIN ANALYZE on document status poll (frequent during ingestion)
  echo ""
  echo "[D5] EXPLAIN ANALYZE on document status lookup:"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT)
    SELECT id, status, error_msg, created_at
    FROM documents
    WHERE id = 'replace-with-real-document-uuid';
  " 2>/dev/null || echo "     (replace document UUID before running)"

  # D6 — Enable slow query logging for future load tests
  echo ""
  echo "[D6] Enable slow query logging in PostgreSQL (queries > 100ms):"
  echo "     Run in psql:"
  echo "       ALTER SYSTEM SET log_min_duration_statement = 100;"
  echo "       SELECT pg_reload_conf();"
  echo "     Then check logs at:"
  echo "       -- Linux: /var/log/postgresql/postgresql-17-main.log"
  echo "       -- Windows: C:\\Program Files\\PostgreSQL\\17\\data\\log\\"

  # D7 — Kill a specific long-running query by PID
  echo ""
  echo "[D7] Kill a specific slow query by PID (get PID from D1 above):"
  echo "     SELECT pg_cancel_backend(<pid>);    -- graceful (recommended)"
  echo "     SELECT pg_terminate_backend(<pid>); -- forceful (if cancel doesn't work)"
}

# =============================================================================
# SECTION E — Add Missing Indexes
# =============================================================================
# WARNING SIGNS IN GRAFANA:
#   - EXPLAIN ANALYZE output shows 'Seq Scan' on large tables
#   - High 'heap_blks_read' in pg_statio_user_tables
#   - Slow queries on document_chunks, documents, or rag_query_logs
# =============================================================================
run_section_E() {
  echo "======================================================"
  echo "  SECTION E — Add Missing Indexes"
  echo "======================================================"
  echo ""

  # E1 — Diagnose: find tables with high sequential scan counts
  echo "[E1] Tables with high sequential scan count (candidates for new indexes):"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    SELECT schemaname,
           relname                          AS table_name,
           seq_scan,
           idx_scan,
           seq_tup_read,
           n_live_tup                       AS row_count,
           round(seq_scan::numeric / nullif(idx_scan + seq_scan, 0) * 100, 1) AS pct_seq
    FROM pg_stat_user_tables
    WHERE n_live_tup > 1000
    ORDER BY seq_scan DESC
    LIMIT 10;
  "

  # E2 — Diagnose: list all existing indexes
  echo ""
  echo "[E2] Existing indexes on RAG tables:"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    SELECT tablename, indexname, indexdef
    FROM pg_indexes
    WHERE tablename IN ('documents', 'document_chunks', 'rag_query_logs',
                        'domains', 'domain_roles', 'eval_results')
    ORDER BY tablename, indexname;
  "

  # E3 — Diagnose: find missing indexes (unused foreign-key-like columns)
  echo ""
  echo "[E3] Columns likely to need indexes (based on query patterns):"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    SELECT t.relname AS table_name,
           a.attname AS column_name,
           pg_size_pretty(pg_relation_size(t.oid)) AS table_size
    FROM pg_class t
    JOIN pg_attribute a ON a.attrelid = t.oid
    WHERE t.relkind = 'r'
      AND NOT a.attisdropped
      AND a.attnum > 0
      AND t.relname IN ('documents', 'document_chunks', 'rag_query_logs')
      AND a.attname IN ('domain_id', 'document_id', 'status', 'user_id', 'created_at')
      AND NOT EXISTS (
        SELECT 1 FROM pg_index i
        JOIN pg_attribute ia ON ia.attrelid = i.indrelid AND ia.attnum = ANY(i.indkey)
        WHERE i.indrelid = t.oid AND ia.attname = a.attname
      )
    ORDER BY t.relname, a.attname;
  "

  # E4 — Fix: add all recommended indexes (CONCURRENTLY — no table lock)
  echo ""
  echo "[E4] Creating all recommended indexes (CONCURRENTLY — no downtime):"
  echo "     Running... this may take a few seconds on large tables."
  echo ""

  INDEXES=(
    # document_chunks — most queried table in retrieval
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_domain_id
         ON document_chunks (domain_id);"

    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_document_id
         ON document_chunks (document_id);"

    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_domain_created
         ON document_chunks (domain_id, created_at DESC);"

    # GIN index on search_vec — required for BM25 full-text search performance
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_chunks_search_vec
         ON document_chunks USING GIN (search_vec);"

    # documents — polled frequently for status
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_domain_id
         ON documents (domain_id);"

    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_status
         ON documents (status);"

    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_documents_user_id
         ON documents (user_id);"

    # rag_query_logs — queried for audit and evaluation
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rag_logs_domain_id
         ON rag_query_logs (domain_id);"

    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rag_logs_user_id
         ON rag_query_logs (user_id);"

    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rag_logs_created_at
         ON rag_query_logs (created_at DESC);"

    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_rag_logs_domain_created
         ON rag_query_logs (domain_id, created_at DESC);"

    # domain_roles — checked on every authenticated request
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_domain_roles_user_id
         ON domain_roles (user_id);"

    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_domain_roles_domain_user
         ON domain_roles (domain_id, user_id);"

    # eval_results — queried in dashboard
    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eval_query_log_id
         ON eval_results (query_log_id);"

    "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_eval_domain_id
         ON eval_results (domain_id);"
  )

  for idx_sql in "${INDEXES[@]}"; do
    echo "  Applying: $(echo "${idx_sql}" | head -1 | sed 's/CREATE INDEX CONCURRENTLY IF NOT EXISTS //')"
    psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" \
      -c "${idx_sql}" 2>&1 | grep -v "^$" | sed 's/^/    /'
  done

  echo ""
  echo "[E4] All indexes applied."

  # E5 — Update table statistics after indexing
  echo ""
  echo "[E5] Running ANALYZE to update query planner statistics:"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    ANALYZE documents;
    ANALYZE document_chunks;
    ANALYZE rag_query_logs;
    ANALYZE domain_roles;
    ANALYZE eval_results;
  "
  echo "     ANALYZE complete."

  # E6 — Verify indexes were created
  echo ""
  echo "[E6] Verify all indexes exist:"
  psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" -c "
    SELECT tablename, indexname
    FROM pg_indexes
    WHERE tablename IN ('documents', 'document_chunks', 'rag_query_logs',
                        'domain_roles', 'eval_results')
    ORDER BY tablename, indexname;
  "
}

# =============================================================================
# DISPATCHER — run requested section or show full menu
# =============================================================================
case "${SECTION^^}" in
  A) run_section_A ;;
  B) run_section_B ;;
  C) run_section_C ;;
  D) run_section_D ;;
  E) run_section_E ;;
  ALL)
    run_section_A
    echo ""
    run_section_B
    echo ""
    run_section_C
    echo ""
    run_section_D
    echo ""
    run_section_E
    ;;
  MENU|*)
    echo "Usage: bash tuning.sh <SECTION>"
    echo ""
    echo "  bash tuning.sh A   — Fix PostgreSQL connection pool exhaustion"
    echo "  bash tuning.sh B   — Fix Redis memory limit and eviction"
    echo "  bash tuning.sh C   — Increase Uvicorn / Celery worker processes"
    echo "  bash tuning.sh D   — Find slow queries with EXPLAIN ANALYZE"
    echo "  bash tuning.sh E   — Add missing database indexes"
    echo "  bash tuning.sh ALL — Run all sections (diagnostic + fixes)"
    echo ""
    echo "  Example: bash monitoring/scripts/tuning.sh E"
    ;;
esac

unset PGPASSWORD
