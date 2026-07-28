"""Minimalizacja i kryptograficzna ochrona numerów używanych przez katalog botów."""

from __future__ import annotations

import hashlib
import hmac
import re

from cryptography.fernet import Fernet

from app.core.config import settings


def normalize_customer_phone(value: str | None) -> str:
    """Normalizuje polski numer do dziewięciu cyfr, zachowując inne formaty E.164."""

    digits = re.sub(r"\D+", "", value or "")
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("48") and len(digits) == 11:
        digits = digits[2:]
    return digits if 7 <= len(digits) <= 15 else ""


def normalize_customer_nip(value: str | None) -> str:
    """Normalizuje NIP do dziesięciu cyfr bez ujawniania go w indeksie."""
    digits = re.sub(r"\D+", "", value or "")
    return digits if len(digits) == 10 else ""


class BotIdentityCrypto:
    """Szyfruje PII i tworzy stabilny HMAC do dokładnego wyszukiwania."""

    def __init__(self, secret_key: str | None = None) -> None:
        raw = (secret_key or settings.bot_identity_secret_key or "").strip()
        if not raw:
            raise RuntimeError("BOT_IDENTITY_SECRET_KEY nie jest skonfigurowany.")
        try:
            self._fernet = Fernet(raw.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise RuntimeError(
                "BOT_IDENTITY_SECRET_KEY musi być poprawnym kluczem Fernet."
            ) from exc
        self._hmac_key = hashlib.sha256(raw.encode("ascii") + b":bot-identity").digest()

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str | None) -> str:
        if not value:
            return ""
        return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")

    def phone_hmac(self, normalized_phone: str) -> str:
        return hmac.new(
            self._hmac_key,
            b"phone:" + normalized_phone.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()

    def nip_hmac(self, normalized_nip: str) -> str:
        """Tworzy osobny deterministyczny indeks HMAC dla NIP."""
        return hmac.new(
            self._hmac_key,
            b"nip:" + normalized_nip.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
