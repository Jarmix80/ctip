"""Orkiestracja przeglądu, nadania i zamknięcia przesyłek."""

from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from app.core.config import settings
from app.models import (
    AdminUser,
    ShippingAddress,
    ShippingCase,
    ShippingConsumableCompatibility,
    ShippingDayClose,
    ShippingEvent,
    ShippingItem,
    ShippingShipment,
)
from app.schemas.shipping import ShippingReviewRequest
from app.services.dpd_shipping import (
    DPD_LABEL_TEXT_LIMIT,
    DpdShippingClient,
    DpdTransportError,
    normalize_dpd_label_text,
)
from app.services.email_client import send_smtp_message
from app.services.firebird_runtime import firebird_writes_enabled
from app.services.shipping_archive import archive_shipping_shipment
from app.services.shipping_firebird import (
    ShippingOrderStateConflict,
    finalize_shipping_order,
    load_physical_stock,
    load_shipping_order_state,
    shipping_document_mode,
    shipping_order_state_conflict_message,
    shipping_order_state_payload,
    write_shipment_to_order,
)
from app.services.sms_provider import HttpSmsProvider

ACTIVE_RESERVATION_STATUSES = ("ready", "shipment_created", "handed_over")


class ShippingConflictError(RuntimeError):
    """Konflikt stanu lub klucza idempotencji w procesie wysyłki."""


class ShippingLocationChangedError(ShippingConflictError):
    """Zmiana lokalizacji urządzenia wymagająca ponownej weryfikacji adresu."""


def _now() -> datetime:
    return datetime.now(UTC)


def _text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def build_shipping_label_text(
    *,
    order_numbers: list[str],
    items: list[dict[str, Any]],
) -> str:
    """Buduje widoczną treść etykiety z numerów zleceń, ilości i nazw części."""
    segments = list(dict.fromkeys(value for value in order_numbers if _text(value)))
    for item in items:
        try:
            quantity = f"{float(item.get('quantity') or 0):g}"
        except (TypeError, ValueError):
            quantity = _text(item.get("quantity")) or "1"
        item_name = _text(item.get("item_name")) or "część"
        segments.append(f"{quantity}x {item_name}")
    generated = "; ".join(segments) or "Materiały serwisowe"
    if len(generated) <= DPD_LABEL_TEXT_LIMIT:
        return generated
    return generated[: DPD_LABEL_TEXT_LIMIT - 1].rstrip() + "…"


def _shipping_case_label_text(case: ShippingCase) -> str:
    """Zwraca zapisany tekst albo zgodny z interfejsem tekst wygenerowany ze sprawy."""
    if _text(case.label_text):
        return normalize_dpd_label_text(case.label_text)
    return build_shipping_label_text(
        order_numbers=[f"{case.firebird_order_id}/{case.firebird_order_year}"],
        items=[{"quantity": item.quantity, "item_name": item.item_name} for item in case.items],
    )


def _shipment_label_text(shipment: ShippingShipment | None) -> str | None:
    """Odczytuje dokładną treść nowej etykiety albo rekonstruuje starsze żądanie DPD."""
    if shipment is None or not isinstance(shipment.provider_request, dict):
        return None
    request = shipment.provider_request
    metadata = request.get("_ctip") if isinstance(request.get("_ctip"), dict) else {}
    stored = _text(metadata.get("label_text"))
    if stored:
        return normalize_dpd_label_text(stored)
    try:
        package = (request.get("packages") or [])[0]
        parcel = (package.get("parcels") or [])[0]
    except (IndexError, TypeError):
        return None
    reconstructed = " ".join(
        value for value in (_text(package.get("ref1")), _text(parcel.get("content"))) if value
    )
    if not reconstructed:
        return None
    if len(reconstructed) > DPD_LABEL_TEXT_LIMIT:
        reconstructed = reconstructed[: DPD_LABEL_TEXT_LIMIT - 1].rstrip() + "…"
    return reconstructed


def _shipping_item_price(
    *,
    order_kind: Any,
    invoice_required: bool,
    requested_price: Decimal | None,
    warehouse: dict[str, Any],
) -> tuple[Decimal, Decimal, Decimal, str]:
    """Ustala cenę dokumentu i zapisuje źródło podpowiedzi lub zmiany ręcznej."""
    catalog_price = Decimal(str(warehouse.get("price_net") or 0))
    purchase_price = Decimal(str(warehouse.get("purchase_price_net") or 0))
    contract_order = (_text(order_kind) or "").casefold() == "umowa"
    uses_sale_price = invoice_required or not contract_order
    if not uses_sale_price:
        if purchase_price <= 0:
            raise ValueError(f"Pozycja {warehouse['item_name']} nie ma ceny zakupu w kartotece MS.")
        return purchase_price, catalog_price, purchase_price, "purchase_contract"

    default_price = catalog_price if catalog_price > 0 else purchase_price
    default_source = "sale" if catalog_price > 0 else "purchase_fallback"
    selected_price = requested_price if requested_price is not None else default_price
    if selected_price <= 0:
        raise ValueError(
            f"Pozycja {warehouse['item_name']} nie ma ceny sprzedaży ani zakupu. "
            "Wpisz cenę sprzedaży netto."
        )
    source = "manual" if selected_price != default_price else default_source
    return selected_price, catalog_price, purchase_price, source


def normalize_shipping_location(value: Any) -> str:
    """Normalizuje lokalizację tak, aby kosmetyczne różnice nie zmieniały odcisku."""
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _shipping_consolidation_signature(address: dict[str, Any]) -> tuple[str, str, str, str]:
    """Buduje podpis firmy i adresu wymagany dla jednej wspólnej paczki."""
    return (
        normalize_shipping_location(address.get("company_name")),
        normalize_shipping_location(address.get("street")),
        (_text(address.get("postal_code")) or "").upper(),
        normalize_shipping_location(address.get("city")),
    )


def shipping_shipment_consolidation(
    shipment: ShippingShipment | None,
) -> dict[str, Any] | None:
    """Zwraca bezpieczne metadane wspólnej paczki zapisane przy przesyłce."""
    if shipment is None or not isinstance(shipment.provider_request, dict):
        return None
    raw = shipment.provider_request.get("ctip_consolidation")
    if not isinstance(raw, dict):
        return None
    order_table_ids = [
        int(value)
        for value in raw.get("order_table_ids", [])
        if isinstance(value, int) and value > 0
    ]
    if len(order_table_ids) < 2:
        return None
    order_numbers = [str(value) for value in raw.get("order_numbers", []) if value]
    return {
        "group_id": _text(raw.get("group_id")),
        "count": len(order_table_ids),
        "order_table_ids": order_table_ids,
        "order_numbers": order_numbers,
        "primary_order_table_id": int(raw.get("primary_order_table_id") or order_table_ids[0]),
    }


def _bind_shipping_case(case: ShippingCase, shipment: ShippingShipment) -> None:
    """Wiąże jawnie załadowaną sprawę i przesyłkę bez uruchamiania lazy load."""
    set_committed_value(case, "shipment", shipment)
    set_committed_value(shipment, "shipping_case", case)


def _shipping_notification_group_key(shipment: ShippingShipment) -> str | None:
    """Zwraca klucz fizycznej wspólnej paczki używany do deduplikacji powiadomień."""
    consolidation = shipping_shipment_consolidation(shipment)
    if consolidation is None:
        return None
    return _text(consolidation.get("group_id")) or None


def _shipping_notification_attempted(shipment: ShippingShipment) -> bool:
    """Sprawdza, czy dla rekordu wykonano już rzeczywistą albo symulowaną próbę powiadomienia."""
    return shipment.notification_sms_status in {"sent", "failed", "simulated"}


def _skip_consolidated_notifications(shipment: ShippingShipment) -> None:
    """Oznacza pominięcie duplikatu powiadomień dla kolejnego zlecenia wspólnej paczki."""
    shipment.notification_sms_status = "skipped_consolidated"
    shipment.notification_email_status = "skipped_consolidated"
    shipment.notification_error = None


def build_shipping_consolidation_groups(
    cases: list[ShippingCase],
) -> dict[int, dict[str, Any]]:
    """Wykrywa gotowe zlecenia tej samej firmy kierowane na identyczny adres."""
    grouped: dict[tuple[str, str, str, str], list[ShippingCase]] = {}
    for case in cases:
        if case.status != "ready" or case.shipment is not None:
            continue
        address = case.address_snapshot or {}
        signature = _shipping_consolidation_signature(address)
        if not all(signature):
            continue
        grouped.setdefault(signature, []).append(case)

    result: dict[int, dict[str, Any]] = {}
    for signature, matches in grouped.items():
        if len(matches) < 2:
            continue
        ordered = sorted(
            matches, key=lambda case: (case.firebird_order_year, case.firebird_order_id)
        )
        group_key = hashlib.sha256("|".join(signature).encode("utf-8")).hexdigest()[:16]
        details = {
            "group_key": group_key,
            "count": len(ordered),
            "order_table_ids": [case.firebird_order_table_id for case in ordered],
            "order_numbers": [
                f"{case.firebird_order_id}/{case.firebird_order_year}" for case in ordered
            ],
            "company_name": ordered[0].address_snapshot.get("company_name"),
            "address": ", ".join(
                value
                for value in (
                    ordered[0].address_snapshot.get("street"),
                    " ".join(
                        value
                        for value in (
                            ordered[0].address_snapshot.get("postal_code"),
                            ordered[0].address_snapshot.get("city"),
                        )
                        if value
                    ),
                )
                if value
            ),
        }
        for case in ordered:
            result[case.firebird_order_table_id] = details
    return result


def _shipping_declared_weight(shipment: ShippingShipment) -> Decimal | None:
    """Odczytuje wagę zapisaną przy istniejącym liście przewozowym."""
    request = shipment.provider_request if isinstance(shipment.provider_request, dict) else {}
    try:
        package = (request.get("packages") or [])[0]
        parcel = (package.get("parcels") or [])[0]
        weight = Decimal(str(parcel.get("weight")))
    except (IndexError, InvalidOperation, TypeError, AttributeError):
        return None
    return weight if weight > 0 else None


