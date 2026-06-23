"""
JWT authentication and domain RBAC dependency for the retrieval service.

Sprint 2 addition — closes the RBAC filtering gap identified in the audit.
Before this, retrieval-service accepted any domain_id without verifying
user access. Now it mirrors the auth pattern used by ingestion-service
and generation-service.

Flow:
1. Parse Authorization header → decode JWT (Keycloak or dev auth fallback)
2. Extract user_id and roles
3. For domain-scoped endpoints, verify user has at least 'reader' role
   on the target domain via domain-service /internal/check-access
4. system_admin bypasses domain checks
"""
import json
from functools import lru_cache
from typing import Annotated
from urllib.error import URLError
from urllib.request import urlopen

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from config import settings

bearer_scheme = HTTPBearer(auto_error=True)


# ---------- Public key helpers ----------

def _build_public_key(raw: str) -> str:
    raw = raw.strip()
    if "BEGIN PUBLIC KEY" in raw:
        return raw
    return f"-----BEGIN PUBLIC KEY-----\n{raw}\n-----END PUBLIC KEY-----"


def _issuer_candidates() -> list[str]:
    return [i.strip() for i in settings.KEYCLOAK_ISSUER.split(",") if i.strip()]


@lru_cache
def _get_public_key() -> str | None:
    if settings.KEYCLOAK_PUBLIC_KEY.strip():
        return _build_public_key(settings.KEYCLOAK_PUBLIC_KEY)

    try:
        with urlopen(settings.KEYCLOAK_REALM_URL, timeout=3) as resp:
            data = json.load(resp)
            pk = data.get("public_key")
            if pk:
                return _build_public_key(pk)
    except Exception:
        pass
    return None


def _extract_roles(payload: dict) -> list[str]:
    roles: list[str] = []
    roles.extend((payload.get("realm_access") or {}).get("roles", []))
    client = (payload.get("resource_access") or {}).get(settings.KEYCLOAK_CLIENT_ID) or {}
    roles.extend(client.get("roles", []))
    return roles


# ---------- Current user dependency ----------

async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> dict:
    """
    Decodes JWT from Authorization header.
    Tries Keycloak issuers first, then falls back to dev_auth for local development.
    """
    public_key = _get_public_key()
    errors: list[str] = []

    if public_key:
        for issuer in _issuer_candidates():
            try:
                payload = jwt.decode(
                    credentials.credentials,
                    public_key,
                    algorithms=[settings.KEYCLOAK_ALGORITHM],
                    audience=settings.KEYCLOAK_CLIENT_ID,
                    issuer=issuer,
                    options={"verify_aud": False},
                )
                user_id = payload.get("sub")
                if not user_id:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Token missing subject (sub) claim",
                    )
                roles = _extract_roles(payload)
                return {
                    "user_id": user_id,
                    "roles": roles,
                    "is_system_admin": settings.SYSTEM_ADMIN_ROLE in roles,
                    "token": credentials.credentials,
                }
            except JWTError as exc:
                errors.append(f"{issuer}: {exc}")
    else:
        errors.append("Keycloak realm public key is not available (Keycloak is offline).")

    # Fallback for local dev login (minted via dev_auth.py)
    try:
        import dev_auth
        dev_pub = dev_auth.get_public_key_pem()
        payload = jwt.decode(
            credentials.credentials,
            dev_pub,
            algorithms=["RS256"],
            audience=settings.KEYCLOAK_CLIENT_ID,
            issuer=dev_auth.DEV_ISSUER,
            options={"verify_aud": False},
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing subject (sub) claim",
            )
        roles = _extract_roles(payload)
        return {
            "user_id": user_id,
            "roles": roles,
            "is_system_admin": settings.SYSTEM_ADMIN_ROLE in roles,
            "token": credentials.credentials,
        }
    except Exception as exc:
        errors.append(f"dev_auth fallback: {exc}")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Invalid token — {' | '.join(errors)}",
        headers={"WWW-Authenticate": "Bearer"},
    )


CurrentUser = Annotated[dict, Depends(get_current_user)]


# ---------- Domain RBAC check via domain-service ----------

async def check_domain_access(
    user_id: str,
    domain_id: str,
    required_role: str,
    is_system_admin: bool = False,
) -> bool:
    """
    Calls domain-service /internal/check-access to verify per-domain RBAC.
    Returns True if allowed, False otherwise.
    On connection failure → raises 503 so the caller can surface it cleanly.
    """
    if is_system_admin:
        return True

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                f"{settings.DOMAIN_SERVICE_URL}/internal/check-access",
                json={
                    "user_id": user_id,
                    "domain_id": domain_id,
                    "required_role": required_role,
                },
                headers={"X-Internal-Key": settings.INTERNAL_API_KEY},
            )
            if resp.status_code == 200:
                return resp.json().get("allowed", False)
            return False
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Domain service unreachable: {exc}",
        ) from exc
