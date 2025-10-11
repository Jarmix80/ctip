# sms_sender.py
import os
import time

import psycopg

from app.core.config import settings
from app.services.sms_provider import HttpSmsProvider, SmsTransportError

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
)


def main() -> None:
    with psycopg.connect(**CONN) as conn:
        while True:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, dest, text
                        FROM sms_out
                        WHERE status='NEW'
                        ORDER BY created_at
                        LIMIT 10
                        FOR UPDATE SKIP LOCKED
                    """
                    )
                    rows = cur.fetchall()
                    if not rows:
                        time.sleep(POLL_SEC)
                        continue

                    for sms_id, dest, text in rows:
                        try:
                            result = provider.send_sms(dest, text, metadata={"sms_id": sms_id})
                            if result.success:
                                cur.execute(
                                    "UPDATE sms_out SET status='SENT', provider_status=%s, provider_msg_id=%s WHERE id=%s",
                                    (result.provider_status, result.provider_message_id, sms_id),
                                )
                            else:
                                cur.execute(
                                    "UPDATE sms_out SET status='ERROR', error_msg=%s, provider_status=%s WHERE id=%s",
                                    (result.error, result.provider_status, sms_id),
                                )
                        except SmsTransportError as exc:
                            cur.execute(
                                "UPDATE sms_out SET status='ERROR', error_msg=%s WHERE id=%s",
                                (str(exc), sms_id),
                            )
            except Exception as exc:
                print("[ERR]", exc)
                time.sleep(2)


if __name__ == "__main__":
    main()
