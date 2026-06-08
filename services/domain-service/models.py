import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Float,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class DomainStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class RoleEnum(str, enum.Enum):
    domain_admin = "domain_admin"
    contributor = "contributor"
    reader = "reader"


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[DomainStatus] = mapped_column(
        SAEnum(DomainStatus, name="domain_status"),
        default=DomainStatus.active,
        nullable=False,
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    roles: Mapped[list["DomainRole"]] = relationship(
        back_populates="domain", cascade="all, delete-orphan"
    )
    config: Mapped["DomainConfig"] = relationship(
        back_populates="domain",
        cascade="all, delete-orphan",
        uselist=False,
    )


class DomainRole(Base):
    __tablename__ = "domain_roles"
    __table_args__ = (
        UniqueConstraint("domain_id", "user_id", name="uq_domain_user"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domains.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[RoleEnum] = mapped_column(
        SAEnum(RoleEnum, name="domain_role_enum"), nullable=False
    )
    assigned_by: Mapped[str] = mapped_column(String(255), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    domain: Mapped["Domain"] = relationship(back_populates="roles")


class DomainConfig(Base):
    __tablename__ = "domain_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=_uuid
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("domains.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    llm_route: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False, default=512)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False, default=64)
    confidence_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.5
    )
    extra_settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    domain: Mapped["Domain"] = relationship(back_populates="config")
