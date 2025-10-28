"""Model mapowania IVR na automatyczne SMS."""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class IvrMap(Base):
    """Powiązanie numeru wewnętrznego z treścią SMS wysyłaną automatycznie."""

    __tablename__ = "ivr_map"
    __table_args__ = (UniqueConstraint("ext", name="uq_ivr_map_ext"),)

    ext: Mapped[str] = mapped_column(Text, primary_key=True)
    digit: Mapped[int] = mapped_column(Integer, nullable=False)
    sms_text: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


__all__ = ["IvrMap"]
