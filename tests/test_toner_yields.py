"""Izolowane testy katalogu wydajności; bez dostępu do produkcji i dostawców."""

import unittest
from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

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

    async def test_import_invalid_duplicate_rolls_back_whole_batch(self):
        document = sample_document()
        document["items"].append(deepcopy(document["items"][0]))
        async with self.sessions() as session:
            with self.assertRaises(ValueError):
                await import_yields(session, document)
            await session.rollback()
            self.assertEqual(list(await session.scalars(select(TonerYield))), [])


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
