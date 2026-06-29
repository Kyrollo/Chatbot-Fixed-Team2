#!/usr/bin/env bash
# =============================================================================
# baseline.sh — Pre-Load-Test Baseline Metrics Collector
# =============================================================================
#
# PURPOSE
#   Run this script BEFORE starting the Locust load test.
#   It captures idle-state metrics for CPU, memory, PostgreSQL connections,
#   and Redis memory, then writes everything to monitoring/baseline_results.txt.
#
# USAGE
#   bash monitoring/scripts/baseline.sh
#
# OUTPUT
#   monitoring/baseline_results.txt  (overwritten each run)
#
# REQUIREMENTS
#   - PostgreSQL 17 running on localhost:5434  (psql in PATH or adjust PSQL var)
#   - Redis running on localhost:6379          (redis-cli in PATH)
#   - Linux/macOS or WSL2 for /proc/meminfo and top
#   - Run with all services idle (before the load test begins)
# =============================================================================

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUTPUT_FILE="${PROJECT_ROOT}/monitoring/baseline_results.txt"

# PostgreSQL connection settings — match your .env / run_services.py defaults
PG_HOST="${POSTGRES_HOST:-localhost}"
PG_PORT="${POSTGRES_PORT:-5434}"
PG_USER="${POSTGRES_USER:-postgres}"
PG_DB="${POSTGRES_DB:-domain_db}"
PG_PASSWORD="${POSTGRES_PASSWORD:-1234}"

# Redis connection settings
REDIS_HOST="${REDIS_HOST:-localhost}"
REDIS_PORT="${REDIS_PORT:-6379}"

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

TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S %Z')"

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "[baseline] $*"; }
warn() { echo "[baseline] WARNING: $*" >&2; }

require_cmd() {
  if ! command -v "$1" &>/dev/null; then
    warn "Command '$1' not found. Skipping that metric."
    return 1
  fi
}

# ── Start output file ─────────────────────────────────────────────────────────
mkdir -p "$(dirname "${OUTPUT_FILE}")"
cat > "${OUTPUT_FILE}" <<EOF
=============================================================================
  LOAD TEST BASELINE METRICS
  Captured: ${TIMESTAMP}
  Run BEFORE the Locust load test. Fill this file into load_test_infra_report.md
=============================================================================

EOF

log "Writing baseline to: ${OUTPUT_FILE}"
log "Timestamp: ${TIMESTAMP}"

# ─────────────────────────────────────────────────────────────────────────────
# 1. CPU USAGE (idle snapshot via top, 3-sample average)
# ─────────────────────────────────────────────────────────────────────────────
{
  echo "## 1. CPU USAGE"
  echo "   Method: top -bn3 (3-sample average, non-interactive)"
  echo ""
} >> "${OUTPUT_FILE}"

if require_cmd top; then
  # top -bn3: batch mode, 3 iterations — gives a stable average
  CPU_IDLE=$(top -bn3 | grep "Cpu(s)" | tail -1 | awk '{print $8}' | tr -d '%id,')
  # Fallback: some systems format differs — try second approach
  if [[ -z "${CPU_IDLE}" ]]; then
    CPU_IDLE=$(top -bn3 | grep -oP '\d+\.\d+\s*id' | tail -1 | awk '{print $1}')
  fi

  if [[ -n "${CPU_IDLE}" ]]; then
    CPU_USED=$(echo "100 - ${CPU_IDLE}" | bc 2>/dev/null || echo "N/A")
    echo "   CPU Idle:    ${CPU_IDLE}%" >> "${OUTPUT_FILE}"
    echo "   CPU In Use:  ${CPU_USED}%"   >> "${OUTPUT_FILE}"
  else
    echo "   CPU In Use:  (could not parse top output — run 'top' manually)" >> "${OUTPUT_FILE}"
  fi
else
  # Fallback: read /proc/stat for a single-shot CPU sample
  if [[ -f /proc/stat ]]; then
    CPU_LINE=$(grep "^cpu " /proc/stat)
    echo "   /proc/stat snapshot (single-shot, less accurate):" >> "${OUTPUT_FILE}"
    echo "   ${CPU_LINE}" >> "${OUTPUT_FILE}"
  else
    echo "   CPU:  top not available and /proc/stat not found." >> "${OUTPUT_FILE}"
  fi
