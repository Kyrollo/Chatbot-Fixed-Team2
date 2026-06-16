import uuid

from fastapi import APIRouter, status

import service
from dependencies import CurrentUser, DBSession, InternalService, SystemAdmin
from schemas import (
    AccessCheckRequest,
    AccessCheckResponse,
    ConfigResponse,
    ConfigUpdate,
    DomainCreate,
    DomainResponse,
    DomainUpdate,
    MemberAssign,
    MemberResponse,
    MemberUpdate,
    DocumentResponse,
    ChunkResponse,
    UserCreate,
    UserResponse,
)

# ---------- Domain & member router ----------
router = APIRouter(prefix="/domains", tags=["domains"])

from fastapi import HTTPException
from sqlalchemy import select
from models import User
import dev_auth
from schemas import LoginRequest, LoginResponse

@router.get("/health", tags=["health"])
async def router_health_check():
    return {"status": "ok", "service": "domain-service"}


@router.post("/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest, db: DBSession):
    user_id = payload.user_id.strip()
    
    # 1. Fetch user by user_id from database
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unauthorized: User ID '{user_id}' not found in the system database.",
        )
    
    # 2. Map role to JWT realm roles
    roles = []
    if user.role == "system_admin":
        roles = ["system_admin", "domain_admin", "contributor", "reader"]
    elif user.role == "domain_admin":
        roles = ["domain_admin", "contributor", "reader"]
    elif user.role == "contributor":
        roles = ["contributor", "reader"]
    elif user.role == "reader":
        roles = ["reader"]
    else:
        roles = []
        
    # 3. Mint JWT access token using the dev auth helper
    token = dev_auth.mint_token(
        user_id=user.id,
        username=user.name.lower().replace(" ", "_"),
        roles=roles,
        email=f"{user.id}@rag.local",
    )
    
    return LoginResponse(
        token=token,
        user_id=user.id,
        username=user.name,
        role=user.role,
        roles=roles
    )


@router.post("", response_model=DomainResponse, status_code=status.HTTP_201_CREATED)
async def create_domain(payload: DomainCreate, db: DBSession, admin: SystemAdmin):
    return await service.create_domain(db, payload, admin)


@router.get("", response_model=list[DomainResponse])
async def list_domains(db: DBSession, user: CurrentUser):
    return await service.list_domains(db, user)


@router.get("/{domain_id}", response_model=DomainResponse)
async def get_domain(domain_id: uuid.UUID, db: DBSession, user: CurrentUser):
    return await service.get_domain(db, domain_id, user)


@router.patch("/{domain_id}", response_model=DomainResponse)
async def update_domain(
    domain_id: uuid.UUID, payload: DomainUpdate, db: DBSession, user: CurrentUser
):
    return await service.update_domain(db, domain_id, payload, user)


@router.delete("/{domain_id}", response_model=DomainResponse)
async def archive_domain(domain_id: uuid.UUID, db: DBSession, user: CurrentUser):
    return await service.archive_domain(db, domain_id, user)


# ---------- Members ----------
@router.post(
    "/{domain_id}/members",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def assign_member(
    domain_id: uuid.UUID, payload: MemberAssign, db: DBSession, user: CurrentUser
):
    return await service.assign_member(db, domain_id, payload, user)


@router.get("/{domain_id}/members", response_model=list[MemberResponse])
async def list_members(domain_id: uuid.UUID, db: DBSession, user: CurrentUser):
    return await service.list_members(db, domain_id, user)


@router.patch("/{domain_id}/members/{user_id}", response_model=MemberResponse)
async def update_member_role(
    domain_id: uuid.UUID,
    user_id: str,
    payload: MemberUpdate,
    db: DBSession,
    user: CurrentUser,
):
    return await service.update_member_role(db, domain_id, user_id, payload, user)


@router.delete(
    "/{domain_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_member(
    domain_id: uuid.UUID, user_id: str, db: DBSession, user: CurrentUser
):
    await service.remove_member(db, domain_id, user_id, user)
    return None


# ---------- Config ----------
@router.get("/{domain_id}/config", response_model=ConfigResponse)
async def get_config(domain_id: uuid.UUID, db: DBSession, user: CurrentUser):
    return await service.get_config(db, domain_id, user)


@router.patch("/{domain_id}/config", response_model=ConfigResponse)
async def update_config(
    domain_id: uuid.UUID, payload: ConfigUpdate, db: DBSession, user: CurrentUser
):
    return await service.update_config(db, domain_id, payload, user)


# ---------- Documents in a Domain ----------
@router.get("/{domain_id}/documents", response_model=list[DocumentResponse])
async def list_documents(domain_id: uuid.UUID, db: DBSession, user: CurrentUser):
    """Lists all documents uploaded to this domain with chunk counts."""
    return await service.list_documents(db, domain_id, user)


@router.delete(
    "/{domain_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_document(
    domain_id: uuid.UUID, document_id: str, db: DBSession, user: CurrentUser
):
    """Deletes a document and all its chunks from Qdrant + PostgreSQL + disk."""
    await service.delete_document(db, domain_id, document_id, user)
    return None


@router.get("/{domain_id}/documents/{document_id}/chunks", response_model=list[ChunkResponse])
async def list_document_chunks(
    domain_id: uuid.UUID, document_id: str, db: DBSession, user: CurrentUser
):
    """Lists all chunks for a specific document (for the multi-view inspector)."""
    return await service.list_document_chunks(db, domain_id, document_id, user)


# ---------- Admin: User Management ----------
@router.get("/admin/users", response_model=list[UserResponse])
async def list_users(db: DBSession, admin: SystemAdmin):
    """Lists all users from the users table in PostgreSQL."""
    return await service.list_users(db)


@router.post("/admin/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(payload: UserCreate, db: DBSession, admin: SystemAdmin):
    """Creates a new user in the users table."""
    return await service.create_user(db, payload.id, payload.name, payload.role)


@router.delete("/admin/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: str, db: DBSession, admin: SystemAdmin):
    """Deletes a user and cascades to domain_roles."""
    await service.delete_user(db, user_id)
    return None


# ---------- Internal RBAC router ----------
internal_router = APIRouter(prefix="/internal", tags=["internal"])


@internal_router.post("/check-access", response_model=AccessCheckResponse)
async def check_access(
    payload: AccessCheckRequest, db: DBSession, _: InternalService
):
    result = await service.check_access(
        db,
        user_id=payload.user_id,
        domain_id=payload.domain_id,
        required_role=payload.required_role,
    )
    return AccessCheckResponse(**result)

