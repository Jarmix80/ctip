"""Adapter oficjalnego DPD Services REST z bezpiecznym trybem lokalnym."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import settings
from app.services.shipping_documents import build_mock_shipping_label

DPD_DEMO_URL = "https://dpdservicesdemo.dpd.com.pl"
DPD_PRODUCTION_URL = "https://dpdservices.dpd.com.pl"
DPD_MODES = {"mock", "demo", "production"}
DPD_LABEL_TEXT_LIMIT = 81

_API_FIELD_LIMITS = {
    "company": 100,
    "name": 100,
    "address": 100,
    "city": 50,
    "countryCode": 2,
    "postalCode": 10,
    "phone": 100,
    "email": 100,
}
_LABEL_FIELD_LIMITS = {
    "receiver.company": 57,
    "receiver.name": 60,
    "receiver.address": 30,
    "receiver.city": 37,
    "receiver.phone": 30,
    "sender.company": 37,
    "sender.name": 38,
    "sender.address": 36,
    "sender.city": 28,
    "sender.phone": 31,
    "ref1": 27,
    "ref2": 27,
    "ref3": 27,
    "content": 54,
}
_FIELD_LABELS = {
    "company": "nazwa firmy",
    "name": "osoba kontaktowa",
    "address": "ulica i numer",
    "city": "miejscowość",
    "countryCode": "kod kraju",
    "postalCode": "kod pocztowy",
    "phone": "telefon",
    "email": "e-mail",
}


def dpd_recipient_field_limits() -> dict[str, dict[str, int | None]]:
    """Zwraca limity danych odbiorcy wspólne dla backendu i formularza Shipping."""
    return {
        "company_name": {
            "api_limit": _API_FIELD_LIMITS["company"],
            "label_limit": _LABEL_FIELD_LIMITS["receiver.company"],
        },
        "contact_name": {
            "api_limit": _API_FIELD_LIMITS["name"],
            "label_limit": _LABEL_FIELD_LIMITS["receiver.name"],
        },
        "street": {
            "api_limit": _API_FIELD_LIMITS["address"],
            "label_limit": _LABEL_FIELD_LIMITS["receiver.address"],
        },
        "city": {
            "api_limit": _API_FIELD_LIMITS["city"],
            "label_limit": _LABEL_FIELD_LIMITS["receiver.city"],
        },
        "email": {
            "api_limit": _API_FIELD_LIMITS["email"],
            "label_limit": None,
        },
    }


_DPD_ERROR_LABELS = {
    "DISABLED_API": "DPD wyłączyło dostęp do tej metody API.",
    "DISALLOWED_FID": "Podany FID nie ma dostępu do operacji DPD.",
    "ERROR_PAYER_BLOCKED": "DPD zablokowało konto płatnika.",
    "ERROR_NO_FID_PERMISSION": "Podany FID nie ma wymaganych uprawnień.",
    "DUPLICATED_PACKAGE_REFERENCE": "DPD ma już paczkę z tą referencją techniczną.",
    "DUPLICATED_PARCEL_REFERENCE": "DPD ma już przesyłkę z tą referencją techniczną.",
    "INCORRECT_DATA": "DPD odrzuciło dane przesyłki.",
    "INCORRECT_WEIGHT": "Waga paczki jest nieprawidłowa.",
    "INCORRECT_RECEIVER_POSTAL_CODE": "Kod pocztowy odbiorcy jest nieprawidłowy.",
    "INCORRECT_SENDER_POSTAL_CODE": "Kod pocztowy nadawcy jest nieprawidłowy.",
    "INCORRECT_RECEIVER_ADDRESS": "Adres odbiorcy jest nieprawidłowy.",
    "INCORRECT_RECEIVER_CITY": "Miejscowość odbiorcy jest nieprawidłowa.",
    "NOT_FOUND": "DPD nie znalazło wskazanej przesyłki lub etykiety.",
    "NOT_PROCESSED": "DPD nie przetworzyło żądania.",
    "SERVICE_UNAVAILABLE": "Usługa DPD jest chwilowo niedostępna.",
    "UNKNOWN_ERROR": "DPD zwróciło nieokreślony błąd.",
}


class DpdConfigurationError(RuntimeError):
    """Błąd niekompletnej albo niebezpiecznej konfiguracji DPD."""


class DpdTransportError(RuntimeError):
    """Błąd komunikacji lub odpowiedzi DPD wraz z danymi do odzyskania."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        request_payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.request_payload = request_payload


