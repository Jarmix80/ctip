"""Kontrola DDL ORBIT w trybie PostgreSQL offline, bez wykonywania migracji bazy."""

import ast
import io
import re
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import Boolean
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models import telemetry
from app.models.admin import AdminUser
from app.models.orbit import OrbitDevice, OrbitEvent, OrbitRun

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = ROOT / "alembic" / "versions" / "c8f3e5a7b920_add_orbit.py"
SCHEMA_PATH = ROOT / "docs" / "baza" / "schema_ctip.sql"
TABLES = (telemetry.record_head, OrbitDevice.__table__, OrbitEvent.__table__, OrbitRun.__table__)


def normalize_sql(value):
    """Pomija wyłącznie formatowanie DDL, zachowując nazwy, typy i ograniczenia."""
    return re.sub(r"\s+", " ", str(value).strip().rstrip(";")).strip()


@pytest.fixture(scope="module")
def migration():
    """Wczytuje pojedynczą rewizję bez uruchamiania środowiska Alembic."""
    spec = spec_from_file_location("orbit_migration", MIGRATION_PATH)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def statements(migration):
    """Przekazuje upgrade do bufora SQL; nie tworzy silnika ani połączenia."""
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        migration.upgrade()
    return [
        normalize_sql(statement) for statement in output.getvalue().split(";") if statement.strip()
    ]


def test_revision_follows_toner_yields(migration):
    """Nowa rewizja rozszerza wskazany łańcuch bez tworzenia osobnej gałęzi."""
    scripts = ScriptDirectory(str(ROOT / "alembic"))
    revision = scripts.get_revision("c8f3e5a7b920")
    assert migration.revision == revision.revision == "c8f3e5a7b920"
    assert migration.down_revision == revision.down_revision == "b7e2d4f6a810"
    assert scripts.get_revision(revision.down_revision).revision == "b7e2d4f6a810"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_migration_ddl_is_literal_and_independent_of_models(migration, statements):
    """Przyszłe zmiany modeli nie mogą zmienić SQL historycznej rewizji."""
    tree = ast.parse(MIGRATION_PATH.read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert len(imports) == 1
    assert isinstance(imports[0], ast.ImportFrom)
    assert imports[0].module == "alembic"
    literal = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "STATEMENTS" for target in node.targets
        )
    )
    assert ast.literal_eval(literal) == migration.STATEMENTS
    assert statements == [normalize_sql(statement) for statement in migration.STATEMENTS]


def test_offline_tables_and_indexes_match_models(statements):
    """Porównuje całe DDL, w tym PK, FK, nullowalność, JSON, UTC i status urządzenia."""
    dialect = postgresql.dialect()
    expected = {normalize_sql(CreateTable(table).compile(dialect=dialect)) for table in TABLES}
    indexes = [index for table in TABLES for index in table.indexes]
    indexes.append(
        next(
            index
            for index in telemetry.record.indexes
            if index.name == "idx_telemetry_record_serial_time"
        )
    )
    expected.update(normalize_sql(CreateIndex(index).compile(dialect=dialect)) for index in indexes)
    actual = [statement for statement in statements if statement.startswith("CREATE ")]
    assert len(actual) == len(expected) == 9
    assert set(actual) == expected
    assert (
        "CREATE INDEX idx_telemetry_record_serial_time ON ctip.telemetry_record (serial, observed_at)"
        in actual
    )
    device = next(
        statement
        for statement in actual
        if statement.startswith("CREATE TABLE ctip.shipping_orbit_device ")
    )
    assert (
        "CONSTRAINT orbit_device_status CHECK (status IN ('active','suspended','scrapped','review'))"
        in device
    )


def test_finance_permission_is_disabled_for_existing_accounts(statements):
    """Jedyna zmiana istniejącej tabeli dodaje wymagane uprawnienie z DEFAULT false."""
    alterations = [statement for statement in statements if statement.startswith("ALTER ")]
    assert alterations == [
        "ALTER TABLE ctip.admin_user ADD COLUMN can_view_orbit_finance BOOLEAN NOT NULL DEFAULT false"
    ]
    permission = AdminUser.__table__.c.can_view_orbit_finance
    assert isinstance(permission.type, Boolean)
    assert not permission.nullable
    assert permission.default.arg is False
    assert str(permission.server_default.arg) == "false"


def test_upgrade_does_not_backfill_or_rewrite_source_history(statements):
    """Pusty head pozostawia rozstrzygnięcie wersji obserwacji źródła i fallbackowi ORBIT."""
    assert len(statements) == 10
    assert all(
        statement.startswith(("CREATE TABLE ", "CREATE INDEX ", "ALTER TABLE "))
        for statement in statements
    )
    assert sum(statement.startswith("CREATE TABLE ") for statement in statements) == 4


def test_schema_document_matches_migration_and_dependency_order(statements):
    """SQL dokumentacyjny zawiera każde DDL raz i tworzy rodziców przed nowymi FK."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    normalized_schema = normalize_sql(schema) + ";"
    table_positions = {
        match.group(1): match.start()
        for match in re.finditer(r"CREATE TABLE\s+(ctip\.\w+)\s*\(", schema)
    }
    available = {"ctip.telemetry_source", "ctip.telemetry_record", "ctip.admin_user"}
    for statement in statements:
        assert normalized_schema.count(statement + ";") == 1
        created = re.match(r"CREATE TABLE (ctip\.\w+) ", statement)
        if created:
            table_name = created.group(1)
            references = re.findall(r"REFERENCES (ctip\.\w+) ", statement)
            assert set(references) <= available
            assert all(
                table_positions[parent] < table_positions[table_name] for parent in references
            )
            available.add(table_name)
        else:
            target = re.search(r"(?:ON|ALTER TABLE) (ctip\.\w+) ", statement).group(1)
            assert target in available
            assert table_positions[target] < schema.index(statement)


def test_downgrade_is_blocked_before_any_ddl(migration):
    """Rollback kodu nie emituje nawet częściowego SQL usuwającego dane lub uprawnienia."""
    output = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context), pytest.raises(RuntimeError, match="zatwierdzonej procedury"):
        migration.downgrade()
    assert output.getvalue() == ""
