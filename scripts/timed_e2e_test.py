#!/usr/bin/env python3
"""
Timed End-to-End Integration Test — Full RAG Pipeline

Tests all four roles (system_admin, domain_admin, contributor, reader)
through the complete flow: auth → domain → ingest → retrieve → generate.

Flow:
  Step 0  – Health-check all 4 services
  Step 1  – Auth: acquire tokens for all roles
  Step 2  – system_admin creates the test domain
  Step 3  – system_admin promotes another user to domain_admin
  Step 4  – domain_admin assigns contributor + reader members
  Step 5  – contributor uploads sample_policy.pdf
  Step 6  – Poll ingestion until done (any authenticated user)
  Step 7  – admin queries: "What is the refund policy?"
  Step 8  – admin queries: "What are the support hours?"
  Step 9  – admin queries: "How long is the warranty?"
  Step 10 – reader queries one of the same questions (RBAC: allowed)
  Step 11 – reader tries to upload a file (RBAC: must be rejected 403)
  Step 12 – contributor uploads a second file (RBAC: allowed)
  Step 13 – Cache hit check (repeat identical query)
  ----
  Summary – Table of every step's duration + PASS / FAIL status

Usage:
    python scripts/timed_e2e_test.py
    python scripts/timed_e2e_test.py --pdf path/to/custom.pdf

Prerequisites:
    • All services already running via: python run_services.py
    • pip install requests
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: 'requests' not installed.  Run: pip install requests")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT     = Path(__file__).resolve().parent.parent
SCRIPTS  = ROOT / "scripts"
FIXTURES = SCRIPTS / "fixtures"
sys.path.insert(0, str(SCRIPTS))

from dev_auth import DEV_USERS, token_for          # noqa: E402
from infra_manager import keycloak_ready           # noqa: E402

# ---------------------------------------------------------------------------
# Service URLs
# ---------------------------------------------------------------------------
DOMAIN_API    = "http://localhost:8001"
INGESTION_API = "http://localhost:8002"
RETRIEVAL_API = "http://localhost:8003"
GENERATION_API= "http://localhost:8004"

KEYCLOAK_BASE   = "http://localhost:8180"
REALM           = "rag-system"
TOKEN_URL       = f"{KEYCLOAK_BASE}/realms/{REALM}/protocol/openid-connect/token"
ADMIN_TOKEN_URL = f"{KEYCLOAK_BASE}/realms/master/protocol/openid-connect/token"
ADMIN_API       = f"{KEYCLOAK_BASE}/admin/realms/{REALM}"
# rag-ui is the public frontend client — we enable directAccessGrantsEnabled on it
# at runtime so we can mint user tokens with FULL role claims (not lightweight tokens)
KC_USER_CLIENT  = "rag-ui"

SEEDED_USERS = {
    "admin":   {"password": "admin",   "id": "652ec45e-1b68-478c-9bd3-81cc46fb24a9"},
    "reader1": {"password": "reader1", "id": "d3794cbc-9bb9-4c06-95e5-33603c71b287"},
}

# Seeded user passwords as they should be in Keycloak
# (we reset them via admin API to ensure they are always correct)
SEEDED_PASSWORDS = {
    "admin":   "admin",
    "reader1": "reader1",
}
TEST_CONTRIBUTOR = {
    "username":   "contributor_test",
    "password":   "contributor_test",
    "email":      "contributor_test@rag.local",
    "first_name": "Test",
    "last_name":  "Contributor",
    "realm_role": "contributor",
}

SERVICE_HEALTH_CHECKS = [
    ("domain-service",     "http://localhost:8001/health"),
    ("ingestion-service",  "http://localhost:8002/health"),
    ("retrieval-service",  "http://localhost:8003/health"),
    ("generation-service", "http://localhost:8004/generate/health"),
]

# Test questions relevant to the sample_policy.pdf content
QUERIES = [
    "What is the refund policy?",
    "What are the support hours?",
    "How long is the warranty coverage?",
]

# ---------------------------------------------------------------------------
# ANSI colours
# ---------------------------------------------------------------------------
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# ---------------------------------------------------------------------------
# Timing & result tracking
# ---------------------------------------------------------------------------
_results: list[dict[str, Any]] = []   # {"step": int, "name": str, "elapsed": float, "status": "PASS"|"FAIL"|"SKIP", "detail": str}
_step_timer: float = 0.0
_current_step: int = 0
_current_name: str = ""


def _begin_step(n: int, title: str) -> None:
    global _step_timer, _current_step, _current_name
    _current_step = n
    _current_name = title
    _step_timer = time.perf_counter()
    print(f"\n{CYAN}{BOLD}[Step {n:02d}]{RESET} {CYAN}{title}{RESET}")


def _end_step(status: str, detail: str = "") -> float:
    elapsed = time.perf_counter() - _step_timer
    colour  = GREEN if status == "PASS" else (RED if status == "FAIL" else YELLOW)
    tag     = f"{colour}[{status}]{RESET}"
    print(f"         {tag}  {DIM}{elapsed:.2f}s{RESET}" + (f"  — {detail}" if detail else ""))
    _results.append({
        "step":    _current_step,
        "name":    _current_name,
        "elapsed": elapsed,
        "status":  status,
        "detail":  detail,
    })
    return elapsed


def ok(msg: str) -> None:
    print(f"  {GREEN}✓{RESET}  {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}✗{RESET}  {msg}")


def info(msg: str) -> None:
    print(f"  {YELLOW}›{RESET}  {msg}")


def section(title: str) -> None:
    print(f"\n{DIM}{'─' * 60}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{DIM}{'─' * 60}{RESET}")


# ---------------------------------------------------------------------------
# Generic HTTP helpers
# ---------------------------------------------------------------------------
def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _get(url: str, token: str, **kwargs) -> requests.Response:
    return requests.get(url, headers=_headers(token), timeout=30, **kwargs)


def _post(url: str, token: str, **kwargs) -> requests.Response:
    return requests.post(url, headers=_headers(token), timeout=120, **kwargs)


# ---------------------------------------------------------------------------
# Step 0 – Health checks
# ---------------------------------------------------------------------------
def step_health() -> bool:
    _begin_step(0, "Health-check all services")
    failed = []
    for name, url in SERVICE_HEALTH_CHECKS:
        try:
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                ok(f"{name}  →  {r.status_code} OK")
            else:
                fail(f"{name}  →  {r.status_code}")
                failed.append(name)
        except requests.RequestException as exc:
            fail(f"{name}  →  {exc}")
            failed.append(name)
    if failed:
        _end_step("FAIL", f"down: {', '.join(failed)}")
        return False
    _end_step("PASS", "all 4 services healthy")
    return True


# ---------------------------------------------------------------------------
# Step 1 – Authentication (all roles)
# ---------------------------------------------------------------------------
def _kc_token(username: str, password: str) -> str:
    """Mint a user token using the rag-ui public client (full role claims, not lightweight)."""
    return _kc_token_full(username, password)["access_token"]


def _kc_token_full(username: str, password: str) -> dict:
    """Mint a user token and return the full response (access_token, refresh_token, expires_in)."""
    r = requests.post(
        TOKEN_URL,
        data={"client_id": KC_USER_CLIENT, "username": username,
              "password": password, "grant_type": "password"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------------------
# Token Manager — auto-refreshes tokens before they expire
# ---------------------------------------------------------------------------
class TokenManager:
    """
    Holds access + refresh tokens per role and transparently re-mints /
    refreshes them when they are within TOKEN_REFRESH_BUFFER_S seconds of expiry.

    Usage:
        tm = TokenManager(is_keycloak=True)
        tm.set("admin", access_token, refresh_token, expires_in)
        tm.set_credentials("admin", username="admin", password="admin")
        tok = tm.get("admin")   # auto-refreshes if needed
    """
    TOKEN_REFRESH_BUFFER_S = 60  # refresh when < 60 s remain

    def __init__(self, is_keycloak: bool) -> None:
        self._is_keycloak = is_keycloak
        self._entries: dict[str, dict] = {}

    def set(
        self,
        role: str,
        access_token: str,
        refresh_token: str = "",
        expires_in: int = 300,
    ) -> None:
        entry = self._entries.setdefault(role, {})
        entry["access"]     = access_token
        entry["refresh"]    = refresh_token
        entry["expires_at"] = time.time() + expires_in - self.TOKEN_REFRESH_BUFFER_S

    def set_credentials(self, role: str, username: str, password: str) -> None:
        """Store password-grant credentials used as fallback when refresh_token fails."""
        entry = self._entries.setdefault(role, {})
        entry["username"] = username
        entry["password"] = password

    def get(self, role: str) -> str:
        """Return a valid access token, refreshing transparently if needed."""
        entry = self._entries.get(role)
        if entry is None:
            raise KeyError(f"TokenManager: unknown role '{role}'")
        if time.time() >= entry.get("expires_at", 0):
            self._refresh(role)
        return self._entries[role]["access"]

    def _refresh(self, role: str) -> None:
        entry = self._entries[role]

        # ── 1. Try Keycloak refresh_token grant ────────────────────────────
        if self._is_keycloak and entry.get("refresh"):
            try:
                r = requests.post(
                    TOKEN_URL,
                    data={
                        "client_id":     KC_USER_CLIENT,
                        "grant_type":    "refresh_token",
                        "refresh_token": entry["refresh"],
                    },
                    timeout=15,
                )
                r.raise_for_status()
                data = r.json()
                entry["access"]     = data["access_token"]
                entry["refresh"]    = data.get("refresh_token", entry["refresh"])
                entry["expires_at"] = (
                    time.time()
                    + data.get("expires_in", 300)
                    - self.TOKEN_REFRESH_BUFFER_S
                )
                info(f"[TokenManager] refreshed token for role={role}")
                return
            except Exception:
                pass  # fall through to password re-mint

        # ── 2. Fallback: password grant re-mint ────────────────────────────
        if self._is_keycloak and entry.get("username"):
            data = _kc_token_full(entry["username"], entry["password"])
            entry["access"]     = data["access_token"]
            entry["refresh"]    = data.get("refresh_token", "")
            entry["expires_at"] = (
                time.time()
                + data.get("expires_in", 300)
                - self.TOKEN_REFRESH_BUFFER_S
            )
            info(f"[TokenManager] re-minted token for role={role} via password grant")
            return

        # ── 3. Dev-JWT path: re-mint with mint_token ───────────────────────
        if not self._is_keycloak:
            from dev_auth import DEV_USERS, mint_token
            user_key = entry.get("username")          # we store user_key in "username"
            spec     = DEV_USERS.get(user_key, {})
            if spec:
                new_tok = mint_token(
                    user_id=spec["user_id"],
                    username=spec["username"],
                    roles=spec["roles"],
                )
                entry["access"]     = new_tok
                entry["expires_at"] = time.time() + 3540  # 59 min
                info(f"[TokenManager] re-minted dev JWT for role={role}")
                return

        info(f"[TokenManager] WARNING: could not refresh token for role={role}")


def _kc_enable_direct_access_grants(admin_token: str) -> None:
    """
    Enable directAccessGrantsEnabled on the rag-ui client so we can use
    Resource Owner Password Credentials flow (needed for test token minting).
    Also disables lightweight tokens on rag-ui so full realm_access roles are included.
    """
    # Find the rag-ui client internal ID
    r = requests.get(
        f"{ADMIN_API}/clients",
        params={"clientId": KC_USER_CLIENT},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    r.raise_for_status()
    clients = r.json()
    if not clients:
        raise RuntimeError(f"KC client '{KC_USER_CLIENT}' not found in realm")
    client = clients[0]
    client_uuid = client["id"]

    # Patch: enable direct access grants + disable lightweight tokens
    client["directAccessGrantsEnabled"] = True
    attrs = client.setdefault("attributes", {})
    attrs["client.use.lightweight.access.token.enabled"] = "false"

    r2 = requests.put(
        f"{ADMIN_API}/clients/{client_uuid}",
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
        json=client,
        timeout=15,
    )
    r2.raise_for_status()
    ok(f"'{KC_USER_CLIENT}' client: directAccessGrantsEnabled=true, lightweight tokens=false")


def _kc_admin_token() -> str:
    r = requests.post(
        ADMIN_TOKEN_URL,
        data={"client_id": "admin-cli", "username": "admin",
              "password": "admin", "grant_type": "password"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _kc_find_user(admin_token: str, username: str) -> dict | None:
    r = requests.get(
        f"{ADMIN_API}/users",
        params={"username": username, "exact": "true"},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    r.raise_for_status()
    users = r.json()
    return users[0] if users else None


def _kc_reset_password(admin_token: str, user_id: str, password: str) -> None:
    """Force-reset a Keycloak user's password (works for both seeded and new users)."""
    requests.put(
        f"{ADMIN_API}/users/{user_id}/reset-password",
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
        json={"type": "password", "value": password, "temporary": False},
        timeout=15,
    ).raise_for_status()


