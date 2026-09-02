"""Walidacja polskich numerów telefonu używanych przez moduł Shipping."""

from __future__ import annotations

import re

_ALLOWED_PHONE_CHARACTERS = re.compile(r"^[0-9+().\s-]+$")
_POLISH_NATIONAL_NUMBER = re.compile(r"^[1-9][0-9]{8}$")


def normalize_polish_shipping_phone(value: str) -> str:
    """Normalizuje krajowy numer do `+48XXXXXXXXX` albo zgłasza czytelny błąd."""
    phone = str(value or "").strip()
    if not phone:
        raise ValueError("Podaj numer telefonu odbiorcy.")
    if not _ALLOWED_PHONE_CHARACTERS.fullmatch(phone):
        raise ValueError(
            "Numer telefonu może zawierać wyłącznie cyfry, spacje, nawiasy, kropki i myślniki."
        )
    if phone.count("+") > 1 or ("+" in phone and not phone.startswith("+")):
        raise ValueError("Znak plus może wystąpić tylko raz, na początku numeru telefonu.")

    compact = re.sub(r"[().\s-]+", "", phone)
    if compact.startswith("+48"):
        national_number = compact[3:]
    elif compact.startswith("0048"):
        national_number = compact[4:]
    elif compact.startswith("+") or compact.startswith("00"):
        raise ValueError("W Shipping można podać wyłącznie polski numer telefonu.")
    elif len(compact) == 10 and compact.startswith("0"):
        national_number = compact[1:]
    else:
        national_number = compact

    if not _POLISH_NATIONAL_NUMBER.fullmatch(national_number):
        raise ValueError("Polski numer telefonu musi zawierać dokładnie 9 cyfr.")
    return f"+48{national_number}"


__all__ = ["normalize_polish_shipping_phone"]
