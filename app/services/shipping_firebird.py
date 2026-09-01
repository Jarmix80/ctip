"""Odczyt i kontrolowany zapis procesu wysyłki w Menadżerze Serwisu."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from app.services.firebird_runtime import firebird_connection, firebird_writes_enabled

DELIVERY_TYPE_ID = 8
DELIVERY_TYPE_NAME = "dowóz materiałów"
SHIPPING_TECHNICIAN_NAME = "Wysyłka Wysyłka"
QUEUE_STATUSES = ("O", "ZR")
KSEF_NUMERIC_VAT_RATES = frozenset(
    Decimal(value) for value in ("23", "22", "8", "7", "5", "4", "3")
)
INVOICE_DOCUMENT_KIND = "KPSK"
OVERDUE_INVOICE_BATCH_SIZE = 200
WARSAW = ZoneInfo("Europe/Warsaw")
ORDER_STATUS_LABELS = {
    "O": "otwarte",
    "ZR": "przygotowane do realizacji",
    "Z": "zrealizowane",
}
ORDER_PHONE_PATTERN = re.compile(r"(?<!\d)(?:(?:\+|00)\s*48[\s.-]*)?(?:\d[\s.-]*){8}\d(?!\d)")
ORDER_PHONE_CUE_PATTERN = re.compile(
    r"(?:tel(?:efon)?|kom(?:órkowy|orkowy)?|kontakt)\s*[:.]?\s*$",
    flags=re.IGNORECASE,
)
ORDER_PHONE_BLOCK_CUE_PATTERN = re.compile(
    r"(?:indeks|symbol|kod(?:\s+towaru)?|nr\s*(?:kat(?:alogowy)?|części|czesci|towaru))"
    r"\s*[:.#-]?\s*$",
    flags=re.IGNORECASE,
)
SHIPPING_MILESTONE_FIELDS = (
    "DATA_PRZES",
    "WYKONANIE",
    "DATA_PRZES_WE",
    "PRZESYLKA_WE",
)
SHIPPING_MILESTONE_STATE_KEYS = {
    "DATA_PRZES": "pickup_date",
    "WYKONANIE": "execution_text",
    "DATA_PRZES_WE": "delivery_date",
    "PRZESYLKA_WE": "description_text",
}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _search_terms(value: Any) -> list[str]:
    """Dzieli zapytanie na unikalne wyrazy wyszukiwane niezależnie od kolejności."""
    terms: list[str] = []
    seen: set[str] = set()
    for term in re.findall(r"[\w]+", _text(value) or "", flags=re.UNICODE):
        normalized = term.casefold()
        if normalized not in seen:
            terms.append(term)
            seen.add(normalized)
    return terms


def _number(value: Any) -> float:
    return float(value or 0)


def _vat_rate(value: Any) -> Decimal:
    normalized = str(value or "0").replace("%", "").replace(",", ".").strip()
    try:
        return Decimal(normalized or "0")
    except InvalidOperation as exc:
        raise RuntimeError(f"Niepoprawna stawka VAT w kartotece magazynowej: {value!r}.") from exc


def _ms_vat_rate_text(value: Any) -> str:
    """Zapisuje liczbową stawkę VAT w kanonicznym formacie Menadżera Serwisu."""
    rate = _vat_rate(value)
    if rate < 0 or rate != rate.to_integral_value():
        raise RuntimeError(f"Nieobsługiwana ułamkowa stawka VAT dla dokumentu MS: {value!r}.")
    return f"{rate.quantize(Decimal('1')):f} %"


def _validate_ksef_numeric_vat_rate(value: Decimal) -> None:
    """Blokuje utworzenie FV dla stawki bez jednoznacznego mapowania FA(3)."""
    if value not in KSEF_NUMERIC_VAT_RATES:
        raise RuntimeError(
            "Stawka VAT nie ma bezpiecznego mapowania do KSeF FA(3). "
            "Wystaw fakturę ręcznie w Menadżerze Serwisu."
        )


def _money(value: Decimal) -> Decimal:
    """Zaokrągla wartość dokumentu do pełnych groszy."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _shipping_execution_note(*, shipped_on: date, tracking_number: str) -> str:
    """Buduje wpis wykonania wymagany dla wysyłki zlecenia MS."""
    return f"Wysłana paczka {shipped_on.strftime('%d.%m.%Y')} {tracking_number.strip()}"


def _shipping_label_note(
    *,
    generated_on: date,
    tracking_number: str,
    tracking_source: str,
) -> str:
    """Buduje wpis wykonania opisujący pochodzenie numeru przesyłki."""
    source_labels = {
        "manual": "numer wpisany ręcznie",
        "mock": "numer testowy CTIP",
        "existing": "dołączono do istniejącej etykiety DPD",
    }
    source = source_labels.get(tracking_source, "numer nadany przez API DPD")
    return (
        f"Utworzono przesyłkę DPD {generated_on.strftime('%d.%m.%Y')}, "
        f"nr: {tracking_number.strip()} ({source})."
    )


def _shipping_local_date(value: datetime | date | None) -> date:
    """Zwraca datę operacyjną w polskiej strefie czasowej."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(WARSAW).date()
    if isinstance(value, date):
        return value
    return datetime.now(WARSAW).date()


def _shipping_address_text(address: dict[str, Any] | None) -> str | None:
    """Składa odbiorcę i adres pocztowy do pola `ADRES_PRZES` MS."""
    if not address:
        return None
    company = _text(address.get("company_name"))
    contact = _text(address.get("contact_name"))
    if contact and company and contact.casefold() == company.casefold():
        contact = None
    postal_city = " ".join(
        value for value in (_text(address.get("postal_code")), _text(address.get("city"))) if value
    )
    result = " | ".join(
        value for value in (company, contact, _text(address.get("street")), postal_city) if value
    )
    return result[:500] or None


def _append_execution_note(existing: Any, note: str) -> str:
    """Dopisuje wpis wykonania bez dublowania ponowionej operacji."""
    current = _text(existing) or ""
    if note.casefold() in current.casefold():
        return current
    return f"{current}\n{note}".strip()[:2000]


def shipping_document_mode(*, order_kind: Any, invoice_required: bool) -> str:
    """Wybiera RW, WZ albo FV z WZ na podstawie rodzaju zlecenia i decyzji FV."""
    if invoice_required:
        return "invoice_wz"
    return "rw" if (_text(order_kind) or "").casefold() == "umowa" else "wz"


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _dict_row(cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    names = [str(item[0]).strip().lower() for item in cursor.description]
    return {name: _json_value(value) for name, value in zip(names, row, strict=True)}


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _date_from_payload(value: Any) -> date | None:
    """Odtwarza datę z raportu pilota bez akceptowania innych formatów."""
    if value is None or isinstance(value, date):
        return _date_value(value)
    normalized = str(value).strip()
    return date.fromisoformat(normalized) if normalized else None


def _load_shipping_milestone_state(
    cursor: Any,
    *,
    order_table_id: int,
    with_lock: bool,
) -> dict[str, Any]:
    lock_clause = " WITH LOCK" if with_lock else ""
    cursor.execute(
        f"""
        SELECT ID_ZLECENIE_TABLE, ID_ZLECENIE, ROK, STAN, PRZESYLKA,
               DATA_PRZES, WYKONANIE, DATA_PRZES_WE, PRZESYLKA_WE
        FROM ZLECENIE
        WHERE ID_ZLECENIE_TABLE = ? AND TYP_US = ?{lock_clause}
        """,
        (int(order_table_id), DELIVERY_TYPE_ID),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError("Nie znaleziono zlecenia do aktualizacji statusu przesyłki.")
    return {
        "order_table_id": int(row[0]),
        "order_id": int(row[1]),
        "order_year": int(row[2]),
        "status": (_text(row[3]) or "").upper(),
        "tracking_number": _text(row[4]),
        "pickup_date": _date_value(row[5]),
        "execution_text": _text(row[6]),
        "delivery_date": _date_value(row[7]),
        "description_text": _text(row[8]),
    }


def shipping_milestone_field_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """Zwraca cztery pola MS w formacie bezpiecznym dla raportu i audytu."""
    return {
        field: _json_value(state.get(state_key))
        for field, state_key in SHIPPING_MILESTONE_STATE_KEYS.items()
    }


def shipping_milestone_state_token(state: dict[str, Any]) -> str:
    """Wylicza token współbieżności dla pól przesyłki jednego zlecenia MS."""
    payload = {
        "order_table_id": int(state["order_table_id"]),
        "order_id": int(state["order_id"]),
        "order_year": int(state["order_year"]),
        "status": _text(state.get("status")),
        "tracking_number": _text(state.get("tracking_number")),
        "fields": shipping_milestone_field_snapshot(state),
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_shipping_milestone_state(order_table_id: int) -> dict[str, Any]:
    """Odczytuje pola przesyłki MS bez blokady i bez wykonywania zapisu."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        return _load_shipping_milestone_state(
            cursor,
            order_table_id=order_table_id,
            with_lock=False,
        )
    finally:
        cursor.close()
        connection.close()


def _shipping_milestone_updates(
    state: dict[str, Any],
    *,
    tracking_number: str,
    pickup_date: date | None,
    pickup_note: str | None,
    delivery_date: date | None,
    description_text: str | None,
    expected_description_text: str | None,
) -> dict[str, Any]:
    if _text(state.get("tracking_number")) != _text(tracking_number):
        raise ShippingOrderStateConflict("Numer przesyłki w MS różni się od numeru zdarzenia DPD.")

    updates: dict[str, Any] = {}
    if pickup_date is not None:
        current_pickup = _date_value(state.get("pickup_date"))
        if current_pickup not in {None, pickup_date}:
            raise ShippingOrderStateConflict(
                "Data nadania w MS została wcześniej ustawiona na inną wartość."
            )
        if current_pickup != pickup_date:
            updates["DATA_PRZES"] = pickup_date
        if pickup_note:
            execution = _append_execution_note(state.get("execution_text"), pickup_note)
            if execution != (_text(state.get("execution_text")) or ""):
                updates["WYKONANIE"] = execution
    if delivery_date is not None:
        current_delivery = _date_value(state.get("delivery_date"))
        if current_delivery not in {None, delivery_date}:
            raise ShippingOrderStateConflict(
                "Data odebrania przesyłki w MS została wcześniej ustawiona na inną wartość."
            )
        if current_delivery != delivery_date:
            updates["DATA_PRZES_WE"] = delivery_date
    if description_text is not None:
        normalized_description = description_text.strip()[:250]
        current_text = _text(state.get("description_text"))
        expected_text = _text(expected_description_text)
        allowed_current = {None, normalized_description}
        if expected_text:
            allowed_current.add(expected_text)
        if current_text not in allowed_current:
            raise ShippingOrderStateConflict(
                "Opis przesyłki odebranej w MS został zmieniony ręcznie."
            )
        if current_text != normalized_description:
            updates["PRZESYLKA_WE"] = normalized_description
    return updates


def preview_shipping_milestones_to_order(
    *,
    order_table_id: int,
    tracking_number: str,
    pickup_date: date | None = None,
    pickup_note: str | None = None,
    delivery_date: date | None = None,
    description_text: str | None = None,
    expected_description_text: str | None = None,
) -> dict[str, Any]:
    """Buduje podgląd zmian pól przesyłki bez blokady i bez zapisu."""
    state = load_shipping_milestone_state(order_table_id)
    updates = _shipping_milestone_updates(
        state,
        tracking_number=tracking_number,
        pickup_date=pickup_date,
        pickup_note=pickup_note,
        delivery_date=delivery_date,
        description_text=description_text,
        expected_description_text=expected_description_text,
    )
    after = shipping_milestone_field_snapshot(state)
    for field, value in updates.items():
        after[field] = _json_value(value)
    return {
        "state_token": shipping_milestone_state_token(state),
        "order": {
            "order_table_id": state["order_table_id"],
            "order_id": state["order_id"],
            "order_year": state["order_year"],
            "status": state["status"],
            "tracking_number": state["tracking_number"],
        },
        "changed_fields": list(updates),
        "before": shipping_milestone_field_snapshot(state),
        "after": after,
    }


