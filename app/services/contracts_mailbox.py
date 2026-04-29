"""Narzędzia pomocnicze dla automatyzacji obsługi wiadomości umów."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

MAILBOX_EVENT_DECISION = "decision_for_signature"
MAILBOX_EVENT_APPROVAL = "approval_for_delivery"

_REJECTION_PATTERNS = (
    "odmowa",
    "decyzja negatywna",
    "wniosek odrzucony",
    "brak zgody",
    "nie wyraza zgody",
    "nie wyraza zgody na zawarcie umowy",
    "nie wyrazila zgody",
    "nie wyrazamy zgody",
    "nie mozemy wyrazic zgody",
)

_APP_NO_PATTERN = re.compile(r"\b(\d{3})\s*[-_/]\s*(\d{4,7})\b")
_PROFORMA_NO_PATTERN = re.compile(
    r"(?is)\b(?:faktura\s*)?pro\s*forma(?:\s*(?:nr|numer|no|#)\s*[:.]?)?\s*"
    r"(\d[\d\s]*/\s*pro\s*forma\s*/\s*\d[\d\s]*)"
)
_NIP_PATTERN = re.compile(r"\b(?:PL)?\d{10}\b", re.IGNORECASE)
_PESEL_PATTERN = re.compile(r"\b\d{11}\b")
_NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)


@dataclass(slots=True)
class ParsedApplicationNumber:
    """Numer wniosku wyciągnięty z treści/tematu wiadomości."""

    raw: str
    normalized: str


@dataclass(slots=True)
class ParsedProformaNumber:
    """Numer proformy wyciągnięty z treści/tematu wiadomości."""

    raw: str
    normalized: str


def classify_mail_subject(subject: str) -> str | None:
    """Klasyfikuje temat wiadomości pod kątem workflow umów."""
    normalized = _normalize_for_match(subject)
    if "decyzja do wniosku" in normalized:
        return MAILBOX_EVENT_DECISION
    if "zgoda na realizacje zamowienia do wniosku" in normalized:
        return MAILBOX_EVENT_APPROVAL
    return None


def detect_rejection_decision(text: str) -> bool:
    """Rozpoznaje odmowę GRENKE w treści wiadomości lub PDF."""
    normalized = _normalize_for_match(text)
    if not normalized:
        return False
    return any(pattern in normalized for pattern in _REJECTION_PATTERNS)


def extract_application_number(text: str) -> ParsedApplicationNumber | None:
    """Wyciąga numer wniosku z tekstu i zwraca formę surową oraz znormalizowaną."""
    match = _APP_NO_PATTERN.search(text or "")
    if match is None:
        return None
    raw = f"{match.group(1)}-{match.group(2)}"
    normalized = f"{match.group(1)}{match.group(2)}"
    return ParsedApplicationNumber(raw=raw, normalized=normalized)


def normalize_application_number(value: str | None) -> str | None:
    """Normalizuje numer wniosku do postaci samych cyfr."""
    if value is None:
        return None
    digits = re.sub(r"\D+", "", str(value))
    return digits or None


def normalize_proforma_number(value: str | None) -> str | None:
    """Normalizuje numer proformy do stabilnego klucza porównań."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    text = text.replace("\\", "/")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^a-z0-9/._-]+", "", text)
    return text or None


def extract_proforma_number(text: str) -> ParsedProformaNumber | None:
    """Wyciąga numer proformy z tekstu i zwraca formę surową oraz znormalizowaną."""
    match = _PROFORMA_NO_PATTERN.search(text or "")
    if match is None:
        return None
    raw = re.sub(r"\s+", "", match.group(1))
    normalized = normalize_proforma_number(raw)
    if not normalized:
        return None
    return ParsedProformaNumber(raw=raw, normalized=normalized)