def build_shipping_existing_label_attachment_groups(
    cases: list[ShippingCase],
) -> dict[int, dict[str, Any]]:
    """Wykrywa gotowe zlecenia możliwe do dołączenia do jednej istniejącej etykiety."""
    grouped: dict[tuple[str, str, str, str], list[ShippingCase]] = {}
    for case in cases:
        signature = _shipping_consolidation_signature(case.address_snapshot or {})
        if all(signature):
            grouped.setdefault(signature, []).append(case)

    result: dict[int, dict[str, Any]] = {}
    for signature, matches in grouped.items():
        ready = [case for case in matches if case.status == "ready" and case.shipment is None]
        label_groups: dict[str, list[ShippingCase]] = {}
        for case in matches:
            shipment = case.shipment
            if (
                case.status != "shipment_created"
                or shipment is None
                or shipment.status != "label_ready"
                or shipment.handed_over_at is not None
                or not shipment.tracking_number
                or not shipment.label_content
            ):
                continue
            label_groups.setdefault(str(shipment.tracking_number), []).append(case)
        if not ready or len(label_groups) != 1:
            continue
        tracking_number, existing = next(iter(label_groups.items()))
        available_slots = 20 - len(existing)
        if available_slots <= 0:
            continue
        existing_ids = {case.firebird_order_table_id for case in existing}
        primary = existing[0]
        saved_consolidation = shipping_shipment_consolidation(primary.shipment)
        saved_primary_id = int((saved_consolidation or {}).get("primary_order_table_id") or 0)
        if saved_primary_id in existing_ids:
            primary = next(
                case for case in existing if case.firebird_order_table_id == saved_primary_id
            )
        ordered_ready = sorted(
            ready, key=lambda case: (case.firebird_order_year, case.firebird_order_id)
        )[:available_slots]
        selection_ids = [
            primary.firebird_order_table_id,
            *(case.firebird_order_table_id for case in ordered_ready),
        ]
        group_key = hashlib.sha256(
            "|".join((*signature, tracking_number)).encode("utf-8")
        ).hexdigest()[:16]
        declared_weight = _shipping_declared_weight(primary.shipment)
        details = {
            "group_key": group_key,
            "primary_order_table_id": primary.firebird_order_table_id,
            "primary_order_number": f"{primary.firebird_order_id}/{primary.firebird_order_year}",
            "ready_order_table_ids": [case.firebird_order_table_id for case in ordered_ready],
            "ready_order_numbers": [
                f"{case.firebird_order_id}/{case.firebird_order_year}" for case in ordered_ready
            ],
            "selection_order_table_ids": selection_ids,
            "tracking_number": tracking_number,
            "declared_weight_kg": float(declared_weight) if declared_weight else None,
            "company_name": primary.address_snapshot.get("company_name"),
            "address": ", ".join(
                value
                for value in (
                    primary.address_snapshot.get("street"),
                    " ".join(
                        value
                        for value in (
                            primary.address_snapshot.get("postal_code"),
                            primary.address_snapshot.get("city"),
                        )
                        if value
                    ),
                )
                if value
            ),
        }
        for case in [*existing, *ordered_ready]:
            result[case.firebird_order_table_id] = details
    return result


def shipping_location_context(order: dict[str, Any]) -> dict[str, Any]:
    """Buduje bieżący odcisk lokalizacji urządzenia na podstawie danych MS."""
    machine_location = _text(order.get("machine_location"))
    order_location = _text(order.get("order_location"))
    current_text = machine_location or order_location
    source = "machine" if machine_location else "order" if order_location else "missing"
    identity = "|".join(
        (
            str(order.get("company_id") or ""),
            str(order.get("client_id") or ""),
            str(order.get("machine_id") or ""),
            normalize_shipping_location(current_text),
        )
    )
    fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return {
        "source": source,
        "source_label": {
            "machine": "Bieżąca lokalizacja urządzenia w MS",
            "order": "Lokalizacja zapisana na zleceniu",
            "missing": "Brak lokalizacji urządzenia w MS",
        }[source],
        "current_text": current_text,
        "machine_text": machine_location,
        "order_text": order_location,
        "fingerprint": fingerprint,
        "verifiable": bool(current_text),
        "machine_differs_from_order": bool(
            machine_location
            and order_location
            and normalize_shipping_location(machine_location)
            != normalize_shipping_location(order_location)
        ),
    }


def parse_shipping_location(value: Any) -> dict[str, str] | None:
    """Rozpoznaje tylko jednoznaczny polski adres zawierający kod i numer budynku."""
    raw = _text(value)
    if not raw:
        return None
    postcodes = re.findall(r"(?<!\d)(\d{2}-\d{3})(?!\d)", raw)
    if len(postcodes) != 1:
        return None
    phone_match = re.search(
        r"\b(?:tel(?:efon)?|kom)\.?\s*:?[\s-]*([+]?[\d][\d\s().-]{6,})",
        raw,
        flags=re.IGNORECASE,
    )
    phone = _text(phone_match.group(1)) if phone_match else None
    address_text = raw[: phone_match.start()].rstrip(" ,;") if phone_match else raw
    postcode_match = re.search(r"(?<!\d)\d{2}-\d{3}(?!\d)", address_text)
    if postcode_match is None:
        return None
    before = address_text[: postcode_match.start()].strip(" ,;")
    after = address_text[postcode_match.end() :].strip(" ,;")

    def has_building_number(text: str) -> bool:
        return bool(re.search(r"(?<!\w)\d+[a-z]?(?:\s*[/\\-]\s*\d+[a-z]?)?(?!\w)", text, re.I))

    street = ""
    city = ""
    if before and after:
        if has_building_number(before) and not re.search(r"\d", after):
            street, city = before, after
        elif has_building_number(after) and not re.search(r"\d", before):
            city, street = before, after
    elif before and "," in before:
        possible_street, possible_city = (part.strip() for part in before.rsplit(",", 1))
        if has_building_number(possible_street) and not re.search(r"\d", possible_city):
            street, city = possible_street, possible_city
    if not street or not city:
        return None
    result = {
        "street": re.sub(r"\s+", " ", street).strip(),
        "postal_code": postcodes[0],
        "city": re.sub(r"\s+", " ", city).strip(),
    }
    if phone:
        result["phone"] = phone
    return result


def _missing_address_fields(address: dict[str, Any]) -> list[str]:
    """Zwraca wymagane pola, których źródło nie wypełnia poprawnie."""
    missing: list[str] = []
    if len(_text(address.get("company_name")) or "") < 2:
        missing.append("company_name")
    if len(_text(address.get("street")) or "") < 3:
        missing.append("street")
    if not re.fullmatch(r"\d{2}-\d{3}", _text(address.get("postal_code")) or ""):
        missing.append("postal_code")
    if len(_text(address.get("city")) or "") < 2:
        missing.append("city")
    phone_digits = re.sub(r"\D", "", _text(address.get("phone")) or "")
    if len(phone_digits) < 9:
        missing.append("phone")
    return missing


def _complete_address(address: dict[str, Any]) -> bool:
    return not _missing_address_fields(address)


def _has_shipping_address_data(address: dict[str, Any]) -> bool:
    """Sprawdza, czy źródło zawiera choć jeden element fizycznego adresu."""
    return any(_text(address.get(field)) for field in ("street", "postal_code", "city"))


def _address_from_order(
    order: dict[str, Any],
    *,
    prefix: str,
    source: str,
    location_text: str | None = None,
) -> dict[str, Any]:
    return {
        "company_name": order.get(f"{prefix}_company_name")
        or order.get("order_company_name")
        or order.get("client_company_name"),
        "contact_name": order.get("preferred_contact_name")
        or order.get(f"{prefix}_contact_name")
        or order.get("contact_name")
        or order.get("client_contact_name"),
        "street": order.get(f"{prefix}_street"),
        "postal_code": order.get(f"{prefix}_postal_code"),
        "city": order.get(f"{prefix}_city"),
        "country_code": "PL",
        "phone": order.get("preferred_contact_phone")
        or order.get(f"{prefix}_phone")
        or order.get("order_phone")
        or order.get("client_phone"),
        "email": order.get("preferred_contact_email")
        or order.get(f"{prefix}_email")
        or order.get("order_email")
        or order.get("machine_email")
        or order.get("client_email"),
        "source": source,
        "location_text": location_text,
    }