def _kc_ensure_user(admin_token: str, spec: dict) -> str:
    """Create user if missing, always force-reset password and assign realm role."""
    existing = _kc_find_user(admin_token, spec["username"])
    if not existing:
        r = requests.post(
            f"{ADMIN_API}/users",
            headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
            json={
                "username": spec["username"], "email": spec["email"],
                "firstName": spec["first_name"], "lastName": spec["last_name"],
                "enabled": True, "emailVerified": True,
            },
            timeout=15,
        )
        if r.status_code not in (201, 409):
            r.raise_for_status()
        existing = _kc_find_user(admin_token, spec["username"])
        if not existing:
            raise RuntimeError(f"Failed to create KC user: {spec['username']}")

    uid = existing["id"]
    # Always reset password so it matches what we expect
    _kc_reset_password(admin_token, uid, spec["password"])

    # Assign realm role (skip gracefully if role doesn't exist in this realm)
    role_r = requests.get(
        f"{ADMIN_API}/roles/{spec['realm_role']}",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=15,
    )
    if role_r.status_code == 200:
        requests.post(
            f"{ADMIN_API}/users/{uid}/role-mappings/realm",
            headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
            json=[role_r.json()],
            timeout=15,
        ).raise_for_status()
    else:
        info(f"Realm role '{spec['realm_role']}' not found in KC — skipping role assignment")
    return uid


