"""Warstwa transportowa SMS z prostym klientem HTTP."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True)
class SmsSendResult:
    success: bool
    provider_status: str | None
    provider_message_id: str | None
    error: str | None


class SmsTransportError(RuntimeError):
    """Błąd wysyłki SMS poprzez transport HTTP."""


class HttpSmsProvider:
    """Prosty klient HTTPS API SerwerSMS (lub kompatybilny)."""

    UNIQUE_ID_PREFIX = "CTIP-"
    UNIQUE_ID_MIN_LEN = 6
    UNIQUE_ID_MAX_LEN = 24

    def __init__(
        self,
        base_url: str | None,
        token: str | None,
        sender: str | None,
        *,
        username: str | None = None,
        password: str | None = None,
        sms_type: str | None = None,
        test_mode: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.username = username or ""
        self.password = password or ""
        self.sender = sender or ""
        self.sms_type = sms_type or None
        self.test_mode = test_mode
        self.timeout = timeout

    # ------------------------------------------------------------------
    def _is_configured(self) -> bool:
        if not self.base_url:
            return False
        if self.token:
            return True
        return bool(self.username and self.password)

    def _client(self) -> httpx.Client:
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return httpx.Client(base_url=self.base_url, headers=headers, timeout=self.timeout)

    # ------------------------------------------------------------------
    @classmethod
    def _build_unique_id_from_sms_id(cls, value: Any) -> str | None:
        """Generuje unikalny identyfikator zgodny z wymaganiami API operatora."""
        if value is None:
            return None
        try:
            numeric = int(str(value))
        except (TypeError, ValueError):
            return cls._sanitize_unique_id(str(value))
        if numeric < 0:
            numeric = abs(numeric)
        return f"{cls.UNIQUE_ID_PREFIX}{numeric:0{cls.UNIQUE_ID_MIN_LEN}d}"

    @classmethod
    def _sanitize_unique_id(cls, raw: str | None) -> str | None:
        if not raw:
            return None
        raw = raw.strip()
        if not raw:
            return None
        if raw.upper().startswith(cls.UNIQUE_ID_PREFIX):
            candidate = raw.upper()
        else:
            cleaned = re.sub(r"[^0-9A-Za-z]", "", raw).upper()
            if not cleaned:
                return None
            if len(cleaned) < cls.UNIQUE_ID_MIN_LEN:
                cleaned = cleaned.rjust(cls.UNIQUE_ID_MIN_LEN, "0")
            candidate = f"{cls.UNIQUE_ID_PREFIX}{cleaned}"
        # Utrzymujemy wyłącznie znaki alfanumeryczne oraz '-' i przycinamy do limitu.
        candidate = re.sub(r"[^0-9A-Z-]", "", candidate)
        prefix = cls.UNIQUE_ID_PREFIX
        suffix = candidate[len(prefix) :]
        if len(suffix) < cls.UNIQUE_ID_MIN_LEN:
            suffix = suffix.rjust(cls.UNIQUE_ID_MIN_LEN, "0")
        suffix = suffix[: cls.UNIQUE_ID_MAX_LEN]
        return f"{prefix}{suffix}"

    def _resolve_unique_id(self, metadata: Mapping[str, Any] | None) -> str | None:
        if not metadata:
            return None
        if "sms_id" in metadata and metadata["sms_id"] is not None:
            unique_id = self._build_unique_id_from_sms_id(metadata["sms_id"])
            if unique_id:
                return unique_id
        if "unique_id" in metadata and metadata["unique_id"] is not None:
            return self._sanitize_unique_id(str(metadata["unique_id"]))
        return None

    def send_sms(
        self,
        dest: str,
        text: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> SmsSendResult:
        """Wysyła SMS – przy braku konfiguracji zwraca wynik symulowany."""
        if not self._is_configured():
            return SmsSendResult(True, "SIMULATED", None, None)

        unique_id = self._resolve_unique_id(metadata)

        payload: dict[str, Any] = {
            "phone": [dest],
            "text": text,
            "details": True,
        }
        if self.sender:
            payload["sender"] = self.sender
        if self.sms_type:
            payload["type"] = self.sms_type
        if self.test_mode:
            payload["test"] = True
        if unique_id:
            payload["unique_id"] = [unique_id]

        if self.username and self.password:
            payload["username"] = self.username
            payload["password"] = self.password

        try:
            with self._client() as client:
                response = client.post("/messages/send_sms", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise SmsTransportError(f"Błąd transportu SMS: {exc}") from exc

        success = bool(data.get("success", False))
        provider_status: str | None = None
        provider_message_id: str | None = None
        error_text: str | None = None

        items = data.get("items") or []
        if items:
            first = items[0]
            provider_status = first.get("status") or data.get("status")
            provider_message_id = first.get("id")
            if not success and first.get("error_message"):
                error_text = first.get("error_message")
        if not success and not error_text:
            error_obj = data.get("error")
            if isinstance(error_obj, dict):
                error_text = error_obj.get("message") or str(error_obj)
            else:
                error_text = str(error_obj)

        return SmsSendResult(success, provider_status, provider_message_id, error_text)


__all__ = ["HttpSmsProvider", "SmsSendResult", "SmsTransportError"]
