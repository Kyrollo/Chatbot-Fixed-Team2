"""
RBAC isolation integration tests for Sprint 4.

These tests verify authentication, per-domain authorization, and role-specific
permissions across domain management, ingestion, and generation workflows.

Run manually:
    pytest tests/test_rbac.py -v
"""

import io
import uuid

import httpx
import pytest

from conftest import BASE_URL, TIMEOUT, get_db_connection


class TestReaderCannotUpload:
    """A reader must not be able to upload documents."""

    def test_reader_upload_returns_403(self, assigned_users, test_domain):
        reader = assigned_users["test_reader"]
        domain_id = test_domain["id"]
        dummy_pdf = io.BytesIO(b"%PDF-1.4 dummy content for reader upload test")

        resp = httpx.post(
            f"{BASE_URL}/ingest",
            headers=reader["headers"],
            files={"file": ("reader_test.pdf", dummy_pdf, "application/pdf")},
            data={"domain_id": str(domain_id)},
            timeout=TIMEOUT,
        )

        assert resp.status_code == 403, (
            f"Expected 403 for reader upload, got {resp.status_code}: {resp.text}"
        )


class TestContributorCanUpload:
    """A contributor can upload documents to a domain they belong to."""

    def test_contributor_upload_returns_202(self, assigned_users, test_domain):
        contributor = assigned_users["test_contrib"]
        domain_id = test_domain["id"]
        dummy_pdf = io.BytesIO(b"%PDF-1.4 contributor upload test content")

        resp = httpx.post(
            f"{BASE_URL}/ingest",
            headers=contributor["headers"],
            files={"file": ("contributor_test.pdf", dummy_pdf, "application/pdf")},
            data={"domain_id": str(domain_id)},
            timeout=TIMEOUT,
        )

        assert resp.status_code == 202, (
            f"Expected 202 for contributor upload, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert "document_id" in body
        assert body["status"] == "pending"


class TestContributorCannotDeleteDomain:
    """A contributor must not archive or delete a domain."""

    def test_contributor_delete_domain_returns_403(self, assigned_users, test_domain):
        contributor = assigned_users["test_contrib"]
        domain_id = test_domain["id"]

        resp = httpx.delete(
            f"{BASE_URL}/domains/{domain_id}",
            headers=contributor["headers"],
            timeout=TIMEOUT,
        )

        assert resp.status_code == 403, (
            f"Expected 403 for contributor domain delete, got {resp.status_code}: {resp.text}"
        )


class TestAdminCanDeleteDomain:
    """A system admin can archive a domain."""

    def test_admin_delete_domain_returns_200(self, admin_headers, cleanup_domains):
        domain_name = f"delete-me-{uuid.uuid4().hex[:8]}"
        create_resp = httpx.post(
            f"{BASE_URL}/domains",
            json={"name": domain_name, "description": "Temporary delete test domain"},
            headers=admin_headers,
            timeout=TIMEOUT,
        )
        assert create_resp.status_code == 201, create_resp.text
        domain_id = create_resp.json()["id"]
        cleanup_domains.append(domain_id)

        delete_resp = httpx.delete(
            f"{BASE_URL}/domains/{domain_id}",
            headers=admin_headers,
            timeout=TIMEOUT,
        )

        assert delete_resp.status_code == 200, (
            f"Expected 200 for admin domain delete, got {delete_resp.status_code}: "
            f"{delete_resp.text}"
        )
        assert delete_resp.json()["status"] == "archived"


class TestNoTokenReturns401:
    """Protected endpoints must reject requests without usable auth."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/domains"),
            ("POST", "/ingest"),
            ("POST", "/generate/query"),
        ],
    )
    def test_no_token_returns_401_or_403(self, method, path):
        resp = httpx.request(method, f"{BASE_URL}{path}", timeout=TIMEOUT)
        assert resp.status_code in (401, 403), (
            f"Expected 401/403 for no-token {method} {path}, got {resp.status_code}"
        )

    def test_invalid_token_returns_401(self):
        resp = httpx.get(
            f"{BASE_URL}/domains",
            headers={"Authorization": "Bearer this.is.not.a.valid.jwt"},
            timeout=TIMEOUT,
        )
        assert resp.status_code == 401, (
            f"Expected 401 for invalid token, got {resp.status_code}: {resp.text}"
        )


class TestAdminCanAssignRole:
    """A domain admin or system admin can assign a role on a domain."""

    def test_admin_assign_role_returns_201(
        self, admin_headers, test_domain, cleanup_users
    ):
        domain_id = test_domain["id"]
        fresh_user_id = f"assign-target-{uuid.uuid4().hex[:8]}"
        cleanup_users.append(fresh_user_id)

        conn = get_db_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO users (id, name, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (id) DO NOTHING
                """,
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


class TestReaderCannotAssignRole:
    """A reader must not assign domain roles."""

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


class TestContributorCrossDomainBlocked:
    """A contributor must not query a domain they are not assigned to."""

    def test_contributor_cross_domain_query_returns_403(
        self, assigned_users, admin_headers, cleanup_domains
    ):
        contributor = assigned_users["test_contrib"]
        create_resp = httpx.post(
            f"{BASE_URL}/domains",
            json={
                "name": f"other-domain-{uuid.uuid4().hex[:8]}",
                "description": "No contributor access",
            },
            headers=admin_headers,
            timeout=TIMEOUT,
        )
        assert create_resp.status_code == 201, create_resp.text
        other_domain_id = create_resp.json()["id"]
        cleanup_domains.append(other_domain_id)

        resp = httpx.post(
            f"{BASE_URL}/generate/query",
            json={"query": "What is this about?", "domain_id": str(other_domain_id)},
            headers=contributor["headers"],
            timeout=TIMEOUT,
        )

        assert resp.status_code == 403, (
            f"Expected 403 for cross-domain query, got {resp.status_code}: {resp.text}"
        )


class TestNonMemberDomainAdminBlocked:
    """A domain_admin user must not access domains they are not assigned to."""

    def test_domain_admin_non_member_returns_403(
        self, assigned_users, admin_headers, cleanup_domains
    ):
        domain_admin = assigned_users["test_domadmin"]
        create_resp = httpx.post(
            f"{BASE_URL}/domains",
            json={
                "name": f"isolated-{uuid.uuid4().hex[:8]}",
                "description": "Isolated domain",
            },
            headers=admin_headers,
            timeout=TIMEOUT,
        )
        assert create_resp.status_code == 201, create_resp.text
        isolated_id = create_resp.json()["id"]
        cleanup_domains.append(isolated_id)

        resp = httpx.get(
            f"{BASE_URL}/domains/{isolated_id}",
            headers=domain_admin["headers"],
            timeout=TIMEOUT,
        )

        assert resp.status_code == 403, (
            f"Expected 403 for non-member domain_admin, got {resp.status_code}: {resp.text}"
        )


class TestReaderCanQueryOwnDomain:
    """A reader can query a domain they belong to."""

    def test_reader_query_own_domain_is_authorized(self, assigned_users, test_domain):
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

        assert resp.status_code not in (401, 403), (
            f"Reader should have access to own domain, got {resp.status_code}: "
            f"{resp.text}"
        )