def build_shipping_address_candidates(
    order: dict[str, Any],
    saved_addresses: list[ShippingAddress],
    case: ShippingCase | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Buduje jawne źródła adresu i wybiera bezpieczny domyślny wariant."""
    context = shipping_location_context(order)
    candidates: list[dict[str, Any]] = []
    seen_selectable: set[tuple[str, str, str]] = set()

    def add_candidate(
        *,
        key: str,
        label: str,
        description: str,
        address: dict[str, Any] | None,
        selectable: bool,
        usable: bool | None = None,
        warning: str | None = None,
    ) -> None:
        if address is not None and not _has_shipping_address_data(address):
            return
        if address and selectable:
            signature = (
                normalize_shipping_location(address.get("street")),
                _text(address.get("postal_code")) or "",
                normalize_shipping_location(address.get("city")),
            )
            if signature in seen_selectable:
                return
            seen_selectable.add(signature)
        candidates.append(
            {
                "key": key,
                "label": label,
                "description": description,
                "selectable": bool(selectable and address),
                "usable": bool((selectable if usable is None else usable) and address),
                "warning": warning,
                "address": address,
                "missing_fields": _missing_address_fields(address) if address else [],
            }
        )

    case_matches = bool(
        case and case.location_fingerprint and case.location_fingerprint == context["fingerprint"]
    )
    if case and case.address_snapshot:
        add_candidate(
            key="case",
            label="Adres zaakceptowany w tej sprawie",
            description="Ostatni adres zatwierdzony przez operatora.",
            address=case.address_snapshot,
            selectable=case_matches,
            usable=case_matches,
            warning=(
                None
                if case_matches
                else "Lokalizacja urządzenia zmieniła się po akceptacji. Ten adres jest zablokowany."
            ),
        )

    for saved in saved_addresses:
        matches = bool(
            context["verifiable"]
            and saved.firebird_machine_id == order.get("machine_id")
            and saved.location_fingerprint
            and saved.location_fingerprint == context["fingerprint"]
        )
        saved_address = {
            "company_name": saved.company_name,
            "contact_name": saved.contact_name,
            "street": saved.street,
            "postal_code": saved.postal_code,
            "city": saved.city,
            "country_code": saved.country_code,
            "phone": saved.phone,
            "email": saved.email,
            "source": "saved",
            "location_text": saved.location_text_snapshot,
        }
        add_candidate(
            key=f"saved-{saved.id}",
            label="Zweryfikowany adres urządzenia",
            description=f"Zapamiętany dla lokalizacji: {saved.location_text_snapshot or 'brak snapshotu'}.",
            address=saved_address,
            selectable=matches,
            usable=matches,
            warning=(
                None
                if matches
                else "Adres pochodzi z innej lub niezweryfikowanej wersji lokalizacji MS."
            ),
        )

    location_sources = [
        ("machine-location", "Bieżąca lokalizacja urządzenia", order.get("machine_location")),
        ("order-location", "Lokalizacja zapisana na zleceniu", order.get("order_location")),
        ("shipment-address", "Adres przesyłki zapisany w MS", order.get("order_shipping_address")),
        ("machine-address", "Adres urządzenia zapisany w MS", order.get("machine_address")),
    ]
    for key, label, raw_location in location_sources:
        raw_text = _text(raw_location)
        if not raw_text:
            continue
        parsed = parse_shipping_location(raw_text)
        if parsed:
            address = {
                "company_name": order.get("order_company_name") or order.get("client_company_name"),
                "contact_name": order.get("preferred_contact_name")
                or order.get("contact_name")
                or order.get("client_contact_name"),
                **parsed,
                "country_code": "PL",
                "phone": parsed.get("phone")
                or order.get("preferred_contact_phone")
                or order.get("order_phone")
                or order.get("client_phone"),
                "email": order.get("preferred_contact_email")
                or order.get("order_email")
                or order.get("machine_email")
                or order.get("client_email"),
                "source": "location",
                "location_text": raw_text,
            }
            add_candidate(
                key=key,
                label=label,
                description="Adres jednoznacznie rozpoznany z tekstu lokalizacji MS.",
                address=address,
                selectable=_complete_address(address),
                usable=True,
                warning=(
                    None
                    if _complete_address(address)
                    else "Brakuje wymaganych danych kontaktowych."
                ),
            )
        else:
            add_candidate(
                key=key,
                label=label,
                description=raw_text,
                address=None,
                selectable=False,
                usable=False,
                warning="Tekst nie zawiera kompletnego, jednoznacznego adresu. Nie został użyty automatycznie.",
            )

    for prefix, key, label, source in (
        ("branch", "branch", "Adres oddziału klienta", "order"),
        ("order", "order", "Adres firmy zapisany na zleceniu", "order"),
        ("client", "client", "Adres kartoteki klienta", "client"),
    ):
        address = _address_from_order(
            order,
            prefix=prefix,
            source=source,
            location_text=context["current_text"],
        )
        add_candidate(
            key=key,
            label=label,
            description="Ustrukturyzowane pola adresowe Menadżera Serwisu.",
            address=address,
            selectable=_complete_address(address),
            usable=True,
            warning=(
                None
                if _complete_address(address)
                else "Źródło nie zawiera kompletu wymaganych danych."
            ),
        )

    preferred = next((candidate for candidate in candidates if candidate["selectable"]), None)
    if preferred is None:
        partial_priority = {"order": 3, "branch": 2, "client": 1}
        partial_candidates = [
            candidate
            for candidate in candidates
            if candidate["key"] in partial_priority and candidate["address"]
        ]
        partial = max(
            partial_candidates,
            key=lambda candidate: (
                5 - len(candidate["missing_fields"]),
                partial_priority[candidate["key"]],
            ),
            default=None,
        )
        partial_address = (
            dict(partial["address"])
            if partial
            else {
                "company_name": order.get("order_company_name")
                or order.get("client_company_name")
                or "",
                "contact_name": order.get("preferred_contact_name")
                or order.get("contact_name")
                or order.get("client_contact_name"),
                "street": "",
                "postal_code": "",
                "city": "",
                "country_code": "PL",
                "phone": order.get("preferred_contact_phone")
                or order.get("order_phone")
                or order.get("client_phone")
                or "",
                "email": order.get("preferred_contact_email")
                or order.get("order_email")
                or order.get("client_email"),
            }
        )
        partial_address["source"] = "manual"
        partial_address["location_text"] = context["current_text"]
        preferred = {
            "key": "manual",
            "label": "Adres ręczny",
            "description": (
                "Uzupełnij brakujące pola adresu pobranego z MS."
                if partial
                else "Wprowadź i zweryfikuj komplet danych odbiorcy."
            ),
            "selectable": True,
            "usable": True,
            "warning": None,
            "missing_fields": _missing_address_fields(partial_address),
            "address": partial_address,
        }
    return candidates, preferred


def _location_key(order: dict[str, Any], address: dict[str, Any]) -> str:
    context = shipping_location_context(order)
    raw = "|".join(
        str(value or "").strip().casefold()
        for value in (
            context["fingerprint"],
            address.get("street"),
            address.get("postal_code"),
            address.get("city"),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _serialize_case(case: ShippingCase) -> dict[str, Any]:
    shipment = case.shipment
    consolidation = shipping_shipment_consolidation(shipment)
    return {
        "id": case.id,
        "order_table_id": case.firebird_order_table_id,
        "order_id": case.firebird_order_id,
        "order_year": case.firebird_order_year,
        "status": case.status,
        "order_kind": case.order_kind,
        "invoice_required": case.invoice_required,
        "weight_kg": float(case.weight_kg),
        "label_text": _shipping_case_label_text(case),
        "address": case.address_snapshot,
        "location_source": case.location_source,
        "location_text_snapshot": case.location_text_snapshot,
        "location_fingerprint": case.location_fingerprint,
        "items": [
            {
                "id": item.id,
                "firebird_warehouse_item_id": item.firebird_warehouse_item_id,
                "warehouse_id": item.warehouse_id,
                "item_index": item.item_index,
                "item_name": item.item_name,
                "unit": item.unit,
                "quantity": float(item.quantity),
                "price_net": float(item.price_net),
                "catalog_price_net": float(item.catalog_price_net),
                "purchase_price_net": float(item.purchase_price_net),
                "price_source": item.price_source,
                "allow_negative_stock": item.allow_negative_stock,
                "firebird_position_id": item.firebird_position_id,
            }
            for item in case.items
        ],
        "shipment": (
            None
            if shipment is None
            else {
                "id": shipment.id,
                "status": shipment.status,
                "provider_mode": shipment.provider_mode,
                "tracking_number": shipment.tracking_number,
                "label_available": bool(shipment.label_content),
                "firebird_status": shipment.firebird_status,
                "firebird_error": shipment.firebird_error,
                "ms_milestones": {
                    "eligible": shipment.firebird_label_metadata_synced_at is not None,
                    "label_metadata_synced_at": (
                        shipment.firebird_label_metadata_synced_at.isoformat()
                        if shipment.firebird_label_metadata_synced_at
                        else None
                    ),
                    "pickup": {
                        "status": (
                            "synchronized" if shipment.firebird_pickup_event_key else "pending"
                        ),
                        "event_key": shipment.firebird_pickup_event_key,
                        "synced_at": (
                            shipment.firebird_pickup_synced_at.isoformat()
                            if shipment.firebird_pickup_synced_at
                            else None
                        ),
                    },
                    "delivery": {
                        "status": (
                            "synchronized" if shipment.firebird_delivery_event_key else "pending"
                        ),
                        "event_key": shipment.firebird_delivery_event_key,
                        "synced_at": (
                            shipment.firebird_delivery_synced_at.isoformat()
                            if shipment.firebird_delivery_synced_at
                            else None
                        ),
                    },
                    "description": {
                        "status": (
                            "synchronized" if shipment.firebird_description_event_key else "pending"
                        ),
                        "event_key": shipment.firebird_description_event_key,
                        "synced_at": (
                            shipment.firebird_description_synced_at.isoformat()
                            if shipment.firebird_description_synced_at
                            else None
                        ),
                    },
                    "error": shipment.firebird_milestone_error,
                },
                "firebird_rw_id": shipment.firebird_rw_id,
                "firebird_rw_number": shipment.firebird_rw_number,
                "firebird_wz_id": shipment.firebird_wz_id,
                "firebird_wz_number": shipment.firebird_wz_number,
                "firebird_invoice_id": shipment.firebird_invoice_id,
                "firebird_invoice_number": shipment.firebird_invoice_number,
                "notification_sms_status": shipment.notification_sms_status,
                "notification_email_status": shipment.notification_email_status,
                "provider_warnings": list((shipment.provider_response or {}).get("warnings") or []),
                "consolidation": consolidation,
                "created_at": shipment.created_at.isoformat() if shipment.created_at else None,
                "handed_over_at": (
                    shipment.handed_over_at.isoformat() if shipment.handed_over_at else None
                ),
            }
        ),
    }


async def get_shipping_case(session: AsyncSession, order_table_id: int) -> ShippingCase | None:
    """Pobiera sprawę z pozycjami i przesyłką."""
    stmt = (
        select(ShippingCase)
        .options(selectinload(ShippingCase.items), selectinload(ShippingCase.shipment))
        .where(ShippingCase.firebird_order_table_id == int(order_table_id))
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def serialize_shipping_case(
    session: AsyncSession, order_table_id: int
) -> dict[str, Any] | None:
    """Zwraca serializowany stan sprawy dla API."""
    case = await get_shipping_case(session, order_table_id)
    if case is None:
        return None
    payload = _serialize_case(case)
    if case.shipment and case.shipment.tracking_number:
        from app.services.shipping_tracking import tracking_for_waybills

        tracking = await tracking_for_waybills(session, {str(case.shipment.tracking_number)})
        payload["dpd_tracking"] = tracking.get(str(case.shipment.tracking_number))
    else:
        payload["dpd_tracking"] = None
    return payload


async def invalidate_shipping_case_for_location_change(
    session: AsyncSession,
    *,
    case: ShippingCase | None,
    order: dict[str, Any],
    user_id: int | None = None,
) -> bool:
    """Cofa gotową sprawę do weryfikacji, jeżeli lokalizacja w MS się zmieniła."""
    if case is None or case.shipment is not None:
        return False
    context = shipping_location_context(order)
    matches = bool(
        case.location_fingerprint and case.location_fingerprint == context["fingerprint"]
    )
    if matches:
        return False
    if case.status != "review_pending":
        case.status = "review_pending"
        case.updated_at = _now()
        session.add(
            ShippingEvent(
                shipping_case_id=case.id,
                event_type="location_changed",
                payload={
                    "previous_location": case.location_text_snapshot,
                    "current_location": context["current_text"],
                },
                created_by=user_id,
                created_at=_now(),
            )
        )
        await session.commit()
    return True


async def _soft_reservations(
    session: AsyncSession, *, exclude_case_id: int | None = None
) -> dict[int, Decimal]:
    stmt = (
        select(ShippingItem.firebird_warehouse_item_id, func.sum(ShippingItem.quantity))
        .join(ShippingCase, ShippingCase.id == ShippingItem.shipping_case_id)
        .where(ShippingCase.status.in_(ACTIVE_RESERVATION_STATUSES))
        .group_by(ShippingItem.firebird_warehouse_item_id)
    )
    if exclude_case_id is not None:
        stmt = stmt.where(ShippingCase.id != exclude_case_id)
    rows = (await session.execute(stmt)).all()
    return {int(item_id): Decimal(str(quantity or 0)) for item_id, quantity in rows}


async def build_stock_payload(
    session: AsyncSession,
    *,
    model_id: int | None,
    exclude_case_id: int | None = None,
    query: str | None = None,
    compatible_only: bool = False,
    only_available: bool = False,
    limit: int = 2000,
    include_item_ids: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Łączy fizyczny stan Firebird, miękkie rezerwacje i potwierdzoną zgodność."""
    compatible_ids: set[int] = set()
    if model_id:
        stmt = select(ShippingConsumableCompatibility.firebird_warehouse_item_id).where(
            ShippingConsumableCompatibility.firebird_model_id == int(model_id),
            ShippingConsumableCompatibility.status == "confirmed",
        )
        compatible_ids = {int(value) for value in (await session.execute(stmt)).scalars().all()}
    requested_ids: set[int] | None = None
    if compatible_only:
        requested_ids = compatible_ids | set(include_item_ids or set())
    stock = await asyncio.to_thread(
        load_physical_stock,
        warehouse_id=settings.shipping_warehouse_id,
        query=query,
        item_ids=requested_ids,
        only_available=only_available,
        limit=limit,
    )
    reservations = await _soft_reservations(session, exclude_case_id=exclude_case_id)
    for item in stock:
        item_id = int(item["warehouse_item_id"])
        soft_reserved = reservations.get(item_id, Decimal("0"))
        item["soft_reserved_quantity"] = float(soft_reserved)
        item["available_after_soft_reservations"] = max(
            0.0, float(item["available_quantity"]) - float(soft_reserved)
        )
        item["compatible"] = item_id in compatible_ids
    stock.sort(key=lambda item: (not item["compatible"], item.get("item_name") or ""))
    return stock


async def review_shipping_order(
    session: AsyncSession,
    *,
    order: dict[str, Any],
    payload: ShippingReviewRequest,
    user_id: int,
) -> dict[str, Any]:
    """Weryfikuje dane i rozliczenie oraz zastępuje miękką rezerwację pozycji."""
    order_state = shipping_order_state_payload(order)
    if not order_state["can_review"]:
        raise ShippingConflictError(
            shipping_order_state_conflict_message(
                order_state,
                operation="zatwierdzenie danych wysyłki",
            )
        )
    case = await get_shipping_case(session, int(order["order_table_id"]))
    if case and case.shipment is not None:
        raise ShippingConflictError(
            "Dla zlecenia istnieje już przesyłka; nie można zmienić wyboru."
        )
    location_context = shipping_location_context(order)
    if payload.location_fingerprint != location_context["fingerprint"]:
        await invalidate_shipping_case_for_location_change(
            session,
            case=case,
            order=order,
            user_id=user_id,
        )
        raise ShippingLocationChangedError(
            "Lokalizacja urządzenia zmieniła się w MS. Odśwież zlecenie i ponownie zweryfikuj adres."
        )
    now = _now()
    address_data = payload.address.model_dump(mode="json")
    invoice_required = payload.invoice_required
    location_key = _location_key(order, address_data)
    address_row = None
    if payload.save_address:
        stmt = select(ShippingAddress).where(
            ShippingAddress.firebird_client_id == int(order["client_id"]),
            ShippingAddress.location_key == location_key,
        )
        address_row = (await session.execute(stmt)).scalar_one_or_none()
        values = {
            "firebird_machine_id": order.get("machine_id"),
            "location_source": location_context["source"],
            "location_text_snapshot": location_context["current_text"],
            "location_fingerprint": location_context["fingerprint"],
            "company_name": address_data["company_name"],
            "contact_name": address_data.get("contact_name"),
            "street": address_data["street"],
            "postal_code": address_data["postal_code"],
            "city": address_data["city"],
            "country_code": "PL",
            "phone": address_data["phone"],
            "email": address_data.get("email"),
            "source": address_data["source"],
            "verified_by": user_id,
            "verified_at": now,
            "updated_at": now,
        }
        if address_row is None:
            address_row = ShippingAddress(
                firebird_client_id=int(order["client_id"]),
                location_key=location_key,
                **values,
            )
            session.add(address_row)
            await session.flush()
        else:
            for key, value in values.items():
                setattr(address_row, key, value)

    if case is None:
        case = ShippingCase(
            firebird_order_table_id=int(order["order_table_id"]),
            firebird_order_id=int(order["order_id"]),
            firebird_order_year=int(order["order_year"]),
            firebird_client_id=int(order["client_id"]),
            firebird_machine_id=order.get("machine_id"),
            firebird_model_id=order.get("model_id"),
            order_kind=order.get("order_kind"),
            invoice_required=invoice_required,
            status="review_pending",
            address_snapshot={},
            source_snapshot=order,
            weight_kg=payload.weight_kg,
            created_at=now,
            updated_at=now,
            items=[],
        )
        session.add(case)
        await session.flush()

    stock = await build_stock_payload(
        session,
        model_id=order.get("model_id"),
        exclude_case_id=case.id,
        limit=5000,
    )
    stock_by_id = {int(item["warehouse_item_id"]): item for item in stock}
    seen: set[int] = set()
    selected: list[tuple[Any, dict[str, Any], bool, Decimal, Decimal, Decimal, str]] = []
    for requested in payload.items:
        item_id = int(requested.firebird_warehouse_item_id)
        if item_id in seen:
            raise ValueError("Ta sama kartoteka została wybrana więcej niż raz.")
        seen.add(item_id)
        warehouse = stock_by_id.get(item_id)
        if warehouse is None:
            raise ValueError(f"Pozycja {item_id} nie jest fizyczną częścią ani towarem magazynu.")
        physical_available = Decimal(str(warehouse.get("available_quantity") or 0))
        available_after_reservations = Decimal(str(warehouse["available_after_soft_reservations"]))
        negative_stock_allowed = physical_available <= 0 and requested.allow_negative_stock
        if available_after_reservations < requested.quantity and not negative_stock_allowed:
            if physical_available <= 0:
                raise ShippingConflictError(
                    f"Pozycja {warehouse['item_name']} ma stan zerowy. "
                    "Zaznacz jawne zezwolenie na ujemny stan."
                )
            raise ShippingConflictError(f"Brak dostępnego stanu: {warehouse['item_name']}.")
        selected_price, catalog_price, purchase_price, price_source = _shipping_item_price(
            order_kind=order.get("order_kind"),
            invoice_required=invoice_required,
            requested_price=requested.unit_price_net,
            warehouse=warehouse,
        )
        selected.append(
            (
                requested,
                warehouse,
                negative_stock_allowed,
                selected_price,
                catalog_price,
                purchase_price,
                price_source,
            )
        )

    case.items.clear()
    await session.flush()
    case.address_id = address_row.id if address_row else None
    case.invoice_required = invoice_required
    case.address_snapshot = address_data
    case.source_snapshot = order
    case.location_source = location_context["source"]
    case.location_text_snapshot = location_context["current_text"]
    case.location_fingerprint = location_context["fingerprint"]
    case.weight_kg = payload.weight_kg
    case.label_text = payload.label_text
    case.reviewed_by = user_id
    case.reviewed_at = now
    case.updated_at = now
    case.status = "ready"
    for (
        requested,
        warehouse,
        negative_stock_allowed,
        selected_price,
        catalog_price,
        purchase_price,
        price_source,
    ) in selected:
        case.items.append(
            ShippingItem(
                shipping_case_id=case.id,
                firebird_warehouse_item_id=int(warehouse["warehouse_item_id"]),
                warehouse_id=int(warehouse["warehouse_id"]),
                item_index=warehouse.get("item_index"),
                item_name=warehouse["item_name"],
                unit=warehouse.get("unit") or "szt.",
                quantity=requested.quantity,
                price_net=selected_price,
                catalog_price_net=catalog_price,
                purchase_price_net=purchase_price,
                price_source=price_source,
                vat_rate=Decimal(str(warehouse.get("vat_rate") or 23)),
                allow_negative_stock=negative_stock_allowed,
                created_at=now,
            )
        )
        if requested.remember_for_model and order.get("model_id"):
            stmt = select(ShippingConsumableCompatibility).where(
                ShippingConsumableCompatibility.firebird_model_id == int(order["model_id"]),
                ShippingConsumableCompatibility.firebird_warehouse_item_id
                == int(warehouse["warehouse_item_id"]),
            )
            mapping = (await session.execute(stmt)).scalar_one_or_none()
            if mapping is None:
                session.add(
                    ShippingConsumableCompatibility(
                        firebird_model_id=int(order["model_id"]),
                        firebird_warehouse_item_id=int(warehouse["warehouse_item_id"]),
                        model_label=" ".join(
                            value
                            for value in (
                                order.get("device_brand") or order.get("machine_brand"),
                                order.get("device_model") or order.get("machine_model"),
                            )
                            if value
                        ),
                        item_index=warehouse.get("item_index"),
                        item_name=warehouse["item_name"],
                        item_kind=warehouse.get("item_kind"),
                        status="confirmed",
                        confidence="high",
                        evidence=[
                            {
                                "source": "manual",
                                "label": "Zgodność potwierdzona podczas przygotowania wysyłki.",
                            }
                        ],
                        first_seen_at=now,
                        last_seen_at=now,
                        reviewed_by=user_id,
                        reviewed_at=now,
                        confirmed_by=user_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                mapping.model_label = " ".join(
                    value
                    for value in (
                        order.get("device_brand") or order.get("machine_brand"),
                        order.get("device_model") or order.get("machine_model"),
                    )
                    if value
                )
                mapping.item_index = warehouse.get("item_index")
                mapping.item_name = warehouse["item_name"]
                mapping.item_kind = warehouse.get("item_kind")
                mapping.status = "confirmed"
                mapping.confidence = "high"
                mapping.evidence = [
                    {
                        "source": "manual",
                        "label": "Zgodność potwierdzona podczas przygotowania wysyłki.",
                    }
                ]
                mapping.last_seen_at = now
                mapping.reviewed_by = user_id
                mapping.reviewed_at = now
                mapping.confirmed_by = user_id
                mapping.updated_at = now
    if case.label_text is None:
        case.label_text = _shipping_case_label_text(case)
    session.add(
        ShippingEvent(
            shipping_case_id=case.id,
            event_type="review_accepted",
            payload={
                "item_count": len(selected),
                "address_source": address_data["source"],
                "invoice_required": invoice_required,
                "label_text": case.label_text,
                "negative_stock_item_ids": [
                    int(warehouse["warehouse_item_id"])
                    for (
                        _requested,
                        warehouse,
                        negative_stock_allowed,
                        _selected_price,
                        _catalog_price,
                        _purchase_price,
                        _price_source,
                    ) in selected
                    if negative_stock_allowed
                ],
            },
            created_by=user_id,
            created_at=now,
        )
    )
    await session.commit()
    refreshed = await get_shipping_case(session, int(order["order_table_id"]))
    assert refreshed is not None
    return _serialize_case(refreshed)


def _shipping_case_label_items(
    case: ShippingCase,
    *,
    include_order_number: bool = False,
) -> list[dict[str, Any]]:
    """Buduje listę części do etykiety i dokumentu kompletacyjnego."""
    order_number = f"{case.firebird_order_id}/{case.firebird_order_year}"
    return [
        {
            "order_number": order_number if include_order_number else None,
            "item_index": item.item_index,
            "item_name": item.item_name,
            "quantity": float(item.quantity),
            "unit": item.unit,
        }
        for item in case.items
    ]


def _shipping_case_firebird_items(case: ShippingCase) -> list[dict[str, Any]]:
    """Buduje zaakceptowane pozycje jednego zlecenia do zapisu w Firebirdzie."""
    return [
        {
            "firebird_warehouse_item_id": item.firebird_warehouse_item_id,
            "quantity": float(item.quantity),
            "price_net": float(item.price_net),
            "catalog_price_net": float(item.catalog_price_net),
            "purchase_price_net": float(item.purchase_price_net),
            "price_source": item.price_source,
            "vat_rate": float(item.vat_rate),
            "allow_negative_stock": item.allow_negative_stock,
        }
        for item in case.items
    ]


async def create_shipping_shipment(
    session: AsyncSession,
    *,
    order: dict[str, Any],
    order_table_id: int,
    idempotency_key: str,
    user_id: int,
    manual_tracking: str | None = None,
) -> dict[str, Any]:
    """Po kontroli lokalizacji tworzy etykietę DPD i aktualizuje Firebird."""
    existing_stmt = select(ShippingShipment).where(
        ShippingShipment.idempotency_key == idempotency_key
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing:
        case = await get_shipping_case(session, order_table_id)
        if case is None or case.id != existing.shipping_case_id:
            raise ShippingConflictError("Klucz idempotencji został użyty dla innego zlecenia.")
        return _serialize_case(case)

    case = await get_shipping_case(session, order_table_id)
    if case is None:
        raise ShippingConflictError("Zlecenie wymaga wcześniejszej akceptacji danych wysyłki.")
    if case.shipment is not None:
        raise ShippingConflictError("Dla zlecenia istnieje już przesyłka.")
    if await invalidate_shipping_case_for_location_change(
        session,
        case=case,
        order=order,
        user_id=user_id,
    ):
        raise ShippingLocationChangedError(
            "Lokalizacja urządzenia zmieniła się w MS. Adres został zablokowany do ponownej weryfikacji."
        )
    if case.status != "ready":
        raise ShippingConflictError("Zlecenie wymaga wcześniejszej akceptacji danych wysyłki.")
    case.label_text = _shipping_case_label_text(case)
    order_state = shipping_order_state_payload(order)
    if not order_state["can_prepare_shipment"]:
        raise ShippingConflictError(
            shipping_order_state_conflict_message(
                order_state,
                operation="generowanie etykiety",
            )
        )
    dpd = DpdShippingClient()
    mode = "manual" if manual_tracking else dpd.mode
    test_firebird_writes = (
        not manual_tracking
        and dpd.is_nonproduction
        and settings.shipping_test_firebird_writes_active
    )
    if mode != "mock" or test_firebird_writes:
        enabled, reason = firebird_writes_enabled()
        if not enabled:
            raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")
    now = _now()
    shipment = ShippingShipment(
        shipping_case=case,
        idempotency_key=idempotency_key,
        provider_mode=mode,
        status="processing",
        provider_request={},
        firebird_status="pending",
        created_by=user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(shipment)
    await session.flush()
    session.add(
        ShippingEvent(
            shipping_case_id=case.id,
            shipment_id=shipment.id,
            event_type="shipment_started",
            payload={
                "provider_mode": mode,
                "test_firebird_writes": test_firebird_writes,
            },
            created_by=user_id,
            created_at=now,
        )
    )
    await session.commit()

    try:
        if manual_tracking:
            shipment.provider_shipment_id = manual_tracking.strip()
            shipment.tracking_number = manual_tracking.strip()
            shipment.provider_request = {"mode": "manual"}
            shipment.provider_response = {"mode": "manual"}
        else:
            request_payload, result = await asyncio.to_thread(
                dpd.create_shipment,
                idempotency_key=idempotency_key,
                reference=f"MS-{case.firebird_order_year}-{case.firebird_order_id}",
                receiver=case.address_snapshot,
                weight_kg=float(case.weight_kg),
                items=_shipping_case_label_items(case),
                business_references=[f"{case.firebird_order_id}/{case.firebird_order_year}"],
                label_text=case.label_text,
            )
            if test_firebird_writes:
                request_payload["ctip_test_firebird_writes"] = True
            shipment.provider_request = request_payload
            shipment.provider_response = result.raw_response
            shipment.provider_shipment_id = result.shipment_id
            shipment.tracking_number = result.tracking_number
            shipment.label_content = result.label_content
            shipment.label_content_type = result.label_content_type
            shipment.label_format = result.label_format
        shipment.status = "label_ready"
        shipment.updated_at = _now()
        await session.commit()
    except Exception as exc:
        if isinstance(exc, DpdTransportError) and exc.request_payload:
            shipment.provider_request = exc.request_payload
        shipment.status = "failed"
        shipment.error_text = str(exc)
        shipment.updated_at = _now()
        await session.commit()
        raise

    if mode == "mock" and not test_firebird_writes:
        shipment.firebird_status = "simulated"
    else:
        try:
            result = await asyncio.to_thread(
                write_shipment_to_order,
                order_table_id=case.firebird_order_table_id,
                tracking_number=str(shipment.tracking_number),
                items=_shipping_case_firebird_items(case),
                shipping_address=case.address_snapshot,
                tracking_source=(
                    "manual" if manual_tracking else ("mock" if mode == "mock" else "dpd")
                ),
                generated_at=shipment.created_at,
            )
            shipment.firebird_status = result["status"]
            shipment.firebird_label_metadata_synced_at = _now()
            positions = result.get("created_position_ids", [])
            for item, position_id in zip(case.items, positions, strict=False):
                item.firebird_position_id = position_id
        except ShippingOrderStateConflict as exc:
            shipment.firebird_status = "reconcile_required"
            shipment.firebird_error = str(exc)
            shipment.status = "reconcile_required"
            case.status = "reconcile_required"
            shipment.updated_at = _now()
            session.add(
                ShippingEvent(
                    shipping_case_id=case.id,
                    shipment_id=shipment.id,
                    event_type="external_order_state_conflict",
                    payload={"stage": "shipment_write", "error": str(exc)},
                    created_by=user_id,
                    created_at=_now(),
                )
            )
            await session.commit()
            raise ShippingConflictError(str(exc)) from exc
        except Exception as exc:
            shipment.firebird_status = "reconcile_required"
            shipment.firebird_error = str(exc)
            shipment.status = "reconcile_required"
            case.status = "reconcile_required"
            shipment.updated_at = _now()
            await session.commit()
            return _serialize_case(case)

    case.status = "shipment_created"
    case.updated_at = _now()
    shipment.updated_at = _now()
    session.add(
        ShippingEvent(
            shipping_case_id=case.id,
            shipment_id=shipment.id,
            event_type="shipment_created",
            payload={
                "tracking_number": shipment.tracking_number,
                "firebird_status": shipment.firebird_status,
                "previous_order_status": (
                    result.get("previous_order_status")
                    if mode != "mock" or test_firebird_writes
                    else None
                ),
            },
            created_by=user_id,
            created_at=_now(),
        )
    )
    await session.commit()
    refreshed = await get_shipping_case(session, order_table_id)
    assert refreshed is not None
    return _serialize_case(refreshed)


async def _shipping_cases_for_order_ids(
    session: AsyncSession,
    order_table_ids: list[int],
) -> list[ShippingCase]:
    """Pobiera sprawy w kolejności wskazanej przez operatora."""
    rows = list(
        (
            await session.execute(
                select(ShippingCase)
                .options(
                    selectinload(ShippingCase.items),
                    selectinload(ShippingCase.shipment),
                )
                .where(ShippingCase.firebird_order_table_id.in_(order_table_ids))
            )
        )
        .scalars()
        .all()
    )
    by_order_id = {case.firebird_order_table_id: case for case in rows}
    return [by_order_id[order_id] for order_id in order_table_ids if order_id in by_order_id]


def _serialize_consolidated_shipment(cases: list[ShippingCase]) -> dict[str, Any]:
    """Buduje odpowiedź API jednej paczki powiązanej z wieloma zleceniami."""
    primary = cases[0]
    shipment = primary.shipment
    consolidation = shipping_shipment_consolidation(shipment) or {}
    return {
        "status": primary.status,
        "consolidated": True,
        "group_id": consolidation.get("group_id"),
        "tracking_number": shipment.tracking_number if shipment else None,
        "order_table_ids": [case.firebird_order_table_id for case in cases],
        "order_numbers": [f"{case.firebird_order_id}/{case.firebird_order_year}" for case in cases],
        "printable_order_ids": [case.firebird_order_table_id for case in cases],
        "cases": [_serialize_case(case) for case in cases],
        "primary_case": _serialize_case(primary),
    }


async def create_consolidated_shipping_shipment(
    session: AsyncSession,
    *,
    orders: list[dict[str, Any]],
    order_table_ids: list[int],
    idempotency_key: str,
    user_id: int,
    label_text: str | None = None,
) -> dict[str, Any]:
    """Tworzy jedną paczkę DPD i przypisuje ją do kilku zgodnych zleceń."""
    existing = (
        await session.execute(
            select(ShippingShipment).where(ShippingShipment.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()
    if existing is not None:
        consolidation = shipping_shipment_consolidation(existing)
        if consolidation is None:
            raise ShippingConflictError(
                "Klucz idempotencji został wcześniej użyty dla innej przesyłki."
            )
        existing_cases = await _shipping_cases_for_order_ids(
            session,
            consolidation["order_table_ids"],
        )
        return _serialize_consolidated_shipment(existing_cases)

    cases = await _shipping_cases_for_order_ids(session, order_table_ids)
    if len(cases) != len(order_table_ids):
        raise ShippingConflictError(
            "Nie wszystkie wybrane zlecenia zostały wcześniej zatwierdzone."
        )
    orders_by_id = {int(order["order_table_id"]): order for order in orders}
    if set(orders_by_id) != set(order_table_ids):
        raise ShippingConflictError("Nie udało się odczytać wszystkich wybranych zleceń z MS.")

    signatures = {_shipping_consolidation_signature(case.address_snapshot or {}) for case in cases}
    if len(signatures) != 1 or not all(next(iter(signatures))):
        raise ShippingConflictError(
            "Wspólna paczka wymaga identycznej firmy, ulicy, kodu pocztowego i miejscowości."
        )
    for case in cases:
        if case.status != "ready" or case.shipment is not None:
            raise ShippingConflictError(
                f"Zlecenie {case.firebird_order_id}/{case.firebird_order_year} "
                "nie jest gotowe do wspólnej etykiety."
            )
        order = orders_by_id[case.firebird_order_table_id]
        order_state = shipping_order_state_payload(order)
        if not order_state["can_prepare_shipment"]:
            raise ShippingConflictError(
                shipping_order_state_conflict_message(
                    order_state,
                    operation="generowanie wspólnej etykiety",
                )
            )
        if await invalidate_shipping_case_for_location_change(
            session,
            case=case,
            order=order,
            user_id=user_id,
        ):
            raise ShippingLocationChangedError(
                "Lokalizacja jednego ze zleceń zmieniła się w MS. "
                "Odśwież dane i ponownie zatwierdź adres."
            )

    total_weight = sum((case.weight_kg for case in cases), Decimal("0"))
    if total_weight > Decimal("31.5"):
        raise ShippingConflictError("Łączna waga wspólnej paczki przekracza limit 31,5 kg.")
    order_numbers = [f"{case.firebird_order_id}/{case.firebird_order_year}" for case in cases]
    signature = next(iter(signatures))
    group_source = "|".join((*signature, *(str(value) for value in order_table_ids)))
    group_id = hashlib.sha256(group_source.encode("utf-8")).hexdigest()[:20]
    consolidation = {
        "group_id": group_id,
        "order_table_ids": order_table_ids,
        "order_numbers": order_numbers,
        "primary_order_table_id": order_table_ids[0],
    }

    dpd = DpdShippingClient()
    mode = dpd.mode
    test_firebird_writes = dpd.is_nonproduction and settings.shipping_test_firebird_writes_active
    if mode != "mock" or test_firebird_writes:
        enabled, reason = firebird_writes_enabled()
        if not enabled:
            raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")

    now = _now()
    shipments: list[ShippingShipment] = []
    for index, case in enumerate(cases):
        shipment = ShippingShipment(
            shipping_case=case,
            idempotency_key=(idempotency_key if index == 0 else f"{idempotency_key}:{case.id}"),
            provider_mode=mode,
            status="processing",
            provider_request={"ctip_consolidation": consolidation},
            firebird_status="pending",
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        shipments.append(shipment)
        session.add(shipment)
        await session.flush()
        session.add(
            ShippingEvent(
                shipping_case_id=case.id,
                shipment_id=shipment.id,
                event_type="consolidated_shipment_started",
                payload={"group_id": group_id, "order_table_ids": order_table_ids},
                created_by=user_id,
                created_at=now,
            )
        )
    await session.commit()

    label_items = [
        item
        for case in cases
        for item in _shipping_case_label_items(case, include_order_number=True)
    ]
    effective_label_text = (
        normalize_dpd_label_text(label_text)
        if label_text
        else build_shipping_label_text(order_numbers=order_numbers, items=label_items)
    )
    for case in cases:
        case.label_text = effective_label_text
    try:
        request_payload, result = await asyncio.to_thread(
            dpd.create_shipment,
            idempotency_key=idempotency_key,
            reference=f"MS-GROUP-{group_id}",
            receiver=cases[0].address_snapshot,
            weight_kg=float(total_weight),
            items=label_items,
            business_references=order_numbers,
            label_text=effective_label_text,
        )
        request_payload["ctip_consolidation"] = consolidation
        if test_firebird_writes:
            request_payload["ctip_test_firebird_writes"] = True
        for shipment in shipments:
            shipment.provider_request = dict(request_payload)
            shipment.provider_response = result.raw_response
            shipment.provider_shipment_id = result.shipment_id
            shipment.tracking_number = result.tracking_number
            shipment.label_content = result.label_content
            shipment.label_content_type = result.label_content_type
            shipment.label_format = result.label_format
            shipment.status = "label_ready"
            shipment.updated_at = _now()
        await session.commit()
    except Exception as exc:
        for shipment in shipments:
            if isinstance(exc, DpdTransportError) and exc.request_payload:
                stored_payload = dict(exc.request_payload)
                stored_payload["ctip_consolidation"] = consolidation
                shipment.provider_request = stored_payload
            shipment.status = "failed"
            shipment.error_text = str(exc)
            shipment.updated_at = _now()
        await session.commit()
        raise

    write_errors: list[str] = []
    for case, shipment in zip(cases, shipments, strict=True):
        if mode == "mock" and not test_firebird_writes:
            shipment.firebird_status = "simulated"
            continue
        try:
            write_result = await asyncio.to_thread(
                write_shipment_to_order,
                order_table_id=case.firebird_order_table_id,
                tracking_number=str(shipment.tracking_number),
                items=_shipping_case_firebird_items(case),
                shipping_address=case.address_snapshot,
                tracking_source="mock" if mode == "mock" else "dpd",
                generated_at=shipment.created_at,
            )
            shipment.firebird_status = write_result["status"]
            shipment.firebird_label_metadata_synced_at = _now()
            positions = write_result.get("created_position_ids", [])
            for item, position_id in zip(case.items, positions, strict=False):
                item.firebird_position_id = position_id
        except Exception as exc:
            write_errors.append(f"{case.firebird_order_id}/{case.firebird_order_year}: {exc}")

    if write_errors:
        error_text = (
            "Wspólna etykieta została utworzona, ale zapis MS wymaga uzgodnienia: "
            + "; ".join(write_errors)
        )
        for case, shipment in zip(cases, shipments, strict=True):
            case.status = "reconcile_required"
            shipment.status = "reconcile_required"
            shipment.firebird_status = "reconcile_required"
            shipment.firebird_error = error_text
            shipment.updated_at = _now()
            session.add(
                ShippingEvent(
                    shipping_case_id=case.id,
                    shipment_id=shipment.id,
                    event_type="consolidated_shipment_reconcile_required",
                    payload={"group_id": group_id, "errors": write_errors},
                    created_by=user_id,
                    created_at=_now(),
                )
            )
        await session.commit()
        raise ShippingConflictError(error_text)

    for case, shipment in zip(cases, shipments, strict=True):
        case.status = "shipment_created"
        case.updated_at = _now()
        shipment.updated_at = _now()
        session.add(
            ShippingEvent(
                shipping_case_id=case.id,
                shipment_id=shipment.id,
                event_type="consolidated_shipment_created",
                payload={
                    "group_id": group_id,
                    "tracking_number": shipment.tracking_number,
                    "order_table_ids": order_table_ids,
                    "firebird_status": shipment.firebird_status,
                },
                created_by=user_id,
                created_at=_now(),
            )
        )
    await session.commit()
    refreshed = await _shipping_cases_for_order_ids(session, order_table_ids)
    return _serialize_consolidated_shipment(refreshed)


async def attach_shipping_cases_to_existing_label(
    session: AsyncSession,
    *,
    orders: list[dict[str, Any]],
    primary_order_table_id: int,
    additional_order_table_ids: list[int],
    idempotency_key: str,
    confirm_weight_within_existing_label: bool,
    user_id: int,
) -> dict[str, Any]:
    """Dołącza gotowe zlecenia do istniejącego listu bez tworzenia nowej etykiety DPD."""
    if not confirm_weight_within_existing_label:
        raise ShippingConflictError(
            "Dołączenie wymaga potwierdzenia, że rzeczywista waga całej paczki "
            "nie przekracza wagi zapisanej na istniejącej etykiecie."
        )
    additional_ids = list(dict.fromkeys(int(value) for value in additional_order_table_ids))
    if primary_order_table_id in additional_ids:
        raise ShippingConflictError(
            "Zlecenie z istniejącą etykietą nie może być dołączane samo do siebie."
        )

    existing_request = (
        await session.execute(
            select(ShippingShipment).where(ShippingShipment.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()
    if existing_request is not None:
        consolidation = shipping_shipment_consolidation(existing_request)
        if consolidation is None:
            raise ShippingConflictError(
                "Poprzednia próba dołączenia wymaga uzgodnienia przed ponowieniem operacji."
            )
        existing_cases = await _shipping_cases_for_order_ids(
            session, consolidation["order_table_ids"]
        )
        return _serialize_consolidated_shipment(existing_cases)

    primary = await get_shipping_case(session, primary_order_table_id)
    if primary is None or primary.shipment is None:
        raise ShippingConflictError("Wybrane zlecenie nie ma istniejącej etykiety.")
    primary_shipment = primary.shipment
    if (
        primary.status != "shipment_created"
        or primary_shipment.status != "label_ready"
        or primary_shipment.handed_over_at is not None
        or not primary_shipment.tracking_number
        or not primary_shipment.label_content
    ):
        raise ShippingConflictError(
            "Dołączenie jest możliwe wyłącznie do gotowej etykiety przed przekazaniem kurierowi."
        )
    existing_label_text = _shipment_label_text(primary_shipment) or _shipping_case_label_text(
        primary
    )
    declared_weight = _shipping_declared_weight(primary_shipment)
    if declared_weight is None:
        raise ShippingConflictError(
            "Istniejąca etykieta nie zawiera odczytywalnej wagi paczki. "
            "Dołączenie zostało zablokowane."
        )

    saved_consolidation = shipping_shipment_consolidation(primary_shipment)
    existing_ids = list((saved_consolidation or {}).get("order_table_ids") or [])
    if not existing_ids:
        existing_ids = [primary_order_table_id]
    if len(set([*existing_ids, *additional_ids])) > 20:
        raise ShippingConflictError("Jedna wspólna paczka może obejmować maksymalnie 20 zleceń.")
    if primary_order_table_id not in existing_ids:
        raise ShippingConflictError(
            "Wybrane zlecenie nie jest głównym zleceniem istniejącej paczki."
        )
    existing_cases = await _shipping_cases_for_order_ids(session, existing_ids)
    if len(existing_cases) != len(existing_ids):
        raise ShippingConflictError("Nie udało się odczytać wszystkich zleceń istniejącej paczki.")
    tracking_number = str(primary_shipment.tracking_number)
    if any(
        case.shipment is None
        or case.shipment.status != "label_ready"
        or case.shipment.handed_over_at is not None
        or str(case.shipment.tracking_number or "") != tracking_number
        for case in existing_cases
    ):
        raise ShippingConflictError("Istniejąca wspólna paczka ma niespójny stan etykiet.")

    additional_cases = await _shipping_cases_for_order_ids(session, additional_ids)
    if len(additional_cases) != len(additional_ids):
        raise ShippingConflictError("Nie wszystkie zlecenia do dołączenia zostały zatwierdzone.")
    orders_by_id = {int(order["order_table_id"]): order for order in orders}
    requested_ids = {primary_order_table_id, *additional_ids}
    if set(orders_by_id) != requested_ids:
        raise ShippingConflictError("Nie udało się odczytać wybranych zleceń z MS.")

    signatures = {
        _shipping_consolidation_signature(case.address_snapshot or {})
        for case in [*existing_cases, *additional_cases]
    }
    if len(signatures) != 1 or not all(next(iter(signatures))):
        raise ShippingConflictError(
            "Dołączenie do etykiety wymaga identycznej firmy, ulicy, kodu i miejscowości."
        )
    primary_state = shipping_order_state_payload(orders_by_id[primary_order_table_id])
    if not primary_state["can_finalize"] or primary_state["tracking_number"] != tracking_number:
        raise ShippingConflictError(
            "Stan lub numer przesyłki głównego zlecenia różni się od istniejącej etykiety."
        )
    for case in additional_cases:
        if case.status != "ready" or case.shipment is not None:
            raise ShippingConflictError(
                f"Zlecenie {case.firebird_order_id}/{case.firebird_order_year} "
                "nie jest gotowe do dołączenia."
            )
        order = orders_by_id[case.firebird_order_table_id]
        order_state = shipping_order_state_payload(order)
        if not order_state["can_prepare_shipment"]:
            raise ShippingConflictError(
                shipping_order_state_conflict_message(
                    order_state, operation="dołączenie do istniejącej etykiety"
                )
            )
        if await invalidate_shipping_case_for_location_change(
            session, case=case, order=order, user_id=user_id
        ):
            raise ShippingLocationChangedError(
                "Lokalizacja jednego ze zleceń zmieniła się w MS. "
                "Odśwież dane i ponownie zatwierdź adres."
            )

    mock_firebird_writes = bool(
        primary_shipment.provider_mode == "mock"
        and primary_shipment.provider_request.get("ctip_test_firebird_writes") is True
    )
    if primary_shipment.provider_mode != "mock" or mock_firebird_writes:
        enabled, reason = firebird_writes_enabled()
        if not enabled:
            raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")

    now = _now()
    new_shipments: list[ShippingShipment] = []
    for index, case in enumerate(additional_cases):
        request = dict(primary_shipment.provider_request or {})
        request.pop("ctip_consolidation", None)
        request["ctip_attachment_pending"] = {
            "primary_order_table_id": primary_order_table_id,
            "tracking_number": tracking_number,
        }
        shipment = ShippingShipment(
            shipping_case=case,
            idempotency_key=(idempotency_key if index == 0 else f"{idempotency_key}:{case.id}"),
            provider=primary_shipment.provider,
            provider_mode=primary_shipment.provider_mode,
            provider_shipment_id=primary_shipment.provider_shipment_id,
            tracking_number=tracking_number,
            status="label_ready",
            label_content=primary_shipment.label_content,
            label_content_type=primary_shipment.label_content_type,
            label_format=primary_shipment.label_format,
            provider_request=request,
            provider_response=primary_shipment.provider_response,
            firebird_status="pending",
            created_by=user_id,
            created_at=now,
            updated_at=now,
        )
        new_shipments.append(shipment)
        session.add(shipment)
        await session.flush()
    await session.commit()

    write_errors: list[str] = []
    for case, shipment in zip(additional_cases, new_shipments, strict=True):
        if primary_shipment.provider_mode == "mock" and not mock_firebird_writes:
            shipment.firebird_status = "simulated"
            continue
        try:
            write_result = await asyncio.to_thread(
                write_shipment_to_order,
                order_table_id=case.firebird_order_table_id,
                tracking_number=tracking_number,
                items=_shipping_case_firebird_items(case),
                shipping_address=case.address_snapshot,
                tracking_source="existing",
                generated_at=shipment.created_at,
            )
            shipment.firebird_status = write_result["status"]
            shipment.firebird_label_metadata_synced_at = _now()
            positions = write_result.get("created_position_ids", [])
            for item, position_id in zip(case.items, positions, strict=False):
                item.firebird_position_id = position_id
        except Exception as exc:
            write_errors.append(f"{case.firebird_order_id}/{case.firebird_order_year}: {exc}")

    if write_errors:
        error_text = (
            "Nie udało się bezpiecznie dołączyć wszystkich zleceń do istniejącej etykiety: "
            + "; ".join(write_errors)
        )
        for case, shipment in zip(additional_cases, new_shipments, strict=True):
            case.status = "reconcile_required"
            shipment.status = "reconcile_required"
            shipment.firebird_status = "reconcile_required"
            shipment.firebird_error = error_text
            shipment.updated_at = _now()
            session.add(
                ShippingEvent(
                    shipping_case_id=case.id,
                    shipment_id=shipment.id,
                    event_type="existing_label_attachment_reconcile_required",
                    payload={"tracking_number": tracking_number, "errors": write_errors},
                    created_by=user_id,
                    created_at=_now(),
                )
            )
        await session.commit()
        raise ShippingConflictError(error_text)

    all_cases = [*existing_cases, *additional_cases]
    all_order_ids = [case.firebird_order_table_id for case in all_cases]
    order_numbers = [f"{case.firebird_order_id}/{case.firebird_order_year}" for case in all_cases]
    signature = next(iter(signatures))
    group_id = (
        _text((saved_consolidation or {}).get("group_id"))
        or hashlib.sha256(
            "|".join(
                (*signature, tracking_number, *(str(value) for value in all_order_ids))
            ).encode("utf-8")
        ).hexdigest()[:20]
    )
    consolidation = {
        "group_id": group_id,
        "order_table_ids": all_order_ids,
        "order_numbers": order_numbers,
        "primary_order_table_id": int(
            (saved_consolidation or {}).get("primary_order_table_id") or primary_order_table_id
        ),
    }
    for case in all_cases:
        shipment = case.shipment
        assert shipment is not None
        request = dict(shipment.provider_request or {})
        request.pop("ctip_attachment_pending", None)
        request["ctip_consolidation"] = consolidation
        shipment.provider_request = request
        shipment.updated_at = _now()
        case.label_text = existing_label_text
        case.status = "shipment_created"
        case.updated_at = _now()
        session.add(
            ShippingEvent(
                shipping_case_id=case.id,
                shipment_id=shipment.id,
                event_type="consolidated_shipment_attached_existing_label",
                payload={
                    "group_id": group_id,
                    "tracking_number": tracking_number,
                    "order_table_ids": all_order_ids,
                    "primary_order_table_id": consolidation["primary_order_table_id"],
                },
                created_by=user_id,
                created_at=_now(),
            )
        )
    await session.commit()
    refreshed = await _shipping_cases_for_order_ids(session, all_order_ids)
    return _serialize_consolidated_shipment(refreshed)


async def _send_notifications(shipment: ShippingShipment, case: ShippingCase) -> list[str]:
    errors: list[str] = []
    address = case.address_snapshot
    if shipment.provider_mode == "mock":
        shipment.notification_sms_status = "simulated"
        shipment.notification_email_status = "simulated" if address.get("email") else "skipped"
        return errors
    text = (
        f"Przesyłka DPD {shipment.tracking_number} została przekazana kurierowi. "
        "Status można sprawdzić na stronie DPD."
    )
    provider = HttpSmsProvider(
        base_url=settings.sms_api_url,
        token=settings.sms_api_token,
        sender=settings.sms_default_sender,
        username=settings.sms_api_username,
        password=settings.sms_api_password,
        sms_type=settings.sms_type,
        test_mode=settings.sms_test_mode,
        delivery_mode=settings.outbound_delivery_mode,
    )
    try:
        result = await asyncio.to_thread(
            provider.send_sms,
            str(address["phone"]),
            text,
            metadata={"source": "shipping_handover", "shipment_id": shipment.id},
        )
        shipment.notification_sms_status = "sent" if result.success else "failed"
        if not result.success:
            errors.append(result.error or "Nie udało się wysłać SMS.")
    except Exception as exc:
        shipment.notification_sms_status = "failed"
        errors.append(str(exc))

    recipient = address.get("email")
    if not recipient:
        shipment.notification_email_status = "skipped"
        return errors
    message = EmailMessage()
    sender_address = settings.email_sender_address or settings.email_username
    if not sender_address:
        shipment.notification_email_status = "failed"
        errors.append("Brak adresu nadawcy SMTP.")
        return errors
    message["From"] = formataddr((settings.email_sender_name or "Ksero-Partner", sender_address))
    message["To"] = recipient
    message["Subject"] = f"Przesyłka DPD {shipment.tracking_number}"
    message.set_content(text)
    result = await send_smtp_message(
        host=settings.email_host or "",
        port=settings.email_port,
        username=settings.email_username,
        password=settings.email_password,
        use_tls=settings.email_use_tls,
        use_ssl=settings.email_use_ssl,
        message=message,
        source="shipping_handover",
    )
    shipment.notification_email_status = "sent" if result.success else "failed"
    if not result.success:
        errors.append(result.message or "Nie udało się wysłać e-maila.")
    return errors


async def _shipping_operator_name(session: AsyncSession, user_id: int) -> str:
    """Zwraca nazwę operatora zapisywaną w dokumentach Menadżera Serwisu."""
    operator = await session.get(AdminUser, user_id)
    operator_name = (
        " ".join(value for value in (operator.first_name, operator.last_name) if value)
        if operator
        else ""
    )
    if not operator_name and operator:
        operator_name = operator.email
    return operator_name or "CTIP"


def _shipping_document_snapshot(shipment: ShippingShipment) -> dict[str, Any]:
    """Zwraca zapisane identyfikatory dokumentów Firebird dla odpowiedzi API."""
    return {
        "rw_id": shipment.firebird_rw_id,
        "rw_number": shipment.firebird_rw_number,
        "wz_id": shipment.firebird_wz_id,
        "wz_number": shipment.firebird_wz_number,
        "invoice_id": shipment.firebird_invoice_id,
        "invoice_number": shipment.firebird_invoice_number,
    }


async def _finalize_shipping_shipment(
    session: AsyncSession,
    *,
    shipment: ShippingShipment,
    user_id: int,
    operator_name: str,
    day_close_id: int | None = None,
    send_notifications: bool = True,
) -> dict[str, Any]:
    """Finalizuje przesyłkę i opcjonalnie wysyła jedno powiadomienie dla fizycznej paczki."""
    if shipment.status not in {"label_ready", "handed_over"}:
        raise ShippingConflictError(
            "Wybrane zlecenie nie ma gotowej etykiety oczekującej na odbiór kuriera."
        )
    case = shipment.shipping_case
    if not shipment.tracking_number:
        raise ShippingConflictError("Wybrana przesyłka nie ma numeru listu przewozowego.")
    mock_firebird_writes = bool(
        shipment.provider_mode == "mock"
        and shipment.provider_request.get("ctip_test_firebird_writes") is True
    )
    if shipment.provider_mode != "mock" or mock_firebird_writes:
        order_state = await asyncio.to_thread(
            load_shipping_order_state,
            case.firebird_order_table_id,
        )
        tracking_matches = order_state["tracking_number"] == _text(shipment.tracking_number)
        if not order_state["can_finalize"] or not tracking_matches:
            if tracking_matches:
                conflict_message = shipping_order_state_conflict_message(
                    order_state,
                    operation="potwierdzenie wysyłki i zamknięcie zlecenia",
                )
            else:
                conflict_message = (
                    "Numer przesyłki w MS różni się od numeru etykiety zapisanej w CTIP. "
                    "Potwierdzenie wysyłki zostało zablokowane do ręcznego uzgodnienia."
                )
            shipment.status = "reconcile_required"
            shipment.firebird_status = "reconcile_required"
            shipment.firebird_error = conflict_message
            case.status = "reconcile_required"
            session.add(
                ShippingEvent(
                    shipping_case_id=case.id,
                    shipment_id=shipment.id,
                    event_type="external_order_state_conflict",
                    payload={"stage": "finalization", "order_state": order_state},
                    created_by=user_id,
                    created_at=_now(),
                )
            )
            await session.flush()
            raise ShippingConflictError(conflict_message)
    now = _now()
    if day_close_id is not None:
        shipment.day_close_id = day_close_id
    if shipment.handed_over_at is None:
        shipment.handed_over_at = now
    shipment.status = "handed_over"
    case.status = "handed_over"
    try:
        document_mode = shipping_document_mode(
            order_kind=case.order_kind,
            invoice_required=case.invoice_required,
        )
        if shipment.provider_mode == "mock" and not mock_firebird_writes:
            document_result = {
                "status": "simulated",
                "document_mode": document_mode,
                "rw_id": None,
                "rw_number": None,
                "wz_id": None,
                "wz_number": None,
                "invoice_id": None,
                "invoice_number": None,
            }
        else:
            document_result = await asyncio.to_thread(
                finalize_shipping_order,
                order_table_id=case.firebird_order_table_id,
                warehouse_id=settings.shipping_warehouse_id,
                items=[
                    {
                        "firebird_warehouse_item_id": item.firebird_warehouse_item_id,
                        "quantity": float(item.quantity),
                        "price_net": float(item.price_net),
                        "catalog_price_net": float(item.catalog_price_net),
                        "purchase_price_net": float(item.purchase_price_net),
                        "price_source": item.price_source,
                        "vat_rate": float(item.vat_rate),
                        "allow_negative_stock": item.allow_negative_stock,
                    }
                    for item in case.items
                ],
                invoice_required=case.invoice_required,
                tracking_number=str(shipment.tracking_number),
                issued_by=operator_name,
                shipping_address=case.address_snapshot,
            )
        document_result.setdefault("document_mode", document_mode)
        shipment.firebird_rw_id = document_result.get("rw_id")
        shipment.firebird_rw_number = document_result.get("rw_number")
        shipment.firebird_wz_id = document_result.get("wz_id")
        shipment.firebird_wz_number = document_result.get("wz_number")
        shipment.firebird_invoice_id = document_result.get("invoice_id")
        shipment.firebird_invoice_number = document_result.get("invoice_number")
        case.status = "closed"
        shipment.status = "closed"
        shipment.closed_at = now
        await archive_shipping_shipment(
            session,
            shipment=shipment,
            closed_by=user_id,
            closed_at=now,
            closing_operator_name=operator_name,
        )
        if send_notifications:
            notification_errors = await _send_notifications(shipment, case)
            notification_skip_reason = None
        else:
            _skip_consolidated_notifications(shipment)
            notification_errors = []
            notification_skip_reason = "consolidated_duplicate"
        if notification_errors:
            shipment.notification_error = "; ".join(notification_errors)
        session.add(
            ShippingEvent(
                shipping_case_id=case.id,
                shipment_id=shipment.id,
                event_type="courier_handover",
                payload={
                    "documents": document_result,
                    "notification_errors": notification_errors,
                    "notification_skip_reason": notification_skip_reason,
                    "scope": "day_close" if day_close_id is not None else "single_order",
                },
                created_by=user_id,
                created_at=now,
            )
        )
        return {
            "status": "closed",
            "documents": document_result,
            "notification_errors": notification_errors,
            "notification_skip_reason": notification_skip_reason,
        }
    except ShippingOrderStateConflict as exc:
        shipment.status = "reconcile_required"
        shipment.firebird_status = "reconcile_required"
        shipment.firebird_error = str(exc)
        case.status = "reconcile_required"
        raise ShippingConflictError(str(exc)) from exc
    except Exception as exc:
        shipment.status = "reconcile_required"
        shipment.firebird_status = "reconcile_required"
        shipment.firebird_error = str(exc)
        case.status = "reconcile_required"
        raise


async def close_shipping_order(
    session: AsyncSession,
    *,
    order_table_id: int,
    user_id: int,
) -> dict[str, Any]:
    """Kończy wybrane zlecenie albo wszystkie zlecenia jednej wspólnej paczki."""
    stmt = (
        select(ShippingCase, ShippingShipment)
        .join(ShippingShipment, ShippingShipment.shipping_case_id == ShippingCase.id)
        .options(selectinload(ShippingCase.items))
        .where(ShippingCase.firebird_order_table_id == int(order_table_id))
        .execution_options(populate_existing=True)
    )
    selected_row = (await session.execute(stmt)).one_or_none()
    if selected_row is None:
        raise ShippingConflictError(
            "Wybrane zlecenie nie ma przesyłki przygotowanej do zakończenia."
        )
    selected_case, shipment = selected_row
    _bind_shipping_case(selected_case, shipment)
    consolidation = shipping_shipment_consolidation(shipment)
    if consolidation:
        grouped_rows = (
            await session.execute(
                select(ShippingCase, ShippingShipment)
                .join(ShippingShipment, ShippingShipment.shipping_case_id == ShippingCase.id)
                .options(selectinload(ShippingCase.items))
                .where(ShippingCase.firebird_order_table_id.in_(consolidation["order_table_ids"]))
                .order_by(ShippingShipment.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        for grouped_case, grouped_shipment in grouped_rows:
            _bind_shipping_case(grouped_case, grouped_shipment)
        shipments_by_order_id = {
            grouped_case.firebird_order_table_id: grouped_shipment
            for grouped_case, grouped_shipment in grouped_rows
        }
        if len(shipments_by_order_id) != len(consolidation["order_table_ids"]):
            raise ShippingConflictError("Wspólna paczka nie ma kompletu powiązanych zleceń w CTIP.")
        cases_by_order_id = {
            grouped_case.firebird_order_table_id: grouped_case for grouped_case, _ in grouped_rows
        }
        cases = [cases_by_order_id[order_id] for order_id in consolidation["order_table_ids"]]
        shipments_by_case_id = {
            grouped_case.id: grouped_shipment for grouped_case, grouped_shipment in grouped_rows
        }
    else:
        selected_case, shipment = (
            await session.execute(
                select(ShippingCase, ShippingShipment)
                .join(ShippingShipment, ShippingShipment.shipping_case_id == ShippingCase.id)
                .options(selectinload(ShippingCase.items))
                .where(ShippingCase.id == selected_case.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).one()
        _bind_shipping_case(selected_case, shipment)
        cases = [selected_case]
        shipments_by_case_id = {selected_case.id: shipment}

    selected_case = next(
        case for case in cases if case.firebird_order_table_id == int(order_table_id)
    )

    operator_name = await _shipping_operator_name(session, user_id)
    order_results: list[dict[str, Any]] = []
    newly_closed_count = 0
    handled_notification_groups = {
        key
        for grouped_shipment in shipments_by_case_id.values()
        if (key := _shipping_notification_group_key(grouped_shipment))
        and _shipping_notification_attempted(grouped_shipment)
    }
    for case in cases:
        grouped_shipment = shipments_by_case_id[case.id]
        if grouped_shipment.status == "closed":
            result = {
                "status": "already_closed",
                "documents": _shipping_document_snapshot(grouped_shipment),
                "notification_errors": [],
            }
        else:
            notification_group = _shipping_notification_group_key(grouped_shipment)
            send_notifications = (
                notification_group is None or notification_group not in handled_notification_groups
            )
            try:
                result = await _finalize_shipping_shipment(
                    session,
                    shipment=grouped_shipment,
                    user_id=user_id,
                    operator_name=operator_name,
                    send_notifications=send_notifications,
                )
                if notification_group is not None and send_notifications:
                    handled_notification_groups.add(notification_group)
                newly_closed_count += 1
            except Exception:
                await session.commit()
                raise
        order_results.append(
            {
                "order_table_id": case.firebird_order_table_id,
                "order_number": f"{case.firebird_order_id}/{case.firebird_order_year}",
                **result,
            }
        )

    selected_result = next(
        result for result in order_results if result["order_table_id"] == int(order_table_id)
    )
    response = {
        "status": "already_closed" if newly_closed_count == 0 else "closed",
        "documents": selected_result["documents"],
        "notification_errors": selected_result["notification_errors"],
        "case": _serialize_case(selected_case),
        "consolidated": bool(consolidation),
        "closed_count": len(cases),
        "newly_closed_count": newly_closed_count,
        "idempotent_replay": newly_closed_count == 0,
        "order_results": order_results,
    }
    await session.commit()
    return response


async def close_shipping_day(
    session: AsyncSession,
    *,
    business_date: date,
    user_id: int,
) -> dict[str, Any]:
    """Oznacza odbiór, tworzy właściwe dokumenty MS i wysyła powiadomienia raz."""
    existing_stmt = select(ShippingDayClose).where(ShippingDayClose.business_date == business_date)
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing and existing.status in {"completed", "partial"}:
        return {
            "id": existing.id,
            "business_date": existing.business_date.isoformat(),
            "status": existing.status,
            "idempotent_replay": True,
            **existing.summary,
        }
    from zoneinfo import ZoneInfo

    local_start = datetime.combine(business_date, time.min, tzinfo=ZoneInfo("Europe/Warsaw"))
    start = local_start.astimezone(UTC)
    end = (local_start + timedelta(days=1)).astimezone(UTC)
    stmt = (
        select(ShippingShipment)
        .options(selectinload(ShippingShipment.shipping_case).selectinload(ShippingCase.items))
        .where(
            ShippingShipment.created_at >= start,
            ShippingShipment.created_at < end,
            ShippingShipment.status.in_(("label_ready", "handed_over")),
        )
        .order_by(ShippingShipment.id)
        .with_for_update()
    )
    shipments = list((await session.execute(stmt)).scalars().all())
    now = _now()
    day_close = existing or ShippingDayClose(
        business_date=business_date,
        status="processing",
        shipment_count=len(shipments),
        closed_count=0,
        error_count=0,
        summary={},
        closed_by=user_id,
        created_at=now,
    )
    if existing is None:
        session.add(day_close)
        await session.flush()
    errors: list[dict[str, Any]] = []
    closed_count = 0
    rw_count = 0
    wz_count = 0
    invoice_count = 0
    operator_name = await _shipping_operator_name(session, user_id)
    handled_notification_groups = {
        key
        for shipment in shipments
        if (key := _shipping_notification_group_key(shipment))
        and _shipping_notification_attempted(shipment)
    }
    for shipment in shipments:
        notification_group = _shipping_notification_group_key(shipment)
        send_notifications = (
            notification_group is None or notification_group not in handled_notification_groups
        )
        try:
            result = await _finalize_shipping_shipment(
                session,
                shipment=shipment,
                user_id=user_id,
                operator_name=operator_name,
                day_close_id=day_close.id,
                send_notifications=send_notifications,
            )
            if notification_group is not None and send_notifications:
                handled_notification_groups.add(notification_group)
            document_result = result["documents"]
            document_mode = document_result["document_mode"]
            if document_mode == "rw":
                rw_count += 1
            elif document_mode == "wz":
                wz_count += 1
            else:
                wz_count += 1
                invoice_count += 1
            closed_count += 1
            notification_errors = result["notification_errors"]
            if notification_errors:
                errors.append(
                    {
                        "shipment_id": shipment.id,
                        "stage": "notification",
                        "error": shipment.notification_error,
                    }
                )
        except Exception as exc:
            errors.append(
                {"shipment_id": shipment.id, "stage": "firebird_documents", "error": str(exc)}
            )

    day_close.shipment_count = len(shipments)
    day_close.closed_count = closed_count
    day_close.error_count = len(errors)
    day_close.status = "completed" if not errors else "partial"
    day_close.completed_at = now
    day_close.summary = {
        "shipment_count": len(shipments),
        "closed_count": closed_count,
        "manual_billing_count": 0,
        "rw_count": rw_count,
        "wz_count": wz_count,
        "invoice_count": invoice_count,
        "error_count": len(errors),
        "errors": errors,
    }
    await session.commit()
    return {
        "id": day_close.id,
        "business_date": business_date.isoformat(),
        "status": day_close.status,
        "idempotent_replay": False,
        **day_close.summary,
    }


__all__ = [
    "ShippingConflictError",
    "attach_shipping_cases_to_existing_label",
    "build_shipping_consolidation_groups",
    "build_shipping_existing_label_attachment_groups",
    "build_shipping_label_text",
    "build_stock_payload",
    "close_shipping_day",
    "close_shipping_order",
    "create_consolidated_shipping_shipment",
    "create_shipping_shipment",
    "get_shipping_case",
    "review_shipping_order",
    "serialize_shipping_case",
    "shipping_shipment_consolidation",
]