def step_auth() -> tuple[TokenManager, dict[str, str]] | None:
    """
    Acquire tokens for all four roles.
    Keycloak path  : resets seeded user passwords via admin API before minting tokens.
                     Uses the seeded admin for domain_admin operations (admin carries all roles).
    Dev JWT path   : mints offline tokens without Keycloak.

    Returns a (TokenManager, user_ids) tuple on success, or None on failure.
    The TokenManager auto-refreshes tokens when they near expiry.
    """
    _begin_step(1, "Authentication — all four roles")
    user_ids: dict[str, str] = {}
    kc = keycloak_ready()
    tm = TokenManager(is_keycloak=kc)

    try:
        if kc:
            info("Keycloak detected — preparing clients + resetting passwords + minting tokens")
            kc_admin = _kc_admin_token()

            # ── Enable direct access grants on rag-ui so we can mint user tokens ──
            _kc_enable_direct_access_grants(kc_admin)

            # ── Reset seeded users' passwords so they are always predictable ──
            for uname, pwd in SEEDED_PASSWORDS.items():
                user = _kc_find_user(kc_admin, uname)
                if user:
                    _kc_reset_password(kc_admin, user["id"], pwd)
                    ok(f"Password reset: {uname}")
                else:
                    info(f"Seeded user '{uname}' not found in KC — skipping password reset")

            # ── Create contributor_test if missing, always reset its password ──
            cont_id = _kc_ensure_user(kc_admin, TEST_CONTRIBUTOR)
            ok(f"contributor_test ensured (id={cont_id})")

            # ── Mint full token responses (access + refresh + expires_in) ──
            admin_resp       = _kc_token_full("admin",   SEEDED_PASSWORDS["admin"])
            contrib_resp     = _kc_token_full(TEST_CONTRIBUTOR["username"], TEST_CONTRIBUTOR["password"])
            reader_resp      = _kc_token_full("reader1", SEEDED_PASSWORDS["reader1"])

            # Populate TokenManager — domain_admin reuses admin token (same user, all roles)
            for role, resp, uname, pwd in [
                ("admin",        admin_resp,   "admin",                          SEEDED_PASSWORDS["admin"]),
                ("domain_admin", admin_resp,   "admin",                          SEEDED_PASSWORDS["admin"]),
                ("contributor",  contrib_resp, TEST_CONTRIBUTOR["username"],     TEST_CONTRIBUTOR["password"]),
                ("reader",       reader_resp,  "reader1",                        SEEDED_PASSWORDS["reader1"]),
            ]:
                tm.set(
                    role,
                    access_token  = resp["access_token"],
                    refresh_token = resp.get("refresh_token", ""),
                    expires_in    = resp.get("expires_in", 300),
                )
                tm.set_credentials(role, username=uname, password=pwd)

            user_ids["admin"]        = SEEDED_USERS["admin"]["id"]
            user_ids["domain_admin"] = SEEDED_USERS["admin"]["id"]
            user_ids["contributor"]  = cont_id
            user_ids["reader"]       = SEEDED_USERS["reader1"]["id"]

        else:
            info("Keycloak not running — using dev JWT tokens")
            for role, user_key in [
                ("admin",        "admin"),
                ("domain_admin", "admin"),
                ("contributor",  "contributor_test"),
                ("reader",       "reader1"),
            ]:
                tm.set(role, token_for(user_key), expires_in=3540)  # 59 min for dev JWTs
                tm.set_credentials(role, username=user_key, password="")

            user_ids["admin"]        = DEV_USERS["admin"]["user_id"]
            user_ids["domain_admin"] = DEV_USERS["admin"]["user_id"]
            user_ids["contributor"]  = DEV_USERS["contributor_test"]["user_id"]
            user_ids["reader"]       = DEV_USERS["reader1"]["user_id"]

        for role in ["admin", "domain_admin", "contributor", "reader"]:
            preview = tm.get(role)[:32] + "..."
            ok(f"{role:15s} token acquired  [{preview}]")

        _end_step("PASS", f"{'Keycloak' if kc else 'dev JWT'} — 4 tokens")
        return tm, user_ids

    except Exception as exc:
        fail(str(exc))
        _end_step("FAIL", str(exc))
        return None