fi

# Additional: mpstat if available (more detailed per-core breakdown)
if require_cmd mpstat 2>/dev/null; then
  echo "" >> "${OUTPUT_FILE}"
  echo "   mpstat output (1 sample):" >> "${OUTPUT_FILE}"
  mpstat 1 1 2>/dev/null | tail -3 >> "${OUTPUT_FILE}" || true
fi

echo "" >> "${OUTPUT_FILE}"
log "✓ CPU metrics captured"

# ─────────────────────────────────────────────────────────────────────────────
# 2. MEMORY USAGE
# ─────────────────────────────────────────────────────────────────────────────
{
  echo "## 2. MEMORY USAGE"
} >> "${OUTPUT_FILE}"

if require_cmd free; then
  FREE_OUTPUT=$(free -m)
  MEM_TOTAL=$(echo "${FREE_OUTPUT}" | awk '/^Mem:/ {print $2}')
  MEM_USED=$(echo "${FREE_OUTPUT}"|  awk '/^Mem:/ {print $3}')
  MEM_FREE=$(echo "${FREE_OUTPUT}" | awk '/^Mem:/ {print $4}')
  MEM_AVAILABLE=$(echo "${FREE_OUTPUT}" | awk '/^Mem:/ {print $7}')
  SWAP_TOTAL=$(echo "${FREE_OUTPUT}" | awk '/^Swap:/ {print $2}')
  SWAP_USED=$(echo "${FREE_OUTPUT}" | awk '/^Swap:/ {print $3}')

  MEM_PCT_USED=$(awk "BEGIN {printf \"%.1f\", ${MEM_USED}/${MEM_TOTAL}*100}" 2>/dev/null || echo "N/A")

  {
    echo "   Total RAM:      ${MEM_TOTAL} MB"
    echo "   Used RAM:       ${MEM_USED} MB  (${MEM_PCT_USED}%)"
    echo "   Free RAM:       ${MEM_FREE} MB"
    echo "   Available RAM:  ${MEM_AVAILABLE} MB  (available = free + reclaimable cache)"
    echo "   Swap Total:     ${SWAP_TOTAL} MB"
    echo "   Swap Used:      ${SWAP_USED} MB"
  } >> "${OUTPUT_FILE}"

elif [[ -f /proc/meminfo ]]; then
  {
    echo "   /proc/meminfo snapshot:"
    grep -E "^(MemTotal|MemFree|MemAvailable|SwapTotal|SwapFree|Cached|Buffers):" /proc/meminfo
  } >> "${OUTPUT_FILE}"
else
  echo "   Memory: 'free' not available. Run 'free -m' manually." >> "${OUTPUT_FILE}"
fi

echo "" >> "${OUTPUT_FILE}"
log "✓ Memory metrics captured"

# ─────────────────────────────────────────────────────────────────────────────
# 3. POSTGRESQL — OPEN CONNECTIONS
# ─────────────────────────────────────────────────────────────────────────────
{
  echo "## 3. POSTGRESQL CONNECTIONS"
  echo "   Host: ${PG_HOST}:${PG_PORT}   Database: ${PG_DB}"
  echo ""
} >> "${OUTPUT_FILE}"

PSQL_CMD=""
if command -v psql &>/dev/null; then
  PSQL_CMD="psql"
elif command -v psql.exe &>/dev/null; then
  PSQL_CMD="psql.exe"
fi

