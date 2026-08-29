"""Testy procesu wysyłki części, etykiety DPD i zamknięcia dnia."""

from __future__ import annotations

import base64
import io
import json
import re
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from fastapi import HTTPException
from pydantic import ValidationError
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdf_canvas
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.api.routes.admin_shipping import _require_catalog_mutations, _require_fulfillment
from app.core.config import settings
from app.main import create_app
from app.models import (
    AdminAuditLog,
    AdminUser,
    Base,
    ShippingAddress,
    ShippingCase,
    ShippingConsumableCompatibility,
    ShippingDayClose,
    ShippingEvent,
    ShippingItem,
    ShippingShipment,
    ShippingTrackingEvent,
    ShippingTrackingParcel,
    ShippingTrackingSyncRun,
)
from app.schemas.shipping import (
    ShippingBulkCreateRequest,
    ShippingCompatibilityManualBatchRequest,
    ShippingConsolidatedCreateRequest,
    ShippingReviewRequest,
)
from app.services.dpd_shipping import (
    DpdConfigurationError,
    DpdShippingClient,
    DpdTransportError,
)
from app.services.shipping_archive import (
    get_shipping_archive_detail,
    list_shipping_archive,
    normalize_shipping_archive_text,
)
from app.services.shipping_compatibility import (
    confirm_manual_compatibilities,
    derive_compatibility_candidates,
    list_compatibility_items,
    review_compatibilities,
    scan_compatibility_catalog,
)
from app.services.shipping_compatibility_web import enrich_compatibilities_with_web
from app.services.shipping_documents import (
    build_mock_shipping_label_sheet,
    build_shipping_packing_summary,
    merge_shipping_pdf_documents,
    pack_shipping_labels_four_up,
)
from app.services.shipping_firebird import (
    SHIPPING_TECHNICIAN_NAME,
    ShippingOrderStateConflict,
    _created_from_mobile_app,
    _extract_phone_from_order_text,
    _match_shipping_mobile_contact,
    _phone_key,
    _search_terms,
    _vat_rate,
    finalize_shipping_order,
    load_shipping_order,
    load_shipping_overdue_invoices,
    load_shipping_overdue_summaries,
    load_shipping_queue,
    shipping_document_mode,
    shipping_order_state_payload,
    write_shipment_to_order,
)
from app.services.shipping_workflow import (
    ShippingConflictError,
    ShippingLocationChangedError,
    _shipping_item_price,
    build_shipping_address_candidates,
    build_shipping_consolidation_groups,
    close_shipping_day,
    close_shipping_order,
    create_consolidated_shipping_shipment,
    create_shipping_shipment,
    normalize_shipping_location,
    parse_shipping_location,
    review_shipping_order,
    shipping_location_context,
    shipping_shipment_consolidation,
)


def _order(order_table_id: int = 1001) -> dict:
    return {
        "order_table_id": order_table_id,
        "order_id": 77,
        "order_year": 2026,
        "status": "O",
        "company_id": 1,
        "client_id": 15,
        "machine_id": 44,
        "model_id": 900,
        "order_kind": "Umowa",
        "order_location": "Sekretariat, parter",
        "machine_location": None,
        "device_brand": "Ricoh",
        "device_model": "IM C3000",
        "problem": "Proszę wysłać toner czarny",
        "order_company_name": "Przykładowa Firma",
        "order_street": "Testowa 10",
        "order_postal_code": "00-001",
        "order_city": "Warszawa",
        "order_phone": "500 600 700",
        "order_email": "klient@example.com",
        "tracking_number": None,
        "technician": None,
        "secondary_technician": None,
    }


def _stock(quantity: float = 5.0) -> list[dict]:
    return [
        {
            "warehouse_item_id": 501,
            "warehouse_id": 1,
            "item_kind": "1. Część zamienna",
            "item_index": "IMC3000-BK",
            "item_name": "Toner Ricoh IM C3000 czarny",
            "unit": "szt.",
            "stock_quantity": quantity,
            "reserved_quantity": 0.0,
            "available_quantity": quantity,
            "price_net": 120.0,
            "purchase_price_net": 60.0,
            "vat_rate": 23.0,
        }
    ]


def _empty_pdf() -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.write(output)
    return output.getvalue()


def _native_a4_label_pages(labels: list[str]) -> bytes:
    output = io.BytesIO()
    document = pdf_canvas.Canvas(output, pagesize=A4)
    for label in labels:
        document.drawString(20, A4[1] - 30, label)
        document.showPage()
    document.save()
    return output.getvalue()


def _review_payload(
    order: dict | None = None,
    *,
    allow_negative_stock: bool = False,
    unit_price_net: str | None = None,
) -> ShippingReviewRequest:
    source_order = order or _order()
    return ShippingReviewRequest.model_validate(
        {
            "address": {
                "company_name": "Przykładowa Firma",
                "contact_name": "Jan Kowalski",
                "street": "Testowa 10",
                "postal_code": "00-001",
                "city": "Warszawa",
                "country_code": "PL",
                "phone": "500 600 700",
                "email": "klient@example.com",
                "source": "location",
                "location_text": "Sekretariat, parter",
            },
            "location_fingerprint": shipping_location_context(source_order)["fingerprint"],
            "weight_kg": "2.000",
            "items": [
                {
                    "firebird_warehouse_item_id": 501,
                    "quantity": "1.000",
                    "unit_price_net": unit_price_net,
                    "remember_for_model": True,
                    "allow_negative_stock": allow_negative_stock,
                }
            ],
            "save_address": True,
        }
    )


class DpdShippingClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous = {
            "dpd_enabled": settings.dpd_enabled,
            "dpd_mode": settings.dpd_mode,
            "dpd_test_mode": settings.dpd_test_mode,
            "dpd_api_url": settings.dpd_api_url,
            "dpd_login": settings.dpd_login,
            "dpd_password": settings.dpd_password,
            "dpd_master_fid": settings.dpd_master_fid,
            "dpd_payer_fid": settings.dpd_payer_fid,
            "dpd_sender_company": settings.dpd_sender_company,
            "dpd_sender_contact": settings.dpd_sender_contact,
            "dpd_sender_street": settings.dpd_sender_street,
            "dpd_sender_postal_code": settings.dpd_sender_postal_code,
            "dpd_sender_city": settings.dpd_sender_city,
            "dpd_sender_phone": settings.dpd_sender_phone,
            "dpd_sender_email": settings.dpd_sender_email,
        }

    def tearDown(self) -> None:
        for key, value in self.previous.items():
            setattr(settings, key, value)

    def test_mock_generuje_realistyczna_etykiete_pdf_a4(self) -> None:
        settings.dpd_enabled = True
        settings.dpd_mode = "mock"
        receiver = _review_payload().address.model_dump(mode="json")
        receiver["company_name"] = "Przykładowa Firma Łódź"
        payload, result = DpdShippingClient().create_shipment(
            idempotency_key=str(uuid4()),
            reference="MS-2026-77",
            receiver=receiver,
            weight_kg=2.0,
            items=[
                {
                    "item_index": "ŻÓŁTY-1",
                    "item_name": "Żółty toner i bęben",
                    "quantity": 2,
                    "unit": "szt.",
                }
            ],
        )
        package = payload["packages"][0]
        self.assertEqual(payload["generationPolicy"], "STOP_ON_FIRST_ERROR")
        self.assertNotIn("services", package)
        self.assertRegex(package["sender"]["postalCode"], r"^\d{5}$")
        self.assertEqual(package["receiver"]["postalCode"], "00001")
        self.assertEqual(package["parcels"][0]["weight"], 2.0)
        self.assertEqual(
            package["parcels"][0]["content"],
            "2x Żółty toner i bęben",
        )
        self.assertLessEqual(len(package["reference"]), 50)
        self.assertLessEqual(len(package["parcels"][0]["reference"]), 50)
        self.assertTrue(result.tracking_number.startswith("MOCK"))
        self.assertTrue(result.label_content.startswith(b"%PDF"))
        self.assertEqual(result.label_format, "A4")
        reader = PdfReader(io.BytesIO(result.label_content))
        self.assertEqual(len(reader.pages), 1)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 595.28, places=1)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.height), 841.89, places=1)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        self.assertIn("ETYKIETA TESTOWA — NIE NADAWAĆ", text)
        self.assertIn("ADRES DOSTAWY / DELIVERY ADDRESS", text)
        self.assertIn("Przykładowa Firma Łódź", text)
        self.assertIn("DPD CLASSIC TEST", text)
        self.assertIn("PL-TEST", text)
        self.assertIn("KOD 2D TEST", text)
        self.assertIn("ZAWARTOŚĆ PACZKI", text)
        self.assertIn(result.tracking_number, text)
        self.assertIn("Żółty toner i bęben", text)

    def test_zestawienie_pakowania_laczy_tabele_i_etykiete(self) -> None:
        settings.dpd_enabled = True
        settings.dpd_mode = "mock"
        _, result = DpdShippingClient().create_shipment(
            idempotency_key=str(uuid4()),
            reference="MS-2026-77",
            receiver=_review_payload().address.model_dump(mode="json"),
            weight_kg=2.0,
            items=[{"item_index": "842255", "item_name": "Bęben czarny", "quantity": 1}],
        )
        summary = build_shipping_packing_summary(
            [
                {
                    "order_number": "77/2026",
                    "address": {"company_name": "Klient Łódź", "contact_name": "Jan Żak"},
                    "tracking_number": result.tracking_number,
                    "items": [
                        {
                            "item_index": "842255",
                            "item_name": "Bęben czarny",
                            "quantity": 1,
                            "unit": "szt.",
                        }
                    ],
                }
            ]
        )
        bundle = merge_shipping_pdf_documents([summary, result.label_content])
        reader = PdfReader(io.BytesIO(bundle))
        self.assertGreaterEqual(len(reader.pages), 2)
        summary_text = reader.pages[0].extract_text() or ""
        self.assertIn("Zestawienie kompletacji wysyłek", summary_text)
        self.assertIn("Klient Łódź", summary_text)
        self.assertIn("Bęben czarny", summary_text)

    def test_wspolna_paczka_ma_dwa_zlecenia_i_jedna_etykiete(self) -> None:
        settings.dpd_enabled = True
        settings.dpd_mode = "mock"
        payload, result = DpdShippingClient().create_shipment(
            idempotency_key=str(uuid4()),
            reference="MS-GROUP-TEST",
            receiver=_review_payload().address.model_dump(mode="json"),
            weight_kg=4.0,
            business_references=["77/2026", "78/2026"],
            items=[
                {
                    "order_number": "77/2026",
                    "item_index": "842255",
                    "item_name": "Toner czarny",
                    "quantity": 1,
                    "unit": "szt.",
                },
                {
                    "order_number": "78/2026",
                    "item_index": "842256",
                    "item_name": "Toner żółty",
                    "quantity": 1,
                    "unit": "szt.",
                },
            ],
        )
        label_text = "\n".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(result.label_content)).pages
        )
        self.assertIn("77/2026", label_text)
        self.assertIn("78/2026", label_text)
        self.assertIn("Toner czarny", label_text)
        self.assertIn("Toner żółty", label_text)

        summary = build_shipping_packing_summary(
            [
                {
                    "order_number": order_number,
                    "address": {"company_name": "Wspólny klient"},
                    "tracking_number": result.tracking_number,
                    "items": [{"item_name": item_name, "quantity": 1}],
                }
                for order_number, item_name in (
                    ("77/2026", "Toner czarny"),
                    ("78/2026", "Toner żółty"),
                )
            ]
        )
        summary_text = "\n".join(
            page.extract_text() or "" for page in PdfReader(io.BytesIO(summary)).pages
        )
        self.assertIn("Zlecenia: 2", summary_text)
        self.assertIn("Paczki: 1", summary_text)
        self.assertEqual(payload["packages"][0]["ref1"], "77/2026, 78/2026")

    def test_mock_arkusz_etykiet_zachowuje_uklad_dwa_na_dwa(self) -> None:
        settings.dpd_enabled = True
        settings.dpd_mode = "mock"
        for labels_count, expected_pages in ((1, 1), (4, 1), (5, 2)):
            with self.subTest(labels_count=labels_count):
                entries = []
                for index in range(labels_count):
                    payload = DpdShippingClient().build_payload(
                        idempotency_key=str(uuid4()),
                        reference=f"MS-2026-{index}",
                        receiver=_review_payload().address.model_dump(mode="json"),
                        weight_kg=2.0,
                        business_references=[f"{index}/2026"],
                    )
                    entries.append(
                        {
                            "tracking_number": f"MOCK-{index}",
                            "payload": payload,
                            "items": [
                                {
                                    "item_index": f"IDX-{index}",
                                    "item_name": f"Toner testowy {index}",
                                    "quantity": 1,
                                    "unit": "szt.",
                                }
                            ],
                        }
                    )
                content = build_mock_shipping_label_sheet(entries)
                reader = PdfReader(io.BytesIO(content))
                self.assertEqual(len(reader.pages), expected_pages)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                self.assertIn("MOCK-0", text)
                self.assertIn(f"MOCK-{labels_count - 1}", text)
                self.assertIn("Toner testowy 0", text)
                self.assertEqual(text.count("ETYKIETA TESTOWA — NIE NADAWAĆ"), labels_count)

    def test_natywne_strony_dpd_sa_skladane_po_cztery_na_a4(self) -> None:
        labels = [f"DPD-ETYKIETA-{index}" for index in range(1, 6)]
        content = pack_shipping_labels_four_up(
            _native_a4_label_pages(labels),
            label_count=len(labels),
        )
        reader = PdfReader(io.BytesIO(content))
        self.assertEqual(len(reader.pages), 2)
        first_page = reader.pages[0].extract_text() or ""
        second_page = reader.pages[1].extract_text() or ""
        self.assertTrue(all(label in first_page for label in labels[:4]))
        self.assertIn(labels[4], second_page)

    def test_zawartosc_przesylki_preferuje_nazwy_czesci(self) -> None:
        settings.dpd_enabled = True
        settings.dpd_mode = "mock"
        payload = DpdShippingClient().build_payload(
            idempotency_key=str(uuid4()),
            reference="MS-2026-NAZWY",
            receiver=_review_payload().address.model_dump(mode="json"),
            weight_kg=3.0,
            items=[
                {"item_index": "IDX-BLK", "item_name": "Toner czarny", "quantity": 1},
                {"item_index": "IDX-YLW", "item_name": "Toner żółty", "quantity": 2},
            ],
        )
        content = payload["packages"][0]["parcels"][0]["content"]
        self.assertEqual(content, "1x Toner czarny; 2x Toner żółty")
        self.assertNotIn("IDX-", content)

    def test_mock_etykieta_skrotem_oznacza_dalsze_pozycje(self) -> None:
        settings.dpd_enabled = True
        settings.dpd_mode = "mock"
        payload = DpdShippingClient().build_payload(
            idempotency_key=str(uuid4()),
            reference="MS-2026-WIELE",
            receiver=_review_payload().address.model_dump(mode="json"),
            weight_kg=2.0,
            business_references=["99/2026"],
        )
        content = build_mock_shipping_label_sheet(
            [
                {
                    "tracking_number": "MOCK-WIELE",
                    "payload": payload,
                    "items": [
                        {
                            "item_index": f"IDX-{index}",
                            "item_name": f"Część testowa {index}",
                            "quantity": index,
                            "unit": "szt.",
                        }
                        for index in range(1, 6)
                    ],
                }
            ]
        )
        text = PdfReader(io.BytesIO(content)).pages[0].extract_text() or ""
        self.assertIn("Część testowa 1", text)
        self.assertIn("Część testowa 2", text)
        self.assertIn("+3 poz. — pełna lista na kompletacji", text)
        self.assertNotIn("Część testowa 5", text)

    def test_mock_etykieta_obsluguje_dlugi_adres_i_brak_telefonu(self) -> None:
        content = build_mock_shipping_label_sheet(
            [
                {
                    "tracking_number": "MOCK-DLUGI-ADRES",
                    "payload": {
                        "receiver": {
                            "companyName": "Bardzo długa nazwa firmy " * 8,
                            "street": "Ulica Testowa 123 lokal 456 piętro 7",
                            "postalCode": "62-052",
                            "city": "Komorniki",
                        },
                        "parcels": [{"weightKg": 1.5, "content": "Materiały serwisowe"}],
                        "reference": "18425/2026",
                    },
                }
            ]
        )
        reader = PdfReader(io.BytesIO(content))
        self.assertEqual(len(reader.pages), 1)
        text = reader.pages[0].extract_text() or ""
        self.assertIn("MOCK-DLUGI-ADRES", text)
        self.assertIn("Tel.: brak", text)
        self.assertIn("18425/2026", text)

    def _configure_demo(self) -> None:
        settings.dpd_enabled = True
        settings.dpd_mode = "demo"
        settings.dpd_api_url = "https://dpdservicesdemo.dpd.com.pl"
        settings.dpd_login = "login-demo"
        settings.dpd_password = "haslo-demo"
        settings.dpd_master_fid = "1234"
        settings.dpd_payer_fid = "5678"
        settings.dpd_sender_company = "Ksero-Partner"
        settings.dpd_sender_contact = "Operator"
        settings.dpd_sender_street = "Fabianowska 165"
        settings.dpd_sender_postal_code = "62-052"
        settings.dpd_sender_city = "Komorniki"
        settings.dpd_sender_phone = "618280118"
        settings.dpd_sender_email = "biuro@example.com"

    def test_demo_uzywa_basic_auth_fid_i_dwuetapowego_api(self) -> None:
        self._configure_demo()
        pdf = _empty_pdf()
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.headers["x-dpd-fid"], "1234")
            self.assertTrue(request.headers["authorization"].startswith("Basic "))
            if request.url.path.endswith("generatePackagesNumbers"):
                body = json.loads(request.content)
                package = body["packages"][0]
                self.assertEqual(package["payerFID"], 5678)
                self.assertEqual(package["receiver"], package["sender"])
                self.assertEqual(package["sender"]["postalCode"], "62052")
                self.assertEqual(package["receiver"]["postalCode"], "62052")
                self.assertEqual(package["ref1"], "77/2026")
                self.assertEqual(
                    package["parcels"][0]["content"],
                    "1x Toner Ricoh MP3554",
                )
                return httpx.Response(
                    200,
                    json={
                        "status": "OK",
                        "sessionId": 991,
                        "packages": [
                            {
                                "status": "OK",
                                "reference": package["reference"],
                                "parcels": [
                                    {
                                        "status": "OK",
                                        "reference": package["parcels"][0]["reference"],
                                        "waybill": "0000419238001",
                                    }
                                ],
                            }
                        ],
                        "traceId": "trace-packages",
                    },
                )
            body = json.loads(request.content)
            self.assertEqual(body["format"], "A4")
            self.assertEqual(body["outputDocFormat"], "PDF")
            self.assertEqual(body["outputType"], "BIC3")
            self.assertEqual(body["variant"], "STANDARD")
            self.assertEqual(body["labelSearchParams"]["session"]["sessionId"], 991)
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "documentData": base64.b64encode(pdf).decode("ascii"),
                    "documentId": "doc-1",
                    "session": {"sessionId": 991, "statusInfo": {"status": "OK"}},
                    "traceId": "trace-label",
                },
            )

        receiver = _review_payload().address.model_dump(mode="json")
        payload, result = DpdShippingClient(transport=httpx.MockTransport(handler)).create_shipment(
            idempotency_key=str(uuid4()),
            reference="MS-2026-77",
            receiver=receiver,
            weight_kg=2.0,
            business_references=["77/2026"],
            items=[
                {
                    "item_index": "842348",
                    "item_name": "Toner Ricoh MP3554",
                    "quantity": 1,
                }
            ],
        )
        self.assertEqual(len(requests), 2)
        self.assertTrue(payload["_ctip"]["demo_receiver_override"])
        self.assertEqual(
            payload["_ctip"]["requested_receiver"]["company"],
            "Przykładowa Firma",
        )
        self.assertEqual(result.tracking_number, "0000419238001")
        self.assertTrue(result.label_content.startswith(b"%PDF"))
        self.assertNotIn("documentData", result.raw_response["generate_labels"])

    def test_zbiorczy_arkusz_przekazuje_numery_waybill(self) -> None:
        self._configure_demo()
        pdf = _empty_pdf()

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            packages = body["labelSearchParams"]["session"]["packages"]
            self.assertEqual(
                [package["parcels"][0]["waybill"] for package in packages],
                ["0001", "0002"],
            )
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "documentData": base64.b64encode(pdf).decode("ascii"),
                    "documentId": "sheet-1",
                    "session": {"statusInfo": {"status": "OK"}},
                    "traceId": "sheet-trace",
                },
            )

        result = DpdShippingClient(transport=httpx.MockTransport(handler)).generate_label_sheet(
            ["0001", "0002", "0001"]
        )
        self.assertEqual(result.document_id, "sheet-1")
        self.assertEqual(result.waybills, ("0001", "0002"))

    def test_timeout_tworzenia_odzyskuje_etykiete_po_referencji(self) -> None:
        self._configure_demo()
        pdf = _empty_pdf()
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            if request.url.path.endswith("generatePackagesNumbers"):
                raise httpx.ReadTimeout("przerwany odczyt", request=request)
            body = json.loads(request.content)
            package = body["labelSearchParams"]["session"]["packages"][0]
            self.assertTrue(package["reference"].startswith("CTIP-"))
            self.assertTrue(package["parcels"][0]["reference"].startswith("CTIP-P-"))
            return httpx.Response(
                200,
                json={
                    "status": "OK",
                    "documentData": base64.b64encode(pdf).decode("ascii"),
                    "documentId": "recovered-1",
                    "session": {
                        "sessionId": 992,
                        "statusInfo": {"status": "OK"},
                        "packages": [{"parcels": [{"waybill": "0000419238992"}]}],
                    },
                    "traceId": "trace-recovery",
                },
            )

        _, result = DpdShippingClient(transport=httpx.MockTransport(handler)).create_shipment(
            idempotency_key=str(uuid4()),
            reference="MS-2026-77",
            receiver=_review_payload().address.model_dump(mode="json"),
            weight_kg=2.0,
            business_references=["77/2026"],
        )
        self.assertEqual(len(requests), 2)
        self.assertEqual(result.tracking_number, "0000419238992")
        self.assertTrue(result.raw_response["recovered_by_reference"])

    def test_demo_blokuje_przypadkowy_host_produkcyjny(self) -> None:
        self._configure_demo()
        settings.dpd_api_url = "https://dpdservices.dpd.com.pl"

        with self.assertRaisesRegex(DpdConfigurationError, "oficjalnego adresu"):
            DpdShippingClient().build_payload(
                idempotency_key=str(uuid4()),
                reference="MS-2026-77",
                receiver=_review_payload().address.model_dump(mode="json"),
                weight_kg=2.0,
                business_references=["77/2026"],
            )

    def test_blad_walidacji_dpd_jest_przetlumaczony(self) -> None:
        self._configure_demo()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "status": "INCORRECT_DATA",
                    "packages": [
                        {
                            "status": "INCORRECT_DATA",
                            "validationInfo": [{"errorCode": "INCORRECT_RECEIVER_POSTAL_CODE"}],
                            "parcels": [],
                        }
                    ],
                    "traceId": "trace-error",
                },
            )

        with self.assertRaisesRegex(DpdTransportError, "Kod pocztowy odbiorcy"):
            DpdShippingClient(transport=httpx.MockTransport(handler)).create_shipment(
                idempotency_key=str(uuid4()),
                reference="MS-2026-77",
                receiver=_review_payload().address.model_dump(mode="json"),
                weight_kg=2.0,
                business_references=["77/2026"],
            )

    def test_wylaczona_integracja_blokuje_nadanie(self) -> None:
        settings.dpd_enabled = False
        settings.dpd_mode = "mock"
        with self.assertRaises(DpdConfigurationError):
            DpdShippingClient().create_shipment(
                idempotency_key=str(uuid4()),
                reference="MS-2026-77",
                receiver=_review_payload().address.model_dump(mode="json"),
                weight_kg=2.0,
            )


class ShippingWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            execution_options={"schema_translate_map": {"ctip": None}},
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[
                    AdminUser.__table__,
                    AdminAuditLog.__table__,
                    ShippingAddress.__table__,
                    ShippingConsumableCompatibility.__table__,
                    ShippingCase.__table__,
                    ShippingItem.__table__,
                    ShippingDayClose.__table__,
                    ShippingShipment.__table__,
                    ShippingEvent.__table__,
                    ShippingTrackingParcel.__table__,
                    ShippingTrackingEvent.__table__,
                    ShippingTrackingSyncRun.__table__,
                ],
            )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine, expire_on_commit=False
        )
        self.previous_dpd_enabled = settings.dpd_enabled
        self.previous_dpd_mode = settings.dpd_mode
        self.previous_dpd_test_mode = settings.dpd_test_mode
        self.previous_shipping_test_firebird_writes = settings.shipping_test_firebird_writes
        settings.dpd_enabled = True
        settings.dpd_mode = "mock"
        settings.dpd_test_mode = True
        settings.shipping_test_firebird_writes = False
        async with self.session_factory() as session:
            session.add(
                AdminUser(
                    id=1,
                    email="operator@example.com",
                    role="operator",
                    password_hash="test",
                    is_active=True,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        settings.dpd_enabled = self.previous_dpd_enabled
        settings.dpd_mode = self.previous_dpd_mode
        settings.dpd_test_mode = self.previous_dpd_test_mode
        settings.shipping_test_firebird_writes = self.previous_shipping_test_firebird_writes
        await self.engine.dispose()

    async def test_review_zapisuje_adres_rezerwacje_i_zgodnosc(self) -> None:
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_physical_stock", return_value=_stock()):
                result = await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["address"]["phone"], "+48500600700")
            self.assertEqual(result["items"][0]["firebird_warehouse_item_id"], 501)
            self.assertEqual(
                await session.scalar(
                    select(func.count()).select_from(ShippingConsumableCompatibility)
                ),
                1,
            )

    async def test_przypisany_technik_blokuje_review(self) -> None:
        order = {**_order(), "technician": "Tomek Kurtek"}
        async with self.session_factory() as session:
            with self.assertRaisesRegex(
                ShippingConflictError,
                "ma przypisanego technika",
            ):
                await review_shipping_order(
                    session,
                    order=order,
                    payload=_review_payload(order),
                    user_id=1,
                )

            self.assertIsNone(await session.scalar(select(ShippingCase)))

    async def test_ponowny_review_zastepuje_te_sama_pozycje_bez_konfliktu(self) -> None:
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_physical_stock", return_value=_stock()):
                await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )
                result = await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )

            self.assertEqual(result["status"], "ready")
            self.assertEqual(len(result["items"]), 1)
            self.assertEqual(
                await session.scalar(select(func.count()).select_from(ShippingItem)),
                1,
            )

    async def test_stan_zerowy_wymaga_jawnej_zgody_operatora(self) -> None:
        async with self.session_factory() as session:
            with (
                patch(
                    "app.services.shipping_workflow.load_physical_stock",
                    return_value=_stock(0),
                ),
                self.assertRaisesRegex(ShippingConflictError, "Zaznacz jawne zezwolenie"),
            ):
                await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )

    async def test_jawna_zgoda_zapisuje_wybor_stanu_ujemnego(self) -> None:
        async with self.session_factory() as session:
            with patch(
                "app.services.shipping_workflow.load_physical_stock",
                return_value=_stock(0),
            ):
                result = await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(allow_negative_stock=True),
                    user_id=1,
                )

            self.assertTrue(result["items"][0]["allow_negative_stock"])
            item = await session.scalar(select(ShippingItem))
            self.assertTrue(item.allow_negative_stock)

    async def test_mock_nadanie_jest_idempotentne_i_nie_zapisuje_firebirda(self) -> None:
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_physical_stock", return_value=_stock()):
                await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )
            key = str(uuid4())
            first = await create_shipping_shipment(
                session,
                order=_order(),
                order_table_id=1001,
                idempotency_key=key,
                user_id=1,
            )
            second = await create_shipping_shipment(
                session,
                order=_order(),
                order_table_id=1001,
                idempotency_key=key,
                user_id=1,
            )
            self.assertEqual(first["shipment"]["id"], second["shipment"]["id"])
            self.assertEqual(first["shipment"]["firebird_status"], "simulated")
            shipment = await session.get(ShippingShipment, first["shipment"]["id"])
            self.assertTrue(shipment.label_content.startswith(b"%PDF"))

    async def test_wspolna_paczka_ma_jedna_etykiete_i_zamyka_dwa_zlecenia(self) -> None:
        first_order = _order(1001)
        second_order = {**_order(1002), "order_id": 78}
        async with self.session_factory() as session:
            with patch(
                "app.services.shipping_workflow.load_physical_stock",
                return_value=_stock(),
            ):
                await review_shipping_order(
                    session,
                    order=first_order,
                    payload=_review_payload(first_order),
                    user_id=1,
                )
                await review_shipping_order(
                    session,
                    order=second_order,
                    payload=_review_payload(second_order),
                    user_id=1,
                )

            dpd = DpdShippingClient()
            with patch(
                "app.services.shipping_workflow.DpdShippingClient.create_shipment",
                wraps=dpd.create_shipment,
            ) as create_dpd_shipment:
                created = await create_consolidated_shipping_shipment(
                    session,
                    orders=[first_order, second_order],
                    order_table_ids=[1001, 1002],
                    idempotency_key=str(uuid4()),
                    user_id=1,
                )

            self.assertEqual(create_dpd_shipment.call_count, 1)
            self.assertTrue(created["consolidated"])
            self.assertEqual(created["printable_order_ids"], [1001, 1002])
            shipments = list(
                (await session.execute(select(ShippingShipment).order_by(ShippingShipment.id)))
                .scalars()
                .all()
            )
            self.assertEqual(len(shipments), 2)
            self.assertEqual(
                {shipment.tracking_number for shipment in shipments},
                {created["tracking_number"]},
            )
            consolidation = shipping_shipment_consolidation(shipments[0])
            self.assertEqual(consolidation["order_table_ids"], [1001, 1002])

            closed = await close_shipping_order(
                session,
                order_table_id=1001,
                user_id=1,
            )

            self.assertTrue(closed["consolidated"])
            self.assertEqual(closed["closed_count"], 2)
            self.assertEqual(closed["newly_closed_count"], 2)
            self.assertEqual(len(closed["order_results"]), 2)
            self.assertEqual(
                set((await session.execute(select(ShippingCase.status))).scalars()),
                {"closed"},
            )
            self.assertEqual(
                set((await session.execute(select(ShippingShipment.status))).scalars()),
                {"closed"},
            )

    async def test_mock_moze_zapisac_pozycje_i_rw_wylacznie_w_trybie_testowym(self) -> None:
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_physical_stock", return_value=_stock()):
                await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )
            with (
                patch.object(
                    type(settings),
                    "shipping_test_firebird_writes_active",
                    new_callable=PropertyMock,
                    return_value=True,
                ),
                patch(
                    "app.services.shipping_workflow.firebird_writes_enabled",
                    return_value=(True, None),
                ),
                patch(
                    "app.services.shipping_workflow.write_shipment_to_order",
                    return_value={"status": "written", "created_position_ids": [901]},
                ) as write_order,
            ):
                created = await create_shipping_shipment(
                    session,
                    order=_order(),
                    order_table_id=1001,
                    idempotency_key=str(uuid4()),
                    user_id=1,
                )

            self.assertEqual(created["shipment"]["firebird_status"], "written")
            write_order.assert_called_once()
            shipment = await session.get(ShippingShipment, created["shipment"]["id"])
            self.assertTrue(shipment.provider_request["ctip_test_firebird_writes"])
            ready_order_state = shipping_order_state_payload(
                {
                    **_order(),
                    "status": "ZR",
                    "tracking_number": shipment.tracking_number,
                }
            )

            with (
                patch(
                    "app.services.shipping_workflow.load_shipping_order_state",
                    return_value=ready_order_state,
                ),
                patch(
                    "app.services.shipping_workflow.finalize_shipping_order",
                    return_value={"status": "created", "rw_id": 1, "rw_number": "1/2026"},
                ) as finalize_order,
                patch("app.services.shipping_workflow._send_notifications", return_value=[]),
            ):
                result = await close_shipping_day(
                    session,
                    business_date=datetime.now(ZoneInfo("Europe/Warsaw")).date(),
                    user_id=1,
                )

            self.assertEqual(result["closed_count"], 1)
            finalize_order.assert_called_once()

    def test_zapis_mock_do_firebirda_ma_twarde_warunki_srodowiska(self) -> None:
        previous = {
            "shipping_test_firebird_writes": settings.shipping_test_firebird_writes,
            "pg_database": settings.pg_database,
            "fb_mode": settings.fb_mode,
            "fb_host": settings.fb_host,
            "fb_database": settings.fb_database,
            "fb_allow_writes": settings.fb_allow_writes,
            "dpd_mode": settings.dpd_mode,
            "dpd_test_mode": settings.dpd_test_mode,
            "sms_test_mode": settings.sms_test_mode,
        }
        try:
            settings.shipping_test_firebird_writes = True
            settings.pg_database = "ctip_test"
            settings.fb_mode = "network"
            settings.fb_host = "127.0.0.1"
            settings.fb_database = "BAZAMS_TEST"
            settings.fb_allow_writes = True
            settings.dpd_mode = "mock"
            settings.dpd_test_mode = True
            settings.sms_test_mode = True
            self.assertTrue(settings.shipping_test_firebird_writes_active)

            settings.dpd_mode = "demo"
            settings.dpd_test_mode = False
            self.assertTrue(settings.shipping_test_firebird_writes_active)

            settings.dpd_mode = "production"
            self.assertFalse(settings.shipping_test_firebird_writes_active)

            settings.dpd_mode = "demo"

            settings.fb_host = "172.28.250.2"
            self.assertFalse(settings.shipping_test_firebird_writes_active)

            settings.fb_host = "192.168.0.8"
            self.assertFalse(settings.shipping_test_firebird_writes_active)
        finally:
            for key, value in previous.items():
                setattr(settings, key, value)

    async def test_zamkniecie_dnia_mock_zamyka_umowe_i_symuluje_powiadomienia(self) -> None:
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_physical_stock", return_value=_stock()):
                await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )
            created = await create_shipping_shipment(
                session,
                order=_order(),
                order_table_id=1001,
                idempotency_key=str(uuid4()),
                user_id=1,
            )
            result = await close_shipping_day(
                session,
                business_date=datetime.now(ZoneInfo("Europe/Warsaw")).date(),
                user_id=1,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["closed_count"], 1)
            shipment = await session.get(ShippingShipment, created["shipment"]["id"])
            self.assertEqual(shipment.status, "closed")
            self.assertEqual(shipment.notification_sms_status, "simulated")
            self.assertEqual(shipment.notification_email_status, "simulated")

    async def test_pojedyncze_zamkniecie_uzywa_tego_samego_procesu(self) -> None:
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_physical_stock", return_value=_stock()):
                await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )
            created = await create_shipping_shipment(
                session,
                order=_order(),
                order_table_id=1001,
                idempotency_key=str(uuid4()),
                user_id=1,
            )

            result = await close_shipping_order(
                session,
                order_table_id=1001,
                user_id=1,
            )

            self.assertEqual(result["status"], "closed")
            self.assertEqual(result["documents"]["document_mode"], "rw")
            self.assertEqual(result["case"]["status"], "closed")
            shipment = await session.get(ShippingShipment, created["shipment"]["id"])
            self.assertEqual(shipment.status, "closed")
            self.assertIsNone(shipment.day_close_id)
            self.assertEqual(shipment.closed_by, 1)
            self.assertEqual(shipment.archive_snapshot["order"]["order_number"], "77/2026")
            self.assertEqual(shipment.archive_snapshot["documents"]["mode"], "rw")
            self.assertIn("przykladowa firma", shipment.archive_search_text)
            self.assertIn("toner czarny", shipment.archive_search_text)
            self.assertEqual(
                await session.scalar(select(func.count()).select_from(ShippingDayClose)),
                0,
            )

            archive = await list_shipping_archive(session)
            self.assertEqual(archive["total"], 1)
            self.assertEqual(archive["items"][0]["order_number"], "77/2026")
            self.assertEqual(archive["items"][0]["item_count"], 1)
            self.assertEqual(archive["items"][0]["operators"]["closed"]["id"], 1)

            detail = await get_shipping_archive_detail(
                session,
                order_table_id=1001,
            )
            self.assertEqual(detail["snapshot"]["recipient"]["city"], "Warszawa")
            self.assertTrue(detail["label_url"].endswith("/label"))
            self.assertEqual(detail["events"][-1]["type"], "courier_handover")

            repeated = await close_shipping_order(
                session,
                order_table_id=1001,
                user_id=1,
            )
            self.assertEqual(repeated["status"], "already_closed")

    def test_normalizacja_archiwum_obsluguje_polskie_znaki_i_numery(self) -> None:
        self.assertEqual(
            normalize_shipping_archive_text("Żółty toner – FV 12/KPSK/2026"),
            "zolty toner fv 12 kpsk 2026",
        )

    async def test_wystaw_fv_na_umowie_tworzy_fv_i_wz(self) -> None:
        payload_data = _review_payload().model_dump(mode="json")
        payload_data["invoice_required"] = True
        payload = ShippingReviewRequest.model_validate(payload_data)
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_physical_stock", return_value=_stock()):
                await review_shipping_order(
                    session,
                    order=_order(),
                    payload=payload,
                    user_id=1,
                )
            created = await create_shipping_shipment(
                session,
                order=_order(),
                order_table_id=1001,
                idempotency_key=str(uuid4()),
                user_id=1,
            )
            result = await close_shipping_day(
                session,
                business_date=datetime.now(ZoneInfo("Europe/Warsaw")).date(),
                user_id=1,
            )
            case = await session.get(ShippingCase, created["id"])
            self.assertEqual(result["closed_count"], 1)
            self.assertEqual(result["wz_count"], 1)
            self.assertEqual(result["invoice_count"], 1)
            self.assertEqual(case.status, "closed")
            self.assertTrue(case.invoice_required)

    async def test_zlecenie_poza_umowa_moze_odznaczyc_fv_i_utworzyc_wz(self) -> None:
        paid_order = {**_order(), "order_kind": "Płatne"}
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_physical_stock", return_value=_stock()):
                result = await review_shipping_order(
                    session,
                    order=paid_order,
                    payload=_review_payload(paid_order),
                    user_id=1,
                )
            self.assertFalse(result["invoice_required"])
            created = await create_shipping_shipment(
                session,
                order=paid_order,
                order_table_id=1001,
                idempotency_key=str(uuid4()),
                user_id=1,
            )
            close_result = await close_shipping_day(
                session,
                business_date=datetime.now(ZoneInfo("Europe/Warsaw")).date(),
                user_id=1,
            )
            case = await session.get(ShippingCase, created["id"])
            self.assertEqual(close_result["closed_count"], 1)
            self.assertEqual(close_result["manual_billing_count"], 0)
            self.assertEqual(close_result["rw_count"], 0)
            self.assertEqual(close_result["wz_count"], 1)
            self.assertEqual(case.status, "closed")

    async def test_zamkniecie_dnia_przekazuje_zaakceptowane_ilosci_i_ceny(self) -> None:
        async with self.session_factory() as session:
            with patch(
                "app.services.shipping_workflow.load_physical_stock",
                return_value=_stock(0),
            ):
                await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(allow_negative_stock=True),
                    user_id=1,
                )
            case = await session.scalar(select(ShippingCase))
            shipment = ShippingShipment(
                shipping_case_id=case.id,
                idempotency_key=str(uuid4()),
                provider_mode="manual",
                provider_shipment_id="123456789",
                tracking_number="123456789",
                status="label_ready",
                provider_request={},
                provider_response={},
                firebird_status="written",
                created_by=1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(shipment)
            case.status = "shipment_created"
            await session.commit()
            ready_order_state = shipping_order_state_payload(
                {
                    **_order(),
                    "status": "ZR",
                    "tracking_number": "123456789",
                }
            )

            with (
                patch(
                    "app.services.shipping_workflow.load_shipping_order_state",
                    return_value=ready_order_state,
                ),
                patch(
                    "app.services.shipping_workflow.finalize_shipping_order",
                    return_value={"status": "created", "rw_id": 1, "rw_number": "1/2026"},
                ) as finalize_order,
                patch(
                    "app.services.shipping_workflow._send_notifications",
                    return_value=[],
                ),
            ):
                await close_shipping_day(
                    session,
                    business_date=datetime.now(ZoneInfo("Europe/Warsaw")).date(),
                    user_id=1,
                )

            items = finalize_order.call_args.kwargs["items"]
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["firebird_warehouse_item_id"], 501)
            self.assertEqual(items[0]["quantity"], 1.0)
            self.assertEqual(items[0]["price_net"], 60.0)
            self.assertEqual(items[0]["catalog_price_net"], 120.0)
            self.assertEqual(items[0]["purchase_price_net"], 60.0)
            self.assertEqual(items[0]["price_source"], "purchase_contract")
            self.assertTrue(items[0]["allow_negative_stock"])

    async def test_zrealizowane_w_ms_blokuje_potwierdzenie_wysylki(self) -> None:
        async with self.session_factory() as session:
            with patch(
                "app.services.shipping_workflow.load_physical_stock",
                return_value=_stock(),
            ):
                await review_shipping_order(
                    session,
                    order=_order(),
                    payload=_review_payload(),
                    user_id=1,
                )
            case = await session.scalar(select(ShippingCase))
            shipment = ShippingShipment(
                shipping_case_id=case.id,
                idempotency_key=str(uuid4()),
                provider_mode="manual",
                provider_shipment_id="123456789",
                tracking_number="123456789",
                status="label_ready",
                provider_request={},
                provider_response={},
                firebird_status="written",
                created_by=1,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            session.add(shipment)
            case.status = "shipment_created"
            await session.commit()
            completed_state = shipping_order_state_payload(
                {
                    **_order(),
                    "status": "Z",
                    "tracking_number": "123456789",
                }
            )

            with (
                patch(
                    "app.services.shipping_workflow.load_shipping_order_state",
                    return_value=completed_state,
                ),
                patch("app.services.shipping_workflow.finalize_shipping_order") as finalize,
                self.assertRaisesRegex(ShippingConflictError, "zrealizowane"),
            ):
                await close_shipping_order(
                    session,
                    order_table_id=1001,
                    user_id=1,
                )

            finalize.assert_not_called()
            self.assertEqual(case.status, "reconcile_required")
            self.assertEqual(shipment.status, "reconcile_required")
            self.assertEqual(
                await session.scalar(
                    select(func.count())
                    .select_from(ShippingEvent)
                    .where(ShippingEvent.event_type == "external_order_state_conflict")
                ),
                1,
            )

    async def test_zmiana_lokalizacji_blokuje_utworzenie_przesylki(self) -> None:
        original_order = _order()
        changed_order = {**original_order, "machine_location": "Magazyn, piętro 1"}
        async with self.session_factory() as session:
            with patch("app.services.shipping_workflow.load_physical_stock", return_value=_stock()):
                await review_shipping_order(
                    session,
                    order=original_order,
                    payload=_review_payload(original_order),
                    user_id=1,
                )
            with self.assertRaises(ShippingLocationChangedError):
                await create_shipping_shipment(
                    session,
                    order=changed_order,
                    order_table_id=1001,
                    idempotency_key=str(uuid4()),
                    user_id=1,
                )
            case = await session.scalar(select(ShippingCase))
            self.assertEqual(case.status, "review_pending")
            self.assertIsNone(await session.scalar(select(ShippingShipment)))

    async def test_stary_fingerprint_blokuje_ponowna_akceptacje(self) -> None:
        original_order = _order()
        changed_order = {**original_order, "machine_location": "Nowa lokalizacja"}
        async with self.session_factory() as session:
            with self.assertRaises(ShippingLocationChangedError):
                await review_shipping_order(
                    session,
                    order=changed_order,
                    payload=_review_payload(original_order),
                    user_id=1,
                )
            self.assertIsNone(await session.scalar(select(ShippingCase)))


class ShippingSchemaTests(unittest.TestCase):
    def test_blokady_etapowego_wdrozenia_sa_wymuszane_przez_backend(self) -> None:
        with (
            patch.object(settings, "shipping_catalog_mutations_enabled", False),
            self.assertRaises(HTTPException) as catalog_error,
        ):
            _require_catalog_mutations()
        with (
            patch.object(settings, "shipping_fulfillment_enabled", False),
            self.assertRaises(HTTPException) as fulfillment_error,
        ):
            _require_fulfillment()

        self.assertEqual(catalog_error.exception.status_code, 423)
        self.assertEqual(fulfillment_error.exception.status_code, 423)

        with (
            patch.object(settings, "shipping_catalog_mutations_enabled", True),
            patch.object(settings, "shipping_fulfillment_enabled", True),
        ):
            _require_catalog_mutations()
            _require_fulfillment()

    def test_widoki_shipping_sa_domyslnie_wylaczone(self) -> None:
        app = create_app()
        with patch.object(settings, "shipping_enabled", False):
            response = TestClient(app).get("/shipping")

        self.assertEqual(response.status_code, 503)

    def test_glowny_widok_shipping_uzywa_funkcjonalnego_v2(self) -> None:
        app = create_app()
        client = TestClient(app)
        with patch.object(settings, "shipping_enabled", True):
            response = client.get("/shipping")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Wysyłki i dokumenty", response.text)
        self.assertIn("Poprzedni wygląd", response.text)
        self.assertIn('href="/shipping/legacy"', response.text)
        self.assertIn("Archiwum", response.text)
        self.assertIn('id="shipping-archive-view"', response.text)
        self.assertIn("Status przesyłek", response.text)
        self.assertIn('id="shipping-tracking-view"', response.text)
        self.assertIn('id="shipping-tracking-sync"', response.text)
        self.assertIn(
            f"/static/shipping/shipping-v2.css?v={app.version}-tracking-02",
            response.text,
        )
        self.assertIn('id="shipping-order-state-warning"', response.text)
        self.assertIn('id="shipping-sort"', response.text)
        self.assertIn('id="shipping-queue-refresh"', response.text)
        self.assertIn("Etap pracy (domyślne)", response.text)
        self.assertIn('id="shipping-phone-note"', response.text)
        self.assertIn("Zezwól na część ze stanem zerowym", response.text)
        javascript = Path("app/static/shipping/shipping.js").read_text(encoding="utf-8")
        required_ids = set(re.findall(r'getElementById\("([^"]+)"\)', javascript))
        rendered_ids = set(re.findall(r'id="([^"]+)"', response.text))
        self.assertFalse(required_ids - rendered_ids)
        stylesheet = Path("app/static/shipping/shipping-v2.css").read_text(encoding="utf-8")
        self.assertIn("linear-gradient(rgba(126, 154, 184, .055)", stylesheet)
        self.assertIn(".shipping-v2-body .shipping-alert", stylesheet)
        self.assertIn("background: #14282f", stylesheet)
        self.assertIn(".shipping-negative-stock-control", stylesheet)
        self.assertIn("font-size: 17px", stylesheet)
        self.assertIn("height: calc(100vh - 104px)", stylesheet)
        self.assertIn("min-height: 0; max-height: none", stylesheet)
        enhancer = Path("app/static/shipping/shipping-v2.js").read_text(encoding="utf-8")
        self.assertNotIn("fetch(", enhancer)
        self.assertIn("MutationObserver", enhancer)
        self.assertIn("selectedPackageItems", enhancer)
        self.assertIn("shipping-v2-package-items", response.text)
        self.assertIn("refreshShippingQueueManually", javascript)
        self.assertIn("usunięte z kolejki", javascript)

    def test_dotychczasowy_adres_v2_przekierowuje_do_glownego_widoku(self) -> None:
        app = create_app()
        client = TestClient(app, follow_redirects=False)
        with patch.object(settings, "shipping_enabled", True):
            response = client.get("/shipping/v2")

        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/shipping")

    def test_poprzedni_widok_pozostaje_dostepny_pod_osobnym_adresem(self) -> None:
        app = create_app()
        client = TestClient(app)
        with patch.object(settings, "shipping_enabled", True):
            response = client.get("/shipping/legacy")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Wysyłki części i tonerów", response.text)
        self.assertIn('href="/shipping"', response.text)

    def test_strona_prototypow_pokazuje_siedem_niefunkcjonalnych_wariantow(
        self,
    ) -> None:
        app = create_app()
        with patch.object(settings, "shipping_enabled", True):
            response = TestClient(app).get("/shipping/prototypes")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count('data-prototype="'), 7)
        for label in (
            "Operacyjny",
            "Etapowy",
            "Dyspozytornia",
            "Dokumentowy",
            "Minimalny",
            "Hybryda operacyjna",
            "Hybryda dokumentowa",
            "Realizacja wysyłek",
            "Katalog zgodności",
            "Archiwum",
            "RW 3421/2026",
        ):
            self.assertIn(label, response.text)
        self.assertIn(
            f"/static/shipping/shipping-prototypes.css?v={app.version}",
            response.text,
        )
        javascript = Path("app/static/shipping/shipping-prototypes.js").read_text(encoding="utf-8")
        self.assertNotIn("fetch(", javascript)
        self.assertIn("^#wariant-([1-7])$", javascript)

    def test_polski_numer_telefonu_jest_normalizowany(self) -> None:
        payload = _review_payload()
        self.assertEqual(payload.address.phone, "+48500600700")
        self.assertEqual(payload.weight_kg, Decimal("2.000"))

    def test_tekstowa_stawka_vat_z_firebirda_jest_normalizowana(self) -> None:
        self.assertEqual(_vat_rate("23 %"), Decimal("23"))

    def test_tryb_dokumentow_rozroznia_umowe_wz_i_fakture(self) -> None:
        self.assertEqual(
            shipping_document_mode(order_kind="Umowa", invoice_required=False),
            "rw",
        )
        self.assertEqual(
            shipping_document_mode(order_kind="Płatne", invoice_required=False),
            "wz",
        )
        self.assertEqual(
            shipping_document_mode(order_kind="Umowa", invoice_required=True),
            "invoice_wz",
        )

    def test_stan_zlecenia_blokuje_zamkniete_i_przypisane_technikowi(self) -> None:
        completed = shipping_order_state_payload({**_order(), "status": "Z"})
        assigned = shipping_order_state_payload({**_order(), "technician": "Tomek Kurtek"})
        shipping = shipping_order_state_payload(
            {**_order(), "technician": SHIPPING_TECHNICIAN_NAME}
        )

        self.assertTrue(completed["completed"])
        self.assertFalse(completed["eligible_for_shipping"])
        self.assertFalse(completed["can_finalize"])
        self.assertTrue(assigned["has_assigned_technician"])
        self.assertEqual(assigned["assigned_technician"], "Tomek Kurtek")
        self.assertFalse(assigned["can_review"])
        self.assertFalse(assigned["can_prepare_shipment"])
        self.assertFalse(shipping["has_assigned_technician"])
        self.assertTrue(shipping["has_shipping_technician"])
        self.assertEqual(shipping["shipping_technician"], SHIPPING_TECHNICIAN_NAME)
        self.assertTrue(shipping["can_review"])
        self.assertTrue(shipping["can_prepare_shipment"])

    def test_kolejka_wyklucza_zlecenia_z_przypisanym_technikiem(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = []
        with patch(
            "app.services.shipping_firebird.firebird_connection",
            return_value=connection,
        ):
            result = load_shipping_queue(days=30)

        query = cursor.execute.call_args.args[0]
        parameters = cursor.execute.call_args.args[1]
        self.assertEqual(result, [])
        self.assertIn("z.TYP_US = ?", query)
        self.assertIn("z.STAN IN (?, ?)", query)
        self.assertIn("TRIM(z.TECHNIK)", query)
        self.assertIn("TRIM(z.TECHNIK2)", query)
        self.assertIn("ON m.ID_KLIENT = z.ID_KLIENT", query)
        self.assertNotIn("m.ID_FIRMA = z.ID_FIRMA", query)
        self.assertEqual(parameters.count(SHIPPING_TECHNICIAN_NAME), 2)

    def test_szczegoly_zlecenia_uzywaja_globalnych_id_klienta_i_maszyny(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.description = [
            ("order_table_id",),
            ("order_id",),
            ("order_year",),
            ("status",),
            ("company_id",),
            ("client_id",),
            ("machine_id",),
            ("model_id",),
            ("order_kind",),
            ("order_operator",),
        ]
        cursor.fetchone.return_value = (83493, 18493, 2026, "O", 1, 2954, 7222, 458, "Umowa", "")
        cursor.fetchall.side_effect = [[], []]

        with patch(
            "app.services.shipping_firebird.firebird_connection",
            return_value=connection,
        ):
            result = load_shipping_order(83493)

        detail_query = cursor.execute.call_args_list[0].args[0]
        contact_queries = [call.args for call in cursor.execute.call_args_list[1:]]
        self.assertEqual(result["model_id"], 458)
        self.assertIn("ON k.ID_KLIENT = z.ID_KLIENT", detail_query)
        self.assertIn("ON o.ID_ODDZIAL = z.ID_ODDZIAL", detail_query)
        self.assertIn("ON m.ID_KLIENT = z.ID_KLIENT", detail_query)
        self.assertNotIn("k.ID_FIRMA = z.ID_FIRMA", detail_query)
        self.assertNotIn("m.ID_FIRMA = z.ID_FIRMA", detail_query)
        self.assertTrue(all(args[1] == (2954,) for args in contact_queries))

    def test_zapis_etykiety_nie_otwiera_ponownie_zrealizowanego_zlecenia(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = (
            15,
            44,
            77,
            2026,
            "Z",
            "MOCK123",
            None,
            None,
        )
        with (
            patch(
                "app.services.shipping_firebird.firebird_writes_enabled",
                return_value=(True, None),
            ),
            patch(
                "app.services.shipping_firebird.firebird_connection",
                return_value=connection,
            ),
            self.assertRaisesRegex(
                ShippingOrderStateConflict,
                "zrealizowane",
            ),
        ):
            write_shipment_to_order(
                order_table_id=1001,
                tracking_number="MOCK123",
                items=[],
            )

        queries = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
        self.assertNotIn("INSERT INTO ZPOZYCJA", queries)
        self.assertNotIn("SET PRZESYLKA", queries)
        connection.rollback.assert_called_once()

    def test_rw_powstaje_w_zakupy_z_kolejnym_numerem_magazynowym(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [
            (
                1,
                1,
                15,
                44,
                77,
                2026,
                "Przykładowa Firma",
                "Testowa 10",
                "00-001",
                "Warszawa",
                "1234567890",
                "Umowa",
                None,
                None,
                None,
                None,
                None,
                "MOCK123",
                None,
                "Ricoh",
                "MPC 3003",
                "ZR",
                None,
                None,
            ),
            None,
            None,
            None,
            None,
            (2089,),
            (38711,),
        ]
        cursor.fetchall.return_value = [
            (
                501,
                1,
                "1. Część zamienna",
                "TONER-1",
                "Toner testowy",
                "szt.",
                Decimal("120"),
                Decimal("60"),
                Decimal("23"),
                1,
                Decimal("5"),
            )
        ]
        with (
            patch(
                "app.services.shipping_firebird.firebird_writes_enabled",
                return_value=(True, None),
            ),
            patch(
                "app.services.shipping_firebird.firebird_connection",
                return_value=connection,
            ),
        ):
            result = finalize_shipping_order(
                order_table_id=1001,
                warehouse_id=1,
                items=[
                    {
                        "firebird_warehouse_item_id": 501,
                        "quantity": 1,
                        "price_net": 60,
                        "purchase_price_net": 60,
                        "vat_rate": 23,
                    }
                ],
                invoice_required=False,
                tracking_number="MOCK123",
                issued_by="Operator Testowy",
                shipping_address=None,
            )

        executed_queries = "\n".join(call.args[0] for call in cursor.execute.call_args_list)
        for execute_call in cursor.execute.call_args_list:
            if len(execute_call.args) > 1:
                self.assertEqual(
                    execute_call.args[0].count("?"),
                    len(execute_call.args[1]),
                )
        self.assertEqual(result["rw_id"], 38711)
        self.assertEqual(result["rw_number"], "RW / 2089 / 2026")
        self.assertIn("INSERT INTO ZAKUPY", executed_queries)
        self.assertIn("RODZAJ_DOK = 'RW'", executed_queries)
        self.assertIn("INSERT INTO ZAKPOZYCJA", executed_queries)
        self.assertNotIn("'ROK'", executed_queries)
        connection.commit.assert_called_once()

    def test_wz_powiazane_z_faktura_zapisuje_numer_fv_w_dokumencie_zewnetrznym(
        self,
    ) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [
            (
                1,
                1,
                739,
                7229,
                18495,
                2026,
                "Przykładowa Firma",
                "Testowa 10",
                "00-001",
                "Warszawa",
                "1234567890",
                "Płatne",
                None,
                None,
                None,
                None,
                None,
                "MOCK123",
                None,
                "Ricoh",
                "MPC 2011",
                "ZR",
                None,
                None,
            ),
            None,
            None,
            None,
            None,
            (185,),
            (38791,),
            (64557, 5318),
        ]
        cursor.fetchall.return_value = [
            (
                501,
                1,
                "2. Towar inny",
                "TONER-1",
                "Toner testowy",
                "szt.",
                Decimal("490"),
                Decimal("120"),
                Decimal("23"),
                1,
                Decimal("5"),
            )
        ]
        with (
            patch(
                "app.services.shipping_firebird.firebird_writes_enabled",
                return_value=(True, None),
            ),
            patch(
                "app.services.shipping_firebird.firebird_connection",
                return_value=connection,
            ),
        ):
            result = finalize_shipping_order(
                order_table_id=83495,
                warehouse_id=1,
                items=[
                    {
                        "firebird_warehouse_item_id": 501,
                        "quantity": 1,
                        "price_net": 490,
                        "purchase_price_net": 120,
                        "vat_rate": 23,
                    }
                ],
                invoice_required=True,
                tracking_number="MOCK123",
                issued_by="Operator Testowy",
                shipping_address=None,
            )

        document_link_calls = [
            execute_call
            for execute_call in cursor.execute.call_args_list
            if "UPDATE ZAKUPY SET DOK_ZEW" in execute_call.args[0]
        ]
        executed_queries = "\n".join(
            execute_call.args[0] for execute_call in cursor.execute.call_args_list
        )
        self.assertEqual(result["wz_id"], 38791)
        self.assertEqual(result["invoice_number"], "5318/KPSK/2026")
        self.assertEqual(len(document_link_calls), 1)
        self.assertEqual(document_link_calls[0].args[1], ("5318/KPSK/2026", 38791))
        self.assertIn("POBRANO, ILOSCWZ, PARAGON", executed_queries)
        self.assertIn("?, ?, ?, 0)", executed_queries)
        connection.commit.assert_called_once()

    def test_ponowienie_fv_z_wz_odtwarza_numer_fv_w_dokumencie_zewnetrznym(
        self,
    ) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [
            (
                1,
                1,
                739,
                7229,
                18495,
                2026,
                "Przykładowa Firma",
                "Testowa 10",
                "00-001",
                "Warszawa",
                "1234567890",
                "Płatne",
                64557,
                None,
                None,
                "5318/KPSK/2026",
                None,
                "MOCK123",
                datetime(2026, 8, 28).date(),
                "Ricoh",
                "MPC 2011",
                "Z",
                None,
                None,
            ),
            (64557, "5318/KPSK/2026", 38791),
            (38791, "WZ / 185 / 2026"),
            None,
            ("WZ / 185 / 2026",),
        ]
        with (
            patch(
                "app.services.shipping_firebird.firebird_writes_enabled",
                return_value=(True, None),
            ),
            patch(
                "app.services.shipping_firebird.firebird_connection",
                return_value=connection,
            ),
        ):
            result = finalize_shipping_order(
                order_table_id=83495,
                warehouse_id=1,
                items=[
                    {
                        "firebird_warehouse_item_id": 501,
                        "quantity": 1,
                        "price_net": 490,
                        "purchase_price_net": 120,
                        "vat_rate": 23,
                    }
                ],
                invoice_required=True,
                tracking_number="MOCK123",
                issued_by="Operator Testowy",
                shipping_address=None,
            )

        document_link_calls = [
            execute_call
            for execute_call in cursor.execute.call_args_list
            if "UPDATE ZAKUPY SET DOK_ZEW" in execute_call.args[0]
        ]
        self.assertEqual(result["status"], "already_exists")
        self.assertEqual(len(document_link_calls), 1)
        self.assertEqual(document_link_calls[0].args[1], ("5318/KPSK/2026", 38791))
        connection.commit.assert_called_once()

    def test_umowa_uzywa_ceny_zakupu_niezaleznie_od_podanej_ceny(self) -> None:
        selected, catalog, purchase, source = _shipping_item_price(
            order_kind="Umowa",
            invoice_required=False,
            requested_price=Decimal("99.00"),
            warehouse=_stock()[0],
        )

        self.assertEqual(selected, Decimal("60.0"))
        self.assertEqual(catalog, Decimal("120.0"))
        self.assertEqual(purchase, Decimal("60.0"))
        self.assertEqual(source, "purchase_contract")

    def test_poza_umowa_podpowiada_cene_sprzedazy_i_pozwala_ja_zmienic(self) -> None:
        selected, _, _, source = _shipping_item_price(
            order_kind="Płatne",
            invoice_required=False,
            requested_price=Decimal("99.00"),
            warehouse=_stock()[0],
        )

        self.assertEqual(selected, Decimal("99.00"))
        self.assertEqual(source, "manual")

    def test_brak_ceny_sprzedazy_wyroznia_cene_zakupu_jako_awaryjna(self) -> None:
        warehouse = {**_stock()[0], "price_net": 0}
        selected, _, purchase, source = _shipping_item_price(
            order_kind="Płatne",
            invoice_required=True,
            requested_price=None,
            warehouse=warehouse,
        )

        self.assertEqual(selected, Decimal("60.0"))
        self.assertEqual(purchase, Decimal("60.0"))
        self.assertEqual(source, "purchase_fallback")

    def test_zbiorcze_mapowanie_usuwa_powtorzone_modele(self) -> None:
        payload = ShippingCompatibilityManualBatchRequest.model_validate(
            {
                "firebird_model_ids": [900, 901, 900],
                "firebird_warehouse_item_id": 501,
            }
        )
        self.assertEqual(payload.firebird_model_ids, [900, 901])

    def test_zbiorcze_etykiety_wymagaja_wyboru_albo_wszystkich_gotowych(self) -> None:
        selected = ShippingBulkCreateRequest.model_validate(
            {"order_table_ids": [1001, 1002, 1001], "all_ready": False}
        )
        self.assertEqual(selected.order_table_ids, [1001, 1002])
        all_ready = ShippingBulkCreateRequest.model_validate(
            {"order_table_ids": [], "all_ready": True}
        )
        self.assertTrue(all_ready.all_ready)
        for invalid in (
            {"order_table_ids": [], "all_ready": False},
            {"order_table_ids": [1001], "all_ready": True},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                ShippingBulkCreateRequest.model_validate(invalid)

    def test_wspolna_paczka_wymaga_dwoch_roznych_zlecen(self) -> None:
        payload = ShippingConsolidatedCreateRequest.model_validate(
            {
                "order_table_ids": [1001, 1002, 1001],
                "idempotency_key": str(uuid4()),
            }
        )
        self.assertEqual(payload.order_table_ids, [1001, 1002])
        with self.assertRaises(ValidationError):
            ShippingConsolidatedCreateRequest.model_validate(
                {
                    "order_table_ids": [1001, 1001],
                    "idempotency_key": str(uuid4()),
                }
            )

    def test_wyszukiwanie_dzieli_fraze_na_niezalezne_wyrazy(self) -> None:
        self.assertEqual(_search_terms("toner  MPC 3503 toner"), ["toner", "MPC", "3503"])

    def test_podsumowanie_zaleglych_faktur_grupuje_kwote_i_opoznienie(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            (1, 15, 2, Decimal("450.50"), datetime(2026, 8, 1)),
        ]
        with patch(
            "app.services.shipping_firebird.firebird_connection",
            return_value=connection,
        ):
            result = load_shipping_overdue_summaries(
                {(1, 15), (1, 16)},
                as_of=datetime(2026, 8, 25).date(),
            )

        self.assertTrue(result[(1, 15)]["has_overdue_invoices"])
        self.assertEqual(result[(1, 15)]["invoice_count"], 2)
        self.assertEqual(result[(1, 15)]["total_overdue_amount"], 450.5)
        self.assertEqual(result[(1, 15)]["max_days_overdue"], 24)
        self.assertFalse(result[(1, 16)]["has_overdue_invoices"])
        query, parameters = cursor.execute.call_args.args
        self.assertIn("RODZAJ_DOK = ?", query)
        self.assertEqual(parameters[-1], "KPSK")

    def test_szczegoly_zaleglej_faktury_zawieraja_kwoty_i_dni(self) -> None:
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            (
                501,
                123,
                "123/KPSK/2026",
                datetime(2026, 7, 1),
                datetime(2026, 7, 15),
                Decimal("615.00"),
                Decimal("115.00"),
                Decimal("500.00"),
            )
        ]
        with patch(
            "app.services.shipping_firebird.firebird_connection",
            return_value=connection,
        ):
            result = load_shipping_overdue_invoices(
                company_id=1,
                client_id=15,
                as_of=datetime(2026, 8, 25).date(),
            )

        self.assertEqual(result["total_overdue_amount"], 500.0)
        self.assertEqual(result["max_days_overdue"], 41)
        self.assertEqual(result["invoices"][0]["invoice_number"], "123/KPSK/2026")
        self.assertEqual(result["invoices"][0]["amount_gross"], 615.0)
        self.assertEqual(result["invoices"][0]["amount_due"], 500.0)

    def test_generator_etykiety_ma_fallback_uuid_dla_http_w_lan(self) -> None:
        javascript = Path("app/static/shipping/shipping.js").read_text(encoding="utf-8")
        self.assertIn("function shippingCatalogMutationsEnabled()", javascript)
        self.assertIn("function shippingFulfillmentEnabled()", javascript)
        self.assertIn("Tryb wdrożeniowy", javascript)
        self.assertIn("function shippingRequestUuid()", javascript)
        self.assertIn("idempotency_key: shippingRequestUuid()", javascript)
        self.assertIn("shipping-generate-selected", javascript)
        self.assertIn("shipping-generate-consolidated", javascript)
        self.assertIn("/admin/shipping/shipments/consolidated", javascript)
        self.assertIn("function selectShippingConsolidationGroup()", javascript)
        self.assertIn("shipping-print-selected", javascript)
        self.assertIn("shipping-print-packing", javascript)
        self.assertIn("/admin/shipping/shipments/labels-sheet", javascript)
        self.assertIn("/admin/shipping/shipments/packing-list", javascript)
        self.assertIn("/admin/shipping/dpd/demo-diagnostic", javascript)
        self.assertIn('review_pending: "Do weryfikacji"', javascript)
        self.assertIn('shipment_created: "Etykieta wygenerowana"', javascript)
        self.assertIn('manual_billing: "Do wystawienia FV"', javascript)
        self.assertIn('source === "mobile" ? "Aplikacja mobilna"', javascript)
        self.assertIn("shipping-invoice-required", javascript)
        self.assertIn(
            "caseData ? Boolean(caseData.invoice_required) : !contractOrder",
            javascript,
        )
        self.assertIn("invoiceInput.disabled = Boolean(caseData?.shipment);", javascript)
        self.assertIn("shipping-consolidation-warning", javascript)
        self.assertIn("function renderShippingOverduePayment(payment)", javascript)
        self.assertIn("has-overdue-payment", javascript)
        self.assertIn("Użyj danych", javascript)
        self.assertIn("fillShippingAddress(candidate.address, true)", javascript)
        self.assertIn("allow_negative_stock", javascript)
        self.assertIn("shipping-allow-negative-stock", javascript)
        self.assertIn("shipping-v2-location-note", javascript)
        self.assertIn("shipping-close-order", javascript)
        self.assertIn("/close`,", javascript)
        self.assertIn("SHIPPING_ORDER_STATE_REFRESH_MS = 30000", javascript)
        self.assertIn("/state`", javascript)
        self.assertIn("function ensureShippingOrderState(action)", javascript)
        self.assertIn("function shippingQueueWorkflowRank(item)", javascript)
        self.assertIn("function loadShippingArchive(resetPage = false)", javascript)
        self.assertIn("/admin/shipping/archive?", javascript)
        self.assertIn("function renderShippingArchiveDetail(payload)", javascript)
        archive_template = Path("app/templates/shipping/v2.html").read_text(encoding="utf-8")
        self.assertIn('data-shipping-view="archive"', archive_template)
        self.assertIn('id="shipping-archive-query"', archive_template)
        self.assertIn('id="shipping-archive-detail"', archive_template)
        self.assertIn("function sortShippingQueue(items)", javascript)
        self.assertIn("has_overdue_invoices) return 3", javascript)
        self.assertIn("unit_price_net", javascript)
        self.assertIn("Brak ceny sprzedaży — użyto ceny zakupu", javascript)
        self.assertIn('return invoiceRequired ? "invoice_wz"', javascript)
        self.assertNotIn('addressLine || "Brak danych adresowych"', javascript)
        stylesheet = Path("app/static/shipping/shipping.css").read_text(encoding="utf-8")
        self.assertIn(".shipping-consolidation-warning[hidden] { display: none; }", stylesheet)
        self.assertIn(".shipping-payment-warning[hidden] { display: none; }", stylesheet)
        self.assertIn(".shipping-purchase-fallback-row", stylesheet)

    def test_powtorzony_numer_telefonu_zlecenia_jest_redukowany(self) -> None:
        self.assertEqual(_phone_key("614365026 614365026 "), "614365026")

    def test_parser_pobiera_wiarygodny_telefon_z_tresci_zlecenia(self) -> None:
        self.assertEqual(
            _extract_phone_from_order_text("mgt 790 742 957"),
            "+48790742957",
        )
        self.assertEqual(
            _extract_phone_from_order_text("Telefon: +48 790-742-957"),
            "+48790742957",
        )
        self.assertEqual(
            _extract_phone_from_order_text("kontakt 790742957"),
            "+48790742957",
        )

    def test_parser_nie_myli_indeksu_ani_numeru_zlecenia_z_telefonem(self) -> None:
        self.assertIsNone(_extract_phone_from_order_text("Zlecenie 18425/2026"))
        self.assertIsNone(_extract_phone_from_order_text("Indeks 2021400211764"))

    def test_autor_zlecenia_mobilnego_jest_dopasowany_do_kontaktu(self) -> None:
        contact = {
            "id": 10,
            "name": "Anna Nowak",
            "phone": "+48500600700",
            "email": "anna@example.com",
            "is_mobile_user": True,
            "match_names": {"anna nowak"},
            "match_logins": {"anna@example.com"},
            "match_phones": {"500600700"},
            "match_emails": {"anna@example.com"},
        }
        order = {
            "created_from_mobile_app": True,
            "order_operator": "Utworzył z aplikacji: Anna Nowak Login: anna@example.com",
            "contact_name": "Anna Nowak",
            "order_phone": "500 600 700",
            "order_email": "anna@example.com",
        }

        self.assertIs(_match_shipping_mobile_contact(order, [contact]), contact)

    def test_zrodlo_zlecenia_rozpoznaje_jawny_znacznik_aplikacji(self) -> None:
        self.assertTrue(
            _created_from_mobile_app("Utworzył z aplikacji: Anna Nowak Login: anna@example.com")
        )
        self.assertFalse(_created_from_mobile_app("Utworzył: JoannaG Edytował: JoannaG"))

    def test_zwykle_zlecenie_nie_jest_dopasowywane_do_konta_mobilnego(self) -> None:
        contact = {
            "is_mobile_user": True,
            "match_names": {"anna nowak"},
            "match_logins": {"anna@example.com"},
            "match_phones": {"500600700"},
            "match_emails": {"anna@example.com"},
        }
        self.assertIsNone(
            _match_shipping_mobile_contact(
                {
                    "created_from_mobile_app": False,
                    "order_email": "anna@example.com",
                },
                [contact],
            )
        )

    def test_parser_rozpoznaje_obslugiwane_formaty_adresu(self) -> None:
        examples = {
            "ul. Janiszowska 14/2, 02-264 Warszawa": (
                "ul. Janiszowska 14/2",
                "02-264",
                "Warszawa",
            ),
            "Kraków 30-376 Tyniecka 173A tel.: 500 600 700": (
                "Tyniecka 173A",
                "30-376",
                "Kraków",
            ),
            "Al Solidarności 34, Kielce 25-323": (
                "Al Solidarności 34",
                "25-323",
                "Kielce",
            ),
            "Kicin Ul. Nowe Osiedle 51, 62-004Czerwonak": (
                "Kicin Ul. Nowe Osiedle 51",
                "62-004",
                "Czerwonak",
            ),
        }
        for raw, expected in examples.items():
            with self.subTest(raw=raw):
                parsed = parse_shipping_location(raw)
                self.assertIsNotNone(parsed)
                self.assertEqual(
                    (parsed["street"], parsed["postal_code"], parsed["city"]),
                    expected,
                )

    def test_parser_odrzuca_opisowa_lokalizacje(self) -> None:
        for raw in ("Kontrola jakości", "budynek MOSINOVA", "biura - góra Nieszawska"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_shipping_location(raw))

    def test_fingerprint_ignoruje_kosmetyke_ale_wykrywa_zmiane(self) -> None:
        original = _order()
        cosmetic = {**original, "order_location": "  SEKRETARIAT - PARTER "}
        changed = {**original, "order_location": "Sekretariat, piętro"}
        self.assertEqual(
            shipping_location_context(original)["fingerprint"],
            shipping_location_context(cosmetic)["fingerprint"],
        )
        self.assertNotEqual(
            shipping_location_context(original)["fingerprint"],
            shipping_location_context(changed)["fingerprint"],
        )
        self.assertEqual(normalize_shipping_location("A / 10"), "a 10")

    def test_stary_zapisany_adres_nie_jest_wybierany_po_zmianie_lokalizacji(self) -> None:
        old_order = _order()
        changed_order = {**old_order, "machine_location": "Nowa lokalizacja"}
        saved = ShippingAddress(
            id=1,
            firebird_client_id=15,
            firebird_machine_id=44,
            location_key="test",
            location_source="order",
            location_text_snapshot=old_order["order_location"],
            location_fingerprint=shipping_location_context(old_order)["fingerprint"],
            company_name="Przykładowa Firma",
            street="Stara 1",
            postal_code="00-001",
            city="Warszawa",
            country_code="PL",
            phone="+48500600700",
            source="saved",
        )
        candidates, preferred = build_shipping_address_candidates(changed_order, [saved])
        saved_candidate = next(item for item in candidates if item["key"] == "saved-1")
        self.assertFalse(saved_candidate["selectable"])
        self.assertFalse(saved_candidate["usable"])
        self.assertEqual(preferred["key"], "order")

    def test_brak_telefonu_nie_usuwa_podpowiedzi_adresu_zlecenia(self) -> None:
        order = {**_order(), "order_phone": "", "client_phone": None}
        candidates, preferred = build_shipping_address_candidates(order, [])
        order_candidate = next(item for item in candidates if item["key"] == "order")
        self.assertFalse(order_candidate["selectable"])
        self.assertTrue(order_candidate["usable"])
        self.assertEqual(preferred["key"], "manual")
        self.assertEqual(preferred["address"]["street"], "Testowa 10")
        self.assertEqual(preferred["address"]["postal_code"], "00-001")
        self.assertEqual(preferred["address"]["city"], "Warszawa")
        self.assertEqual(preferred["missing_fields"], ["phone"])

    def test_puste_strukturalne_zrodla_adresu_nie_sa_zwracane(self) -> None:
        order = {
            **_order(),
            "order_street": None,
            "order_postal_code": None,
            "order_city": None,
            "branch_street": None,
            "branch_postal_code": None,
            "branch_city": None,
            "client_street": None,
            "client_postal_code": None,
            "client_city": None,
        }
        candidates, preferred = build_shipping_address_candidates(order, [])
        keys = {candidate["key"] for candidate in candidates}
        self.assertTrue({"branch", "order", "client"}.isdisjoint(keys))
        self.assertEqual(preferred["key"], "manual")

    def test_dane_kontaktu_mobilnego_maja_pierwszenstwo_w_adresie(self) -> None:
        order = {
            **_order(),
            "preferred_contact_name": "Anna Nowak",
            "preferred_contact_phone": "+48555666777",
            "preferred_contact_email": "anna@example.com",
        }
        _, preferred = build_shipping_address_candidates(order, [])
        self.assertEqual(preferred["address"]["contact_name"], "Anna Nowak")
        self.assertEqual(preferred["address"]["phone"], "+48555666777")
        self.assertEqual(preferred["address"]["email"], "anna@example.com")

    def test_grupowanie_wspolnej_paczki_wymaga_tego_samego_adresu_i_stanu_ready(self) -> None:
        common_address = {
            "company_name": "Przykładowa Firma",
            "street": "Testowa 10",
            "postal_code": "00-001",
            "city": "Warszawa",
        }
        cases = [
            SimpleNamespace(
                firebird_order_table_id=1001,
                firebird_order_id=77,
                firebird_order_year=2026,
                status="ready",
                shipment=None,
                address_snapshot=common_address,
            ),
            SimpleNamespace(
                firebird_order_table_id=1002,
                firebird_order_id=78,
                firebird_order_year=2026,
                status="ready",
                shipment=None,
                address_snapshot={**common_address, "street": " TESTOWA 10 "},
            ),
            SimpleNamespace(
                firebird_order_table_id=1003,
                firebird_order_id=79,
                firebird_order_year=2026,
                status="ready",
                shipment=None,
                address_snapshot={**common_address, "street": "Inna 1"},
            ),
            SimpleNamespace(
                firebird_order_table_id=1004,
                firebird_order_id=80,
                firebird_order_year=2026,
                status="shipment_created",
                shipment=object(),
                address_snapshot=common_address,
            ),
        ]
        groups = build_shipping_consolidation_groups(cases)
        self.assertEqual(set(groups), {1001, 1002})
        self.assertEqual(groups[1001]["order_numbers"], ["77/2026", "78/2026"])


def _compatibility_source(item_name: str = "Toner Ricoh IM C3000 czarny") -> dict:
    return {
        "items": [
            {
                "warehouse_item_id": 501,
                "warehouse_id": 1,
                "item_kind": "1. Część zamienna",
                "item_index": "842255",
                "catalog_number_1": None,
                "catalog_number_2": None,
                "item_name": item_name,
                "brand": None,
                "model": None,
            }
        ],
        "models": [
            {
                "id_model": 900,
                "marka": "Ricoh",
                "model": "IM C3000",
                "toner": "842255",
                "toner_c": None,
                "toner_m": None,
                "toner_y": None,
            }
        ],
        "history": [
            {
                "warehouse_item_id": 501,
                "model_id": 900,
                "order_count": 3,
                "machine_count": 2,
            }
        ],
    }


class ShippingCompatibilityRulesTests(unittest.TestCase):
    def test_dwa_niezalezne_sygnaly_daja_wysoka_pewnosc(self) -> None:
        candidates = derive_compatibility_candidates(_compatibility_source())
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["confidence"], "high")
        self.assertEqual(
            {entry["source"] for entry in candidates[0]["evidence"]},
            {"name", "catalog", "history"},
        )

    def test_model_301_nie_pasuje_do_3010(self) -> None:
        source = _compatibility_source("Toner Ricoh MPC3010 czarny")
        source["models"] = [
            {"id_model": 301, "marka": "Ricoh", "model": "MPC 301"},
            {"id_model": 3010, "marka": "Ricoh", "model": "MPC 3010"},
        ]
        source["history"] = []
        candidates = derive_compatibility_candidates(source)
        self.assertEqual(
            {candidate["firebird_model_id"] for candidate in candidates},
            {3010},
        )

    def test_urzadzenie_z_numerem_seryjnym_nie_jest_sugerowane_jako_czesc(self) -> None:
        source = _compatibility_source("Ricoh IM C3000 S/N: 123456, nr.wew: KP/100")
        source["items"][0]["item_index"] = "KP/100"
        self.assertEqual(derive_compatibility_candidates(source), [])

    def test_jedna_czesc_moze_miec_wiele_sugerowanych_modeli(self) -> None:
        source = _compatibility_source("Toner Ricoh MPC 3003/3503 YLW")
        source["items"][0]["item_index"] = "MPC-YLW"
        source["models"] = [
            {"id_model": 3003, "marka": "Ricoh", "model": "MPC 3003"},
            {"id_model": 3503, "marka": "Ricoh", "model": "MPC 3503"},
        ]
        source["history"] = []
        candidates = derive_compatibility_candidates(source)
        self.assertEqual(
            {candidate["firebird_model_id"] for candidate in candidates},
            {3003, 3503},
        )

    def test_skan_nie_miesza_serii_o_tym_samym_numerze(self) -> None:
        source = _compatibility_source("Wałek Ricoh MPC 2000/2500")
        source["models"] = [
            {"id_model": 1, "marka": "Ricoh", "model": "MP C2500"},
            {"id_model": 2, "marka": "Ricoh", "model": "MP 2500"},
            {"id_model": 3, "marka": "Ricoh", "model": "IM C2500"},
        ]
        source["history"] = []
        candidates = derive_compatibility_candidates(source)
        self.assertEqual(
            {candidate["firebird_model_id"] for candidate in candidates},
            {1},
        )


class ShippingCompatibilityPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            execution_options={"schema_translate_map": {"ctip": None}},
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(
                Base.metadata.create_all,
                tables=[
                    AdminUser.__table__,
                    AdminAuditLog.__table__,
                    ShippingConsumableCompatibility.__table__,
                ],
            )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.session_factory() as session:
            session.add(
                AdminUser(
                    id=1,
                    email="operator@example.com",
                    role="operator",
                    password_hash="test",
                    is_active=True,
                    created_at=datetime.now(UTC),
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_skan_nie_nadpisuje_odrzucenia_operatora(self) -> None:
        async with self.session_factory() as session:
            await scan_compatibility_catalog(session, source=_compatibility_source())
            row = await session.scalar(select(ShippingConsumableCompatibility))
            row.status = "rejected"
            row.reviewed_by = 1
            await session.commit()
            await scan_compatibility_catalog(session, source=_compatibility_source())
            await session.refresh(row)
            self.assertEqual(row.status, "rejected")

    async def test_czesc_jest_grupowana_z_wieloma_niezaleznymi_modelami(self) -> None:
        models = [
            {"id_model": 3003, "marka": "Ricoh", "model": "MPC 3003"},
            {"id_model": 3503, "marka": "Ricoh", "model": "MP C3503"},
            {"id_model": 4503, "marka": "Ricoh", "model": "MPC 4503"},
        ]
        item = _compatibility_source("Toner Ricoh YLW")["items"][0]
        async with self.session_factory() as session:
            rows = await confirm_manual_compatibilities(
                session,
                models=[*models, models[0]],
                item=item,
                user_id=1,
            )
            self.assertEqual(len(rows), 3)
            await review_compatibilities(
                session,
                mapping_ids=[rows[1].id],
                action="reject",
                user_id=1,
                note=None,
            )
            grouped = await list_compatibility_items(
                session,
                status=None,
                confidence=None,
                query="toner mpc 3503",
                page=1,
                page_size=25,
            )
            self.assertEqual(grouped["total"], 1)
            self.assertEqual(len(grouped["items"][0]["models"]), 1)
            self.assertEqual(grouped["items"][0]["models"][0]["status"], "rejected")
            statuses = {
                row.firebird_model_id: row.status
                for row in (
                    await session.execute(select(ShippingConsumableCompatibility))
                ).scalars()
            }
            self.assertEqual(
                statuses,
                {3003: "confirmed", 3503: "rejected", 4503: "confirmed"},
            )

    async def test_web_search_zapisuje_tylko_sugestie_z_cytowaniem(self) -> None:
        previous_enabled = settings.shipping_compatibility_web_enabled
        settings.shipping_compatibility_web_enabled = True
        response = {
            "id": "resp_test",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": '{"matches":[{"warehouse_item_id":501,"models":["Ricoh IM C3000"],"source_urls":["https://example.com/catalog"]}]}',
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://example.com/catalog",
                                    "title": "Katalog producenta",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        try:
            async with self.session_factory() as session:
                with (
                    patch(
                        "app.services.shipping_compatibility_web.load_assistant_runtime_config",
                        return_value=SimpleNamespace(api_key="test-key"),
                    ),
                    patch(
                        "app.services.shipping_compatibility_web._call_web_search",
                        return_value=response,
                    ),
                ):
                    result = await enrich_compatibilities_with_web(
                        session,
                        source=_compatibility_source(),
                        warehouse_item_ids=[501],
                    )
                row = await session.scalar(select(ShippingConsumableCompatibility))
                self.assertEqual(result["created"], 1)
                self.assertEqual(row.status, "suggested")
                self.assertEqual(row.evidence[0]["url"], "https://example.com/catalog")
        finally:
            settings.shipping_compatibility_web_enabled = previous_enabled