@dataclass(slots=True, frozen=True)
class DpdLabelResult:
    """Dokument etykiet zwrócony przez `generateSpedLabels`."""

    label_content: bytes
    document_id: str | None
    session_id: str | None
    trace_id: str | None
    waybills: tuple[str, ...]
    raw_response: dict[str, Any]


@dataclass(slots=True, frozen=True)
class DpdShipmentResult:
    """Ujednolicony rezultat utworzenia przesyłki i pobrania etykiety."""

    shipment_id: str
    tracking_number: str
    label_content: bytes
    label_content_type: str
    label_format: str
    raw_response: dict[str, Any]
    warnings: tuple[str, ...] = ()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_int(value: Any, setting_name: str) -> int:
    try:
        parsed = int(_text(value))
    except ValueError as exc:
        raise DpdConfigurationError(
            f"Ustawienie {setting_name} musi być liczbą całkowitą."
        ) from exc
    if parsed <= 0:
        raise DpdConfigurationError(f"Ustawienie {setting_name} musi być większe od zera.")
    return parsed


def _dpd_postal_code(value: Any) -> str:
    """Normalizuje polski kod pocztowy do pięciu cyfr wymaganych przez DPD REST."""
    postal_code = _text(value)
    if re.fullmatch(r"\d{2}-\d{3}", postal_code):
        return postal_code.replace("-", "")
    return postal_code


def _technical_reference(prefix: str, idempotency_key: str) -> str:
    token = re.sub(r"[^0-9A-Za-z-]+", "-", _text(idempotency_key)).strip("-")
    candidate = f"{prefix}-{token}"
    if token and len(candidate) <= 50:
        return candidate
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{digest}"


def _business_reference_fields(values: list[str]) -> dict[str, str]:
    unique_values = list(dict.fromkeys(_text(value) for value in values if _text(value)))
    if not unique_values:
        unique_values = ["CTIP"]
    chunks: list[str] = []
    current = ""
    for value in unique_values:
        candidate = f"{current}, {value}" if current else value
        if len(candidate) <= 100:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(value) > 100:
            raise DpdConfigurationError(
                "Numer lub opis referencyjny zlecenia przekracza limit 100 znaków DPD."
            )
        current = value
    if current:
        chunks.append(current)
    if len(chunks) > 3:
        raise DpdConfigurationError(
            "Lista numerów zleceń nie mieści się w polach ref1, ref2 i ref3 DPD."
        )
    return {f"ref{index}": value for index, value in enumerate(chunks, start=1)}


def normalize_dpd_label_text(value: Any) -> str:
    """Normalizuje wspólną treść dwóch drukowanych pól etykiety DPD."""
    normalized = " ".join(str(value or "").split())
    if not normalized:
        raise DpdConfigurationError("Treść etykiety DPD nie może być pusta.")
    if len(normalized) > DPD_LABEL_TEXT_LIMIT:
        raise DpdConfigurationError(
            f"Treść etykiety DPD może mieć maksymalnie {DPD_LABEL_TEXT_LIMIT} znaków."
        )
    return normalized


def split_dpd_label_text(value: Any) -> tuple[str, str]:
    """Dzieli treść na drukowane pola `ref1` i `content` bez utraty znaków."""
    normalized = normalize_dpd_label_text(value)
    reference_limit = _LABEL_FIELD_LIMITS["ref1"]
    content_limit = _LABEL_FIELD_LIMITS["content"]
    if len(normalized) <= reference_limit:
        return normalized, ""
    minimum_split = max(1, len(normalized) - content_limit)
    split_at = reference_limit
    for index in range(reference_limit, minimum_split - 1, -1):
        if normalized[index - 1].isspace():
            split_at = index - 1
            break
    reference = normalized[:split_at].rstrip()
    content = normalized[split_at:].lstrip()
    if not reference or len(content) > content_limit:
        reference = normalized[:reference_limit]
        content = normalized[reference_limit:]
    return reference, content


