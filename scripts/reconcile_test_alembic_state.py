"""Bezpiecznie uzgadnia znacznik Alembic istniejącej bazy `ctip_test`.

Narzędzie nie wykonuje migracji ani zmian tabel biznesowych. Tryb `--apply`
jest przeznaczony wyłącznie dla bazy testowej, której schemat został wcześniej
utworzony poza aktualnym grafem Alembic. Zapis jest dozwolony dopiero po pełnej
walidacji modeli, krytycznych ograniczeń i kopii zapasowej.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.core.config import settings
from app.models import Base

EXPECTED_CURRENT = "e4a8c1d9f2b7"
EXPECTED_TARGET = "f7b2d4e6a810"
ALLOWED_TEST_HOSTS = {
    "127.0.0.1",
    "localhost",
    "::1",
    "postgres",
    "ctip-prod-mirror-postgres-1",
}
SHA256_LINE = re.compile(r"^([0-9a-f]{64})\s+\*?(.+)$", re.IGNORECASE)


def expected_model_columns() -> dict[str, set[str]]:
    """Zwraca wymagane tabele i kolumny wynikające z bieżących modeli ORM."""

    return {
        table.name: {column.name for column in table.columns}
        for table in Base.metadata.tables.values()
        if table.schema == "ctip"
    }


def schema_errors(
    expected: dict[str, set[str]],
    actual: dict[str, set[str]],
) -> list[str]:
    """Wskazuje brakujące tabele i kolumny bez odrzucania rozszerzeń bazy."""

    errors: list[str] = []
    for table_name, expected_columns in sorted(expected.items()):
        if table_name not in actual:
            errors.append(f"brak tabeli ctip.{table_name}")
            continue
        missing_columns = sorted(expected_columns - actual[table_name])
        if missing_columns:
            errors.append(f"ctip.{table_name}: brak kolumn {', '.join(missing_columns)}")
    return errors


def validate_test_runtime() -> None:
    """Blokuje wykonanie poza izolowanym profilem i bazą testową."""

    problems: list[str] = []
    if settings.ctip_runtime_profile != "test":
        problems.append("CTIP_RUNTIME_PROFILE musi mieć wartość test")
    if settings.pg_database.strip().lower() != "ctip_test":
        problems.append("PGDATABASE musi mieć wartość ctip_test")
    if settings.pg_host.strip().lower() not in ALLOWED_TEST_HOSTS:
        problems.append("PGHOST nie wskazuje dozwolonego lokalnego PostgreSQL")
    if settings.fb_allow_writes:
        problems.append("FB_ALLOW_WRITES musi pozostać wyłączone")
    if not settings.sms_test_mode:
        problems.append("SMS_TEST_MODE musi pozostać włączone")
    if problems:
        raise RuntimeError("; ".join(problems))


def repository_head() -> str:
    """Zwraca jedyną głowę grafu migracji repozytorium."""

    script = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = script.get_heads()
    if heads != [EXPECTED_TARGET]:
        raise RuntimeError(
            "Graf Alembic nie ma oczekiwanej pojedynczej głowy: "
            f"otrzymano {heads}, oczekiwano {[EXPECTED_TARGET]}."
        )
    return heads[0]


def verify_backup_manifest(manifest_path: Path) -> None:
    """Sprawdza sumy PostgreSQL i Firebird zapisane obok backupu testowego."""

    if not manifest_path.is_file():
        raise RuntimeError(f"Brak manifestu backupu: {manifest_path}.")
    entries: dict[str, str] = {}
    for raw_line in manifest_path.read_text(encoding="utf-8").splitlines():
        match = SHA256_LINE.match(raw_line.strip())
        if match:
            entries[Path(match.group(2)).name] = match.group(1).lower()
    required = {"ctip_test.dump", "BAZAMS_TEST.FDB"}
    if set(entries) != required:
        raise RuntimeError("Manifest backupu musi zawierać dokładnie PostgreSQL i Firebird.")
    for filename, expected_hash in sorted(entries.items()):
        file_path = manifest_path.parent / filename
        if not file_path.is_file():
            raise RuntimeError(f"Brak pliku backupu: {file_path}.")
        digest = hashlib.sha256()
        with file_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_hash:
            raise RuntimeError(f"Niezgodna suma SHA-256 pliku {filename}.")


async def current_revisions(connection: AsyncConnection) -> set[str]:
    """Odczytuje znaczniki migracji zapisane w bazie."""

    rows = await connection.execute(text("SELECT version_num FROM alembic_version"))
    return {str(value).lower() for value in rows.scalars()}


async def database_columns(connection: AsyncConnection) -> dict[str, set[str]]:
    """Odczytuje rzeczywiste tabele i kolumny schematu `ctip`."""

    rows = await connection.execute(
        text(
            """
            SELECT table_name, column_name
              FROM information_schema.columns
             WHERE table_schema = 'ctip'
             ORDER BY table_name, ordinal_position
            """
        )
    )
    result: dict[str, set[str]] = {}
    for table_name, column_name in rows:
        result.setdefault(str(table_name), set()).add(str(column_name))
    return result


async def constraint_definitions(connection: AsyncConnection) -> dict[str, str]:
    """Odczytuje definicje ograniczeń wymaganych przez uzgadniany graf."""

    rows = await connection.execute(
        text(
            """
            SELECT constraint_name, pg_get_constraintdef(pg_constraint.oid)
              FROM information_schema.table_constraints
              JOIN pg_namespace
                ON pg_namespace.nspname = table_constraints.constraint_schema
              JOIN pg_constraint
                ON pg_constraint.conname = table_constraints.constraint_name
               AND pg_constraint.connamespace = pg_namespace.oid
             WHERE table_constraints.constraint_schema = 'ctip'
               AND constraint_name IN (
                   'ck_crm_case_queue',
                   'uq_bot_identity_device_ref'
               )
            """
        )
    )
    return {str(name): " ".join(str(definition).lower().split()) for name, definition in rows}


async def critical_data_errors(connection: AsyncConnection) -> list[str]:
    """Sprawdza dane, które powinny zostać przekształcone przez brakującą gałąź."""

    checks = {
        "crm_case zawiera pustą kategorię": """
            SELECT count(*) FROM ctip.crm_case WHERE category IS NULL
        """,
        "crm_case zawiera historyczną kolejkę": """
            SELECT count(*) FROM ctip.crm_case
             WHERE queue NOT IN ('sales', 'service_it', 'contracts', 'other')
        """,
        "bot_identity_device zawiera pusty device_ref": """
            SELECT count(*) FROM ctip.bot_identity_device WHERE device_ref IS NULL
        """,
        "bot_identity_device zawiera powielony device_ref": """
            SELECT count(*) FROM (
                SELECT device_ref FROM ctip.bot_identity_device
                 GROUP BY device_ref HAVING count(*) > 1
            ) AS duplicates
        """,
    }
    errors: list[str] = []
    for message, query in checks.items():
        count = int((await connection.execute(text(query))).scalar_one())
        if count:
            errors.append(f"{message}: {count}")
    return errors


async def validate_database(connection: AsyncConnection) -> list[str]:
    """Wykonuje pełną walidację zgodności przed zmianą znacznika."""

    errors = schema_errors(expected_model_columns(), await database_columns(connection))
    constraints = await constraint_definitions(connection)
    queue_constraint = constraints.get("ck_crm_case_queue", "")
    for allowed in ("sales", "service_it", "contracts", "other"):
        if f"'{allowed}'" not in queue_constraint:
            errors.append(f"ograniczenie ck_crm_case_queue nie dopuszcza {allowed}")
    for forbidden in ("accounting", "meters"):
        if f"'{forbidden}'" in queue_constraint:
            errors.append(f"ograniczenie ck_crm_case_queue nadal dopuszcza {forbidden}")
    if "unique (device_ref)" not in constraints.get("uq_bot_identity_device_ref", ""):
        errors.append("brak unikalności uq_bot_identity_device_ref")
    errors.extend(await critical_data_errors(connection))
    return errors


async def reconcile(*, apply: bool, backup_manifest: Path | None) -> None:
    """Waliduje bazę i opcjonalnie uzgadnia wyłącznie znacznik Alembic."""

    validate_test_runtime()
    target = repository_head()
    if apply:
        if backup_manifest is None:
            raise RuntimeError("Tryb --apply wymaga --backup-manifest.")
        verify_backup_manifest(backup_manifest)

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            revisions = await current_revisions(connection)
            if revisions not in ({EXPECTED_CURRENT}, {target}):
                raise RuntimeError(
                    "Nieobsługiwany stan alembic_version: "
                    f"{sorted(revisions)}; oczekiwano {EXPECTED_CURRENT} albo {target}."
                )
            errors = await validate_database(connection)
            if errors:
                joined = "\n- ".join(errors)
                raise RuntimeError(f"Schemat nie przeszedł walidacji:\n- {joined}")
            if apply and revisions == {EXPECTED_CURRENT}:
                result = await connection.execute(
                    text(
                        "UPDATE alembic_version SET version_num = :target "
                        "WHERE version_num = :current"
                    ),
                    {"target": target, "current": EXPECTED_CURRENT},
                )
                if result.rowcount != 1:
                    raise RuntimeError("Nie udało się jednoznacznie zmienić znacznika Alembic.")
                print(f"[OK] Uzgodniono alembic_version: {EXPECTED_CURRENT} -> {target}.")
            elif revisions == {target}:
                print(f"[OK] Baza była już uzgodniona z rewizją {target}.")
            else:
                print(
                    f"[OK] Schemat odpowiada rewizji {target}; "
                    "tryb kontrolny nie zmienił alembic_version."
                )
    finally:
        await engine.dispose()


def parse_args() -> argparse.Namespace:
    """Buduje interfejs polecenia z domyślnym trybem tylko do odczytu."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="po walidacji zmień wyłącznie znacznik alembic_version",
    )
    parser.add_argument(
        "--backup-manifest",
        type=Path,
        help="ścieżka do SHA256SUMS zweryfikowanego backupu testowego",
    )
    return parser.parse_args()


def main() -> int:
    """Uruchamia kontrolę i zwraca niezerowy kod przy każdym odstępstwie."""

    arguments = parse_args()
    try:
        asyncio.run(
            reconcile(
                apply=arguments.apply,
                backup_manifest=arguments.backup_manifest,
            )
        )
    except RuntimeError as exc:
        print(f"[BŁĄD] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
