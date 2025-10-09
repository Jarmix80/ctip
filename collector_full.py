# collector_full.py
# Slican CTIP -> PostgreSQL (tables: calls, call_events) + IVR mapping (ext -> SMS queue)
# - No DTMF parsing from IVR (CTIP does not emit it)
# - Infers IVR selection by first RING to mapped internal (ctip.ivr_map)
# - Enqueues SMS into ctip.sms_out (status NEW); external poller sends SMS

import os
import socket
import time
from contextlib import closing
from datetime import datetime, timezone
import threading

import psycopg

# -------------------- Config (env) --------------------
PBX_HOST = os.getenv("PBX_HOST", "192.168.0.8")
PBX_PORT = int(os.getenv("PBX_PORT", "5524"))
PBX_PIN  = os.getenv("PBX_PIN", "0000")  # CTIP LOGA PIN

PGHOST = os.getenv("PGHOST", "192.168.0.8")
PGPORT = int(os.getenv("PGPORT", "5433"))
PGDATABASE = os.getenv("PGDATABASE", "ctip")
PGUSER = os.getenv("PGUSER", "appuser")
PGPASSWORD = os.getenv("PGPASSWORD", "change_me")
PGSSLMODE = os.getenv("PGSSLMODE", "disable")

SOCK_CONNECT_TIMEOUT = int(os.getenv("SOCK_CONNECT_TIMEOUT", "5"))
SOCK_RECV_TIMEOUT    = int(os.getenv("SOCK_RECV_TIMEOUT", "5"))
RECONNECT_DELAY_SEC  = int(os.getenv("RECONNECT_DELAY_SEC", "3"))

PAYLOAD_ENCODING = os.getenv("PAYLOAD_ENCODING", "latin-1")
LOG_PREFIX = "[CTIP]"

# -------------------- Helpers --------------------
def norm_ext(s: str) -> str:
    if not s:
        return s
    s = s.strip()
    s = s.split('/', 1)[0]
    while s.endswith('_'):
        s = s[:-1]
    s = ''.join(ch for ch in s if ch.isdigit())
    return s

def utcnow():
    return datetime.now(timezone.utc)

def db_connect():
    return psycopg.connect(
        host=PGHOST, port=PGPORT, dbname=PGDATABASE,
        user=PGUSER, password=PGPASSWORD, sslmode=PGSSLMODE,
        autocommit=True,
        options="-c search_path=ctip"
    )

def ensure_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
        CREATE SCHEMA IF NOT EXISTS ctip;

        CREATE TABLE IF NOT EXISTS calls (
            id            bigserial PRIMARY KEY,
            ext           text,
            number        text,
            direction     text,
            started_at    timestamptz,
            connected_at  timestamptz,
            answered_by   text,
            ended_at      timestamptz,
            duration_s    integer,
            last_state    text,
            disposition   text
        );

        CREATE TABLE IF NOT EXISTS call_events (
            id        bigserial PRIMARY KEY,
            call_id   bigint,
            ts        timestamptz NOT NULL,
            typ       text,
            ext       text,
            number    text,
            payload   text
        );

        CREATE TABLE IF NOT EXISTS sms_out (
            id           bigserial PRIMARY KEY,
            created_at   timestamptz NOT NULL DEFAULT now(),
            dest         text NOT NULL,
            text         text NOT NULL,
            source       varchar(32) DEFAULT 'ivr',
            status       varchar(16) NOT NULL DEFAULT 'NEW',
            error_msg    text,
            call_id      bigint,
            meta         jsonb
        );

        CREATE INDEX IF NOT EXISTS idx_sms_out_status ON sms_out(status);

        CREATE UNIQUE INDEX IF NOT EXISTS uq_sms_out_callid_ivr
          ON sms_out(call_id) WHERE source='ivr';

        CREATE TABLE IF NOT EXISTS ivr_map (
          digit     smallint    NOT NULL,
          ext       text        NOT NULL,
          sms_text  text        NOT NULL,
          enabled   boolean     NOT NULL DEFAULT true,
          PRIMARY KEY (digit, ext)
        );
        CREATE INDEX IF NOT EXISTS idx_ivr_map_ext ON ivr_map(ext);

        -- helpful indices
        CREATE INDEX IF NOT EXISTS idx_calls_started_at ON calls(started_at);
        CREATE INDEX IF NOT EXISTS idx_calls_number ON calls(number);
        CREATE INDEX IF NOT EXISTS idx_call_events_ts ON call_events(ts);
        """)

def call_insert(conn, ext, number, direction, state):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO calls(ext, number, direction, started_at, last_state, disposition)
            VALUES (%s, %s, %s, %s, %s, 'UNKNOWN')
            RETURNING id
        """, (ext, number, direction, utcnow(), state))
        return cur.fetchone()[0]

