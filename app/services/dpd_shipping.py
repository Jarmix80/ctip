"""Adapter DPD Services z bezpiecznym trybem testowym i etykietą A4."""

from __future__ import annotations

import base64
import io
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.core.config import settings


class DpdConfigurationError(RuntimeError):
    """Błąd niekompletnej konfiguracji integracji DPD."""


class DpdTransportError(RuntimeError):
    """Błąd komunikacji lub niezgodnej odpowiedzi DPD."""


@dataclass(slots=True, frozen=True)
class DpdShipmentResult:
    """Ujednolicony rezultat utworzenia przesyłki i pobrania etykiety."""

    shipment_id: str
    tracking_number: str
    label_content: bytes
    label_content_type: str
    label_format: str
    raw_response: dict[str, Any]


def _first_text(payload: Any, keys: set[str]) -> str | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in keys and value not in (None, ""):
                return str(value).strip()
        for value in payload.values():
            found = _first_text(value, keys)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _first_text(value, keys)
            if found:
                return found
    return None


def _mock_label(payload: dict[str, Any], tracking_number: str) -> bytes:
    """Generuje jednoznacznie oznaczoną testową etykietę PDF A4."""
    receiver = payload["receiver"]
    parcel = payload["parcels"][0]
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4)
    width, height = A4
    pdf.setTitle(f"Etykieta testowa DPD {tracking_number}")
    pdf.setFont("Helvetica-Bold", 28)
    pdf.drawString(42, height - 62, "DPD — ETYKIETA TESTOWA")
    pdf.setFont("Helvetica-Bold", 19)
    pdf.drawString(42, height - 108, tracking_number)
    pdf.setFont("Helvetica", 13)
    lines = [
        receiver.get("companyName") or receiver.get("contactName") or "",
        receiver.get("contactName") or "",
        receiver.get("street") or "",
        f"{receiver.get('postalCode', '')} {receiver.get('city', '')}",
        f"Telefon: {receiver.get('phone', '')}",
        f"Waga: {parcel.get('weightKg', '')} kg",
        f"Referencja: {payload.get('reference', '')}",
    ]
    y = height - 160
    for line in lines:
        if line:
            pdf.drawString(42, y, str(line))
            y -= 24
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(42, 72, "NIE PRZEKAZYWAĆ KURIEROWI — TRYB MOCK")
    pdf.rect(28, 40, width - 56, height - 72)
    pdf.showPage()
    pdf.save()
    return output.getvalue()


