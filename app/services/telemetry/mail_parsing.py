"""Interpretacja części MIME i historii bez wykonywania aktywnej treści."""

import hashlib
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from html.parser import HTMLParser

from defusedxml import ElementTree

from app.services.telemetry.parsers import Reading, fingerprint, parse_csv, parse_mail, timestamp

SERIAL_TAGS = {
    "deviceid",
    "devicesn",
    "deviceappliancesn",
    "appliancesn",
    "serialnumber",
    "deviceserialnumber",
}


def tag_name(value):
    """Porównuje nazwy elementów niezależnie od przestrzeni nazw XML."""
    return re.sub("[^a-z0-9]", "", value.rsplit("}", 1)[-1].lower())


class PlainHTML(HTMLParser):
    """Wydobywa wyłącznie tekst, pomijając skrypty i style."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "tr", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
        elif tag in {"p", "div", "tr", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def xml_groups(root):
    """Rozdziela dokument zbiorczy wyłącznie po jawnych identyfikatorach urządzeń."""
    serials = {
        node.text.strip()
        for node in root.iter()
        if tag_name(node.tag) in SERIAL_TAGS and (node.text or "").strip()
    }
    if len(serials) <= 1:
        yield root
        return
    for child in root:
        if any(tag_name(node.tag) in SERIAL_TAGS for node in child.iter()):
            yield from xml_groups(child)


def xml_payload(node):
    """Zachowuje powtórzenia, atrybuty i strukturę nieznanych pól historii."""
    return {
        "tag": node.tag,
        "attributes": dict(node.attrib),
        "text": (node.text or "").strip(),
        "children": [xml_payload(child) for child in node],
    }


def history_readings(root, serial, zone):
    """Oddziela dostępne wpisy historii bez zamieniania ich w bieżące zdarzenia."""
    result = []
    for container in root.iter():
        name = tag_name(container.tag)
        if not any(word in name for word in ("history", "eventlist", "errorlog", "jamlog")):
            continue
        for entry in container:
            payload = {"history_type": name, "entry": xml_payload(entry)}
            fields = {
                tag_name(node.tag): (node.text or "").strip()
                for node in entry.iter()
                if not len(node)
            }
            date_value = next(
                (
                    fields[key]
                    for key in ("occurrencedate", "eventdate", "datetime", "date", "time")
                    if fields.get(key)
                ),
                "",
            )
            observed, precision, basis = timestamp(date_value, zone=zone)
            key = fingerprint(["mail_history", serial, payload])
            result.append(
                Reading(
                    key,
                    "device_event",
                    serial,
                    payload,
                    observed,
                    precision,
                    basis,
                    semantic_key=key,
                )
            )
    return result


def parse_remote_message(blob, zone=""):
    """Przetwarza wszystkie obsługiwane części, zachowując błędy do bezpiecznego ponowienia."""
    message = BytesParser(policy=policy.default).parsebytes(blob)
    result = []
    for part in message.walk():
        if part.is_multipart():
            continue
        payload = part.get_payload(decode=True) or b""
        filename = str(part.get_filename() or "").lower()
        content_type = part.get_content_type()
        if filename.endswith(".csv") or content_type == "text/csv":
            result.extend(parse_csv(payload))
            continue
        xml = (
            filename.endswith(".xml")
            or content_type in {"application/xml", "text/xml"}
            or payload.lstrip().startswith(b"<?xml")
        )
        groups = []
        if xml:
            root = ElementTree.fromstring(payload)
            groups = [
                (ElementTree.tostring(group, encoding="utf-8"), group) for group in xml_groups(root)
            ]
        elif content_type in {"text/plain", "text/html"}:
            decoded = payload.decode(part.get_content_charset() or "utf-8")
            if content_type == "text/html":
                parser = PlainHTML()
                parser.feed(decoded)
                decoded = "".join(parser.parts)
            blocks = re.split(
                r"(?im)(?=^\s*(?:Device(?:\(Appliance\))?|Appliance) S/N\s*:)", decoded
            )
            groups = [(block.encode(), None) for block in blocks if block.strip()]
        else:
            continue
        for content, tree in groups:
            envelope = EmailMessage()
            for header in ("Subject", "Date", "Message-ID"):
                if message.get(header):
                    envelope[header] = message[header]
            if tree is not None:
                envelope.add_attachment(
                    content, maintype="application", subtype="xml", filename="report.xml"
                )
            else:
                envelope.set_content(content.decode())
            readings = parse_mail(envelope.as_bytes(), zone)
            for reading in readings:
                if not any(
                    reading.payload["fields"].get(key)
                    for key in ("occurrencedate", "receivedate", "receivedateandtime")
                ):
                    key = fingerprint(
                        [
                            "mail_part",
                            reading.serial,
                            str(message.get("Subject", "")),
                            hashlib.sha256(content).hexdigest(),
                        ]
                    )
                    reading.external_key = key
                    reading.semantic_key = key
                if tree is not None:
                    reading.payload["xml_document"] = xml_payload(tree)
                    result.extend(history_readings(tree, reading.serial, zone))
                result.append(reading)
    if not result:
        raise ValueError("mail_content_unsupported")
    unique = {}
    for reading in result:
        unique[(reading.external_key, fingerprint(reading.payload))] = reading
    return list(unique.values())
