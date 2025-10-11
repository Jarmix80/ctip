"""Warstwa transportowa SMS z prostym klientem HTTP."""

from __future__ import annotations

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
    """Prosty klient HTTP API dla usług SMS."""

    def __init__(
        self,
        base_url: str | None,
        token: str | None,
        sender: str | None,
        *,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.sender = sender or ""
        self.timeout = timeout

    def _is_configured(self) -> bool:
        return bool(self.base_url and self.token)

    def _client(self) -> httpx.Client:
        headers = {"Authorization": f"Bearer {self.token}"}
        return httpx.Client(base_url=self.base_url, headers=headers, timeout=self.timeout)

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

        payload = {
            "to": dest,
            "text": text,
            "sender": self.sender,
            "meta": metadata or {},
        }

        try:
            with self._client() as client:
                response = client.post("/sms", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPError as exc:
            raise SmsTransportError(f"Błąd transportu SMS: {exc}") from exc

        success = bool(data.get("success", False))
        provider_status = data.get("status")
        provider_message_id = data.get("message_id")
        error = data.get("error") if not success else None
        return SmsSendResult(success, provider_status, provider_message_id, error)


__all__ = ["HttpSmsProvider", "SmsSendResult", "SmsTransportError"]