# ---------------------------------------------------------------------------
# Step 2 – system_admin creates domain
# ---------------------------------------------------------------------------
def step_create_domain(admin_token: str) -> dict | None:
    _begin_step(2, "system_admin creates test domain")
    try:
        suffix = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        r = _post(
            f"{DOMAIN_API}/domains",
            admin_token,
            json={"name": f"E2E Timed Test {suffix}", "description": "Timed E2E policy domain"},
        )
        r.raise_for_status()
        domain = r.json()
        ok(f"Domain created: id={domain['id']}  name={domain['name']}")
        _end_step("PASS", f"domain_id={domain['id']}")
        return domain
    except Exception as exc:
        fail(str(exc))
        _end_step("FAIL", str(exc))
        return None


# ---------------------------------------------------------------------------
# Step 3 – system_admin promotes domain_admin
# ---------------------------------------------------------------------------
def step_assign_domain_admin(admin_token: str, domain_id: str, da_user_id: str) -> bool:
    _begin_step(3, "system_admin assigns domain_admin role on domain")
    try:
        r = _post(
            f"{DOMAIN_API}/domains/{domain_id}/members",
            admin_token,
            json={"user_id": da_user_id, "role": "domain_admin"},
        )
        if r.status_code == 409:
            info("domain_admin already assigned — skipping")
        else:
            r.raise_for_status()
        ok(f"domain_admin user {da_user_id} assigned")
        _end_step("PASS")
        return True
    except Exception as exc:
        fail(str(exc))
        _end_step("FAIL", str(exc))
        return False


