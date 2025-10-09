# sms_sender.py
import os
import time

import psycopg

CONN = dict(
    host=os.getenv("PGHOST", "192.168.0.8"),
    port=int(os.getenv("PGPORT", "5433")),
    dbname=os.getenv("PGDATABASE", "ctip"),
    user=os.getenv("PGUSER", "appuser"),
    password=os.getenv("PGPASSWORD", "change_me"),
    options="-c search_path=ctip",
    sslmode=os.getenv("PGSSLMODE", "disable"),
    autocommit=True,
)

POLL_SEC = int(os.getenv("POLL_SEC", "3"))


def send_sms(dest: str, text: str) -> tuple[bool, str]:
    # TODO: implement real provider call here (SMSAPI/Teltonika/etc.)
    print(f"[FAKE-SEND] SMS to {dest}: {text}")
    return True, ""


def main():
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

                    for id_, dest, text in rows:
                        ok, err = send_sms(dest, text)
                        if ok:
                            cur.execute("UPDATE sms_out SET status='SENT' WHERE id=%s", (id_,))
                        else:
                            cur.execute(
                                "UPDATE sms_out SET status='ERROR', error_msg=%s WHERE id=%s",
                                (err, id_),
                            )
            except Exception as e:
                print("[ERR]", e)
                time.sleep(2)


if __name__ == "__main__":
    main()
