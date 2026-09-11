"""Bezpieczne parsery raportów i wiadomości Remote z zachowaniem oryginałów."""

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from defusedxml import ElementTree

PARSER_VERSION = "1"
COUNTERS = {
    "Total": "lifetime.total",
    "B&W Total": "lifetime.black",
    "Color Total": "lifetime.color",
    "Black Development": "development.black",
    "Color Development": "development.color",
    "Copier: Total": "lifetime.copy",
    "Printer: Total": "lifetime.print",
    "Scan (Input): Total": "lifetime.scan_input",
    "Scan: Total": "lifetime.scan",
}
COLORS = {"B": "black", "M": "magenta", "Y": "yellow", "C": "cyan"}


def fingerprint(value) -> str:
    """Wyznacza stabilny skrót wartości bez zależności od kolejności pól."""
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def serial_number(value) -> str:
    """Normalizuje zapis numeru bez usuwania znaków istotnych dla tożsamości."""
    return str(value or "").strip().upper()


def number(value):
    """Rozpoznaje liczby, pozostawiając nieznane i opisowe stany bez interpretacji."""
    try:
        result = Decimal(str(value).strip().removesuffix("%").replace(",", "."))
    except InvalidOperation:
        return None
    if not result.is_finite() or result.adjusted() > 20:
        return None
    return int(result) if result == result.to_integral_value() else float(result)


def timestamp(date_value: str, time_value: str = "", zone: str = "Europe/Warsaw"):
    """Zachowuje dokładność daty oraz odrzuca niejednoznaczną godzinę zmiany czasu."""
    raw = f"{date_value or ''} {time_value or ''}".strip()
    if not raw:
        return None, "unknown", "missing"
    parsed = None
    for pattern in (
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            parsed = datetime.strptime(raw, pattern)
            break
        except ValueError:
            continue
    if parsed is None:
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None, "unknown", "invalid"
    precision = "date" if ":" not in raw else ("minute" if raw.count(":") == 1 else "second")
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC), precision, "explicit"
    if not zone:
        return None, precision, "unresolved"
    timezone = ZoneInfo(zone)
    first = parsed.replace(tzinfo=timezone, fold=0)
    second = parsed.replace(tzinfo=timezone, fold=1)
    if first.utcoffset() != second.utcoffset():
        return None, precision, "ambiguous"
    return first.astimezone(UTC), precision, zone


@dataclass
class Reading:
    """Rekord pozyskany ze źródła, niezależny od bazy docelowej."""

    external_key: str
    kind: str
    serial: str
    payload: dict
    observed_at: datetime | None = None
    precision: str = "unknown"
    time_basis: str = "missing"
    received_at: datetime | None = None
    measurements: dict = field(default_factory=dict)
    semantic_key: str = ""
    issues: list[tuple[str, str]] = field(default_factory=list)


def parse_csv(blob: bytes, zone: str = "Europe/Warsaw") -> list[Reading]:
    """Czyta pięć odmian CSV po nagłówkach, nigdy według nazwy eksportu."""
    decoded = None
    for encoding in ("utf-8-sig", "cp1250"):
        try:
            decoded = blob.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("csv_encoding")
    reader = csv.DictReader(io.StringIO(decoded, newline=""), strict=True)
    headers = reader.fieldnames or []
    if len(headers) != len(set(headers)) or "Device Serial Number" not in headers:
        raise ValueError("csv_headers")
    unavailable = "Last Acquisition Date (mm/dd/yyyy)" in headers
    supplies = "Call Type" in headers
    if not unavailable and not supplies and "Acquisition Date (mm/dd/yyyy)" not in headers:
        raise ValueError("csv_format")
    result = []
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("csv_incomplete_row")
        serial = serial_number(row.get("Device Serial Number"))
        date_header = (
            "Last Acquisition Date (mm/dd/yyyy)"
            if unavailable
            else "Occurrance Date (mm/dd/yyyy)" if supplies else "Acquisition Date (mm/dd/yyyy)"
        )
        time_header = (
            "Last Acquisition Time"
            if unavailable
            else "Occurrance Time" if supplies else "Acquisition Time"
        )
        observed, precision, basis = timestamp(
            row.get(date_header, ""), row.get(time_header, ""), zone
        )
        kind = "unavailable" if unavailable else "supply_event" if supplies else "reading"
        identity = [
            "ricoh",
            serial,
            kind,
            row.get(date_header),
            row.get(time_header),
            row.get("Counter Type", ""),
        ]
        if unavailable:
            identity += [row.get("Starting date"), row.get("End date")]
        if supplies:
            identity += [row.get("Call Type")]
        semantic = fingerprint(identity if serial and precision != "date" else [*identity, row])
        reading = Reading(
            semantic, kind, serial, row, observed, precision, basis, semantic_key=semantic
        )
        for header, metric in COUNTERS.items():
            value = number(row.get(("Last " if unavailable else "") + header))
            if value is not None:
                reading.measurements[metric] = value
        for code, color in COLORS.items():
            value = number(row.get(f"Toner Remaining({code})", row.get(f"Toner Status ({code})")))
            if value is not None:
                reading.measurements[f"toner.{color}.percent"] = value
            for header, metric in (
                (f"Replaced toner (pce)  {color.title()}", f"replacements.{color}"),
                (f"{color.title()} counter by current toner (Page)", f"bottle_pages.{color}"),
            ):
                value = number(row.get(header))
                if value is not None:
                    reading.measurements[metric] = value
        result.append(reading)
    return result