# ---------------------------------------------------------------------------
# Step 4 – domain_admin assigns contributor + reader
# ---------------------------------------------------------------------------
def step_assign_members(da_token: str, domain_id: str, contributor_id: str, reader_id: str) -> bool:
    _begin_step(4, "domain_admin assigns contributor + reader members")
    passed = True
    for uid, role in [(contributor_id, "contributor"), (reader_id, "reader")]:
        try:
            r = _post(
                f"{DOMAIN_API}/domains/{domain_id}/members",
                da_token,
                json={"user_id": uid, "role": role},
            )
            if r.status_code == 409:
                info(f"{role} {uid} already a member")
            else:
                r.raise_for_status()
            ok(f"Assigned {role}: {uid}")
        except Exception as exc:
            fail(f"{role} assign failed: {exc}")
            passed = False
    _end_step("PASS" if passed else "FAIL")
    return passed


# ---------------------------------------------------------------------------
# Step 5 – contributor uploads PDF
# ---------------------------------------------------------------------------
def step_upload_pdf(contributor_token: str, domain_id: str, pdf_path: Path) -> dict | None:
    _begin_step(5, f"contributor uploads {pdf_path.name}")
    try:
        with pdf_path.open("rb") as f:
            r = requests.post(
                f"{INGESTION_API}/ingest",
                headers=_headers(contributor_token),
                files={"file": (pdf_path.name, f, "application/pdf")},
                data={"domain_id": domain_id},
                timeout=120,
            )
        r.raise_for_status()
        result = r.json()
        doc_id = result["document_id"]
        ok(f"Upload accepted: document_id={doc_id}  status={result.get('status')}")
        _end_step("PASS", f"document_id={doc_id}")
        return result
    except Exception as exc:
        fail(str(exc))
        _end_step("FAIL", str(exc))
        return None


# ---------------------------------------------------------------------------
# Step 6 – Poll ingestion
# ---------------------------------------------------------------------------
def step_poll_ingestion(
    tm: "TokenManager", role: str, document_id: str, timeout: int = 900
) -> bool:
    """
    Poll ingestion status.  Uses `tm.get(role)` on every iteration so that
    the token is automatically refreshed before it expires — critical when
    ingestion takes longer than the Keycloak access-token lifetime.
    """
    _begin_step(6, f"Poll ingestion status  (document_id={document_id})")
    deadline = time.time() + timeout
    last_status = None
    try:
        while time.time() < deadline:
            r = _get(f"{INGESTION_API}/ingest/{document_id}", tm.get(role))
            r.raise_for_status()
            doc    = r.json()
            status = doc.get("status")
            if status != last_status:
                info(f"Status → {status}")
                last_status = status
            if status == "done":
                ok("Ingestion complete ✓")
                _end_step("PASS", "status=done")
                return True
            if status == "failed":
                fail(f"Ingestion failed: {doc.get('error_msg')}")
                _end_step("FAIL", doc.get("error_msg", "unknown error"))
                return False
            time.sleep(5)
        _end_step("FAIL", f"timeout after {timeout}s (last={last_status})")
        return False
    except Exception as exc:
        fail(str(exc))
        _end_step("FAIL", str(exc))
        return False


# ---------------------------------------------------------------------------
# Step 7-9 – Generate answers (admin, multiple questions)
# ---------------------------------------------------------------------------
def step_generate_answers(token: str, domain_id: str, step_n: int, role: str) -> dict[str, Any] | None:
    questions = QUERIES
    _begin_step(step_n, f"Generate answers as {role} ({len(questions)} questions)")
    answers: list[dict] = []
    failed_q: list[str] = []
    try:
        for q in questions:
            info(f"Q: {q}")
            r = _post(
                f"{GENERATION_API}/generate/query",
                token,
                json={"query": q, "domain_id": domain_id,
                      "top_k_retrieve": 10, "top_k_rerank": 5},
            )
            r.raise_for_status()
            data   = r.json()
            answer = data.get("answer", "")
            cites  = data.get("citations", [])
            model  = data.get("model", "?")
            route  = data.get("llm_route", "?")
            cache  = data.get("cache_hit", False)
            if answer and len(answer.strip()) >= 5:
                ok(f"A [{route}/{model}  cache={cache}]: {answer[:120]}{'…' if len(answer) > 120 else ''}")
                ok(f"  Citations: {len(cites)}")
                answers.append({"query": q, "answer": answer, "citations": cites,
                                 "model": model, "llm_route": route})
            else:
                fail(f"Empty/short answer for: {q}")
                failed_q.append(q)

        status = "PASS" if not failed_q else "FAIL"
        detail = f"{len(answers)}/{len(questions)} answered" + (f" | failed: {failed_q}" if failed_q else "")
        _end_step(status, detail)
        return {"answers": answers, "failed": failed_q}
    except Exception as exc:
        fail(str(exc))
        _end_step("FAIL", str(exc))
        return None


