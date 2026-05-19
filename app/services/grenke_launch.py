"""Integracja przygotowania prefillu kalkulacji dla formularza GRENKE."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

import httpx

from app.core.config import settings
from app.models import FormRequest, FormWorkflowCase, FormWorkflowDevice

_VAT_RATE = Decimal("0.23")
_DEVICE_TYPE = "Drukarka IT"
_DEVICE_TYPE_FALLBACK_QUERY = "Drukarka IT"
_RATE_OPTIONS_DEFAULT = ["kwartalna", "miesieczna"]
_MONTHS_CANDIDATES_DESC = ["60", "48", "36", "30", "24", "18"]
_SESSIONDATA_DEFAULT_MONTHS_RE = re.compile(
    r"defaultMonths\s*:\s*['\"]?(\d{1,3})['\"]?",
    re.IGNORECASE,
)
_SESSIONDATA_MIN_MONTHS_RE = re.compile(
    r"minMonths\s*:\s*['\"]?(\d{1,3})['\"]?",
    re.IGNORECASE,
)
_SESSIONDATA_MAX_MONTHS_RE = re.compile(r"maxMonths\s*:\s*['\"]?(\d{2,3})['\"]?", re.IGNORECASE)
_GRENKE_DEFAULT_COUNTRY_BIRTH = "Polska"
_GRENKE_DEFAULT_CITIZENSHIP = "Polskie"
_GRENKE_DOCUMENT_ID = "Dowód osobisty"
_GRENKE_DOCUMENT_PASSPORT = "Paszport"
_GRENKE_DOCUMENT_OTHER = "Inny (np. karta pobytu)"


@dataclass(slots=True)
class GrenkeLaunchResult:
    """Wynik przygotowania uruchomienia formularza GRENKE."""

    url: str
    prefill_state: str
    warnings: list[str]
    session_key: str


@dataclass(slots=True)
class _ProviderDetails:
    """Dane dostawcy przekazywane do formularza GRENKE."""

    enabled: bool
    name: str
    postal_code: str
    nip: str


def _parse_decimal(value: Any) -> Decimal | None:
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace(" ", "").replace(",", ".")
    try:
        parsed = Decimal(normalized)
    except InvalidOperation:
        return None
    if parsed <= 0:
        return None
    return parsed


def _format_amount(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(normalized, "f")


def _format_pln(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{normalized:,.2f}"
    return text.replace(",", " ").replace(".", ",")


def _format_pln_currency(value: Decimal) -> str:
    return f"{_format_pln(value)} zł"


def _normalize_rate_value(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in {"--", "-", "brak", "n/a", "na"}:
        return None
    cleaned = text.replace("zł", "").replace("PLN", "").strip()
    parsed = _parse_decimal(cleaned)
    if parsed is not None:
        return _format_pln_currency(parsed)
    if not any(char.isdigit() for char in cleaned):
        return None
    if text.endswith("zł"):
        return text
    return f"{text} zł"


def _provider_text(value: Any, *, max_length: int = 250) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""
    return text[:max_length]


def _resolve_provider_from_proforma(proforma_id: int | None) -> _ProviderDetails:
    """Pobiera dane dostawcy z zapisanej proformy Firebird (sprzedawca)."""

    numeric_id = int(proforma_id or 0)
    if numeric_id <= 0:
        return _ProviderDetails(enabled=False, name="", postal_code="", nip="")

    try:
        # Lokalny import ogranicza ryzyko cyklicznych zaleznosci.
        from app.services.contracts_proforma import load_proforma_preview_data

        preview = load_proforma_preview_data(numeric_id)
    except Exception:
        return _ProviderDetails(enabled=False, name="", postal_code="", nip="")

    seller = preview.get("seller") if isinstance(preview, dict) else None
    if not isinstance(seller, dict):
        return _ProviderDetails(enabled=False, name="", postal_code="", nip="")

    provider_name = _provider_text(seller.get("name"), max_length=500)
    provider_postal = _provider_text(seller.get("postal_code"), max_length=16)
    provider_nip = _provider_text(seller.get("nip"), max_length=32)
    if not provider_name:
        return _ProviderDetails(enabled=False, name="", postal_code="", nip="")

    return _ProviderDetails(
        enabled=True,
        name=provider_name,
        postal_code=provider_postal,
        nip=provider_nip,
    )


def _device_label(device: FormWorkflowDevice) -> str:
    snapshot = device.snapshot if isinstance(device.snapshot, dict) else {}
    snapshot_name = str(snapshot.get("name") or snapshot.get("description") or "").strip()
    if snapshot_name:
        return snapshot_name
    producer = str(device.producer or "").strip()
    model = str(device.model or "").strip()
    serial = str(device.serial or "").strip()
    ewidencja = str(device.ewidencja or "").strip()
    base = " ".join(part for part in [producer, model] if part).strip()
    if not base:
        base = "Urządzenie"
    if serial:
        return f"{base} S/N :{serial}"
    if ewidencja:
        return f"{base} ({ewidencja})"
    return base


def _device_net_price(device: FormWorkflowDevice) -> Decimal | None:
    net_value = _parse_decimal(device.price_net)
    if net_value is not None:
        return net_value
    gross_value = _parse_decimal(device.price_gross) or _parse_decimal(device.price)
    if gross_value is None:
        return None
    return (gross_value / (Decimal("1.00") + _VAT_RATE)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def _build_products_payload(
    workflow_devices: list[FormWorkflowDevice],
) -> tuple[list[dict[str, Any]], Decimal]:
    products: list[dict[str, Any]] = []
    total_net = Decimal("0.00")

    for device in workflow_devices:
        net_price = _device_net_price(device)
        if net_price is None:
            continue
        total_net += net_price
        price_text = _format_pln(net_price)
        products.append(
            {
                "type": _DEVICE_TYPE,
                "name": _device_label(device),
                "price": price_text,
                "quantity": "1",
                "equal": price_text,
                "amortization": 0.3,
                "added": True,
                "upload": True,
            }
        )

    if not products:
        products.append(
            {
                "type": _DEVICE_TYPE,
                "name": "Urządzenie z workflow CTIP",
                "price": "0,00",
                "quantity": "1",
                "equal": "0,00",
                "amortization": 0.3,
                "added": True,
                "upload": True,
            }
        )

    products.append(
        {
            "type": "",
            "name": "",
            "price": "",
            "quantity": "",
            "equal": "",
            "amortization": "",
            "added": False,
        }
    )
    return products, total_net.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _parse_form_date_to_iso(value: Any) -> str:
    """Konwertuje datę z formularza CTIP do formatu YYYY-MM-DD oczekiwanego przez GRENKE."""
    text = str(value or "").strip()
    if not text:
        return ""
    for date_format in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d:%m:%Y"):
        try:
            parsed = datetime.strptime(text, date_format)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _normalize_document_type(value: Any) -> str:
    """Mapuje typ dokumentu CTIP do słownika wartości używanego w formularzu GRENKE."""
    text = _provider_text(value, max_length=80)
    lowered = text.lower()
    if "paszport" in lowered:
        return _GRENKE_DOCUMENT_PASSPORT
    if "dow" in lowered:
        return _GRENKE_DOCUMENT_ID
    if text:
        return _GRENKE_DOCUMENT_OTHER
    return _GRENKE_DOCUMENT_ID


def _normalize_pesel(value: Any) -> str:
    """Zwraca PESEL z samych cyfr, wyłącznie gdy ma 11 znaków."""
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) == 11:
        return digits
    return ""


def _normalize_phone(value: Any) -> str:
    """Czyści numer telefonu do znaków akceptowanych przez formularz GRENKE."""
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = re.sub(r"[^0-9+]", "", text)
    return normalized[:32]


def _build_registered_address(payload: dict[str, Any]) -> str:
    """Buduje pojedynczą linię adresu z danych siedziby formularza CTIP."""
    street = _provider_text(payload.get("registered_street"), max_length=120)
    building = _provider_text(payload.get("registered_building_no"), max_length=32)
    apartment = _provider_text(payload.get("registered_apartment_no"), max_length=32)
    postal = _provider_text(payload.get("registered_postal_code"), max_length=32)
    city = _provider_text(payload.get("registered_city"), max_length=120)
    line_one = " ".join(part for part in [street, building] if part).strip()
    if apartment:
        line_one = f"{line_one} lok. {apartment}".strip()
    line_two = " ".join(part for part in [postal, city] if part).strip()
    return " ".join(part for part in [line_one, line_two] if part).strip()[:240]


def _split_name(full_name: str) -> tuple[str, str]:
    """Rozdziela nazwę pełną na imię i nazwisko (fallback, gdy brak reprezentantów)."""
    parts = [part for part in full_name.split(" ") if part]
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def _build_authorized_person(
    representative: dict[str, Any],
    *,
    fallback_address: str,
) -> dict[str, Any]:
    """Mapuje jednego reprezentanta CTIP do obiektu `authorizedPersons` GRENKE."""
    email = _provider_text(representative.get("representative_email"), max_length=180)
    phone = _normalize_phone(representative.get("representative_phone"))
    return {
        "name": _provider_text(representative.get("first_name"), max_length=120),
        "surname": _provider_text(representative.get("last_name"), max_length=120),
        "pesel": _normalize_pesel(representative.get("pesel")),
        "id": _provider_text(representative.get("document_number"), max_length=80),
        "document": _normalize_document_type(representative.get("document_type")),
        "countryBirth": _GRENKE_DEFAULT_COUNTRY_BIRTH,
        "citizenship": _GRENKE_DEFAULT_CITIZENSHIP,
        "citizenship2Status": False,
        "citizenship2": "",
        "idReleaseDate": _parse_form_date_to_iso(representative.get("document_issue_date")),
        "idExpirationDate": _parse_form_date_to_iso(representative.get("document_expiry_date")),
        "address": fallback_address,
        "maritalStatus": "",
        "phone": phone,
        "email": email,
    }


def _build_authorized_people_payload(
    form: FormRequest,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], dict[str, str], list[str]]:
    """Przygotowuje payload kroku 3 GRENKE na podstawie danych reprezentantów z CTIP."""
    warnings: list[str] = []
    payload: dict[str, Any] = {}
    try:
        from app.services import form_generator

        decoded_payload, _ = form_generator.decode_submitted_payload(form)
        if isinstance(decoded_payload, dict):
            payload = decoded_payload
    except Exception as exc:
        warnings.append(f"submitted_payload: {exc}")

    fallback_address = _build_registered_address(payload)
    raw_representatives = payload.get("representatives")
    representatives: list[dict[str, Any]] = (
        [item for item in raw_representatives if isinstance(item, dict)]
        if isinstance(raw_representatives, list)
        else []
    )
    authorized_persons = [
        _build_authorized_person(representative, fallback_address=fallback_address)
        for representative in representatives
    ]

    if not authorized_persons:
        fallback_name = _provider_text(form.customer_name, max_length=240)
        first_name, last_name = _split_name(fallback_name)
        authorized_persons = [
            {
                "name": first_name,
                "surname": last_name,
                "pesel": "",
                "id": "",
                "document": _GRENKE_DOCUMENT_ID,
                "countryBirth": _GRENKE_DEFAULT_COUNTRY_BIRTH,
                "citizenship": _GRENKE_DEFAULT_CITIZENSHIP,
                "citizenship2Status": False,
                "citizenship2": "",
                "idReleaseDate": "",
                "idExpirationDate": "",
                "address": fallback_address,
                "maritalStatus": "",
                "phone": _normalize_phone(form.customer_phone),
                "email": _provider_text(form.customer_email, max_length=180),
            }
        ]

    first_person = authorized_persons[0]
    auth_fields = {
        "authName": str(first_person.get("name") or ""),
        "authSurname": str(first_person.get("surname") or ""),
        "authAddress": str(first_person.get("address") or ""),
        "authPesel": str(first_person.get("pesel") or ""),
        "authId": str(first_person.get("id") or ""),
    }
    esign_persons = [
        {
            "name": str(person.get("name") or ""),
            "surname": str(person.get("surname") or ""),
            "email": str(person.get("email") or ""),
            "phone": str(person.get("phone") or ""),
        }
        for person in authorized_persons
    ]
    return authorized_persons, esign_persons, auth_fields, warnings


def _base_launch_url(session_key: str) -> str:
    app_base = settings.grenke_app_base_url.rstrip("/")
    return f"{app_base}/kalkulacja/{session_key}"


def _encode_legacy_query_value(value: Any) -> str:
    """Koduje wartosc query zgodnie z parserem decodeURI po stronie GRENKE.

    decodeURI nie odkodowuje niektorych znakow zarezerwowanych (np. `%2F`, `%3A`),
    dlatego trzymamy `/`, `:`, `,` w postaci literalnej oraz kodujemy tylko
    separatory query (`&`, `=`, `#`) i znaki niedozwolone.
    """

    encoded = quote(str(value or ""), safe="/:,+@$;?()!*'")
    return encoded.replace("&", "%26").replace("=", "%3D").replace("#", "%23")


def _build_legacy_query(query: dict[str, Any]) -> str:
    pairs: list[str] = []
    for key, value in query.items():
        encoded_key = quote(str(key), safe="")
        encoded_value = _encode_legacy_query_value(value)
        pairs.append(f"{encoded_key}={encoded_value}")
    return "&".join(pairs)


def _extract_rate_options(calculate_data: Any) -> list[str]:
    options: list[str] = []

    def _add_option(value: Any) -> None:
        normalized = str(value or "").strip().lower()
        if normalized in {"kwartalna", "miesieczna"} and normalized not in options:
            options.append(normalized)

    if isinstance(calculate_data, dict):
        raw_options = calculate_data.get("rate") or calculate_data.get("rates")
        if isinstance(raw_options, list):
            for item in raw_options:
                _add_option(item)

        result = calculate_data.get("result")
        if isinstance(result, dict):
            if _normalize_rate_value(result.get("quater")) is not None:
                _add_option("kwartalna")
            if _normalize_rate_value(result.get("month")) is not None:
                _add_option("miesieczna")
    elif isinstance(calculate_data, list):
        for item in calculate_data:
            _add_option(item)

    if not options:
        return _RATE_OPTIONS_DEFAULT.copy()

    for required in _RATE_OPTIONS_DEFAULT:
        if required not in options:
            options.append(required)
    return options


def _extract_final_rate(calculate_data: Any, *, rate_frequency: str) -> str:
    if not isinstance(calculate_data, dict):
        return "0,00 zł"
    result = calculate_data.get("result")
    if not isinstance(result, dict):
        return "0,00 zł"

    preferred_key = "quater" if rate_frequency == "kwartalnie" else "month"
    candidates = [
        result.get(preferred_key),
        result.get("quater"),
        result.get("month"),
    ]
    for candidate in candidates:
        normalized = _normalize_rate_value(candidate)
        if normalized is not None:
            return normalized
    return "0,00 zł"


def _has_calculated_rate(calculate_data: Any) -> bool:
    if not isinstance(calculate_data, dict):
        return False
    result = calculate_data.get("result")
    if not isinstance(result, dict):
        return False
    return (
        _normalize_rate_value(result.get("quater")) is not None
        or _normalize_rate_value(result.get("month")) is not None
    )


def _extract_remote_int(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if not match:
        return None
    try:
        parsed = int(match.group(1))
    except ValueError:
        return None
    if parsed <= 0:
        return None
    return parsed


async def _fetch_remote_lease_bounds(
    client: httpx.AsyncClient,
    *,
    app_base: str,
) -> tuple[int | None, int | None, int | None]:
    """Pobiera default/min/max okresu leasingu z AppConfig GRENKE."""

    url = f"{app_base.rstrip('/')}/static/media/sessiondata.f74cbf5d.php"
    response = await client.get(url)
    response.raise_for_status()
    text = response.text
    default_months = _extract_remote_int(_SESSIONDATA_DEFAULT_MONTHS_RE, text)
    min_months = _extract_remote_int(_SESSIONDATA_MIN_MONTHS_RE, text)
    max_months = _extract_remote_int(_SESSIONDATA_MAX_MONTHS_RE, text)
    return default_months, min_months, max_months


def _months_candidates(max_months: int | None) -> list[str]:
    candidates: list[str] = []
    for value in _MONTHS_CANDIDATES_DESC:
        if max_months is not None and int(value) > max_months:
            continue
        candidates.append(value)
    return candidates or _MONTHS_CANDIDATES_DESC.copy()


def _fallback_launch_url(session_key: str, products: list[dict[str, Any]]) -> str:
    base = _base_launch_url(session_key)
    first = next((item for item in products if item.get("added")), None)
    if not first:
        return base
    query = {
        "p": str(first.get("name") or "Urządzenie"),
        "c": str(first.get("price") or "0,00").replace(" ", "").replace(",", "."),
        "k": _DEVICE_TYPE_FALLBACK_QUERY,
        "paramsUrl": "1",
    }
    return f"{base}?{_build_legacy_query(query)}"


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
) -> tuple[bool, Any]:
    response = await client.post(url, json=payload)
    response.raise_for_status()
    try:
        return True, response.json()
    except ValueError:
        return True, None


async def launch_grenke_prefill(
    *,
    form: FormRequest,
    workflow_case: FormWorkflowCase,
    workflow_devices: list[FormWorkflowDevice],
) -> GrenkeLaunchResult:
    """Buduje i zapisuje kalkulację GRENKE, zwraca URL do otwarcia w nowym oknie."""

    session_key = secrets.token_hex(8)
    warnings: list[str] = []
    products, total_net = _build_products_payload(workflow_devices)
    provider_details = _resolve_provider_from_proforma(workflow_case.proforma_firebird_id)
    total_gross = (total_net * (Decimal("1.00") + _VAT_RATE)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    (
        authorized_persons,
        esign_persons,
        auth_fields,
        authorized_people_warnings,
    ) = _build_authorized_people_payload(form)
    warnings.extend(authorized_people_warnings)

    calculation_payload_base = {
        "type": "nowoczesny",
        "value": _format_amount(total_net),
        "price": "350.00",
        "initial": "0",
        "products": products,
    }
    rate_frequency = "kwartalnie"
    calculate_result_payload: Any = None
    selected_months = _MONTHS_CANDIDATES_DESC[0]
    min_months_value = 18
    max_months_value = int(_MONTHS_CANDIDATES_DESC[0])
    default_months_value = int(_MONTHS_CANDIDATES_DESC[1])

    api_base = settings.grenke_api_base_url.rstrip("/")
    timeout = max(float(settings.grenke_timeout_seconds), 5.0)
    timeout_config = httpx.Timeout(timeout, connect=min(timeout, 10.0))

    async with httpx.AsyncClient(timeout=timeout_config) as client:
        try:
            await _post_json(
                client,
                f"{api_base}/setSession.php",
                {"key": session_key},
            )
        except (httpx.HTTPError, ValueError) as exc:
            warnings.append(f"setSession.php: {exc}")

        app_base = settings.grenke_app_base_url.rstrip("/")
        remote_default_months: int | None = None
        remote_min_months: int | None = None
        remote_max_months: int | None = None
        try:
            (
                remote_default_months,
                remote_min_months,
                remote_max_months,
            ) = await _fetch_remote_lease_bounds(
                client,
                app_base=app_base,
            )
        except Exception:
            remote_default_months = None
            remote_min_months = None
            remote_max_months = None

        months_candidates = _months_candidates(remote_max_months)
        if remote_min_months is not None and remote_min_months > 0:
            filtered_candidates = [
                month for month in months_candidates if int(month) >= remote_min_months
            ]
            if filtered_candidates:
                months_candidates = filtered_candidates

        min_months_value = remote_min_months or 18
        max_months_value = remote_max_months or int(months_candidates[0])
        if max_months_value < min_months_value:
            max_months_value = min_months_value
        default_months_value = remote_default_months or max_months_value
        default_months_value = min(max(default_months_value, min_months_value), max_months_value)
        months_list_value = [
            month
            for month in _MONTHS_CANDIDATES_DESC
            if min_months_value <= int(month) <= max_months_value
        ]

        calculate_warning: str | None = None
        first_calculate_payload: Any = None
        for candidate_months in months_candidates:
            calculation_payload = {
                **calculation_payload_base,
                "months": candidate_months,
            }
            try:
                ok, calculate_data = await _post_json(
                    client,
                    f"{api_base}/calculate.php",
                    calculation_payload,
                )
            except (httpx.HTTPError, ValueError) as exc:
                calculate_warning = f"calculate.php({candidate_months}): {exc}"
                continue
            if not ok or calculate_data is None:
                continue
            if first_calculate_payload is None:
                first_calculate_payload = calculate_data
            if _has_calculated_rate(calculate_data):
                selected_months = candidate_months
                calculate_result_payload = calculate_data
                break
        if calculate_result_payload is None:
            calculate_result_payload = first_calculate_payload
        if calculate_result_payload is None and calculate_warning:
            warnings.append(calculate_warning)

        rate_payload = _extract_rate_options(calculate_result_payload)
        final_rate = _extract_final_rate(
            calculate_result_payload,
            rate_frequency=rate_frequency,
        )
        save_payload = {
            "leasingTime": str(selected_months),
            "productPrice": _format_amount(total_net),
            "emailProductPrice": f"{_format_pln(total_net)} zł",
            "productPriceEnd": _format_amount(total_gross),
            "tax": "23",
            "selectedLease": "nowoczesny",
            "modernLease": True,
            "traditionalLease": False,
            "minMonth": str(min_months_value),
            "maxMonth": str(max_months_value),
            "minMonths": min_months_value,
            "maxMonths": max_months_value,
            "defaultMonths": str(default_months_value),
            "monthsList": months_list_value,
            "finalRate": final_rate,
            "rateFrequency": rate_frequency,
            "provider": provider_details.enabled,
            "providerName": provider_details.name,
            "providerPostal": provider_details.postal_code,
            "providerNip": provider_details.nip,
            "legalForm": True,
            "callNumber": str(form.customer_phone or ""),
            "callEmail": str(form.customer_email or ""),
            "isShop": False,
            "chargePrice": "0,00 zł",
            "products": products,
            "initialCharge": "0%",
            "calculationKey": session_key,
            "openedMail": 0,
            "sendedRepeatMail": 0,
            "clientEmail": str(form.customer_email or ""),
            "allowForm": True,
            "rate": json.dumps(rate_payload, ensure_ascii=False),
            "urlFormFinish": settings.grenke_app_base_url.rstrip("/"),
            "courier": False,
            "modalAxaText": None,
            "authorizedPersons": authorized_persons,
            "representatives": authorized_persons,
            "authName": auth_fields["authName"],
            "authSurname": auth_fields["authSurname"],
            "authAddress": auth_fields["authAddress"],
            "authPesel": auth_fields["authPesel"],
            "authId": auth_fields["authId"],
            "esignPersons": esign_persons,
            "paramsUrl": 1,
            "ctipFormId": int(form.id),
            "ctipWorkflowCaseId": int(workflow_case.id),
        }

        save_ok = False
        try:
            save_ok, _ = await _post_json(
                client,
                f"{api_base}/saveCalculation.php",
                save_payload,
            )
        except (httpx.HTTPError, ValueError) as exc:
            warnings.append(f"saveCalculation.php: {exc}")
            save_ok = False

    prefill_state = "full" if save_ok and not warnings else "partial"
    launch_url = (
        _base_launch_url(session_key)
        if save_ok
        else _fallback_launch_url(
            session_key,
            products,
        )
    )
    return GrenkeLaunchResult(
        url=launch_url,
        prefill_state=prefill_state,
        warnings=warnings,
        session_key=session_key,
    )


__all__ = ["GrenkeLaunchResult", "launch_grenke_prefill"]