if [[ -n "${PSQL_CMD}" ]]; then
  export PGPASSWORD="${PG_PASSWORD}"

  # Total connections across all databases
  TOTAL_CONN=$(${PSQL_CMD} -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" \
    -tAc "SELECT count(*) FROM pg_stat_activity;" 2>/dev/null || echo "ERR")

  # Active (non-idle) connections
  ACTIVE_CONN=$(${PSQL_CMD} -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" \
    -tAc "SELECT count(*) FROM pg_stat_activity WHERE state = 'active';" 2>/dev/null || echo "ERR")

  # Idle connections
  IDLE_CONN=$(${PSQL_CMD} -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" \
    -tAc "SELECT count(*) FROM pg_stat_activity WHERE state = 'idle';" 2>/dev/null || echo "ERR")

  # Max connections setting
  MAX_CONN=$(${PSQL_CMD} -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" \
    -tAc "SHOW max_connections;" 2>/dev/null || echo "ERR")

  # Connection utilization %
  if [[ "${TOTAL_CONN}" != "ERR" && "${MAX_CONN}" != "ERR" ]]; then
    CONN_PCT=$(awk "BEGIN {printf \"%.1f\", ${TOTAL_CONN}/${MAX_CONN}*100}" 2>/dev/null || echo "N/A")
  else
    CONN_PCT="N/A"
  fi

  # Per-database connection counts
  PER_DB=$(${PSQL_CMD} -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" \
    -tAc "SELECT datname, count(*) FROM pg_stat_activity GROUP BY datname ORDER BY count DESC;" \
    2>/dev/null || echo "ERR")

  {
    echo "   Total Connections:   ${TOTAL_CONN}"
    echo "   Active Connections:  ${ACTIVE_CONN}"
    echo "   Idle Connections:    ${IDLE_CONN}"
    echo "   Max Connections:     ${MAX_CONN}"
    echo "   Connection Pool Use: ${CONN_PCT}%"
    echo ""
    echo "   Per-database breakdown:"
    echo "${PER_DB}" | sed 's/^/   /'
  } >> "${OUTPUT_FILE}"

  unset PGPASSWORD
else
  warn "psql not found. Skipping PostgreSQL metrics."
  {
    echo "   ERROR: psql not found in PATH."
    echo "   Install psql or add it to PATH, then re-run."
    echo "   Manual query: SELECT count(*) FROM pg_stat_activity;"
  } >> "${OUTPUT_FILE}"
fi

echo "" >> "${OUTPUT_FILE}"
log "✓ PostgreSQL metrics captured"

# ─────────────────────────────────────────────────────────────────────────────
# 4. REDIS — MEMORY USED
# ─────────────────────────────────────────────────────────────────────────────
{
  echo "## 4. REDIS MEMORY"
  echo "   Host: ${REDIS_HOST}:${REDIS_PORT}"
  echo ""
} >> "${OUTPUT_FILE}"

if [[ -n "${REDIS_CLI_CMD}" ]]; then
  REDIS_INFO=$("${REDIS_CLI_CMD}" -h "${REDIS_HOST}" -p "${REDIS_PORT}" INFO memory 2>/dev/null || echo "ERR")

  if [[ "${REDIS_INFO}" != "ERR" ]]; then
    REDIS_USED=$(echo "${REDIS_INFO}" | grep "^used_memory_human:" | cut -d: -f2 | tr -d '\r ' || true)
    REDIS_PEAK=$(echo "${REDIS_INFO}" | grep "^used_memory_peak_human:" | cut -d: -f2 | tr -d '\r ' || true)
    
    REDIS_RSS=$(echo "${REDIS_INFO}" | grep "^used_memory_rss_human:" | cut -d: -f2 | tr -d '\r ' || true)
    if [[ -z "${REDIS_RSS}" ]]; then
      REDIS_RSS_BYTES=$(echo "${REDIS_INFO}" | grep "^used_memory_rss:" | cut -d: -f2 | tr -d '\r ' || true)
      if [[ -n "${REDIS_RSS_BYTES}" ]]; then
        REDIS_RSS=$(awk "BEGIN {printf \"%.2fM\", ${REDIS_RSS_BYTES}/1024/1024}" 2>/dev/null || echo "${REDIS_RSS_BYTES} bytes")
      fi
    fi

    REDIS_MAXMEM=$(echo "${REDIS_INFO}" | grep "^maxmemory_human:" | cut -d: -f2 | tr -d '\r ' || true)
    if [[ -z "${REDIS_MAXMEM}" ]]; then
      REDIS_MAXMEM_BYTES=$(echo "${REDIS_INFO}" | grep "^maxmemory:" | cut -d: -f2 | tr -d '\r ' || true)
      if [[ -n "${REDIS_MAXMEM_BYTES}" && "${REDIS_MAXMEM_BYTES}" != "0" ]]; then
        REDIS_MAXMEM=$(awk "BEGIN {printf \"%.2fM\", ${REDIS_MAXMEM_BYTES}/1024/1024}" 2>/dev/null || echo "${REDIS_MAXMEM_BYTES}")
      else
        REDIS_MAXMEM="0 (no limit)"
      fi
    fi

    REDIS_POLICY=$(echo "${REDIS_INFO}" | grep "^maxmemory_policy:" | cut -d: -f2 | tr -d '\r ' || true)
    REDIS_FRAG=$(echo "${REDIS_INFO}" | grep "^mem_fragmentation_ratio:" | cut -d: -f2 | tr -d '\r ' || true)

    # Get key count
    REDIS_KEYS=$("${REDIS_CLI_CMD}" -h "${REDIS_HOST}" -p "${REDIS_PORT}" DBSIZE 2>/dev/null || echo "ERR")

    {
      echo "   Used Memory:          ${REDIS_USED:-N/A}"
      echo "   Peak Memory:          ${REDIS_PEAK:-N/A}"
      echo "   RSS (OS view):        ${REDIS_RSS:-N/A}"
      echo "   Max Memory Limit:     ${REDIS_MAXMEM:-0 (no limit)}"
      echo "   Eviction Policy:      ${REDIS_POLICY:-N/A}"
      echo "   Fragmentation Ratio:  ${REDIS_FRAG:-N/A}  (>1.5 = high fragmentation)"
      echo "   Total Keys (DBSIZE):  ${REDIS_KEYS}"
    } >> "${OUTPUT_FILE}"
  else
    {
      echo "   ERROR: Could not connect to Redis at ${REDIS_HOST}:${REDIS_PORT}"
      echo "   Is Redis running? Check: redis-cli ping"
    } >> "${OUTPUT_FILE}"
  fi
else
  warn "redis-cli not found. Skipping Redis metrics."
  {
    echo "   ERROR: redis-cli not found in PATH or ${PROJECT_ROOT}/tools/redis/."
    echo "   Install redis-tools or ensure it is downloaded in tools/redis, then re-run."
  } >> "${OUTPUT_FILE}"
fi

echo "" >> "${OUTPUT_FILE}"
log "✓ Redis metrics captured"

# ─────────────────────────────────────────────────────────────────────────────
# 5. SYSTEM LOAD AVERAGE + DISK I/O (bonus context)
# ─────────────────────────────────────────────────────────────────────────────
{
  echo "## 5. SYSTEM LOAD AVERAGE"
} >> "${OUTPUT_FILE}"

if [[ -f /proc/loadavg ]]; then
  LOAD=$(cat /proc/loadavg)
  echo "   Load avg (1m / 5m / 15m): ${LOAD}" >> "${OUTPUT_FILE}"
elif require_cmd uptime 2>/dev/null; then
  LOAD=$(uptime | grep -oP 'load average[s]?: \K.*')
  echo "   Load avg (1m / 5m / 15m): ${LOAD}" >> "${OUTPUT_FILE}"
else
  echo "   Load avg: not available" >> "${OUTPUT_FILE}"
fi

# Number of CPU cores (for context — load avg interpretation)
NCPUS=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo "N/A")
echo "   CPU Cores:                ${NCPUS}" >> "${OUTPUT_FILE}"

echo "" >> "${OUTPUT_FILE}"

# ─────────────────────────────────────────────────────────────────────────────
# 6. FOOTER — instructions
# ─────────────────────────────────────────────────────────────────────────────
cat >> "${OUTPUT_FILE}" <<'EOF'
=============================================================================
  NEXT STEPS
  1. Copy the values above into docs/load_test_infra_report.md (Baseline table)
  2. Start the Locust load test:
       locust -f tests/load_test.py --host=http://localhost:8000
  3. During the test, watch Grafana at http://localhost:3000
  4. After the test, run monitoring/scripts/tuning.sh if bottlenecks appear
=============================================================================
EOF

echo ""
log "=============================================="
log "Baseline capture complete."
log "Results saved to: ${OUTPUT_FILE}"
log "=============================================="
echo ""
cat "${OUTPUT_FILE}"
