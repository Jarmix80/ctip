"""Modele ORM dla modułu administratora."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class AdminUser(Base):
    """Użytkownik panelu administracyjnego."""

    __tablename__ = "admin_user"
    __table_args__ = (
        CheckConstraint("role in ('admin','operator')", name="admin_user_role_check"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    internal_ext: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="admin")
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    mobile_phone: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )

    sessions: Mapped[list[AdminSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    audit_entries: Mapped[list[AdminAuditLog]] = relationship(
        back_populates="user", cascade="save-update"
    )


class AdminSession(Base):
    """Sesja uwierzytelniająca administratora."""

    __tablename__ = "admin_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("ctip.admin_user.id", ondelete="CASCADE"))
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[AdminUser] = relationship(back_populates="sessions")


class AdminSetting(Base):
    """Pojedyncze ustawienie konfiguracyjne panelu."""

    __tablename__ = "admin_setting"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )

    updated_by_user: Mapped[AdminUser | None] = relationship()


class AdminAuditLog(Base):
    """Dziennik operacji administratora."""

    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped[AdminUser | None] = relationship(back_populates="audit_entries")


__all__ = ["AdminUser", "AdminSession", "AdminSetting", "AdminAuditLog"]
