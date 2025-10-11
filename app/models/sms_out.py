"""Model kolejki wysyłki SMS (ctip.sms_out)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class SmsOut(Base):
    """Wiadomość SMS oczekująca na wysyłkę lub zarchiwizowana po wysyłce."""

    __tablename__ = "sms_out"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(UTC))
    dest: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, default="ivr")
    status: Mapped[str] = mapped_column(Text, default="NEW")
    error_msg: Mapped[str | None] = mapped_column(Text)
    call_id: Mapped[int | None] = mapped_column(ForeignKey("ctip.calls.id", ondelete="SET NULL"))
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[int | None] = mapped_column()
    template_id: Mapped[int | None] = mapped_column(
        ForeignKey("ctip.sms_template.id", ondelete="SET NULL")
    )
    origin: Mapped[str] = mapped_column(Text, default="ui")
    provider_msg_id: Mapped[str | None] = mapped_column(Text)
    provider_status: Mapped[str | None] = mapped_column(Text)
    provider_error_code: Mapped[str | None] = mapped_column(Text)
    provider_error_desc: Mapped[str | None] = mapped_column(Text)

    call: Mapped[Call | None] = relationship(back_populates="sms_list")
    template: Mapped[SmsTemplate | None] = relationship(lazy="joined")


from .call import Call  # noqa: E402
from .sms_template import SmsTemplate  # noqa: E402

Index("idx_sms_out_status", SmsOut.status)
Index("idx_sms_out_dest_created", SmsOut.dest, SmsOut.created_at.desc())
Index("idx_sms_out_created_by", SmsOut.created_by, SmsOut.created_at.desc())
Index(
    "uq_sms_out_callid_ivr",
    SmsOut.call_id,
    unique=True,
    postgresql_where=(SmsOut.source == "ivr"),
)
