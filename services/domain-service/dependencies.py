import json
from functools import lru_cache
from typing import Annotated, AsyncGenerator
from urllib.error import URLError
from urllib.request import urlopen

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import AsyncSessionLocal

bearer_scheme = HTTPBearer(auto_error=True)


# ---------- Database ----------
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DBSession = Annotated[AsyncSession, Depends(get_db)]


# ---------- Auth helpers ----------
def _build_public_key(raw: str) -> str:
    """Accept either a bare base64 body or a full PEM and normalize to PEM."""
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
            detail=(
                "Unable to fetch Keycloak realm metadata. Set KEYCLOAK_PUBLIC_KEY "
                f"or make KEYCLOAK_REALM_URL reachable. Details: {exc}"
            ),
        ) from exc

    public_key = realm_data.get("public_key")
    if not public_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Keycloak realm metadata did not include a public_key value",
        )

    return _build_public_key(public_key)


def _decode_token(token: str) -> dict:
    public_key = _get_public_key()
    errors: list[str] = []

    for issuer in _issuer_candidates():
        try:
            payload = jwt.decode(
                token,
                public_key,
                algorithms=[settings.KEYCLOAK_ALGORITHM],
                audience=settings.KEYCLOAK_CLIENT_ID,
                issuer=issuer,
                options={"verify_aud": False},  # Keycloak puts client in azp; relax aud
            )
            return payload
        except JWTError as exc:
            errors.append(f"{issuer}: {exc}")

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=f"Invalid token: {' | '.join(errors)}",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _extract_roles(payload: dict) -> list[str]:
    roles: list[str] = []
    realm_access = payload.get("realm_access") or {}
    roles.extend(realm_access.get("roles", []))
    resource_access = payload.get("resource_access") or {}
    client = resource_access.get(settings.KEYCLOAK_CLIENT_ID) or {}
    roles.extend(client.get("roles", []))
    return roles


# ---------- Current user ----------
async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
) -> dict:
    payload = _decode_token(credentials.credentials)
    roles = _extract_roles(payload)

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing subject (sub) claim",
        )

    return {
        "user_id": user_id,
        "username": payload.get("preferred_username"),
        "email": payload.get("email"),
        "is_system_admin": settings.SYSTEM_ADMIN_ROLE in roles,
        "roles": roles,
    }


CurrentUser = Annotated[dict, Depends(get_current_user)]


# ---------- Authorization helpers ----------
async def require_system_admin(user: CurrentUser) -> dict:
    if not user.get("is_system_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System administrator privileges required",
        )
    return user


SystemAdmin = Annotated[dict, Depends(require_system_admin)]


# ---------- Internal service auth ----------
async def require_internal_service(
    x_internal_key: Annotated[str | None, Header(alias="X-Internal-Key")] = None,
) -> bool:
    if not x_internal_key or x_internal_key != settings.INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Valid internal service key required",
        )
    return True


InternalService = Annotated[bool, Depends(require_internal_service)]