class DpdShippingClient:
    """Tworzy standardową krajową przesyłkę jednopaczkową przez DPD REST API."""

    def __init__(self) -> None:
        self.test_mode = bool(settings.dpd_test_mode)
        self.base_url = settings.dpd_api_url.rstrip("/")

    def configuration_status(self) -> dict[str, Any]:
        """Zwraca stan integracji bez ujawniania sekretów."""
        sender_ready = all(
            (
                settings.dpd_sender_street,
                settings.dpd_sender_postal_code,
                settings.dpd_sender_city,
                settings.dpd_sender_phone,
            )
        )
        api_ready = all(
            (
                self.base_url,
                settings.dpd_login,
                settings.dpd_api_key,
                settings.dpd_client_number,
            )
        )
        return {
            "enabled": bool(settings.dpd_enabled),
            "test_mode": self.test_mode,
            "api_ready": bool(api_ready),
            "sender_ready": bool(sender_ready),
        }

    def _validate(self) -> None:
        if not settings.dpd_enabled:
            raise DpdConfigurationError("Integracja DPD jest wyłączona (`DPD_ENABLED=false`).")
        if self.test_mode:
            return
        missing = [
            name
            for name, value in (
                ("DPD_API_URL", self.base_url),
                ("DPD_LOGIN", settings.dpd_login),
                ("DPD_API_KEY", settings.dpd_api_key),
                ("DPD_CLIENT_NUMBER", settings.dpd_client_number),
                ("DPD_SENDER_STREET", settings.dpd_sender_street),
                ("DPD_SENDER_POSTAL_CODE", settings.dpd_sender_postal_code),
                ("DPD_SENDER_CITY", settings.dpd_sender_city),
                ("DPD_SENDER_PHONE", settings.dpd_sender_phone),
            )
            if not value
        ]
        if missing:
            raise DpdConfigurationError("Brak wymaganych ustawień DPD: " + ", ".join(missing) + ".")

    def build_payload(
        self,
        *,
        reference: str,
        receiver: dict[str, Any],
        weight_kg: float,
    ) -> dict[str, Any]:
        """Buduje ograniczony payload krajowej przesyłki standardowej."""
        return {
            "payerNumber": settings.dpd_client_number,
            "reference": reference,
            "sender": {
                "companyName": settings.dpd_sender_company,
                "contactName": settings.dpd_sender_contact,
                "street": settings.dpd_sender_street,
                "postalCode": settings.dpd_sender_postal_code,
                "city": settings.dpd_sender_city,
                "countryCode": "PL",
                "phone": settings.dpd_sender_phone,
                "email": settings.dpd_sender_email,
            },
            "receiver": {
                "companyName": receiver.get("company_name"),
                "contactName": receiver.get("contact_name"),
                "street": receiver.get("street"),
                "postalCode": receiver.get("postal_code"),
                "city": receiver.get("city"),
                "countryCode": "PL",
                "phone": receiver.get("phone"),
                "email": receiver.get("email"),
            },
            "service": {"type": "DOMESTIC_STANDARD"},
            "parcels": [{"weightKg": weight_kg, "content": "Tonery i części"}],
            "label": {"format": "A4", "type": "PDF"},
        }

    def create_shipment(
        self,
        *,
        idempotency_key: str,
        reference: str,
        receiver: dict[str, Any],
        weight_kg: float,
    ) -> tuple[dict[str, Any], DpdShipmentResult]:
        """Tworzy przesyłkę lub bezpieczną symulację i zwraca etykietę PDF."""
        self._validate()
        payload = self.build_payload(
            reference=reference,
            receiver=receiver,
            weight_kg=weight_kg,
        )
        if self.test_mode:
            suffix = secrets.token_hex(5).upper()
            tracking = f"MOCK{datetime.now():%y%m%d}{suffix}"
            raw = {"mode": "mock", "shipmentId": tracking, "trackingNumber": tracking}
            return payload, DpdShipmentResult(
                shipment_id=tracking,
                tracking_number=tracking,
                label_content=_mock_label(payload, tracking),
                label_content_type="application/pdf",
                label_format="A4",
                raw_response=raw,
            )

        headers = {
            "Authorization": f"Bearer {settings.dpd_api_key}",
            "X-DPD-Login": str(settings.dpd_login),
            "X-DPD-Client-Number": str(settings.dpd_client_number),
            "Idempotency-Key": idempotency_key,
            "Accept": "application/json",
        }
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=settings.dpd_timeout_seconds,
            ) as client:
                response = client.post(settings.dpd_create_shipment_path, json=payload)
                response.raise_for_status()
                raw = response.json()
                shipment_id = _first_text(
                    raw, {"shipmentid", "shipment_id", "sessionid", "session_id"}
                )
                tracking = _first_text(
                    raw, {"trackingnumber", "tracking_number", "waybill", "parcelnumber"}
                )
                if not shipment_id or not tracking:
                    raise DpdTransportError(
                        "Odpowiedź DPD nie zawiera identyfikatora przesyłki lub numeru listu."
                    )
                label_encoded = _first_text(
                    raw, {"labelcontent", "label_content", "labelbase64", "label_base64"}
                )
                if label_encoded:
                    try:
                        label_content = base64.b64decode(label_encoded, validate=True)
                    except ValueError as exc:
                        raise DpdTransportError(
                            "DPD zwróciło niepoprawną etykietę Base64."
                        ) from exc
                else:
                    label_path = settings.dpd_label_path_template.format(
                        shipment_id=shipment_id,
                        tracking_number=tracking,
                    )
                    label_response = client.get(label_path, headers={"Accept": "application/pdf"})
                    label_response.raise_for_status()
                    label_content = label_response.content
        except httpx.HTTPError as exc:
            raise DpdTransportError(f"Błąd komunikacji z DPD: {exc}") from exc
        except ValueError as exc:
            raise DpdTransportError("DPD zwróciło odpowiedź inną niż JSON.") from exc

        if not label_content.startswith(b"%PDF"):
            raise DpdTransportError("DPD nie zwróciło etykiety w formacie PDF A4.")
        return payload, DpdShipmentResult(
            shipment_id=shipment_id,
            tracking_number=tracking,
            label_content=label_content,
            label_content_type="application/pdf",
            label_format="A4",
            raw_response=raw,
        )


__all__ = [
    "DpdConfigurationError",
    "DpdShipmentResult",
    "DpdShippingClient",
    "DpdTransportError",
]
