"""Model tabeli ctip.calls."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Call(Base):
    """Zarejestrowane połączenie telefoniczne."""

    __tablename__ = "calls"
    __table_args__ = (
        CheckConstraint("direction in ('IN','OUT')", name="calls_direction_check"),
        CheckConstraint(
            "disposition in ('ANSWERED','NO_ANSWER','BUSY','FAILED','UNKNOWN')",
            name="calls_disposition_check",
        ),
        Index("idx_calls_answered_by", "answered_by"),
        Index("idx_calls_direction", "direction"),
        Index("idx_calls_ext", "ext"),
        Index("idx_calls_started_at", "started_at"),
        Index("idx_calls_number", "number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ext: Mapped[str] = mapped_column(Text, nullable=False)
    number: Mapped[str | None] = mapped_column(Text)
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    answered_by: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column()
    connected_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    disposition: Mapped[str] = mapped_column(Text, default="UNKNOWN", nullable=False)
    last_state: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    events: Mapped[list[CallEvent]] = relationship(
        back_populates="call", cascade="all, delete-orphan", lazy="selectin"
    )
    sms_list: Mapped[list[SmsOut]] = relationship(
        back_populates="call", cascade="save-update", lazy="selectin"
    )


from .call_event import CallEvent  # noqa: E402
from .sms_out import SmsOut  # noqa: E402
