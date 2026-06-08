import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from config import settings
from models import Domain, DomainConfig, DomainRole, DomainStatus, RoleEnum
from schemas import (
    ConfigUpdate,
    DomainCreate,
    DomainUpdate,
    MemberAssign,
    MemberUpdate,
)

# Role hierarchy: higher number => more permissions
ROLE_LEVEL = {
    RoleEnum.reader: 1,
    RoleEnum.contributor: 2,
    RoleEnum.domain_admin: 3,
}


# ---------- Internal lookups ----------
async def _get_domain_or_404(db: AsyncSession, domain_id: uuid.UUID) -> Domain:
    domain = await db.get(Domain, domain_id)
    if not domain:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Domain not found"
        )
    return domain


async def _get_user_role(
    db: AsyncSession, domain_id: uuid.UUID, user_id: str
) -> RoleEnum | None:
    result = await db.execute(
        select(DomainRole.role).where(
            DomainRole.domain_id == domain_id,
            DomainRole.user_id == user_id,
        )
    )
    role = result.scalar_one_or_none()
    return role


async def _ensure_min_role(
    db: AsyncSession, user: dict, domain_id: uuid.UUID, required: RoleEnum
) -> None:
    """Raise 403 unless the user is a system admin or holds >= required role."""
    if user.get("is_system_admin"):
        return
    role = await _get_user_role(db, domain_id, user["user_id"])
    if role is None or ROLE_LEVEL[role] < ROLE_LEVEL[required]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Requires at least '{required.value}' role on this domain",
        )


# ============================================================
# Domain CRUD
# ============================================================
async def create_domain(
    db: AsyncSession, payload: DomainCreate, user: dict
) -> Domain:
    existing = await db.execute(select(Domain).where(Domain.name == payload.name))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Domain '{payload.name}' already exists",
        )

    domain = Domain(
        name=payload.name,
        description=payload.description,
        status=DomainStatus.active,
        created_by=user["user_id"],
    )
    db.add(domain)
    await db.flush()  # populate domain.id

    # Default config
    config = DomainConfig(
        domain_id=domain.id,
        llm_route=settings.DEFAULT_LLM_ROUTE,
        chunk_size=settings.DEFAULT_CHUNK_SIZE,
        chunk_overlap=settings.DEFAULT_CHUNK_OVERLAP,
        confidence_threshold=settings.DEFAULT_CONFIDENCE_THRESHOLD,
        extra_settings={},
    )
    db.add(config)

    # Creator becomes domain_admin
    creator_role = DomainRole(
        domain_id=domain.id,
        user_id=user["user_id"],
        role=RoleEnum.domain_admin,
        assigned_by=user["user_id"],
    )
    db.add(creator_role)

    await db.flush()
    await db.refresh(domain)
    return domain


async def list_domains(db: AsyncSession, user: dict) -> list[Domain]:
    if user.get("is_system_admin"):
        result = await db.execute(select(Domain).order_by(Domain.created_at.desc()))
        return list(result.scalars().all())

    # Only assigned domains
    result = await db.execute(
        select(Domain)
        .join(DomainRole, DomainRole.domain_id == Domain.id)
        .where(DomainRole.user_id == user["user_id"])
        .order_by(Domain.created_at.desc())
    )
    return list(result.scalars().unique().all())


async def get_domain(
    db: AsyncSession, domain_id: uuid.UUID, user: dict
) -> Domain:
    domain = await _get_domain_or_404(db, domain_id)
    # Any assigned member (reader+) or system admin may view
    await _ensure_min_role(db, user, domain_id, RoleEnum.reader)
    return domain


async def update_domain(
    db: AsyncSession, domain_id: uuid.UUID, payload: DomainUpdate, user: dict
) -> Domain:
    domain = await _get_domain_or_404(db, domain_id)
    await _ensure_min_role(db, user, domain_id, RoleEnum.domain_admin)

    if payload.name is not None and payload.name != domain.name:
        dup = await db.execute(select(Domain).where(Domain.name == payload.name))
        if dup.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Domain '{payload.name}' already exists",
            )
        domain.name = payload.name

    if payload.description is not None:
        domain.description = payload.description

    await db.flush()
    await db.refresh(domain)
    return domain