def _empty_overdue_invoice_summary() -> dict[str, Any]:
    return {
        "has_overdue_invoices": False,
        "invoice_count": 0,
        "total_overdue_amount": 0.0,
        "oldest_due_date": None,
        "max_days_overdue": 0,
    }


def _phone_key(value: Any) -> str:
    digits = re.sub(r"\D", "", _text(value) or "")
    if len(digits) in {18, 22} and digits[: len(digits) // 2] == digits[len(digits) // 2 :]:
        digits = digits[: len(digits) // 2]
    return digits[-9:] if len(digits) >= 9 else ""


def _display_phone(value: Any) -> str | None:
    key = _phone_key(value)
    return f"+48{key}" if key else _text(value)


def _extract_phone_from_order_text(value: Any) -> str | None:
    """Wyodrębnia opisany telefon albo pojedynczy zwarty numer mobilny z treści."""
    text = _text(value) or ""
    matches = list(ORDER_PHONE_PATTERN.finditer(text))
    compact_mobile_matches = [
        match
        for match in matches
        if (phone_key := _phone_key(match.group(0)))
        and match.group(0) == phone_key
        and phone_key[0] in "45678"
    ]
    best_candidate: tuple[int, int, str] | None = None
    for match in matches:
        raw_phone = match.group(0)
        phone_key = _phone_key(raw_phone)
        if len(phone_key) != 9:
            continue
        context = text[max(0, match.start() - 24) : match.start()]
        has_cue = bool(ORDER_PHONE_CUE_PATTERN.search(context))
        has_block_cue = bool(ORDER_PHONE_BLOCK_CUE_PATTERN.search(context))
        has_separator = bool(re.search(r"\d[\s.-]+\d", raw_phone))
        has_country_prefix = bool(re.match(r"\s*(?:\+\s*48|00\s*48)", raw_phone))
        is_unique_compact_mobile = (
            len(compact_mobile_matches) == 1
            and compact_mobile_matches[0] is match
            and not has_block_cue
        )
        if not (has_cue or has_separator or has_country_prefix or is_unique_compact_mobile):
            continue
        score = (3 if has_cue else 0) + (2 if has_country_prefix else 0)
        score += 1 if has_separator else 0
        candidate = (score, -match.start(), f"+48{phone_key}")
        if best_candidate is None or candidate > best_candidate:
            best_candidate = candidate
    return best_candidate[2] if best_candidate else None


def _email_key(value: Any) -> str:
    return (_text(value) or "").casefold()


def _name_key(value: Any) -> str:
    return " ".join((_text(value) or "").casefold().split())


def _mobile_login(operator: Any) -> str:
    match = re.search(
        r"\bLogin:\s*(.+?)(?=\s+Edytował:|,\s*Zamknął|$)",
        _text(operator) or "",
        flags=re.IGNORECASE,
    )
    return _email_key(match.group(1)) if match else ""


def _created_from_mobile_app(operator: Any) -> bool:
    """Rozpoznaje jawny znacznik utworzenia zlecenia w aplikacji mobilnej."""
    return "utworzył z aplikacji:" in (_text(operator) or "").casefold()


class ShippingOrderStateConflict(RuntimeError):
    """Zmiana stanu zlecenia w MS blokująca zapis procesu wysyłki."""


def shipping_order_state_payload(order: dict[str, Any]) -> dict[str, Any]:
    """Buduje bezpieczny stan operacyjny zlecenia do kontroli współbieżności."""
    order_status = (_text(order.get("status")) or "").upper()
    tracking_number = _text(order.get("tracking_number"))
    technicians = [
        value
        for value in (
            _text(order.get("technician")),
            _text(order.get("secondary_technician")),
        )
        if value
    ]
    shipping_technicians = [
        value for value in technicians if _name_key(value) == _name_key(SHIPPING_TECHNICIAN_NAME)
    ]
    assigned_technicians = [
        value for value in technicians if _name_key(value) != _name_key(SHIPPING_TECHNICIAN_NAME)
    ]
    shipping_technician = " / ".join(dict.fromkeys(shipping_technicians)) or None
    assigned_technician = " / ".join(dict.fromkeys(assigned_technicians)) or None
    has_assigned_technician = bool(assigned_technician)
    is_queue_status = order_status in QUEUE_STATUSES
    eligible_for_shipping = is_queue_status and not has_assigned_technician
    completed = order_status == "Z"
    return {
        "order_table_id": int(order["order_table_id"]),
        "order_id": int(order["order_id"]),
        "order_year": int(order["order_year"]),
        "status": order_status,
        "status_label": ORDER_STATUS_LABELS.get(order_status, order_status or "nieznany"),
        "tracking_number": tracking_number,
        "invoice_id": order.get("invoice_id"),
        "wz_id": order.get("wz_id"),
        "rw_id": order.get("rw_id"),
        "document_number": _text(order.get("document_number")),
        "shipping_technician": shipping_technician,
        "has_shipping_technician": bool(shipping_technician),
        "assigned_technician": assigned_technician,
        "has_assigned_technician": has_assigned_technician,
        "is_queue_status": is_queue_status,
        "eligible_for_shipping": eligible_for_shipping,
        "completed": completed,
        "can_review": eligible_for_shipping and not tracking_number,
        "can_prepare_shipment": eligible_for_shipping
        and (order_status == "O" or (order_status == "ZR" and not tracking_number)),
        "can_finalize": eligible_for_shipping and order_status == "ZR" and bool(tracking_number),
    }


def shipping_order_state_conflict_message(
    order_state: dict[str, Any],
    *,
    operation: str,
) -> str:
    """Opisuje blokadę wynikającą ze świeżego stanu zlecenia w MS."""
    order_number = f"{order_state['order_id']}/{order_state['order_year']}"
    if order_state.get("has_assigned_technician"):
        technician = order_state.get("assigned_technician") or "nieznany"
        return (
            f"Zlecenie #{order_number} ma przypisanego technika „{technician}”. "
            f"Operacja „{operation}” została zablokowana, ponieważ takie zlecenie "
            "nie jest wysyłką magazynową — materiał dostarcza pracownik."
        )
    status_label = order_state["status_label"]
    status_code = order_state["status"] or "brak"
    return (
        f"Zlecenie #{order_number} ma obecnie w MS stan „{status_label}” ({status_code}). "
        f"Operacja „{operation}” została zablokowana, aby nie utworzyć podwójnych "
        "dokumentów ani ponownie nie rozchodować magazynu. Odśwież zlecenie i uzgodnij je w MS."
    )


def load_shipping_order_state(order_table_id: int) -> dict[str, Any]:
    """Odczytuje lekki, bieżący stan zlecenia do okresowej kontroli interfejsu."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT ID_ZLECENIE_TABLE, ID_ZLECENIE, ROK, STAN, PRZESYLKA,
                   ID_FAKTURA, ID_WZ, ID_RW, FAKTURA, TECHNIK, TECHNIK2
            FROM ZLECENIE
            WHERE ID_ZLECENIE_TABLE = ? AND TYP_US = ?
            """,
            (int(order_table_id), DELIVERY_TYPE_ID),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("Nie znaleziono zlecenia dowozu materiałów.")
        (
            table_id,
            order_id,
            order_year,
            order_status,
            tracking_number,
            invoice_id,
            wz_id,
            rw_id,
            document_number,
            technician,
            secondary_technician,
        ) = row
        return shipping_order_state_payload(
            {
                "order_table_id": table_id,
                "order_id": order_id,
                "order_year": order_year,
                "status": order_status,
                "tracking_number": tracking_number,
                "invoice_id": invoice_id,
                "wz_id": wz_id,
                "rw_id": rw_id,
                "document_number": document_number,
                "technician": technician,
                "secondary_technician": secondary_technician,
            }
        )
    finally:
        cursor.close()
        connection.close()


def _match_shipping_mobile_contact(
    order: dict[str, Any], contacts: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Dopasowuje autora zlecenia mobilnego do aktywnego kontaktu klienta."""
    if not order.get("created_from_mobile_app"):
        return None
    order_phone = _phone_key(order.get("order_phone"))
    order_email = _email_key(order.get("order_email"))
    order_name = _name_key(order.get("contact_name"))
    login = _mobile_login(order.get("order_operator"))
    scored: list[tuple[int, dict[str, Any]]] = []
    for contact in contacts:
        if not contact["is_mobile_user"]:
            continue
        score = 0
        if login and login in contact["match_logins"]:
            score += 8
        if order_email and order_email in contact["match_emails"]:
            score += 6
        if order_phone and order_phone in contact["match_phones"]:
            score += 4
        if order_name and order_name in contact["match_names"]:
            score += 2
        if score:
            scored.append((score, contact))
    if not scored:
        return None
    best_score = max(score for score, _ in scored)
    best = [contact for score, contact in scored if score == best_score]
    if len(best) == 1:
        return best[0]
    signatures = {(contact["name"], contact["phone"], contact["email"]) for contact in best}
    return best[0] if len(signatures) == 1 else None


def _load_shipping_contacts(cursor, order: dict[str, Any]) -> list[dict[str, Any]]:
    """Pobiera aktywne osoby po globalnym identyfikatorze klienta MS."""
    parameters = (int(order["client_id"]),)
    cursor.execute(
        """
        SELECT ID_KONTAKT_TABLE, NAZWA, NAZWA_S, FUNKCJA,
               TEL_K, TEL_S, TEL_D, MAIL1, MAIL2
        FROM KONTAKT
        WHERE ID_KLIENT = ?
          AND (AKTYWNY IS NULL OR AKTYWNY <> 'NIE')
        ORDER BY NAZWA, ID_KONTAKT_TABLE
        """,
        parameters,
    )
    rows = cursor.fetchall()
    cursor.execute(
        """
        SELECT ID_KONTAKT_TABLE
        FROM KONTAKT
        WHERE ID_KLIENT = ?
          AND NAZWA_S IS NOT NULL AND TRIM(NAZWA_S) <> ''
          AND LOCK_USER IS NOT NULL AND TRIM(LOCK_USER) <> ''
          AND (AKTYWNY IS NULL OR AKTYWNY <> 'NIE')
        """,
        parameters,
    )
    mobile_ids = {int(row[0]) for row in cursor.fetchall()}
    contacts: list[dict[str, Any]] = []
    for row in rows:
        contact_id = int(row[0])
        name = _text(row[1]) or _text(row[2]) or f"Kontakt {contact_id}"
        phone_values = row[4:7]
        email_values = row[7:9]
        contacts.append(
            {
                "id": contact_id,
                "name": name,
                "short_name": _text(row[2]),
                "role": _text(row[3]),
                "phone": next(
                    (_display_phone(value) for value in phone_values if _phone_key(value)),
                    None,
                ),
                "email": next((_text(value) for value in email_values if _email_key(value)), None),
                "is_mobile_user": contact_id in mobile_ids,
                "match_names": {key for key in (_name_key(row[1]), _name_key(row[2])) if key},
                "match_logins": {
                    key
                    for key in (_email_key(row[2]), *(_email_key(value) for value in email_values))
                    if key
                },
                "match_phones": {_phone_key(value) for value in phone_values if _phone_key(value)},
                "match_emails": {_email_key(value) for value in email_values if _email_key(value)},
            }
        )
    return contacts


def validate_shipping_dictionary() -> dict[str, Any]:
    """Potwierdza mapowanie typu usługi przed wyświetleniem kolejki."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT NAZWA FROM TYP_US WHERE ID_TU = ?", (DELIVERY_TYPE_ID,))
        row = cursor.fetchone()
        actual_name = _text(row[0]) if row else None
        valid = bool(actual_name and actual_name.casefold() == DELIVERY_TYPE_NAME.casefold())
        return {
            "valid": valid,
            "type_id": DELIVERY_TYPE_ID,
            "expected_name": DELIVERY_TYPE_NAME,
            "actual_name": actual_name,
        }
    finally:
        cursor.close()
        connection.close()


def load_shipping_overdue_summaries(
    customer_keys: set[tuple[int, int]] | list[tuple[int, int]],
    *,
    as_of: date | None = None,
) -> dict[tuple[int, int], dict[str, Any]]:
    """Zwraca zbiorcze zaległości FV dla klientów widocznych w kolejce wysyłek."""
    normalized_keys = sorted(
        {
            (int(company_id), int(client_id))
            for company_id, client_id in customer_keys
            if int(company_id) > 0 and int(client_id) > 0
        }
    )
    result = {key: _empty_overdue_invoice_summary() for key in normalized_keys}
    if not normalized_keys:
        return result

    due_before = as_of or date.today()
    clients_by_company: dict[int, list[int]] = {}
    for company_id, client_id in normalized_keys:
        clients_by_company.setdefault(company_id, []).append(client_id)

    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        for company_id, client_ids in clients_by_company.items():
            for offset in range(0, len(client_ids), OVERDUE_INVOICE_BATCH_SIZE):
                batch = client_ids[offset : offset + OVERDUE_INVOICE_BATCH_SIZE]
                placeholders = ", ".join("?" for _ in batch)
                cursor.execute(
                    f"""
                    SELECT ID_FIRMA, ID_KLIENT, COUNT(*), SUM(DO_ZAPLATY), MIN(DATA_PLAT)
                    FROM FAKTURA
                    WHERE ID_FIRMA = ?
                      AND ID_KLIENT IN ({placeholders})
                      AND ID_ODBIORCA = 0
                      AND DATA_PLAT < ?
                      AND DO_ZAPLATY > 0
                      AND RODZAJ_DOK = ?
                    GROUP BY ID_FIRMA, ID_KLIENT
                    """,
                    (company_id, *batch, due_before, INVOICE_DOCUMENT_KIND),
                )
                for row in cursor.fetchall():
                    key = (int(row[0]), int(row[1]))
                    oldest_due_date = _date_value(row[4])
                    result[key] = {
                        "has_overdue_invoices": True,
                        "invoice_count": int(row[2] or 0),
                        "total_overdue_amount": _number(row[3]),
                        "oldest_due_date": (
                            oldest_due_date.isoformat() if oldest_due_date else None
                        ),
                        "max_days_overdue": (
                            max(0, (due_before - oldest_due_date).days) if oldest_due_date else 0
                        ),
                    }
        return result
    finally:
        cursor.close()
        connection.close()


def load_shipping_overdue_invoices(
    *,
    company_id: int,
    client_id: int,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Zwraca przeterminowane i nieopłacone faktury klienta wraz z kwotami."""
    due_before = as_of or date.today()
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT ID_FAKTURA_TABLE, DOKUMENT, NUMER, DATA_WYST, DATA_PLAT,
                   SUMA_BRUTTO, ZAPLACONO, DO_ZAPLATY
            FROM FAKTURA
            WHERE ID_FIRMA = ?
              AND ID_KLIENT = ?
              AND ID_ODBIORCA = 0
              AND DATA_PLAT < ?
              AND DO_ZAPLATY > 0
              AND RODZAJ_DOK = ?
            ORDER BY DATA_PLAT ASC, DATA_WYST ASC, ID_FAKTURA_TABLE ASC
            """,
            (int(company_id), int(client_id), due_before, INVOICE_DOCUMENT_KIND),
        )
        invoices: list[dict[str, Any]] = []
        total_overdue = Decimal("0")
        for row in cursor.fetchall():
            issue_date = _date_value(row[3])
            due_date = _date_value(row[4])
            amount_due = Decimal(str(row[7] or 0))
            total_overdue += amount_due
            invoices.append(
                {
                    "invoice_id": int(row[0]),
                    "invoice_number": _text(row[2]) or str(row[1] or row[0]),
                    "issue_date": issue_date.isoformat() if issue_date else None,
                    "due_date": due_date.isoformat() if due_date else None,
                    "amount_gross": _number(row[5]),
                    "amount_paid": _number(row[6]),
                    "amount_due": float(amount_due),
                    "days_overdue": (max(0, (due_before - due_date).days) if due_date else 0),
                }
            )
        summary = _empty_overdue_invoice_summary()
        if invoices:
            summary.update(
                {
                    "has_overdue_invoices": True,
                    "invoice_count": len(invoices),
                    "total_overdue_amount": float(total_overdue),
                    "oldest_due_date": invoices[0]["due_date"],
                    "max_days_overdue": invoices[0]["days_overdue"],
                }
            )
        summary["invoices"] = invoices
        return summary
    finally:
        cursor.close()
        connection.close()


def load_shipping_queue(*, days: int = 30, limit: int = 200) -> list[dict[str, Any]]:
    """Pobiera dowozy i rozpoznaje model po globalnych identyfikatorach klienta i maszyny."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        date_from = date.today() - timedelta(days=int(days))
        cursor.execute(
            f"""
            SELECT FIRST {int(limit)}
                z.ID_ZLECENIE_TABLE AS ORDER_TABLE_ID,
                z.ID_ZLECENIE AS ORDER_ID,
                z.ROK AS ORDER_YEAR,
                z.DATA AS ORDER_DATE,
                z.STAN AS STATUS,
                z.RODZAJ_US AS ORDER_KIND,
                z.ID_FIRMA AS COMPANY_ID,
                z.ID_KLIENT AS CLIENT_ID,
                z.ID_MASZYNA AS MACHINE_ID,
                z.NAZWA AS COMPANY_NAME,
                z.STOI AS LOCATION,
                z.MARKA AS DEVICE_BRAND,
                z.MODEL AS DEVICE_MODEL,
                z.SERIAL AS DEVICE_SERIAL,
                z.PROBLEM AS PROBLEM,
                z.TELEFON AS PHONE,
                z.E_MAIL AS EMAIL,
                z.OPERATOR AS ORDER_OPERATOR,
                z.PRZESYLKA AS TRACKING_NUMBER,
                z.DATA_PRZES AS SHIPPED_AT,
                m.ID_MODEL AS MODEL_ID
            FROM ZLECENIE z
            LEFT JOIN MASZYNA m
              ON m.ID_KLIENT = z.ID_KLIENT
             AND m.ID_MASZYNA = z.ID_MASZYNA
            WHERE z.TYP_US = ?
              AND z.STAN IN (?, ?)
              AND (COALESCE(TRIM(z.TECHNIK), '') = '' OR TRIM(z.TECHNIK) = ?)
              AND (COALESCE(TRIM(z.TECHNIK2), '') = '' OR TRIM(z.TECHNIK2) = ?)
              AND z.DATA >= ?
            ORDER BY z.DATA ASC, z.ID_ZLECENIE_TABLE ASC
            """,
            (
                DELIVERY_TYPE_ID,
                *QUEUE_STATUSES,
                SHIPPING_TECHNICIAN_NAME,
                SHIPPING_TECHNICIAN_NAME,
                date_from,
            ),
        )
        items = [_dict_row(cursor, row) for row in cursor.fetchall()]
        for item in items:
            item["created_from_mobile_app"] = _created_from_mobile_app(item.get("order_operator"))
            item["order_source"] = "mobile" if item["created_from_mobile_app"] else "manual"
        return items
    finally:
        cursor.close()
        connection.close()


def load_shipping_order(order_table_id: int) -> dict[str, Any]:
    """Pobiera źródła zlecenia po globalnych identyfikatorach rekordów MS."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT FIRST 1
                z.ID_ZLECENIE_TABLE AS ORDER_TABLE_ID,
                z.ID_ZLECENIE AS ORDER_ID,
                z.ROK AS ORDER_YEAR,
                z.DATA AS ORDER_DATE,
                z.STAN AS STATUS,
                z.RODZAJ_US AS ORDER_KIND,
                z.ID_FIRMA AS COMPANY_ID,
                z.ID_ODDZIAL AS BRANCH_ID,
                z.ID_KLIENT AS CLIENT_ID,
                z.ID_MASZYNA AS MACHINE_ID,
                z.NAZWA AS ORDER_COMPANY_NAME,
                z.ADRES AS ORDER_STREET,
                z.KOD AS ORDER_POSTAL_CODE,
                z.POCZTA AS ORDER_CITY,
                z.STOI AS ORDER_LOCATION,
                z.ADRES_PRZES AS ORDER_SHIPPING_ADDRESS,
                z.TELEFON AS ORDER_PHONE,
                z.E_MAIL AS ORDER_EMAIL,
                z.ZGLASZA AS CONTACT_NAME,
                z.OPERATOR AS ORDER_OPERATOR,
                z.MARKA AS DEVICE_BRAND,
                z.MODEL AS DEVICE_MODEL,
                z.SERIAL AS DEVICE_SERIAL,
                z.EWIDENCJA AS DEVICE_ASSET_NUMBER,
                z.PROBLEM AS PROBLEM,
                z.PRZESYLKA AS TRACKING_NUMBER,
                z.ID_FAKTURA AS INVOICE_ID,
                z.ID_WZ AS WZ_ID,
                z.ID_RW AS RW_ID,
                z.FAKTURA AS DOCUMENT_NUMBER,
                z.TECHNIK AS TECHNICIAN,
                z.TECHNIK2 AS SECONDARY_TECHNICIAN,
                k.NAZWA AS CLIENT_COMPANY_NAME,
                k.ADRES AS CLIENT_STREET,
                k.KOD AS CLIENT_POSTAL_CODE,
                k.POCZTA AS CLIENT_CITY,
                k.TELEFON AS CLIENT_PHONE,
                k.E_MAIL AS CLIENT_EMAIL,
                k.KONTAKT AS CLIENT_CONTACT_NAME,
                k.KOD_KRAJU AS CLIENT_COUNTRY_CODE,
                o.NAZWA AS BRANCH_COMPANY_NAME,
                o.ADRES AS BRANCH_STREET,
                o.KOD AS BRANCH_POSTAL_CODE,
                o.POCZTA AS BRANCH_CITY,
                o.TEL AS BRANCH_PHONE,
                o.EMAIL AS BRANCH_EMAIL,
                o.KONTAKT AS BRANCH_CONTACT_NAME,
                m.ID_MODEL AS MODEL_ID,
                m.STOI AS MACHINE_LOCATION,
                m.ADRES AS MACHINE_ADDRESS,
                m.MIEJSCOWOSC AS MACHINE_CITY,
                m.EMAIL AS MACHINE_EMAIL,
                m.MARKA AS MACHINE_BRAND,
                m.MODEL AS MACHINE_MODEL
            FROM ZLECENIE z
            LEFT JOIN KLIENT k
              ON k.ID_KLIENT = z.ID_KLIENT
            LEFT JOIN ODDZIAL o
              ON o.ID_ODDZIAL = z.ID_ODDZIAL
            LEFT JOIN MASZYNA m
              ON m.ID_KLIENT = z.ID_KLIENT
             AND m.ID_MASZYNA = z.ID_MASZYNA
            WHERE z.ID_ZLECENIE_TABLE = ? AND z.TYP_US = ?
            """,
            (int(order_table_id), DELIVERY_TYPE_ID),
        )
        row = cursor.fetchone()
        if row is None:
            raise ValueError("Nie znaleziono zlecenia dowozu materiałów.")
        result = _dict_row(cursor, row)
        result["created_from_mobile_app"] = _created_from_mobile_app(result.get("order_operator"))
        contacts = _load_shipping_contacts(cursor, result)
        mobile_contact = _match_shipping_mobile_contact(result, contacts)
        for contact in contacts:
            contact.pop("match_names", None)
            contact.pop("match_logins", None)
            contact.pop("match_phones", None)
            contact.pop("match_emails", None)
        result["contacts"] = contacts
        result["mobile_contact"] = mobile_contact
        problem_phone = _extract_phone_from_order_text(result.get("problem"))
        result["problem_phone"] = problem_phone
        result["preferred_contact_name"] = (
            mobile_contact.get("name") if mobile_contact else result.get("contact_name")
        )
        result["preferred_contact_phone"] = problem_phone or (
            mobile_contact.get("phone")
            if mobile_contact and mobile_contact.get("phone")
            else _display_phone(result.get("order_phone"))
        )
        result["preferred_contact_phone_source"] = (
            "problem"
            if problem_phone
            else (
                "mobile"
                if mobile_contact and mobile_contact.get("phone")
                else "order" if _phone_key(result.get("order_phone")) else None
            )
        )
        result["preferred_contact_email"] = (
            mobile_contact.get("email")
            if mobile_contact and mobile_contact.get("email")
            else result.get("order_email")
        )
        use_order_address = bool(
            result.get("order_street")
            and result.get("order_postal_code")
            and result.get("order_city")
        )
        prefix = "order" if use_order_address else "client"
        result["suggested_address"] = {
            "company_name": result.get(f"{prefix}_company_name")
            or result.get("order_company_name")
            or result.get("client_company_name"),
            "contact_name": result.get("preferred_contact_name")
            or result.get(f"{prefix}_contact_name"),
            "street": result.get(f"{prefix}_street"),
            "postal_code": result.get(f"{prefix}_postal_code"),
            "city": result.get(f"{prefix}_city"),
            "country_code": "PL",
            "phone": result.get("preferred_contact_phone")
            or result.get(f"{prefix}_phone")
            or result.get("client_phone"),
            "email": result.get("preferred_contact_email")
            or result.get("machine_email")
            or result.get("client_email"),
            "source": prefix,
            "location_text": result.get("machine_location") or result.get("order_location"),
        }
        return result
    finally:
        cursor.close()
        connection.close()


def load_physical_stock(
    *,
    warehouse_id: int,
    query: str | None = None,
    item_ids: set[int] | None = None,
    only_available: bool = False,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """Pobiera wyłącznie części i towary fizyczne z wybranego magazynu."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        conditions = [
            "ID_MAGAZYN = ?",
            "RODZAJ IN ('1. Część zamienna', '2. Towar inny')",
        ]
        parameters: list[Any] = [int(warehouse_id)]
        stock_search = (
            "COALESCE(INDEKS, '') || ' ' || COALESCE(NR_KAT1, '') || ' ' || "
            "COALESCE(NR_KAT2, '') || ' ' || COALESCE(NAZWA, '') || ' ' || "
            "COALESCE(MARKA, '') || ' ' || COALESCE(MODEL, '')"
        )
        compact_stock_search = (
            f"REPLACE(REPLACE(REPLACE(({stock_search}), ' ', ''), '-', ''), '/', '')"
        )
        for term in _search_terms(query):
            conditions.append(
                f"(UPPER({stock_search}) CONTAINING UPPER(?) "
                f"OR UPPER({compact_stock_search}) CONTAINING UPPER(?))"
            )
            parameters.extend([term, term])
        if item_ids is not None:
            normalized_ids = sorted({int(value) for value in item_ids if int(value) > 0})
            if not normalized_ids:
                return []
            placeholders = ", ".join("?" for _ in normalized_ids)
            conditions.append(f"ID_MAGAZYN_TABLE IN ({placeholders})")
            parameters.extend(normalized_ids)
        if only_available:
            conditions.append("COALESCE(ILOSC, 0) - COALESCE(IL_REZ, 0) > 0")
        cursor.execute(
            f"""
            SELECT FIRST {max(1, min(int(limit), 5000))}
                ID_MAGAZYN_TABLE AS WAREHOUSE_ITEM_ID,
                ID_MAGAZYN AS WAREHOUSE_ID,
                RODZAJ AS ITEM_KIND,
                INDEKS AS ITEM_INDEX,
                NR_KAT1 AS CATALOG_NUMBER_1,
                NR_KAT2 AS CATALOG_NUMBER_2,
                NAZWA AS ITEM_NAME,
                JM AS UNIT,
                ILOSC AS STOCK_QUANTITY,
                IL_REZ AS RESERVED_QUANTITY,
                CENA_NETTO AS PRICE_NET,
                CENA_Z1 AS PURCHASE_PRICE_NET,
                VAT_STAWKA AS VAT_RATE,
                IDVAT AS VAT_ID,
                MARKA AS BRAND,
                MODEL AS MODEL
            FROM MAGAZYN
            WHERE {' AND '.join(conditions)}
            ORDER BY NAZWA, INDEKS
            """,
            tuple(parameters),
        )
        result = []
        for row in cursor.fetchall():
            item = _dict_row(cursor, row)
            item["vat_rate"] = float(_vat_rate(item.get("vat_rate")))
            stock = _number(item.get("stock_quantity"))
            reserved = _number(item.get("reserved_quantity"))
            item["available_quantity"] = max(0.0, stock - reserved)
            result.append(item)
        return result
    finally:
        cursor.close()
        connection.close()


def load_toner_stock(*, warehouse_id: int) -> list[dict[str, Any]]:
    """Zwraca fizyczne części i towary; nazwa funkcji zachowuje zgodność modułu wysyłek."""
    return load_physical_stock(warehouse_id=warehouse_id)


def load_compatibility_catalog(*, warehouse_id: int) -> dict[str, list[dict[str, Any]]]:
    """Pobiera kartoteki, modele i historyczne użycia bez wykonywania zapisów w Firebird."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT
                ID_MAGAZYN_TABLE AS WAREHOUSE_ITEM_ID,
                ID_MAGAZYN AS WAREHOUSE_ID,
                RODZAJ AS ITEM_KIND,
                INDEKS AS ITEM_INDEX,
                NR_KAT1 AS CATALOG_NUMBER_1,
                NR_KAT2 AS CATALOG_NUMBER_2,
                NAZWA AS ITEM_NAME,
                MARKA AS BRAND,
                MODEL AS MODEL,
                ILOSC AS STOCK_QUANTITY,
                IL_REZ AS RESERVED_QUANTITY
            FROM MAGAZYN
            WHERE ID_MAGAZYN = ?
              AND RODZAJ IN ('1. Część zamienna', '2. Towar inny')
            ORDER BY ID_MAGAZYN_TABLE
            """,
            (int(warehouse_id),),
        )
        items = [_dict_row(cursor, row) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT ID_MODEL, MARKA, MODEL, RODZAJ, TONER, TONER_C, TONER_M, TONER_Y
            FROM MODEL
            WHERE ID_MODEL IS NOT NULL
            ORDER BY ID_MODEL
            """
        )
        models = [_dict_row(cursor, row) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT
                p.ID_MAGPOZ AS WAREHOUSE_ITEM_ID,
                ma.ID_MODEL AS MODEL_ID,
                COUNT(DISTINCT z.ID_ZLECENIE_TABLE) AS ORDER_COUNT,
                COUNT(DISTINCT ma.ID_MASZYNA_TABLE) AS MACHINE_COUNT
            FROM ZPOZYCJA p
            JOIN ZLECENIE z
              ON z.ID_ZLECENIE = p.ID_ZLECENIE AND z.ROK = p.ROK
            JOIN MASZYNA ma
              ON ma.ID_KLIENT = z.ID_KLIENT AND ma.ID_MASZYNA = z.ID_MASZYNA
            JOIN MAGAZYN mg
              ON mg.ID_MAGAZYN_TABLE = p.ID_MAGPOZ
            WHERE mg.ID_MAGAZYN = ?
              AND mg.RODZAJ IN ('1. Część zamienna', '2. Towar inny')
              AND ma.ID_MODEL IS NOT NULL
            GROUP BY p.ID_MAGPOZ, ma.ID_MODEL
            """,
            (int(warehouse_id),),
        )
        history = [_dict_row(cursor, row) for row in cursor.fetchall()]
        return {"items": items, "models": models, "history": history}
    finally:
        cursor.close()
        connection.close()


