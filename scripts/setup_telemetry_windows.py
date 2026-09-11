"""Przygotowanie dedykowanych kont odczytowych i prywatnej konfiguracji telemetrii."""

import argparse
import json
import logging
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import firebirdsql
import psycopg
from dotenv import dotenv_values
from firebirdsql import services
from firebirdsql.consts import isc_action_svc_display_user, isc_info_svc_get_users
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[1]
READER = "ctip_telemetry_ro"
VM_CHARSET = "UTF8"
VM_TABLES = ("MASZYNY", "MASZYNY_STATS", "WEZWANIE", "MAGAZYNY", "DODAJ", "CPC")
PR_TABLES = ("device_fingerprints", "raw_counter_samples", "service_snapshots", "material_readings")
MAIL_KEYS = (
    "REMOTE_EMAIL_ADDRESS",
    "REMOTE_EMAIL_PASSWORD",
    "REMOTE_IMAP_HOST",
    "REMOTE_IMAP_PORT",
)


def firebird_reader_exists(base: dict) -> bool:
    """Sprawdza konto przez usługę zgodną z Firebird 2.5, bez nieistniejącej SEC$USERS."""
    connection = services.connect(
        host=base["FB_HOST"],
        port=int(base.get("FB_PORT", "3050")),
        user=base["FB_USER"],
        password=base["FB_PASSWORD"],
        timeout=20,
    )
    try:
        username = READER.upper().encode("ascii")
        username_tag = 7
        connection._op_service_start(
            bytes([isc_action_svc_display_user, username_tag])
            + len(username).to_bytes(2, "little")
            + username
        )
        connection._op_response()
        connection._op_service_info(b"", bytes([isc_info_svc_get_users]))
        _, _, buffer = connection._op_response()
        if len(buffer) < 4 or buffer[0] != isc_info_svc_get_users:
            raise RuntimeError("Nieprawidłowa odpowiedź katalogu użytkowników Firebird.")
        length = int.from_bytes(buffer[1:3], "little")
        if length == 0:
            return False
        payload = buffer[3 : 3 + length]
        if len(payload) < 3 or payload[0] != username_tag:
            raise RuntimeError("Nieprawidłowy rekord użytkownika Firebird.")
        size = int.from_bytes(payload[1:3], "little")
        return payload[3 : 3 + size] == username
    finally:
        connection.close()


def write_private_env(path: Path, values: dict):
    """Zapisuje atomowo plik, nadając prywatne ACL jeszcze przed zapisaniem sekretów."""
    temporary = path.with_name(path.name + ".new")
    temporary.touch(exist_ok=True)
    identity = subprocess.run(["whoami", "/user"], capture_output=True, text=True, check=True)
    matched = re.search(r"S-1-5-[\d-]+", identity.stdout)
    if not matched:
        raise RuntimeError("Nie udało się ustalić SID konta wdrożeniowego.")
    subprocess.run(
        [
            "icacls",
            str(temporary),
            "/inheritance:r",
            "/grant:r",
            "*S-1-5-18:F",
            "*S-1-5-32-544:F",
            f"*{matched[0]}:F",
        ],
        capture_output=True,
        check=True,
    )
    content = (
        "\n".join(
            f"{key}={json.dumps(str(value), ensure_ascii=False)}"
            for key, value in sorted(values.items())
        )
        + "\n"
    )
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def provision_postgres(base: dict, printradar: dict, password: str, existing_password: bool):
    """Tworzy login bez uprawnień administracyjnych i nadaje SELECT przez właściciela źródła."""
    with psycopg.connect(
        host=base["PGHOST"],
        port=base["PGPORT"],
        dbname=base["PGDATABASE"],
        user=base["PGUSER"],
        password=base["PGPASSWORD"],
        connect_timeout=10,
    ) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (READER,))
        if not cursor.fetchone():
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                ).format(sql.Identifier(READER), sql.Literal(password))
            )
        elif not existing_password:
            raise RuntimeError(
                "Istniejące konto PostgreSQL wymaga istniejącej prywatnej konfiguracji."
            )
        cursor.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only=on").format(
                sql.Identifier(READER)
            )
        )
    with psycopg.connect(printradar["RICOH_COUNTER_CENTRAL_DB"], connect_timeout=10) as connection:
        cursor = connection.cursor()
        cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(READER)))
        for table in PR_TABLES:
            cursor.execute(
                sql.SQL("GRANT SELECT ON TABLE {} TO {}").format(
                    sql.Identifier(table), sql.Identifier(READER)
                )
            )
            cursor.execute(
                "SELECT has_table_privilege(%s,%s,'INSERT,UPDATE,DELETE,TRUNCATE')", (READER, table)
            )
            if cursor.fetchone()[0]:
                raise RuntimeError("Konto odczytowe ma nadmiarowe uprawnienia do PrintRadar.")
    values = conninfo_to_dict(printradar["RICOH_COUNTER_CENTRAL_DB"])
    values.update(user=READER, password=password)
    return make_conninfo(**values)


