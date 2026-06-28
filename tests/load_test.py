"""
Locust load test script for Sprint 4.

Simulates concurrent users with different roles against the RAG gateway.

Endpoints tested:
  - POST /domains/auth/login
  - GET  /domains
  - GET  /ingest/{document_id}
  - POST /generate/query

Manual run examples:
    locust -f tests/load_test.py --host=http://localhost:8000
    locust -f tests/load_test.py --host=http://localhost:8000 --users 50 --spawn-rate 5 --run-time 3m --headless --csv=tests/load_results

Prerequisites:
  - All services running: python run_services.py --worker --evaluation
  - The admin user exists. The load test creates reusable users for the
    domain_admin, contributor, and reader roles, then assigns them to a
    load-test domain.

Pass/fail criteria:
  - p95 response time < 3000 ms
  - Error rate < 5%
"""

import logging
import os
import random
import uuid

from locust import HttpUser, between, events, task
import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = float(os.getenv("LOCUST_SETUP_TIMEOUT", "30"))
ADMIN_USER = os.getenv("LOCUST_ADMIN_USER", "admin")
LOAD_DOMAIN_NAME_PREFIX = os.getenv("LOCUST_DOMAIN_PREFIX", "locust-rbac")
ROLE_USER_SPECS = {
    "system_admin": {
        "id": ADMIN_USER,
        "name": "Admin",
        "role": "system_admin",
        "create": False,
    },
    "domain_admin": {
        "id": os.getenv("LOCUST_DOMAIN_ADMIN_USER", "load_domain_admin"),
        "name": "Load Domain Admin",
        "role": "domain_admin",
        "create": True,
    },
    "contributor": {
        "id": os.getenv("LOCUST_CONTRIBUTOR_USER", "load_contributor"),
        "name": "Load Contributor",
        "role": "contributor",
        "create": True,
    },
    "reader": {
        "id": os.getenv("LOCUST_READER_USER", "load_reader"),
        "name": "Load Reader",
        "role": "reader",
        "create": True,
    },
}
LOGIN_USERS = [spec["id"] for spec in ROLE_USER_SPECS.values()]
SAMPLE_DOCUMENT_ID = os.getenv("LOCUST_SAMPLE_DOCUMENT_ID", "nonexistent-doc-id")
LOAD_TEST_DOMAIN_ID: str | None = None
QUERY_BANK = [
    "What is the main topic of the documents?",
    "Summarize the key findings.",
    "What are the recommendations?",
    "Explain the methodology used.",
    "What are the conclusions?",
]


def _base_url(environment) -> str:
    return (environment.host or "http://localhost:8000").rstrip("/")


