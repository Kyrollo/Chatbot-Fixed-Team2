"""
Gateway Smoke Test
------------------
Run this before integrating with other services.
Confirms Traefik is up, routes correctly, and Keycloak auth is working.

Usage:
    docker compose up -d
    pip install requests pyyaml
    python smoke_test.py
    docker compose down
"""

import sys
import time
import requests
import yaml

TRAEFIK_BASE   = "http://localhost:80"
DASHBOARD_URL  = "http://localhost:8080/api/rawdata"
KEYCLOAK_URL   = "http://localhost:8180/realms/rag-system"
ROUTES_CONFIG  = "traefik/dynamic/routes.yml"

EXPECTED_ROUTES = [
    {"name": "domain-service", "path": "/domains"},
]

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):   print(f"  {GREEN}✓{RESET}  {msg}")
def fail(msg): print(f"  {RED}✗{RESET}  {msg}")
def info(msg): print(f"  {YELLOW}→{RESET}  {msg}")


# ------------------------------------------------------------------
# 1. Wait for Traefik to be ready
# ------------------------------------------------------------------
def wait_for_traefik(retries=10, delay=2):
    print("\n[1] Waiting for Traefik to be ready...")
    for i in range(retries):
        try:
            r = requests.get(DASHBOARD_URL, timeout=3)
            if r.status_code == 200:
                ok("Traefik is up")
                return True
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
            pass
        info(f"Not ready yet — retry {i+1}/{retries}")
        time.sleep(delay)
    fail("Traefik did not start in time")
    return False


# ------------------------------------------------------------------
# 2. Validate routes.yml
# ------------------------------------------------------------------
def validate_routes_config():
    print("\n[2] Validating routes.yml config...")
    try:
        with open(ROUTES_CONFIG) as f:
            config = yaml.safe_load(f)

        routers  = config.get("http", {}).get("routers", {})
        services = config.get("http", {}).get("services", {})
        all_ok = True

        for route in EXPECTED_ROUTES:
            name = route["name"]
            if name in routers:
                ok(f"Router defined: {name}")
            else:
                fail(f"Router MISSING: {name}")
                all_ok = False

            if name in services:
                ok(f"Service defined: {name}")
            else:
                fail(f"Service MISSING: {name}")
                all_ok = False

        return all_ok

    except FileNotFoundError:
        fail(f"routes.yml not found at {ROUTES_CONFIG}")
        return False
    except yaml.YAMLError as e:
        fail(f"routes.yml is invalid YAML: {e}")
        return False


# ------------------------------------------------------------------
# 3. Check Keycloak is reachable
# ------------------------------------------------------------------
def check_keycloak():
    print("\n[3] Checking Keycloak is reachable...")
    try:
        r = requests.get(KEYCLOAK_URL, timeout=5)
        if r.status_code == 200:
            ok("Keycloak realm rag-system is up")
            return True
        else:
            fail(f"Keycloak returned {r.status_code} (expected 200)")
            return False
    except requests.exceptions.ConnectionError:
        fail("Keycloak not reachable at http://localhost:8180 — is it running?")
        return False
    except requests.exceptions.ReadTimeout:
        fail("Keycloak timed out — still starting up, wait 30s and retry")
        return False


# ------------------------------------------------------------------
# 4. Hit each route — expect 401 (auth is on) not 200 or timeout
# ------------------------------------------------------------------
def check_routes():
    print("\n[4] Checking route responses...")
    info("Routes are protected — expecting 401 Unauthorized (no token sent)")
    all_passed = True

    for route in EXPECTED_ROUTES:
        path = route["path"]
        url  = f"{TRAEFIK_BASE}{path}"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 401:
                ok(f"GET {path} → 401 (auth is working)")
            elif r.status_code == 200:
                fail(f"GET {path} → 200 (auth middleware not applied!)")
                all_passed = False
            else:
                fail(f"GET {path} → {r.status_code} (unexpected)")
                all_passed = False
        except requests.exceptions.ConnectionError:
            fail(f"GET {path} → connection refused")
            all_passed = False
        except requests.exceptions.ReadTimeout:
            fail(f"GET {path} → timed out (Keycloak may not be reachable from Traefik)")
            all_passed = False

    return all_passed


# ------------------------------------------------------------------
# 5. Unknown route should return 404
# ------------------------------------------------------------------
def check_unknown_route():
    print("\n[5] Checking unknown route returns 404...")
    try:
        r = requests.get(f"{TRAEFIK_BASE}/this-route-does-not-exist", timeout=5)
        if r.status_code == 404:
            ok("Unknown route → 404 (correct)")
            return True
        else:
            fail(f"Unknown route → {r.status_code} (expected 404)")
            return False
    except requests.exceptions.ConnectionError:
        fail("Could not connect to Traefik")
        return False
    except requests.exceptions.ReadTimeout:
        fail("Unknown route timed out")
        return False


# ------------------------------------------------------------------
# 6. Dashboard is accessible
# ------------------------------------------------------------------
def check_dashboard():
    print("\n[6] Checking Traefik dashboard...")
    try:
        r = requests.get(DASHBOARD_URL, timeout=5)
        if r.status_code == 200:
            ok("Dashboard accessible at http://localhost:8080/dashboard/")
            return True
        else:
            fail(f"Dashboard returned {r.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        fail("Dashboard not reachable")
        return False
    


# ------------------------------------------------------------------
# Run all checks
# ------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 50)
    print("  Gateway Smoke Test")
    print("=" * 50)

    results = []

    if not wait_for_traefik():
        print(f"\n{RED}Traefik is not running. Start it with: docker compose up -d{RESET}")
        sys.exit(1)

    results.append(validate_routes_config())
    results.append(check_keycloak())
    results.append(check_routes())
    results.append(check_unknown_route())
    results.append(check_dashboard())

    print("\n" + "=" * 50)
    if all(results):
        print(f"{GREEN}All checks passed. Gateway + Auth are ready.{RESET}")
        sys.exit(0)
    else:
        print(f"{RED}Some checks failed. Fix before integrating.{RESET}")
        sys.exit(1)