def _field_key(value: str) -> str:
    """Ujednolica etykiety XML i tekstu bez interpretacji ich wartości."""
    return re.sub(r"[^a-z0-9]", "", value.lower())


def parse_mail(blob: bytes, zone: str = "") -> list[Reading]:
    """Czyta wiadomość i bezpieczny XML; nie renderuje HTML ani nie pobiera odnośników."""
    message = BytesParser(policy=policy.default).parsebytes(blob)
    subject = str(message.get("Subject", ""))
    received = None
    try:
        received = parsedate_to_datetime(str(message.get("Date", "")))
        if received.tzinfo is None:
            received = None
    except (ValueError, TypeError):
        pass
    fields, sections, xml_documents = {}, [], []
    for part in message.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True) or b""
        if str(part.get_filename() or "").lower().endswith(".xml") or payload.lstrip().startswith(
            b"<?xml"
        ):
            root = ElementTree.fromstring(payload)
            leaves = []
            for node in root.iter():
                if not len(node) and (node.text or "").strip():
                    tag = node.tag.rsplit("}", 1)[-1]
                    leaves.append([tag, node.text.strip()])
                    fields.setdefault(_field_key(tag), node.text.strip())
            xml_documents.append(leaves)
        elif part.get_content_type() == "text/plain":
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            sections.append(text)
            for line in text.splitlines():
                matched = re.match(r"^\s*(?:\d{1,2}\.)?([^:\r\n]{1,80}):\s*(.*)$", line)
                if matched:
                    fields.setdefault(_field_key(matched[1]), matched[2].strip())
    serial = next(
        (
            fields.get(key)
            for key in ("deviceid", "devicesn", "deviceappliancesn", "appliancesn")
            if fields.get(key)
        ),
        "",
    )
    if not serial:
        matched = re.search(r"(?:Device(?:\(Appliance\))?|Appliance) S/N:\s*([^,]+)", subject, re.I)
        serial = matched[1] if matched else ""
    serial = serial_number(serial)
    event = re.split(
        r",\s*(?:TAG ID|Customer Name / ID|Device S/N|Appliance S/N|Service Depot Name)",
        subject,
        maxsplit=1,
        flags=re.I,
    )[0].strip()
    raw_date = next(
        (
            fields.get(key)
            for key in ("occurrencedate", "receivedate", "receivedateandtime")
            if fields.get(key)
        ),
        "",
    )
    raw_date = re.sub(r"\(.*$", "", raw_date).strip()
    observed, precision, basis = timestamp(raw_date, zone=zone)
    semantic = fingerprint(
        ["remote_mail", serial, event, raw_date]
        if serial and raw_date
        else ["mail", hashlib.sha256(blob).hexdigest()]
    )
    reading = Reading(
        semantic,
        "remote_event",
        serial,
        {
            "subject": subject,
            "fields": fields,
            "text_sections": sections,
            "xml_leaves": xml_documents,
        },
        observed,
        precision,
        basis,
        received,
        semantic_key=semantic,
    )
    for key, metric in (
        ("meterblack", "lifetime.black"),
        ("metercolor", "lifetime.color"),
        ("metertotal", "lifetime.total"),
    ):
        value = number(fields.get(key))
        if value is not None:
            reading.measurements[metric] = value
    meter = fields.get("metercolorblack", "")
    if re.fullmatch(r"\d+/\d+", meter):
        color, black = meter.split("/")
        reading.measurements.update({"lifetime.color": int(color), "lifetime.black": int(black)})
    elif meter.isdigit():
        reading.measurements["lifetime.black"] = int(meter)
    for color, value in re.findall(
        r"(Black|Yellow|Magenta|Cyan)\s+(-?\d+)%", fields.get("tonerstatus", ""), re.I
    ):
        reading.measurements[f"toner.{color.lower()}.percent"] = int(value)
    return [reading]
