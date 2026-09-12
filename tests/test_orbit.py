"""Izolowany odbiór projekcji ORBIT, uprawnień i powtarzanych importów."""

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes import admin_orbit
from app.core.config import settings
from app.models import (
    AdminAuditLog,
    AdminUser,
    Base,
    OrbitDevice,
    OrbitEvent,
    OrbitEvidence,
    OrbitPolicy,
    OrbitRun,
    TonerYield,
    telemetry,
)
from app.services import orbit


def snapshot(price="100", *, status="active"):
    """Oddziela cenę zakupu od sprzedaży na syntetycznym urządzeniu."""
    return {
        "complete": True,
        "devices": [
            {
                "id": "ms:7",
                "ms_machine_id": 7,
                "serial": "ORBIT-TEST-7",
                "model": "IM C300",
                "model_id": 3,
                "customer": "Klient testowy",
                "customer_id": 9,
                "contract_id": 8,
                "status": status,
            }
        ],
        "facts": [
            {
                "external_key": "issue:10",
                "device_id": "ms:7",
                "kind": "material_issue",
                "observed_at": "2026-09-10",
                "time_precision": "date",
                "status": "confirmed",
                "data": {
                    "is_contract": True,
                    "contract_id": 8,
                    "customer_id": 9,
                    "purchase_price": price,
                    "sale_price": "180",
                    "issued_quantity": "2",
                    "returned_quantity": "0",
                    "currency": "PLN",
                    "raw": {"invoice": "PRYWATNE"},
                },
            }
        ],
    }


def create_tables(connection):
    """Tworzy wyłącznie tabele potrzebne testowi, bez źródeł i komunikacji."""
    Base.metadata.create_all(
        connection,
        tables=[
            *telemetry.TELEMETRY_TABLES,
            *[
                model.__table__
                for model in (
                    AdminAuditLog,
                    AdminUser,
                    OrbitDevice,
                    OrbitEvent,
                    OrbitEvidence,
                    OrbitPolicy,
                    OrbitRun,
                    TonerYield,
                )
            ],
        ],
    )


