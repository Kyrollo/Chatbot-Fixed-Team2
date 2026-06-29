# Load Test Infrastructure Report

**Project:** Multi-Domain RAG System
**Sprint:** 4 — Load Testing & Infrastructure Monitoring
**Date:** _______________
**Tester:** _______________
**Environment:** http://localhost:8000
**Locust Script:** `tests/load_test.py`

---

## 1. Baseline Metrics (Before Load Test)

> Run `bash monitoring/scripts/baseline.sh` and paste values here.
> All values captured with all services running but **zero concurrent users**.

| Metric | Value | Notes |
|---|---|---|
| **CPU — Idle %** | | e.g. 92% idle |
| **CPU — In Use %** | | e.g. 8% in use |
| **RAM — Total** | MB | |
| **RAM — Used** | MB | |
| **RAM — Available** | MB | |
| **RAM — Used %** | % | |
| **Swap — Used** | MB | Ideally 0 |
| **System Load Avg (1m / 5m / 15m)** | / / | |
| **PostgreSQL — Total Connections** | | |
| **PostgreSQL — Active Connections** | | |
| **PostgreSQL — Idle Connections** | | |
| **PostgreSQL — Max Connections** | | default 100 |
| **PostgreSQL — Connection Pool Use %** | % | |
| **Redis — Used Memory** | | e.g. 45.2M |
| **Redis — Peak Memory** | | |
| **Redis — Max Memory Limit** | | 0 = no limit |
| **Redis — Eviction Policy** | | e.g. allkeys-lru |
| **Redis — Total Keys** | | |

**Baseline captured at:** _______________

---

## 2. Peak Metrics During Load Test

> Watch these values in Grafana during the test. Record the **peak** value observed.
> Grafana: http://localhost:3000 → Dashboards → infra-overview / service-health

### 2.1 — At 10 Concurrent Users

| Metric | Peak Value | Grafana Panel | Pass / Warn / Fail |
|---|---|---|---|
| **p50 Response Time** | ms | Service Health → Response Times | |
| **p95 Response Time** | ms | Service Health → p95 | ✅ < 3000 ms |
| **p99 Response Time** | ms | Service Health → p99 | |
| **Requests per Second (RPS)** | req/s | Service Health → RPS | |
| **Error Rate %** | % | Service Health → Error Rate | ✅ < 5% |
| **CPU — In Use %** | % | Infra Overview → CPU | |
| **RAM — Used** | MB | Infra Overview → Memory | |
| **PostgreSQL Connections** | | Infra Overview → DB Connections | |
| **Redis Memory Used** | | Infra Overview → Redis Memory | |
| **Redis Evictions** | | Infra Overview → Evictions | ✅ = 0 |
| **Active Celery Tasks** | | Infra Overview → Celery | |

**Locust stats at 10 users:**
- Median (p50): _____ ms
- p95: _____ ms
- RPS: _____ req/s
- Failures: _____ ( _____ %)

---

### 2.2 — At 25 Concurrent Users

| Metric | Peak Value | Grafana Panel | Pass / Warn / Fail |
|---|---|---|---|
| **p50 Response Time** | ms | | |
| **p95 Response Time** | ms | | ✅ < 3000 ms |
| **p99 Response Time** | ms | | |
| **Requests per Second (RPS)** | req/s | | |
| **Error Rate %** | % | | ✅ < 5% |
| **CPU — In Use %** | % | | |
| **RAM — Used** | MB | | |
| **PostgreSQL Connections** | | | |
| **Redis Memory Used** | | | |
| **Redis Evictions** | | | ✅ = 0 |
| **Active Celery Tasks** | | | |

**Locust stats at 25 users:**
- Median (p50): _____ ms
- p95: _____ ms
- RPS: _____ req/s
- Failures: _____ ( _____ %)

---

### 2.3 — At 50 Concurrent Users (Sustained — 3 Minutes)