async def archive_domain(
    db: AsyncSession, domain_id: uuid.UUID, user: dict
) -> Domain:
    domain = await _get_domain_or_404(db, domain_id)
    await _ensure_min_role(db, user, domain_id, RoleEnum.domain_admin)
    domain.status = DomainStatus.archived
    await db.flush()
    await db.refresh(domain)
    return domain


# ============================================================
# Member Management
# ============================================================
async def assign_member(
    db: AsyncSession, domain_id: uuid.UUID, payload: MemberAssign, user: dict
) -> DomainRole:
    await _get_domain_or_404(db, domain_id)
    await _ensure_min_role(db, user, domain_id, RoleEnum.domain_admin)

    existing = await db.execute(
        select(DomainRole).where(
            DomainRole.domain_id == domain_id,
            DomainRole.user_id == payload.user_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User is already a member of this domain",
        )

    member = DomainRole(
        domain_id=domain_id,
        user_id=payload.user_id,
        role=payload.role,
        assigned_by=user["user_id"],
    )
    db.add(member)
    await db.flush()
    await db.refresh(member)
    return member


async def list_members(
    db: AsyncSession, domain_id: uuid.UUID, user: dict
) -> list[DomainRole]:
    await _get_domain_or_404(db, domain_id)
    await _ensure_min_role(db, user, domain_id, RoleEnum.contributor)
    result = await db.execute(
        select(DomainRole)
        .where(DomainRole.domain_id == domain_id)
        .order_by(DomainRole.assigned_at.asc())
    )
    return list(result.scalars().all())


async def update_member_role(
    db: AsyncSession,
    domain_id: uuid.UUID,
    target_user_id: str,
    payload: MemberUpdate,
    user: dict,
) -> DomainRole:
    await _get_domain_or_404(db, domain_id)
    await _ensure_min_role(db, user, domain_id, RoleEnum.domain_admin)

    result = await db.execute(
        select(DomainRole).where(
            DomainRole.domain_id == domain_id,
            DomainRole.user_id == target_user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this domain",
        )

    member.role = payload.role
    await db.flush()
    await db.refresh(member)
    return member


async def remove_member(
    db: AsyncSession, domain_id: uuid.UUID, target_user_id: str, user: dict
) -> None:
    await _get_domain_or_404(db, domain_id)
    await _ensure_min_role(db, user, domain_id, RoleEnum.domain_admin)

    result = await db.execute(
        select(DomainRole).where(
            DomainRole.domain_id == domain_id,
            DomainRole.user_id == target_user_id,
        )
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found in this domain",
        )
    await db.delete(member)
    await db.flush()


# ============================================================
# Domain Configuration
# ============================================================
async def _get_config_or_404(
    db: AsyncSession, domain_id: uuid.UUID
) -> DomainConfig:
    result = await db.execute(
        select(DomainConfig).where(DomainConfig.domain_id == domain_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Domain config not found",
        )
    return config


async def get_config(
    db: AsyncSession, domain_id: uuid.UUID, user: dict
) -> DomainConfig:
    await _get_domain_or_404(db, domain_id)
    await _ensure_min_role(db, user, domain_id, RoleEnum.contributor)
    return await _get_config_or_404(db, domain_id)


async def update_config(
    db: AsyncSession, domain_id: uuid.UUID, payload: ConfigUpdate, user: dict
) -> DomainConfig:
    await _get_domain_or_404(db, domain_id)
    await _ensure_min_role(db, user, domain_id, RoleEnum.domain_admin)
    config = await _get_config_or_404(db, domain_id)

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(config, field, value)

    await db.flush()
    await db.refresh(config)
    return config


# ============================================================
# Internal RBAC
# ============================================================
async def check_access(
    db: AsyncSession,
    user_id: str,
    domain_id: uuid.UUID,
    required_role: RoleEnum,
    is_system_admin: bool = False,
) -> dict:
    if is_system_admin:
        return {"allowed": True, "role": None, "reason": "system_admin"}

    domain = await db.get(Domain, domain_id)
    if not domain:
        return {"allowed": False, "role": None, "reason": "domain_not_found"}
    if domain.status == DomainStatus.archived:
        return {"allowed": False, "role": None, "reason": "domain_archived"}

    role = await _get_user_role(db, domain_id, user_id)
    if role is None:
        return {"allowed": False, "role": None, "reason": "not_a_member"}

    allowed = ROLE_LEVEL[role] >= ROLE_LEVEL[required_role]
    return {
        "allowed": allowed,
        "role": role,
        "reason": None if allowed else "insufficient_role",
    }
