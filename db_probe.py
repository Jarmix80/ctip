import datetime as dt
import pathlib

import psycopg2

LOG = pathlib.Path("db_probe.log")
DB = "postgresql://appuser:Lomtjjz1@192.168.0.8:5433/ctip"
SCHEMA = "ctip"


def log(msg):
    LOG.open("a", encoding="utf-8").write(f"{dt.datetime.now().isoformat()} {msg}\n")


try:
    con = psycopg2.connect(DB, connect_timeout=5)
    cur = con.cursor()
    cur.execute(f"SET search_path TO {SCHEMA}")
    cur.execute("SELECT now()")
    log(f"DB OK: {cur.fetchone()[0]}")
    cur.execute(
        """INSERT INTO ctip.call_events(ts,typ,ext,number,payload)
                   VALUES (now(),'TEST','0000',NULL,'probe-from-db-probe')
                   RETURNING id"""
    )
    eid = cur.fetchone()[0]
    con.commit()
    log(f"INSERT OK: call_events.id={eid}")
    con.close()
except Exception as e:
    log(f"ERROR: {e!r}")