def call_update_connected(conn, call_id, answered_by):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE calls
               SET connected_at = COALESCE(connected_at, %s),
                   answered_by  = COALESCE(answered_by, %s),
                   last_state   = 'CONN',
                   disposition  = 'ANSWERED'
             WHERE id = %s
        """, (utcnow(), answered_by, call_id))

def call_update_ended(conn, call_id):
    now = utcnow()
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE calls
               SET ended_at = %s,
                   duration_s = CASE
                                  WHEN connected_at IS NOT NULL THEN EXTRACT(EPOCH FROM (%s - connected_at))::int
                                  ELSE duration_s
                                END,
                   last_state = 'REL',
                   disposition = CASE
                                  WHEN connected_at IS NULL THEN 'NO_ANSWER'
                                  ELSE disposition
                                END
             WHERE id = %s
        """, (now, now, call_id))

def ev_insert(conn, call_id, typ, ext, number, payload_bytes: bytes):
    payload_txt = payload_bytes.decode(PAYLOAD_ENCODING, errors="ignore") if payload_bytes else None
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO call_events(call_id, ts, typ, ext, number, payload)
            VALUES (NULLIF(%s,0), %s, %s, %s, %s, %s)
        """, (call_id or 0, utcnow(), typ, ext, number, payload_txt))

def enqueue_sms(conn, call_id: int, dest: str, text: str):
    if not dest:
        return
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO sms_out(dest, text, source, status, call_id, meta)
            VALUES (%s, %s, 'ivr', 'NEW', %s, jsonb_build_object('reason','ivr_map'))
            ON CONFLICT (call_id) WHERE source='ivr' DO NOTHING
        """, (dest, text, call_id))

