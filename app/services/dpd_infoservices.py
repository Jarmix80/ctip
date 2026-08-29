"""Bezpieczny klient SOAP dla zdarzeń DPD InfoServices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, SubElement, tostring
from zoneinfo import ZoneInfo

import httpx
from defusedxml.ElementTree import fromstring

from app.core.config import settings

DPD_INFO_PRODUCTION_URL = (
    "https://dpdinfoservices.dpd.com.pl/" "DPDInfoServicesObjEventsService/DPDInfoServicesObjEvents"
)
SOAP_ENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"
DPD_EVENTS_NS = "http://events.dpdinfoservices.dpd.com.pl/"
WARSAW = ZoneInfo("Europe/Warsaw")


class DpdInfoConfigurationError(RuntimeError):
    """Błąd niekompletnej albo niebezpiecznej konfiguracji InfoServices."""


class DpdInfoTransportError(RuntimeError):
    """Błąd komunikacji lub odpowiedzi SOAP DPD InfoServices."""


@dataclass(slots=True, frozen=True)
class DpdInfoEventData:
    """Pojedyncza wartość dodatkowa zdarzenia DPD."""

    code: str | None
    description: str | None
    value: str | None

    def as_dict(self) -> dict[str, str | None]:
        """Zwraca bezpieczną postać do zapisu w JSON."""
        return {"code": self.code, "description": self.description, "value": self.value}


@dataclass(slots=True, frozen=True)
class DpdInfoEvent:
    """Znormalizowane zdarzenie listu przewozowego."""

    event_id: str | None
    object_id: str | None
    business_code: str | None
    waybill: str
    description: str | None
    event_time: datetime | None
    depot: str | None
    depot_name: str | None
    country: str | None
    package_reference: str | None
    parcel_reference: str | None
    operation_type: str
    event_data: tuple[DpdInfoEventData, ...]

    def as_dict(self) -> dict[str, Any]:
        """Zwraca postać zdarzenia bez danych autoryzacyjnych."""
        return {
            "event_id": self.event_id,
            "object_id": self.object_id,
            "business_code": self.business_code,
            "waybill": self.waybill,
            "description": self.description,
            "event_time": self.event_time.isoformat() if self.event_time else None,
            "depot": self.depot,
            "depot_name": self.depot_name,
            "country": self.country,
            "package_reference": self.package_reference,
            "parcel_reference": self.parcel_reference,
            "operation_type": self.operation_type,
            "event_data": [value.as_dict() for value in self.event_data],
        }


@dataclass(slots=True, frozen=True)
class DpdInfoBatch:
    """Partia zdarzeń wraz z identyfikatorem potwierdzenia."""

    confirm_id: str | None
    events: tuple[DpdInfoEvent, ...]


def _text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", maxsplit=1)[-1]


def _direct_child_text(element: Element, name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == name:
            return _text(child.text)
    return None


def _parse_event_time(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=WARSAW)
    return parsed.astimezone(UTC)


def _auth(parent: Element, *, login: str, password: str, channel: str) -> None:
    auth = SubElement(parent, "authDataV1")
    SubElement(auth, "channel").text = channel
    SubElement(auth, "login").text = login
    SubElement(auth, "password").text = password


def _envelope(operation: str) -> tuple[Element, Element]:
    envelope = Element(f"{{{SOAP_ENV_NS}}}Envelope")
    SubElement(envelope, f"{{{SOAP_ENV_NS}}}Header")
    body = SubElement(envelope, f"{{{SOAP_ENV_NS}}}Body")
    method = SubElement(body, f"{{{DPD_EVENTS_NS}}}{operation}")
    return envelope, method


def _parse_fault(root: Element) -> str | None:
    for element in root.iter():
        if _local_name(element.tag) == "faultstring":
            return _text(element.text)
    return None


def _parse_event(element: Element) -> DpdInfoEvent | None:
    waybill = _direct_child_text(element, "waybill")
    operation_type = (_direct_child_text(element, "operationType") or "INSERT").upper()
    if operation_type not in {"INSERT", "CANCEL"}:
        operation_type = "INSERT"
    if not waybill and operation_type != "CANCEL":
        return None
    event_data: list[DpdInfoEventData] = []
    for child in element:
        if _local_name(child.tag) != "eventDataList":
            continue
        event_data.append(
            DpdInfoEventData(
                code=_direct_child_text(child, "code"),
                description=_direct_child_text(child, "description"),
                value=_direct_child_text(child, "value"),
            )
        )
    return DpdInfoEvent(
        event_id=_direct_child_text(element, "id"),
        object_id=_direct_child_text(element, "objectId"),
        business_code=_direct_child_text(element, "businessCode"),
        waybill=waybill or "",
        description=_direct_child_text(element, "description"),
        event_time=_parse_event_time(_direct_child_text(element, "eventTime")),
        depot=_direct_child_text(element, "depot"),
        depot_name=_direct_child_text(element, "depotName"),
        country=_direct_child_text(element, "country"),
        package_reference=_direct_child_text(element, "packageReference"),
        parcel_reference=_direct_child_text(element, "parcelReference"),
        operation_type=operation_type,
        event_data=tuple(event_data),
    )


def _parse_batch(content: bytes) -> DpdInfoBatch:
    try:
        root = fromstring(content)
    except Exception as exc:
        raise DpdInfoTransportError("DPD InfoServices zwróciło niepoprawny dokument XML.") from exc
    fault = _parse_fault(root)
    if fault:
        raise DpdInfoTransportError(f"DPD InfoServices odrzuciło zapytanie: {fault}")
    return_element = next(
        (element for element in root.iter() if _local_name(element.tag) == "return"),
        None,
    )
    if return_element is None:
        return DpdInfoBatch(confirm_id=None, events=())
    confirm_id = _direct_child_text(return_element, "confirmId")
    events = tuple(
        event
        for child in return_element
        if _local_name(child.tag) == "eventsList" and (event := _parse_event(child)) is not None
    )
    return DpdInfoBatch(confirm_id=confirm_id, events=events)


class DpdInfoServicesClient:
    """Pobiera i potwierdza zdarzenia z kanału DPD InfoServices."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        self.transport = transport
        self.base_url = str(settings.dpd_info_api_url or "").strip()

    def configuration_status(self) -> dict[str, Any]:
        """Zwraca gotowość klienta bez ujawniania sekretów."""
        return {
            "enabled": bool(settings.dpd_info_enabled),
            "api_ready": bool(
                self.base_url
                and settings.dpd_login
                and settings.dpd_password
                and settings.dpd_info_channel
            ),
            "channel_configured": bool(settings.dpd_info_channel),
            "sync_interval_seconds": int(settings.dpd_info_sync_interval_seconds),
        }

    def _credentials(self) -> tuple[str, str, str]:
        if not settings.dpd_info_enabled:
            raise DpdInfoConfigurationError(
                "Integracja DPD InfoServices jest wyłączona (`DPD_INFO_ENABLED=false`)."
            )
        missing = [
            name
            for name, value in (
                ("DPD_LOGIN", settings.dpd_login),
                ("DPD_PASSWORD", settings.dpd_password),
                ("DPD_INFO_CHANNEL", settings.dpd_info_channel),
            )
            if not _text(value)
        ]
        if missing:
            raise DpdInfoConfigurationError(
                "Brak wymaganych ustawień DPD InfoServices: " + ", ".join(missing) + "."
            )
        parsed = urlsplit(self.base_url)
        expected = urlsplit(DPD_INFO_PRODUCTION_URL)
        if parsed.scheme != "https" or parsed.hostname != expected.hostname:
            raise DpdInfoConfigurationError(
                "DPD_INFO_API_URL musi wskazywać oficjalny host HTTPS DPD InfoServices."
            )
        return str(settings.dpd_login), str(settings.dpd_password), str(settings.dpd_info_channel)

    def _post(self, envelope: Element) -> bytes:
        payload = tostring(envelope, encoding="utf-8", xml_declaration=True)
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=float(settings.dpd_info_timeout_seconds),
            ) as client:
                response = client.post(
                    self.base_url,
                    content=payload,
                    headers={
                        "Accept": "text/xml",
                        "Content-Type": "text/xml; charset=utf-8",
                        "SOAPAction": '""',
                    },
                )
                response.raise_for_status()
                return response.content
        except httpx.HTTPError as exc:
            raise DpdInfoTransportError(f"Błąd komunikacji z DPD InfoServices: {exc}") from exc

    def get_customer_events(self) -> DpdInfoBatch:
        """Pobiera kolejną partię niepotwierdzonych zdarzeń kanału."""
        login, password, channel = self._credentials()
        envelope, method = _envelope("getEventsForCustomerV4")
        limit = min(max(int(settings.dpd_info_batch_limit), 1), 1000)
        SubElement(method, "limit").text = str(limit)
        SubElement(method, "language").text = "PL"
        _auth(method, login=login, password=password, channel=channel)
        return _parse_batch(self._post(envelope))

    def get_waybill_events(self, waybill: str) -> DpdInfoBatch:
        """Pobiera pełną historię jednego istniejącego numeru listu."""
        login, password, channel = self._credentials()
        normalized = str(waybill or "").strip()
        if not normalized:
            raise DpdInfoConfigurationError("Numer listu DPD nie może być pusty.")
        envelope, method = _envelope("getEventsForWaybillV1")
        SubElement(method, "waybill").text = normalized
        SubElement(method, "eventsSelectType").text = "ALL"
        SubElement(method, "language").text = "PL"
        _auth(method, login=login, password=password, channel=channel)
        return _parse_batch(self._post(envelope))

    def mark_events_processed(self, confirm_id: str) -> bool:
        """Potwierdza dopiero trwale zapisaną partię zdarzeń kanału."""
        login, password, channel = self._credentials()
        normalized = str(confirm_id or "").strip()
        if not normalized:
            return False
        envelope, method = _envelope("markEventsAsProcessedV1")
        SubElement(method, "confirmId").text = normalized
        _auth(method, login=login, password=password, channel=channel)
        content = self._post(envelope)
        try:
            root = fromstring(content)
        except Exception as exc:
            raise DpdInfoTransportError("DPD zwróciło niepoprawne potwierdzenie XML.") from exc
        fault = _parse_fault(root)
        if fault:
            raise DpdInfoTransportError(f"DPD odrzuciło potwierdzenie zdarzeń: {fault}")
        value = next(
            (
                _text(element.text)
                for element in root.iter()
                if _local_name(element.tag) == "return"
            ),
            None,
        )
        return str(value or "").casefold() == "true"


__all__ = [
    "DPD_INFO_PRODUCTION_URL",
    "DpdInfoBatch",
    "DpdInfoConfigurationError",
    "DpdInfoEvent",
    "DpdInfoEventData",
    "DpdInfoServicesClient",
    "DpdInfoTransportError",
]
