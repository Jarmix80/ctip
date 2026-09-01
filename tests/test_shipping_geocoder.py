"""Testy serwerowego geokodera adresów dostawy Shipping."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from app.core.config import settings
from app.schemas.shipping import ShippingGeocoderRequest
from app.services.shipping_geocoder import (
    ShippingGeocoderClient,
    ShippingGeocoderConfigurationError,
    ShippingGeocoderTransportError,
)


class ShippingGeocoderTests(unittest.IsolatedAsyncioTestCase):
    async def test_dopasowuje_adres_i_zachowuje_numer_lokalu(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "status": "found",
                            "match": {
                                "miejscowosc": "Poznań",
                                "ulica_norm": "Szkolna",
                                "nr_budynku": "13",
                                "kod_pocztowy": "60-001",
                                "score": 0.96,
                            },
                        }
                    ]
                },
            )

        with (
            patch.object(settings, "shipping_geocoder_enabled", True),
            patch.object(settings, "ctip_runtime_profile", "test"),
            patch.object(settings, "addresy_app_api_key", "klucz-testowy"),
            patch.object(settings, "addresy_app_min_score", 0.85),
        ):
            result = await ShippingGeocoderClient(transport=httpx.MockTransport(handler)).match(
                street="ul. Szkolna 13/7",
                postal_code="60-001",
                city="Poznań",
            )

        self.assertTrue(result["matched"])
        self.assertEqual(result["auto_select"]["street"], "Szkolna 13/7")
        self.assertEqual(result["auto_select"]["postal_code"], "60-001")
        self.assertEqual(captured[0].headers["Authorization"], "Bearer klucz-testowy")
        query = str(captured[0].url)
        self.assertIn("Szkolna", query)
        self.assertNotIn("klient", query.casefold())
        self.assertNotIn("telefon", query.casefold())
        self.assertNotIn("email", query.casefold())

    async def test_nie_dubluje_lokalu_zwracanego_przez_dostawce(self) -> None:
        transport = httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "match": {
                                "miejscowosc": "Poznań",
                                "ulica_norm": "Szkolna",
                                "nr_budynku": "13/7",
                                "kod_pocztowy": "60-001",
                                "score": 0.98,
                            }
                        }
                    ]
                },
            )
        )
        with (
            patch.object(settings, "shipping_geocoder_enabled", True),
            patch.object(settings, "ctip_runtime_profile", "test"),
        ):
            result = await ShippingGeocoderClient(transport=transport).match(
                street="Szkolna 13/7",
                postal_code=None,
                city="Poznań",
            )

        self.assertEqual(result["candidates"][0]["street"], "Szkolna 13/7")

    async def test_limit_dostawcy_jest_czytelnym_bledem(self) -> None:
        transport = httpx.MockTransport(lambda _: httpx.Response(429, json={"detail": "limit"}))
        with (
            patch.object(settings, "shipping_geocoder_enabled", True),
            patch.object(settings, "ctip_runtime_profile", "test"),
            self.assertRaisesRegex(ShippingGeocoderTransportError, "limit zapytań"),
        ):
            await ShippingGeocoderClient(transport=transport).match(
                street="Szkolna 13",
                postal_code=None,
                city="Poznań",
            )

    async def test_produkcja_wymaga_klucza_i_oficjalnego_https(self) -> None:
        with (
            patch.object(settings, "shipping_geocoder_enabled", True),
            patch.object(settings, "ctip_runtime_profile", "production"),
            patch.object(settings, "addresy_app_api_key", None),
            self.assertRaises(ShippingGeocoderConfigurationError),
        ):
            await ShippingGeocoderClient().match(
                street="Szkolna 13",
                postal_code=None,
                city="Poznań",
            )

    def test_schemat_normalizuje_kod_pocztowy(self) -> None:
        payload = ShippingGeocoderRequest.model_validate(
            {"street": "Szkolna 13", "postal_code": "60001", "city": "Poznań"}
        )
        self.assertEqual(payload.postal_code, "60-001")


if __name__ == "__main__":
    unittest.main()