def _normalize_for_match(value: str | None) -> str:
    """Normalizuje tekst do porównań semantycznych (bez polskich znaków)."""
    text = str(value or "").strip().lower()
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char)
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _ascii_upper(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return text.upper()


def _normalize_nip(value: str) -> str | None:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 10:
        return None
    return digits


def _is_valid_nip(value: str) -> bool:
    normalized = _normalize_nip(value)
    if normalized is None:
        return False
    checksum = (
        sum(
            int(digit) * weight for digit, weight in zip(normalized[:9], _NIP_WEIGHTS, strict=False)
        )
        % 11
    )
    return checksum == int(normalized[9])


def build_pdf_password_candidates(payload: dict[str, Any]) -> list[str]:
    """Buduje listę kandydatów hasła PDF na podstawie danych reprezentantów."""
    representatives = payload.get("representatives")
    if not isinstance(representatives, list):
        return []

    output: list[str] = []
    seen: set[str] = set()

    for rep in representatives:
        if not isinstance(rep, dict):
            continue
        pesel = str(rep.get("pesel") or "").strip()
        if len(pesel) < 5 or not pesel[-5:].isdigit():
            continue

        first_name = str(rep.get("first_name") or "").strip()
        last_name = str(rep.get("last_name") or "").strip()
        if not first_name or not last_name:
            continue

        first_initials = "".join(token[:1] for token in first_name.split() if token)
        last_initials = "".join(token[:1] for token in last_name.split() if token)
        initials_ascii = _ascii_upper(first_initials + last_initials)
        variants = [f"{pesel[-5:]}{initials_ascii}$"]
        for candidate in variants:
            if candidate not in seen:
                seen.add(candidate)
                output.append(candidate)
    return output


def score_form_match(body_text: str, payload: dict[str, Any]) -> int:
    """Wylicza punktację dopasowania wiadomości do payloadu formularza."""
    if not isinstance(payload, dict):
        return 0

    normalized_body = _normalize_for_match(body_text)
    if not normalized_body:
        return 0

    score = 0

    company_name = _normalize_for_match(payload.get("company_name"))
    if company_name and company_name in normalized_body:
        score += 10

    company_nip = normalize_application_number(str(payload.get("company_nip") or ""))
    if company_nip and company_nip in normalize_application_number(normalized_body or ""):
        score += 8

    representatives = payload.get("representatives")
    if isinstance(representatives, list):
        for rep in representatives:
            if not isinstance(rep, dict):
                continue
            full_name = _normalize_for_match(
                " ".join(
                    part
                    for part in [
                        str(rep.get("first_name") or "").strip(),
                        str(rep.get("last_name") or "").strip(),
                    ]
                    if part
                )
            )
            if full_name and full_name in normalized_body:
                score += 6
            pesel = str(rep.get("pesel") or "").strip()
            if len(pesel) == 11 and pesel in normalized_body:
                score += 5
    return score


def extract_data_from_contract_text(text: str) -> dict[str, Any]:
    """Wyciąga podstawowe dane biznesowe z treści dokumentu PDF."""
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return {
            "application_numbers": [],
            "nips": [],
            "pesels": [],
            "company": None,
            "representative": None,
        }

    app_numbers = []
    seen_apps: set[str] = set()
    for match in _APP_NO_PATTERN.finditer(normalized):
        raw = f"{match.group(1)}-{match.group(2)}"
        if raw in seen_apps:
            continue
        seen_apps.add(raw)
        app_numbers.append(raw)

    nips = []
    seen_nips: set[str] = set()
    for raw_nip in _NIP_PATTERN.findall(normalized):
        normalized_nip = _normalize_nip(raw_nip)
        if normalized_nip is None or not _is_valid_nip(normalized_nip):
            continue
        canonical_nip = raw_nip.upper()
        if canonical_nip in seen_nips:
            continue
        seen_nips.add(canonical_nip)
        nips.append(canonical_nip)
    pesels = sorted(set(_PESEL_PATTERN.findall(normalized)))

    company_match = re.search(
        r"Wnioskodawca\s*:\s*(.+?)(?=\s+(?:NIP|Reprezentant|PESEL|$))",
        normalized,
        flags=re.IGNORECASE,
    )
    representative_match = re.search(
        r"Reprezentant\s*:\s*(.+?)(?=\s+(?:NIP|PESEL|$))",
        normalized,
        flags=re.IGNORECASE,
    )

    company = company_match.group(1).strip() if company_match else None
    representative = representative_match.group(1).strip() if representative_match else None

    return {
        "application_numbers": app_numbers,
        "nips": nips,
        "pesels": pesels,
        "company": company,
        "representative": representative,
    }


__all__ = [
    "MAILBOX_EVENT_APPROVAL",
    "MAILBOX_EVENT_DECISION",
    "ParsedApplicationNumber",
    "ParsedProformaNumber",
    "build_pdf_password_candidates",
    "classify_mail_subject",
    "extract_proforma_number",
    "extract_application_number",
    "extract_data_from_contract_text",
    "normalize_application_number",
    "normalize_proforma_number",
    "score_form_match",
]
