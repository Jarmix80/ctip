"""Importuje formularze workflow z produkcyjnego PostgreSQL do lokalnego ctip_test.

Skrypt działa w trybie:
- źródło (`source`) tylko do odczytu,
- cel (`target`) z upsertami do lokalnej bazy testowej.

Zakres importu:
- `ctip.form_request`
- `ctip.form_workflow_case`
- `ctip.form_workflow_device`

Założenia bezpieczeństwa:
- połączenie źródłowe jest natychmiast przełączane w `default_transaction_read_only = on`,
- skrypt nie wykonuje żadnych operacji `INSERT/UPDATE/DELETE` na bazie źródłowej,
- identyfikatory użytkowników (`created_by`, `updated_by`) są zachowywane tylko wtedy,
  gdy odpowiadający rekord `ctip.admin_user` istnieje już w bazie testowej.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json


@dataclass(slots=True)
class DbConfig:
    """Parametry połączenia PostgreSQL."""

    host: str
    port: int
    database: str
    user: str
    password: str
    sslmode: str


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None:
        raise SystemExit(f"Brak wymaganej zmiennej środowiskowej: {name}")
    return value


def _build_source_config(args: argparse.Namespace) -> DbConfig:
    return DbConfig(
        host=args.source_host or _env("PROD_PGHOST", _env("PGHOST")),
        port=int(args.source_port or _env("PROD_PGPORT", _env("PGPORT", "5432"))),
        database=args.source_db or _env("PROD_PGDATABASE", _env("PGDATABASE")),
        user=args.source_user or _env("PROD_PGUSER", _env("PGUSER")),
        password=args.source_password or _env("PROD_PGPASSWORD", _env("PGPASSWORD")),
        sslmode=args.source_sslmode or _env("PROD_PGSSLMODE", _env("PGSSLMODE", "disable")),
    )


def _build_target_config(args: argparse.Namespace) -> DbConfig:
    return DbConfig(
        host=args.target_host or _env("PGHOST"),
        port=int(args.target_port or _env("PGPORT", "5432")),
        database=args.target_db or _env("PGDATABASE"),
        user=args.target_user or _env("PGUSER"),
        password=args.target_password or _env("PGPASSWORD"),
        sslmode=args.target_sslmode or _env("PGSSLMODE", "disable"),
    )


def _connect(config: DbConfig) -> psycopg.Connection[Any]:
    return psycopg.connect(
        host=config.host,
        port=config.port,
        dbname=config.database,
        user=config.user,
        password=config.password,
        sslmode=config.sslmode,
        row_factory=dict_row,
    )


def _normalize_statuses(items: Sequence[str] | None) -> list[str]:
    values = [str(item).strip().upper() for item in (items or []) if str(item).strip()]
    if not values:
        return ["SUBMITTED"]
    return list(dict.fromkeys(values))


def _coerce_fk(value: Any, *, allowed_ids: set[int]) -> int | None:
    if value in (None, ""):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    if normalized not in allowed_ids:
        return None
    return normalized


def _set_sequence(conn: psycopg.Connection[Any], sequence_name: str, table_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT setval(%s, COALESCE((SELECT MAX(id) FROM {table_name}), 1), true)",
            (sequence_name,),
        )


def _json_param(value: Any) -> Any:
    """Zwraca parametr bezpieczny dla kolumn JSON."""

    if value is None:
        return None
    return Json(value)


def _fetch_forms(
    conn: psycopg.Connection[Any], *, statuses: Sequence[str], limit: int
) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SET default_transaction_read_only = on")
        cur.execute(
            """
            SELECT
                id,
                created_at,
                updated_at,
                created_by,
                customer_name,
                customer_email,
                customer_phone,
                status,
                token_hash,
                token_expires_at,
                token_used_at,
                sms_status,
                email_status,
                ms_status,
                notification_error,
                submitted_payload,
                submitted_at
            FROM ctip.form_request
            WHERE status = ANY(%s)
            ORDER BY COALESCE(submitted_at, created_at) DESC, id DESC
            LIMIT %s
            """,
            (list(statuses), int(limit)),
        )
        return list(cur.fetchall())


def _fetch_workflow_cases(
    conn: psycopg.Connection[Any], *, form_ids: Sequence[int]
) -> list[dict[str, Any]]:
    if not form_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                created_at,
                updated_at,
                form_request_id,
                created_by,
                updated_by,
                stage,
                business_status,
                client_mode,
                firebird_client_id,
                firebird_client_status,
                client_payload_snapshot,
                proforma_firebird_id,
                proforma_number,
                proforma_pdf_path,
                delivery_date,
                delivery_time_window,
                delivery_contact_name,
                delivery_contact_phone,
                delivery_notes
            FROM ctip.form_workflow_case
            WHERE form_request_id = ANY(%s)
            ORDER BY id
            """,
            (list(form_ids),),
        )
        return list(cur.fetchall())


