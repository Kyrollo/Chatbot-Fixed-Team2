# Sprint 4 — Testing, Compliance & Documentation

Complete implementation plan for all 6 tasks. Updated after review feedback.

---

## Issue Analysis

### Issue 1: `POST /domains/admin/users` — Does it exist?

**Verdict: The endpoint DOES exist, but I'll switch to direct DB anyway.**

The endpoint is defined at [router.py:202-205](file:///d:/Personal/Fixed Solutions/Project Files/v6/services/domain-service/router.py#L202-L205):

```python
@router.post("/admin/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, db: DBSession, admin: SystemAdmin):
    """Creates a new user in the users table."""
    return await service.create_user(db, payload.id, payload.name, payload.role)
```

There's no route collision with `/{domain_id}` because:
- `/{domain_id}` matches **one-segment** paths like `/domains/some-uuid` (where `domain_id: uuid.UUID`)
- `/admin/users` is a **two-segment** path `/domains/admin/users`
- These are structurally different patterns — FastAPI/Starlette won't confuse them

There's also a corresponding `DELETE /domains/admin/users/{user_id}` at [line 208](file:///d:/Personal/Fixed Solutions/Project Files/v6/services/domain-service/router.py#L208-L212) for cleanup.

**However**, I agree that using direct DB is more robust for test infrastructure — it avoids a circular dependency where tests depend on the very API they're testing. If the admin user doesn't exist in the DB yet, the API call to create test users would fail before we even begin. **I'll switch to `psycopg2` direct inserts.**

---

### Issue 2: Load test uses only one user

**Verdict: Valid issue. Will fix.**

Using only `admin` for all 50 simulated users doesn't test RBAC under load and doesn't reflect reality. I'll change to `LOGIN_USERS = ["admin", "test_reader", "test_contrib"]` with `random.choice()` in `on_start()`.

**Important caveat**: These users must exist in the `users` table for `POST /domains/auth/login` to work — the [login endpoint](file:///d:/Personal/Fixed Solutions/Project Files/v6/services/domain-service/router.py#L38-L59) does a DB lookup (`select(User).where(User.id == user_id)` then fallback by name). It returns **401 if the user doesn't exist**. So the load test docstring will note that these users need to be seeded first (the RBAC test suite creates them via direct DB insert, or they can be created manually).

---

### Issue 3: `SAMPLE_DOCUMENT_ID` inflates failure rate

**Verdict: Valid issue. Will fix with `catch_response=True`.**

A 404 for a nonexistent document ID is **expected behavior**, not a system failure. Locust counts it as a failure by default, which inflates the error rate past the 5% threshold. I'll use `catch_response=True` and mark 404 as success since we're testing endpoint performance, not document existence.

---

### Issue 4: No teardown after tests

**Verdict: Valid issue. Will add yield-based cleanup.**

I'll convert fixtures to use `yield` with cleanup code:
- **test_domain**: After all tests, call `DELETE /domains/{domain_id}` (archives it)
- **test_users**: After all tests, delete via direct DB `DELETE FROM users WHERE id = ...`
- **assigned_users**: Members get cascade-deleted when domain is archived, but I'll explicitly remove them too

Note: `DELETE /domains/{id}` doesn't truly delete — it [sets status to `archived`](file:///d:/Personal/Fixed Solutions/Project Files/v6/services/domain-service/service.py#L157-L165). For true cleanup, I'll use direct DB deletes.

---

## Proposed Changes

### Task 1 — RBAC Isolation Test Cases

#### [NEW] [conftest.py](file:///d:/Personal/Fixed Solutions/Project Files/v6/tests/conftest.py)

Shared fixtures — direct DB user creation, yield-based cleanup.

```python
"""
conftest.py — Shared fixtures for Sprint 4 RBAC integration tests.

Prerequisites:
  - All services running: python run_services.py --worker --evaluation
  - PostgreSQL running on port 5434 with database domain_db
  - The monolith gateway available at http://localhost:8000

Fixtures create isolated test data (domain + users + roles) via direct DB
and API calls, and clean up after the session completes.
"""

import os
import uuid
import pytest
import httpx
import psycopg2

BASE_URL = "http://localhost:8000"
TIMEOUT = 15.0

# ──────────────────────────────────────────────────────────────
# Database connection (direct access for user seeding)
# Reads from SYNC_DATABASE_URL env var (same one used by services)
# with a localhost fallback for convenience.
# ──────────────────────────────────────────────────────────────
DB_DSN = os.getenv(
    "SYNC_DATABASE_URL",
    "postgresql://postgres:1234@localhost:5434/domain_db",
)


def get_db_connection():
    """Return a psycopg2 connection to the domain database."""
    return psycopg2.connect(DB_DSN)


# ──────────────────────────────────────────────────────────────
# Helper: login and return {"token", "user_id", "role", ...}
# ──────────────────────────────────────────────────────────────
def login(user_id: str) -> dict:
    """Login via dev auth and return the full response dict including token."""
    resp = httpx.post(
        f"{BASE_URL}/domains/auth/login",
        json={"user_id": user_id},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def auth_header(token: str) -> dict:
    """Build Authorization header dict from a token string."""
    return {"Authorization": f"Bearer {token}"}


# ──────────────────────────────────────────────────────────────
# Fixture: seed test users directly into the database
# ──────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def test_users() -> dict:
    """
    Insert three disposable users directly into the PostgreSQL users table:
      - test_reader   (role=reader)
      - test_contrib  (role=contributor)
      - test_domadmin (role=domain_admin)

    Uses psycopg2 to avoid depending on the admin API.
    Yields dict mapping key → user_id, then cleans up after session.
    """
    users = {}
    specs = [
        ("test_reader", f"test_reader_{uuid.uuid4().hex[:8]}", "Test Reader RBAC", "reader"),
        ("test_contrib", f"test_contrib_{uuid.uuid4().hex[:8]}", "Test Contributor RBAC", "contributor"),
        ("test_domadmin", f"test_domadmin_{uuid.uuid4().hex[:8]}", "Test DomAdmin RBAC", "domain_admin"),
    ]

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for key, user_id, name, role in specs:
            cur.execute(
                """
                INSERT INTO users (id, name, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (user_id, name, role),
            )
            users[key] = user_id
        conn.commit()
    finally:
        cur.close()
        conn.close()

    yield users

    # ── Teardown: remove test users from the database ──
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for key, user_id in users.items():
            # Delete domain roles first (FK constraint)
            cur.execute("DELETE FROM domain_roles WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()


# ──────────────────────────────────────────────────────────────
# Fixture: admin session (system_admin user)
# ──────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def admin_auth() -> dict:
    """Login as the seeded 'admin' user (system_admin role)."""
    data = login("admin")
    assert data["token"], "Admin login failed — is the admin user seeded in the DB?"
    return data


@pytest.fixture(scope="session")
def admin_token(admin_auth) -> str:
    return admin_auth["token"]


@pytest.fixture(scope="session")
def admin_headers(admin_token) -> dict:
    return auth_header(admin_token)


# ──────────────────────────────────────────────────────────────
# Fixture: create a test domain (admin-owned) with cleanup
# ──────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def test_domain(admin_headers):
    """Create a fresh domain for RBAC testing. Yields domain response dict.
    Cleans up (deletes from DB) after all tests complete."""
    domain_name = f"rbac-test-{uuid.uuid4().hex[:8]}"
    resp = httpx.post(
        f"{BASE_URL}/domains",
        json={"name": domain_name, "description": "RBAC integration test domain"},
        headers=admin_headers,
        timeout=TIMEOUT,
    )
    assert resp.status_code == 201, f"Failed to create domain: {resp.text}"
    domain_data = resp.json()
    domain_id = domain_data["id"]

    yield domain_data

    # ── Teardown: hard-delete domain and related records from DB ──
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM domain_roles WHERE domain_id = %s", (domain_id,))
        cur.execute("DELETE FROM domain_configs WHERE domain_id = %s", (domain_id,))
        cur.execute("DELETE FROM domains WHERE id = %s", (domain_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()


# ──────────────────────────────────────────────────────────────
# Fixture: collect throwaway domain IDs for cleanup
# ──────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def cleanup_domains():
    """
    Collects domain IDs created during tests (scenarios 4, 8, 9)
    and hard-deletes them from the DB after the session ends.

    Usage in tests:
        cleanup_domains.append(domain_id)
    """
    domain_ids: list[str] = []
    yield domain_ids

    # ── Teardown: remove all collected domains ──
    if not domain_ids:
        return
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for domain_id in domain_ids:
            cur.execute("DELETE FROM domain_roles WHERE domain_id = %s", (domain_id,))
            cur.execute("DELETE FROM domain_configs WHERE domain_id = %s", (domain_id,))
            cur.execute("DELETE FROM domains WHERE id = %s", (domain_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()


# ──────────────────────────────────────────────────────────────
# Fixture: assign roles to test users on the test domain
# ──────────────────────────────────────────────────────────────
@pytest.fixture(scope="session")
def assigned_users(admin_headers, test_domain, test_users) -> dict:
    """
    Assign test users to the test domain with their respective roles.
    Returns dict mapping role-key → {"user_id", "token", "headers"}.

    Cleanup is handled by the test_domain and test_users fixtures
    (domain_roles get cascade-deleted when domain or user is removed).
    """
    domain_id = test_domain["id"]
    role_map = {
        "test_reader": "reader",
        "test_contrib": "contributor",
        "test_domadmin": "domain_admin",
    }
    result = {}
    for key, role in role_map.items():
        user_id = test_users[key]
        # Assign to domain via API
        resp = httpx.post(
            f"{BASE_URL}/domains/{domain_id}/members",
            json={"user_id": user_id, "role": role},
            headers=admin_headers,
            timeout=TIMEOUT,
        )
        if resp.status_code not in (201, 409):
            pytest.fail(f"Assign {key} to domain failed: {resp.text}")

        # Login to get token
        login_data = login(user_id)
        result[key] = {
            "user_id": user_id,
            "token": login_data["token"],
            "headers": auth_header(login_data["token"]),
        }
    return result
```

---

#### [NEW] [test_rbac.py](file:///d:/Personal/Fixed Solutions/Project Files/v6/tests/test_rbac.py)

Full RBAC isolation test suite — 10 scenarios. (No changes from previous version — the test logic is correct.)

```python
"""
test_rbac.py — RBAC Isolation Integration Tests (Sprint 4, Task 1)

Verifies the permission system across all microservices:
  - 401 for unauthenticated requests
  - 403 for insufficient roles
  - 200/201/202 for permitted operations

Run:
    pytest tests/test_rbac.py -v

Prerequisites:
    python run_services.py --worker --evaluation
"""

import io
import uuid

import httpx
import psycopg2
import pytest

from conftest import get_db_connection

BASE_URL = "http://localhost:8000"
TIMEOUT = 15.0


# ═══════════════════════════════════════════════════════════════
# Scenario 1: Reader tries to upload a document → expect 403
# ═══════════════════════════════════════════════════════════════
class TestReaderCannotUpload:
    """A reader should NOT be able to upload documents (requires contributor+)."""

    def test_reader_upload_returns_403(self, assigned_users, test_domain):
        reader = assigned_users["test_reader"]
        domain_id = test_domain["id"]

        # Create a minimal PDF-like file
        dummy_pdf = io.BytesIO(b"%PDF-1.4 dummy content for test")
        dummy_pdf.name = "test.pdf"

        resp = httpx.post(
            f"{BASE_URL}/ingest",
            headers=reader["headers"],
            files={"file": ("test.pdf", dummy_pdf, "application/pdf")},
            data={"domain_id": str(domain_id)},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 403, (
            f"Expected 403 for reader upload, got {resp.status_code}: {resp.text}"
        )


# ═══════════════════════════════════════════════════════════════
# Scenario 2: Contributor uploads to their domain → expect 202
# ═══════════════════════════════════════════════════════════════
class TestContributorCanUpload:
    """A contributor should be able to upload documents to their domain."""

    def test_contributor_upload_returns_202(self, assigned_users, test_domain):
        contrib = assigned_users["test_contrib"]
        domain_id = test_domain["id"]

        dummy_pdf = io.BytesIO(b"%PDF-1.4 contributor test upload content")
        dummy_pdf.name = "contrib_test.pdf"

        resp = httpx.post(
            f"{BASE_URL}/ingest",
            headers=contrib["headers"],
            files={"file": ("contrib_test.pdf", dummy_pdf, "application/pdf")},
            data={"domain_id": str(domain_id)},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 202, (
            f"Expected 202 for contributor upload, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "document_id" in body
        assert body["status"] == "pending"


# ═══════════════════════════════════════════════════════════════
# Scenario 3: Contributor tries to delete a domain → expect 403
# ═══════════════════════════════════════════════════════════════
class TestContributorCannotDeleteDomain:
    """A contributor should NOT be able to delete (archive) a domain
    (requires domain_admin+)."""

    def test_contributor_delete_domain_returns_403(self, assigned_users, test_domain):
        contrib = assigned_users["test_contrib"]
        domain_id = test_domain["id"]

        resp = httpx.delete(
            f"{BASE_URL}/domains/{domain_id}",
            headers=contrib["headers"],
            timeout=TIMEOUT,
        )
        assert resp.status_code == 403, (
            f"Expected 403 for contributor domain delete, got {resp.status_code}: {resp.text}"
        )


# ═══════════════════════════════════════════════════════════════
# Scenario 4: Admin deletes their own domain → expect 200
# ═══════════════════════════════════════════════════════════════
class TestAdminCanDeleteDomain:
    """A system admin / domain_admin can archive (delete) a domain they own."""

    def test_admin_delete_domain_returns_200(self, admin_headers, cleanup_domains):
        # Create a throwaway domain so we don't destroy the shared test domain
        domain_name = f"delete-me-{uuid.uuid4().hex[:8]}"
        create_resp = httpx.post(
            f"{BASE_URL}/domains",
            json={"name": domain_name, "description": "To be deleted"},
            headers=admin_headers,
            timeout=TIMEOUT,
        )
        assert create_resp.status_code == 201
        domain_id = create_resp.json()["id"]
        cleanup_domains.append(domain_id)  # ensure DB cleanup even if test fails

        # Now delete (archive) it
        del_resp = httpx.delete(
            f"{BASE_URL}/domains/{domain_id}",
            headers=admin_headers,
            timeout=TIMEOUT,
        )
        assert del_resp.status_code == 200, (
            f"Expected 200 for admin domain delete, got {del_resp.status_code}: {del_resp.text}"
        )
        # Verify it's archived
        body = del_resp.json()
        assert body["status"] == "archived"


# ═══════════════════════════════════════════════════════════════
# Scenario 5: User with no token calls any endpoint → expect 401
# ═══════════════════════════════════════════════════════════════
class TestNoTokenReturns401:
    """Any protected endpoint must return 401 when no Bearer token is provided."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/domains/"),
            ("POST", "/ingest"),
            ("POST", "/generate/query"),
        ],
    )
    def test_no_token_returns_401(self, method, path):
        resp = httpx.request(
            method,
            f"{BASE_URL}{path}",
            timeout=TIMEOUT,
        )
        # FastAPI's HTTPBearer(auto_error=True) returns 403 when the header
        # is missing entirely (not "invalid" but "absent"). Some frameworks
        # return 401. Accept both as "unauthorized".
        assert resp.status_code in (401, 403), (
            f"Expected 401/403 for no-token {method} {path}, got {resp.status_code}"
        )

    def test_invalid_token_returns_401(self):
        """A garbage token should yield 401."""
        resp = httpx.get(
            f"{BASE_URL}/domains/",
            headers={"Authorization": "Bearer this.is.not.a.valid.jwt"},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 401, (
            f"Expected 401 for invalid token, got {resp.status_code}: {resp.text}"
        )


# ═══════════════════════════════════════════════════════════════
# Scenario 6: Admin assigns a role to another user → expect 201
# ═══════════════════════════════════════════════════════════════
class TestAdminCanAssignRole:
    """A domain_admin (or system_admin) can assign roles to users on their domain."""

    def test_admin_assign_role_returns_201(self, admin_headers, test_domain, test_users):
        domain_id = test_domain["id"]
        # Use a fresh user created by direct DB insert — not yet assigned to domain
        # We need a user who is NOT already a member. Create one inline.
        fresh_user_id = f"assign-target-{uuid.uuid4().hex[:8]}"
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users (id, name, role) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (fresh_user_id, "Assign Target", "reader"),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

        resp = httpx.post(
            f"{BASE_URL}/domains/{domain_id}/members",
            json={"user_id": fresh_user_id, "role": "reader"},
            headers=admin_headers,
            timeout=TIMEOUT,
        )
        assert resp.status_code == 201, (
            f"Expected 201 for admin role assignment, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["user_id"] == fresh_user_id
        assert body["role"] == "reader"


# ═══════════════════════════════════════════════════════════════
# Scenario 7: Reader tries to assign a role → expect 403
# ═══════════════════════════════════════════════════════════════
class TestReaderCannotAssignRole:
    """A reader should NOT be able to assign roles (requires domain_admin)."""

    def test_reader_assign_role_returns_403(self, assigned_users, test_domain):
        reader = assigned_users["test_reader"]
        domain_id = test_domain["id"]

        resp = httpx.post(
            f"{BASE_URL}/domains/{domain_id}/members",
            json={"user_id": "some-nonexistent-user", "role": "reader"},
            headers=reader["headers"],
            timeout=TIMEOUT,
        )
        assert resp.status_code == 403, (
            f"Expected 403 for reader role assignment, got {resp.status_code}: {resp.text}"
        )


# ═══════════════════════════════════════════════════════════════
# Scenario 8: Contributor queries from a domain they don't belong to → 403
# ═══════════════════════════════════════════════════════════════
class TestContributorCrossDomainBlocked:
    """A contributor must NOT be able to query a domain they are not a member of."""

    def test_contributor_cross_domain_query_returns_403(
        self, assigned_users, admin_headers, cleanup_domains
    ):
        contrib = assigned_users["test_contrib"]

        # Create a separate domain that the contributor is NOT a member of
        other_domain_name = f"other-domain-{uuid.uuid4().hex[:8]}"
        create_resp = httpx.post(
            f"{BASE_URL}/domains",
            json={"name": other_domain_name, "description": "No contributor access"},
            headers=admin_headers,
            timeout=TIMEOUT,
        )
        assert create_resp.status_code == 201
        other_domain_id = create_resp.json()["id"]
        cleanup_domains.append(other_domain_id)  # schedule for DB cleanup

        # Contributor tries to generate a query against this domain
        resp = httpx.post(
            f"{BASE_URL}/generate/query",
            json={"query": "What is this about?", "domain_id": str(other_domain_id)},
            headers=contrib["headers"],
            timeout=TIMEOUT,
        )
        assert resp.status_code == 403, (
            f"Expected 403 for cross-domain query, got {resp.status_code}: {resp.text}"
        )


# ═══════════════════════════════════════════════════════════════
# Scenario 9: Admin accesses a domain they are not a member of → 403
# ═══════════════════════════════════════════════════════════════
class TestNonMemberAdminBlockedOnDomain:
    """A domain_admin (NOT system_admin) must NOT access a domain
    they are not a member of."""

    def test_domain_admin_non_member_returns_403(
        self, assigned_users, admin_headers, cleanup_domains
    ):
        domadmin = assigned_users["test_domadmin"]

        # Create a domain that test_domadmin is NOT a member of
        isolated_name = f"isolated-{uuid.uuid4().hex[:8]}"
        create_resp = httpx.post(
            f"{BASE_URL}/domains",
            json={"name": isolated_name, "description": "Isolated domain"},
            headers=admin_headers,
            timeout=TIMEOUT,
        )
        assert create_resp.status_code == 201
        isolated_id = create_resp.json()["id"]
        cleanup_domains.append(isolated_id)  # schedule for DB cleanup

        # test_domadmin tries to GET this domain
        resp = httpx.get(
            f"{BASE_URL}/domains/{isolated_id}",
            headers=domadmin["headers"],
            timeout=TIMEOUT,
        )
        assert resp.status_code == 403, (
            f"Expected 403 for non-member domain_admin, got {resp.status_code}: {resp.text}"
        )


# ═══════════════════════════════════════════════════════════════
# Scenario 10: Reader asks a question in their domain → expect 200
# ═══════════════════════════════════════════════════════════════
class TestReaderCanQueryOwnDomain:
    """A reader should be able to query (generate) in a domain they belong to."""

    def test_reader_query_own_domain(self, assigned_users, test_domain):
        reader = assigned_users["test_reader"]
        domain_id = test_domain["id"]

        resp = httpx.post(
            f"{BASE_URL}/generate/query",
            json={
                "query": "What documents are in this domain?",
                "domain_id": str(domain_id),
            },
            headers=reader["headers"],
            timeout=TIMEOUT,
        )
        # The request should be authorized (200).
        # It may return 200 with empty citations or a "no documents" answer,
        # or possibly 502/504 if the LLM is unreachable — but NOT 401 or 403.
        assert resp.status_code not in (401, 403), (
            f"Reader should have access to own domain, got {resp.status_code}: {resp.text}"
        )
```

---

### Task 2 — Load Test Script (Locust)

#### [NEW] [load_test.py](file:///d:/Personal/Fixed Solutions/Project Files/v6/tests/load_test.py)

Updated with multi-role users, `catch_response` for 404 doc status.

```python
"""
load_test.py — Locust Load Test Script (Sprint 4, Task 2)

Simulates multiple concurrent users with different roles hitting the RAG system.

Endpoints tested:
  - POST /domains/auth/login          (weight=2)
  - POST /generate/query              (weight=3 — heaviest)
  - GET  /ingest/{document_id}        (weight=2 — 404 treated as success)
  - GET  /domains/                    (weight=3)

Install:
    pip install locust

Run (Web UI):
    locust -f tests/load_test.py --host=http://localhost:8000

    Then open http://localhost:8089 in the browser:
      - Number of users: 10
      - Ramp up (users/sec): 2
      - Increase to 50 users for sustained load test

Run (CLI headless, 3 minutes):
    locust -f tests/load_test.py --host=http://localhost:8000 \
           --users 50 --spawn-rate 5 --run-time 3m \
           --headless --csv=tests/load_results

Prerequisites:
    - All services running: python run_services.py --worker --evaluation
    - Users "admin", "test_reader", "test_contrib" must exist in the
      users table. The RBAC test suite (test_rbac.py) seeds test_reader
      and test_contrib. Run RBAC tests first, or seed manually:
        INSERT INTO users (id, name, role) VALUES
          ('test_reader', 'Test Reader', 'reader'),
          ('test_contrib', 'Test Contributor', 'contributor');

Pass/fail criteria:
    - p95 response time < 3000 ms
    - Error rate < 5%

Metrics to record:
    - Requests per second (RPS)
    - Median response time (p50)
    - 95th percentile response time (p95)
    - 99th percentile response time (p99)
    - Failure rate (%)
    - Total request count
"""

import random
import logging

from locust import HttpUser, task, between, events

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Configuration — multiple users with different roles
# ──────────────────────────────────────────────────────────────
LOGIN_USERS = ["admin", "test_reader", "test_contrib"]

SAMPLE_DOCUMENT_ID = "nonexistent-doc-id"  # Expected 404 — handled gracefully


class RAGSystemUser(HttpUser):
    """
    Simulates a real user interacting with the RAG system.

    On start: picks a random user identity, logs in, caches the JWT token,
    and discovers an available domain.
    During test: hits various endpoints with realistic wait times.
    """

    # Wait between 1 and 3 seconds between tasks (simulates think time)
    wait_time = between(1, 3)

    def on_start(self):
        """Called once per simulated user — performs login and setup."""
        self.token = None
        self.domain_id = None
        self.document_id = SAMPLE_DOCUMENT_ID
        self.user_identity = random.choice(LOGIN_USERS)

        # Login to get JWT token
        resp = self.client.post(
            "/domains/auth/login",
            json={"user_id": self.user_identity},
            name="/domains/auth/login [setup]",
        )
        if resp.status_code == 200:
            data = resp.json()
            self.token = data.get("token")
            logger.info("Logged in as %s (role: %s)", self.user_identity, data.get("role"))
        else:
            logger.error(
                "Login failed for %s: %s %s",
                self.user_identity, resp.status_code, resp.text,
            )
            return

        # Discover available domains
        if self.token:
            resp = self.client.get(
                "/domains/",
                headers=self._auth_headers(),
                name="/domains/ [setup]",
            )
            if resp.status_code == 200:
                domains = resp.json()
                if domains:
                    self.domain_id = domains[0]["id"]
                    logger.info("Using domain: %s", self.domain_id)

    def _auth_headers(self) -> dict:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {}

    # ── Task 1: Login ─────────────────────────────────────────
    @task(2)
    def login(self):
        """POST /domains/auth/login — authenticate a user."""
        user = random.choice(LOGIN_USERS)
        self.client.post(
            "/domains/auth/login",
            json={"user_id": user},
            name="POST /domains/auth/login",
        )

    # ── Task 2: Generate (RAG query) ─────────────────────────
    @task(3)
    def generate_query(self):
        """POST /generate/query — the main RAG generation endpoint."""
        if not self.token or not self.domain_id:
            return

        queries = [
            "What is the main topic of the documents?",
            "Summarize the key findings.",
            "What are the recommendations?",
            "Explain the methodology used.",
            "What are the conclusions?",
        ]
        query = random.choice(queries)

        self.client.post(
            "/generate/query",
            json={
                "query": query,
                "domain_id": self.domain_id,
                "max_tokens": 256,
                "stream": False,
            },
            headers=self._auth_headers(),
            name="POST /generate/query",
            timeout=30,
        )

    # ── Task 3: Check document status ─────────────────────────
    @task(2)
    def check_document_status(self):
        """GET /ingest/{document_id} — poll document processing status.
        Uses catch_response to mark expected 404 as success (we're
        testing endpoint performance, not document existence)."""
        if not self.token:
            return

        with self.client.get(
            f"/ingest/{self.document_id}",
            headers=self._auth_headers(),
            name="GET /ingest/{document_id}",
            catch_response=True,
        ) as resp:
            if resp.status_code == 404:
                # Expected for our placeholder doc ID — not a system failure
                resp.success()
            elif resp.status_code >= 400:
                resp.failure(f"Unexpected error: {resp.status_code}")

    # ── Task 4: List domains ──────────────────────────────────
    @task(3)
    def list_domains(self):
        """GET /domains/ — list user's accessible domains."""
        if not self.token:
            return

        self.client.get(
            "/domains/",
            headers=self._auth_headers(),
            name="GET /domains/",
        )


# ──────────────────────────────────────────────────────────────
# Event listener: print summary & pass/fail at end
# ──────────────────────────────────────────────────────────────
@events.quitting.add_listener
def check_results(environment, **kwargs):
    """Evaluate pass/fail criteria after the test run."""
    stats = environment.runner.stats

    total_requests = stats.total.num_requests
    total_failures = stats.total.num_failures
    p95 = stats.total.get_response_time_percentile(0.95) or 0
    error_rate = (total_failures / total_requests * 100) if total_requests > 0 else 0

    print("\n" + "=" * 70)
    print("  LOAD TEST RESULTS SUMMARY")
    print("=" * 70)
    print(f"  Total requests:     {total_requests}")
    print(f"  Total failures:     {total_failures}")
    print(f"  Error rate:         {error_rate:.2f}%")
    print(f"  p95 response time:  {p95:.0f} ms")
    print(f"  Median (p50):       {stats.total.get_response_time_percentile(0.50) or 0:.0f} ms")
    print(f"  p99 response time:  {stats.total.get_response_time_percentile(0.99) or 0:.0f} ms")
    print(f"  RPS (avg):          {stats.total.current_rps:.1f}")
    print("-" * 70)

    passed = True
    if p95 > 3000:
        print(f"  ❌ FAIL: p95 response time {p95:.0f}ms exceeds 3000ms threshold")
        passed = False
    else:
        print(f"  ✅ PASS: p95 response time {p95:.0f}ms within 3000ms threshold")

    if error_rate > 5:
        print(f"  ❌ FAIL: Error rate {error_rate:.2f}% exceeds 5% threshold")
        passed = False
    else:
        print(f"  ✅ PASS: Error rate {error_rate:.2f}% within 5% threshold")

    if passed:
        print("\n  🎉 OVERALL: PASSED")
    else:
        print("\n  💥 OVERALL: FAILED")
    print("=" * 70)
```

---

### Task 3 — UAT Plan

#### [NEW] [UAT_plan.md](file:///d:/Personal/Fixed Solutions/Project Files/v6/docs/UAT_plan.md)

```markdown
# User Acceptance Testing (UAT) Plan

**Project:** Multi-Domain RAG System  
**Sprint:** 4 — Testing & Compliance  
**Date:** June 2026  
**Environment:** http://localhost:8000 (API) / http://localhost:3000 (UI)  
**Prepared by:** Dev2 — Testing & Compliance

---

## 1. Test Scope

This UAT plan covers all user-facing features of the RAG system including authentication, domain management, document handling, question answering, evaluation, RBAC enforcement, and error handling.

## 2. Prerequisites

- All services running: `python run_services.py --worker --evaluation`
- React frontend running: `cd rag-ui && npm run dev`
- At least one domain with uploaded and processed documents
- Users with admin, contributor, and reader roles seeded in the system

---

## 3. Test Scenarios

| Test ID | Feature | Test Steps | Expected Result | Actual Result | Pass/Fail |
|---------|---------|------------|-----------------|---------------|-----------|
| UAT-01 | Dev Auth Login | 1. Open http://localhost:3000 <br> 2. Enter user ID "admin" <br> 3. Click Login | User is authenticated, redirected to dashboard, JWT token stored | | |
| UAT-02 | Dev Auth Login — Invalid User | 1. Open login page <br> 2. Enter non-existent user ID "fakeuser999" <br> 3. Click Login | Error message displayed: "User not found" (HTTP 401) | | |
| UAT-03 | Keycloak Login (if configured) | 1. Open login page <br> 2. Click "Login with Keycloak" <br> 3. Enter Keycloak credentials | User is authenticated via Keycloak, redirected to dashboard | | |
| UAT-04 | Domain Creation | 1. Login as admin <br> 2. Navigate to Domains page <br> 3. Click "Create Domain" <br> 4. Enter name "UAT Test Domain" and description <br> 5. Submit | Domain created successfully, appears in domain list with status "active" | | |
| UAT-05 | Domain Creation — Duplicate Name | 1. Login as admin <br> 2. Try to create a domain with an existing name | Error message: "Domain already exists" (HTTP 409) | | |
| UAT-06 | Document Upload — PDF | 1. Login as contributor <br> 2. Select a domain <br> 3. Click "Upload Document" <br> 4. Select a valid PDF file (< 50 MB) <br> 5. Submit | Upload accepted (HTTP 202), document appears with status "pending" | | |
| UAT-07 | Document Upload — DOCX | 1. Login as contributor <br> 2. Select a domain <br> 3. Upload a valid DOCX file | Upload accepted (HTTP 202), document appears with status "pending" | | |
| UAT-08 | Document Upload — Invalid File Type | 1. Login as contributor <br> 2. Try to upload a .exe or .zip file | Error message: "Unsupported file type" (HTTP 400) | | |
| UAT-09 | Document Upload — File Too Large | 1. Login as contributor <br> 2. Try to upload a file exceeding 50 MB | Error message: "File exceeds 50 MB limit" (HTTP 400) | | |
| UAT-10 | Document Processing Status — Pending to Done | 1. Upload a valid PDF <br> 2. Observe status column <br> 3. Wait for worker to process <br> 4. Refresh page | Status transitions: pending → processing → done. Chunk count > 0 when done. | | |
| UAT-11 | Document Processing — Error State | 1. Upload a corrupted or password-protected PDF <br> 2. Wait for worker to attempt processing | Status changes to "failed", error_msg column shows reason | | |
| UAT-12 | Ask a Question — With Documents | 1. Login as reader <br> 2. Select a domain with processed documents <br> 3. Enter question: "What is this document about?" <br> 4. Submit | Answer is generated with citations. Each citation shows filename, page number, and relevance score. | | |
| UAT-13 | Ask a Question — Empty Domain | 1. Login as reader <br> 2. Select a domain with no documents <br> 3. Ask a question | System responds with appropriate message (e.g., "No relevant documents found") or an answer indicating no context is available | | |
| UAT-14 | Citation Display | 1. Ask a question in a domain with documents <br> 2. Review the citations section | Each citation shows: filename, page number (if available), relevance score, and a text snippet. Citations are clickable/expandable. | | |
| UAT-15 | Evaluation Scores in Dashboard | 1. Login as admin <br> 2. Navigate to Quality Dashboard <br> 3. Review recent evaluations | Evaluation logs show query ID, scores (faithfulness, relevance, completeness), overall score, and model used | | |
| UAT-16 | RBAC — Reader Cannot Upload | 1. Login as reader <br> 2. Try to upload a document to a domain | Upload is blocked. UI hides upload button or shows error on API call (HTTP 403) | | |
| UAT-17 | RBAC — Reader Cannot Delete Domain | 1. Login as reader <br> 2. Try to delete or archive a domain | Action is blocked (HTTP 403). No delete button visible or error shown on attempt. | | |
| UAT-18 | RBAC — Contributor Can Upload | 1. Login as contributor <br> 2. Upload a document to assigned domain | Upload succeeds (HTTP 202) | | |
| UAT-19 | RBAC — Contributor Cannot Manage Members | 1. Login as contributor <br> 2. Try to assign a role to another user on the domain | Action is blocked (HTTP 403) | | |
| UAT-20 | RBAC — Admin Can Manage Members | 1. Login as admin <br> 2. Navigate to domain members <br> 3. Assign reader role to a new user | Member assigned successfully (HTTP 201), appears in member list | | |
| UAT-21 | RBAC — Cross-Domain Isolation | 1. Login as reader on Domain A <br> 2. Try to query Domain B (not assigned) | Query is blocked (HTTP 403). User only sees domains they are assigned to in the list. | | |
| UAT-22 | Error Handling — Service Down | 1. Stop the worker service <br> 2. Upload a document | Upload accepted but document stays in "pending" forever. System does not crash. | | |
| UAT-23 | Error Handling — LLM Unreachable | 1. Set an invalid GROQ_API_KEY in .env <br> 2. Restart services <br> 3. Ask a question | System returns a clear error message (HTTP 502) indicating LLM is unavailable | | |
| UAT-24 | Document Deletion | 1. Login as contributor or admin <br> 2. Select a processed document <br> 3. Click Delete | Document is removed from the list. Chunks deleted from PostgreSQL and Qdrant. | | |
| UAT-25 | Session Persistence | 1. Login successfully <br> 2. Close browser tab <br> 3. Reopen http://localhost:3000 | If JWT is still valid (24-hour expiry), user remains logged in. Otherwise redirected to login. | | |

---

## 4. Defect Tracking

| Defect ID | Test ID | Description | Severity | Status | Assigned To |
|-----------|---------|-------------|----------|--------|-------------|
| | | | | | |

**Severity Levels:** Critical / High / Medium / Low

---

## 5. Sign-Off

| Role | Name | Signature | Date |
|------|------|-----------|------|
| QA Lead | ______________ | ______________ | ______________ |
| Dev Lead | ______________ | ______________ | ______________ |
| Product Owner | ______________ | ______________ | ______________ |
| Project Manager | ______________ | ______________ | ______________ |

**UAT Status:** ☐ Passed — All scenarios pass  |  ☐ Passed with conditions  |  ☐ Failed — Blockers found

**Notes:**
_____________________________________________________________
_____________________________________________________________
_____________________________________________________________
```

---

### Task 4 — AI Governance Policy

#### [NEW] [AI_governance_policy.md](file:///d:/Personal/Fixed Solutions/Project Files/v6/docs/AI_governance_policy.md)

```markdown
# AI Governance Policy

**System:** Multi-Domain RAG (Retrieval-Augmented Generation) System  
**Version:** 1.0  
**Effective Date:** June 2026  
**Owner:** Project Lead  
**Review Cycle:** Every 6 months or after any major system change

---

## 1. Purpose

This policy governs the responsible use of the Multi-Domain RAG System. The system allows authorized users to upload organizational documents into isolated knowledge domains and ask questions that are answered exclusively from those uploaded documents — not from general internet knowledge. This policy ensures that the system is used ethically, securely, and in compliance with organizational standards.

## 2. Acceptable Use

- **Document-grounded Q&A:** Use the system to ask questions about content contained in uploaded documents. The system retrieves relevant passages and generates answers with source citations.
- **Authorized domains only:** Users may only access knowledge domains they have been explicitly assigned to by an administrator.
- **Supported content:** Upload PDF, DOCX, CSV, and image files (PNG, JPG) containing organizational, educational, or research content.
- **Multilingual queries:** The system supports Arabic and English. Users may ask questions and upload documents in either language.
- **Quality review:** Use the evaluation dashboard to review answer quality scores and identify areas where document coverage may be insufficient.

## 3. Prohibited Use

- **Generating content not grounded in documents:** Do not treat the system as a general-purpose AI assistant. It is designed to answer only from uploaded material.
- **Uploading sensitive personal data:** Do not upload documents containing personally identifiable information (PII), health records, financial credentials, or classified material unless approved by your data protection officer.
- **Circumventing access controls:** Do not share JWT tokens, attempt to access domains you are not assigned to, or bypass the RBAC system in any way.
- **Automated scraping or abuse:** Do not use scripts to extract bulk answers or overload the system beyond normal usage patterns without authorization.
- **Relying on answers for critical decisions:** Do not use system outputs as the sole basis for legal, medical, financial, or safety-critical decisions without independent verification.

## 4. Data Handling

- **Domain isolation:** Documents uploaded to one domain are never visible to users of another domain. Each domain maintains a separate vector index and access control list.
- **Cloud LLM processing:** When using the Groq cloud API, query text and retrieved document chunks are sent to Groq's servers for answer generation. Organizations handling highly sensitive documents should consider using the Ollama local LLM option to keep all data on-premises.
- **Storage:** Uploaded files are stored on the server's local filesystem. Document chunks and metadata are stored in PostgreSQL. Vector embeddings are stored in Qdrant.
- **Audit logging:** Every query, answer, and evaluation score is logged in the `rag_query_logs` PostgreSQL table with timestamps, user IDs, and domain IDs. These logs support compliance auditing and quality monitoring.
- **Retention:** Uploaded documents and query logs are retained indefinitely unless manually deleted by a domain administrator. Organizations should establish their own data retention schedules.

## 5. Known Limitations

- **Answers are only as good as the documents.** If the uploaded documents do not contain the answer, the system may produce incomplete or incorrect responses. Always check citations.
- **No real-time data.** The system only knows about documents that have been uploaded and processed. It has no access to live databases, websites, or email.
- **OCR accuracy.** For scanned documents or images, answer quality depends on OCR accuracy. Poor-quality scans may produce unreliable text extraction.
- **Hallucination risk.** Although the system is designed to ground answers in source documents, the underlying LLM may occasionally generate plausible-sounding but unsupported statements. Citations should always be verified.
- **Language coverage.** While Arabic and English are supported, performance may vary for mixed-language documents or less common dialects.

## 6. User Responsibilities

- Verify all answers against the cited source documents before acting on them.
- Report inaccurate answers or suspicious system behavior to the system administrator.
- Do not share your authentication credentials or JWT tokens with others.
- Upload only documents you are authorized to share within the assigned domain.
- Use the system in accordance with your organization's acceptable use policy.

## 7. Administrator Responsibilities

- Assign users to domains with the minimum role required for their function (principle of least privilege).
- Regularly review domain membership and remove users who no longer require access.
- Monitor the evaluation dashboard for low-scoring answers and investigate root causes.
- Review audit logs periodically for unusual access patterns.
- Keep the system updated and apply security patches promptly.
- When handling sensitive documents, configure the system to use the local Ollama LLM to prevent data from leaving the server.
- Establish and enforce data retention policies appropriate to your organization.

---

*This policy is a living document and will be updated as the system evolves. All users are expected to read and comply with this policy before using the system.*
```

---

### Task 5 — End-User Documentation

#### [NEW] [user_guide.md](file:///d:/Personal/Fixed Solutions/Project Files/v6/docs/user_guide.md)

```markdown
# RAG System — User Guide

**Version:** 1.0  
**Last Updated:** June 2026  
**Application URL:** http://localhost:3000

---

## Table of Contents

1. [How to Log In](#1-how-to-log-in)
2. [How to Create a Knowledge Domain](#2-how-to-create-a-knowledge-domain)
3. [How to Upload a Document](#3-how-to-upload-a-document)
4. [How to Check if a Document Was Processed](#4-how-to-check-if-a-document-was-processed)
5. [How to Ask a Question and Read the Answer](#5-how-to-ask-a-question-and-read-the-answer)
6. [How to Understand Citations](#6-how-to-understand-citations)
7. [What to Do When an Error Appears](#7-what-to-do-when-an-error-appears)
8. [Frequently Asked Questions (FAQ)](#frequently-asked-questions-faq)

---

## 1. How to Log In

1. Open your web browser and go to **http://localhost:3000**.
2. You will see the login page.
3. Enter your **User ID** in the text field. This is the ID or username given to you by your administrator (for example: `admin`, `reader1`, or your unique user ID).
4. Click the **Login** button.
5. If your credentials are correct, you will be redirected to the main dashboard.
6. If you see an error message saying "User not found", contact your administrator to make sure your account has been created.

> **Note:** Your login session lasts 24 hours. After that, you will need to log in again.

SCREENSHOT — Login page with user ID field and login button

---

## 2. How to Create a Knowledge Domain

> **Who can do this:** Only administrators can create domains.

A domain is a separate knowledge space. Each domain has its own documents and its own set of users. Think of it as a private folder for a specific team or project.

1. After logging in, click on **Domains** in the navigation menu.
2. Click the **Create Domain** button.
3. Enter a **Domain Name** (for example: "HR Policies" or "Engineering Docs").
4. Optionally enter a **Description** to help users understand what this domain is for.
5. Click **Create**.
6. Your new domain will appear in the domain list with status **Active**.

SCREENSHOT — Domain creation form with name and description fields

---

## 3. How to Upload a Document

> **Who can do this:** Contributors and administrators.

1. Navigate to the **Documents** page from the navigation menu.
2. Select the **domain** you want to upload to from the dropdown or domain list.
3. Click the **Upload Document** button.
4. Select a file from your computer. Supported file types:
   - **PDF** (`.pdf`)
   - **Word Document** (`.docx`)
   - **CSV** (`.csv`)
   - **Images** (`.png`, `.jpg`, `.jpeg`)
5. Maximum file size: **50 MB**.
6. Click **Upload** or **Submit**.
7. You will see a confirmation message: "Document accepted. Processing has been queued."
8. The document will appear in the document list with status **Pending**.

SCREENSHOT — Document upload interface with file selection and domain dropdown

---

## 4. How to Check if a Document Was Processed

After uploading, the system needs time to read your document, extract the text, and prepare it for searching. This usually takes a few seconds to a few minutes depending on the document size.

1. Go to the **Documents** page.
2. Find your document in the list.
3. Check the **Status** column:
   - **Pending** — The document is waiting in the queue.
   - **Processing** — The system is currently reading and indexing the document.
   - **Done** — The document is ready. You can now ask questions about it.
   - **Failed** — Something went wrong. Check the error message column for details.
4. Once the status shows **Done**, you will also see a **Chunk Count** (the number of text pieces the document was split into).

> **Tip:** If a document stays in "Pending" for more than 10 minutes, the worker service may not be running. Contact your administrator.

SCREENSHOT — Documents list showing status column with different statuses

---

## 5. How to Ask a Question and Read the Answer

> **Who can do this:** Readers, contributors, and administrators.

1. Navigate to the **Chat** page from the navigation menu.
2. Select the **domain** you want to ask about. You can only query domains you have been assigned to.
3. Type your question in the text box (for example: "What is the vacation policy?").
4. Click **Send** or press **Enter**.
5. Wait a few seconds for the system to:
   - Search your domain's documents for relevant passages
   - Generate an answer based on what it found
6. The answer will appear below your question.
7. Below the answer, you will see **citations** — the specific document passages the answer was based on.

> **Important:** The system only answers from documents that have been uploaded to the selected domain. It does not use general internet knowledge.

SCREENSHOT — Chat interface with question input, answer display, and citations

---

## 6. How to Understand Citations

Every answer includes citations that show exactly where the information came from. Here is what each part means:

| Field | What It Means |
|-------|---------------|
| **Filename** | The name of the document the passage was found in (e.g., `HR_Policy_2026.pdf`) |
| **Page** | The page number in the original document where the passage appears (if available) |
| **Score** | A relevance score between 0 and 1. Higher scores mean the passage is more relevant to your question. A score above 0.7 is generally good. |
| **Text** | A short snippet of the actual text from the document that was used to generate the answer |

**How to read citations:**

- If the score is **above 0.7**: The system is confident this passage is relevant.
- If the score is **between 0.4 and 0.7**: The passage may be partially relevant. Read it carefully.
- If the score is **below 0.4**: The passage may not be very relevant. The system included it because nothing better was found.

> **Tip:** Always read at least the top citation to verify the answer. If the citation text doesn't match the answer, the answer may be inaccurate.

SCREENSHOT — Citation display showing filename, page, score, and text snippet

---

## 7. What to Do When an Error Appears

| Error Message | What It Means | What to Do |
|---------------|---------------|------------|
| "User not found" | Your user ID doesn't exist in the system | Contact your administrator to create your account |
| "Unsupported file type" | You tried to upload a file type that isn't supported | Convert your file to PDF, DOCX, CSV, PNG, or JPG |
| "File exceeds 50 MB limit" | Your file is too large | Split the file into smaller parts or compress it |
| "You do not have access to this domain" | You don't have permission for this domain | Ask a domain administrator to assign you a role |
| "You do not have contributor or higher access" | You are a reader trying to upload | Ask an administrator to upgrade your role to contributor |
| "Retrieval timed out" | The search took too long | Try again with a simpler or shorter question |
| "Judge LLM unavailable" | The AI evaluation service is not responding | This only affects quality scoring, not your answers. Report to admin. |
| "Domain service unreachable" | A backend service is down | Wait a few minutes and try again. If it persists, contact your administrator. |
| Document stuck in "Pending" | The worker service may not be running | Contact your administrator |

> **General advice:** If you see an unexpected error, try refreshing the page first. If the error persists, take a screenshot and send it to your administrator.

SCREENSHOT — Example error message displayed in the UI

---

## Frequently Asked Questions (FAQ)

### Q1: Can the system answer questions about topics not in my documents?

**No.** The system only answers based on the documents uploaded to your domain. If the information is not in any document, the system will either say it cannot find an answer or provide a response indicating no relevant content was found. It does not use general internet knowledge.

### Q2: How long does it take to process an uploaded document?

It depends on the document size and type. A 10-page PDF typically takes 10–30 seconds. Large documents (100+ pages) or scanned PDFs that require OCR may take several minutes. You can monitor the status on the Documents page.

### Q3: Can I ask questions in Arabic?

**Yes.** The system supports both Arabic and English. You can upload documents in either language and ask questions in either language. For best results, ask questions in the same language as the document content.

### Q4: What happens if I upload the same document twice?

Each upload creates a new document record. The system does not automatically detect duplicates. You will have two copies of the same content, which may cause duplicate citations in answers. Delete the extra copy from the Documents page.

### Q5: Can other users in a different domain see my documents?

**No.** Each domain is completely isolated. Users in Domain A cannot see, search, or access documents in Domain B. Only users who have been explicitly assigned to a domain by an administrator can access its content.

---

*If you have additional questions, please contact your system administrator.*
```

---

### Task 6 — Go-Live Checklist

#### [NEW] [go_live_checklist.md](file:///d:/Personal/Fixed Solutions/Project Files/v6/docs/go_live_checklist.md)

```markdown
# Go-Live Checklist

**Project:** Multi-Domain RAG System  
**Target Date:** _______________  
**Prepared by:** Dev2 — Testing & Compliance

---

## Instructions

Complete every item below before going live. Check each box when done. Each item must be verified by the person listed in the **Responsible** column. Do not skip items — mark as N/A with justification if not applicable.

---

## 1. Infrastructure Checks

| ☐ | Item | Verification | Responsible |
|---|------|-------------|-------------|
| ☐ | PostgreSQL 17 is running on the production server on port 5434 | Run `psql -h localhost -p 5434 -U postgres -c "SELECT version();"` and confirm version 17.x | Dev1 (Infra) |
| ☐ | Database `domain_db` exists with all required tables | Run `\dt` in psql and verify: `domains`, `domain_roles`, `domain_configs`, `users`, `documents`, `document_chunks`, `rag_query_logs`, `evaluation_logs` | Dev1 (Infra) |
| ☐ | Redis is running on port 6379 | Run `redis-cli ping` — should return `PONG` | Dev1 (Infra) |
| ☐ | Qdrant embedded storage directory exists and is writable | Verify `data/qdrant/` directory exists with proper permissions | Dev1 (Infra) |
| ☐ | Upload directory exists and is writable | Verify `data/uploads/` directory exists with proper write permissions | Dev1 (Infra) |
| ☐ | All ML model files are present on disk | Verify embedding model, reranker model, NER model, and PaddleOCR models exist at paths specified in `.env` | Dev1 (Infra) |
| ☐ | Monolith gateway starts without errors on port 8000 | Run `python run_services.py` and confirm all services initialize without exceptions | Dev1 (Infra) |
| ☐ | Health endpoint returns OK | `curl http://localhost:8000/health` returns `{"status": "ok"}` | Dev1 (Infra) |
| ☐ | Celery worker starts and connects to Redis | Verify worker logs show "ready" and queue `ingestion` is bound | Dev1 (Infra) |
| ☐ | Evaluation worker and beat scheduler are running | Verify evaluation-worker and evaluation-beat processes are alive | Dev3 (Evaluation) |
| ☐ | React frontend builds and serves without errors | Run `cd rag-ui && npm run build` — no TypeScript or build errors | Dev1 (Infra) |
| ☐ | Frontend is accessible at http://localhost:3000 | Open in browser and verify login page loads | Dev1 (Infra) |
| ☐ | Disk space is sufficient | At least 10 GB free for document uploads, models, and database growth | Dev1 (Infra) |

---

## 2. Security Checks

| ☐ | Item | Verification | Responsible |
|---|------|-------------|-------------|
| ☐ | `.env` file is NOT committed to version control | Run `git status` and `git log -- .env` — file should be in `.gitignore` and never committed | Dev2 (Testing/Compliance) |
| ☐ | `GROQ_API_KEY` is set to a valid production key (not dev key) | Verify the key in `.env` is the production API key, not a test key | Dev2 (Testing/Compliance) |
| ☐ | `INTERNAL_API_KEY` is changed from default value | Verify the value in `.env` is NOT `rag-internal-dev-key-change-in-prod` | Dev2 (Testing/Compliance) |
| ☐ | JWT keys are generated and stored securely | Verify `data/dev/jwt_private.pem` and `jwt_public.pem` exist (dev mode) or Keycloak is configured (production mode) | Dev2 (Testing/Compliance) |
| ☐ | Keycloak is configured (if using production auth) | Verify Keycloak realm `rag-system` exists, client `domain-service` is configured, and users are provisioned | Dev2 (Testing/Compliance) |
| ☐ | All API endpoints require authentication | Run `curl http://localhost:8000/domains/` without a token — should return 401 or 403 | Dev2 (Testing/Compliance) |
| ☐ | RBAC isolation tests pass | Run `pytest tests/test_rbac.py -v` — all 10 scenarios pass | Dev2 (Testing/Compliance) |
| ☐ | Database credentials use strong passwords | Verify `POSTGRES_PASSWORD` in `.env` is not a weak default like `1234` or `postgres` | Dev2 (Testing/Compliance) |
| ☐ | CORS is configured for production frontend origin | Verify FastAPI CORS middleware allows only the production frontend URL, not `*` | Dev2 (Testing/Compliance) |

---

## 3. Testing Checks

| ☐ | Item | Verification | Responsible |
|---|------|-------------|-------------|
| ☐ | RBAC isolation tests pass (10/10) | Run `pytest tests/test_rbac.py -v` — all pass | Dev2 (Testing/Compliance) |
| ☐ | Load test passes (p95 < 3s, errors < 5%) | Run Locust load test with 50 users for 3 minutes — verify pass/fail output | Dev2 (Testing/Compliance) |
| ☐ | UAT plan completed — all 25 scenarios tested | Review `docs/UAT_plan.md` — every row has Actual Result and Pass/Fail filled in | Dev2 (Testing/Compliance) |
| ☐ | End-to-end upload → process → query flow works | Upload a PDF, wait for "done" status, ask a question, verify answer has citations | Dev2 (Testing/Compliance) |
| ☐ | Evaluation pipeline produces scores | Ask a question and verify `evaluation_logs` table has a new row with scores | Dev3 (Evaluation) |
| ☐ | Error handling tested — invalid file types rejected | Upload a .exe file — should return HTTP 400 | Dev2 (Testing/Compliance) |
| ☐ | Error handling tested — oversized files rejected | Upload a file > 50 MB — should return HTTP 400 | Dev2 (Testing/Compliance) |
| ☐ | Arabic document upload and query works | Upload an Arabic PDF, ask a question in Arabic, verify answer and citations | Dev2 (Testing/Compliance) |

---

## 4. Documentation Checks

| ☐ | Item | Verification | Responsible |
|---|------|-------------|-------------|
| ☐ | AI Governance Policy reviewed and approved | `docs/AI_governance_policy.md` exists and has been reviewed by stakeholders | Dev2 (Testing/Compliance) |
| ☐ | End-User Guide is complete | `docs/user_guide.md` covers all 7 flows with step-by-step instructions | Dev2 (Testing/Compliance) |
| ☐ | UAT Plan is complete with sign-off section | `docs/UAT_plan.md` has all test scenarios documented and sign-off table at bottom | Dev2 (Testing/Compliance) |
| ☐ | README is up to date | `README.md` reflects current architecture, ports, and setup instructions | Dev1 (Infra) |
| ☐ | `.env.example` is up to date | All required environment variables are documented in `.env.example` with descriptions | Dev1 (Infra) |
| ☐ | API documentation is accessible | OpenAPI docs at `http://localhost:8000/docs` load correctly with all endpoints listed | Dev1 (Infra) |

---

## 5. Team Sign-Off

Each team member must confirm their area is ready for production.

| Role | Name | Area | Ready? | Signature | Date |
|------|------|------|--------|-----------|------|
| Dev1 | ______________ | Infrastructure & Deployment | ☐ Yes ☐ No | ______________ | ______________ |
| Dev2 | ______________ | Testing & Compliance | ☐ Yes ☐ No | ______________ | ______________ |
| Dev3 | ______________ | Evaluation & Quality | ☐ Yes ☐ No | ______________ | ______________ |
| Project Lead | ______________ | Overall Approval | ☐ Yes ☐ No | ______________ | ______________ |

---

## Final Go/No-Go Decision

| Decision | Made By | Date | Notes |
|----------|---------|------|-------|
| ☐ **GO** — Approved for production deployment | ______________ | ______________ | |
| ☐ **NO-GO** — Issues must be resolved first | ______________ | ______________ | Blocking issues: |

---

*This checklist must be completed and signed before any production deployment. Keep this document as part of the project audit trail.*
```

---

## Execution Order

| Step | Task | File(s) to Create |
|------|------|--------------------|
| 1 | Task 1 | `tests/conftest.py` |
| 2 | Task 1 | `tests/test_rbac.py` |
| 3 | Task 2 | `tests/load_test.py` |
| 4 | Task 3 | `docs/UAT_plan.md` |
| 5 | Task 4 | `docs/AI_governance_policy.md` |
| 6 | Task 5 | `docs/user_guide.md` |
| 7 | Task 6 | `docs/go_live_checklist.md` |

## How to Run (Commands Only)

```bash
# ── Task 1 — RBAC Tests ────────────────────────────────────────────────────
pip install pytest httpx psycopg2-binary
pytest tests/test_rbac.py -v

# ── Task 2 — Load Test ─────────────────────────────────────────────────────
pip install locust

# Web UI mode (interactive ramp-up: 10 → 25 → 50 users):
locust -f tests/load_test.py --host=http://localhost:8000
# Open http://localhost:8089 in browser

# Headless mode (50 users, 3 minutes, outputs CSV):
locust -f tests/load_test.py --host=http://localhost:8000 \
       --users 50 --spawn-rate 5 --run-time 3m \
       --headless --csv=tests/load_results

# ── Load Testing Infrastructure Monitoring ─────────────────────────────────

# Step 1: Start the monitoring stack (Prometheus + Grafana + Alertmanager)
cd monitoring
docker compose -f docker-compose.monitoring.yml up -d
docker compose -f docker-compose.monitoring.yml ps
# Grafana:      http://localhost:3000  (admin / admin)
# Prometheus:   http://localhost:9091
# Alertmanager: http://localhost:9093

# Step 2: Capture baseline metrics (run BEFORE the load test, services idle)
bash monitoring/scripts/baseline.sh
# Output saved to: monitoring/baseline_results.txt

# Step 3: Run the load test (see Task 2 commands above)

# Step 4: Apply fixes if bottlenecks are found (run DURING or AFTER the test)
bash monitoring/scripts/tuning.sh A   # PostgreSQL connection pool exhaustion
bash monitoring/scripts/tuning.sh B   # Redis memory limit / eviction
bash monitoring/scripts/tuning.sh C   # Increase Uvicorn / Celery workers
bash monitoring/scripts/tuning.sh D   # Find slow queries (EXPLAIN ANALYZE)
bash monitoring/scripts/tuning.sh E   # Add missing database indexes
bash monitoring/scripts/tuning.sh ALL # Run all sections (diagnostic sweep)

# Step 5: Stop the monitoring stack
docker compose -f monitoring/docker-compose.monitoring.yml down
# To also wipe stored metrics:
docker compose -f monitoring/docker-compose.monitoring.yml down -v
```

## Verification (Manual)

| Task | How to Verify |
|------|--------------|
| Task 1 | All 10 pytest scenarios pass with green checkmarks |
| Task 2 | Locust summary shows p95 < 3000ms and error rate < 5% |
| Task 3 | `docs/UAT_plan.md` opens with 25 test scenarios in table format |
| Task 4 | `docs/AI_governance_policy.md` has all 7 sections, professional tone |
| Task 5 | `docs/user_guide.md` has 7 numbered flows with SCREENSHOT placeholders and 5 FAQ items |
| Task 6 | `docs/go_live_checklist.md` has checkboxes, responsibility assignments (Dev1/Dev2/Dev3), and sign-off section |
