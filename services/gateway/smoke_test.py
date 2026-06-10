"""
API Services Smoke Test
-----------------------
Confirms all FastAPI services are up and JWT auth is enforced.

Usage:
    python run_services.py          # in another terminal
    pip install requests
    python services/gateway/smoke_test.py
"""

import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from dev_auth import token_for  # noqa: E402

SERVICES = [
    ("domain-service", "http://localhost:8001/health", None),
    ("ingestion-service", "http://localhost:8002/health", None),
    ("retrieval-service", "http://localhost:8003/health", None),
    ("generation-service", "http://localhost:8004/generate/health", None),
]

PROTECTED = [
    ("domain-service", "http://localhost:8001/domains"),
    ("ingestion-service", "http://localhost:8002/ingest/test-id"),
    ("generation-service", "http://localhost:8004/generate/query"),
]

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}[OK]{RESET}  {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}[FAIL]{RESET}  {msg}")


def info(msg: str) -> None:
    print(f"  {YELLOW}>{RESET}  {msg}")


def wait_for_services(retries: int = 30, delay: int = 2) -> bool:
    print("\n[1] Waiting for services to be healthy...")
    for attempt in range(retries):
        pending = []
        for name, url, _ in SERVICES:
            try:
                r = requests.get(url, timeout=3)
                if r.status_code == 200:
                    ok(f"{name} is up")
                else:
                    pending.append(name)
            except requests.RequestException:
                pending.append(name)
        if not pending:
            return True
        info(f"Waiting for {', '.join(pending)} — retry {attempt + 1}/{retries}")
        time.sleep(delay)
    fail("Services did not become healthy in time")
    return False


def check_auth_required() -> bool:
    print("\n[2] Checking protected routes require JWT...")
    all_ok = True
    for name, url in PROTECTED:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code in (401, 403, 405, 422):
                ok(f"{name} rejects unauthenticated access ({r.status_code})")
            elif r.status_code == 200:
                fail(f"{name} returned 200 without token")
                all_ok = False
            else:
                ok(f"{name} returned {r.status_code} without token")
        except requests.RequestException as exc:
            fail(f"{name} unreachable: {exc}")
            all_ok = False
    return all_ok


def check_authenticated_access() -> bool:
    print("\n[3] Checking authenticated domain list...")
    token = token_for("admin")
    r = requests.get(
        "http://localhost:8001/domains",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if r.status_code == 200:
        ok("Admin token accepted by domain-service")
        return True
    fail(f"Authenticated request failed: {r.status_code} {r.text[:200]}")
    return False


if __name__ == "__main__":
    print("=" * 50)
    print("  API Services Smoke Test")
    print("=" * 50)

    results = []
    if not wait_for_services():
        print(f"\n{RED}Services are not running. Start with: python run_services.py{RESET}")
        sys.exit(1)

    results.append(check_auth_required())
    results.append(check_authenticated_access())

    print("\n" + "=" * 50)
    if all(results):
        print(f"{GREEN}All checks passed. API services are ready.{RESET}")
        sys.exit(0)
    print(f"{RED}Some checks failed.{RESET}")
    sys.exit(1)
