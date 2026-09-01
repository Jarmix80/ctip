"""Serwerowy klient dopasowania polskich adresów przez Adresy.app."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings


class ShippingGeocoderConfigurationError(RuntimeError):
    """Brak lub niebezpieczna konfiguracja geokodera Shipping."""


class ShippingGeocoderTransportError(RuntimeError):
    """Błąd transportu albo odpowiedzi zewnętrznego geokodera."""


def _text(value: Any) -> str | None:
    normalized = " ".join(str(value or "").split())
    return normalized or None


def _local_number(street: str) -> str | None:
    """Zachowuje numer lokalu zapisany po ukośniku w danych operatora."""
    match = re.search(r"/\s*([0-9A-Za-z-]+)\s*$", street)
    return match.group(1) if match else None


def _normalized_street(match: dict[str, Any], original_street: str) -> str | None:
    street_name = _text(
        match.get("ulica_norm")
        or match.get("ulica_dopasowana")
        or match.get("ulica")
        or match.get("nazwa_1")
    )
    building = _text(match.get("nr_budynku"))
    apartment = _text(match.get("nr_lokalu")) or _local_number(original_street)
    if not street_name:
        return None
    number = building or ""
    if number and apartment and not number.casefold().endswith(f"/{apartment}".casefold()):
        number = f"{number}/{apartment}"
    return " ".join(value for value in (street_name, number) if value)


def _candidate(
    match: dict[str, Any],
    *,
    original_street: str,
    fallback_score: Any = None,
) -> dict[str, Any] | None:
    street = _normalized_street(match, original_street)
    city = _text(match.get("miejscowosc") or match.get("miasto"))
    postal_code = _text(match.get("kod_pocztowy"))
    if not street or not city:
        return None
    try:
        score = float(match.get("score", fallback_score) or 0)
    except (TypeError, ValueError):
        score = 0.0
    if score > 1:
        score /= 100
    return {
        "street": street,
        "postal_code": postal_code,
        "city": city,
        "score": max(0.0, min(score, 1.0)),
    }


class ShippingGeocoderClient:
    """Dopasowuje tylko pocztową część adresu, bez danych kontaktowych klienta."""

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport
        self.base_url = str(settings.addresy_app_api_url or "").strip().rstrip("/")

    def configuration_status(self) -> dict[str, Any]:
        """Zwraca jawny stan konfiguracji bez ujawniania klucza API."""
        parsed = urlparse(self.base_url)
        try:
            safe_port = parsed.port in {None, 443}
        except ValueError:
            safe_port = False
        safe_url = bool(
            parsed.scheme == "https"
            and parsed.hostname == "api.adresy.app"
            and safe_port
            and parsed.path.rstrip("/") == "/api/v1"
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
        )
        key_configured = bool(settings.addresy_app_api_key)
        key_ready = key_configured or settings.is_test_runtime
        return {
            "enabled": bool(settings.shipping_geocoder_enabled),
            "api_ready": bool(settings.shipping_geocoder_enabled and safe_url and key_ready),
            "provider": "Adresy.app",
            "key_configured": key_configured,
            "min_score": float(settings.addresy_app_min_score),
        }

    def _validate_configuration(self) -> None:
        status = self.configuration_status()
        if not status["enabled"]:
            raise ShippingGeocoderConfigurationError("Geokoder adresowy jest wyłączony.")
        if not status["api_ready"]:
            raise ShippingGeocoderConfigurationError(
                "Konfiguracja Adresy.app jest niekompletna albo wskazuje niedozwolony adres API."
            )

    async def match(
        self,
        *,
        street: str,
        postal_code: str | None,
        city: str | None,
    ) -> dict[str, Any]:
        """Zwraca znormalizowanych kandydatów bez utrwalania surowej odpowiedzi dostawcy."""
        self._validate_configuration()
        address_query = ", ".join(
            value for value in (_text(street), _text(postal_code), _text(city)) if value
        )
        headers = {"Accept": "application/json"}
        if settings.addresy_app_api_key:
            headers["Authorization"] = f"Bearer {settings.addresy_app_api_key}"
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                transport=self.transport,
                timeout=float(settings.addresy_app_timeout_seconds),
                headers=headers,
            ) as client:
                response = await client.get(
                    "/match",
                    params={
                        "q": address_query,
                        "min_score": float(settings.addresy_app_min_score),
                    },
                )
        except httpx.TimeoutException as exc:
            raise ShippingGeocoderTransportError(
                "Adresy.app nie odpowiedziało w wymaganym czasie."
            ) from exc
        except httpx.HTTPError as exc:
            raise ShippingGeocoderTransportError("Nie udało się połączyć z Adresy.app.") from exc
        if response.status_code == 429:
            raise ShippingGeocoderTransportError(
                "Przekroczono limit zapytań Adresy.app. Spróbuj ponownie później."
            )
        if response.status_code >= 400:
            raise ShippingGeocoderTransportError(
                f"Adresy.app odrzuciło zapytanie (HTTP {response.status_code})."
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ShippingGeocoderTransportError(
                "Adresy.app zwróciło niepoprawną odpowiedź JSON."
            ) from exc

        candidates: list[dict[str, Any]] = []
        for result in payload.get("results") or []:
            if not isinstance(result, dict):
                continue
            provider_match = result.get("match")
            if isinstance(provider_match, dict):
                candidate = _candidate(
                    provider_match,
                    original_street=street,
                    fallback_score=(result.get("score_detail") or {}).get("final"),
                )
                if candidate:
                    candidates.append(candidate)
            for provider_candidate in result.get("candidates") or []:
                if not isinstance(provider_candidate, dict):
                    continue
                candidate = _candidate(
                    provider_candidate.get("match") or provider_candidate,
                    original_street=street,
                    fallback_score=provider_candidate.get("score"),
                )
                if candidate:
                    candidates.append(candidate)

        unique: dict[tuple[str, str | None, str], dict[str, Any]] = {}
        for candidate in candidates:
            key = (
                candidate["street"].casefold(),
                candidate["postal_code"],
                candidate["city"].casefold(),
            )
            if key not in unique or candidate["score"] > unique[key]["score"]:
                unique[key] = candidate
        ordered = sorted(unique.values(), key=lambda value: value["score"], reverse=True)[:5]
        threshold = float(settings.addresy_app_min_score)
        accepted = [candidate for candidate in ordered if candidate["score"] >= threshold]
        auto_select = accepted[0] if len(accepted) == 1 else None
        return {
            "matched": bool(accepted),
            "auto_select": auto_select,
            "candidates": ordered,
            "min_score": threshold,
            "message": (
                "Znaleziono adres w rejestrze PRG."
                if accepted
                else "Nie znaleziono jednoznacznego adresu o wymaganej jakości."
            ),
        }


__all__ = [
    "ShippingGeocoderClient",
    "ShippingGeocoderConfigurationError",
    "ShippingGeocoderTransportError",
]
