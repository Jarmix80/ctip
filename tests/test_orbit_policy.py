"""Kontrola dziedziczenia polityk i niezależności nowej migracji od modeli."""

import importlib.util
import re
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.models import AdminUser, Base, OrbitEvidence, OrbitPolicy
from app.services.orbit_policy import resolve, save


def test_policy_precedence_and_restoring_inheritance():
    """Urządzenie nadpisuje klienta, kolor nadpisuje regułę danego zakresu."""
    engine = create_engine("sqlite://", execution_options={"schema_translate_map": {"ctip": None}})
    with engine.begin() as connection:
        Base.metadata.create_all(connection, tables=[AdminUser.__table__, OrbitPolicy.__table__])
        save(connection, "global", "0", "all", {"spare_toners": 1}, None)
        save(connection, "customer", "9", "black", {"spare_toners": 2}, None)
        save(connection, "device", "ms:7", "all", {"spare_toners": 0}, None)
        save(connection, "device", "ms:7", "black", {"low_percent": 15}, None)
        device = {"id": "ms:7", "data": {"customer_id": 9}}
        result = resolve(connection, device)["effective"]
        assert result["black"]["spare_toners"] == 0
        assert result["black"]["low_percent"] == 15
        assert result["cyan"]["spare_toners"] == 0
        save(connection, "device", "ms:7", "all", {}, None)
        result = resolve(connection, device)["effective"]
        assert result["black"]["spare_toners"] == 2
        assert result["cyan"]["spare_toners"] == 1
    engine.dispose()


def test_extension_migration_matches_models_and_schema():
    """Nowa rewizja zachowuje literalne DDL, ograniczenia i dokumentację."""
    root = Path(__file__).resolve().parents[1]
    path = root / "alembic/versions/d9a4f6b8c031_orbit_evidence_policy.py"
    spec = importlib.util.spec_from_file_location("orbit_extension", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def normalized(value):
        """Pomija formatowanie bez zmieniania treści ograniczeń DDL."""
        return re.sub(r"\s+", " ", str(value).strip().rstrip(";"))

    expected = {
        normalized(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
        for model in (OrbitEvidence, OrbitPolicy)
    }
    assert {normalized(value) for value in migration.STATEMENTS} == expected
    schema = normalized((root / "docs/baza/schema_ctip.sql").read_text())
    assert all(value in schema for value in expected)