def _login(session: requests.Session, base_url: str, user_id: str) -> dict:
    resp = session.post(
        f"{base_url}/domains/auth/login",
        json={"user_id": user_id},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _ensure_user(
    session: requests.Session,
    base_url: str,
    admin_headers: dict,
    *,
    user_id: str,
    name: str,
    role: str,
) -> None:
    resp = session.post(
        f"{base_url}/domains/admin/users",
        json={"id": user_id, "name": name, "role": role},
        headers=admin_headers,
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code not in (201, 409):
        resp.raise_for_status()


def _create_domain(
    session: requests.Session,
    base_url: str,
    admin_headers: dict,
) -> str:
    resp = session.post(
        f"{base_url}/domains",
        json={
            "name": f"{LOAD_DOMAIN_NAME_PREFIX}-{uuid.uuid4().hex[:8]}",
            "description": "Locust RBAC load-test domain",
        },
        headers=admin_headers,
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _assign_member(
    session: requests.Session,
    base_url: str,
    admin_headers: dict,
    *,
    domain_id: str,
    user_id: str,
    role: str,
) -> None:
    resp = session.post(
        f"{base_url}/domains/{domain_id}/members",
        json={"user_id": user_id, "role": role},
        headers=admin_headers,
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code not in (201, 409):
        resp.raise_for_status()


@events.test_start.add_listener
def prepare_rbac_load_data(environment, **kwargs):
    """Create one domain and one user for every RBAC role under test."""
    global LOAD_TEST_DOMAIN_ID

    base_url = _base_url(environment)
    session = requests.Session()

    admin = _login(session, base_url, ADMIN_USER)
    admin_headers = _auth_headers(admin["token"])

    for role, spec in ROLE_USER_SPECS.items():
        if spec["create"]:
            _ensure_user(
                session,
                base_url,
                admin_headers,
                user_id=spec["id"],
                name=spec["name"],
                role=spec["role"],
            )

    LOAD_TEST_DOMAIN_ID = _create_domain(session, base_url, admin_headers)

    for role, spec in ROLE_USER_SPECS.items():
        if role == "system_admin":
            continue
        _assign_member(
            session,
            base_url,
            admin_headers,
            domain_id=LOAD_TEST_DOMAIN_ID,
            user_id=spec["id"],
            role=spec["role"],
        )

    logger.info(
        "Prepared Locust RBAC domain %s for users: %s",
        LOAD_TEST_DOMAIN_ID,
        ", ".join(LOGIN_USERS),
    )


class RAGSystemUser(HttpUser):
    """A simulated user browsing domains, checking ingestion, and querying RAG."""

    wait_time = between(1, 3)

    def on_start(self):
        self.token = None
        self.domain_id = None
        self.document_id = SAMPLE_DOCUMENT_ID
        self.user_identity = random.choice(LOGIN_USERS)

        resp = self.client.post(
            "/domains/auth/login",
            json={"user_id": self.user_identity},
            name="/domains/auth/login [setup]",
        )
        if resp.status_code != 200:
            logger.error(
                "Login failed for %s: %s %s",
                self.user_identity,
                resp.status_code,
                resp.text,
            )
            return

        data = resp.json()
        self.token = data.get("token")
        logger.info("Logged in as %s with role %s", self.user_identity, data.get("role"))

        resp = self.client.get(
            "/domains",
            headers=self._auth_headers(),
            name="/domains [setup]",
        )
        if resp.status_code == 200:
            domains = resp.json()
            if domains:
                self.domain_id = LOAD_TEST_DOMAIN_ID or domains[0]["id"]
                logger.info("Using domain %s for %s", self.domain_id, self.user_identity)

    def _auth_headers(self) -> dict:
        if not self.token:
            return {}
        return {"Authorization": f"Bearer {self.token}"}

    @task(2)
    def login(self):
        """Exercise dev-auth login under load."""
        self.client.post(
            "/domains/auth/login",
            json={"user_id": random.choice(LOGIN_USERS)},
            name="POST /domains/auth/login",
        )

    @task(3)
    def list_domains(self):
        """List domains visible to the current user."""
        if not self.token:
            return

        self.client.get(
            "/domains",
            headers=self._auth_headers(),
            name="GET /domains",
        )

    @task(2)
    def check_document_status(self):
        """
        Poll document status.

        A 404 for the default placeholder document ID is expected and marked as
        success so the load test measures endpoint behavior rather than fixture
        document availability.
        """
        if not self.token:
            return

        with self.client.get(
            f"/ingest/{self.document_id}",
            headers=self._auth_headers(),
            name="GET /ingest/{document_id}",
            catch_response=True,
        ) as resp:
            if resp.status_code == 404:
                resp.success()
            elif resp.status_code >= 400:
                resp.failure(f"Unexpected status: {resp.status_code}")

    @task(3)
    def generate_query(self):
        """Submit a representative RAG query."""
        if not self.token or not self.domain_id:
            return

        self.client.post(
            "/generate/query",
            json={
                "query": random.choice(QUERY_BANK),
                "domain_id": self.domain_id,
                "max_tokens": 256,
                "stream": False,
            },
            headers=self._auth_headers(),
            name="POST /generate/query",
            timeout=30,
        )


@events.quitting.add_listener
def check_results(environment, **kwargs):
    """Print pass/fail criteria when a headless or UI run ends."""
    stats = environment.runner.stats
    total_requests = stats.total.num_requests
    total_failures = stats.total.num_failures
    p95 = stats.total.get_response_time_percentile(0.95) or 0
    p50 = stats.total.get_response_time_percentile(0.50) or 0
    p99 = stats.total.get_response_time_percentile(0.99) or 0
    error_rate = (total_failures / total_requests * 100) if total_requests else 0

    print("\n" + "=" * 70)
    print("  LOAD TEST RESULTS SUMMARY")
    print("=" * 70)
    print(f"  Total requests:     {total_requests}")
    print(f"  Total failures:     {total_failures}")
    print(f"  Error rate:         {error_rate:.2f}%")
    print(f"  Median (p50):       {p50:.0f} ms")
    print(f"  p95 response time:  {p95:.0f} ms")
    print(f"  p99 response time:  {p99:.0f} ms")
    print(f"  RPS (current):      {stats.total.current_rps:.1f}")
    print("-" * 70)

    passed = True
    if p95 > 3000:
        print(f"  FAIL: p95 response time {p95:.0f} ms exceeds 3000 ms")
        passed = False
    else:
        print(f"  PASS: p95 response time {p95:.0f} ms is within 3000 ms")

    if error_rate > 5:
        print(f"  FAIL: error rate {error_rate:.2f}% exceeds 5%")
        passed = False
    else:
        print(f"  PASS: error rate {error_rate:.2f}% is within 5%")

    print(f"\n  OVERALL: {'PASSED' if passed else 'FAILED'}")
    print("=" * 70)
