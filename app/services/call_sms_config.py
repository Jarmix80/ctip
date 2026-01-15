"""Domyslna konfiguracja i normalizacja ustawien SMS dla dzwoniacych.

Domyslne tresci zawieraja link do aplikacji i sluza jako wartosci startowe.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

CALL_SMS_COOLDOWN_MODES = {"never", "after_days", "always"}

APP_LINK = "https://www.ksero-partner.com.pl/app/"
DEFAULT_INBOUND_ANSWERED_TEXT = (
    "Dziekujemy za rozmowe. Aby szybciej zglaszac zlecenia, polecamy "
    f"bezplatna aplikacje: {APP_LINK}"
)
DEFAULT_INBOUND_MISSED_TEXT = (
    "Nie udalo sie polaczyc. Dla szybkiego zgloszenia sprawy polecamy "
    f"bezplatna aplikacje: {APP_LINK}"
)
DEFAULT_INBOUND_REPEAT_ANSWERED_TEXT = (
    "Dziekujemy za ponowna rozmowe. Dla wygody polecamy bezplatna aplikacje: " f"{APP_LINK}"
)
DEFAULT_INBOUND_REPEAT_MISSED_TEXT = (
    "Ponowne polaczenie nieudane. Zgloszenia szybciej zrealizujesz w "
    f"bezplatnej aplikacji: {APP_LINK}"
)
DEFAULT_OUTBOUND_ANSWERED_TEXT = (
    "Dziekujemy za rozmowe. Dla szybszej obslugi polecamy bezplatna aplikacje: " f"{APP_LINK}"
)
DEFAULT_OUTBOUND_MISSED_TEXT = (
    "Nie udalo sie skontaktowac. Sprawy szybko zglosisz w bezplatnej aplikacji: " f"{APP_LINK}"
)
DEFAULT_OUTBOUND_REPEAT_ANSWERED_TEXT = (
    "Dziekujemy za ponowny kontakt. Polecamy bezplatna aplikacje: " f"{APP_LINK}"
)
DEFAULT_OUTBOUND_REPEAT_MISSED_TEXT = (
    "Ponowna proba kontaktu nieudana. Zgloszenia przyspieszy bezplatna " f"aplikacja: {APP_LINK}"
)
DEFAULT_AFTER_HOURS_TEXT = (
    "Jestesmy poza godzinami pracy. Sprawy szybko zglosisz w bezplatnej " f"aplikacji: {APP_LINK}"
)


@dataclass
class CallSmsConfig:
    """Konfiguracja automatycznych SMS wysylanych po zdarzeniach telefonicznych."""

    enabled: bool = False
    inbound_enabled: bool = True
    outbound_enabled: bool = False
    inbound_answered_enabled: bool = False
    inbound_answered_text: str = DEFAULT_INBOUND_ANSWERED_TEXT
    inbound_missed_enabled: bool = False
    inbound_missed_text: str = DEFAULT_INBOUND_MISSED_TEXT
    inbound_repeat_answered_enabled: bool = False
    inbound_repeat_answered_text: str = DEFAULT_INBOUND_REPEAT_ANSWERED_TEXT
    inbound_repeat_missed_enabled: bool = False
    inbound_repeat_missed_text: str = DEFAULT_INBOUND_REPEAT_MISSED_TEXT
    outbound_answered_enabled: bool = False
    outbound_answered_text: str = DEFAULT_OUTBOUND_ANSWERED_TEXT
    outbound_missed_enabled: bool = False
    outbound_missed_text: str = DEFAULT_OUTBOUND_MISSED_TEXT
    outbound_repeat_answered_enabled: bool = False
    outbound_repeat_answered_text: str = DEFAULT_OUTBOUND_REPEAT_ANSWERED_TEXT
    outbound_repeat_missed_enabled: bool = False
    outbound_repeat_missed_text: str = DEFAULT_OUTBOUND_REPEAT_MISSED_TEXT
    after_hours_enabled: bool = False
    after_hours_text: str = DEFAULT_AFTER_HOURS_TEXT
    after_hours_exts: str = "500"
    cooldown_mode: str = "after_days"
    cooldown_days: int = 30
    opt_out_numbers: str = ""

    def as_dict(self) -> dict[str, object]:
        """Zwraca konfiguracje jako slownik do serializacji."""
        return asdict(self)


def _to_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "on"}


def _to_int(
    value: str | int | None,
    default: int,
    *,
    min_value: int | None = None,
    max_value: int | None = None,
) -> int:
    if value is None:
        numeric = default
    elif isinstance(value, int):
        numeric = value
    else:
        try:
            numeric = int(str(value).strip())
        except ValueError:
            numeric = default
    if min_value is not None:
        numeric = max(min_value, numeric)
    if max_value is not None:
        numeric = min(max_value, numeric)
    return numeric


def normalize_call_sms_config(stored: Mapping[str, str]) -> CallSmsConfig:
    """Buduje konfiguracje na podstawie wpisow z admin_setting."""
    config = CallSmsConfig()
    config.enabled = _to_bool(stored.get("enabled"), config.enabled)
    config.inbound_enabled = _to_bool(stored.get("inbound_enabled"), config.inbound_enabled)
    config.outbound_enabled = _to_bool(stored.get("outbound_enabled"), config.outbound_enabled)
    config.inbound_answered_enabled = _to_bool(
        stored.get("inbound_answered_enabled"), config.inbound_answered_enabled
    )
    config.inbound_answered_text = (
        stored.get("inbound_answered_text") or config.inbound_answered_text
    )
    config.inbound_missed_enabled = _to_bool(
        stored.get("inbound_missed_enabled"), config.inbound_missed_enabled
    )
    config.inbound_missed_text = stored.get("inbound_missed_text") or config.inbound_missed_text
    config.inbound_repeat_answered_enabled = _to_bool(
        stored.get("inbound_repeat_answered_enabled"),
        config.inbound_repeat_answered_enabled,
    )
    config.inbound_repeat_answered_text = (
        stored.get("inbound_repeat_answered_text") or config.inbound_repeat_answered_text
    )
    config.inbound_repeat_missed_enabled = _to_bool(
        stored.get("inbound_repeat_missed_enabled"),
        config.inbound_repeat_missed_enabled,
    )
    config.inbound_repeat_missed_text = (
        stored.get("inbound_repeat_missed_text") or config.inbound_repeat_missed_text
    )
    config.outbound_answered_enabled = _to_bool(
        stored.get("outbound_answered_enabled"), config.outbound_answered_enabled
    )
    config.outbound_answered_text = (
        stored.get("outbound_answered_text") or config.outbound_answered_text
    )
    config.outbound_missed_enabled = _to_bool(
        stored.get("outbound_missed_enabled"), config.outbound_missed_enabled
    )
    config.outbound_missed_text = stored.get("outbound_missed_text") or config.outbound_missed_text
    config.outbound_repeat_answered_enabled = _to_bool(
        stored.get("outbound_repeat_answered_enabled"),
        config.outbound_repeat_answered_enabled,
    )
    config.outbound_repeat_answered_text = (
        stored.get("outbound_repeat_answered_text") or config.outbound_repeat_answered_text
    )
    config.outbound_repeat_missed_enabled = _to_bool(
        stored.get("outbound_repeat_missed_enabled"),
        config.outbound_repeat_missed_enabled,
    )
    config.outbound_repeat_missed_text = (
        stored.get("outbound_repeat_missed_text") or config.outbound_repeat_missed_text
    )
    config.after_hours_enabled = _to_bool(
        stored.get("after_hours_enabled"), config.after_hours_enabled
    )
    config.after_hours_text = stored.get("after_hours_text") or config.after_hours_text
    config.after_hours_exts = stored.get("after_hours_exts") or config.after_hours_exts

    raw_mode = (stored.get("cooldown_mode") or "").strip().lower()
    if raw_mode in CALL_SMS_COOLDOWN_MODES:
        config.cooldown_mode = raw_mode
    config.cooldown_days = _to_int(
        stored.get("cooldown_days"),
        config.cooldown_days,
        min_value=1,
        max_value=3650,
    )
    config.opt_out_numbers = stored.get("opt_out_numbers") or ""
    return config


__all__ = ["CallSmsConfig", "CALL_SMS_COOLDOWN_MODES", "normalize_call_sms_config"]