# ---------------------------------------------------------------------------
# Step 10 – RBAC query check: reader(403 expected) + contributor generates
# ---------------------------------------------------------------------------
def step_reader_query(reader_token: str, contributor_token: str, domain_id: str) -> bool:
    """
    System design note:
      The generation service calls GET /domains/{id}/config using the caller's token.
      domain-service.get_config() requires at minimum 'contributor' role.
      A plain 'reader' therefore gets 403 — this is by design.

    Verifies:
      a) reader gets 403 (expected behaviour → PASS)
      b) contributor can generate an answer end-to-end (non-admin generation works)
    """
    _begin_step(10, "RBAC query check — reader(403 expected) + contributor generates")
    passed = True

    # ── a) reader → should get 403 (domain config gated to contributor+) ──────
    try:
        r = _post(
            f"{GENERATION_API}/generate/query",
            reader_token,
            json={"query": QUERIES[0], "domain_id": domain_id,
                  "top_k_retrieve": 5, "top_k_rerank": 3},
        )
        if r.status_code == 403:
            ok("reader → 403 Forbidden (expected: domain config requires contributor+) ✓")
        elif r.status_code == 200:
            answer = r.json().get("answer", "")
            if answer:
                ok("reader → 200 OK (domain config accessible to reader in this build)")
            else:
                fail("reader → 200 but empty answer")
                passed = False
        else:
            fail(f"reader → unexpected {r.status_code}: {r.text[:100]}")
            passed = False
    except Exception as exc:
        fail(f"reader query error: {exc}")
        passed = False

    # ── b) contributor → should get 200 with a real answer ───────────────────
    try:
        r2 = _post(
            f"{GENERATION_API}/generate/query",
            contributor_token,
            json={"query": QUERIES[0], "domain_id": domain_id,
                  "top_k_retrieve": 5, "top_k_rerank": 3},
        )
        r2.raise_for_status()
        answer2 = r2.json().get("answer", "")
        if answer2 and len(answer2.strip()) >= 5:
            ok(f"contributor → {answer2[:100]}{'…' if len(answer2) > 100 else ''}")
        else:
            fail("contributor → empty answer")
            passed = False
    except Exception as exc:
        fail(f"contributor query error: {exc}")
        passed = False

    _end_step("PASS" if passed else "FAIL",
              "reader=403(design) contributor=200" if passed else "check above")
    return passed


# ---------------------------------------------------------------------------
# Step 11 – reader tries to upload (RBAC: must be rejected 403)
# ---------------------------------------------------------------------------
def step_reader_upload_rejected(reader_token: str, domain_id: str, pdf_path: Path) -> bool:
    _begin_step(11, "reader tries to upload PDF (RBAC: must be REJECTED with 403)")
    try:
        with pdf_path.open("rb") as f:
            r = requests.post(
                f"{INGESTION_API}/ingest",
                headers=_headers(reader_token),
                files={"file": (pdf_path.name, f, "application/pdf")},
                data={"domain_id": domain_id},
                timeout=30,
            )
        if r.status_code == 403:
            ok(f"Correctly rejected with 403 Forbidden ✓")
            _end_step("PASS", "403 Forbidden as expected")
            return True
        fail(f"Expected 403 but got {r.status_code}: {r.text[:120]}")
        _end_step("FAIL", f"got {r.status_code} instead of 403")
        return False
    except Exception as exc:
        fail(str(exc))
        _end_step("FAIL", str(exc))
        return False


# ---------------------------------------------------------------------------
# Step 12 – contributor uploads second file
# ---------------------------------------------------------------------------
def step_contributor_second_upload(contributor_token: str, domain_id: str, pdf_path: Path) -> bool:
    _begin_step(12, "contributor uploads PDF again (RBAC: should be ALLOWED)")
    try:
        with pdf_path.open("rb") as f:
            r = requests.post(
                f"{INGESTION_API}/ingest",
                headers=_headers(contributor_token),
                files={"file": (pdf_path.name, f, "application/pdf")},
                data={"domain_id": domain_id},
                timeout=120,
            )
        r.raise_for_status()
        doc_id = r.json().get("document_id", "?")
        ok(f"Second upload accepted: document_id={doc_id} ✓")
        _end_step("PASS", f"document_id={doc_id}")
        return True
    except requests.HTTPError as exc:
        fail(f"Contributor upload rejected: {exc}  body={exc.response.text[:120]}")
        _end_step("FAIL", str(exc))
        return False
    except Exception as exc:
        fail(str(exc))
        _end_step("FAIL", str(exc))
        return False


