# collector_full.py
# Slican CTIP -> PostgreSQL (tables: calls, call_events) + IVR mapping (ext -> SMS queue)
# - No DTMF parsing from IVR (CTIP does not emit it)
# - Infers IVR selection by first RING to mapped internal (ctip.ivr_map)
# - Enqueues SMS into ctip.sms_out (status NEW); external poller sends SMS

import os
import socket
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime

import psycopg

from log_utils import append_log


class CTIPProtocolError(RuntimeError):
    """Błąd protokołu CTIP odebrany w komunikacie kontrolnym."""


class CTIPLoginError(CTIPProtocolError):
    """Błąd logowania monitoringu CTIP (np. zajęte połączenie lub błędny PIN)."""


class SchemaValidationError(RuntimeError):
    """Nieprawidłowy lub niekompletny schemat bazy danych."""


@dataclass(slots=True)
class CallContext:
    """Stan aktywnego połączenia powiązanego z numerem wewnętrznym."""

    call_id: int
    ext_raw: str | None
    ext_norm: str | None
    direction: str | None
    number: str | None
    keys: tuple[str, ...]


# -------------------- Config (env) --------------------
PBX_HOST = os.getenv("PBX_HOST", "192.168.0.11")
PBX_PORT = int(os.getenv("PBX_PORT", "5524"))
PBX_PIN = os.getenv("PBX_PIN", "1234")  # CTIP LOGA PIN

PGHOST = os.getenv("PGHOST", "192.168.0.8")
PGPORT = int(os.getenv("PGPORT", "5433"))
PGDATABASE = os.getenv("PGDATABASE", "ctip")
PGUSER = os.getenv("PGUSER", "appuser")
PGPASSWORD = os.getenv("PGPASSWORD", "change_me")
PGSSLMODE = os.getenv("PGSSLMODE", "disable")

SOCK_CONNECT_TIMEOUT = int(os.getenv("SOCK_CONNECT_TIMEOUT", "5"))
SOCK_RECV_TIMEOUT = int(os.getenv("SOCK_RECV_TIMEOUT", "5"))
RECONNECT_DELAY_SEC = int(os.getenv("RECONNECT_DELAY_SEC", "3"))
LOGIN_ACK_TIMEOUT = int(os.getenv("LOGIN_ACK_TIMEOUT", "8"))

PAYLOAD_ENCODING = os.getenv("PAYLOAD_ENCODING", "latin-1")
LOG_PREFIX = "[CTIP]"

COLLECTOR_LOG_SUBDIR = "Centralka"
COLLECTOR_LOG_BASE = "log_collector"
DB_LOG_SUBDIR = "BAZAPostGre"
DB_LOG_BASE = f"log_{PGHOST}_postgre"


def _format_timestamp(now: datetime | None = None) -> tuple[datetime, str]:
    now = now or datetime.now()
    return now, now.strftime("%Y-%m-%d %H:%M:%S")


def log_collector(message: str, *, now: datetime | None = None) -> None:
    now, ts = _format_timestamp(now)
    line = f"{LOG_PREFIX} {message}"
    append_log(COLLECTOR_LOG_SUBDIR, COLLECTOR_LOG_BASE, line, now=now)
    print(f"[{ts}] {line}", flush=True)


def log_db(message: str, *, now: datetime | None = None, prefix: str | None = "[DB]") -> None:
    now, ts = _format_timestamp(now)
    line = f"{prefix} {message}" if prefix else message
    append_log(DB_LOG_SUBDIR, DB_LOG_BASE, line, now=now)
    print(f"[{ts}] {line}", flush=True)


# -------------------- Helpers --------------------
def norm_ext(s: str) -> str:
    if not s:
        return s
    s = s.strip()
    s = s.split("/", 1)[0]
    while s.endswith("_"):
        s = s[:-1]
    s = "".join(ch for ch in s if ch.isdigit())
    return s


def utcnow():
    return datetime.now(UTC)


def db_connect():
    return psycopg.connect(
        host=PGHOST,
        port=PGPORT,
        dbname=PGDATABASE,
        user=PGUSER,
        password=PGPASSWORD,
        sslmode=PGSSLMODE,
        autocommit=True,
        options="-c search_path=ctip",
    )


