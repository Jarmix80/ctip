"""Schematy API modułu automatyzacji wysyłek."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.services.shipping_phone import normalize_polish_shipping_phone

SHIPPING_LABEL_TEXT_LIMIT = 81


def _shipping_character_count_label(count: int) -> str:
    """Odmienia techniczny licznik znaków używany w błędach walidacji."""
    last_two = count % 100
    last = count % 10
    if count == 1:
        return "1 znak"
    if 2 <= last <= 4 and not 12 <= last_two <= 14:
        return f"{count} znaki"
    return f"{count} znaków"


def _normalize_shipping_label_text(value: str | None, *, label: str) -> str | None:
    """Normalizuje treść etykiety i zwraca dokładny komunikat przekroczenia limitu."""
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ValueError(f"{label} nie może być pusta.")
    if len(normalized) > SHIPPING_LABEL_TEXT_LIMIT:
        overage = len(normalized) - SHIPPING_LABEL_TEXT_LIMIT
        raise ValueError(
            f"{label} ma {len(normalized)} znaków. "
            f"Usuń {_shipping_character_count_label(overage)}, aby zmieścić się "
            f"w limicie {SHIPPING_LABEL_TEXT_LIMIT}."
        )
    return normalized


class StrictShippingRequest(BaseModel):
    """Bazowy model odrzucający nieznane pola zapisu."""

    model_config = ConfigDict(extra="forbid")


class ShippingAddressRequest(StrictShippingRequest):
    """Zweryfikowany krajowy adres odbiorcy przesyłki."""

    company_name: str = Field(min_length=2, max_length=100)
    contact_name: str | None = Field(default=None, max_length=100)
    street: str = Field(min_length=3, max_length=100)
    postal_code: str = Field(pattern=r"^\d{2}-\d{3}$")
    city: str = Field(min_length=2, max_length=50)
    country_code: Literal["PL"] = "PL"
    phone: str = Field(min_length=7, max_length=30)
    email: EmailStr | None = Field(default=None, max_length=100)
    source: Literal["location", "order", "client", "saved", "manual"]
    location_text: str | None = Field(default=None, max_length=500)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        """Waliduje i normalizuje wyłącznie dziewięciocyfrowy numer polski."""
        return normalize_polish_shipping_phone(value)


class ShippingGeocoderRequest(StrictShippingRequest):
    """Niepełny adres pocztowy przekazywany do serwerowego geokodera."""

    street: str = Field(min_length=1, max_length=250)
    postal_code: str | None = Field(default=None, max_length=20)
    city: str | None = Field(default=None, max_length=150)

    @field_validator("street", "postal_code", "city", mode="before")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        """Scala białe znaki i zamienia puste wartości opcjonalne na `None`."""
        if value is None:
            return None
        normalized = " ".join(str(value).split())
        return normalized or None

    @field_validator("postal_code")
    @classmethod
    def validate_postal_code(cls, value: str | None) -> str | None:
        """Normalizuje pięć cyfr, a niepełną wartość pozostawia geokoderowi."""
        digits = "".join(character for character in value or "" if character.isdigit())
        if len(digits) == 5:
            return f"{digits[:2]}-{digits[2:]}"
        return value


class ShippingReviewItemRequest(StrictShippingRequest):
    """Wybór części, ceny netto i zgody na wydanie przy stanie zerowym."""

    firebird_warehouse_item_id: int = Field(gt=0)
    quantity: Decimal = Field(gt=0, le=100, decimal_places=3)
    unit_price_net: Decimal | None = Field(default=None, gt=0, le=1_000_000, decimal_places=4)
    remember_for_model: bool = False
    allow_negative_stock: bool = False


class ShippingReviewRequest(StrictShippingRequest):
    """Akceptacja adresu, kontaktu, wagi, pozycji i sposobu rozliczenia wysyłki."""

    address: ShippingAddressRequest
    location_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    weight_kg: Decimal = Field(gt=0, le=31.5, decimal_places=3)
    items: list[ShippingReviewItemRequest] = Field(min_length=1, max_length=20)
    label_text: str | None = Field(default=None, min_length=1, max_length=81)
    save_address: bool = True
    invoice_required: bool = False

    @field_validator("label_text", mode="before")
    @classmethod
    def normalize_label_text(cls, value: str | None) -> str | None:
        """Scala białe znaki i zwraca czytelny błąd przekroczenia limitu DPD."""
        return _normalize_shipping_label_text(value, label="Treść etykiety DPD")


class ShippingCreateRequest(StrictShippingRequest):
    """Idempotentne żądanie utworzenia przesyłki DPD."""

    order_table_id: int = Field(gt=0)
    idempotency_key: UUID


class ShippingBulkCreateRequest(StrictShippingRequest):
    """Zbiorcze generowanie etykiet dla wybranych albo wszystkich gotowych spraw."""

    order_table_ids: list[int] = Field(default_factory=list, max_length=100)
    all_ready: bool = False

    @field_validator("order_table_ids")
    @classmethod
    def normalize_order_ids(cls, value: list[int]) -> list[int]:
        """Usuwa powtórzenia, zachowując kolejność zleceń."""
        normalized: list[int] = []
        seen: set[int] = set()
        for order_id in value:
            if order_id <= 0:
                raise ValueError("Identyfikator zlecenia musi być większy od zera.")
            if order_id not in seen:
                normalized.append(order_id)
                seen.add(order_id)
        return normalized

    @model_validator(mode="after")
    def validate_scope(self) -> ShippingBulkCreateRequest:
        """Wymaga wybranych zleceń albo jawnej operacji dla wszystkich gotowych."""
        if self.all_ready == bool(self.order_table_ids):
            raise ValueError("Wybierz zlecenia albo użyj opcji wszystkich gotowych.")
        return self


class ShippingConsolidatedCreateRequest(StrictShippingRequest):
    """Utworzenie jednej fizycznej paczki dla kilku zgodnych zleceń."""

    order_table_ids: list[int] = Field(min_length=2, max_length=20)
    idempotency_key: UUID
    label_text: str | None = Field(default=None, min_length=1, max_length=81)

    @field_validator("order_table_ids")
    @classmethod
    def normalize_order_ids(cls, value: list[int]) -> list[int]:
        """Usuwa powtórzenia i wymaga co najmniej dwóch różnych zleceń."""
        normalized = list(dict.fromkeys(value))
        if len(normalized) < 2:
            raise ValueError("Wspólna paczka wymaga co najmniej dwóch różnych zleceń.")
        if any(order_id <= 0 for order_id in normalized):
            raise ValueError("Identyfikator zlecenia musi być większy od zera.")
        return normalized

    @field_validator("label_text", mode="before")
    @classmethod
    def normalize_label_text(cls, value: str | None) -> str | None:
        """Normalizuje tekst wspólnej etykiety i raportuje przekroczenie limitu DPD."""
        return _normalize_shipping_label_text(value, label="Treść wspólnej etykiety DPD")


class ShippingAttachExistingRequest(StrictShippingRequest):
    """Dołączenie gotowych zleceń do istniejącej fizycznej paczki."""

    primary_order_table_id: int = Field(gt=0)
    additional_order_table_ids: list[int] = Field(min_length=1, max_length=19)
    idempotency_key: UUID
    confirm_weight_within_existing_label: bool

    @field_validator("additional_order_table_ids")
    @classmethod
    def normalize_additional_order_ids(cls, value: list[int]) -> list[int]:
        """Usuwa powtórzenia i odrzuca niepoprawne identyfikatory zleceń."""
        normalized = list(dict.fromkeys(value))
        if not normalized or any(order_id <= 0 for order_id in normalized):
            raise ValueError("Wybierz co najmniej jedno poprawne zlecenie do dołączenia.")
        return normalized


class ShippingManualTrackingRequest(StrictShippingRequest):
    """Rejestracja przesyłki utworzonej ręcznie poza CTIP."""

    order_table_id: int = Field(gt=0)
    idempotency_key: UUID
    tracking_number: str = Field(min_length=5, max_length=100)


class ShippingDayCloseRequest(StrictShippingRequest):
    """Potwierdzenie przekazania paczek kurierowi i zamknięcia dnia."""

    business_date: date
    confirm_handover: bool


class ShippingOrderCloseRequest(StrictShippingRequest):
    """Potwierdzenie przekazania kurierowi jednej wybranej paczki."""

    confirm_handover: bool


class ShippingCompatibilityReviewRequest(StrictShippingRequest):
    """Zbiorcza decyzja operatora dla sugestii zgodności."""

    mapping_ids: list[int] = Field(min_length=1, max_length=200)
    action: Literal["confirm", "reject"]
    note: str | None = Field(default=None, max_length=500)


class ShippingCompatibilityManualRequest(StrictShippingRequest):
    """Ręczne przypisanie kartoteki magazynowej do modelu Firebird."""

    firebird_model_id: int = Field(gt=0)
    firebird_warehouse_item_id: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=500)


class ShippingCompatibilityManualBatchRequest(StrictShippingRequest):
    """Ręczne przypisanie kartoteki do wielu modeli Firebird."""

    firebird_model_ids: list[int] = Field(min_length=1, max_length=100)
    firebird_warehouse_item_id: int = Field(gt=0)
    note: str | None = Field(default=None, max_length=500)

    @field_validator("firebird_model_ids")
    @classmethod
    def normalize_model_ids(cls, value: list[int]) -> list[int]:
        """Usuwa powtórzenia i odrzuca nieprawidłowe identyfikatory modeli."""
        normalized: list[int] = []
        seen: set[int] = set()
        for model_id in value:
            if model_id <= 0:
                raise ValueError("Identyfikator modelu musi być większy od zera.")
            if model_id not in seen:
                normalized.append(model_id)
                seen.add(model_id)
        return normalized


class ShippingCompatibilityWebRequest(StrictShippingRequest):
    """Wybrane kartoteki przeznaczone do ręcznego sprawdzenia w WWW."""

    warehouse_item_ids: list[int] = Field(min_length=1, max_length=20)


__all__ = [
    "ShippingAttachExistingRequest",
    "ShippingCompatibilityManualBatchRequest",
    "ShippingCompatibilityManualRequest",
    "ShippingCompatibilityReviewRequest",
    "ShippingCompatibilityWebRequest",
    "ShippingConsolidatedCreateRequest",
    "ShippingCreateRequest",
    "ShippingDayCloseRequest",
    "ShippingGeocoderRequest",
    "ShippingManualTrackingRequest",
    "ShippingOrderCloseRequest",
    "ShippingReviewRequest",
]
