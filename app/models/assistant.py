"""Modele ORM dla modułu CTIP AI Asystent."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class AssistantChatThread(Base):
    """Wątek rozmowy użytkownika z asystentem."""

    __tablename__ = "assistant_chat_thread"
    __table_args__ = (
        CheckConstraint(
            "status in ('active','archived','deleted')",
            name="assistant_chat_thread_status_check",
        ),
        CheckConstraint(
            "worker_key in ('ksero_partner_analyst','opiekun_klienta','diagnosta_bazy_ms')",
            name="assistant_chat_thread_worker_key_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, default="Nowa rozmowa")
    worker_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="ksero_partner_analyst",
        server_default="ksero_partner_analyst",
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )

    owner_user = relationship("AdminUser")
    messages: Mapped[list[AssistantChatMessage]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )
    tool_logs: Mapped[list[AssistantToolCallLog]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )


class AssistantChatMessage(Base):
    """Pojedyncza wiadomość w wątku asystenta."""

    __tablename__ = "assistant_chat_message"
    __table_args__ = (
        CheckConstraint(
            "role in ('user','assistant','system','tool')",
            name="assistant_chat_message_role_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.assistant_chat_thread.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    openai_response_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )

    thread: Mapped[AssistantChatThread] = relationship(back_populates="messages")
    user = relationship("AdminUser")


class AssistantToolCallLog(Base):
    """Audyt wywołań narzędzi read-only użytych przez asystenta."""

    __tablename__ = "assistant_tool_call_log"
    __table_args__ = (
        CheckConstraint(
            "tool_name in ('firebird_read','firebird_business_read','firebird_knowledge_read','sheets_read','imap_read','ctip_schema_read')",
            name="assistant_tool_call_log_tool_name_check",
        ),
        CheckConstraint(
            "status in ('success','error','blocked')",
            name="assistant_tool_call_log_status_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.assistant_chat_thread.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.assistant_chat_message.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="success")
    tool_input: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tool_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    generated_sql: Mapped[str | None] = mapped_column(Text, nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    thread: Mapped[AssistantChatThread] = relationship(back_populates="tool_logs")
    message: Mapped[AssistantChatMessage | None] = relationship()
    user = relationship("AdminUser")


class AssistantChangeRequest(Base):
    """Wniosek o zmianę wygenerowany z rozmowy z asystentem."""

    __tablename__ = "assistant_change_request"
    __table_args__ = (
        CheckConstraint(
            "status in ('pending','approved','rejected','executed')",
            name="assistant_change_request_status_check",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    thread_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.assistant_chat_thread.id", ondelete="SET NULL"), nullable=True
    )
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.assistant_chat_message.id", ondelete="SET NULL"), nullable=True
    )
    request_text: Mapped[str] = mapped_column(Text, nullable=False)
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    decided_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )

    created_by_user = relationship("AdminUser", foreign_keys=[created_by])
    decided_by_user = relationship("AdminUser", foreign_keys=[decided_by])
    thread: Mapped[AssistantChatThread | None] = relationship()
    message: Mapped[AssistantChatMessage | None] = relationship()


class AssistantUserProfile(Base):
    """Profil personalizacji asystenta dla użytkownika."""

    __tablename__ = "assistant_user_profile"
    __table_args__ = (UniqueConstraint("user_id", name="uq_assistant_user_profile_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="CASCADE"), nullable=False
    )
    personalization_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    preferences: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    memory_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )

    user = relationship("AdminUser")


class AssistantWeeklyInsight(Base):
    """Tygodniowe sugestie usprawnień generowane na podstawie logów asystenta."""

    __tablename__ = "assistant_weekly_insight"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.timezone("utc", func.now())
    )
    generated_by: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.admin_user.id", ondelete="SET NULL"), nullable=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    generated_by_user = relationship("AdminUser")


Index(
    "idx_assistant_chat_thread_owner_last_activity",
    AssistantChatThread.owner_user_id,
    AssistantChatThread.last_activity_at.desc(),
)
Index(
    "idx_assistant_chat_message_thread_created",
    AssistantChatMessage.thread_id,
    AssistantChatMessage.created_at.asc(),
)
Index(
    "idx_assistant_tool_call_log_thread_started",
    AssistantToolCallLog.thread_id,
    AssistantToolCallLog.started_at.desc(),
)
Index(
    "idx_assistant_change_request_status_created",
    AssistantChangeRequest.status,
    AssistantChangeRequest.created_at.desc(),
)
Index(
    "idx_assistant_change_request_created_by",
    AssistantChangeRequest.created_by,
    AssistantChangeRequest.created_at.desc(),
)
Index(
    "idx_assistant_weekly_insight_week_start",
    AssistantWeeklyInsight.week_start.desc(),
)


__all__ = [
    "AssistantChatThread",
    "AssistantChatMessage",
    "AssistantToolCallLog",
    "AssistantChangeRequest",
    "AssistantUserProfile",
    "AssistantWeeklyInsight",
]