REQUIRED_SCHEMA_COLUMNS: dict[str, set[str]] = {
    "calls": {
        "id",
        "ext",
        "number",
        "direction",
        "answered_by",
        "started_at",
        "connected_at",
        "ended_at",
        "duration_s",
        "disposition",
        "last_state",
        "notes",
    },
    "call_events": {
        "id",
        "call_id",
        "ts",
        "typ",
        "ext",
        "number",
        "payload",
    },
    "sms_out": {
        "id",
        "created_at",
        "dest",
        "text",
        "source",
        "status",
        "error_msg",
        "call_id",
        "meta",
        "created_by",
        "template_id",
        "origin",
        "provider_msg_id",
        "provider_status",
        "provider_error_code",
        "provider_error_desc",
    },
    "ivr_map": {"digit", "ext", "sms_text", "enabled"},
    "contact": {
        "id",
        "number",
        "ext",
        "first_name",
        "last_name",
        "company",
        "nip",
        "email",
        "notes",
        "source",
        "created_at",
        "updated_at",
    },
    "contact_device": {
        "id",
        "contact_id",
        "device_name",
        "serial_number",
        "location",
        "notes",
        "created_at",
    },
}


def _collect_schema_columns(conn) -> dict[str, set[str]]:
    """
    Zwraca mapę {tabela: zbiór kolumn} dla schematu `ctip`.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name
              FROM information_schema.columns
             WHERE table_schema = 'ctip'
            """
        )
        rows = cur.fetchall()
    tables: dict[str, set[str]] = {}
    for table, column in rows:
        tables.setdefault(table, set()).add(column)
    return tables


def _missing_required_columns(existing: dict[str, set[str]]) -> dict[str, set[str]]:
    """
    Dla podanej mapy kolumn zwraca słownik braków {tabela: brakujące kolumny}.
    """
    missing: dict[str, set[str]] = {}
    for table, required_cols in REQUIRED_SCHEMA_COLUMNS.items():
        present = existing.get(table, set())
        not_found = required_cols - present
        if not_found:
            missing[table] = not_found
    return missing


def verify_required_schema(conn) -> None:
    """
    Weryfikuje, czy schemat `ctip` zawiera wymagane tabele i kolumny.
    W razie braków zgłasza RuntimeError i nie kontynuuje pracy kolektora.
    """
    try:
        existing = _collect_schema_columns(conn)
    except Exception as exc:
        raise SchemaValidationError(f"Nie można odczytać metadanych schematu ctip: {exc}") from exc

    missing = _missing_required_columns(existing)
    if missing:
        problems = ", ".join(
            f"{tbl}: {', '.join(sorted(cols))}" for tbl, cols in sorted(missing.items())
        )
        raise SchemaValidationError(
            f"Niekompletna struktura bazy (schemat ctip). Brakuje kolumn: {problems}."
        )


def call_insert(conn, ext, number, direction, state):
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO calls(ext, number, direction, started_at, last_state, disposition)
            VALUES (%s, %s, %s, %s, %s, 'UNKNOWN')
            RETURNING id
        """,
            (ext, number, direction, utcnow(), state),
        )
        return cur.fetchone()[0]


def call_update_connected(conn, call_id, answered_by):
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE calls
               SET connected_at = COALESCE(connected_at, %s),
                   answered_by  = COALESCE(answered_by, %s),
                   last_state   = 'CONN',
                   disposition  = 'ANSWERED'
             WHERE id = %s
        """,
            (utcnow(), answered_by, call_id),
        )


def call_update_ended(conn, call_id):
    now = utcnow()
    with conn.cursor() as cur:
        cur.execute(
            """
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
        """,
            (now, now, call_id),
        )


def ev_insert(conn, call_id, typ, ext, number, payload_bytes: bytes):
    payload_txt = payload_bytes.decode(PAYLOAD_ENCODING, errors="ignore") if payload_bytes else None
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO call_events(call_id, ts, typ, ext, number, payload)
            VALUES (NULLIF(%s,0), %s, %s, %s, %s, %s)
        """,
            (call_id or 0, utcnow(), typ, ext, number, payload_txt),
        )
    log_line: str | None = None
    if payload_bytes:
        log_line = payload_bytes.decode(PAYLOAD_ENCODING, errors="ignore").strip()
    elif payload_txt:
        log_line = payload_txt.strip()
    else:
        components = [part for part in (typ, ext, number) if part]
        if components:
            log_line = " ".join(components)
    if log_line:
        log_db(log_line, prefix=None)


def enqueue_sms(conn, call_id: int, dest: str, text: str):
    if not dest:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sms_out(dest, text, source, status, call_id, meta)
            VALUES (%s, %s, 'ivr', 'NEW', %s, jsonb_build_object('reason','ivr_map'))
            ON CONFLICT (call_id) WHERE source='ivr' DO NOTHING
        """,
            (dest, text, call_id),
        )