def load_device_models(*, query: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Wyszukuje kanoniczne modele urządzeń w słowniku Firebird."""
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        conditions = ["ID_MODEL IS NOT NULL"]
        parameters: list[Any] = []
        model_search = (
            "COALESCE(MARKA, '') || ' ' || COALESCE(MODEL, '') || ' ' || " + "COALESCE(RODZAJ, '')"
        )
        compact_model_search = (
            f"REPLACE(REPLACE(REPLACE(({model_search}), ' ', ''), '-', ''), '/', '')"
        )
        for term in _search_terms(query):
            conditions.append(
                f"(UPPER({model_search}) CONTAINING UPPER(?) "
                f"OR UPPER({compact_model_search}) CONTAINING UPPER(?))"
            )
            parameters.extend([term, term])
        cursor.execute(
            f"""
            SELECT FIRST {max(1, min(int(limit), 500))}
                ID_MODEL, MARKA, MODEL, RODZAJ
            FROM MODEL
            WHERE {' AND '.join(conditions)}
            ORDER BY MARKA, MODEL, ID_MODEL
            """,
            tuple(parameters),
        )
        return [_dict_row(cursor, row) for row in cursor.fetchall()]
    finally:
        cursor.close()
        connection.close()


def write_shipment_to_order(
    *,
    order_table_id: int,
    tracking_number: str,
    items: list[dict[str, Any]],
    shipping_address: dict[str, Any] | None = None,
    tracking_source: str = "dpd",
    generated_at: datetime | date | None = None,
) -> dict[str, Any]:
    """Dodaje pozycje, dane etykiety i oznacza zlecenie jako przygotowane (`ZR`)."""
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")
    connection = firebird_connection()
    cursor = connection.cursor()
    created_position_ids: list[int] = []
    try:
        cursor.execute(
            """
            SELECT ID_KLIENT, ID_MASZYNA, ID_ZLECENIE, ROK, STAN, PRZESYLKA,
                   TECHNIK, TECHNIK2, WYKONANIE, ADRES_PRZES
            FROM ZLECENIE WHERE ID_ZLECENIE_TABLE = ? AND TYP_US = ?
            WITH LOCK
            """,
            (int(order_table_id), DELIVERY_TYPE_ID),
        )
        order = cursor.fetchone()
        if order is None:
            raise ValueError("Nie znaleziono zlecenia do aktualizacji.")
        (
            client_id,
            machine_id,
            order_id,
            order_year,
            order_status,
            existing_tracking,
            technician,
            secondary_technician,
            existing_execution,
            existing_shipping_address,
        ) = order
        order_state = shipping_order_state_payload(
            {
                "order_table_id": order_table_id,
                "order_id": order_id,
                "order_year": order_year,
                "status": order_status,
                "tracking_number": existing_tracking,
                "technician": technician,
                "secondary_technician": secondary_technician,
            }
        )
        is_idempotent_retry = bool(
            order_state["status"] == "ZR"
            and order_state["tracking_number"] == _text(tracking_number)
            and not order_state["has_assigned_technician"]
        )
        if not order_state["can_prepare_shipment"] and not is_idempotent_retry:
            raise ShippingOrderStateConflict(
                shipping_order_state_conflict_message(
                    order_state,
                    operation="zapis etykiety i części",
                )
            )
        if _text(existing_tracking) and _text(existing_tracking) != tracking_number:
            raise ShippingOrderStateConflict(
                "Zlecenie ma już inny numer przesyłki; wymagane jest uzgodnienie ręczne."
            )

        address_text = _shipping_address_text(shipping_address)
        if (
            address_text
            and _text(existing_shipping_address)
            and _text(existing_shipping_address) != address_text
        ):
            raise ShippingOrderStateConflict(
                "Zlecenie ma już inny adres przesyłki; wymagane jest uzgodnienie ręczne."
            )
        label_note = _shipping_label_note(
            generated_on=_shipping_local_date(generated_at),
            tracking_number=tracking_number,
            tracking_source=tracking_source,
        )
        execution = _append_execution_note(existing_execution, label_note)

        for item in items:
            warehouse_item_id = int(item["firebird_warehouse_item_id"])
            requested_quantity = Decimal(str(item["quantity"]))
            cursor.execute(
                """
                SELECT COALESCE(SUM(ILOSC), 0)
                FROM ZPOZYCJA
                WHERE ID_ZLECENIE = ? AND ROK = ? AND ID_MAGPOZ = ?
                """,
                (order_id, order_year, warehouse_item_id),
            )
            existing_quantity = Decimal(str(cursor.fetchone()[0] or 0))
            missing_quantity = requested_quantity - existing_quantity
            if missing_quantity <= 0:
                continue
            cursor.execute(
                """
                SELECT ID_MAGAZYN, RODZAJ, INDEKS, NAZWA, JM,
                       COALESCE(CENA_NETTO, 0), COALESCE(CENA_Z1, 0),
                       COALESCE(VAT_STAWKA, 23), COALESCE(IDVAT, 1),
                       COALESCE(ILOSC, 0) - COALESCE(IL_REZ, 0)
                FROM MAGAZYN WHERE ID_MAGAZYN_TABLE = ?
                """,
                (warehouse_item_id,),
            )
            warehouse = cursor.fetchone()
            if warehouse is None:
                raise RuntimeError(f"Brak kartoteki magazynowej {warehouse_item_id}.")
            (
                _warehouse_id,
                item_kind,
                item_index,
                item_name,
                unit,
                price_net,
                purchase_price,
                vat_rate,
                vat_id,
                available,
            ) = warehouse
            if (
                Decimal(str(available or 0)) < missing_quantity
                and item.get("allow_negative_stock") is not True
            ):
                raise RuntimeError(f"Brak wystarczającego stanu dla pozycji {item_name}.")
            vat_rate_value = _vat_rate(vat_rate)
            selected_price = Decimal(
                str(item.get("price_net") or item.get("unit_price_net") or price_net or 0)
            )
            if selected_price <= 0:
                raise RuntimeError(f"Brak zaakceptowanej ceny netto pozycji {item_name}.")
            net_value = selected_price * missing_quantity
            purchase_value = Decimal(str(purchase_price or 0)) * missing_quantity
            vat_value = net_value * vat_rate_value / Decimal("100")
            gross_value = net_value + vat_value
            normalized_kind = _text(item_kind) or "2. Towar inny"
            part_flag = 0 if normalized_kind.startswith("1") else 1
            cursor.execute(
                """
                INSERT INTO ZPOZYCJA (
                    ID_KLIENT, ID_MASZYNA, ID_ZLECENIE, ID_MAGPOZ,
                    RODZAJ, INDEKS, NAZWA, JM, ILOSC,
                    CENA, WARTOSC, VAT, BRUTTO, CENA_Z, WARTOSC_Z,
                    STAWKA_VAT, IDVAT, CZESC, ROK
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING ID_ZPOZYCJA_TABLE
                """,
                (
                    client_id,
                    machine_id,
                    order_id,
                    warehouse_item_id,
                    normalized_kind,
                    item_index,
                    item_name,
                    _text(unit) or "szt.",
                    missing_quantity,
                    selected_price,
                    net_value,
                    vat_value,
                    gross_value,
                    purchase_price,
                    purchase_value,
                    vat_rate_value,
                    vat_id,
                    part_flag,
                    order_year,
                ),
            )
            created_position_ids.append(int(cursor.fetchone()[0]))

        cursor.execute(
            """
            UPDATE ZLECENIE
            SET PRZESYLKA = ?, WYKONANIE = ?,
                ADRES_PRZES = COALESCE(?, ADRES_PRZES), STAN = 'ZR'
            WHERE ID_ZLECENIE_TABLE = ?
            """,
            (tracking_number, execution, address_text, int(order_table_id)),
        )
        connection.commit()
        return {
            "status": "written",
            "previous_order_status": _text(order_status),
            "created_position_ids": created_position_ids,
            "label_note": label_note,
            "shipping_address": address_text,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def write_shipping_milestones_to_order(
    *,
    order_table_id: int,
    tracking_number: str,
    pickup_date: date | None = None,
    pickup_note: str | None = None,
    delivery_date: date | None = None,
    description_text: str | None = None,
    expected_description_text: str | None = None,
    expected_state_token: str | None = None,
) -> dict[str, Any]:
    """Zapisuje potwierdzone przez DPD daty i ostatni istotny opis przesyłki."""
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        state = _load_shipping_milestone_state(
            cursor,
            order_table_id=order_table_id,
            with_lock=True,
        )
        current_state_token = shipping_milestone_state_token(state)
        if expected_state_token and current_state_token != expected_state_token:
            raise ShippingOrderStateConflict(
                "Pola przesyłki w MS zmieniły się po wykonaniu dry-run. "
                "Ponów podgląd przed zapisem."
            )
        updates = _shipping_milestone_updates(
            state,
            tracking_number=tracking_number,
            pickup_date=pickup_date,
            pickup_note=pickup_note,
            delivery_date=delivery_date,
            description_text=description_text,
            expected_description_text=expected_description_text,
        )
        if updates:
            assignments = [f"{field} = ?" for field in updates]
            parameters = [*updates.values(), int(order_table_id)]
            cursor.execute(
                f"UPDATE ZLECENIE SET {', '.join(assignments)} WHERE ID_ZLECENIE_TABLE = ?",
                tuple(parameters),
            )
        connection.commit()
        return {
            "status": "written",
            "changed_fields": list(updates),
            "state_token_before": current_state_token,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def restore_shipping_milestones_to_order(
    *,
    order_table_id: int,
    tracking_number: str,
    changed_fields: list[str],
    before: dict[str, Any],
    expected_after: dict[str, Any],
) -> dict[str, Any]:
    """Przywraca wyłącznie pola zmienione przez wskazany przebieg pilota."""
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebirda jest zablokowany.")
    normalized_fields = list(dict.fromkeys(str(field) for field in changed_fields))
    if not normalized_fields or any(
        field not in SHIPPING_MILESTONE_FIELDS for field in normalized_fields
    ):
        raise ValueError("Raport pilota zawiera niepoprawną listę pól do wycofania.")
    if any(field not in before or field not in expected_after for field in normalized_fields):
        raise ValueError("Raport pilota nie zawiera pełnego stanu przed i po operacji.")

    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        state = _load_shipping_milestone_state(
            cursor,
            order_table_id=order_table_id,
            with_lock=True,
        )
        if _text(state.get("tracking_number")) != _text(tracking_number):
            raise ShippingOrderStateConflict("Numer przesyłki w MS różni się od raportu pilota.")
        current = shipping_milestone_field_snapshot(state)
        for field in normalized_fields:
            if current[field] != expected_after[field]:
                raise ShippingOrderStateConflict(
                    f"Pole {field} zmieniło się po pilocie; automatyczny rollback jest zabroniony."
                )

        assignments = [f"{field} = ?" for field in normalized_fields]
        parameters: list[Any] = []
        for field in normalized_fields:
            value = before[field]
            if field in {"DATA_PRZES", "DATA_PRZES_WE"}:
                value = _date_from_payload(value)
            parameters.append(value)
        parameters.append(int(order_table_id))
        cursor.execute(
            f"UPDATE ZLECENIE SET {', '.join(assignments)} WHERE ID_ZLECENIE_TABLE = ?",
            tuple(parameters),
        )
        connection.commit()
        return {"status": "restored", "changed_fields": normalized_fields}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def _legacy_create_rw_and_close_order(
    *,
    order_table_id: int,
    warehouse_id: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Blokuje historyczną implementację zapisującą RW w niewłaściwej tabeli."""
    raise RuntimeError(
        "Historyczny generator FAKTURA/ROK jest wyłączony; użyj finalize_shipping_order."
    )
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")
    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT ID_FIRMA, ID_ODDZIAL, ID_KLIENT, ID_MASZYNA, ID_ZLECENIE, ROK,
                   NAZWA, ADRES, KOD, POCZTA, NIP, RODZAJ_US, ID_RW
            FROM ZLECENIE WHERE ID_ZLECENIE_TABLE = ? AND TYP_US = ?
            """,
            (int(order_table_id), DELIVERY_TYPE_ID),
        )
        order = cursor.fetchone()
        if order is None:
            raise ValueError("Nie znaleziono zlecenia do zamknięcia.")
        (
            company_id,
            branch_id,
            client_id,
            machine_id,
            order_id,
            order_year,
            company_name,
            street,
            postal_code,
            city,
            nip,
            order_kind,
            existing_rw_id,
        ) = order
        if _text(order_kind or "").casefold() != "umowa":
            return {"status": "manual_billing", "rw_id": None, "rw_number": None}
        if existing_rw_id:
            cursor.execute(
                "SELECT NUMER FROM FAKTURA WHERE ID_FAKTURA_TABLE = ?",
                (int(existing_rw_id),),
            )
            row = cursor.fetchone()
            cursor.execute(
                "UPDATE ZLECENIE SET STAN = 'Z' WHERE ID_ZLECENIE_TABLE = ?",
                (int(order_table_id),),
            )
            connection.commit()
            return {
                "status": "already_exists",
                "rw_id": int(existing_rw_id),
                "rw_number": _text(row[0]) if row else None,
            }

        if not items:
            raise RuntimeError("Brak pozycji przesyłki do utworzenia dokumentu RW.")
        item_quantities: dict[int, Decimal] = {}
        negative_stock_permissions: dict[int, bool] = {}
        for item in items:
            item_id = int(item["firebird_warehouse_item_id"])
            quantity = Decimal(str(item["quantity"]))
            if quantity <= 0:
                raise RuntimeError(f"Niepoprawna ilość pozycji {item_id} w przesyłce.")
            item_quantities[item_id] = item_quantities.get(item_id, Decimal("0")) + quantity
            negative_stock_permissions[item_id] = (
                negative_stock_permissions.get(item_id, False)
                or item.get("allow_negative_stock") is True
            )
        item_ids = list(item_quantities)
        placeholders = ",".join("?" for _ in item_ids)
        cursor.execute(
            f"""
            SELECT ID_MAGAZYN_TABLE, RODZAJ, INDEKS, NAZWA, JM,
                   COALESCE(CENA_NETTO, 0), COALESCE(CENA_Z1, 0),
                   COALESCE(VAT_STAWKA, 23), COALESCE(IDVAT, 1),
                   COALESCE(ILOSC, 0) - COALESCE(IL_REZ, 0)
            FROM MAGAZYN
            WHERE ID_MAGAZYN = ? AND ID_MAGAZYN_TABLE IN ({placeholders})
            """,
            (int(warehouse_id), *[int(item_id) for item_id in item_ids]),
        )
        warehouse_by_id = {int(row[0]): row for row in cursor.fetchall()}
        if len(warehouse_by_id) != len(set(item_ids)):
            raise RuntimeError("Nie wszystkie pozycje przesyłki istnieją w magazynie wydającym.")

        cursor.execute(
            """
            INSERT INTO FAKTURA (
                ID_ODDZIAL, ID_FIRMA, ID_MAGAZYN, ID_KLIENT, ID_MASZYNA,
                NAZWA, ADRES, KOD, POCZTA, NIP, RODZAJ_DOK, NUMER,
                DATA_SPRZ, DATA_WYST, DATA_PLAT, DNI_PLAT, PLATNOSC,
                WYSTAWIL, SUMA_NETTO, SUMA_VAT, SUMA_BRUTTO, ZAPLACONO,
                DO_ZAPLATY, STAN
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ROK', ?,
                      CURRENT_DATE, CURRENT_DATE, CURRENT_DATE, 0, 'Rozchód',
                      'CTIP', 0, 0, 0, 0, 0, '')
            RETURNING ID_FAKTURA_TABLE, DOKUMENT
            """,
            (
                branch_id or 1,
                company_id,
                int(warehouse_id),
                client_id,
                machine_id,
                company_name,
                street,
                postal_code,
                city,
                nip,
                f"CTIP/{order_table_id}",
            ),
        )
        rw_id, document_number = cursor.fetchone()
        rw_number = f"{document_number}/{date.today().year}"
        cursor.execute(
            "UPDATE FAKTURA SET NUMER = ? WHERE ID_FAKTURA_TABLE = ?",
            (rw_number, rw_id),
        )

        total_net = Decimal("0")
        total_vat = Decimal("0")
        total_gross = Decimal("0")
        for item_id in item_ids:
            quantity = item_quantities[item_id]
            warehouse = warehouse_by_id[int(item_id)]
            (
                _item_id,
                item_kind,
                item_index,
                item_name,
                unit,
                price_net,
                purchase_price,
                vat_rate,
                vat_id,
                available,
            ) = warehouse
            if Decimal(str(available or 0)) < quantity and not negative_stock_permissions[item_id]:
                raise RuntimeError(f"Brak stanu magazynowego dla pozycji {item_name}.")
            vat_rate_value = _vat_rate(vat_rate)
            net_value = Decimal(str(price_net or 0)) * quantity
            purchase_value = Decimal(str(purchase_price or 0)) * quantity
            vat_value = net_value * vat_rate_value / Decimal("100")
            gross_value = net_value + vat_value
            cursor.execute(
                """
                INSERT INTO FPOZYCJA (
                    ID_FAKTURA, ID_FIRMA, ID_KLIENT, ID_MASZYNA,
                    ID_ZLECENIE, ROK_ZLECENIA, ID_MAGAZYN, ID_MAGPOZ,
                    RODZAJ_DOK, NUMER, DATA_SPRZ, RODZAJ, INDEKS, NAZWA,
                    CENA_NETTO, CENA_BRUTTO, CENA_Z, ILOSC, JM,
                    WARTOSC_NETTO, WARTOSC_Z, STAWKA_VAT, VAT, IDVAT,
                    WARTOSC_BRUTTO, POBRANO
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ROK', ?, CURRENT_DATE,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    rw_id,
                    company_id,
                    client_id,
                    machine_id,
                    order_id,
                    order_year,
                    int(warehouse_id),
                    int(item_id),
                    rw_number,
                    _text(item_kind) or "2. Towar inny",
                    item_index,
                    item_name,
                    price_net,
                    Decimal(str(price_net or 0)) * (Decimal("1") + vat_rate_value / Decimal("100")),
                    purchase_price,
                    quantity,
                    _text(unit) or "szt.",
                    net_value,
                    purchase_value,
                    _ms_vat_rate_text(vat_rate_value),
                    vat_value,
                    vat_id,
                    gross_value,
                ),
            )
            total_net += net_value
            total_vat += vat_value
            total_gross += gross_value

        cursor.execute(
            """
            UPDATE FAKTURA
            SET SUMA_NETTO = ?, SUMA_VAT = ?, SUMA_BRUTTO = ?, DO_ZAPLATY = ?
            WHERE ID_FAKTURA_TABLE = ?
            """,
            (total_net, total_vat, total_gross, total_gross, rw_id),
        )
        cursor.execute(
            """
            UPDATE ZLECENIE
            SET ID_RW = ?, ID_FAKTURA = NULL, STAN = 'Z'
            WHERE ID_ZLECENIE_TABLE = ?
            """,
            (rw_id, int(order_table_id)),
        )
        connection.commit()
        return {"status": "created", "rw_id": int(rw_id), "rw_number": rw_number}
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


def finalize_shipping_order(
    *,
    order_table_id: int,
    warehouse_id: int,
    items: list[dict[str, Any]],
    invoice_required: bool,
    tracking_number: str,
    issued_by: str,
    shipping_address: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Tworzy RW albo WZ z opcjonalną FV, wiąże dokumenty i zamyka zlecenie."""
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")
    if not items:
        raise RuntimeError("Brak pozycji przesyłki do rozliczenia.")

    connection = firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            SELECT ID_FIRMA, ID_ODDZIAL, ID_KLIENT, ID_MASZYNA, ID_ZLECENIE, ROK,
                   NAZWA, ADRES, KOD, POCZTA, NIP, RODZAJ_US,
                   ID_FAKTURA, ID_WZ, ID_RW, FAKTURA,
                   PRZESYLKA, MARKA, MODEL, STAN, TECHNIK, TECHNIK2
            FROM ZLECENIE WHERE ID_ZLECENIE_TABLE = ? AND TYP_US = ?
            WITH LOCK
            """,
            (int(order_table_id), DELIVERY_TYPE_ID),
        )
        order = cursor.fetchone()
        if order is None:
            raise ValueError("Nie znaleziono zlecenia do zamknięcia.")
        (
            company_id,
            branch_id,
            client_id,
            machine_id,
            order_id,
            order_year,
            company_name,
            street,
            postal_code,
            city,
            nip,
            order_kind,
            existing_invoice_id,
            existing_wz_id,
            existing_rw_id,
            existing_document_text,
            existing_tracking,
            device_brand,
            device_model,
            order_status,
            technician,
            secondary_technician,
        ) = order
        order_state = shipping_order_state_payload(
            {
                "order_table_id": order_table_id,
                "order_id": order_id,
                "order_year": order_year,
                "status": order_status,
                "tracking_number": existing_tracking,
                "invoice_id": existing_invoice_id,
                "wz_id": existing_wz_id,
                "rw_id": existing_rw_id,
                "document_number": existing_document_text,
                "technician": technician,
                "secondary_technician": secondary_technician,
            }
        )
        document_mode = shipping_document_mode(
            order_kind=order_kind,
            invoice_required=invoice_required,
        )
        document_date = date.today()
        requested_tracking = _text(tracking_number)
        if (
            order_state["tracking_number"]
            and requested_tracking
            and order_state["tracking_number"] != requested_tracking
        ):
            raise ShippingOrderStateConflict(
                "Numer przesyłki w MS różni się od numeru zapisanej etykiety. "
                "Zamknięcie zlecenia wymaga ręcznego uzgodnienia."
            )
        normalized_tracking = requested_tracking or order_state["tracking_number"]
        if not normalized_tracking:
            raise RuntimeError("Brak numeru przesyłki do zamknięcia zlecenia.")

        cursor.execute(
            """
            SELECT FIRST 1 ID_FAKTURA_TABLE, NUMER, ID_WZ
            FROM FAKTURA
            WHERE ID_ZLECENIE = ? AND ROK_ZLECENIA = ? AND RODZAJ_DOK = 'KPSK'
            ORDER BY ID_FAKTURA_TABLE DESC
            """,
            (int(order_id), int(order_year)),
        )
        invoice_row = cursor.fetchone()
        if existing_invoice_id and invoice_row is None:
            cursor.execute(
                "SELECT ID_FAKTURA_TABLE, NUMER, ID_WZ FROM FAKTURA WHERE ID_FAKTURA_TABLE = ?",
                (int(existing_invoice_id),),
            )
            invoice_row = cursor.fetchone()

        cursor.execute(
            """
            SELECT FIRST 1 ID_ZAKUPY_TABLE, NUMER
            FROM ZAKUPY
            WHERE ID_ZLECENIE = ? AND ROK_ZLECENIA = ? AND RODZAJ_DOK = 'WZ'
            ORDER BY ID_ZAKUPY_TABLE DESC
            """,
            (int(order_id), int(order_year)),
        )
        wz_row = cursor.fetchone()
        if existing_wz_id and wz_row is None:
            cursor.execute(
                "SELECT ID_ZAKUPY_TABLE, NUMER FROM ZAKUPY WHERE ID_ZAKUPY_TABLE = ?",
                (int(existing_wz_id),),
            )
            wz_row = cursor.fetchone()

        cursor.execute(
            """
            SELECT FIRST 1 ID_ZAKUPY_TABLE, NUMER
            FROM ZAKUPY
            WHERE ID_ZLECENIE = ? AND ROK_ZLECENIA = ? AND RODZAJ_DOK = 'RW'
            ORDER BY ID_ZAKUPY_TABLE DESC
            """,
            (int(order_id), int(order_year)),
        )
        rw_row = cursor.fetchone()
        if existing_rw_id and rw_row is None:
            cursor.execute(
                "SELECT ID_ZAKUPY_TABLE, NUMER FROM ZAKUPY WHERE ID_ZAKUPY_TABLE = ?",
                (int(existing_rw_id),),
            )
            rw_row = cursor.fetchone()

        if invoice_row is not None and document_mode != "invoice_wz":
            raise RuntimeError("Zlecenie ma już fakturę; nie można utworzyć innego rozliczenia.")
        if existing_rw_id and document_mode != "rw":
            raise RuntimeError("Zlecenie ma już RW; nie można utworzyć WZ ani faktury.")
        if wz_row is not None and document_mode == "rw":
            raise RuntimeError("Zlecenie ma już WZ; nie można dodatkowo utworzyć RW.")

        if invoice_required and invoice_row is not None:
            invoice_id, invoice_number, invoice_wz_id = invoice_row
            wz_id = int(invoice_wz_id) if invoice_wz_id else None
            wz_number = None
            if wz_id:
                cursor.execute("SELECT NUMER FROM ZAKUPY WHERE ID_ZAKUPY_TABLE = ?", (wz_id,))
                row = cursor.fetchone()
                wz_number = _text(row[0]) if row else None
                cursor.execute(
                    "UPDATE ZAKUPY SET DOK_ZEW = ? WHERE ID_ZAKUPY_TABLE = ?",
                    (_text(invoice_number), wz_id),
                )
            cursor.execute(
                """
                UPDATE ZLECENIE
                SET ID_FAKTURA = ?, ID_WZ = NULL, ID_RW = NULL, FAKTURA = ?,
                    PRZESYLKA = ?, STAN = 'Z'
                WHERE ID_ZLECENIE_TABLE = ?
                """,
                (
                    int(invoice_id),
                    _text(invoice_number),
                    normalized_tracking,
                    int(order_table_id),
                ),
            )
            connection.commit()
            return {
                "status": "already_exists",
                "document_mode": document_mode,
                "rw_id": None,
                "rw_number": None,
                "wz_id": wz_id,
                "wz_number": wz_number,
                "invoice_id": int(invoice_id),
                "invoice_number": _text(invoice_number),
            }

        if document_mode == "rw" and rw_row is not None:
            rw_id, rw_number = rw_row
            cursor.execute(
                """
                UPDATE ZLECENIE
                SET ID_RW = ?, ID_WZ = NULL, ID_FAKTURA = NULL, FAKTURA = ?,
                    PRZESYLKA = ?, STAN = 'Z'
                WHERE ID_ZLECENIE_TABLE = ?
                """,
                (
                    int(rw_id),
                    _text(rw_number) or _text(existing_document_text),
                    normalized_tracking,
                    int(order_table_id),
                ),
            )
            connection.commit()
            return {
                "status": "already_exists",
                "document_mode": document_mode,
                "rw_id": int(rw_id),
                "rw_number": _text(rw_number),
                "wz_id": None,
                "wz_number": None,
                "invoice_id": None,
                "invoice_number": None,
            }

        if document_mode == "wz" and wz_row is not None:
            wz_id, wz_number = wz_row
            cursor.execute(
                """
                UPDATE ZLECENIE
                SET ID_WZ = ?, ID_FAKTURA = NULL, ID_RW = NULL, FAKTURA = ?,
                    PRZESYLKA = ?, STAN = 'Z'
                WHERE ID_ZLECENIE_TABLE = ?
                """,
                (
                    int(wz_id),
                    _text(wz_number),
                    normalized_tracking,
                    int(order_table_id),
                ),
            )
            connection.commit()
            return {
                "status": "already_exists",
                "document_mode": document_mode,
                "rw_id": None,
                "rw_number": None,
                "wz_id": int(wz_id),
                "wz_number": _text(wz_number),
                "invoice_id": None,
                "invoice_number": None,
            }

        if not order_state["can_finalize"]:
            raise ShippingOrderStateConflict(
                shipping_order_state_conflict_message(
                    order_state,
                    operation="utworzenie dokumentów i zamknięcie zlecenia",
                )
            )

        item_quantities: dict[int, Decimal] = {}
        item_payloads: dict[int, dict[str, Any]] = {}
        for item in items:
            item_id = int(item["firebird_warehouse_item_id"])
            quantity = Decimal(str(item["quantity"]))
            if quantity <= 0:
                raise RuntimeError(f"Niepoprawna ilość pozycji {item_id} w przesyłce.")
            if item_id in item_payloads:
                raise RuntimeError(f"Pozycja {item_id} występuje w przesyłce więcej niż raz.")
            item_quantities[item_id] = quantity
            item_payloads[item_id] = item
        item_ids = list(item_payloads)
        placeholders = ",".join("?" for _ in item_ids)
        cursor.execute(
            f"""
            SELECT ID_MAGAZYN_TABLE, ID_MAGAZYN, RODZAJ, INDEKS, NAZWA, JM,
                   COALESCE(CENA_NETTO, 0), COALESCE(CENA_Z1, 0),
                   COALESCE(VAT_STAWKA, 23), COALESCE(IDVAT, 1),
                   COALESCE(ILOSC, 0) - COALESCE(IL_REZ, 0)
            FROM MAGAZYN
            WHERE ID_MAGAZYN = ? AND ID_MAGAZYN_TABLE IN ({placeholders})
            """,
            (int(warehouse_id), *item_ids),
        )
        warehouse_by_id = {int(row[0]): row for row in cursor.fetchall()}
        if len(warehouse_by_id) != len(item_ids):
            raise RuntimeError("Nie wszystkie pozycje przesyłki istnieją w magazynie wydającym.")

        document_items: list[dict[str, Any]] = []
        total_net = Decimal("0")
        total_vat = Decimal("0")
        total_gross = Decimal("0")
        for item_id in item_ids:
            warehouse = warehouse_by_id[item_id]
            payload = item_payloads[item_id]
            quantity = item_quantities[item_id]
            (
                _item_id,
                item_warehouse_id,
                item_kind,
                item_index,
                item_name,
                unit,
                catalog_price,
                current_purchase_price,
                current_vat_rate,
                vat_id,
                available,
            ) = warehouse
            stock_already_issued = document_mode == "invoice_wz" and wz_row is not None
            if (
                not stock_already_issued
                and Decimal(str(available or 0)) < quantity
                and payload.get("allow_negative_stock") is not True
            ):
                raise RuntimeError(f"Brak stanu magazynowego dla pozycji {item_name}.")
            selected_price = Decimal(
                str(payload.get("price_net") or payload.get("unit_price_net") or 0)
            )
            if selected_price <= 0:
                raise RuntimeError(f"Brak zaakceptowanej ceny netto pozycji {item_name}.")
            purchase_price = Decimal(
                str(payload.get("purchase_price_net") or current_purchase_price or 0)
            )
            vat_rate_value = _vat_rate(payload.get("vat_rate") or current_vat_rate)
            if document_mode == "invoice_wz":
                _validate_ksef_numeric_vat_rate(vat_rate_value)
            net_value = _money(selected_price * quantity)
            purchase_value = _money(purchase_price * quantity)
            vat_value = _money(net_value * vat_rate_value / Decimal("100"))
            gross_value = _money(net_value + vat_value)
            normalized_kind = _text(item_kind) or "2. Towar inny"
            document_items.append(
                {
                    "item_id": item_id,
                    "warehouse_id": int(item_warehouse_id),
                    "kind": normalized_kind,
                    "index": _text(item_index),
                    "name": _text(item_name) or f"Pozycja {item_id}",
                    "unit": _text(unit) or "szt.",
                    "quantity": quantity,
                    "price_net": selected_price,
                    "purchase_price_net": purchase_price,
                    "net_value": net_value,
                    "purchase_value": purchase_value,
                    "vat_rate": vat_rate_value,
                    "vat_id": int(vat_id or 1),
                    "vat_value": vat_value,
                    "gross_value": gross_value,
                }
            )
            total_net += net_value
            total_vat += vat_value
            total_gross += gross_value

        cursor.execute(
            """
            SELECT FIRST 1 DATA_WYST, DATA_PLAT, PLATNOSC
            FROM FAKTURA
            WHERE ID_KLIENT = ? AND RODZAJ_DOK = 'KPSK'
            ORDER BY ID_FAKTURA_TABLE DESC
            """,
            (int(client_id),),
        )
        payment_row = cursor.fetchone()
        payment_days = 14
        payment_method = "Przelew"
        if payment_row:
            previous_issue, previous_due, previous_method = payment_row
            if isinstance(previous_issue, date) and isinstance(previous_due, date):
                payment_days = max(0, min((previous_due - previous_issue).days, 365))
            payment_method = _text(previous_method) or payment_method
        due_date = document_date + timedelta(days=payment_days)
        operator_name = (_text(issued_by) or "CTIP")[:100]
        order_reference = f"{int(order_id)}/{int(order_year)}"
        receiver = f"Wg zlecenia {order_reference}"[:100]
        document_note = "; ".join(
            value
            for value in (
                f"Zlecenie nr: {order_reference}",
                " ".join(value for value in (_text(device_brand), _text(device_model)) if value),
            )
            if value
        )
        destination = shipping_address or {}
        document_company_name = _text(destination.get("company_name")) or _text(company_name)
        document_street = _text(destination.get("street")) or _text(street)
        document_postal_code = _text(destination.get("postal_code")) or _text(postal_code)
        document_city = _text(destination.get("city")) or _text(city)
        destination_line = ", ".join(
            value
            for value in (
                _text(destination.get("street")),
                " ".join(
                    value
                    for value in (
                        _text(destination.get("postal_code")),
                        _text(destination.get("city")),
                    )
                    if value
                ),
            )
            if value
        )
        if destination_line:
            document_note = f"{document_note}; wysyłka: {destination_line}"
        wz_id: int | None = int(wz_row[0]) if wz_row else None
        wz_number: str | None = _text(wz_row[1]) if wz_row else None

        if document_mode in {"wz", "invoice_wz"} and wz_id is None:
            cursor.execute(
                """
                SELECT COALESCE(MAX(DOKUMENT), 0) + 1
                FROM ZAKUPY
                WHERE ID_FIRMA = ? AND RODZAJ_DOK = 'WZ'
                  AND DATA_WYST BETWEEN ? AND ?
                """,
                (
                    int(company_id),
                    date(document_date.year, 1, 1),
                    date(document_date.year, 12, 31),
                ),
            )
            wz_document = int(cursor.fetchone()[0])
            wz_number = f"WZ / {wz_document} / {document_date.year}"
            cursor.execute(
                """
                INSERT INTO ZAKUPY (
                    ID_ODDZIAL, ID_FIRMA, ID_KLIENT, ID_ZLECENIE, ROK_ZLECENIA,
                    NAZWA, ADRES, KOD, POCZTA, NIP, RODZAJ_DOK, DOKUMENT, NUMER,
                    ID_MP, ID_MW, DATA_SPRZ, DATA_WYST, DATA_PRZY_WYDA, DATA_PLAT,
                    PLATNOSC, MIEJSCE_WYST, WYSTAWIL, ODEBRAL,
                    SUMA_NETTO, SUMA_VAT, SUMA_BRUTTO, UWAGI, KOD_KRAJU
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'WZ', ?, ?, 0, ?,
                          ?, ?, ?, ?, ?, 'Komorniki', ?, ?, ?, ?, ?, ?, 'PL')
                RETURNING ID_ZAKUPY_TABLE
                """,
                (
                    int(branch_id or 1),
                    int(company_id),
                    int(client_id),
                    int(order_id),
                    int(order_year),
                    document_company_name,
                    document_street,
                    document_postal_code,
                    document_city,
                    _text(nip),
                    wz_document,
                    wz_number,
                    int(warehouse_id),
                    document_date,
                    document_date,
                    document_date,
                    due_date,
                    payment_method,
                    operator_name,
                    receiver,
                    _money(total_net),
                    _money(total_vat),
                    _money(total_gross),
                    document_note[:200],
                ),
            )
            wz_id = int(cursor.fetchone()[0])
            for item in document_items:
                cursor.execute(
                    """
                    INSERT INTO ZAKPOZYCJA (
                        ID_ZAKUPY, ID_FIRMA, ID_KLIENT, ID_MAGAZYN,
                        RODZAJ_DOK, NUMER, DATA_PRZY_WYDA, ID_MP, ID_MW,
                        RODZAJ, INDEKS, NAZWA, CENA_NETTO, ILOSC, JM,
                        WARTOSC_NETTO, CENA_Z, WARTOSC_Z, STAWKA_VAT,
                        VAT, IDVAT, WARTOSC_BRUTTO
                    ) VALUES (?, ?, ?, ?, 'WZ', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        wz_id,
                        int(company_id),
                        int(client_id),
                        item["item_id"],
                        wz_number,
                        document_date,
                        int(warehouse_id),
                        item["kind"],
                        item["index"],
                        item["name"],
                        item["price_net"],
                        item["quantity"],
                        item["unit"],
                        item["net_value"],
                        item["purchase_price_net"],
                        item["purchase_value"],
                        _ms_vat_rate_text(item["vat_rate"]),
                        item["vat_value"],
                        item["vat_id"],
                        item["gross_value"],
                    ),
                )

        rw_id: int | None = int(rw_row[0]) if rw_row else None
        rw_number: str | None = _text(rw_row[1]) if rw_row else None
        invoice_id: int | None = None
        invoice_number: str | None = None
        if document_mode == "rw" and rw_id is None:
            cursor.execute(
                """
                SELECT COALESCE(MAX(DOKUMENT), 0) + 1
                FROM ZAKUPY
                WHERE ID_FIRMA = ? AND RODZAJ_DOK = 'RW'
                  AND DATA_WYST BETWEEN ? AND ?
                """,
                (
                    int(company_id),
                    date(document_date.year, 1, 1),
                    date(document_date.year, 12, 31),
                ),
            )
            rw_document = int(cursor.fetchone()[0])
            rw_number = f"RW / {rw_document} / {document_date.year}"
            net_by_vat = [Decimal("0") for _ in range(8)]
            vat_by_rate = [Decimal("0") for _ in range(5)]
            gross_by_vat = [Decimal("0") for _ in range(8)]
            for item in document_items:
                vat_index = max(0, min(int(item["vat_id"]) - 1, 7))
                net_by_vat[vat_index] += item["net_value"]
                gross_by_vat[vat_index] += item["gross_value"]
                if vat_index < len(vat_by_rate):
                    vat_by_rate[vat_index] += item["vat_value"]
            cursor.execute(
                """
                INSERT INTO ZAKUPY (
                    ID_ODDZIAL, ID_FIRMA, ID_KLIENT, ID_ZLECENIE, ROK_ZLECENIA,
                    NAZWA, ADRES, KOD, POCZTA, NIP, RODZAJ_DOK, DOKUMENT, NUMER,
                    ID_MP, ID_MW, DOK_ZEW, DATA_WYST, DATA_PRZY_WYDA, DATA_PLAT,
                    PLATNOSC, MIEJSCE_WYST, WYSTAWIL, ODEBRAL,
                    NETTO1, NETTO2, NETTO3, NETTO4, NETTO5, NETTO6, NETTO7, NETTO8,
                    VAT1, VAT2, VAT3, VAT4, VAT5,
                    BRUTTO1, BRUTTO2, BRUTTO3, BRUTTO4,
                    BRUTTO5, BRUTTO6, BRUTTO7, BRUTTO8,
                    SUMA_NETTO, SUMA_VAT, SUMA_BRUTTO, UWAGI
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'RW', ?, ?, 0, ?, ?, ?, ?, ?,
                          ?, 'Komorniki', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING ID_ZAKUPY_TABLE
                """,
                (
                    int(branch_id or 1),
                    int(company_id),
                    int(client_id),
                    int(order_id),
                    int(order_year),
                    document_company_name,
                    document_street,
                    document_postal_code,
                    document_city,
                    _text(nip),
                    rw_document,
                    rw_number,
                    int(warehouse_id),
                    f"Zlec. {order_reference}",
                    document_date,
                    document_date,
                    due_date,
                    payment_method,
                    operator_name,
                    receiver,
                    *[_money(value) for value in net_by_vat],
                    *[_money(value) for value in vat_by_rate],
                    *[_money(value) for value in gross_by_vat],
                    _money(total_net),
                    _money(total_vat),
                    _money(total_gross),
                    document_note[:200],
                ),
            )
            rw_id = int(cursor.fetchone()[0])
            for item in document_items:
                cursor.execute(
                    """
                    INSERT INTO ZAKPOZYCJA (
                        ID_ZAKUPY, ID_FIRMA, ID_KLIENT, ID_MAGAZYN,
                        RODZAJ_DOK, NUMER, DATA_PRZY_WYDA, ID_MP, ID_MW,
                        RODZAJ, INDEKS, NAZWA, CENA_NETTO, ILOSC, JM,
                        WARTOSC_NETTO, CENA_Z, WARTOSC_Z, STAWKA_VAT,
                        VAT, IDVAT, WARTOSC_BRUTTO
                    ) VALUES (?, ?, ?, ?, 'RW', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        rw_id,
                        int(company_id),
                        int(client_id),
                        item["item_id"],
                        rw_number,
                        document_date,
                        int(warehouse_id),
                        item["kind"],
                        item["index"],
                        item["name"],
                        item["price_net"],
                        item["quantity"],
                        item["unit"],
                        item["net_value"],
                        item["purchase_price_net"],
                        item["purchase_value"],
                        _ms_vat_rate_text(item["vat_rate"]),
                        item["vat_value"],
                        item["vat_id"],
                        item["gross_value"],
                    ),
                )

        if document_mode == "invoice_wz":
            document_kind = "KPSK"
            temporary_number = f"CTIP/{int(order_table_id)}"
            cursor.execute(
                """
                INSERT INTO FAKTURA (
                    ID_ODDZIAL, ID_FIRMA, ID_MAGAZYN, ID_KLIENT, ID_ODBIORCA,
                    ID_MASZYNA, ID_ZLECENIE, ROK_ZLECENIA, ID_WZ,
                    NAZWA, ADRES, KOD, POCZTA, NIP, RODZAJ_DOK, NUMER,
                    DATA_SPRZ, DATA_WYST, DATA_PLAT, DNI_PLAT, PLATNOSC,
                    MIEJSCE_WYST, WYSTAWIL, ODEBRAL,
                    SUMA_NETTO, SUMA_VAT, SUMA_BRUTTO, ZAPLACONO,
                    DO_ZAPLATY, UWAGI, STAN, MAIL, KOD_KRAJU
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, 'Komorniki', ?, ?, 0, 0, 0, 0, 0, ?, '', ?, 'PL')
                RETURNING ID_FAKTURA_TABLE, DOKUMENT
                """,
                (
                    int(branch_id or 1),
                    int(company_id),
                    int(warehouse_id),
                    int(client_id),
                    machine_id,
                    int(order_id) if document_mode == "invoice_wz" else None,
                    int(order_year) if document_mode == "invoice_wz" else None,
                    wz_id if document_mode == "invoice_wz" else None,
                    document_company_name,
                    document_street,
                    document_postal_code,
                    document_city,
                    _text(nip),
                    document_kind,
                    temporary_number,
                    document_date,
                    document_date,
                    due_date if document_mode == "invoice_wz" else document_date,
                    payment_days if document_mode == "invoice_wz" else 0,
                    payment_method if document_mode == "invoice_wz" else "Rozchód",
                    operator_name,
                    receiver,
                    document_note[:2000],
                    "T" if document_mode == "invoice_wz" and destination.get("email") else None,
                ),
            )
            document_id, document_sequence = cursor.fetchone()
            invoice_id = int(document_id)
            invoice_number = f"{int(document_sequence)}/KPSK/{document_date.year}"
            finalized_number = invoice_number
            cursor.execute(
                "UPDATE FAKTURA SET NUMER = ? WHERE ID_FAKTURA_TABLE = ?",
                (finalized_number, int(document_id)),
            )
            cursor.execute(
                "UPDATE ZAKUPY SET DOK_ZEW = ? WHERE ID_ZAKUPY_TABLE = ?",
                (finalized_number, int(wz_id)),
            )
            for item in document_items:
                quantity_from_wz = item["quantity"]
                cursor.execute(
                    """
                    INSERT INTO FPOZYCJA (
                        ID_FAKTURA, ID_FIRMA, ID_KLIENT, ID_MASZYNA,
                        ID_ZLECENIE, ROK_ZLECENIA, ID_MAGAZYN, ID_MAGPOZ,
                        RODZAJ_DOK, NUMER, DATA_SPRZ, RODZAJ, INDEKS, NAZWA,
                        CENA_NETTO, CENA_BRUTTO, CENA_Z, ILOSC, JM,
                        WARTOSC_NETTO, WARTOSC_Z, STAWKA_VAT, VAT, IDVAT,
                        WARTOSC_BRUTTO, POBRANO, ILOSCWZ, PARAGON
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        int(document_id),
                        int(company_id),
                        int(client_id),
                        machine_id,
                        int(order_id),
                        int(order_year),
                        int(warehouse_id),
                        item["item_id"],
                        document_kind,
                        finalized_number,
                        document_date,
                        item["kind"],
                        item["index"],
                        item["name"],
                        item["price_net"],
                        _money(
                            item["price_net"] * (Decimal("1") + item["vat_rate"] / Decimal("100"))
                        ),
                        item["purchase_price_net"],
                        item["quantity"],
                        item["unit"],
                        item["net_value"],
                        item["purchase_value"],
                        _ms_vat_rate_text(item["vat_rate"]),
                        item["vat_value"],
                        item["vat_id"],
                        item["gross_value"],
                        quantity_from_wz,
                        quantity_from_wz,
                    ),
                )
            amount_due = _money(total_gross) if document_mode == "invoice_wz" else Decimal("0")
            cursor.execute(
                """
                UPDATE FAKTURA
                SET SUMA_NETTO = ?, SUMA_VAT = ?, SUMA_BRUTTO = ?, DO_ZAPLATY = ?
                WHERE ID_FAKTURA_TABLE = ?
                """,
                (
                    _money(total_net),
                    _money(total_vat),
                    _money(total_gross),
                    amount_due,
                    int(document_id),
                ),
            )

        if document_mode == "rw":
            order_values = (rw_id, rw_number)
            cursor.execute(
                """
                UPDATE ZLECENIE
                SET ID_RW = ?, ID_WZ = NULL, ID_FAKTURA = NULL, FAKTURA = ?,
                    PRZESYLKA = ?, STAN = 'Z'
                WHERE ID_ZLECENIE_TABLE = ?
                """,
                (
                    order_values[0],
                    order_values[1],
                    normalized_tracking,
                    int(order_table_id),
                ),
            )
        elif document_mode == "wz":
            cursor.execute(
                """
                UPDATE ZLECENIE
                SET ID_WZ = ?, ID_RW = NULL, ID_FAKTURA = NULL, FAKTURA = ?,
                    PRZESYLKA = ?, STAN = 'Z'
                WHERE ID_ZLECENIE_TABLE = ?
                """,
                (
                    wz_id,
                    wz_number,
                    normalized_tracking,
                    int(order_table_id),
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE ZLECENIE
                SET ID_FAKTURA = ?, ID_WZ = NULL, ID_RW = NULL, FAKTURA = ?,
                    PRZESYLKA = ?, STAN = 'Z'
                WHERE ID_ZLECENIE_TABLE = ?
                """,
                (
                    invoice_id,
                    invoice_number,
                    normalized_tracking,
                    int(order_table_id),
                ),
            )
        connection.commit()
        return {
            "status": "created",
            "document_mode": document_mode,
            "rw_id": rw_id,
            "rw_number": rw_number,
            "wz_id": wz_id,
            "wz_number": wz_number,
            "invoice_id": invoice_id,
            "invoice_number": invoice_number,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


__all__ = [
    "DELIVERY_TYPE_ID",
    "DELIVERY_TYPE_NAME",
    "SHIPPING_TECHNICIAN_NAME",
    "ShippingOrderStateConflict",
    "finalize_shipping_order",
    "load_shipping_overdue_invoices",
    "load_shipping_overdue_summaries",
    "load_shipping_order",
    "load_shipping_order_state",
    "load_shipping_queue",
    "load_toner_stock",
    "shipping_document_mode",
    "shipping_order_state_conflict_message",
    "shipping_order_state_payload",
    "validate_shipping_dictionary",
    "write_shipment_to_order",
    "write_shipping_milestones_to_order",
]