def _fetch_workflow_devices(
    conn: psycopg.Connection[Any], *, workflow_case_ids: Sequence[int]
) -> list[dict[str, Any]]:
    if not workflow_case_ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                created_at,
                workflow_case_id,
                source_type,
                source_row,
                producer,
                model,
                serial,
                ewidencja,
                device_status,
                reservation_status,
                price,
                price_net,
                price_gross,
                firebird_machine_id,
                firebird_client_id,
                snapshot
            FROM ctip.form_workflow_device
            WHERE workflow_case_id = ANY(%s)
            ORDER BY id
            """,
            (list(workflow_case_ids),),
        )
        return list(cur.fetchall())


def _fetch_target_admin_user_ids(conn: psycopg.Connection[Any]) -> set[int]:
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM ctip.admin_user")
        return {int(row["id"]) for row in cur.fetchall()}


def _upsert_forms(
    conn: psycopg.Connection[Any], *, rows: Iterable[dict[str, Any]], allowed_admin_ids: set[int]
) -> int:
    inserted = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                INSERT INTO ctip.form_request (
                    id,
                    created_at,
                    updated_at,
                    created_by,
                    customer_name,
                    customer_email,
                    customer_phone,
                    status,
                    token_hash,
                    token_expires_at,
                    token_used_at,
                    sms_status,
                    email_status,
                    ms_status,
                    notification_error,
                    submitted_payload,
                    submitted_at
                )
                VALUES (
                    %(id)s,
                    %(created_at)s,
                    %(updated_at)s,
                    %(created_by)s,
                    %(customer_name)s,
                    %(customer_email)s,
                    %(customer_phone)s,
                    %(status)s,
                    %(token_hash)s,
                    %(token_expires_at)s,
                    %(token_used_at)s,
                    %(sms_status)s,
                    %(email_status)s,
                    %(ms_status)s,
                    %(notification_error)s,
                    %(submitted_payload)s,
                    %(submitted_at)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    created_by = EXCLUDED.created_by,
                    customer_name = EXCLUDED.customer_name,
                    customer_email = EXCLUDED.customer_email,
                    customer_phone = EXCLUDED.customer_phone,
                    status = EXCLUDED.status,
                    token_hash = EXCLUDED.token_hash,
                    token_expires_at = EXCLUDED.token_expires_at,
                    token_used_at = EXCLUDED.token_used_at,
                    sms_status = EXCLUDED.sms_status,
                    email_status = EXCLUDED.email_status,
                    ms_status = EXCLUDED.ms_status,
                    notification_error = EXCLUDED.notification_error,
                    submitted_payload = EXCLUDED.submitted_payload,
                    submitted_at = EXCLUDED.submitted_at
                """,
                {
                    **row,
                    "created_by": _coerce_fk(row.get("created_by"), allowed_ids=allowed_admin_ids),
                },
            )
            inserted += 1
    return inserted


