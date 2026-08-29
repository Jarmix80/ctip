"""Schematy API modułu automatyzacji wysyłek."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class StrictShippingRequest(BaseModel):
    """Bazowy model odrzucający nieznane pola zapisu."""

    model_config = ConfigDict(extra="forbid")


class ShippingAddressRequest(StrictShippingRequest):
    """Zweryfikowany krajowy adres odbiorcy przesyłki."""

    company_name: str = Field(min_length=2, max_length=250)
    contact_name: str | None = Field(default=None, max_length=150)
    street: str = Field(min_length=3, max_length=250)
    postal_code: str = Field(pattern=r"^\d{2}-\d{3}$")
    city: str = Field(min_length=2, max_length=150)
    country_code: Literal["PL"] = "PL"
    phone: str = Field(min_length=7, max_length=30)
    email: EmailStr | None = None
    source: Literal["location", "order", "client", "saved", "manual"]
    location_text: str | None = Field(default=None, max_length=500)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        """Usuwa separatory i normalizuje polski numer do postaci międzynarodowej."""
        normalized = "".join(
            character for character in value if character.isdigit() or character == "+"
        )
        if normalized.startswith("00"):
            normalized = "+" + normalized[2:]
        elif normalized.startswith("0"):
            normalized = "+48" + normalized[1:]
        elif not normalized.startswith("+") and len(normalized) == 9:
            normalized = "+48" + normalized
        if len(normalized) < 9:
            raise ValueError("Numer telefonu jest za krótki.")
        return normalized


class ShippingReviewItemRequest(StrictShippingRequest):
    """Wybór tonera ze stanu magazynowego."""

    firebird_warehouse_item_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, le=100, decimal_places=3)
    remember_for_model: bool = False


class ShippingReviewRequest(StrictShippingRequest):
    """Akceptacja adresu, kontaktu, wagi i pozycji przesyłki."""

    address: ShippingAddressRequest
    weight_kg: Decimal = Field(gt=0, le=31.5, decimal_places=3)
    items: list[ShippingReviewItemRequest] = Field(min_length=1, max_length=20)
    save_address: bool = True


class ShippingCreateRequest(StrictShippingRequest):
    """Idempotentne żądanie utworzenia przesyłki DPD."""

    order_table_id: int = Field(gt=0)
    idempotency_key: UUID


class ShippingManualTrackingRequest(StrictShippingRequest):
    """Rejestracja przesyłki utworzonej ręcznie poza CTIP."""

    order_table_id: int = Field(gt=0)
    idempotency_key: UUID
    tracking_number: str = Field(min_length=5, max_length=100)


class ShippingDayCloseRequest(StrictShippingRequest):
    """Potwierdzenie przekazania paczek kurierowi i zamknięcia dnia."""

    business_date: date
    confirm_handover: bool


__all__ = [
    "ShippingCreateRequest",
    "ShippingDayCloseRequest",
    "ShippingManualTrackingRequest",
    "ShippingReviewRequest",
]
