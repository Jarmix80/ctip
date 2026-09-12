"""Jawne nadanie istniejącemu kontu telemetrii wyłącznie odczytów tabel ORBIT."""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv


def grant_statements(user, tables):
    """Buduje polecenia dla zweryfikowanego identyfikatora, bez haseł i praw zapisu."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_$]{0,30}", user) or user == "SYSDBA":
        raise ValueError("Wymagane osobne konto odczytowe telemetrii.")
    return [f'GRANT SELECT ON "{table}" TO USER "{user}"' for table in tables]


def main(argv=None):
    """Kontrola jest domyślna; --apply wymaga Windows i wcześniejszego backupu wdrożenia."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    if os.name != "nt" or not (root / ".env").is_file():
        raise RuntimeError("Operacja wymaga produkcyjnego repozytorium Windows.")
    os.chdir(root)
    sys.path.insert(0, str(root))
    load_dotenv(root / ".env", override=True)
    os.environ["CTIP_ENV_FILE"] = str(root / ".env")
    os.environ["CTIP_RUNTIME_PROFILE"] = "production"
    import firebirdsql

    from app.core.config import settings
    from app.services.orbit_source import PRIMARY_KEYS
    from app.services.telemetry.config import TelemetrySettings
    from app.services.telemetry.sources import firebird_connection

    config = TelemetrySettings()
    user = config.ms_user.upper()
    statements = grant_statements(user, PRIMARY_KEYS)
    if args.apply:
        connection = firebirdsql.connect(
            host=settings.fb_host,
            port=settings.fb_port,
            database=settings.fb_database,
            user=settings.fb_user,
            password=settings.fb_password,
            charset=settings.fb_charset,
            timeout=30,
        )
        try:
            cursor = connection.cursor()
            for table in PRIMARY_KEYS:
                cursor.execute(
                    "SELECT RDB$PRIVILEGE FROM RDB$USER_PRIVILEGES WHERE RDB$USER=? "
                    "AND RDB$RELATION_NAME=? AND RDB$PRIVILEGE IN ('I','U','D')",
                    (user, table),
                )
                if cursor.fetchone():
                    raise RuntimeError(
                        "Konto ma nadmiarowe uprawnienia zapisu; przerwano bez ich zmiany."
                    )
            for statement in statements:
                cursor.execute(statement)
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
    checked = []
    with firebird_connection(config=config) as connection:
        cursor = connection.cursor()
        for table, primary_key in PRIMARY_KEYS.items():
            cursor.execute(f'SELECT FIRST 1 "{primary_key}" FROM "{table}"')
            cursor.fetchone()
            checked.append(table)
        cursor.close()
    print(json.dumps({"status": "ok", "applied": args.apply, "tables": checked}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