def provision_firebird(
    base: dict, password: str, existing_password: bool, vm_database: str, *, ms_cpc=False
):
    """Nadaje SELECT do jawnej listy tabel, używając osobnego kodowania V-Maintenance."""
    ms_tables = ("MASZYNA", "CPC", "UMOWACPC") if ms_cpc else ("MASZYNA",)
    for database, tables in ((base["FB_DATABASE"], ms_tables), (vm_database, VM_TABLES)):
        charset = VM_CHARSET if database == vm_database else base.get("FB_CHARSET", "WIN1250")
        connection = firebirdsql.connect(
            host=base["FB_HOST"],
            port=int(base.get("FB_PORT", "3050")),
            database=database,
            user=base["FB_USER"],
            password=base["FB_PASSWORD"],
            charset=charset,
            timeout=20,
        )
        try:
            cursor = connection.cursor()
            if not firebird_reader_exists(base):
                cursor.execute(f"CREATE USER {READER} PASSWORD '{password}'")
                connection.commit()
            elif not existing_password:
                raise RuntimeError(
                    "Istniejące konto Firebird wymaga istniejącej prywatnej konfiguracji."
                )
            for table in tables:
                cursor.execute(f"GRANT SELECT ON {table} TO USER {READER}")
            connection.commit()
            existing_password = True
        finally:
            connection.close()
        verification = firebirdsql.connect(
            host=base["FB_HOST"],
            port=int(base.get("FB_PORT", "3050")),
            database=database,
            user=READER,
            password=password,
            charset=charset,
            timeout=20,
            isolation_level=firebirdsql.ISOLATION_LEVEL_READ_COMMITED_RO,
        )
        try:
            cursor = verification.cursor()
            for table in tables:
                cursor.execute(f"SELECT FIRST 1 * FROM {table}")
                cursor.fetchone()
            cursor.execute(
                "SELECT COUNT(*) FROM RDB$USER_PRIVILEGES WHERE TRIM(RDB$USER) IN (?, 'PUBLIC') AND RDB$PRIVILEGE IN ('I','U','D')",
                (READER.upper(),),
            )
            if cursor.fetchone()[0]:
                raise RuntimeError("Konto Firebird ma nadmiarowe uprawnienia do zapisu.")
        finally:
            verification.rollback()
            verification.close()