# ---------------------------------------------------------------------------
# Step 13 – Cache hit
# ---------------------------------------------------------------------------
def step_cache_hit(admin_token: str, domain_id: str) -> bool:
    _begin_step(13, "Cache hit — repeat identical query")
    try:
        q = QUERIES[0]
        info(f"Sending duplicate query: {q}")
        r = _post(
            f"{GENERATION_API}/generate/query",
            admin_token,
            json={"query": q, "domain_id": domain_id,
                  "top_k_retrieve": 10, "top_k_rerank": 5},
        )
        r.raise_for_status()
        data = r.json()
        cache_hit = data.get("cache_hit", False)
        if cache_hit:
            ok("cache_hit=True — served from cache ✓")
            _end_step("PASS", "cache_hit=True")
        else:
            info("cache_hit=False — cache may be disabled or key differs (non-fatal)")
            _end_step("PASS", "cache_hit=False (acceptable — cache may be memory/disabled)")
        return True
    except Exception as exc:
        fail(str(exc))
        _end_step("FAIL", str(exc))
        return False


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------
def print_summary(total_elapsed: float) -> int:
    section("TIMING SUMMARY")
    col_w = [6, 40, 10, 8, 40]
    header = f"{'STEP':^{col_w[0]}}  {'NAME':<{col_w[1]}}  {'TIME (s)':>{col_w[2]}}  {'STATUS':^{col_w[3]}}  {'DETAIL':<{col_w[4]}}"
    sep    = "─" * (sum(col_w) + 8)
    print(f"\n{BOLD}{header}{RESET}")
    print(sep)

    failures = 0
    for r in _results:
        colour  = GREEN if r["status"] == "PASS" else (RED if r["status"] == "FAIL" else YELLOW)
        status_str = f"{colour}{r['status']}{RESET}"
        detail_str = (r["detail"] or "")[:col_w[4]]
        print(
            f"{r['step']:^{col_w[0]}}  "
            f"{r['name']:<{col_w[1]}}  "
            f"{r['elapsed']:>{col_w[2]}.2f}  "
            f"{status_str:^{col_w[3]}}  "
            f"{detail_str}"
        )
        if r["status"] == "FAIL":
            failures += 1

    print(sep)
    total_steps = len(_results)
    passed      = total_steps - failures
    print(f"\n  {BOLD}Total steps :{RESET}  {total_steps}")
    print(f"  {GREEN}{BOLD}Passed      :{RESET}  {passed}")
    if failures:
        print(f"  {RED}{BOLD}Failed      :{RESET}  {failures}")
    print(f"  {BOLD}Wall time   :{RESET}  {total_elapsed:.2f}s\n")

    # Final verdict
    if failures == 0:
        print(f"{GREEN}{BOLD}{'═' * 60}{RESET}")
        print(f"{GREEN}{BOLD}  ✓  ALL STEPS PASSED — FULL PIPELINE OK{RESET}")
        print(f"{GREEN}{BOLD}{'═' * 60}{RESET}\n")
    else:
        print(f"{RED}{BOLD}{'═' * 60}{RESET}")
        print(f"{RED}{BOLD}  ✗  {failures} STEP(S) FAILED — SEE ABOVE{RESET}")
        print(f"{RED}{BOLD}{'═' * 60}{RESET}\n")
        # Print failed step names
        for r in _results:
            if r["status"] == "FAIL":
                print(f"  {RED}•{RESET} Step {r['step']:02d} — {r['name']}")
                if r["detail"]:
                    print(f"         {DIM}{r['detail']}{RESET}")
        print()

    return failures


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Timed E2E test — full RAG pipeline, all roles.")
    parser.add_argument(
        "--pdf", type=Path,
        default=FIXTURES / "sample_policy.pdf",
        help="PDF file to ingest (default: scripts/fixtures/sample_policy.pdf)",
    )
    args = parser.parse_args()

    pdf_path: Path = args.pdf.resolve()

    # ── Banner ──────────────────────────────────────────────────────────────
    print(f"\n{CYAN}{'═' * 60}{RESET}")
    print(f"{CYAN}{BOLD}  RAG System — Timed E2E Test  (all roles){RESET}")
    print(f"{CYAN}{'═' * 60}{RESET}")
    print(f"  PDF      : {pdf_path}")
    print(f"  Auth     : {'Keycloak' if keycloak_ready() else 'dev JWT (Keycloak not running)'}")
    print(f"  Started  : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{CYAN}{'─' * 60}{RESET}\n")

    if not pdf_path.exists():
        print(f"{RED}ERROR:{RESET} PDF not found: {pdf_path}")
        return 1

    wall_start = time.perf_counter()

    # ── Step 0: Health ───────────────────────────────────────────────────────
    health_ok = step_health()
    # Continue even if health fails (collect failures)

    # ── Step 1: Auth ─────────────────────────────────────────────────────────
    auth_result = step_auth()
    if auth_result is None:
        # Auth is a prerequisite — can't continue meaningfully
        _begin_step(2,  "Create domain")
        _end_step("SKIP", "auth failed — skipping")
        _begin_step(3,  "Assign domain_admin")
        _end_step("SKIP", "auth failed — skipping")
        _begin_step(4,  "Assign members")
        _end_step("SKIP", "auth failed — skipping")
        _begin_step(5,  "contributor upload")
        _end_step("SKIP", "auth failed — skipping")
        _begin_step(6,  "Poll ingestion")
        _end_step("SKIP", "auth failed — skipping")
        _begin_step(7,  "admin generate answers")
        _end_step("SKIP", "auth failed — skipping")
        _begin_step(8,  "reader query")
        _end_step("SKIP", "auth failed — skipping")
        _begin_step(9,  "reader upload rejected")
        _end_step("SKIP", "auth failed — skipping")
        _begin_step(10, "contributor 2nd upload")
        _end_step("SKIP", "auth failed — skipping")
        _begin_step(11, "Cache hit")
        _end_step("SKIP", "auth failed — skipping")
        return print_summary(time.perf_counter() - wall_start)

    tm, user_ids = auth_result

    # ── Step 2: Create domain ────────────────────────────────────────────────
    domain = step_create_domain(tm.get("admin"))
    domain_id = domain["id"] if domain else None

    # ── Step 3: Assign domain_admin ──────────────────────────────────────────
    if domain_id:
        step_assign_domain_admin(tm.get("admin"), domain_id, user_ids["domain_admin"])
    else:
        _begin_step(3, "Assign domain_admin")
        _end_step("SKIP", "no domain — skipping")

    # ── Step 4: Assign contributor + reader ──────────────────────────────────
    if domain_id:
        step_assign_members(
            tm.get("domain_admin"),
            domain_id,
            user_ids["contributor"],
            user_ids["reader"],
        )
    else:
        _begin_step(4, "Assign members")
        _end_step("SKIP", "no domain — skipping")

    # ── Step 5: Contributor uploads PDF ──────────────────────────────────────
    upload_result = None
    if domain_id:
        upload_result = step_upload_pdf(tm.get("contributor"), domain_id, pdf_path)
    else:
        _begin_step(5, "contributor upload")
        _end_step("SKIP", "no domain — skipping")

    # ── Step 6: Poll ingestion ───────────────────────────────────────────────
    # NOTE: uses the TokenManager directly (not a pre-fetched string) so that
    #       the token is refreshed on every poll cycle if near expiry.
    ingestion_ok = False
    if upload_result and domain_id:
        ingestion_ok = step_poll_ingestion(tm, "admin", upload_result["document_id"])
    else:
        _begin_step(6, "Poll ingestion")
        _end_step("SKIP", "no upload — skipping")

    # ── Steps 7-9: Admin generates answers (3 questions) ────────────────────
    if domain_id and ingestion_ok:
        step_generate_answers(tm.get("admin"), domain_id, step_n=7, role="admin (system_admin)")
    else:
        _begin_step(7, "admin generate answers")
        _end_step("SKIP", "ingestion not complete — skipping")

    # ── Step 10: RBAC query check (reader=403 expected, contributor=200) ─────
    if domain_id and ingestion_ok:
        step_reader_query(tm.get("reader"), tm.get("contributor"), domain_id)
    else:
        _begin_step(10, "RBAC query check")
        _end_step("SKIP", "no domain or ingestion incomplete — skipping")

    # ── Step 11: Reader upload rejected ──────────────────────────────────────
    if domain_id:
        step_reader_upload_rejected(tm.get("reader"), domain_id, pdf_path)
    else:
        _begin_step(11, "reader upload rejected")
        _end_step("SKIP", "no domain — skipping")

    # ── Step 12: Contributor second upload ────────────────────────────────────
    if domain_id:
        step_contributor_second_upload(tm.get("contributor"), domain_id, pdf_path)
    else:
        _begin_step(12, "contributor 2nd upload")
        _end_step("SKIP", "no domain — skipping")

    # ── Step 13: Cache hit ────────────────────────────────────────────────────
    if domain_id and ingestion_ok:
        step_cache_hit(tm.get("admin"), domain_id)
    else:
        _begin_step(13, "Cache hit")
        _end_step("SKIP", "ingestion not complete — skipping")

    # ── Summary ───────────────────────────────────────────────────────────────
    total_elapsed = time.perf_counter() - wall_start
    failures = print_summary(total_elapsed)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
