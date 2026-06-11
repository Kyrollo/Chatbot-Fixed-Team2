import json
from functools import lru_cache
from typing import Annotated
from urllib.error import URLError
from urllib.request import urlopen

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import httpx

from config import settings

bearer_scheme = HTTPBearer(auto_error=True)


def _build_public_key(raw: str) -> str:
    raw = raw.strip()
    if "BEGIN PUBLIC KEY" in raw:
        return raw
    return f"-----BEGIN PUBLIC KEY-----\n{raw}\n-----END PUBLIC KEY-----"


def _issuer_candidates() -> list[str]:
    return [issuer.strip() for issuer in settings.KEYCLOAK_ISSUER.split(",") if issuer.strip()]


@lru_cache
def _get_public_key() -> str:
    if settings.KEYCLOAK_PUBLIC_KEY.strip():
        return _build_public_key(settings.KEYCLOAK_PUBLIC_KEY)

    try:
        with urlopen(settings.KEYCLOAK_REALM_URL, timeout=5) as response:
            realm_data = json.load(response)
    except URLError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unable to fetch Keycloak realm metadata: {exc}",
        ) from exc

    public_key = realm_data.get("public_key")
    if not public_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Keycloak realm metadata did not include a public_key value",
        )

    return _build_public_key(public_key)


def _extract_roles(payload: dict) -> list[str]:
    roles: list[str] = []
    realm_access = payload.get("realm_access") or {}
    roles.extend(realm_access.get("roles", []))
    resource_access = payload.get("resource_access") or {}
    client = resource_access.get(settings.KEYCLOAK_CLIENT_ID) or {}
    roles.extend(client.get("roles", []))
    return roles


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> dict:
    public_key = _get_public_key()
    errors: list[str] = []

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
                    detail="Token missing subject claim",
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
                detail="Token missing subject claim",
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
        detail=f"Invalid token: {' | '.join(errors)}",
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
    On connection failure → raises 503.
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

