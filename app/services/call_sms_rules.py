"""Reguly kwalifikacji numerow i scenariuszy SMS dla dzwoniacych."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.sms import normalize_sms_destination
from app.services.call_sms_config import CallSmsConfig

POLISH_MOBILE_PREFIXES = {
    "45",
    "50",
    "51",
    "53",
    "57",
    "60",
    "66",
    "69",
    "72",
    "73",
    "78",
    "79",
    "88",
}

CALL_SMS_SCENARIO_LABELS = {
    "after_hours": "Po godzinach pracy",
    "inbound_answered": "Przychodzace odebrane",
    "inbound_missed": "Przychodzace nieodebrane",
    "inbound_repeat_answered": "Przychodzace ponowne (odebrane)",
    "inbound_repeat_missed": "Przychodzace ponowne (nieodebrane)",
    "outbound_answered": "Wychodzace odebrane",
    "outbound_missed": "Wychodzace nieodebrane",
    "outbound_repeat_answered": "Wychodzace ponowne (odebrane)",
    "outbound_repeat_missed": "Wychodzace ponowne (nieodebrane)",
    "bulk": "Wysylka masowa",
}

CALL_SMS_SCENARIO_CODES = tuple(CALL_SMS_SCENARIO_LABELS.keys())


@dataclass(frozen=True)
class CallSmsScenario:
    """Konkretny scenariusz SMS wynikajacy z kontekstu polaczenia."""

    code: str
    text: str
    direction: str
    disposition: str
    repeat: bool


def normalize_destination(value: str | None) -> str | None:
    """Normalizuje numer do E.164; zwraca None, gdy numer jest niepoprawny."""
    if value is None:
        return None
    try:
        return normalize_sms_destination(value)
    except ValueError:
        return None


def parse_opt_out_numbers(raw: str | None) -> set[str]:
    """Zwraca zbior numerow wykluczonych z wysylki (E.164)."""
    if not raw:
        return set()
    tokens = re.split(r"[,\n;]+", raw)
    results: set[str] = set()
    for token in tokens:
        cleaned = token.strip()
        if not cleaned:
            continue
        normalized = normalize_destination(cleaned)
        if normalized:
            results.add(normalized)
    return results


def parse_after_hours_exts(raw: str | None) -> set[str]:
    """Zwraca zbior numerow wewnetrznych (cyfry) dla scenariusza po godzinach."""
    if not raw:
        return set()
    tokens = re.split(r"[,\n;\s]+", raw)
    results: set[str] = set()
    for token in tokens:
        cleaned = token.strip()
        if not cleaned:
            continue
        parts = re.findall(r"\d+", cleaned)
        if not parts:
            continue
        results.add(parts[0])
    return results


def is_polish_mobile(number_e164: str | None) -> bool:
    """Sprawdza, czy numer nalezy do polskiej sieci komorkowej."""
    if not number_e164:
        return False
    normalized = number_e164.strip()
    if not normalized.startswith("+48"):
        return False
    digits = re.sub(r"\D", "", normalized)
    if not digits.startswith("48"):
        return False
    national = digits[2:]
    if len(national) != 9:
        return False
    prefix = national[:2]
    return prefix in POLISH_MOBILE_PREFIXES


def pick_call_sms_scenarios(
    config: CallSmsConfig,
    direction: str | None,
    disposition: str | None,
    is_repeat: bool,
) -> list[CallSmsScenario]:
    """Wybiera aktywne scenariusze SMS na podstawie kierunku i statusu polaczenia.

    Dla polaczen oznaczonych jako powtorne wybiera tylko scenariusz "repeat",
    aby uniknac kilku SMS dla tego samego polaczenia.
    """
    if not config.enabled:
        return []
    direction_value = (direction or "").strip().upper()
    if direction_value not in {"IN", "OUT"}:
        return []
    disposition_value = (disposition or "UNKNOWN").strip().upper()
    answered = disposition_value == "ANSWERED"
    scenarios: list[CallSmsScenario] = []

    def add_scenario(code: str, text: str, repeat: bool) -> None:
        if not text.strip():
            return
        scenarios.append(
            CallSmsScenario(
                code=code,
                text=text,
                direction=direction_value,
                disposition=disposition_value,
                repeat=repeat,
            )
        )

    def repeat_has_text(text: str) -> bool:
        return bool(text and text.strip())

    if direction_value == "IN" and config.inbound_enabled:
        if answered:
            if (
                is_repeat
                and config.inbound_repeat_answered_enabled
                and repeat_has_text(config.inbound_repeat_answered_text)
            ):
                add_scenario("inbound_repeat_answered", config.inbound_repeat_answered_text, True)
            elif config.inbound_answered_enabled:
                add_scenario("inbound_answered", config.inbound_answered_text, False)
        else:
            if (
                is_repeat
                and config.inbound_repeat_missed_enabled
                and repeat_has_text(config.inbound_repeat_missed_text)
            ):
                add_scenario("inbound_repeat_missed", config.inbound_repeat_missed_text, True)
            elif config.inbound_missed_enabled:
                add_scenario("inbound_missed", config.inbound_missed_text, False)

    if direction_value == "OUT" and config.outbound_enabled:
        if answered:
            if (
                is_repeat
                and config.outbound_repeat_answered_enabled
                and repeat_has_text(config.outbound_repeat_answered_text)
            ):
                add_scenario("outbound_repeat_answered", config.outbound_repeat_answered_text, True)
            elif config.outbound_answered_enabled:
                add_scenario("outbound_answered", config.outbound_answered_text, False)
        else:
            if (
                is_repeat
                and config.outbound_repeat_missed_enabled
                and repeat_has_text(config.outbound_repeat_missed_text)
            ):
                add_scenario("outbound_repeat_missed", config.outbound_repeat_missed_text, True)
            elif config.outbound_missed_enabled:
                add_scenario("outbound_missed", config.outbound_missed_text, False)

    return scenarios


__all__ = [
    "CALL_SMS_SCENARIO_CODES",
    "CALL_SMS_SCENARIO_LABELS",
    "CallSmsScenario",
    "is_polish_mobile",
    "normalize_destination",
    "parse_after_hours_exts",
    "parse_opt_out_numbers",
    "pick_call_sms_scenarios",
]
