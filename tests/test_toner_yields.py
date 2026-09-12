"""Izolowane testy katalogu wydajności; bez dostępu do produkcji i dostawców."""

import unittest
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes import admin_toner_yields
from app.core.config import settings
from app.models import (
    AdminSetting,
    AdminUser,
    Base,
    ShippingConsumableCompatibility,
    TonerYield,
    TonerYieldChange,
    TonerYieldEvidence,
)
from app.schemas.toner_yield import TonerYieldUpdate
from app.services.toner_yield_source import is_toner, model_key, toner_color
from app.services.toner_yields import (
    YieldConflict,
    change_yield,
    import_yields,
    list_yields,
    lower_compatible_estimate,
    refresh_catalog,
    yield_detail,
)


def sample_document():
    """Zwraca syntetyczne dane jednego potwierdzonego wkładu."""
    return {
        "format": "ctip-toner-yields-v1",
        "items": [
            {
                "item_id": 11,
                "warehouse_id": 1,
                "item_index": "TEST-C3000-BK",
                "name": "Toner testowy IM C3000 czarny",
                "brand": "Ricoh",
                "color": "black",
                "stock": 0,
                "scope": "active",
                "models": [{"brand": "Ricoh", "model": "IM C3000"}],
                "value": {
                    "pages": 31000,
                    "status": "confirmed",
                    "source": "Katalog testowy",
                    "reason": "Import testowy",
                },
                "evidence": [{"source": "Katalog testowy", "pages": 31000}],
            }
        ],
    }