# -------------------- CTIP Client --------------------
class CTIPClient(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self.sock = None
        self.conn = None
        self.call_contexts: dict[str, CallContext] = {}
        self.last_context: CallContext | None = None
        self.logged_in = False
        self.awaiting_login_ack = False
        self.login_request_ts = None
        self.pbx_identity = None

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

    def send_command(self, command: str):
        """Wysyła komendę CTIP z wymaganym prefiksem `a`."""
        command = (command or "").strip()
        if not command:
            return
        if not command.lower().startswith("a"):
            command = f"a{command}"
        self.send_line(command)

    def parse_line(self, raw: bytes):
        try:
            s = raw.decode(PAYLOAD_ENCODING, errors="ignore").strip()
        except Exception:
            s = raw.decode("latin-1", errors="ignore").strip()
        if not s:
            return None, []
        parts = s.split()
        return parts[0].upper(), parts

    def _make_context(
        self,
        *,
        ext_raw: str | None,
        number: str | None,
        direction: str | None,
        call_id: int,
    ) -> CallContext:
        ext_norm = norm_ext(ext_raw) if ext_raw else None
        keys: list[str] = []
        if ext_norm:
            keys.append(ext_norm)
        if ext_raw and ext_raw != ext_norm:
            keys.append(ext_raw)
        if not keys:
            keys.append(f"call:{call_id}")
        unique_keys = tuple(dict.fromkeys(keys))
        return CallContext(
            call_id=call_id,
            ext_raw=ext_raw,
            ext_norm=ext_norm,
            direction=direction,
            number=number,
            keys=unique_keys,
        )

    def _register_context(self, ctx: CallContext):
        """Zapamiętuje kontekst dla wszystkich powiązanych kluczy."""
        existing = {self.call_contexts.get(key) for key in ctx.keys if key in self.call_contexts}
        for old in existing:
            if old is not None and old is not ctx:
                self._unregister_context(old)
        for key in ctx.keys:
            self.call_contexts[key] = ctx
        self.last_context = ctx

    def _unregister_context(self, ctx: CallContext):
        """Usuwa kontekst z mapy i aktualizuje wskaźnik ostatniego połączenia."""
        for key in ctx.keys:
            if self.call_contexts.get(key) is ctx:
                self.call_contexts.pop(key, None)
        if self.last_context is ctx:
            self.last_context = None

    def _get_context(self, ext_raw: str | None) -> CallContext | None:
        """Zwraca kontekst powiązany z numerem wewnętrznym (raw lub znormalizowanym)."""
        if not ext_raw:
            return None
        ext_norm = norm_ext(ext_raw)
        if ext_norm:
            ctx = self.call_contexts.get(ext_norm)
            if ctx:
                return ctx
        return self.call_contexts.get(ext_raw)

    def _context_for_event(self, ext_raw: str | None) -> CallContext | None:
        """Odnajduje kontekst po numerze lub zwraca ostatnio użyty."""
        ctx = self._get_context(ext_raw)
        if ctx:
            self.last_context = ctx
            return ctx
        if self.last_context:
            return self.last_context
        return None

    def _reset_contexts(self):
        """Czyści wszystkie zapamiętane konteksty po utracie połączenia."""
        self.call_contexts.clear()
        self.last_context = None

    def lookup_ivr_map(self, ext_raw: str):
        """
        Return (digit, sms_text) if ext (normalized or raw) exists and is enabled in ctip.ivr_map; else (None, None).
        """
        if not ext_raw:
            return None, None
        ext_norm = norm_ext(ext_raw)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT digit, sms_text
                  FROM ctip.ivr_map
                 WHERE enabled = true
                   AND ext IN (%s, %s)
                 LIMIT 1
            """,
                (ext_norm, ext_raw),
            )
            row = cur.fetchone()
        return (row[0], row[1]) if row else (None, None)

    def start_handshake(self):
        """Rozpoczyna sekwencję WHO/LOGA po zestawieniu gniazda TCP."""
        self.logged_in = False
        self.awaiting_login_ack = False
        self.login_request_ts = None
        self.pbx_identity = None
        self.send_command("WHO")

    def handle_control_packet(self, typ: str, parts: list[str]):
        """Obsługuje ramki odpowiedzi `aOK`/`aNAK` przesyłane przez centralę."""
        second_token = parts[1] if len(parts) > 1 else ""
        second_upper = second_token.upper()
        tail_txt = " ".join(parts[1:]) if len(parts) > 1 else ""

        if typ == "AOK":
            if second_upper.startswith(("NCP-", "CP-")):
                identity = tail_txt or second_upper
                self.pbx_identity = identity
                log_collector(f"centrala zgłosiła się jako {self.pbx_identity}")
                if not self.logged_in and not self.awaiting_login_ack:
                    self.send_command(f"LOGA {PBX_PIN}")
                    self.awaiting_login_ack = True
                    self.login_request_ts = time.monotonic()
                return
            if self.awaiting_login_ack:
                self.logged_in = True
                self.awaiting_login_ack = False
                self.login_request_ts = None
                if not self.pbx_identity and tail_txt:
                    self.pbx_identity = tail_txt
                suffix = f" ({tail_txt})" if tail_txt else ""
                log_collector(f"monitorowanie CTIP aktywne{suffix}")
                return
            if second_upper == "LOGA":
                self.logged_in = True
                self.awaiting_login_ack = False
                self.login_request_ts = None
                log_collector("monitorowanie CTIP aktywne")
                return

        if typ == "ANAK":
            detail = " ".join(parts[1:]) if len(parts) > 1 else "brak_powodu"
            if self.awaiting_login_ack or "LOGA" in second_upper or "LOGA" in detail.upper():
                self.awaiting_login_ack = False
                self.login_request_ts = None
                raise CTIPLoginError(f"LOGA odrzucone przez centralę: {detail}")
            raise CTIPProtocolError(f"Komenda odrzucona przez centralę: {detail}")

    def run(self):
        while not self._stop.is_set():
            try:
                with closing(
                    socket.create_connection((PBX_HOST, PBX_PORT), timeout=SOCK_CONNECT_TIMEOUT)
                ) as s:
                    s.settimeout(SOCK_RECV_TIMEOUT)
                    self.sock = s
                    log_collector(f"connected to {PBX_HOST}:{PBX_PORT}")

                    with db_connect() as conn:
                        self.conn = conn
                        verify_required_schema(conn)

                        # Sekwencja WHO/LOGA odbywa się w tle podczas pierwszych ramek CTIP.
                        self.start_handshake()

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
                                    try:
                                        self.handle_packet(ln)
                                    except CTIPProtocolError:
                                        raise
                            except TimeoutError:
                                continue
            except SchemaValidationError as e:
                msg = str(e)
                log_collector(msg)
                log_db(msg, prefix="[DB]")
                self._stop.set()
                break
            except Exception as e:
                log_collector(f"reconnect in {RECONNECT_DELAY_SEC}s po błędzie: {e}")
                if isinstance(e, psycopg.Error):
                    log_db(f"Błąd po stronie PostgreSQL: {e}", prefix="[DB]")
                time.sleep(RECONNECT_DELAY_SEC)
            finally:
                try:
                    if self.sock:
                        self.sock.close()
                except Exception:
                    pass
                self.sock = None
                self.conn = None
                self.logged_in = False
                self.awaiting_login_ack = False
                self.login_request_ts = None
                self.pbx_identity = None
                self._reset_contexts()

    def handle_packet(self, raw: bytes):
        typ, parts = self.parse_line(raw)
        if not typ:
            return

        original_typ = typ
        if typ and typ.startswith("A") and len(typ) > 1:
            typ = typ[1:]

        if original_typ in {"AOK", "ANAK"}:
            self.handle_control_packet(original_typ, parts)
            return

        if self.awaiting_login_ack and self.login_request_ts is not None:
            if time.monotonic() - self.login_request_ts > LOGIN_ACK_TIMEOUT:
                self.awaiting_login_ack = False
                self.login_request_ts = None
                raise CTIPLoginError("Brak potwierdzenia LOGA w wymaganym czasie.")

        if not self.logged_in:
            return

        # ECHO: internal dials (OUT)
        if typ == "ECHO":
            # ECHO <ext> <digits>
            ext_txt = parts[1] if len(parts) > 1 else None
            digits = parts[2] if len(parts) > 2 else None

            cid = call_insert(self.conn, ext_txt, digits, "OUT", "ECHO")
            ctx = self._make_context(
                ext_raw=ext_txt,
                number=digits,
                direction="OUT",
                call_id=cid,
            )
            self._register_context(ctx)
            ev_insert(self.conn, cid, typ, ext_txt, digits, raw)
            return

        # RING: incoming rings to internal (IN)
        if typ == "RING":
            # RING <ext> <cli>
            ext_txt = parts[1] if len(parts) > 1 else None
            cli = parts[2] if len(parts) > 2 else None

            cid = call_insert(self.conn, ext_txt, cli, "IN", "RING")
            ctx = self._make_context(
                ext_raw=ext_txt,
                number=cli,
                direction="IN",
                call_id=cid,
            )
            self._register_context(ctx)
            ev_insert(self.conn, cid, typ, ext_txt, cli, raw)

            # IVR inference
            ext_norm = ctx.ext_norm or norm_ext(ext_txt)
            info_payload = (
                f"NORM_EXT={ext_norm}".encode("ascii", errors="ignore")
                if ext_norm
                else b"NORM_EXT="
            )
            ev_insert(
                self.conn,
                cid,
                "INFO",
                ext_txt,
                cli,
                info_payload,
            )

            ivr_lookup_key = ctx.ext_norm or ext_txt or ""
            digit, sms_text = self.lookup_ivr_map(ivr_lookup_key)
            if digit is not None and sms_text:
                ev_insert(
                    self.conn,
                    cid,
                    "INFO",
                    ext_txt,
                    cli,
                    f"IVR_MAP_HIT digit={digit}".encode("ascii", errors="ignore"),
                )
                enqueue_sms(self.conn, cid, cli, sms_text)
            else:
                ev_insert(
                    self.conn,
                    cid,
                    "INFO",
                    ext_txt,
                    cli,
                    f"IVR_MAP_MISS ext={ext_norm}".encode("ascii", errors="ignore"),
                )
            return

        # CONN: connected
        if typ == "CONN":
            # CONN <ext> <peer>
            ext_txt = parts[1] if len(parts) > 1 else None
            peer = parts[2] if len(parts) > 2 else None

            ctx = self._get_context(ext_txt)
            if ctx is None:
                cid = call_insert(self.conn, ext_txt, peer, "IN", "CONN")
                ctx = self._make_context(
                    ext_raw=ext_txt,
                    number=peer,
                    direction="IN",
                    call_id=cid,
                )
                self._register_context(ctx)
            else:
                if peer:
                    ctx.number = peer
                if ctx.direction is None:
                    ctx.direction = "IN"
                self.last_context = ctx

            call_update_connected(self.conn, ctx.call_id, answered_by=ext_txt)
            ev_insert(self.conn, ctx.call_id, typ, ext_txt, peer, raw)
            return

        # REL: released
        if typ == "REL":
            # REL <ext> <reason?>
            ext_txt = parts[1] if len(parts) > 1 else None
            reason = parts[2] if len(parts) > 2 else None

            ctx = self._get_context(ext_txt)
            if ctx is None:
                ev_insert(self.conn, 0, typ, ext_txt, reason, raw)
                return

            call_update_ended(self.conn, ctx.call_id)
            ev_insert(self.conn, ctx.call_id, typ, ext_txt, reason, raw)
            self._unregister_context(ctx)
            return

        # Generic statuses
        if typ in ("STAT", "INFO", "EVNT", "WARN", "ERR"):
            ext_txt = parts[1] if len(parts) > 1 else None
            num = parts[2] if len(parts) > 2 else None
            ctx = self._context_for_event(ext_txt)
            cid = ctx.call_id if ctx else 0
            ev_insert(self.conn, cid, typ, ext_txt, num, raw)
            return

        # Fallback
        ctx = self._context_for_event(None)
        fallback_cid = ctx.call_id if ctx else 0
        ev_insert(self.conn, fallback_cid, typ, None, None, raw)


# -------------------- Main --------------------
def main():
    log_collector(f"starting; PBX={PBX_HOST}:{PBX_PORT} DB={PGHOST}:{PGPORT}/{PGDATABASE}")
    t = CTIPClient()
    t.start()
    try:
        while t.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        log_collector("stopping...")
        t.stop()
        time.sleep(1)


if __name__ == "__main__":
    main()
