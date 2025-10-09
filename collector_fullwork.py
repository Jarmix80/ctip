# collector_full.py  (psycopg3, BYTES-SAFE, writes calls + call_events)
import pathlib
import socket
import time
from datetime import UTC, datetime

import psycopg

# ---- KONFIG ----
HOST, PORT, PIN = "192.168.0.11", 5524, "1234"  # PBX CTIP
DB_KW = dict(
    host="192.168.0.8",
    port=5433,
    dbname="ctip",
    user="appuser",
    password="Lomtjjz1",
    options="-c search_path=ctip",
    sslmode="disable",  # serwer bez TLS
    autocommit=True,
)
PAYLOAD_ENCODING = "latin-1"  # payload zapisujemy tak, by nigdy się nie wywaliło
LOG = pathlib.Path("collector.log")


def log(msg: str):
    try:
        LOG.open("a", encoding="utf-8").write(f"{datetime.now().isoformat()} {msg}\n")
    except Exception:
        pass


def utcnow():
    return datetime.now(UTC)


# --- korelacja stanu po ext ---
ringing, dialed, connected = {}, {}, {}
# ringing[ext]   = {"num": cli, "call_id": id}
# dialed[ext]    = {"num": digits, "call_id": id}
# connected[ext] = {"num": peer, "call_id": id}


# --- DB helpers ---
def ev_insert(conn, call_id, typ, ext, number, payload_bytes: bytes):
    payload_txt = payload_bytes.decode(PAYLOAD_ENCODING, errors="ignore") if payload_bytes else None
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO call_events(call_id, ts, typ, ext, number, payload) VALUES (NULLIF(%s,0), %s, %s, %s, %s, %s)",
            (call_id or 0, utcnow(), typ, ext, number, payload_txt),
        )


def call_insert(conn, ext, number, direction, last_state):
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO calls(ext, number, direction, started_at, last_state, disposition)
               VALUES (%s,%s,%s,%s,%s,'UNKNOWN') RETURNING id""",
            (ext, number, direction, utcnow(), last_state),
        )
        cid = cur.fetchone()[0]
    log(f"[DB] NEW call id={cid} dir={direction} ext={ext} num={number}")
    return cid


def call_update_on_conn(conn, call_id, ext, peer):
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE calls
                 SET connected_at=%s,
                     answered_by  = COALESCE(answered_by, %s),
                     number       = COALESCE(number, %s),
                     last_state   = 'CONN',
                     disposition  = 'ANSWERED'
               WHERE id=%s""",
            (utcnow(), ext, peer, call_id),
        )
    log(f"[DB] CONN -> call {call_id} answered_by={ext} peer={peer}")