def _parcel_content(items: list[dict[str, Any]] | None) -> str:
    """Buduje krótki opis części zgodny z limitem pola etykiety DPD."""
    if not items:
        return "Materiały serwisowe"
    descriptions = []
    for item in items:
        try:
            quantity = f"{float(item.get('quantity') or 0):g}"
        except (TypeError, ValueError):
            quantity = _text(item.get("quantity")) or "1"
        item_name = _text(item.get("item_name"))
        item_index = _text(item.get("item_index"))
        identity = item_name or item_index or "część"
        descriptions.append(f"{quantity}x {identity}")
    content = "; ".join(descriptions)
    limit = _LABEL_FIELD_LIMITS["content"]
    if len(content) <= limit:
        return content
    return content[: limit - 1].rstrip() + "…"


def _without_empty_values(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _sanitize_response(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _sanitize_response(item)
            for key, item in value.items()
            if key.casefold() != "documentdata"
        }
    if isinstance(value, list):
        return [_sanitize_response(item) for item in value]
    return value


def _validation_details(value: Any) -> list[str]:
    details: list[str] = []
    if isinstance(value, dict):
        code = _text(value.get("errorCode") or value.get("code")).upper()
        info = _text(value.get("info") or value.get("description"))
        fields = _text(value.get("fields"))
        if code:
            label = _DPD_ERROR_LABELS.get(code, f"Kod DPD: {code}.")
            suffix = " ".join(part for part in (info, f"Pole: {fields}." if fields else "") if part)
            details.append(f"{label}{' ' + suffix if suffix else ''}".strip())
        for item in value.values():
            details.extend(_validation_details(item))
    elif isinstance(value, list):
        for item in value:
            details.extend(_validation_details(item))
    return list(dict.fromkeys(details))


def _response_error(operation: str, payload: dict[str, Any]) -> str:
    status = _text(payload.get("status")).upper()
    details = _validation_details(payload)
    if not details and status:
        details = [_DPD_ERROR_LABELS.get(status, f"Status DPD: {status}.")]
    trace_id = _text(payload.get("traceId"))
    message = f"DPD odrzuciło operację „{operation}”."
    if details:
        message += " " + " ".join(details)
    if trace_id:
        message += f" Identyfikator diagnostyczny: {trace_id}."
    return message


class DpdShippingClient:
    """Obsługuje krajową przesyłkę jednopaczkową przez DPD Services REST."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        mode: str | None = None,
    ) -> None:
        configured_mode = _text(mode or settings.dpd_effective_mode).lower()
        self.mode = "production" if configured_mode == "live" else configured_mode
        self.test_mode = self.mode == "mock"
        self.transport = transport
        configured_url = _text(settings.dpd_api_url).rstrip("/")
        self.base_url = configured_url or (
            DPD_DEMO_URL if self.mode == "demo" else DPD_PRODUCTION_URL
        )

    @property
    def is_nonproduction(self) -> bool:
        """Informuje, czy klient działa poza środowiskiem produkcyjnym DPD."""
        return self.mode in {"mock", "demo"}

    def configuration_status(self) -> dict[str, Any]:
        """Zwraca stan integracji bez ujawniania sekretów."""
        sender_ready = all(
            (
                settings.dpd_sender_company,
                settings.dpd_sender_street,
                settings.dpd_sender_postal_code,
                settings.dpd_sender_city,
                settings.dpd_sender_phone,
            )
        )
        credentials_ready = all(
            (
                settings.dpd_login,
                settings.dpd_password,
                settings.dpd_master_fid,
                settings.dpd_payer_fid,
            )
        )
        return {
            "enabled": bool(settings.dpd_enabled),
            "mode": self.mode,
            "test_mode": self.test_mode,
            "api_ready": self.test_mode or bool(credentials_ready),
            "sender_ready": bool(sender_ready),
            "base_url": self.base_url if self.mode != "mock" else None,
            "demo_receiver_override": self.mode == "demo",
            "recipient_field_limits": dpd_recipient_field_limits(),
        }

    def _validate(self) -> None:
        if not settings.dpd_enabled:
            raise DpdConfigurationError("Integracja DPD jest wyłączona (`DPD_ENABLED=false`).")
        if self.mode not in DPD_MODES:
            raise DpdConfigurationError(
                "DPD_MODE musi mieć wartość `mock`, `demo` albo `production`."
            )
        if self.test_mode:
            return
        expected_host = urlsplit(
            DPD_DEMO_URL if self.mode == "demo" else DPD_PRODUCTION_URL
        ).hostname
        configured = urlsplit(self.base_url)
        if configured.scheme != "https" or configured.hostname != expected_host:
            raise DpdConfigurationError(
                f"Tryb DPD {self.mode} wymaga oficjalnego adresu "
                f"{DPD_DEMO_URL if self.mode == 'demo' else DPD_PRODUCTION_URL}."
            )
        missing = [
            name
            for name, value in (
                ("DPD_LOGIN", settings.dpd_login),
                ("DPD_PASSWORD", settings.dpd_password),
                ("DPD_MASTER_FID", settings.dpd_master_fid),
                ("DPD_PAYER_FID", settings.dpd_payer_fid),
                ("DPD_SENDER_COMPANY", settings.dpd_sender_company),
                ("DPD_SENDER_STREET", settings.dpd_sender_street),
                ("DPD_SENDER_POSTAL_CODE", settings.dpd_sender_postal_code),
                ("DPD_SENDER_CITY", settings.dpd_sender_city),
                ("DPD_SENDER_PHONE", settings.dpd_sender_phone),
            )
            if not _text(value)
        ]
        if missing:
            raise DpdConfigurationError("Brak wymaganych ustawień DPD: " + ", ".join(missing) + ".")
        _positive_int(settings.dpd_master_fid, "DPD_MASTER_FID")
        _positive_int(settings.dpd_payer_fid, "DPD_PAYER_FID")

    def _sender(self) -> dict[str, Any]:
        if self.test_mode:
            fallback = {
                "company": _text(settings.dpd_sender_company) or "Ksero-Partner Test",
                "name": _text(settings.dpd_sender_contact) or "Operator Testowy",
                "address": _text(settings.dpd_sender_street) or "Testowa 1",
                "postalCode": _dpd_postal_code(settings.dpd_sender_postal_code) or "00001",
                "city": _text(settings.dpd_sender_city) or "Warszawa",
                "countryCode": "PL",
                "phone": _text(settings.dpd_sender_phone) or "500600700",
                "email": _text(settings.dpd_sender_email) or "test@example.com",
            }
            self._validate_address("nadawcy", fallback)
            return fallback
        sender = _without_empty_values(
            {
                "company": _text(settings.dpd_sender_company),
                "name": _text(settings.dpd_sender_contact),
                "address": _text(settings.dpd_sender_street),
                "postalCode": _dpd_postal_code(settings.dpd_sender_postal_code),
                "city": _text(settings.dpd_sender_city),
                "countryCode": "PL",
                "phone": _text(settings.dpd_sender_phone),
                "email": _text(settings.dpd_sender_email),
            }
        )
        self._validate_address("nadawcy", sender)
        return sender

    @staticmethod
    def _receiver(receiver: dict[str, Any]) -> dict[str, Any]:
        return _without_empty_values(
            {
                "company": _text(receiver.get("company_name")),
                "name": _text(receiver.get("contact_name")),
                "address": _text(receiver.get("street")),
                "postalCode": _dpd_postal_code(receiver.get("postal_code")),
                "city": _text(receiver.get("city")),
                "countryCode": _text(receiver.get("country_code")) or "PL",
                "phone": _text(receiver.get("phone")),
                "email": _text(receiver.get("email")),
            }
        )

    @staticmethod
    def _validate_address(role: str, address: dict[str, Any]) -> None:
        for required in ("address", "postalCode", "city", "countryCode"):
            if not _text(address.get(required)):
                raise DpdConfigurationError(
                    f"Brak pola „{_FIELD_LABELS[required]}” w danych {role}."
                )
        if not (_text(address.get("company")) or _text(address.get("name"))):
            raise DpdConfigurationError(f"Brak nazwy firmy lub osoby w danych {role}.")
        for field, limit in _API_FIELD_LIMITS.items():
            value = _text(address.get(field))
            if len(value) > limit:
                raise DpdConfigurationError(
                    f"Pole „{_FIELD_LABELS[field]}” w danych {role} przekracza limit {limit} znaków DPD."
                )
        if _text(address.get("countryCode")) != "PL":
            raise DpdConfigurationError(
                "Pierwszy etap integracji DPD obsługuje wyłącznie adresy w Polsce."
            )
        if not re.fullmatch(r"\d{5}", _text(address.get("postalCode"))):
            raise DpdConfigurationError(
                f"Kod pocztowy w danych {role} musi mieć format 00-000 albo 00000."
            )

    @staticmethod
    def _label_warnings(
        *,
        sender: dict[str, Any],
        receiver: dict[str, Any],
        references: dict[str, str],
        content: str,
    ) -> tuple[str, ...]:
        values = {
            **{f"sender.{key}": value for key, value in sender.items()},
            **{f"receiver.{key}": value for key, value in receiver.items()},
            **references,
            "content": content,
        }
        warnings = []
        for field, limit in _LABEL_FIELD_LIMITS.items():
            value = _text(values.get(field))
            if len(value) > limit:
                warnings.append(
                    f"Pole „{field}” ma {len(value)} znaków i może zostać skrócone na etykiecie DPD."
                )
        return tuple(warnings)

    def build_payload(
        self,
        *,
        idempotency_key: str,
        reference: str,
        receiver: dict[str, Any],
        weight_kg: float,
        business_references: list[str] | None = None,
        items: list[dict[str, Any]] | None = None,
        label_text: str | None = None,
    ) -> dict[str, Any]:
        """Buduje zwalidowane żądanie `generatePackagesNumbers`."""
        self._validate()
        try:
            weight = round(float(weight_kg), 2)
        except (TypeError, ValueError) as exc:
            raise DpdConfigurationError("Waga paczki musi być liczbą.") from exc
        if not 0.01 <= weight <= 31.5:
            raise DpdConfigurationError(
                "Waga standardowej paczki musi mieścić się w zakresie 0,01–31,5 kg."
            )
        sender = self._sender()
        requested_receiver = self._receiver(receiver)
        self._validate_address("odbiorcy", requested_receiver)
        effective_receiver = dict(sender) if self.mode == "demo" else requested_receiver
        effective_business_references = business_references or [reference]
        normalized_label_text = normalize_dpd_label_text(label_text) if label_text else None
        if normalized_label_text:
            reference_text, content = split_dpd_label_text(normalized_label_text)
            references = {"ref1": reference_text}
        else:
            references = _business_reference_fields(effective_business_references)
            content = _parcel_content(items)
        package_reference = _technical_reference("CTIP", idempotency_key)
        parcel_reference = _technical_reference("CTIP-P", idempotency_key)
        parcel = _without_empty_values(
            {
                "reference": parcel_reference,
                "weight": weight,
                "content": content,
                "customerData1": package_reference,
                "customerData2": ", ".join(effective_business_references)[:200],
            }
        )
        payload: dict[str, Any] = {
            "generationPolicy": "STOP_ON_FIRST_ERROR",
            "packages": [
                {
                    "reference": package_reference,
                    "receiver": effective_receiver,
                    "sender": sender,
                    "payerFID": (
                        1
                        if self.test_mode and not _text(settings.dpd_payer_fid)
                        else _positive_int(settings.dpd_payer_fid, "DPD_PAYER_FID")
                    ),
                    **references,
                    "parcels": [parcel],
                }
            ],
            "_ctip": {
                "mode": self.mode,
                "label_text": normalized_label_text,
                "business_references": effective_business_references,
                "demo_receiver_override": self.mode == "demo",
                "requested_receiver": requested_receiver if self.mode == "demo" else None,
                "label_warnings": list(
                    self._label_warnings(
                        sender=sender,
                        receiver=effective_receiver,
                        references=references,
                        content=content,
                    )
                ),
            },
        }
        return payload

    @staticmethod
    def _provider_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in payload.items() if not key.startswith("_")}

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        master_fid = _positive_int(settings.dpd_master_fid, "DPD_MASTER_FID")
        try:
            with httpx.Client(
                base_url=self.base_url,
                auth=httpx.BasicAuth(_text(settings.dpd_login), _text(settings.dpd_password)),
                headers={
                    "X-DPD-FID": str(master_fid),
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=settings.dpd_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise DpdTransportError(f"Nie udało się połączyć z DPD: {exc}", retryable=True) from exc
        try:
            parsed = response.json()
        except ValueError as exc:
            raise DpdTransportError(
                f"DPD zwróciło odpowiedź inną niż JSON (HTTP {response.status_code})."
            ) from exc
        raw = parsed if isinstance(parsed, dict) else {"errors": parsed}
        if response.status_code in {401, 403}:
            raise DpdTransportError(
                "DPD odrzuciło login, hasło albo uprawnienia FID."
                + (
                    f" Identyfikator diagnostyczny: {_text(raw.get('traceId'))}."
                    if _text(raw.get("traceId"))
                    else ""
                )
            )
        if response.status_code >= 400:
            raise DpdTransportError(_response_error(f"HTTP {response.status_code}", raw))
        return raw

    @staticmethod
    def _generated_waybills(payload: dict[str, Any]) -> tuple[str, ...]:
        waybills: list[str] = []
        for package in payload.get("packages") or []:
            for parcel in package.get("parcels") or []:
                waybill = _text(parcel.get("waybill"))
                if waybill:
                    waybills.append(waybill)
        session = payload.get("session") or {}
        for package in session.get("packages") or []:
            for parcel in package.get("parcels") or []:
                waybill = _text(parcel.get("waybill"))
                if waybill:
                    waybills.append(waybill)
        return tuple(dict.fromkeys(waybills))

    @staticmethod
    def _label_request(
        *,
        session_id: int | None = None,
        waybills: list[str] | None = None,
        package_reference: str | None = None,
        parcel_reference: str | None = None,
    ) -> dict[str, Any]:
        session: dict[str, Any] = {"type": "DOMESTIC"}
        if session_id is not None:
            session["sessionId"] = session_id
        else:
            parcel: dict[str, Any] = {}
            if waybills:
                session["packages"] = [{"parcels": [{"waybill": waybill}]} for waybill in waybills]
            else:
                if parcel_reference:
                    parcel["reference"] = parcel_reference
                package: dict[str, Any] = {"parcels": [parcel]}
                if package_reference:
                    package["reference"] = package_reference
                session["packages"] = [package]
        return {
            "labelSearchParams": {
                "policy": "STOP_ON_FIRST_ERROR",
                "session": session,
            },
            "outputDocFormat": "PDF",
            "format": "A4",
            "outputType": "BIC3",
            "variant": "STANDARD",
        }

    def _generate_labels(
        self,
        *,
        session_id: int | None = None,
        waybills: list[str] | None = None,
        package_reference: str | None = None,
        parcel_reference: str | None = None,
        allow_not_found: bool = False,
    ) -> DpdLabelResult | None:
        request = self._label_request(
            session_id=session_id,
            waybills=waybills,
            package_reference=package_reference,
            parcel_reference=parcel_reference,
        )
        raw = self._post_json(settings.dpd_generate_labels_path, request)
        status = _text(raw.get("status")).upper()
        if status == "NOT_FOUND" and allow_not_found:
            return None
        if status != "OK":
            raise DpdTransportError(_response_error("generowanie etykiety", raw))
        encoded = _text(raw.get("documentData"))
        if not encoded:
            raise DpdTransportError("DPD nie zwróciło danych dokumentu etykiety.")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise DpdTransportError("DPD zwróciło niepoprawną etykietę Base64.") from exc
        if not content.startswith(b"%PDF"):
            raise DpdTransportError("DPD nie zwróciło etykiety w formacie PDF A4.")
        response_session = raw.get("session") or {}
        response_session_id = _text(response_session.get("sessionId")) or (
            str(session_id) if session_id is not None else None
        )
        response_waybills = self._generated_waybills(raw) or tuple(waybills or [])
        return DpdLabelResult(
            label_content=content,
            document_id=_text(raw.get("documentId")) or None,
            session_id=response_session_id,
            trace_id=_text(raw.get("traceId")) or None,
            waybills=response_waybills,
            raw_response=_sanitize_response(raw),
        )

    def generate_label_sheet(self, waybills: list[str]) -> DpdLabelResult:
        """Pobiera natywny arkusz A4 dla istniejących numerów listów."""
        self._validate()
        if self.test_mode:
            raise DpdConfigurationError(
                "Natywny arkusz DPD jest dostępny wyłącznie w trybie demo lub produkcyjnym."
            )
        unique = list(dict.fromkeys(_text(value) for value in waybills if _text(value)))
        if not unique or len(unique) > 100:
            raise DpdConfigurationError("Wybierz od 1 do 100 numerów przesyłek DPD.")
        result = self._generate_labels(waybills=unique)
        assert result is not None
        return result

    def _recover_by_reference(self, payload: dict[str, Any]) -> DpdLabelResult | None:
        package = (payload.get("packages") or [{}])[0]
        parcel = (package.get("parcels") or [{}])[0]
        return self._generate_labels(
            package_reference=_text(package.get("reference")) or None,
            parcel_reference=_text(parcel.get("reference")) or None,
            allow_not_found=True,
        )

    def create_shipment(
        self,
        *,
        idempotency_key: str,
        reference: str,
        receiver: dict[str, Any],
        weight_kg: float,
        items: list[dict[str, Any]] | None = None,
        business_references: list[str] | None = None,
        label_text: str | None = None,
    ) -> tuple[dict[str, Any], DpdShipmentResult]:
        """Tworzy przesyłkę, pobiera etykietę A4 albo wykonuje lokalną symulację."""
        payload = self.build_payload(
            idempotency_key=idempotency_key,
            reference=reference,
            receiver=receiver,
            weight_kg=weight_kg,
            business_references=business_references,
            items=items,
            label_text=label_text,
        )
        metadata = payload.get("_ctip") or {}
        warnings = tuple(metadata.get("label_warnings") or [])
        if self.test_mode:
            suffix = secrets.token_hex(5).upper()
            tracking = f"MOCK{datetime.now():%y%m%d}{suffix}"
            raw = {
                "mode": "mock",
                "shipmentId": tracking,
                "trackingNumber": tracking,
                "warnings": list(warnings),
            }
            return payload, DpdShipmentResult(
                shipment_id=tracking,
                tracking_number=tracking,
                label_content=build_mock_shipping_label(payload, tracking, items),
                label_content_type="application/pdf",
                label_format="A4",
                raw_response=raw,
                warnings=warnings,
            )

        provider_payload = self._provider_payload(payload)
        try:
            generated = self._post_json(settings.dpd_generate_packages_path, provider_payload)
        except DpdTransportError as exc:
            if exc.retryable:
                try:
                    recovered = self._recover_by_reference(provider_payload)
                except DpdTransportError:
                    recovered = None
                if recovered and recovered.waybills:
                    return payload, self._shipment_result_from_recovery(
                        recovered, warnings=warnings
                    )
            exc.request_payload = payload
            raise

        generation_status = _text(generated.get("status")).upper()
        if generation_status in {
            "DUPLICATED_PACKAGE_REFERENCE",
            "DUPLICATED_PARCEL_REFERENCE",
        }:
            try:
                recovered = self._recover_by_reference(provider_payload)
            except DpdTransportError as exc:
                exc.request_payload = payload
                raise
            if recovered and recovered.waybills:
                return payload, self._shipment_result_from_recovery(recovered, warnings=warnings)
        if generation_status != "OK":
            raise DpdTransportError(
                _response_error("tworzenie numeru przesyłki", generated),
                request_payload=payload,
            )
        waybills = self._generated_waybills(generated)
        session_id = generated.get("sessionId")
        if not waybills or session_id is None:
            raise DpdTransportError(
                "Odpowiedź DPD nie zawiera numeru listu lub identyfikatora sesji.",
                request_payload=payload,
            )
        try:
            label_result = self._generate_labels(session_id=int(session_id))
        except DpdTransportError as exc:
            exc.request_payload = payload
            raise
        assert label_result is not None
        raw_response = {
            "mode": self.mode,
            "generate_packages": _sanitize_response(generated),
            "generate_labels": label_result.raw_response,
            "warnings": list(warnings),
        }
        return payload, DpdShipmentResult(
            shipment_id=str(session_id),
            tracking_number=waybills[0],
            label_content=label_result.label_content,
            label_content_type="application/pdf",
            label_format="A4",
            raw_response=raw_response,
            warnings=warnings,
        )

    def _shipment_result_from_recovery(
        self,
        label_result: DpdLabelResult,
        *,
        warnings: tuple[str, ...],
    ) -> DpdShipmentResult:
        tracking = label_result.waybills[0]
        return DpdShipmentResult(
            shipment_id=label_result.session_id or label_result.document_id or tracking,
            tracking_number=tracking,
            label_content=label_result.label_content,
            label_content_type="application/pdf",
            label_format="A4",
            raw_response={
                "mode": self.mode,
                "recovered_by_reference": True,
                "generate_labels": label_result.raw_response,
                "warnings": list(warnings),
            },
            warnings=warnings,
        )


__all__ = [
    "DPD_DEMO_URL",
    "DPD_LABEL_TEXT_LIMIT",
    "DPD_PRODUCTION_URL",
    "DpdConfigurationError",
    "DpdLabelResult",
    "DpdShipmentResult",
    "DpdShippingClient",
    "DpdTransportError",
    "dpd_recipient_field_limits",
    "normalize_dpd_label_text",
    "split_dpd_label_text",
]
