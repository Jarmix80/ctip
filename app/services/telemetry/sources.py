"""Odczyt stronicowanych porcji Firebird, PrintRadar i IMAP bez zapisów źródłowych."""

import base64
import imaplib
import json
import re
import ssl
import unicodedata
from contextlib import contextmanager
from datetime import UTC, datetime

import firebirdsql
import psycopg

from app.core.config import settings
from app.services.telemetry.parsers import Reading, fingerprint, number, serial_number, timestamp

VM_TABLES = {
    "MASZYNY": "ID_TBL_MASZYNY",
    "MASZYNY_STATS": "ID_TBL_MASZYNY_STATS",
    "WEZWANIE": "ID_TBL_WEZWANIE",
    "MAGAZYNY": "ID_MAGAZYN",
    "DODAJ": "ID_TBL_DODAJ",
    "CPC": "ID_TBL_CPC",
}
PR_TABLES = {
    "device_fingerprints": "fingerprint_key",
    "raw_counter_samples": "sample_id",
    "service_snapshots": "snapshot_id",
    "material_readings": "id",
}
VM_METRICS = {
    "LICZNIK_CALKOWITY": "lifetime.total",
    "LICZNIK_MONO": "lifetime.black",
    "LICZNIK_KOLOR": "lifetime.color",
    "LICZNIK_KOPIA": "lifetime.copy",
    "LICZNIK_DRUK": "lifetime.print",
    "LICZNIK_SKAN": "lifetime.scan",
}
PR_METRICS = {
    "machine_total": "lifetime.total",
    "total_bw": "lifetime.black",
    "total_color": "lifetime.color",
    "scan_total": "lifetime.scan",
}


def serializable(value):
    """Zachowuje dane źródłowe, kodując tekst z NUL niedopuszczalnym w PostgreSQL JSONB."""
    if isinstance(value, str) and "\x00" in value:
        return {
            "__telemetry_encoding__": "utf-8/base64",
            "value": base64.b64encode(value.encode("utf-8")).decode("ascii"),
        }
    if isinstance(value, dict):
        return {key: serializable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(item) for item in value]
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


@contextmanager
def firebird_connection(vmaintenance=False, config=None):
    """Otwiera transakcję Firebird z potwierdzonym trybem tylko do odczytu."""
    prefix = "fb_v_" if vmaintenance else "fb_"
    connection = firebirdsql.connect(
        host=config.vm_host if vmaintenance and config else getattr(settings, prefix + "host"),
        port=getattr(settings, prefix + "port"),
        database=(
            config.vm_database
            if vmaintenance and config
            else getattr(settings, prefix + "database")
        ),
        user=(
            (config.vm_user if vmaintenance else config.ms_user)
            if config
            else getattr(settings, prefix + "user")
        ),
        password=(
            (config.vm_password if vmaintenance else config.ms_password).get_secret_value()
            if config
            else getattr(settings, prefix + "password")
        ),
        charset=(
            config.vm_charset if vmaintenance and config else getattr(settings, prefix + "charset")
        ),
        role=None if config else (getattr(settings, prefix + "role") or None),
        isolation_level=firebirdsql.ISOLATION_LEVEL_READ_COMMITED_RO,
        timeout=20,
    )
    try:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT MON$READ_ONLY FROM MON$TRANSACTIONS WHERE MON$TRANSACTION_ID=CURRENT_TRANSACTION"
        )
        if cursor.fetchone()[0] != 1:
            raise ValueError("firebird_not_readonly")
        yield connection
    finally:
        connection.rollback()
        connection.close()


def ms_identity_map(config=None) -> dict:
    """Czyta numery seryjne MS bez przyjmowania, że identyfikatory V są identyfikatorami MS."""
    result = {}
    with firebird_connection(config=config) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT ID_MASZYNA, ID_KLIENT, SERIAL FROM MASZYNA")
        for machine, customer, serial in cursor.fetchall():
            normalized = serial_number(serial)
            if normalized:
                result.setdefault(normalized, []).append((machine, customer))
    return result