class OrbitProjectionTests(unittest.TestCase):
    """Sprawdza jedną transakcję importu i odtwarzalny model raportowy."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite://", execution_options={"schema_translate_map": {"ctip": None}}
        )
        with self.engine.begin() as connection:
            create_tables(connection)

    def tearDown(self):
        self.engine.dispose()

    def test_purchase_cost_and_no_finance_leak(self):
        with self.engine.begin() as connection:
            orbit.ingest_snapshot(connection, snapshot())
            orbit.project(connection)
            report = orbit.device_detail(connection, "ms:7", can_view_finance=True)
            self.assertEqual(report["finance"]["purchase_costs"], {"PLN": "200.00"})
            public = orbit.device_detail(connection, "ms:7")
            self.assertNotIn("finance", public)
            self.assertNotIn("PRYWATNE", str(public))
            event = orbit.timeline(connection, "ms:7")["items"][0]
            self.assertNotIn("finance", event)
            self.assertNotIn("purchase_price", str(event))
            self.assertNotIn("raw", event["data"])
            evidence = orbit.evidence(connection, "ms:7", event["id"])
            self.assertEqual(evidence.pop("evidence"), [event])
            self.assertEqual(evidence, event)
            with self.assertRaises(LookupError):
                orbit.evidence(connection, "ms:8", event["id"])

    def test_repeat_and_a_b_a_do_not_duplicate_costs(self):
        with self.engine.begin() as connection:
            for price in ("100", "120", "100", "100"):
                orbit.ingest_snapshot(connection, snapshot(price))
                orbit.project(connection)
            events = connection.execute(select(OrbitEvent.__table__)).mappings().all()
            records = connection.execute(select(telemetry.record)).mappings().all()
            self.assertEqual(len(events), 1)
            self.assertEqual(len(records), 3)
            report = orbit.device_detail(connection, "ms:7", can_view_finance=True)
            self.assertEqual(report["finance"]["purchase_costs"], {"PLN": "200.00"})

    def test_source_withdrawal_retains_original_and_removes_current_fact(self):
        """Usunięta pozycja Shipping nie pozostaje fikcyjną dostawą w bieżącym bilansie."""
        from app.services.telemetry.parsers import Reading
        from app.services.telemetry.store import TelemetryStore

        with self.engine.begin() as connection:
            orbit.ingest_snapshot(connection, snapshot())
            store = TelemetryStore(connection)
            source = store.ensure_source("shipping_orbit", "database")
            store.ingest(
                source["id"],
                "test",
                [
                    Reading(
                        "shipment:1:1",
                        "shipment",
                        "ORBIT-TEST-7",
                        {"device_id": "ms:7", "data": {"quantity": "1"}, "status": "confirmed"},
                    )
                ],
            )
            orbit.project(connection)
            readings = []
            orbit.append_withdrawals(
                connection, source, readings, lambda payload: payload.get("device_id") == "ms:7"
            )
            self.assertEqual(len(readings), 1)
            store.ingest(source["id"], "test-withdrawal", readings)
            orbit.project(connection)
            self.assertEqual(
                [item["kind"] for item in orbit.timeline(connection, "ms:7")["items"]],
                ["material_issue"],
            )

    def test_components_keep_individual_observation_times(self):
        """Migawka serwisowa nie odświeża czasu starszego pomiaru tonera."""
        from app.services.telemetry.daily import daily_reading
        from app.services.telemetry.store import TelemetryStore

        with self.engine.begin() as connection:
            orbit.ingest_snapshot(connection, snapshot())
            store = TelemetryStore(connection)
            source = store.ensure_source("printradar", "database")
            reading = daily_reading(
                {
                    "source": "printradar",
                    "serial": "ORBIT-TEST-7",
                    "day": "2026-09-10",
                    "components": {
                        "toner": {
                            "key": "toner",
                            "time": "2026-09-10T08:00:00+00:00",
                            "precision": "second",
                            "measurements": {"toner.black.percent": 50},
                        },
                        "counters": {
                            "key": "counters",
                            "time": "2026-09-10T18:00:00+00:00",
                            "precision": "second",
                            "measurements": {"lifetime.total": 1000},
                        },
                    },
                }
            )
            store.ingest(source["id"], "daily", [reading])
            orbit.project(connection)
            report = orbit.device_detail(connection, "ms:7")
            self.assertEqual(report["toners"][0]["observed_at"], "2026-09-10T08:00:00+00:00")

    def test_missing_purchase_is_not_sale_price_and_return_reduces_cost(self):
        with self.engine.begin() as connection:
            orbit.ingest_snapshot(connection, snapshot(None))
            orbit.project(connection)
            report = orbit.device_detail(connection, "ms:7", can_view_finance=True)
            self.assertEqual(report["finance"]["purchase_costs"], {})
            self.assertEqual(report["finance"]["missing_costs"], 1)
            returned = snapshot()
            returned["facts"][0]["data"]["returned_quantity"] = "1"
            orbit.ingest_snapshot(connection, returned)
            orbit.project(connection)
            self.assertEqual(
                orbit.device_detail(connection, "ms:7", can_view_finance=True)["finance"][
                    "purchase_costs"
                ],
                {"PLN": "100.00"},
            )

    def test_archive_resume_and_scrap_retain_history(self):
        with self.engine.begin() as connection:
            for state in ("active", "suspended", "active", "scrapped"):
                orbit.ingest_snapshot(connection, snapshot(status=state))
                orbit.project(connection)
                self.assertEqual(orbit.timeline(connection, "ms:7")["total"], 1)
                self.assertEqual(orbit.list_devices(connection)["total"], int(state == "active"))
            self.assertEqual(orbit.list_devices(connection, scope="scrapped")["total"], 1)

    def test_conflicting_source_never_becomes_known_cost(self):
        incoming = snapshot()
        incoming["facts"][0]["status"] = "conflict"
        with self.engine.begin() as connection:
            orbit.ingest_snapshot(connection, incoming)
            orbit.project(connection)
            self.assertEqual(
                orbit.device_detail(connection, "ms:7", can_view_finance=True)["finance"][
                    "purchase_costs"
                ],
                {},
            )

    def test_filters_are_isolated_and_wildcards_literal(self):
        with self.engine.begin() as connection:
            orbit.ingest_snapshot(connection, snapshot())
            orbit.project(connection)
            self.assertEqual(orbit.list_devices(connection, query="%")["total"], 0)
            self.assertEqual(orbit.list_devices(connection, query="C300")["total"], 1)
            self.assertEqual(orbit.timeline(connection, "ms:7", contract_id="999")["total"], 0)
            self.assertEqual(
                orbit.timeline(connection, "ms:7", date_from=date(2026, 9, 11))["total"], 0
            )

    def test_cpc_reuses_original_and_never_fabricates_daily_reading(self):
        incoming = snapshot()
        raw = {
            "ID_CPC_TABLE": 45,
            "ID_MASZYNA": 7,
            "ID_KLIENT": 9,
            "ID_UMOWACPC": 8,
            "ID_FAKTURA": 0,
            "ROK": 2026,
            "MIESIAC": 8,
            "LICZNIK_MONO_START": 1000,
            "LICZNIK_MONO_END": 2000,
            "OPLATA_SUMA": "500",
        }
        incoming["facts"] = [
            {
                "external_key": "CPC:45",
                "device_id": "ms:7",
                "kind": "cpc",
                "data": {"raw": {"CPC": [raw]}},
            }
        ]
        with self.engine.begin() as connection:
            orbit.ingest_snapshot(connection, incoming)
            orbit.project(connection)
            report = orbit.device_detail(connection, "ms:7")
            point = report["counters"][0]["points"][0]
            self.assertEqual(point["value"], "2000")
            self.assertIsNone(point["delta"])
            self.assertIsNone(point["observed_at"])
            self.assertEqual(point["time_precision"], "month")
            timeline = orbit.timeline(connection, "ms:7", bucket="day", date_from=date(2026, 8, 15))
            self.assertEqual(timeline["total"], 1)
            self.assertEqual(timeline["buckets"], [{"period": "month:2026-08", "events": 1}])

    def test_incomplete_snapshot_has_no_side_effects(self):
        incoming = snapshot()
        incoming["complete"] = False
        with self.engine.begin() as connection:
            with self.assertRaises(ValueError):
                orbit.ingest_snapshot(connection, incoming)
            self.assertEqual(orbit.list_devices(connection)["total"], 0)

    def test_withdrawn_source_fact_retains_original_and_can_return(self):
        with self.engine.begin() as connection:
            orbit.ingest_snapshot(connection, snapshot())
            orbit.project(connection)
            removed = snapshot()
            removed["facts"] = []
            orbit.ingest_snapshot(connection, removed)
            orbit.project(connection)
            self.assertEqual(orbit.timeline(connection, "ms:7")["total"], 0)
            self.assertEqual(len(connection.execute(select(telemetry.record)).all()), 3)
            orbit.ingest_snapshot(connection, snapshot())
            orbit.project(connection)
            self.assertEqual(orbit.timeline(connection, "ms:7")["total"], 1)
            self.assertEqual(
                orbit.device_detail(connection, "ms:7", can_view_finance=True)["finance"][
                    "purchase_costs"
                ],
                {"PLN": "200.00"},
            )

    def test_missing_identity_does_not_erase_device_history(self):
        with self.engine.begin() as connection:
            orbit.ingest_snapshot(connection, snapshot())
            orbit.project(connection)
            incoming = snapshot(status="review")
            incoming["devices"][0]["serial"] = None
            orbit.ingest_snapshot(connection, incoming)
            orbit.project(connection)
            self.assertEqual(orbit.device_row(connection, "ms:7")["serial"], "ORBIT-TEST-7")
            self.assertEqual(orbit.timeline(connection, "ms:7")["total"], 1)

    def test_contract_assignment_is_historical_and_ambiguous_stays_unassigned(self):
        device = {"status": "active", "data": {"contract_id": 8}}
        contracts = [
            {
                "kind": "contract",
                "contract_id": "8",
                "data": {"start": "2026-01-01", "end": "2026-08-01", "customer_id": 9},
            }
        ]
        reading = {
            "kind": "reading",
            "contract_id": None,
            "observed_at": orbit.instant("2026-03-01"),
            "data": {},
        }
        orbit.assign_contracts(device, [*contracts, reading])
        self.assertEqual(reading["contract_id"], "8")
        reading["contract_id"] = None
        contracts.append(
            {
                "kind": "contract",
                "contract_id": "9",
                "data": {"start": "2026-02-01", "end": "2026-05-01", "customer_id": 9},
            }
        )
        orbit.assign_contracts(device, [*contracts, reading])
        self.assertIsNone(reading["contract_id"])

    def test_cached_financial_fields_and_nested_period_are_not_public(self):
        hidden = {"purchase_price": "TAJNA_KWOTA"}
        cached = {
            "summary": {"events": 1, **hidden},
            "contracts": [{"id": "8", **hidden, "period": {"start": hidden}}],
            "narrative": "TAJNA_KWOTA",
            "zero_cost_confirmed": True,
            "finance": hidden,
            "unexpected_finance": hidden,
        }
        result = orbit.public_projection(cached, {"serial": "TEST"}, can_view_finance=False)
        self.assertNotIn("TAJNA_KWOTA", str(result))
        self.assertNotIn("zero_cost_confirmed", orbit.safe_data({"zero_cost_confirmed": True}))
        self.assertIsNone(result["contracts"][0]["period"]["start"])

    def test_documented_issue_is_not_final_cost_without_currency_and_returns(self):
        incoming = snapshot()
        incoming["facts"][0]["data"].update(
            currency=None,
            returned_quantity=None,
            issue_purchase_cost={
                "status": "known",
                "amount": "200.00",
                "scope": "before_returns",
                "currency": None,
                "returns_status": "unverified",
            },
        )
        with self.engine.begin() as connection:
            orbit.ingest_snapshot(connection, incoming)
            orbit.project(connection)
            report = orbit.device_detail(connection, "ms:7", can_view_finance=True)
            self.assertEqual(report["finance"]["purchase_costs"], {})
            self.assertEqual(report["finance"]["documented_issues"][0]["amount"], "200.00")
            self.assertNotIn("documented_issues", str(orbit.device_detail(connection, "ms:7")))

    def test_bad_counter_point_breaks_delta_instead_of_disappearing(self):
        device = {"id": "ms:7", "ms_machine_id": 7, "model": "TEST", "data": {}}
        events = []
        for index, (day, counter, issues) in enumerate(
            (
                ("2026-09-01", 100, []),
                ("2026-09-02", 200, ["conflicting_value"]),
                ("2026-09-03", 300, []),
            )
        ):
            row = {
                "id": str(index),
                "kind": "reading",
                "payload": {},
                "source_name": "remote_reporting",
                "observed_at": orbit.instant(day),
                "measurements": {"lifetime.total": counter},
                "source_id": "remote",
                "external_key": str(index),
                "semantic_key": str(index),
                "time_precision": "date",
                "quality_issues": issues,
            }
            events.append(orbit.normalize_record(row, device))
        result = orbit.report_for(device, events, [])
        self.assertIsNone(result["counters"][0]["points"][-1]["delta"])


class OrbitApiTests(unittest.IsolatedAsyncioTestCase):
    """Odbiór HTTP bez kont produkcyjnych, wiadomości i dostępu do Firebirda."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            execution_options={"schema_translate_map": {"ctip": None}},
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(create_tables)
            await connection.run_sync(lambda sync: orbit.ingest_snapshot(sync, snapshot()))
            await connection.run_sync(orbit.project)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.app = FastAPI()
        self.app.include_router(admin_orbit.router)
        self.user = SimpleNamespace(role="operator", can_view_orbit_finance=False)

        async def database():
            async with self.sessions() as session:
                yield session

        self.app.dependency_overrides[get_db_session] = database
        self.app.dependency_overrides[get_admin_session_context] = lambda: (None, self.user)
        self.access = patch.object(
            admin_orbit, "_require_shipping_access", AsyncMock(side_effect=lambda *args: self.user)
        )
        self.enabled = patch.object(settings, "shipping_orbit_enabled", True)
        self.access.start()
        self.enabled.start()
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        self.access.stop()
        self.enabled.stop()
        await self.engine.dispose()

    async def test_feature_gate_and_permissions(self):
        base = "/admin/shipping/orbit"
        response = await self.client.get(base + "/ms:7")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("finance", response.json())
        self.user.can_view_orbit_finance = True
        response = await self.client.get(base + "/ms:7")
        self.assertEqual(response.json()["finance"]["purchase_costs"]["PLN"], "200.00")
        self.user.can_view_orbit_finance = False
        self.user.role = "admin"
        self.assertIn("finance", (await self.client.get(base + "/ms:7")).json())
        with patch.object(settings, "shipping_orbit_enabled", False):
            self.assertFalse((await self.client.get(base + "/capabilities")).json()["enabled"])
            for path in ("", "/ms:7", "/ms:7/timeline", "/ms:7/evidence/unknown"):
                self.assertEqual((await self.client.get(base + path)).status_code, 404)

    async def test_validation_and_no_external_read(self):
        base = "/admin/shipping/orbit"
        for suffix in ("?page_size=101", "?scope=bad"):
            self.assertEqual((await self.client.get(base + suffix)).status_code, 422)
        response = await self.client.get(base + "/ms:7?date_from=2026-09-11&date_to=2026-09-10")
        self.assertEqual(response.status_code, 422)
        self.assertEqual((await self.client.get(base + "/ms:999")).status_code, 404)
        with patch(
            "app.services.telemetry.sources.firebird_connection",
            side_effect=AssertionError("Źródło nie może być wywołane przez HTTP"),
        ):
            self.assertEqual((await self.client.get(base + "/ms:7/timeline")).status_code, 200)

    async def test_policy_admin_audit_and_operator_denial(self):
        """Zapis bez prawa jest zabroniony; ustawienie i audyt powstają atomowo."""
        path = "/admin/shipping/orbit/policies"
        payload = {
            "scope": "device",
            "scope_id": "ms:7",
            "color": "black",
            "values": {"spare_toners": 2},
        }
        self.assertEqual((await self.client.put(path, json=payload)).status_code, 403)
        self.user.role, self.user.id = "admin", 1
        self.assertEqual((await self.client.put(path, json=payload)).status_code, 200)
        result = (await self.client.get(path + "?device_id=ms:7")).json()
        self.assertEqual(result["effective"]["black"]["spare_toners"], 2)
        self.assertEqual(result["effective"]["cyan"]["spare_toners"], 0)
        async with self.sessions() as session:
            entries = (await session.execute(select(AdminAuditLog))).scalars().all()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].action, "orbit_policy_update")
        payload["values"] = {}
        self.assertEqual((await self.client.put(path, json=payload)).status_code, 200)
        result = (await self.client.get(path + "?device_id=ms:7")).json()
        self.assertEqual(result["effective"]["black"]["spare_toners"], 0)

    async def test_assessment_validation_and_feature_flag(self):
        """Błędny szkic nie dociera do silnika, wyłączony moduł nie otwiera nowych tabel."""
        path = "/admin/shipping/orbit/shipment-assessment"
        self.assertEqual((await self.client.post(path, json={"drafts": []})).status_code, 422)
        payload = {
            "drafts": [
                {"order_table_id": 1, "device_id": "ms:7", "items": [{"item_id": 1, "quantity": 1}]}
            ]
        }
        with patch.object(settings, "shipping_orbit_enabled", False):
            self.assertEqual((await self.client.post(path, json=payload)).status_code, 404)
