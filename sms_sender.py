# sms_sender.py
import os
import time

import psycopg

from app.core.config import settings
from app.services.sms_provider import HttpSmsProvider, SmsTransportError
from log_utils import append_log

CONN = dict(
    host=os.getenv("PGHOST", settings.pg_host),
    port=int(os.getenv("PGPORT", settings.pg_port)),
    dbname=os.getenv("PGDATABASE", settings.pg_database),
    user=os.getenv("PGUSER", settings.pg_user),
    password=os.getenv("PGPASSWORD", settings.pg_password),
    options="-c search_path=ctip",
    sslmode=os.getenv("PGSSLMODE", settings.pg_sslmode),
    autocommit=True,
)

POLL_SEC = int(os.getenv("POLL_SEC", "3"))

provider = HttpSmsProvider(
    settings.sms_api_url,
    settings.sms_api_token,
    settings.sms_default_sender,
    username=settings.sms_api_username,
    password=settings.sms_api_password,
    sms_type=settings.sms_type,
    test_mode=settings.sms_test_mode,
    delivery_mode=settings.outbound_delivery_mode,
)


LOG_SUBDIR = "sms"
LOG_BASE = "sms_sender"


def log_event(message: str) -> None:
    """Zapisuje pojedynczy wpis logu pracy sms_sender."""
    try:
        append_log(LOG_SUBDIR, LOG_BASE, message)
    except Exception:
        # W skrajnych przypadkach (brak uprawnień) zachowujemy cichy fallback.
        pass


def open_connection(connect_func=psycopg.connect):
    """Nawiązuje połączenie z PostgreSQL z konfiguracji środowiskowej."""
    return connect_func(**CONN)


def run_sender_loop(
    *,
    connect_func=psycopg.connect,
    sms_provider=provider,
    poll_sec: int = POLL_SEC,
    sleep_fn=time.sleep,
    max_cycles: int | None = None,
    log_fn=log_event,
) -> None:
    """
    Uruchamia pętlę pollingu kolejki SMS z automatycznym odświeżaniem połączenia.

    Parametr max_cycles służy do testów jednostkowych.
    """
    log_fn(f"Start pracy sms_sender (poll={poll_sec}s)")
    conn = None
    cycles = 0
    while True:
        if max_cycles is not None and cycles >= max_cycles:
            break
        cycles += 1
        try:
            if conn is None or getattr(conn, "closed", False):
                conn = open_connection(connect_func)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, dest, text, source, origin
                    FROM sms_out
                    WHERE status='NEW'
                    ORDER BY created_at
                    LIMIT 10
                    FOR UPDATE SKIP LOCKED
                """
                )
                rows = cur.fetchall()
                if not rows:
                    sleep_fn(poll_sec)
                    continue

                for sms_id, dest, text, source, origin in rows:
                    log_fn(f"[{sms_id}] Próba wysyłki do {dest}")
                    try:
                        result = sms_provider.send_sms(
                            dest,
                            text,
                            metadata={
                                "sms_id": sms_id,
                                "source": source or origin or "sms_sender",
                                "origin": origin,
                            },
                        )
                        if result.success:
                            cur.execute(
                                "UPDATE sms_out SET status='SENT', provider_status=%s, provider_msg_id=%s WHERE id=%s",
                                (result.provider_status, result.provider_message_id, sms_id),
                            )
                            log_fn(
                                f"[{sms_id}] Wysłano (status={result.provider_status or 'OK'}, msg_id={result.provider_message_id or '-'})"
                            )
                        else:
                            cur.execute(
                                "UPDATE sms_out SET status='ERROR', error_msg=%s, provider_status=%s WHERE id=%s",
                                (result.error, result.provider_status, sms_id),
                            )
                            log_fn(
                                f"[{sms_id}] Błąd odpowiedzi operatora: {result.error or 'brak szczegółów'}"
                            )
                    except SmsTransportError as exc:
                        cur.execute(
                            "UPDATE sms_out SET status='ERROR', error_msg=%s WHERE id=%s",
                            (str(exc), sms_id),
                        )
                        log_fn(f"[{sms_id}] Błąd transportu: {exc}")
        except psycopg.OperationalError as exc:
            print("[ERR]", exc)
            log_fn(f"[db] Błąd połączenia z PostgreSQL: {exc}")
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            conn = None
            sleep_fn(2)
        except Exception as exc:
            print("[ERR]", exc)
            log_fn(f"[loop] Wyjątek pętli: {exc}")
            sleep_fn(2)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass


def main() -> None:
    run_sender_loop()


if __name__ == "__main__":
    main()
