"""
Shared fixtures for Sprint 4 RBAC integration tests.

Prerequisites:
  - All services running: python run_services.py --worker --evaluation
  - PostgreSQL running on port 5434 with database domain_db
  - The monolith gateway available at http://localhost:8000

Fixtures create isolated test data through direct DB seeding and API calls.
They clean up users, role assignments, and domains after the test session.
"""

import os
import uuid
from pathlib import Path
from dotenv import load_dotenv

# Load env variables from root .env
root_env = Path(__file__).resolve().parent.parent / ".env"
if root_env.exists():
    load_dotenv(root_env)
else:
    load_dotenv()

import httpx
import psycopg2
import pytest

BASE_URL = os.getenv("RAG_BASE_URL", "https://localhost:8000")
TIMEOUT = float(os.getenv("RAG_TEST_TIMEOUT", "15.0"))
DB_DSN = os.getenv(
    "SYNC_DATABASE_URL",
    "postgresql://postgres:1234@localhost:5434/domain_db",
)


def get_db_connection():
    """Return a psycopg2 connection to the domain database."""
    return psycopg2.connect(DB_DSN)


def login(user_id: str) -> dict:
    """Login via dev auth and return the full response dict."""
    resp = httpx.post(
        f"{BASE_URL}/domains/auth/login",
        json={"user_id": user_id},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def auth_header(token: str) -> dict:
    """Build an Authorization header dict from a JWT token string."""
    return {"Authorization": f"Bearer {token}"}


def delete_users(user_ids: list[str]) -> None:
    """Hard-delete disposable users and their domain role rows."""
    if not user_ids:
        return

    conn = get_db_connection()
    cur = conn.cursor()
    try:
        for user_id in user_ids:
            cur.execute("DELETE FROM domain_roles WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    finally:
        cur.close()
        conn.close()


def delete_domains(domain_ids: list[str]) -> None:
    """Hard-delete disposable domains and related domain-owned records."""
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


@pytest.fixture(scope="session")
def cleanup_users():
    """Collect throwaway user IDs and remove them after the session."""
    user_ids: list[str] = []
    yield user_ids
    delete_users(user_ids)


@pytest.fixture(scope="session")
def cleanup_domains():
    """Collect throwaway domain IDs and remove them after the session."""
    domain_ids: list[str] = []
    yield domain_ids
    delete_domains(domain_ids)


@pytest.fixture(scope="session")
def test_users() -> dict:
    """
    Insert three disposable users directly into PostgreSQL:
      - test_reader with global role reader
      - test_contrib with global role contributor
      - test_domadmin with global role domain_admin
    """
    users = {}
    specs = [
        ("test_reader", f"test_reader_{uuid.uuid4().hex[:8]}", "Test Reader RBAC", "reader"),
        (
            "test_contrib",
            f"test_contrib_{uuid.uuid4().hex[:8]}",
            "Test Contributor RBAC",
            "contributor",
        ),
        (
            "test_domadmin",
            f"test_domadmin_{uuid.uuid4().hex[:8]}",
            "Test Domain Admin RBAC",
            "domain_admin",
        ),
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
    delete_users(list(users.values()))


@pytest.fixture(scope="session")
def admin_auth() -> dict:
    """Login as the seeded admin user."""
    data = login("admin")
    assert data["token"], "Admin login failed. Is the admin user seeded in the DB?"
    return data


@pytest.fixture(scope="session")
def admin_token(admin_auth) -> str:
    return admin_auth["token"]


@pytest.fixture(scope="session")
def admin_headers(admin_token) -> dict:
    return auth_header(admin_token)


@pytest.fixture(scope="session")
def test_domain(admin_headers):
    """Create a fresh domain for RBAC testing and hard-delete it afterwards."""
    domain_name = f"rbac-test-{uuid.uuid4().hex[:8]}"
    resp = httpx.post(
        f"{BASE_URL}/domains",
        json={"name": domain_name, "description": "RBAC integration test domain"},
        headers=admin_headers,
        timeout=TIMEOUT,
    )
    assert resp.status_code == 201, f"Failed to create domain: {resp.text}"
    domain_data = resp.json()

    yield domain_data
    delete_domains([domain_data["id"]])


@pytest.fixture(scope="session")
def assigned_users(admin_headers, test_domain, test_users) -> dict:
    """
    Assign test users to the test domain and return auth material by key.

    Return shape:
        {
            "test_reader": {"user_id": "...", "token": "...", "headers": {...}},
            ...
        }
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
        resp = httpx.post(
            f"{BASE_URL}/domains/{domain_id}/members",
            json={"user_id": user_id, "role": role},
            headers=admin_headers,
            timeout=TIMEOUT,
        )
        if resp.status_code not in (201, 409):
            pytest.fail(f"Assign {key} to domain failed: {resp.text}")

        login_data = login(user_id)
        result[key] = {
            "user_id": user_id,
            "token": login_data["token"],
            "headers": auth_header(login_data["token"]),
        }

    return result