class TonerYieldStoreTests(unittest.IsolatedAsyncioTestCase):
    """Sprawdza transakcje wartości, źródeł i odświeżania metadanych."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            execution_options={"schema_translate_map": {"ctip": None}},
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(
                    sync,
                    tables=[
                        model.__table__
                        for model in (
                            AdminUser,
                            AdminSetting,
                            ShippingConsumableCompatibility,
                            TonerYield,
                            TonerYieldEvidence,
                            TonerYieldChange,
                        )
                    ],
                )
            )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_import_is_idempotent_and_manual_value_survives(self):
        async with self.sessions() as session:
            first = await import_yields(session, sample_document())
            await session.commit()
            second = await import_yields(session, sample_document())
            self.assertEqual(first["values_added"], 1)
            self.assertEqual(second["values_added"], 0)
            self.assertEqual(second["evidence_added"], 0)
            await change_yield(
                session,
                11,
                TonerYieldUpdate(
                    revision=1,
                    pages=29000,
                    status="estimated",
                    source="Pomiar testowy",
                    basis="Konserwatywny szacunek",
                    reason="Korekta testowa",
                ),
                actor_label="Operator testowy",
            )
            await session.commit()
            await import_yields(session, sample_document())
            detail = await yield_detail(session, 11)
            self.assertEqual(detail["pages"], 29000)
            self.assertTrue(detail["manual_override"])
            self.assertEqual(len(detail["history"]), 2)
            self.assertEqual(len(detail["evidence"]), 1)
            self.assertEqual(detail["history"][0]["before"]["pages"], 31000)

    async def test_stale_revision_and_rollback_do_not_append_history(self):
        async with self.sessions() as session:
            await import_yields(session, sample_document())
            await session.commit()
            with self.assertRaises(YieldConflict):
                await change_yield(
                    session,
                    11,
                    TonerYieldUpdate(revision=0, status="missing", reason="Nieaktualna wersja"),
                )
            await session.rollback()
            await change_yield(
                session,
                11,
                TonerYieldUpdate(revision=1, status="missing", reason="Wycofanie danych"),
            )
            await session.rollback()
            detail = await yield_detail(session, 11)
            self.assertEqual(detail["pages"], 31000)
            self.assertEqual(len(detail["history"]), 1)

    async def test_refresh_recalculates_contracts_without_changing_yield(self):
        async with self.sessions() as session:
            await import_yields(session, sample_document())
            await session.commit()
            snapshot = {
                "warehouse_id": 1,
                "items": [
                    {
                        "item_id": 11,
                        "name": "Toner nowa nazwa",
                        "item_index": "TEST-C3000-BK",
                        "brand": "Ricoh",
                        "stock": 2,
                    }
                ],
                "models": {},
                "active_keys": [],
            }
            await refresh_catalog(session, snapshot)
            inactive = await yield_detail(session, 11)
            self.assertEqual(inactive["scope"], "inactive")
            self.assertEqual(inactive["pages"], 31000)
            self.assertEqual(inactive["revision"], 1)
            snapshot["active_keys"] = [model_key("Ricoh", "IM C3000")]
            await refresh_catalog(session, snapshot)
            self.assertEqual((await yield_detail(session, 11))["scope"], "active")
            self.assertIsNotNone((await list_yields(session, warehouse_id=1))["sync"])

    async def test_search_scope_zero_stock_and_summary(self):
        document = sample_document()
        other = deepcopy(document["items"][0])
        other.update(item_id=12, scope="review", color="magenta", stock=2)
        other["value"].update(status="estimated", pages=18000, basis="Niższy zgodny wariant")
        document["items"].append(other)
        async with self.sessions() as session:
            await import_yields(session, document)
            result = await list_yields(session, warehouse_id=1, query="imc3000 test")
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["stock"], 0)
            result = await list_yields(session, warehouse_id=1, scope="all")
            self.assertEqual(result["summary"]["confirmed_percent"], 50)
            self.assertEqual(result["summary"]["filled_percent"], 100)
            self.assertEqual(result["summary"]["review"], 1)
            result = await list_yields(
                session, warehouse_id=1, scope="all", color="magenta", only_available=True
            )
            self.assertEqual([item["item_id"] for item in result["items"]], [12])

    async def test_rejected_mapping_does_not_keep_toner_on_active_contract(self):
        document = sample_document()
        document["items"][0]["models"][0]["id"] = 99
        now = datetime.now(UTC)
        async with self.sessions() as session:
            await import_yields(session, document)
            session.add(
                ShippingConsumableCompatibility(
                    firebird_model_id=99,
                    firebird_warehouse_item_id=11,
                    model_label="Ricoh IM C3000",
                    item_name="Toner testowy",
                    status="rejected",
                    evidence=[],
                    first_seen_at=now,
                    last_seen_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
            await refresh_catalog(
                session,
                {
                    "warehouse_id": 1,
                    "items": [
                        {
                            "item_id": 11,
                            "item_index": "TEST",
                            "name": "Toner testowy",
                            "brand": "Ricoh",
                            "stock": 1,
                        }
                    ],
                    "models": {99: {"id": 99, "brand": "Ricoh", "model": "IM C3000"}},
                    "active_keys": [model_key("Ricoh", "IM C3000")],
                },
            )
            detail = await yield_detail(session, 11)
            self.assertEqual(detail["scope"], "review")
            self.assertEqual(detail["models"], [])
            self.assertEqual(detail["pages"], 31000)

    async def test_import_invalid_duplicate_rolls_back_whole_batch(self):
        document = sample_document()
        document["items"].append(deepcopy(document["items"][0]))
        async with self.sessions() as session:
            with self.assertRaises(ValueError):
                await import_yields(session, document)
            await session.rollback()
            self.assertEqual(list(await session.scalars(select(TonerYield))), [])

    async def test_api_permissions_history_conflict_and_refresh_failure(self):
        now = datetime.now(UTC)
        user = AdminUser(
            id=20,
            email="operator@example.com",
            role="operator",
            can_edit_toner_yields=False,
            password_hash="test",
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        async with self.sessions() as session:
            session.add(user)
            session.add(
                AdminSetting(
                    key="user_sections.20", value='["shipping"]', is_secret=False, updated_at=now
                )
            )
            await import_yields(session, sample_document())
            await session.commit()

        app = FastAPI()
        app.include_router(admin_toner_yields.router)

        async def database():
            """Podstawia wyłącznie testową bazę SQLite."""
            async with self.sessions() as session:
                yield session

        async def context():
            """Udostępnia syntetycznego operatora bez tokenów produkcyjnych."""
            return SimpleNamespace(client_ip="127.0.0.1"), user

        app.dependency_overrides[get_db_session] = database
        app.dependency_overrides[get_admin_session_context] = context
        payload = {
            "revision": 1,
            "pages": 30000,
            "status": "estimated",
            "source": "Źródło testowe",
            "basis": "Niższy zgodny wariant",
            "reason": "Sprawdzenie zapisu",
        }
        with (
            patch.object(settings, "shipping_enabled", True),
            patch.object(settings, "shipping_warehouse_id", 1),
        ):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                base = "/admin/shipping/toner-yields"
                response = await client.get(base)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.json()["can_edit"])
                self.assertEqual((await client.patch(f"{base}/11", json=payload)).status_code, 403)
                with patch.object(admin_toner_yields, "load_toner_catalog") as source:
                    self.assertEqual((await client.post(f"{base}/refresh")).status_code, 403)
                    source.assert_not_called()
                user.can_edit_toner_yields = True
                response = await client.patch(f"{base}/11", json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual((await client.patch(f"{base}/11", json=payload)).status_code, 409)
                history = (await client.get(f"{base}/11/history")).json()["items"]
                self.assertEqual(len(history), 2)
                self.assertEqual(history[0]["actor"], user.email)
                with patch.object(
                    admin_toner_yields,
                    "load_toner_catalog",
                    side_effect=RuntimeError("awaria testowa"),
                ):
                    response = await client.post(f"{base}/refresh")
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn("awaria testowa", response.text)
                self.assertEqual((await client.get(f"{base}/11")).json()["pages"], 30000)
                self.assertEqual((await client.get(f"{base}/999")).status_code, 404)
                self.assertEqual((await client.get(base + "?color=red")).status_code, 422)
                user.role = "admin"
                self.assertEqual((await client.get(base)).status_code, 200)
                async with self.sessions() as session:
                    setting = await session.get(AdminSetting, "user_sections.20")
                    setting.value = '["admin"]'
                    await session.commit()
                self.assertEqual((await client.get(base)).status_code, 403)
                app.dependency_overrides.pop(get_admin_session_context)
                self.assertIn((await client.get(base)).status_code, (401, 403))


@pytest.mark.parametrize(
    "patch",
    [
        {"pages": 0},
        {"pages": -1},
        {"pages": 1.5},
        {"pages": True},
        {"pages": None},
        {"source": "  "},
        {"status": "missing"},
        {"status": "estimated", "basis": ""},
        {"reason": "  "},
        {"revision": -1},
    ],
)
def test_update_rejects_invalid_or_unexplained_values(patch):
    """Nie przyjmuje pozornych potwierdzeń ani nieprawidłowej liczby stron."""
    with pytest.raises(ValidationError):
        TonerYieldUpdate(
            **{
                "revision": 1,
                "pages": 12000,
                "status": "confirmed",
                "source": "Katalog",
                "reason": "Sprawdzenie",
                **patch,
            }
        )


def test_model_normalization_keeps_color_family_and_plus():
    """Nie utożsamia modeli monochromatycznych z kolorowymi."""
    assert model_key("Ricoh", "IM C3000") == model_key("Nashuatec", "IMC 3000")
    assert model_key("Ricoh", "MP C3000") != model_key("Ricoh", "MP 3000")
    assert model_key("Develop", "INEO+227") != model_key("Develop", "INEO 227")


def test_toner_detection_excludes_parts_and_unknown_color():
    """Odrzuca podzespoły i nie zgaduje koloru na podstawie modelu."""
    assert is_toner("Toner Ricoh IM C300 magenta")
    assert not is_toner("Pojemnik na zużyty toner")
    assert not is_toner("Chip toner Ricoh")
    assert toner_color("Toner Ricoh IM C300 magenta") == "magenta"
    assert toner_color("Toner Ricoh IM C300") == "unknown"


def test_lower_estimate_keeps_color_family_and_source():
    """Niższa pojemność jest szacunkiem, a inny kolor lub rodzina uniemożliwia kopiowanie."""
    first = {
        "color": "black",
        "family": "IMC300/400",
        "status": "confirmed",
        "pages": 17000,
        "source": "Katalog wariantu 300",
    }
    second = {**first, "pages": 17500, "source": "Katalog wariantu 400"}
    value = lower_compatible_estimate(color="black", family="IMC300/400", variants=[second, first])
    assert value["pages"] == 17000
    assert value["status"] == "estimated"
    assert value["source"] == first["source"]
    for replacement in (
        {"color": "cyan"},
        {"family": "MPC3000"},
        {"status": "estimated"},
        {"pages": None},
    ):
        with pytest.raises(ValueError):
            lower_compatible_estimate(
                color="black", family="IMC300/400", variants=[first, {**second, **replacement}]
            )
