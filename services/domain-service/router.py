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
)

# ---------- Domain & member router ----------
router = APIRouter(prefix="/domains", tags=["domains"])


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
