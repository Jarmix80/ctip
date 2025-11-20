"""Model tabeli ctip.call_events."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class CallEvent(Base):
    """Poszczególne zdarzenia CTIP powiązane z połączeniem."""

    __tablename__ = "call_events"
    __table_args__ = (
        Index("idx_events_call_id", "call_id"),
        Index("idx_events_ts", "ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int | None] = mapped_column(ForeignKey("ctip.calls.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    typ: Mapped[str] = mapped_column(Text)
    ext: Mapped[str | None] = mapped_column(Text)
    number: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[str | None] = mapped_column(Text)

    call: Mapped[Call | None] = relationship(back_populates="events")


from .call import Call  # noqa: E402