| Metric | Peak Value | Grafana Panel | Pass / Warn / Fail |
|---|---|---|---|
| **p50 Response Time** | ms | | |
| **p95 Response Time** | ms | | ✅ < 3000 ms |
| **p99 Response Time** | ms | | |
| **Requests per Second (RPS)** | req/s | | |
| **Error Rate %** | % | | ✅ < 5% |
| **CPU — In Use %** | % | | |
| **RAM — Used** | MB | | |
| **Swap — Used** | MB | | ✅ = 0 |
| **PostgreSQL Connections** | | | |
| **PostgreSQL — Pool Use %** | % | | ✅ < 80% |
| **Redis Memory Used** | | | |
| **Redis Evictions (total)** | | | ✅ = 0 |
| **Active Celery Tasks (backlog)** | | | ✅ < 10 |
| **Service Crashes / Restarts** | | | ✅ = 0 |

**Locust stats at 50 users (3 min run):**
- Median (p50): _____ ms
- p95: _____ ms
- p99: _____ ms
- RPS: _____ req/s
- Total Requests: _____
- Failures: _____ ( _____ %)

---

## 3. Bottlenecks Found

> List every warning sign observed in Grafana or Locust during the test.
> Use this section to record observations before applying fixes.

| # | Bottleneck | Warning Sign Observed | Severity | Section in tuning.sh |
|---|---|---|---|---|
| 1 | | | High / Medium / Low | A / B / C / D / E |
| 2 | | | | |
| 3 | | | | |
| 4 | | | | |
| 5 | | | | |

**Bottleneck detail notes:**

```
[Bottleneck 1]
  Observed at: ___ concurrent users
  Grafana panel: ___
  Metric value: ___
  Threshold exceeded: ___

[Bottleneck 2]
  ...
```

---

## 4. Fixes Applied

> For each fix applied from `monitoring/scripts/tuning.sh`, record what was changed and the result.

| # | Bottleneck | Fix Applied | Command / Change | Before | After | Result |
|---|---|---|---|---|---|---|
| 1 | | | | | | Improved / No Change / Made Worse |
| 2 | | | | | | |
| 3 | | | | | | |

**Detailed fix log:**

```
[Fix 1]
  Bottleneck: ___
  tuning.sh section: ___
  Command run: ___
  Before: ___
  After:  ___
  Observation: ___

[Fix 2]
  ...
```

---

## 5. Final Pass / Fail Verdict

### Pass Criteria

| Criterion | Threshold | Measured Value | Result |
|---|---|---|---|
| p95 response time at 50 users | < 3000 ms | ms | ✅ PASS / ❌ FAIL |
| Error rate at 50 users | < 5% | % | ✅ PASS / ❌ FAIL |
| Service crashes during test | 0 | | ✅ PASS / ❌ FAIL |
| Redis evictions during test | 0 | | ✅ PASS / ❌ FAIL |
| DB connection pool exhaustion | Never exceeded 80% | % | ✅ PASS / ❌ FAIL |

### Overall Verdict

```
┌──────────────────────────────────────────────────────────────────────────┐
│                                                                          │
│   OVERALL VERDICT:    [ ✅ PASS ]  /  [ ❌ FAIL ]                       │
│                                                                          │
│   p95 at 50 users:    ________ ms   (threshold: < 3000 ms)              │
│   Error rate:         ________ %    (threshold: < 5%)                   │
│   Crashes:            ________      (threshold: 0)                      │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**If FAIL — blocking issues:**
- [ ] _______________
- [ ] _______________

**Tester sign-off:** _______________ **Date:** _______________

---

## 6. Appendix — Locust CSV Output

> Paste the contents of `tests/load_results_stats.csv` here after the headless run.
> Generate with: `locust -f tests/load_test.py --headless --users 50 --spawn-rate 5 --run-time 3m --csv=tests/load_results`

```csv
(paste tests/load_results_stats.csv here)
```

---

## 7. Appendix — Grafana Screenshots

> Take screenshots of the following Grafana panels at peak load (50 users) and attach below.

| Panel | Dashboard | Screenshot |
|---|---|---|
| Response Time p95 | service-health | _(attach)_ |
| Error Rate | service-health | _(attach)_ |
| DB Connections | infra-overview | _(attach)_ |
| Redis Memory | infra-overview | _(attach)_ |
| CPU / RAM | infra-overview | _(attach)_ |
| Evaluation Quality | evaluation-quality | _(attach)_ |
