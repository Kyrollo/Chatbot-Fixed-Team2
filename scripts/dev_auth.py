#!/usr/bin/env python3
"""
Local development JWT helper — replaces Keycloak when running without Docker.

Keys are stored under data/dev/ (gitignored). Services validate tokens using
KEYCLOAK_PUBLIC_KEY from the environment.
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

DEV_ISSUER = os.getenv("DEV_AUTH_ISSUER", "http://localhost/dev-realm")
DEV_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "domain-service")


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
    """Base64 body without PEM headers — matches KEYCLOAK_PUBLIC_KEY format."""
    pem = get_public_key_pem().strip()
    lines = [line for line in pem.splitlines() if not line.startswith("-----")]
    return "".join(lines)


def mint_token(
    *,
    user_id: str,
    username: str,
    roles: list[str],
    email: str | None = None,
    expires_minutes: int = 1440,  # 24 hours — was 60
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "preferred_username": username,
        "email": email or f"{username}@rag.local",
        "iss": DEV_ISSUER,
        "aud": DEV_CLIENT_ID,
        "azp": DEV_CLIENT_ID,
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "realm_access": {"roles": roles},
        "resource_access": {DEV_CLIENT_ID: {"roles": roles}},
    }
    return jwt.encode(payload, get_private_key_pem(), algorithm="RS256")


# Seeded users from realm-export.json
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
    spec = DEV_USERS[user_key]
    return mint_token(
        user_id=spec["user_id"],
        username=spec["username"],
        roles=spec["roles"],
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate a dev JWT token")
    parser.add_argument(
        "--role",
        default="admin",
        choices=list(DEV_USERS.keys()),
        help="User to generate token for (default: admin)",
    )
    parser.add_argument(
        "--expire",
        type=int,
        default=1440,
        help="Token lifetime in minutes (default: 1440 = 24 hours)",
    )
    args = parser.parse_args()

    print("Dev auth public key body (set as KEYCLOAK_PUBLIC_KEY):")
    print(get_public_key_body())
    print()

    spec = DEV_USERS[args.role]
    token = mint_token(
        user_id=spec["user_id"],
        username=spec["username"],
        roles=spec["roles"],
        expires_minutes=args.expire,
    )
    hours = args.expire // 60
    mins = args.expire % 60
    print(f"Sample {args.role} token (valid for {hours}h {mins}m):")
    print(token)