def call_update_on_rel(conn, call_id):
    now = utcnow()
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE calls
                 SET ended_at   = %s,
                     last_state = 'REL',
                     duration_s = CASE WHEN connected_at IS NOT NULL
                                       THEN EXTRACT(EPOCH FROM (%s - connected_at))::int END,
                     disposition = CASE WHEN connected_at IS NOT NULL THEN 'ANSWERED'
                                        ELSE 'NO_ANSWER' END
               WHERE id=%s""",
            (now, now, call_id),
        )
    log(f"[DB] REL  -> call {call_id} closed")


# --- CTIP I/O (bytes) ---
def send(sock, cmd_ascii: str):
    sock.sendall(("a" + cmd_ascii + "\r\n").encode("ascii", "ignore"))


def iter_lines(sock):
    buf = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        while True:
            i = buf.find(b"\r\n")
            if i < 0:
                break
            line = buf[:i]
            buf = buf[i + 2 :]
            yield line


def tok_ascii(b: bytes) -> str:
    return b.decode("ascii", errors="ignore")


def handle_line(conn, raw: bytes):
    if not raw or raw == b"t":  # heartbeat
        return
    if raw.startswith(b"a") and len(raw) > 1:
        raw = raw[1:]
    parts = raw.split()
    if not parts:
        return
    typ = tok_ascii(parts[0]).upper()

    if typ in ("OK", "WHO", "HELP"):  # sterujące
        return

    # --- WYCHODZĄCE: ECHO <ext> <digits> ---
    if typ == "ECHO" and len(parts) >= 3:
        ext = tok_ascii(parts[1])
        digits = tok_ascii(parts[2])
        if ext not in dialed:
            cid = call_insert(conn, ext=ext, number=digits, direction="OUT", last_state="ECHO")
            dialed[ext] = {"num": digits, "call_id": cid}
        else:
            dialed[ext]["num"] = digits
            cid = dialed[ext]["call_id"]
        ev_insert(conn, cid, "ECHO", ext, digits, raw)
        return

    # --- PRZYCHODZĄCE: RING <ext> <cli> ---
    if typ == "RING" and len(parts) >= 3:
        ext = tok_ascii(parts[1])
        cli = tok_ascii(parts[2])
        if ext not in ringing:
            cid = call_insert(conn, ext=ext, number=cli, direction="IN", last_state="RING")
            ringing[ext] = {"num": cli, "call_id": cid}
        else:
            cid = ringing[ext]["call_id"]
        ev_insert(conn, cid, "RING", ext, cli, raw)
        return

    # --- ZESTAWIONE: CONN <ext> <peer> ---
    if typ == "CONN" and len(parts) >= 3:
        ext = tok_ascii(parts[1])
        peer = tok_ascii(parts[2])
        if ext in ringing:  # IN -> answered
            cid = ringing[ext]["call_id"]
            call_update_on_conn(conn, cid, ext, peer)
            connected[ext] = {"num": peer, "call_id": cid}
            ev_insert(conn, cid, "CONN", ext, peer, raw)
            ringing.pop(ext, None)
        elif ext in dialed:  # OUT -> connected
            cid = dialed[ext]["call_id"]
            call_update_on_conn(conn, cid, ext, peer)
            connected[ext] = {"num": peer, "call_id": cid}
            ev_insert(conn, cid, "CONN", ext, peer, raw)
            dialed.pop(ext, None)
        else:  # widzimy tylko CONN (brak kontekstu) → potraktuj jako OUT
            cid = call_insert(conn, ext=ext, number=peer, direction="OUT", last_state="CONN")
            call_update_on_conn(conn, cid, ext, peer)
            connected[ext] = {"num": peer, "call_id": cid}
            ev_insert(conn, cid, "CONN", ext, peer, raw)
        return

    # --- ROZŁĄCZONE: REL <ext> <cause> ---
    if typ == "REL" and len(parts) >= 2:
        ext = tok_ascii(parts[1])
        if ext in connected:
            cid = connected[ext]["call_id"]
            call_update_on_rel(conn, cid)
            ev_insert(conn, cid, "REL", ext, None, raw)
            connected.pop(ext, None)
        elif ext in ringing:  # nieodebrane IN
            cid = ringing[ext]["call_id"]
            call_update_on_rel(conn, cid)
            ev_insert(conn, cid, "REL", ext, None, raw)
            ringing.pop(ext, None)
        elif ext in dialed:  # OUT bez CONN
            cid = dialed[ext]["call_id"]
            call_update_on_rel(conn, cid)
            ev_insert(conn, cid, "REL", ext, None, raw)
            dialed.pop(ext, None)
        else:
            ev_insert(conn, None, "REL", ext, None, raw)
        return

    # STAT/INFO/EVNT — opcjonalnie przypnij do najbliższego kontekstu
    if typ in ("STAT", "INFO", "EVNT", "ECHOX"):
        ctx = connected or ringing or dialed
        if ctx:
            any_cid = next(iter(ctx.values()))["call_id"]
            ext = tok_ascii(parts[1]) if len(parts) > 1 else None
            ev_insert(conn, any_cid, typ, ext, None, raw)


def main():
    while True:
        try:
            with psycopg.connect(**DB_KW) as conn:
                while True:
                    try:
                        with socket.create_connection((HOST, PORT), timeout=5) as s:
                            s.settimeout(30)
                            send(s, "WHO")
                            time.sleep(0.2)
                            send(s, f"LOGA {PIN}")
                            for raw in iter_lines(s):
                                try:
                                    handle_line(conn, raw)
                                except Exception as e:
                                    log(f"[ERR] handle_line: {e!r}")
                                    break
                    except Exception as e:
                        log(f"[ERR] socket: {e!r}")
                        time.sleep(2)
        except Exception as e:
            log(f"[ERR] db connect: {e!r}")
            time.sleep(2)


if __name__ == "__main__":
    log("== collector_full start ==")
    main()