def main(argv=None):
    """Domyślnie pokazuje plan; zapis wymaga --apply i uruchomienia na serwerze Windows."""
    parser = argparse.ArgumentParser(description="Konfiguracja produkcyjnej telemetrii CTIP")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--ms-cpc",
        action="store_true",
        help="Dodanie wyłącznie odczytu historii rozliczeniowej MS CPC",
    )
    parser.add_argument(
        "--mail-stdin",
        action="store_true",
        help="Pobranie wyłącznie czterech ustawień IMAP ze standardowego wejścia",
    )
    args = parser.parse_args(argv)
    if os.name != "nt":
        raise RuntimeError("Skrypt jest przeznaczony wyłącznie dla produkcyjnego Windows.")
    logging.getLogger("dotenv.main").setLevel(logging.ERROR)
    base = dotenv_values(ROOT / ".env", interpolate=False)
    private_path = ROOT / ".env.telemetry"
    existing = dotenv_values(private_path, interpolate=False)
    printradar = dotenv_values(Path(r"D:\counter_kp\config\central.env"), interpolate=False)
    mailbox = {key: existing.get(key) or base.get(key) for key in MAIL_KEYS}
    if args.mail_stdin:
        provided = json.load(sys.stdin)
        mailbox.update({key: provided[key] for key in MAIL_KEYS if key in provided})
    if not all(mailbox.values()):
        raise RuntimeError("Brak kompletnych ustawień skrzynki Remote.")
    if not args.apply:
        print(
            json.dumps(
                {
                    "status": "ready",
                    "reader": READER,
                    "sources": ["csv", "imap", "vmaintenance", "printradar", "ms"],
                }
            )
        )
        return 0
    root = Path(r"C:\Users\Administrator.KSEROPARTNER\Desktop\remote ricoh")
    vm_database = r"D:\bazavmantenance\BAZA_CPC.FDB"
    fb_password = existing.get("TELEMETRY_VM_PASSWORD") or secrets.token_urlsafe(18)
    pg_password = existing.get("TELEMETRY_PRINTRADAR_PASSWORD") or secrets.token_urlsafe(30)
    values = {
        **existing,
        **mailbox,
        "TELEMETRY_ENABLED": "false",
        "TELEMETRY_REPORT_ROOT": str(root / "tonery"),
        "TELEMETRY_DPLAC_ROOT": str(root),
        "TELEMETRY_VM_ENABLED": "true",
        "TELEMETRY_MS_ENABLED": "true",
        "TELEMETRY_MS_CPC_ENABLED": (
            "true" if args.ms_cpc or existing.get("TELEMETRY_MS_CPC_ENABLED") == "true" else "false"
        ),
        "TELEMETRY_MAIL_ENABLED": "true",
        "TELEMETRY_VM_HOST": base["FB_HOST"],
        "TELEMETRY_VM_DATABASE": vm_database,
        "TELEMETRY_VM_CHARSET": VM_CHARSET,
        "TELEMETRY_VM_USER": READER,
        "TELEMETRY_VM_PASSWORD": fb_password,
        "TELEMETRY_MS_USER": READER,
        "TELEMETRY_MS_PASSWORD": fb_password,
        "TELEMETRY_PRINTRADAR_PASSWORD": pg_password,
    }
    write_private_env(private_path, values)
    values["TELEMETRY_PRINTRADAR_DSN"] = provision_postgres(
        base, printradar, pg_password, bool(existing.get("TELEMETRY_PRINTRADAR_PASSWORD"))
    )
    with psycopg.connect(values["TELEMETRY_PRINTRADAR_DSN"], connect_timeout=10) as verification:
        verification.execute("SELECT sample_id FROM raw_counter_samples LIMIT 1").fetchone()
        verification.rollback()
    print(json.dumps({"step": "postgres_readonly_verified"}))
    provision_firebird(
        base,
        fb_password,
        bool(existing.get("TELEMETRY_VM_PASSWORD")),
        vm_database,
        ms_cpc=values["TELEMETRY_MS_CPC_ENABLED"] == "true",
    )
    print(json.dumps({"step": "firebird_readonly_verified"}))
    for name in ("Toner", "All Supplies", "Reporting"):
        directory = root / "tonery" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "archiwum").mkdir(exist_ok=True)
    values["TELEMETRY_ENABLED"] = "true"
    write_private_env(private_path, values)
    print(json.dumps({"status": "configured", "readonly_user": READER, "dplac_archive": False}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"status": "error", "code": type(error).__name__}))
        raise SystemExit(1) from None