def vm_serials(config=None) -> dict:
    """Buduje mapę logicznych identyfikatorów urządzeń wyłącznie wewnątrz V-Maintenance."""
    result = {}
    with firebird_connection(True, config) as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT ID_MASZYNA, ID_NR_SERYJNY FROM MASZYNY")
        for key, serial in cursor.fetchall():
            normalized = serial_number(serial)
            result[key] = normalized if key not in result or result[key] == normalized else ""
    return result


def ms_cpc_serials(config) -> dict:
    """Wybiera urządzenia aktywnych umów, używając logicznego ID_MASZYNA i technicznego ID umowy."""
    result = {}
    with firebird_connection(config=config) as connection:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT m.ID_MASZYNA, m.SERIAL FROM MASZYNA m "
            "JOIN UMOWACPC u ON u.ID_UMOWACPC_TABLE=m.ID_UMOWACPC WHERE u.AKTYWNA='TAK'"
        )
        for identifier, serial in cursor.fetchall():
            normalized = serial_number(serial)
            result[identifier] = (
                normalized if identifier not in result or result[identifier] == normalized else ""
            )
    return result


def ms_cpc_page(after, limit: int, config):
    """Czyta trzy lata okresów tylko urządzeń obecnie na aktywnych umowach, bez zapisu MS."""
    today = datetime.now(UTC).date()
    start_year = today.year - config.history_years
    with firebird_connection(config=config) as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"SELECT FIRST {int(limit)} c.* FROM CPC c WHERE c.ID_CPC_TABLE>? "
            "AND (c.ROK>? OR (c.ROK=? AND c.MIESIAC>=?)) "
            "AND (c.ROK<? OR (c.ROK=? AND c.MIESIAC<=?)) "
            "AND c.MIESIAC BETWEEN 1 AND 12 "
            "AND EXISTS (SELECT 1 FROM MASZYNA m JOIN UMOWACPC u "
            "ON u.ID_UMOWACPC_TABLE=m.ID_UMOWACPC "
            "WHERE m.ID_MASZYNA=c.ID_MASZYNA AND u.AKTYWNA='TAK') ORDER BY c.ID_CPC_TABLE",
            (after or 0, start_year, start_year, today.month, today.year, today.year, today.month),
        )
        names = [column[0].strip() for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def vm_page(table: str, after, limit: int, config=None):
    """Czyta porcję jawnie dopuszczonej tabeli z krótkiej transakcji odczytowej."""
    primary = VM_TABLES[table]
    with firebird_connection(True, config) as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"SELECT FIRST {int(limit)} * FROM {table} WHERE {primary}>? ORDER BY {primary}",
            (after or 0,),
        )
        names = [column[0].strip() for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def vm_reading(table: str, row: dict, identities: dict) -> Reading:
    """Przenosi pełny rekord biznesowy V wraz z rozpoznanymi licznikami i materiałami."""
    payload = serializable(row)
    serial = serial_number(
        row.get("ID_NR_SERYJNY") or row.get("NR_SERYJNY") or identities.get(row.get("ID_MASZYNA"))
    )
    date_value = row.get("DATA_LICZNIKA") if table == "MASZYNY" else row.get("DATA")
    observed, precision, basis = timestamp(str(date_value or ""), str(row.get("CZAS") or ""))
    kind = (
        "reading"
        if table in {"MASZYNY", "MASZYNY_STATS"}
        else "request" if table == "WEZWANIE" else "source_state"
    )
    result = Reading(
        f"{table}:{row[VM_TABLES[table]]}", kind, serial, payload, observed, precision, basis
    )
    result.issues.extend(
        ("source_text_encoded", key)
        for key, value in row.items()
        if isinstance(value, str) and "\x00" in value
    )
    if kind == "reading":
        for source, target in VM_METRICS.items():
            value = number(row.get(source))
            if value is not None:
                result.measurements[target] = value
        for code, color in {"BK": "black", "C": "cyan", "M": "magenta", "Y": "yellow"}.items():
            value = number(row.get("T_" + code))
            if value is not None:
                result.measurements[f"toner.{color}.percent"] = value
    result.semantic_key = fingerprint(
        [
            "vmaintenance",
            serial or result.external_key,
            kind,
            str(date_value),
            str(row.get("CZAS")),
            table == "MASZYNY",
        ]
    )
    if precision == "date":
        result.semantic_key = fingerprint([result.semantic_key, result.measurements])
    return result


@contextmanager
def printradar_connection(dsn: str):
    """Otwiera PostgreSQL źródła bez uprawnień do zmian i bez inicjalizacji aplikacji."""
    with psycopg.connect(
        dsn,
        connect_timeout=10,
        options="-c default_transaction_read_only=on -c statement_timeout=20000",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            if cursor.fetchone()[0] != "on":
                raise ValueError("printradar_not_readonly")
        try:
            yield connection
        finally:
            connection.rollback()


def printradar_page(dsn: str, table: str, after, limit: int, *, incremental=False):
    """Projektuje dane na serwerze źródłowym, bez przesyłania pełnych tablic SNMP i HTML."""
    primary = PR_TABLES[table]
    projection = "to_jsonb(data)"
    if table in {"raw_counter_samples", "service_snapshots"}:
        projection = "(to_jsonb(data)-'payload_json') || jsonb_build_object('payload_json', payload_json::jsonb-'raw_rows'-'ricoh_counter_rows'-'web_artifact')"
    condition, parameters = "", []
    order = primary
    if incremental and table != "device_fingerprints":
        order = f"created_at,{primary}"
        if after:
            condition = f"WHERE (created_at,{primary}) > (%s,%s)"
            parameters = list(after)
    elif after is not None:
        condition = f"WHERE {primary}>%s"
        parameters = [after]
    with printradar_connection(dsn) as connection:
        cursor = connection.cursor()
        cursor.execute(
            f"SELECT {projection} FROM {table} data {condition} ORDER BY {order} LIMIT %s",
            [*parameters, limit],
        )
        return [row[0] for row in cursor.fetchall()]


def material_identity(row: dict):
    """Normalizuje tylko potwierdzone aliasy, rozdzielając zużyty toner i materiały serwisowe."""
    raw = " ".join(str(row.get(key) or "") for key in ("supply_key", "description", "color"))
    normalized = "".join(
        char
        for char in unicodedata.normalize("NFKD", raw.casefold())
        if not unicodedata.combining(char)
    )
    normalized = normalized.replace("ł", "l")
    if any(word in normalized for word in ("waste", "zuzyt", "resttoner")):
        return "waste_toner", ""
    if "toner" not in normalized:
        return "other", row.get("supply_key", "")
    for color, aliases in {
        "black": ("black", "czarn", "schwarz"),
        "cyan": ("cyan", "cyjan"),
        "magenta": ("magenta",),
        "yellow": ("yellow", "zol", "gelb"),
    }.items():
        if any(alias in normalized for alias in aliases):
            return "toner", color
    return "toner_unknown", row.get("supply_key", "")


def printradar_reading(table: str, row: dict, identities: dict) -> Reading:
    """Zachowuje przetworzone dane i oceny jakości bez przejmowania prognoz źródła."""
    payload = serializable(row)
    serial = serial_number(row.get("serial_number") or identities.get(row.get("fingerprint_key")))
    observed, precision, basis = timestamp(
        str(row.get("collected_at") or row.get("last_seen_at") or "")
    )
    result = Reading(
        f"{table}:{row[PR_TABLES[table]]}", "reading", serial, payload, observed, precision, basis
    )
    if table == "device_fingerprints":
        result.kind = "device_state"
    canonical = row.get("canonical_counters_json") or {}
    if isinstance(canonical, str):
        canonical = json.loads(canonical)
    for source, target in PR_METRICS.items():
        value = number(canonical.get(source))
        if value is not None:
            result.measurements[target] = value
    if table == "material_readings":
        material, color = material_identity(row)
        value = number(row.get("percent"))
        if value is not None:
            result.measurements[f"{material}.{color}.percent"] = value
        result.semantic_key = fingerprint(
            ["printradar_material", row.get("snapshot_id"), material, color, row.get("unit")]
        )
    if row.get("source_status") == "error" or row.get("status") == "error":
        result.issues.append(("source_error", ""))
    return result


@contextmanager
def remote_mailbox(config):
    """Otwiera skrzynkę przez TLS bez flag zapisu ani poleceń SMTP."""
    mailbox = imaplib.IMAP4_SSL(
        config.imap_host, config.imap_port, ssl_context=ssl.create_default_context(), timeout=30
    )
    try:
        mailbox.login(config.email_address, config.email_password.get_secret_value())
        status, _ = mailbox.select(config.imap_folder, readonly=True)
        if status != "OK":
            raise ValueError("imap_select")
        yield mailbox
    finally:
        try:
            mailbox.logout()
        except (OSError, imaplib.IMAP4.error):
            pass


def mail_uids(mailbox, checkpoint: dict):
    """Unieważnia kursor po zmianie UIDVALIDITY, zachowując deduplikację treści."""
    validity = mailbox.response("UIDVALIDITY")[1]
    if not validity or not validity[0]:
        raise ValueError("imap_uidvalidity")
    generation = validity[0].decode()
    last = int(checkpoint.get("uid", 0)) if checkpoint.get("uidvalidity") == generation else 0
    status, data = mailbox.uid("search", None, "UID", f"{last+1}:*")
    if status != "OK":
        raise ValueError("imap_search")
    return generation, [int(value) for value in data[0].split() if int(value) > last]


def mail_bytes(mailbox, uid: int, max_bytes: int):
    """Sprawdza rozmiar przed pobraniem wiadomości bez oznaczania jej jako przeczytanej."""
    status, metadata = mailbox.uid("fetch", str(uid), "(RFC822.SIZE)")
    size = re.search(
        rb"RFC822.SIZE\s+(\d+)", b" ".join(item for item in metadata if isinstance(item, bytes))
    )
    if status != "OK" or not size or int(size[1]) > max_bytes:
        raise ValueError("imap_message_size")
    status, parts = mailbox.uid("fetch", str(uid), "(UID BODY.PEEK[])")
    if status != "OK":
        raise ValueError("imap_fetch")
    bodies = [part[1] for part in parts if isinstance(part, tuple)]
    if len(bodies) != 1 or len(bodies[0]) > max_bytes:
        raise ValueError("imap_body")
    return bodies[0]


def mail_batch(mailbox, uids: list[int], max_bytes: int) -> dict[int, bytes]:
    """Pobiera porcje IMAP z limitem pamięci, bez dwóch połączeń sieciowych na wiadomość."""
    status, metadata = mailbox.uid("fetch", ",".join(map(str, uids)), "(UID RFC822.SIZE)")
    if status != "OK":
        raise ValueError("imap_metadata")
    sizes, sequences = {}, {}
    for item in metadata:
        if not isinstance(item, bytes):
            continue
        uid = re.search(rb"UID\s+(\d+)", item)
        size = re.search(rb"RFC822.SIZE\s+(\d+)", item)
        sequence = re.match(rb"(\d+)", item)
        if uid and size:
            sizes[int(uid[1])] = int(size[1])
            if sequence:
                sequences[int(sequence[1])] = int(uid[1])
    batches, pending, total = [], [], 0
    for uid in uids:
        size = sizes.get(uid)
        if size is None or size > max_bytes:
            continue
        if pending and total + size > max_bytes:
            batches.append(pending)
            pending, total = [], 0
        pending.append(uid)
        total += size
    if pending:
        batches.append(pending)
    result = {}
    for batch in batches:
        status, parts = mailbox.uid("fetch", ",".join(map(str, batch)), "(UID BODY.PEEK[])")
        if status != "OK":
            raise ValueError("imap_fetch")
        for part in parts:
            if not isinstance(part, tuple):
                continue
            matched = re.search(rb"UID\s+(\d+)", part[0])
            sequence = re.match(rb"(\d+)", part[0])
            uid = (
                int(matched[1])
                if matched
                else sequences.get(int(sequence[1])) if sequence else None
            )
            if uid in batch and len(part[1]) <= max_bytes:
                result[uid] = part[1]
    return result
