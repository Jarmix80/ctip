"""Parser danych z umow PDF (NIP i numer umowy)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)
NIP_LABEL_RE = re.compile(r"(?i)\bnip\b[^\d]{0,12}([0-9][0-9\-\s]{8,20}[0-9])")
CONTRACT_LABEL_RE = re.compile(r"(?i)\b(?:nr|numer)\s+umowy(?:\s+najmu)?\b")
REQUEST_LABEL_RE = re.compile(r"(?i)\b(?:nr|numer)\s+wniosku\b")
CONTRACT_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9./_-]{2,48}")

DISALLOWED_CONTRACT_TOKENS = {
    "NR",
    "NUMER",
    "UMOWY",
    "UMOWA",
    "NAJMU",
    "WNIOSKU",
    "DODATKOWE",
    "OZNACZENIE",
    "STRONA",
    "PL",
}


@dataclass(frozen=True)
class ContractData:
    """Wynik ekstrakcji metadanych z dokumentu umowy."""

    nip: str | None
    contract_number: str | None
    nips_found: tuple[str, ...]
    contract_candidates: tuple[str, ...]


def parse_contract_pdf(pdf_path: str | Path) -> ContractData:
    """Odczytuje PDF i zwraca wykryte pola umowy."""
    text = extract_text_from_pdf(pdf_path)
    return extract_contract_data_from_text(text)


def extract_text_from_pdf(pdf_path: str | Path) -> str:
    """Zwraca polaczony tekst ze wszystkich stron PDF."""
    path = Path(pdf_path)
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def extract_contract_data_from_text(text: str) -> ContractData:
    """Wyciaga NIP i numer umowy z tekstu dokumentu."""
    cleaned = _cleanup_text(text)
    lines = _split_lines(cleaned)

    nip_scores = _collect_nip_scores(cleaned)
    nips_found = tuple(_unique_preserve_order(item[0] for item in nip_scores))

    best_nip: str | None = None
    best_score = -10_000
    for nip, score in nip_scores:
        if score > best_score:
            best_nip = nip
            best_score = score
    if best_score < 0:
        best_nip = None

    contract_candidates = tuple(_extract_contract_candidates(lines))
    contract_number = contract_candidates[0] if contract_candidates else None

    return ContractData(
        nip=best_nip,
        contract_number=contract_number,
        nips_found=nips_found,
        contract_candidates=contract_candidates,
    )


def _cleanup_text(text: str) -> str:
    return text.replace("\xa0", " ").replace("\u200b", "")


def _split_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        normalized = re.sub(r"\s+", " ", line).strip()
        if normalized:
            lines.append(normalized)
    return lines


def _collect_nip_scores(text: str) -> list[tuple[str, int]]:
    scores: list[tuple[str, int]] = []
    for match in NIP_LABEL_RE.finditer(text):
        nip = _normalize_nip(match.group(1))
        if not nip:
            continue
        if not _is_valid_nip(nip):
            continue

        context = text[max(0, match.start() - 180) : min(len(text), match.end() + 180)].upper()
        score = 0

        if "NAJEMCA" in context:
            score += 8
        if "KRS / INNY REJESTR" in context or "KRS/ INNY REJESTR" in context:
            score += 5
        if "WYSTAWCA WEKSLA" in context:
            score += 4

        if any(token in context for token in ("FINANSUJ", "GRENKE", "REGON", "KAPITAŁ")):
            score -= 10
        if "ZBYWCA" in context:
            score -= 4

        scores.append((nip, score))

    return scores


def _normalize_nip(value: str) -> str | None:
    digits = re.sub(r"\D", "", value)
    if len(digits) != 10:
        return None
    return digits


def _is_valid_nip(value: str) -> bool:
    checksum = (
        sum(int(digit) * weight for digit, weight in zip(value[:9], NIP_WEIGHTS, strict=False)) % 11
    )
    return checksum == int(value[9])


def _extract_contract_candidates(lines: list[str]) -> list[str]:
    candidates: list[str] = []

    for idx, line in enumerate(lines):
        if not CONTRACT_LABEL_RE.search(line):
            continue

        fragments: list[str] = []
        inline = CONTRACT_LABEL_RE.sub("", line, count=1).strip(" :-")
        if inline:
            fragments.append(inline)

        if not inline:
            for offset in (1, 2):
                if idx + offset >= len(lines):
                    break
                next_line = lines[idx + offset]
                if not next_line:
                    continue
                if CONTRACT_LABEL_RE.search(next_line) or REQUEST_LABEL_RE.search(next_line):
                    break
                fragments.append(next_line)
                break

        for fragment in fragments:
            for token in CONTRACT_TOKEN_RE.findall(fragment):
                normalized = token.strip(" .,:;)").upper()
                if not _is_valid_contract_token(normalized):
                    continue
                candidates.append(normalized)

    return _unique_preserve_order(candidates)


def _is_valid_contract_token(token: str) -> bool:
    if token in DISALLOWED_CONTRACT_TOKENS:
        return False
    if len(token) < 5:
        return False
    if not any(char.isdigit() for char in token):
        return False
    if re.fullmatch(r"\d{1,2}/\d{2}", token):
        return False
    if token.startswith("PL") and re.fullmatch(r"PL\d{2}/\d{2}", token):
        return False
    return True


def _unique_preserve_order(values: Iterable[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


__all__ = [
    "ContractData",
    "extract_contract_data_from_text",
    "extract_text_from_pdf",
    "parse_contract_pdf",
]
