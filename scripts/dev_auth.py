#!/usr/bin/env python3
"""
Local development JWT helper — fallback when Keycloak is not running.

IMPORTANT:
- Must NOT conflict with real Keycloak tokens
- Uses RS256 keys generated locally
- Compatible with standard JWT validation logic
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jose import jwt

ROOT = Path(__file__).resolve().parent.parent
KEY_DIR = ROOT / "data" / "dev"
PRIVATE_KEY_PATH = KEY_DIR / "jwt_private.pem"
PUBLIC_KEY_PATH = KEY_DIR / "jwt_public.pem"

# ─────────────────────────────────────────────
# Match Keycloak-like configuration (important)
# ─────────────────────────────────────────────
DEV_ISSUER = os.getenv(
    "DEV_AUTH_ISSUER",
    "http://localhost:8080/realms/rag-system"
)

DEV_CLIENT_ID = os.getenv(
    "KEYCLOAK_CLIENT_ID",
    "rag-api"
)


# ─────────────────────────────────────────────
# Key generation
# ─────────────────────────────────────────────
def _ensure_keys() -> None:
    if PRIVATE_KEY_PATH.exists() and PUBLIC_KEY_PATH.exists():
        return

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    KEY_DIR.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    PRIVATE_KEY_PATH.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    PUBLIC_KEY_PATH.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )


def get_private_key_pem() -> str:
    _ensure_keys()
    return PRIVATE_KEY_PATH.read_text(encoding="utf-8")


def get_public_key_pem() -> str:
    _ensure_keys()
    return PUBLIC_KEY_PATH.read_text(encoding="utf-8")


def get_public_key_body() -> str:
    """Base64 body without PEM headers (Keycloak-style format)."""
    pem = get_public_key_pem().strip()
    lines = [line for line in pem.splitlines() if not line.startswith("-----")]
    return "".join(lines)


# ─────────────────────────────────────────────
# Token generation (FIXED)
# ─────────────────────────────────────────────
def mint_token(
    *,
    user_id: str,
    username: str,
    roles: list[str],
    email: str | None = None,
    expires_minutes: int = 60,
) -> str:
    now = datetime.now(timezone.utc)

    payload = {
        "sub": user_id,
        "preferred_username": username,
        "email": email or f"{username}@rag.local",

        # IMPORTANT: match Keycloak format
        "iss": DEV_ISSUER,
        "aud": [DEV_CLIENT_ID],
        "azp": DEV_CLIENT_ID,

        # FIX: JWT expects timestamps (not datetime)
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expires_minutes)).timestamp()),

        "realm_access": {
            "roles": roles
        },

        "resource_access": {
            DEV_CLIENT_ID: {
                "roles": roles
            }
        },

        # optional but harmless (some backends expect it)
        "typ": "Bearer",
    }

    return jwt.encode(payload, get_private_key_pem(), algorithm="RS256")


# ─────────────────────────────────────────────
# Seed users
# ─────────────────────────────────────────────
DEV_USERS = {
    "admin": {
        "user_id": "652ec45e-1b68-478c-9bd3-81cc46fb24a9",
        "username": "admin",
        "roles": ["system_admin", "domain_admin", "contributor", "reader"],
    },
    "reader1": {
        "user_id": "d3794cbc-9bb9-4c06-95e5-33603c71b287",
        "username": "reader1",
        "roles": ["reader"],
    },
    "contributor_test": {
        "user_id": "a1111111-1111-1111-1111-111111111111",
        "username": "contributor_test",
        "roles": ["contributor"],
    },
}


def token_for(user_key: str) -> str:
    if user_key not in DEV_USERS:
        raise ValueError(f"Unknown user: {user_key}")

    spec = DEV_USERS[user_key]

    return mint_token(
        user_id=spec["user_id"],
        username=spec["username"],
        roles=spec["roles"],
    )


# ─────────────────────────────────────────────
# Debug run
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("Dev public key (use in KEYCLOAK_PUBLIC_KEY if needed):\n")
    print(get_public_key_body())
    print("\nSample admin token:\n")
    print(token_for("admin"))