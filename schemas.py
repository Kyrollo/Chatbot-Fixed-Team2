import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from models import DomainStatus, RoleEnum


# ---------- Domain Schemas ----------
class DomainCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class DomainUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class DomainResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    status: DomainStatus
    created_by: str
    created_at: datetime
    updated_at: datetime


# ---------- Member Schemas ----------
class MemberAssign(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=255)
    role: RoleEnum


class MemberUpdate(BaseModel):
    role: RoleEnum


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    domain_id: uuid.UUID
    user_id: str
    role: RoleEnum
    assigned_by: str
    assigned_at: datetime


# ---------- Config Schemas ----------
class ConfigUpdate(BaseModel):
    llm_route: str | None = Field(default=None, max_length=255)
    chunk_size: int | None = Field(default=None, gt=0, le=8192)
    chunk_overlap: int | None = Field(default=None, ge=0, le=4096)
    confidence_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    extra_settings: dict[str, Any] | None = None


class ConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    domain_id: uuid.UUID
    llm_route: str
    chunk_size: int
    chunk_overlap: int
    confidence_threshold: float
    extra_settings: dict[str, Any]
    updated_at: datetime


# ---------- Internal RBAC Schemas ----------
class AccessCheckRequest(BaseModel):
    user_id: str
    domain_id: uuid.UUID
    required_role: RoleEnum


class AccessCheckResponse(BaseModel):
    allowed: bool
    role: RoleEnum | None = None
    reason: str | None = None