# -------------------- CTIP Client --------------------
class CTIPClient(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self.sock = None
        self.conn = None
        # simple context
        self.current_call_id = None
        self.current_ext = None
        self.current_cli = None
        self.direction = None  # IN/OUT

    def stop(self):
        self._stop.set()
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass

    def send_line(self, line: str):
        if not self.sock:
            return
        if not line.endswith("\r\n"):
            line += "\r\n"
        self.sock.sendall(line.encode("ascii", errors="ignore"))

    def parse_line(self, raw: bytes):
        try:
            s = raw.decode(PAYLOAD_ENCODING, errors="ignore").strip()
        except Exception:
            s = raw.decode("latin-1", errors="ignore").strip()
        if not s:
            return None, []
        parts = s.split()
        return parts[0].upper(), parts

    def lookup_ivr_map(self, ext_raw: str):
        """
        Return (digit, sms_text) if ext (normalized or raw) exists and is enabled in ctip.ivr_map; else (None, None).
        """
        if not ext_raw:
            return None, None
        ext_norm = norm_ext(ext_raw)
        with self.conn.cursor() as cur:
            cur.execute("""
                SELECT digit, sms_text
                  FROM ctip.ivr_map
                 WHERE enabled = true
                   AND ext IN (%s, %s)
                 LIMIT 1
            """, (ext_norm, ext_raw))
            row = cur.fetchone()
        return (row[0], row[1]) if row else (None, None)

    def run(self):
        while not self._stop.is_set():
            try:
                with closing(socket.create_connection((PBX_HOST, PBX_PORT), timeout=SOCK_CONNECT_TIMEOUT)) as s:
                    s.settimeout(SOCK_RECV_TIMEOUT)
                    self.sock = s
                    print(f"{LOG_PREFIX} connected to {PBX_HOST}:{PBX_PORT}")

                    with db_connect() as conn:
                        self.conn = conn
                        ensure_tables(conn)

                        # CTIP login
                        self.send_line(f"LOGA {PBX_PIN}")
                        # self.send_line("MONI")  # if required by PBX model/config

                        leftover = b""
                        while not self._stop.is_set():
                            try:
                                chunk = s.recv(4096)
                                if not chunk:
                                    raise ConnectionError("socket closed")
                                data = leftover + chunk
                                lines = data.split(b"\r\n")
                                leftover = lines[-1]
                                for ln in lines[:-1]:
                                    self.handle_packet(ln)
                            except socket.timeout:
                                continue
            except Exception as e:
                print(f"{LOG_PREFIX} reconnect in {RECONNECT_DELAY_SEC}s after error: {e}")
                time.sleep(RECONNECT_DELAY_SEC)
            finally:
                try:
                    if self.sock:
                        self.sock.close()
                except Exception:
                    pass
                self.sock = None
                self.conn = None

    def handle_packet(self, raw: bytes):
        typ, parts = self.parse_line(raw)
        if not typ:
            return

        # ECHO: internal dials (OUT)
        if typ == "ECHO":
            # ECHO <ext> <digits>
            ext_txt = parts[1] if len(parts) > 1 else None
            digits  = parts[2] if len(parts) > 2 else None

            self.current_ext = ext_txt
            self.current_cli = digits
            self.direction = "OUT"

            cid = call_insert(self.conn, ext_txt, digits, "OUT", "ECHO")
            self.current_call_id = cid
            ev_insert(self.conn, cid, "ECHO", ext_txt, digits, raw)
            return

        # RING: incoming rings to internal (IN)
        if typ == "RING":
            # RING <ext> <cli>
            ext_txt = parts[1] if len(parts) > 1 else None
            cli     = parts[2] if len(parts) > 2 else None

            self.current_ext = ext_txt
            self.current_cli = cli
            self.direction = "IN"

            cid = call_insert(self.conn, ext_txt, cli, "IN", "RING")
            self.current_call_id = cid
            ev_insert(self.conn, cid, "RING", ext_txt, cli, raw)

            # IVR inference
            ext_norm = norm_ext(ext_txt)
            ev_insert(self.conn, cid, "INFO", ext_txt, cli, f"NORM_EXT={ext_norm}".encode("ascii", errors="ignore"))

            digit, sms_text = self.lookup_ivr_map(ext_norm)
            if digit is not None and sms_text:
                ev_insert(self.conn, cid, "INFO", ext_txt, cli, f"IVR_MAP_HIT digit={digit}".encode("ascii", errors="ignore"))
                enqueue_sms(self.conn, cid, cli, sms_text)
            else:
                ev_insert(self.conn, cid, "INFO", ext_txt, cli, f"IVR_MAP_MISS ext={ext_norm}".encode("ascii", errors="ignore"))
            return

        # CONN: connected
        if typ == "CONN":
            # CONN <ext> <peer>
            ext_txt = parts[1] if len(parts) > 1 else None
            peer    = parts[2] if len(parts) > 2 else None

            cid = self.current_call_id
            if cid is None:
                cid = call_insert(self.conn, ext_txt, peer, "IN", "CONN")
                self.current_call_id = cid

            call_update_connected(self.conn, cid, answered_by=ext_txt)
            ev_insert(self.conn, cid, "CONN", ext_txt, peer, raw)
            return

        # REL: released
        if typ == "REL":
            # REL <ext> <reason?>
            ext_txt = parts[1] if len(parts) > 1 else None
            reason  = parts[2] if len(parts) > 2 else None

            cid = self.current_call_id
            if cid is None:
                ev_insert(self.conn, 0, "REL", ext_txt, reason, raw)
                return

            call_update_ended(self.conn, cid)
            ev_insert(self.conn, cid, "REL", ext_txt, reason, raw)

            # reset context
            self.current_call_id = None
            self.current_ext = None
            self.current_cli = None
            self.direction = None
            return

        # Generic statuses
        if typ in ("STAT", "INFO", "EVNT", "WARN", "ERR"):
            cid = self.current_call_id or 0
            ext_txt = parts[1] if len(parts) > 1 else None
            num     = parts[2] if len(parts) > 2 else None
            ev_insert(self.conn, cid, typ, ext_txt, num, raw)
            return

        # Fallback
        ev_insert(self.conn, self.current_call_id or 0, typ, None, None, raw)

# -------------------- Main --------------------
def main():
    print(f"{LOG_PREFIX} starting; PBX={PBX_HOST}:{PBX_PORT} DB={PGHOST}:{PGPORT}/{PGDATABASE}")
    t = CTIPClient()
    t.start()
    try:
        while t.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f"{LOG_PREFIX} stopping...")
        t.stop()
        time.sleep(1)

if __name__ == "__main__":
    main()
