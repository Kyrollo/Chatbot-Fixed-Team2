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


# ---------- Authentication Schemas ----------
class LoginRequest(BaseModel):
    user_id: str


class LoginResponse(BaseModel):
    token: str
    user_id: str
    username: str
    role: str
    roles: list[str]


# ---------- Document Schemas ----------
class DocumentResponse(BaseModel):
    id: str
    domain_id: str
    user_id: str
    filename: str
    status: str
    error_msg: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    chunk_count: int = 0


# ---------- Chunk Schemas ----------
class ChunkResponse(BaseModel):
    """Full chunk data for the multi-view inspector."""
    id: str
    document_id: str
    domain_id: str
    page_num: int | None = None
    chunk_index: int = 0
    text: str
    chunk_type: str = "text"
    source_type: str = "pdf"
    filename: str = ""
    created_at: datetime | None = None


# ---------- User Management Schemas ----------
class UserCreate(BaseModel):
    id: str = Field(..., min_length=1, max_length=255)
    name: str = Field(..., min_length=1, max_length=255)
    role: str = Field(..., min_length=1, max_length=50)


class UserResponse(BaseModel):
    id: str
    name: str
    role: str