def _upsert_workflow_cases(
    conn: psycopg.Connection[Any], *, rows: Iterable[dict[str, Any]], allowed_admin_ids: set[int]
) -> int:
    inserted = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                INSERT INTO ctip.form_workflow_case (
                    id,
                    created_at,
                    updated_at,
                    form_request_id,
                    created_by,
                    updated_by,
                    stage,
                    business_status,
                    client_mode,
                    firebird_client_id,
                    firebird_client_status,
                    client_payload_snapshot,
                    proforma_firebird_id,
                    proforma_number,
                    proforma_pdf_path,
                    delivery_date,
                    delivery_time_window,
                    delivery_contact_name,
                    delivery_contact_phone,
                    delivery_notes
                )
                VALUES (
                    %(id)s,
                    %(created_at)s,
                    %(updated_at)s,
                    %(form_request_id)s,
                    %(created_by)s,
                    %(updated_by)s,
                    %(stage)s,
                    %(business_status)s,
                    %(client_mode)s,
                    %(firebird_client_id)s,
                    %(firebird_client_status)s,
                    %(client_payload_snapshot)s,
                    %(proforma_firebird_id)s,
                    %(proforma_number)s,
                    %(proforma_pdf_path)s,
                    %(delivery_date)s,
                    %(delivery_time_window)s,
                    %(delivery_contact_name)s,
                    %(delivery_contact_phone)s,
                    %(delivery_notes)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
                    form_request_id = EXCLUDED.form_request_id,
                    created_by = EXCLUDED.created_by,
                    updated_by = EXCLUDED.updated_by,
                    stage = EXCLUDED.stage,
                    business_status = EXCLUDED.business_status,
                    client_mode = EXCLUDED.client_mode,
                    firebird_client_id = EXCLUDED.firebird_client_id,
                    firebird_client_status = EXCLUDED.firebird_client_status,
                    client_payload_snapshot = EXCLUDED.client_payload_snapshot,
                    proforma_firebird_id = EXCLUDED.proforma_firebird_id,
                    proforma_number = EXCLUDED.proforma_number,
                    proforma_pdf_path = EXCLUDED.proforma_pdf_path,
                    delivery_date = EXCLUDED.delivery_date,
                    delivery_time_window = EXCLUDED.delivery_time_window,
                    delivery_contact_name = EXCLUDED.delivery_contact_name,
                    delivery_contact_phone = EXCLUDED.delivery_contact_phone,
                    delivery_notes = EXCLUDED.delivery_notes
                """,
                {
                    **row,
                    "created_by": _coerce_fk(row.get("created_by"), allowed_ids=allowed_admin_ids),
                    "updated_by": _coerce_fk(row.get("updated_by"), allowed_ids=allowed_admin_ids),
                    "client_payload_snapshot": _json_param(row.get("client_payload_snapshot")),
                },
            )
            inserted += 1
    return inserted


def _upsert_workflow_devices(
    conn: psycopg.Connection[Any], *, rows: Iterable[dict[str, Any]]
) -> int:
    inserted = 0
    with conn.cursor() as cur:
        for row in rows:
            cur.execute(
                """
                INSERT INTO ctip.form_workflow_device (
                    id,
                    created_at,
                    workflow_case_id,
                    source_type,
                    source_row,
                    producer,
                    model,
                    serial,
                    ewidencja,
                    device_status,
                    reservation_status,
                    price,
                    price_net,
                    price_gross,
                    firebird_machine_id,
                    firebird_client_id,
                    snapshot
                )
                VALUES (
                    %(id)s,
                    %(created_at)s,
                    %(workflow_case_id)s,
                    %(source_type)s,
                    %(source_row)s,
                    %(producer)s,
                    %(model)s,
                    %(serial)s,
                    %(ewidencja)s,
                    %(device_status)s,
                    %(reservation_status)s,
                    %(price)s,
                    %(price_net)s,
                    %(price_gross)s,
                    %(firebird_machine_id)s,
                    %(firebird_client_id)s,
                    %(snapshot)s
                )
                ON CONFLICT (id) DO UPDATE SET
                    created_at = EXCLUDED.created_at,
                    workflow_case_id = EXCLUDED.workflow_case_id,
                    source_type = EXCLUDED.source_type,
                    source_row = EXCLUDED.source_row,
                    producer = EXCLUDED.producer,
                    model = EXCLUDED.model,
                    serial = EXCLUDED.serial,
                    ewidencja = EXCLUDED.ewidencja,
                    device_status = EXCLUDED.device_status,
                    reservation_status = EXCLUDED.reservation_status,
                    price = EXCLUDED.price,
                    price_net = EXCLUDED.price_net,
                    price_gross = EXCLUDED.price_gross,
                    firebird_machine_id = EXCLUDED.firebird_machine_id,
                    firebird_client_id = EXCLUDED.firebird_client_id,
                    snapshot = EXCLUDED.snapshot
                """,
                {
                    **row,
                    "snapshot": _json_param(row.get("snapshot")),
                },
            )
            inserted += 1
    return inserted


def build_arg_parser() -> argparse.ArgumentParser:
    """Buduje parser argumentów CLI."""

    parser = argparse.ArgumentParser(
        description=(
            "Importuje formularze z produkcyjnej bazy PostgreSQL do lokalnego ctip_test "
            "bez wykonywania zapisów po stronie źródła."
        )
    )
    parser.add_argument("--limit", type=int, default=200, help="Liczba najnowszych formularzy.")
    parser.add_argument(
        "--status",
        action="append",
        dest="statuses",
        help="Status formularza do importu; można podać wielokrotnie. Domyślnie: SUBMITTED.",
    )
    parser.add_argument("--source-host")
    parser.add_argument("--source-port", type=int)
    parser.add_argument("--source-db")
    parser.add_argument("--source-user")
    parser.add_argument("--source-password")
    parser.add_argument("--source-sslmode")
    parser.add_argument("--target-host")
    parser.add_argument("--target-port", type=int)
    parser.add_argument("--target-db")
    parser.add_argument("--target-user")
    parser.add_argument("--target-password")
    parser.add_argument("--target-sslmode")
    return parser


def main() -> None:
    """Uruchamia import formularzy z produkcji do testu."""

    parser = build_arg_parser()
    args = parser.parse_args()
    statuses = _normalize_statuses(args.statuses)
    source_cfg = _build_source_config(args)
    target_cfg = _build_target_config(args)

    if target_cfg.database != "ctip_test":
        raise SystemExit(
            f"Docelowa baza musi być testowa (`ctip_test`), otrzymano: {target_cfg.database}"
        )
    if source_cfg.host == target_cfg.host and source_cfg.database == target_cfg.database:
        raise SystemExit("Źródło i cel wskazują na tę samą bazę. Import został zablokowany.")

    with _connect(source_cfg) as source_conn, _connect(target_cfg) as target_conn:
        forms = _fetch_forms(source_conn, statuses=statuses, limit=args.limit)
        form_ids = [int(item["id"]) for item in forms]
        workflow_cases = _fetch_workflow_cases(source_conn, form_ids=form_ids)
        workflow_case_ids = [int(item["id"]) for item in workflow_cases]
        workflow_devices = _fetch_workflow_devices(source_conn, workflow_case_ids=workflow_case_ids)

        allowed_admin_ids = _fetch_target_admin_user_ids(target_conn)
        with target_conn.transaction():
            forms_count = _upsert_forms(
                target_conn, rows=forms, allowed_admin_ids=allowed_admin_ids
            )
            cases_count = _upsert_workflow_cases(
                target_conn, rows=workflow_cases, allowed_admin_ids=allowed_admin_ids
            )
            devices_count = _upsert_workflow_devices(target_conn, rows=workflow_devices)
            _set_sequence(target_conn, "ctip.form_request_id_seq", "ctip.form_request")
            _set_sequence(target_conn, "ctip.form_workflow_case_id_seq", "ctip.form_workflow_case")
            _set_sequence(
                target_conn, "ctip.form_workflow_device_id_seq", "ctip.form_workflow_device"
            )

    print(
        "Import zakonczony: "
        f"formularze={forms_count}, workflow={cases_count}, urzadzenia={devices_count}, "
        f"statusy={','.join(statuses)}, limit={args.limit}"
    )


if __name__ == "__main__":
    main()
