"""Testy reguł tożsamości aktywnych i wycofanych urządzeń."""

import asyncio

from sqlalchemy.dialects import postgresql

from app.models import DeviceInventoryUnit
from app.services.device_registry import find_unit_by_source_or_identity


def test_device_identity_indexes_are_unique_only_for_active_units() -> None:
    indexes = {index.name: index for index in DeviceInventoryUnit.__table__.indexes}

    for index_name in (
        "uq_device_inventory_unit_serial_normalized",
        "uq_device_inventory_unit_ewidencja_normalized",
    ):
        index = indexes[index_name]
        assert index.unique is True
        assert str(index.dialect_options["postgresql"]["where"]) == "status = 'active'"

    constraint_names = {constraint.name for constraint in DeviceInventoryUnit.__table__.constraints}
    assert "uq_device_inventory_unit_serial_normalized" not in constraint_names
    assert "uq_device_inventory_unit_ewidencja_normalized" not in constraint_names


def test_identity_lookup_prefers_active_and_latest_unit() -> None:
    expected_unit = object()

    class ScalarResult:
        def first(self):
            return expected_unit

    class QueryResult:
        def scalars(self):
            return ScalarResult()

    class QuerySession:
        statement = None

        async def execute(self, statement):
            self.statement = statement
            return QueryResult()

    session = QuerySession()
    result = asyncio.run(
        find_unit_by_source_or_identity(
            session,
            serial="3371P600941",
            ewidencja="KP/5213",
        )
    )
    compiled = str(
        session.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert result is expected_unit
    assert "CASE WHEN (ctip.device_inventory_unit.status = 'active') THEN 0 ELSE 1 END" in compiled
    assert "ctip.device_inventory_unit.id DESC" in compiled
