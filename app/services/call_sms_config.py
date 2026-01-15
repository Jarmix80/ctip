"""Domyslna konfiguracja i normalizacja ustawien SMS dla dzwoniacych."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass

CALL_SMS_COOLDOWN_MODES = {"never", "after_days", "always"}


@dataclass
class CallSmsConfig:
    """Konfiguracja automatycznych SMS wysylanych po zdarzeniach telefonicznych."""

    enabled: bool = False
    inbound_enabled: bool = True
    outbound_enabled: bool = False
    inbound_answered_enabled: bool = False
    inbound_answered_text: str = ""
    inbound_missed_enabled: bool = False
    inbound_missed_text: str = ""
    inbound_repeat_answered_enabled: bool = False
    inbound_repeat_answered_text: str = ""
    inbound_repeat_missed_enabled: bool = False
    inbound_repeat_missed_text: str = ""
    outbound_answered_enabled: bool = False
    outbound_answered_text: str = ""
    outbound_missed_enabled: bool = False
    outbound_missed_text: str = ""
    outbound_repeat_answered_enabled: bool = False
    outbound_repeat_answered_text: str = ""
    outbound_repeat_missed_enabled: bool = False
    outbound_repeat_missed_text: str = ""
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
    config.inbound_answered_text = stored.get("inbound_answered_text") or ""
    config.inbound_missed_enabled = _to_bool(
        stored.get("inbound_missed_enabled"), config.inbound_missed_enabled
    )
    config.inbound_missed_text = stored.get("inbound_missed_text") or ""
    config.inbound_repeat_answered_enabled = _to_bool(
        stored.get("inbound_repeat_answered_enabled"),
        config.inbound_repeat_answered_enabled,
    )
    config.inbound_repeat_answered_text = stored.get("inbound_repeat_answered_text") or ""
    config.inbound_repeat_missed_enabled = _to_bool(
        stored.get("inbound_repeat_missed_enabled"),
        config.inbound_repeat_missed_enabled,
    )
    config.inbound_repeat_missed_text = stored.get("inbound_repeat_missed_text") or ""
    config.outbound_answered_enabled = _to_bool(
        stored.get("outbound_answered_enabled"), config.outbound_answered_enabled
    )
    config.outbound_answered_text = stored.get("outbound_answered_text") or ""
    config.outbound_missed_enabled = _to_bool(
        stored.get("outbound_missed_enabled"), config.outbound_missed_enabled
    )
    config.outbound_missed_text = stored.get("outbound_missed_text") or ""
    config.outbound_repeat_answered_enabled = _to_bool(
        stored.get("outbound_repeat_answered_enabled"),
        config.outbound_repeat_answered_enabled,
    )
    config.outbound_repeat_answered_text = stored.get("outbound_repeat_answered_text") or ""
    config.outbound_repeat_missed_enabled = _to_bool(
        stored.get("outbound_repeat_missed_enabled"),
        config.outbound_repeat_missed_enabled,
    )
    config.outbound_repeat_missed_text = stored.get("outbound_repeat_missed_text") or ""

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
