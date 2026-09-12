"""Odczyt historii MS dla KP ORBIT bez zapisów źródłowych i domyślania kosztów."""

import calendar
import logging
import re
from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from app.core.config import settings
from app.services.telemetry.config import TelemetrySettings, check_test_host
from app.services.telemetry.sources import firebird_connection, serializable

logger = logging.getLogger(__name__)

PAGE_SIZE = 500
BATCH_SIZE = 100
ID_BATCH_SIZE = 500
PRIMARY_KEYS = {
    "MASZYNA": "ID_MASZYNA_TABLE",
    "MODEL": "ID_MODEL",
    "KLIENT": "ID_KLIENT",
    "UMOWACPC": "ID_UMOWACPC_TABLE",
    "UMOWA": "ID_UMOWA",
    "CPC": "ID_CPC_TABLE",
    "ZLECENIE": "ID_ZLECENIE_TABLE",
    "ZPOZYCJA": "ID_ZPOZYCJA_TABLE",
    "ZAKUPY": "ID_ZAKUPY_TABLE",
    "ZAKPOZYCJA": "ID_ZAKPOZYCJA_TABLE",
    "FAKTURA": "ID_FAKTURA_TABLE",
    "FPOZYCJA": "ID_FPOZYCJA_TABLE",
    "SERIAL": "ID_SERIAL",
    "MAGAZYN": "ID_MAGAZYN_TABLE",
}
REFERENCE_COLUMNS = {
    "KLIENT": {"ID_KLIENT", "NAZWA"},
    "MODEL": {"ID_MODEL", "MARKA", "MODEL"},
    "MAGAZYN": {"ID_MAGAZYN_TABLE", "ID_MAGAZYN", "ILOSC"},
}
SECRET_FIELD = re.compile(r"PASS|PASSWORD|HASLO|HASŁO|SECRET|TOKEN|CREDENTIAL|API_KEY", re.I)


def _text(value):
    """Normalizuje tekst prezentacji, pozostawiając oryginał w części wewnętrznej."""
    return str(value).strip() if value is not None else ""


def _id(value):
    """Rozpoznaje dodatni identyfikator; zera MS pozostają brakiem relacji."""
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value or "")):
        return None
    return int(value) if int(value) > 0 else None


def _decimal(value):
    """Czyta dokładną liczbę bez zastępowania braków zerem."""
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _amount(value):
    """Serializuje liczby dziesiętne bez konwersji przez float."""
    result = _decimal(value)
    return format(result, "f") if result is not None else None


def _day(value):
    """Rozpoznaje wyłącznie daty ISO lub natywne daty Firebird."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def _safe(row):
    """Zachowuje strukturę dopuszczonych kolumn bez binariów i pól uwierzytelnienia."""
    return {
        name: (
            value.isoformat() if isinstance(value, (date, datetime, time)) else serializable(value)
        )
        for name, value in row.items()
        if not SECRET_FIELD.search(name)
        and not isinstance(value, (bytes, bytearray, memoryview))
        and not hasattr(value, "read")
    }


def _groups(rows, key):
    """Indeksuje rekordy, zachowując duplikaty do jawnej kontroli relacji."""
    result = defaultdict(list)
    for row in rows:
        result[key(row)].append(row)
    return result


def _batches(values, *, size=None):
    """Dzieli identyfikatory na porcje mieszczące się w limitach parametrów Firebird."""
    ordered = sorted(set(value for value in values if value is not None))
    size = size or BATCH_SIZE
    for offset in range(0, len(ordered), size):
        yield ordered[offset : offset + size]


def _placeholders(values):
    """Buduje miejsca parametrów bez wstawiania danych użytkownika do SQL."""
    return ",".join("?" for _ in values)


class _Reader:
    """Czyta dozwolone tabele porcjami kursora, zachowując kontrolę kluczy technicznych."""

    def __init__(self, cursor):
        """Sprawdza metadane przed wyborem kolumn, wykluczając BLOB-y i tablice."""
        self.cursor = cursor
        self.tables = {table: {} for table in PRIMARY_KEYS}
        self.columns = defaultdict(list)
        cursor.execute(
            "SELECT TRIM(r.RDB$RELATION_NAME), TRIM(r.RDB$FIELD_NAME) "
            "FROM RDB$RELATION_FIELDS r JOIN RDB$FIELDS f "
            "ON f.RDB$FIELD_NAME=r.RDB$FIELD_SOURCE "
            f"WHERE r.RDB$RELATION_NAME IN ({_placeholders(PRIMARY_KEYS)}) "
            "AND f.RDB$FIELD_TYPE<>261 AND f.RDB$DIMENSIONS IS NULL "
            "ORDER BY r.RDB$RELATION_NAME, r.RDB$FIELD_POSITION",
            tuple(PRIMARY_KEYS),
        )
        while rows := cursor.fetchmany(PAGE_SIZE):
            for table, field in rows:
                if (
                    table in PRIMARY_KEYS
                    and re.fullmatch(r"[A-Z][A-Z0-9_]*", field)
                    and not SECRET_FIELD.search(field)
                    and (table not in REFERENCE_COLUMNS or field in REFERENCE_COLUMNS[table])
                ):
                    self.columns[table].append(field)
        for table, primary in PRIMARY_KEYS.items():
            if primary not in self.columns[table]:
                raise ValueError(f"Niepełny schemat źródła ORBIT: {table}.{primary}.")

    def read(self, table, where="1=1", params=()):
        """Wyczerpuje porcje jednego SELECT bez ponawiania skanów i sortowania po PK."""
        primary = PRIMARY_KEYS[table]
        fields = self.columns[table]
        result = []
        seen = set()
        self.cursor.execute(
            f"SELECT {','.join('t.' + field for field in fields)} FROM {table} t WHERE ({where})",
            tuple(params),
        )
        while True:
            rows = self.cursor.fetchmany(PAGE_SIZE)
            if not rows:
                break
            for values in rows:
                row = dict(zip(fields, values, strict=True))
                key = row[primary]
                if key in seen:
                    raise ValueError("Kursor ORBIT powtórzył klucz w jednym odczycie.")
                seen.add(key)
                previous = self.tables[table].get(key)
                if previous is not None and previous != row:
                    raise ValueError(f"Źródło ORBIT zmieniło rekord podczas odczytu: {table}.")
                self.tables[table][key] = row
                result.append(row)
        if result:
            logger.info("Odczyt MS ORBIT: %s, %d rekordów.", table, len(result))
        return result

    def by_ids(self, table, field, identifiers):
        """Pobiera rekordy powiązane partiami, bez zapytań dla pojedynczej maszyny."""
        for batch in _batches(identifiers, size=ID_BATCH_SIZE):
            self.read(table, f"t.{field} IN ({_placeholders(batch)})", batch)

    def by_pairs(self, table, fields, pairs, where="1=1"):
        """Pobiera pary kluczy partiami bez skorelowanego skanowania całych tabel."""
        pairs = {
            tuple(pair)
            for pair in pairs
            if all(value is not None and value != "" for value in pair)
        }
        for batch in _batches(pairs):
            condition = " OR ".join(
                "(" + " AND ".join(f"t.{field}=?" for field in fields) + ")" for _ in batch
            )
            self.read(
                table,
                f"({where}) AND ({condition})",
                tuple(value for pair in batch for value in pair),
            )


def _collect(cursor, known_ids, only_machine_ids=None):
    """Pobiera aktywną flotę, znane maszyny i pełną osiągalną historię dokumentów."""
    reader = _Reader(cursor)
    active = (
        "EXISTS (SELECT 1 FROM UMOWACPC u "
        "WHERE u.ID_UMOWACPC_TABLE=t.ID_UMOWACPC AND u.AKTYWNA='TAK')"
    )
    if only_machine_ids is None:
        reader.read("MASZYNA", active)
    else:
        for batch in _batches(only_machine_ids):
            reader.read(
                "MASZYNA", f"({active}) AND t.ID_MASZYNA IN ({_placeholders(batch)})", batch
            )
    reader.by_ids("MASZYNA", "ID_MASZYNA", known_ids)
    machine_ids = {_id(row["ID_MASZYNA"]) for row in reader.tables["MASZYNA"].values()}
    for table in ("CPC", "UMOWA", "SERIAL", "ZLECENIE", "ZPOZYCJA", "FPOZYCJA", "FAKTURA"):
        reader.by_ids(table, "ID_MASZYNA", machine_ids)
    order_pairs = {_order_key(row) for row in reader.tables["ZLECENIE"].values()}
    reader.by_pairs("ZLECENIE", ("ID_ZLECENIE", "ROK"), order_pairs)
    reader.by_pairs("ZPOZYCJA", ("ID_ZLECENIE", "ROK"), order_pairs)
    reader.by_pairs(
        "ZAKUPY", ("ID_ZLECENIE", "ROK_ZLECENIA"), order_pairs, "t.RODZAJ_DOK IN ('RW','WZ')"
    )
    reader.by_ids(
        "ZAKUPY",
        "ID_ZAKUPY_TABLE",
        (
            _id(row.get(field))
            for row in reader.tables["ZLECENIE"].values()
            for field in ("ID_RW", "ID_WZ")
        ),
    )
    reader.by_pairs("FAKTURA", ("ID_ZLECENIE", "ROK_ZLECENIA"), order_pairs)
    reader.by_ids(
        "FAKTURA",
        "ID_FAKTURA_TABLE",
        (
            _id(row.get("ID_FAKTURA"))
            for table in ("CPC", "ZLECENIE", "FPOZYCJA")
            for row in reader.tables[table].values()
        ),
    )
    reader.by_ids(
        "FAKTURA",
        "ID_UMOWACPC",
        (
            _id(row.get("ID_UMOWACPC"))
            for table in ("MASZYNA", "CPC")
            for row in reader.tables[table].values()
        ),
    )
    reader.by_ids("ZAKPOZYCJA", "ID_ZAKUPY", reader.tables["ZAKUPY"])
    document_numbers = {
        (_text(row.get("NUMER")), _id(row.get("ID_FIRMA")))
        for row in reader.tables["ZAKUPY"].values()
    }
    reader.by_pairs("ZAKPOZYCJA", ("NUMER", "ID_FIRMA"), document_numbers)
    reader.by_pairs("ZAKUPY", ("NUMER", "ID_FIRMA"), document_numbers)
    reader.by_ids(
        "ZAKUPY",
        "ID_ZAKUPY_TABLE",
        (_id(row.get("ID_ZAKUPY")) for row in reader.tables["ZAKPOZYCJA"].values()),
    )
    pending = set(reader.tables["FAKTURA"])
    visited = set()
    while pending:
        rows = [reader.tables["FAKTURA"][identifier] for identifier in pending]
        reader.by_pairs(
            "FAKTURA",
            ("ID_FIRMA", "NR_KORY"),
            ((row.get("ID_FIRMA"), _text(row.get("NUMER"))) for row in rows),
            "t.RODZAJ_DOK='KOR'",
        )
        reader.by_pairs(
            "FAKTURA",
            ("ID_FIRMA", "NUMER"),
            ((row.get("ID_FIRMA"), _text(row.get("NR_KORY"))) for row in rows),
        )
        visited.update(pending)
        pending = set(reader.tables["FAKTURA"]) - visited
    reader.by_ids("FPOZYCJA", "ID_FAKTURA", reader.tables["FAKTURA"])
    reader.by_ids("CPC", "ID_FAKTURA", reader.tables["FAKTURA"])
    reader.by_pairs(
        "ZLECENIE",
        ("ID_ZLECENIE", "ROK"),
        (
            _order_key(row, "ROK_ZLECENIA")
            for table in ("FAKTURA", "FPOZYCJA")
            for row in reader.tables[table].values()
        ),
    )
    contract_ids = {
        _id(row.get("ID_UMOWACPC"))
        for table in ("MASZYNA", "CPC", "FAKTURA")
        for row in reader.tables[table].values()
    }
    reader.by_ids("UMOWACPC", "ID_UMOWACPC_TABLE", contract_ids)
    reader.by_ids(
        "MODEL", "ID_MODEL", (_id(row.get("ID_MODEL")) for row in reader.tables["MASZYNA"].values())
    )
    reader.by_ids(
        "KLIENT",
        "ID_KLIENT",
        (_id(row.get("ID_KLIENT")) for row in reader.tables["MASZYNA"].values()),
    )
    reader.by_ids(
        "MAGAZYN",
        "ID_MAGAZYN_TABLE",
        (_id(row.get("ID_MAGPOZ")) for row in reader.tables["SERIAL"].values()),
    )
    return {table: list(records.values()) for table, records in reader.tables.items()}


def _payload(title, *, raw=None, **values):
    """Buduje wspólny kontrakt projekcji z oddzielną częścią wewnętrzną administratora."""
    return {
        "schema_version": 1,
        "source": "ms",
        "status": "confirmed",
        "title": title,
        "issues": [],
        "contract_id": None,
        "customer_id": None,
        "is_contract": None,
        **values,
        "raw": {table: [_safe(row) for row in rows] for table, rows in (raw or {}).items()},
    }


def _problem(data, code, status="missing"):
    """Zachowuje wszystkie problemy, przyznając konfliktowi pierwszeństwo przed brakiem."""
    if code not in data["issues"]:
        data["issues"].append(code)
    if data["status"] != "conflict":
        data["status"] = status


def _fact(key, machine_id, kind, data, observed=None, precision=None):
    """Nadaje faktowi czas źródła bez tworzenia fikcyjnej daty importu."""
    observed_at = None
    if isinstance(observed, datetime):
        observed_at, precision = observed.isoformat(), "second"
    elif _day(observed) is not None:
        observed_at, precision = _day(observed).isoformat(), "date"
    if observed_at is None and precision != "month":
        _problem(data, "missing_observed_at")
    if "purchase_cost" in data and data["status"] != "confirmed":
        data["purchase_value"] = None
        data["zero_cost_confirmed"] = False
        data["purchase_cost"].update(status=data["status"], net=None)
    if "issue_purchase_cost" in data:
        if data["status"] != "confirmed":
            data["issue_purchase_cost"].update(
                status="conflict" if data["status"] == "conflict" else "unknown", amount=None
            )
        data["issue_purchase_cost"]["issues"] = list(data["issues"])
    if "net_value" in data:
        data["allocation_status"] = data["status"]
        if data["status"] != "confirmed":
            data["net_value"] = None
    return {
        "external_key": key,
        "device_id": f"ms:{machine_id}",
        "kind": kind,
        "observed_at": observed_at,
        "time_precision": precision or "unknown",
        "title": data["title"],
        "status": data["status"],
        "data": data,
    }


def _period(row):
    """Zwraca pełny okres CPC bez traktowania początku miesiąca jako pomiaru."""
    year, month = row.get("ROK"), row.get("MIESIAC")
    try:
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
    except (ValueError, TypeError):
        start = end = None
    return {
        "year": year,
        "month": month,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
    }


def _in_history(observed, first):
    """Zachowuje także fakty bez daty, których nie można bezpiecznie odfiltrować."""
    return first is None or _day(observed) is None or _day(observed) >= first


def _order_key(row, year="ROK"):
    """Łączy numer zlecenia z rokiem, nie z technicznym ID nagłówka."""
    return _id(row.get("ID_ZLECENIE")), _id(row.get(year))


class _Normalizer:
    """Uzgadnia relacje pamięciowej migawki bez dodatkowych odczytów źródła."""

    def __init__(self, tables):
        """Buduje indeksy wielowartościowe zamiast arbitralnego wyboru pierwszego rekordu."""
        self.tables = tables
        self.machines = _groups(tables["MASZYNA"], lambda row: _id(row.get("ID_MASZYNA")))
        self.contracts = {row["ID_UMOWACPC_TABLE"]: row for row in tables["UMOWACPC"]}
        self.cpc = _groups(tables["CPC"], lambda row: _id(row.get("ID_MASZYNA")))
        self.machine_contracts = {
            machine_id: frozenset(
                identifier
                for row in rows + self.cpc[machine_id]
                if (identifier := _id(row.get("ID_UMOWACPC"))) is not None
            )
            for machine_id, rows in self.machines.items()
        }
        self.contract_machines = defaultdict(set)
        for machine_id, contract_ids in self.machine_contracts.items():
            for contract_id in contract_ids:
                self.contract_machines[contract_id].add(machine_id)
        self.orders = _groups(tables["ZLECENIE"], _order_key)
        self.parts = _groups(tables["ZPOZYCJA"], _order_key)
        self.documents = {row["ID_ZAKUPY_TABLE"]: row for row in tables["ZAKUPY"]}
        self.documents_by_order = _groups(
            tables["ZAKUPY"], lambda row: _order_key(row, "ROK_ZLECENIA")
        )
        self.document_references = defaultdict(list)
        self.invoice_references = defaultdict(list)
        for order in tables["ZLECENIE"]:
            for field in ("ID_RW", "ID_WZ"):
                if identifier := _id(order.get(field)):
                    self.document_references[identifier].append(order)
            if identifier := _id(order.get("ID_FAKTURA")):
                self.invoice_references[identifier].append(order)
        self.stock_lines = defaultdict(list)
        self.stock_line_links = {}
        numbered_documents = _groups(
            tables["ZAKUPY"], lambda row: (_text(row.get("NUMER")), _id(row.get("ID_FIRMA")))
        )
        for line in tables["ZAKPOZYCJA"]:
            direct_id = _id(line.get("ID_ZAKUPY"))
            direct = self.documents.get(direct_id)
            number, company = _text(line.get("NUMER")), _id(line.get("ID_FIRMA"))
            numbered = numbered_documents[(number, company)] if number and company else []
            targets = {row["ID_ZAKUPY_TABLE"] for row in numbered}
            if direct:
                targets.add(direct_id)
            issues = []
            if len(numbered) > 1:
                issues.append("ambiguous_document_number_company")
            if direct_id and not direct:
                issues.append("dangling_document_line_pointer")
            if direct and numbered and any(row["ID_ZAKUPY_TABLE"] != direct_id for row in numbered):
                issues.append("conflicting_document_line_keys")
            if direct and number and number != _text(direct.get("NUMER")):
                issues.append("conflicting_document_line_number")
            if direct and company and company != _id(direct.get("ID_FIRMA")):
                issues.append("conflicting_document_line_company")
            for identifier in targets:
                self.stock_lines[identifier].append(line)
                self.stock_line_links[(identifier, line["ID_ZAKPOZYCJA_TABLE"])] = {
                    "basis": "ID_ZAKUPY" if direct_id == identifier else "NUMER+ID_FIRMA",
                    "issues": issues,
                    "document_ids": sorted(targets),
                }
        self.invoice_lines = _groups(tables["FPOZYCJA"], lambda row: row.get("ID_FAKTURA"))
        self.invoice_cpc = _groups(tables["CPC"], lambda row: row.get("ID_FAKTURA"))
        self.invoices_by_number = _groups(
            tables["FAKTURA"], lambda row: (row.get("ID_FIRMA"), _text(row.get("NUMER")))
        )
        self.first = {}

    def contract_ids(self, machine_id):
        """Zwraca niezmienny indeks umów zbudowany raz dla bieżącej migawki."""
        return self.machine_contracts.get(machine_id, frozenset())

    def context(self, machine_id, observed, customer_id, service_type=None):
        """Wiąże koszt z okresem urządzenia, nie wyłącznie z aktualną umową klienta."""
        day = _day(observed)
        candidates = set()
        if day:
            candidates.update(
                _id(row.get("ID_UMOWACPC"))
                for row in self.cpc[machine_id]
                if (row.get("ROK"), row.get("MIESIAC")) == (day.year, day.month)
                and _id(row.get("ID_KLIENT")) == customer_id
            )
            for row in self.machines[machine_id]:
                start, end = _day(row.get("UM_P")), _day(row.get("UM_K"))
                if (
                    start
                    and start <= day
                    and (end is None or day <= end)
                    and _id(row.get("ID_KLIENT")) == customer_id
                ):
                    candidates.add(_id(row.get("ID_UMOWACPC")))
        candidates.discard(None)
        kind = _text(service_type).casefold()
        is_contract = True if "umowa" in kind or candidates else None
        if kind and "umowa" not in kind:
            is_contract = False
        contract_id = next(iter(candidates)) if len(candidates) == 1 else None
        return {
            "contract_id": contract_id,
            "customer_id": customer_id,
            "is_contract": is_contract,
            "contract_candidates": sorted(candidates),
        }

    def check_context(self, data):
        """Oznacza konflikty identyfikacji umowy niezależnie od poprawności kwot."""
        if len(data.get("contract_candidates", [])) > 1:
            _problem(data, "conflicting_contract_assignment", "conflict")
        elif data["is_contract"] is True and data["contract_id"] not in self.contracts:
            _problem(data, "missing_historical_contract")
        elif data["is_contract"] is None:
            _problem(data, "missing_contract_assignment")

    def devices(self, known_ids):
        """Zachowuje znane urządzenia oraz jawnie konfigurowaną klasyfikację złomowania."""
        result = []
        models = {row["ID_MODEL"]: row for row in self.tables["MODEL"]}
        customers = {row["ID_KLIENT"]: row for row in self.tables["KLIENT"]}
        serials = _groups(self.tables["SERIAL"], lambda row: _id(row.get("ID_MASZYNA")))
        warehouses = {row["ID_MAGAZYN_TABLE"]: row for row in self.tables["MAGAZYN"]}
        scrap_customers = set(getattr(settings, "shipping_orbit_scrap_customer_ids", []))
        scrap_warehouses = set(getattr(settings, "shipping_orbit_scrap_warehouse_ids", []))
        for machine_id in sorted((set(self.machines) | known_ids) - {None}):
            rows = self.machines[machine_id]
            row = rows[0] if len(rows) == 1 else {}
            customer_id, contract_id = _id(row.get("ID_KLIENT")), _id(row.get("ID_UMOWACPC"))
            contract = self.contracts.get(contract_id, {})
            model_id = _id(row.get("ID_MODEL"))
            model = models.get(model_id, {})
            customer = customers.get(customer_id, {})
            current_warehouses = set()
            for serial in serials[machine_id]:
                item = warehouses.get(_id(serial.get("ID_MAGPOZ")), {})
                if (
                    _text(serial.get("SERIAL")) == _text(row.get("SERIAL"))
                    and _text(row.get("SERIAL"))
                    and serial.get("DATA_SPRZ") is None
                    and not any(
                        _id(serial.get(field)) for field in ("ID_WZ", "ID_RW", "ID_FAKTURA")
                    )
                    and (_decimal(item.get("ILOSC")) or Decimal(0)) > 0
                    and _id(serial.get("ID_MAGAZYN")) == _id(item.get("ID_MAGAZYN"))
                ):
                    current_warehouses.add(_id(item.get("ID_MAGAZYN")))
            current_warehouses.discard(None)
            dates = [
                _day(self.contracts.get(identifier, {}).get("U_START"))
                for identifier in self.contract_ids(machine_id)
            ]
            dates.extend(_day(_period(period)["start"]) for period in self.cpc[machine_id])
            dates.append(_day(row.get("UM_P")))
            dates.extend(
                _day(item.get("DATA_START"))
                for item in self.tables["UMOWA"]
                if _id(item.get("ID_MASZYNA")) == machine_id
            )
            first = min((value for value in dates if value), default=None)
            self.first[machine_id] = first
            status = "active" if _text(contract.get("AKTYWNA")) == "TAK" else "suspended"
            data = _payload(
                "Kartoteka urządzenia MS",
                raw={
                    "MASZYNA": rows,
                    "SERIAL": serials[machine_id],
                    "MODEL": [model] if model else [],
                    "KLIENT": [customer] if customer else [],
                    "MAGAZYN": [
                        warehouses[identifier]
                        for serial in serials[machine_id]
                        if (identifier := _id(serial.get("ID_MAGPOZ"))) in warehouses
                    ],
                },
                contract_id=contract_id,
                customer_id=customer_id,
                is_contract=bool(contract_id),
                first_contract_at=first.isoformat() if first else None,
                current_customer={
                    "status": "proposed",
                    "id": customer_id,
                    "name": customer.get("NAZWA"),
                },
                currentwarehouse={
                    "status": "proposed" if len(current_warehouses) == 1 else "missing",
                    "ids": sorted(current_warehouses),
                    "basis": "SERIAL.ID_MAGPOZ/MAGAZYN.ID_MAGAZYN",
                },
            )
            if not rows:
                _problem(data, "missing_known_machine")
                status = "review"
            elif len(rows) != 1:
                _problem(data, "duplicate_machine_identity", "conflict")
                status = "review"
            elif contract_id and not contract:
                _problem(data, "missing_current_contract")
                status = "review"
            elif status == "active" and _text(row.get("AKTYWNA")) != "TAK":
                _problem(data, "conflicting_active_flags", "conflict")
                status = "review"
            if len(current_warehouses) > 1:
                _problem(data, "conflicting_warehouse_proposals", "conflict")
                status = "review"
            if customer_id in scrap_customers or (
                len(current_warehouses) == 1 and bool(current_warehouses & scrap_warehouses)
            ):
                if status == "active":
                    _problem(data, "active_contract_in_scrap_location", "conflict")
                    status = "review"
                else:
                    status = "scrapped"
            if not first:
                _problem(data, "missing_first_contract_date")
            result.append(
                {
                    "id": f"ms:{machine_id}",
                    "ms_machine_id": machine_id,
                    "serial": _text(row.get("SERIAL")) or None,
                    "model": _text(model.get("MODEL") or row.get("MODEL")) or None,
                    "model_id": model_id,
                    "customer": _text(customer.get("NAZWA")) or None,
                    "customer_id": customer_id,
                    "contract_id": contract_id,
                    "status": status,
                    "data": data,
                }
            )
        return result

    def contract_facts(self, machine_id):
        """Zachowuje kompletne nagłówki historycznych umów CPC i umów pozostałych."""
        result = []
        for identifier in sorted(self.contract_ids(machine_id)):
            row = self.contracts.get(identifier, {})
            periods = [
                item for item in self.cpc[machine_id] if item.get("ID_UMOWACPC") == identifier
            ]
            data = _payload(
                _text(row.get("UMOWA")) or f"Umowa CPC {identifier}",
                raw={"UMOWACPC": [row] if row else []},
                contract_id=identifier,
                customer_id=_id(row.get("ID_KLIENT")),
                is_contract=True,
                contract_source="UMOWACPC",
                contract_number=_text(row.get("UMOWA")) or None,
                starts_at=(
                    _day(row.get("U_START")).isoformat() if _day(row.get("U_START")) else None
                ),
                ends_at=_day(row.get("U_STOP")).isoformat() if _day(row.get("U_STOP")) else None,
                cpc_ids=[item["ID_CPC_TABLE"] for item in periods],
            )
            if not row:
                _problem(data, "missing_contract_record")
            result.append(
                _fact(
                    f"UMOWACPC:{identifier}:MASZYNA:{machine_id}",
                    machine_id,
                    "contract",
                    data,
                    row.get("U_START"),
                )
            )
        for row in self.tables["UMOWA"]:
            if _id(row.get("ID_MASZYNA")) != machine_id:
                continue
            data = _payload(
                _text(row.get("NUMER")) or f"Umowa {row['ID_UMOWA']}",
                raw={"UMOWA": [row]},
                customer_id=_id(row.get("ID_KLIENT")),
                is_contract=True,
                contract_source="UMOWA",
                other_contract_id=row["ID_UMOWA"],
            )
            result.append(
                _fact(
                    f"UMOWA:{row['ID_UMOWA']}",
                    machine_id,
                    "contract",
                    data,
                    row.get("DATA_START") or row.get("DATA"),
                )
            )
        return result

    def cpc_facts(self, machine_id):
        """Udostępnia wszystkie okresy oraz oryginał do istniejącego namespace ms_cpc."""
        result = []
        duplicates = _groups(
            self.cpc[machine_id],
            lambda row: (row.get("ID_UMOWACPC"), row.get("ROK"), row.get("MIESIAC")),
        )
        for row in self.cpc[machine_id]:
            period = _period(row)
            data = _payload(
                f"Rozliczenie CPC {row.get('ROK')}/{row.get('MIESIAC')}",
                raw={"CPC": [row]},
                contract_id=_id(row.get("ID_UMOWACPC")),
                customer_id=_id(row.get("ID_KLIENT")),
                is_contract=True,
                invoice_id=_id(row.get("ID_FAKTURA")),
                period=period,
                counters={
                    key: value
                    for key, value in row.items()
                    if key.startswith(("LICZNIK_", "KOPIE_", "STRONY_", "SKANY"))
                },
                charges={
                    key: _amount(value)
                    for key, value in row.items()
                    if key.startswith("OPLATA_") or key == "DOPLATA"
                },
            )
            if period["start"] is None:
                _problem(data, "invalid_cpc_period")
            if data["contract_id"] not in self.contracts:
                _problem(data, "missing_historical_contract")
            if len(duplicates[(row.get("ID_UMOWACPC"), row.get("ROK"), row.get("MIESIAC"))]) > 1:
                _problem(data, "duplicate_cpc_period", "conflict")
            result.append(
                _fact(f"CPC:{row['ID_CPC_TABLE']}", machine_id, "cpc", data, precision="month")
            )
        return result

    def service_facts(self):
        """Przenosi zlecenia i plany części, oddzielając je od potwierdzonych rozchodów."""
        result = []
        for row in self.tables["ZLECENIE"]:
            machine_id = _id(row.get("ID_MASZYNA"))
            if machine_id not in self.first or not _in_history(
                row.get("DATA"), self.first[machine_id]
            ):
                continue
            key = _order_key(row)
            parts = self.parts[key] if all(key) else []
            data = _payload(
                f"Zlecenie {row.get('ID_ZLECENIE')}/{row.get('ROK')}",
                raw={"ZLECENIE": [row], "ZPOZYCJA": parts},
                **self.context(
                    machine_id, row.get("DATA"), _id(row.get("ID_KLIENT")), row.get("RODZAJ_US")
                ),
                order_id=key[0],
                order_year=key[1],
                order_table_id=row["ID_ZLECENIE_TABLE"],
                service_type=_text(row.get("RODZAJ_US")) or None,
                source_state=_text(row.get("STAN")) or None,
                problem=_text(row.get("PROBLEM")) or None,
                work_done=_text(row.get("WYKONANIE")) or None,
                parts=[
                    {
                        "line_id": part["ID_ZPOZYCJA_TABLE"],
                        "item_id": _id(part.get("ID_MAGPOZ")),
                        "name": _text(part.get("NAZWA")),
                        "planned_quantity": _amount(part.get("ILOSC")),
                        "source_taken_quantity": _amount(part.get("POBRANO")),
                    }
                    for part in parts
                ],
            )
            self.check_context(data)
            if not all(key):
                _problem(data, "missing_order_year")
            elif len(self.orders[key]) != 1:
                _problem(data, "duplicate_order_number_year", "conflict")
            result.append(
                _fact(
                    f"ZLECENIE:{row['ID_ZLECENIE_TABLE']}",
                    machine_id,
                    "service",
                    data,
                    row.get("DATA"),
                )
            )
            for field, date_field in (
                ("PRZESYLKA", "DATA_PRZES"),
                ("PRZESYLKA_WE", "DATA_PRZES_WE"),
            ):
                if not _text(row.get(field)):
                    continue
                shipment = _payload(
                    "Przesyłka " + _text(row[field]),
                    raw={"ZLECENIE": [row]},
                    **self.context(
                        machine_id,
                        row.get(date_field),
                        _id(row.get("ID_KLIENT")),
                        row.get("RODZAJ_US"),
                    ),
                    order_id=key[0],
                    order_year=key[1],
                    order_table_id=row["ID_ZLECENIE_TABLE"],
                    tracking_number=_text(row[field]),
                    direction="outbound" if field == "PRZESYLKA" else "inbound",
                    delivery_status=None,
                )
                self.check_context(shipment)
                result.append(
                    _fact(
                        f"ZLECENIE:{row['ID_ZLECENIE_TABLE']}:{field}",
                        machine_id,
                        "shipment",
                        shipment,
                        row.get(date_field),
                    )
                )
        return result

    def document_orders(self, document):
        """Uzgadnia parę numer/rok z bezpośrednimi wskazaniami RW/WZ nagłówka zlecenia."""
        key = _order_key(document, "ROK_ZLECENIA")
        linked = {row["ID_ZLECENIE_TABLE"]: row for row in self.orders[key]} if all(key) else {}
        identifier = document["ID_ZAKUPY_TABLE"]
        for row in self.document_references[identifier]:
            linked[row["ID_ZLECENIE_TABLE"]] = row
        return list(linked.values())

    def issue_cost(self, data, line, parts, order):
        """Oddziela uzgodnioną wartość wydania od niepotwierdzonego bilansu po zwrotach."""
        data.update(
            purchase_price=None,
            document_purchase_price=_amount(line.get("CENA_Z")),
            issued_quantity=None,
            returned_quantity=None,
            purchase_value=None,
            zero_cost_confirmed=False,
            currency=None,
            currency_status="unverified",
            returns_status="unverified",
            returned_quantity_basis=None,
            issued_quantity_basis=None,
            quantity_reconciliation={"status": "unknown", "issues": []},
            issue_purchase_cost={
                "status": "unknown",
                "amount": None,
                "quantity": None,
                "unit_price": None,
                "currency": None,
                "currency_status": "unverified",
                "currency_scope": (
                    f"ms:company:{data['ms_company_id']}" if data.get("ms_company_id") else None
                ),
                "scope": "before_returns",
                "returns_status": "unverified",
                "basis": "ZPOZYCJA.CENA_Z/WARTOSC_Z + ZAKPOZYCJA.CENA_Z/WARTOSC_Z",
                "issues": [],
            },
            purchase_cost={
                "status": "missing",
                "net": None,
                "unit_net": None,
                "quantity": None,
                "scope": "before_returns",
                "basis": "ZPOZYCJA.CENA_Z/WARTOSC_Z + ZAKPOZYCJA.CENA_Z/WARTOSC_Z",
            },
        )
        quantity, taken = _decimal(line.get("ILOSC")), _decimal(line.get("POBRANO"))
        if quantity is None or taken is None:
            _problem(data, "missing_realized_quantity")
        elif quantity < 0 or taken < 0:
            _problem(data, "ambiguous_return_quantity", "conflict")
        elif quantity == 0 or taken == 0:
            _problem(data, "missing_material_realization")
        elif quantity != taken:
            _problem(data, "conflicting_document_realization", "conflict")
        else:
            data["issued_quantity"] = _amount(taken)
            data["issued_quantity_basis"] = "ZAKPOZYCJA.POBRANO"
        if len(parts) != 1:
            _problem(
                data,
                "ambiguous_order_part" if parts else "missing_order_part",
                "conflict" if parts else "missing",
            )
            return
        part = parts[0]
        data["order_line_id"] = part["ID_ZPOZYCJA_TABLE"]
        data["purchase_price"] = _amount(part.get("CENA_Z"))
        price, value = _decimal(part.get("CENA_Z")), _decimal(part.get("WARTOSC_Z"))
        planned, part_taken = _decimal(part.get("ILOSC")), _decimal(part.get("POBRANO"))
        document_price, document_value = _decimal(line.get("CENA_Z")), _decimal(
            line.get("WARTOSC_Z")
        )
        if any(
            amount is None for amount in (price, value, planned, document_price, document_value)
        ):
            _problem(data, "missing_purchase_evidence")
            return
        if min(price, value, planned, document_price, document_value) < 0:
            _problem(data, "ambiguous_negative_purchase", "conflict")
        if price != document_price:
            _problem(data, "conflicting_purchase_price", "conflict")
        if price == document_price == 0:
            _problem(data, "unconfirmed_zero_purchase_cost")
        if (price * planned).quantize(Decimal("0.0001")) != value:
            _problem(data, "conflicting_order_purchase_value", "conflict")
        if (
            quantity is not None
            and (document_price * quantity).quantize(Decimal("0.0001")) != document_value
        ):
            _problem(data, "conflicting_document_purchase_value", "conflict")
        related = []
        for document in self.documents_by_order[_order_key(order)]:
            related.extend(
                item
                for item in self.stock_lines[document["ID_ZAKUPY_TABLE"]]
                if _id(item.get("ID_MAGAZYN")) == _id(part.get("ID_MAGPOZ"))
            )
        amounts = [_decimal(item.get("POBRANO")) for item in related]
        if part_taken is None:
            data["quantity_reconciliation"]["issues"].append("missing_order_taken_quantity")
        elif not amounts or any(amount is None or amount < 0 for amount in amounts):
            _problem(data, "missing_total_realization")
        elif sum(amounts) != part_taken or part_taken > planned:
            _problem(data, "conflicting_total_realization", "conflict")
        else:
            data["quantity_reconciliation"]["status"] = "confirmed"
        if _id(part.get("ID_MASZYNA")) != _id(order.get("ID_MASZYNA")):
            _problem(data, "conflicting_part_machine", "conflict")
        if _id(part.get("ID_KLIENT")) != _id(order.get("ID_KLIENT")):
            _problem(data, "conflicting_part_customer", "conflict")
        if (
            _id(line.get("ID_SERIAL"))
            and _id(part.get("ID_SERIAL"))
            and line["ID_SERIAL"] != part["ID_SERIAL"]
        ):
            _problem(data, "conflicting_part_serial", "conflict")
        if data["status"] == "confirmed":
            data["purchase_value"] = _amount(document_value)
            data["zero_cost_confirmed"] = price == document_price == value == document_value == 0
            data["purchase_cost"].update(
                status="confirmed",
                net=_amount(document_value),
                unit_net=_amount(price),
                quantity=data["issued_quantity"],
            )
            data["issue_purchase_cost"].update(
                status="known",
                amount=_amount(document_value),
                unit_price=_amount(price),
                quantity=data["issued_quantity"],
            )

    def material_facts(self):
        """Rozdziela potwierdzone linie RW/WZ od planowanych części i kosztów sprzedaży."""
        result = []
        for document in self.tables["ZAKUPY"]:
            orders = self.document_orders(document)
            machine_ids = {_id(row.get("ID_MASZYNA")) for row in orders} & self.first.keys()
            for machine_id in sorted(machine_ids):
                order = orders[0] if len(orders) == 1 else {}
                observed = document.get("DATA_PRZY_WYDA") or document.get("DATA_WYST")
                if not _in_history(observed, self.first[machine_id]):
                    continue
                lines = self.stock_lines[document["ID_ZAKUPY_TABLE"]]
                for line in lines or [{}]:
                    part_rows = [
                        part
                        for part in self.parts[_order_key(order)]
                        if _id(line.get("ID_MAGAZYN")) is not None
                        and _id(part.get("ID_MAGPOZ")) == _id(line.get("ID_MAGAZYN"))
                    ]
                    data = _payload(
                        _text(document.get("NUMER"))
                        or f"{document.get('RODZAJ_DOK')} {document['ID_ZAKUPY_TABLE']}",
                        raw={
                            "ZAKUPY": [document],
                            "ZAKPOZYCJA": [line] if line else [],
                            "ZPOZYCJA": part_rows,
                        },
                        **self.context(
                            machine_id,
                            observed,
                            _id(document.get("ID_KLIENT")),
                            order.get("RODZAJ_US"),
                        ),
                        order_id=_order_key(document, "ROK_ZLECENIA")[0],
                        order_year=_order_key(document, "ROK_ZLECENIA")[1],
                        order_table_id=order.get("ID_ZLECENIE_TABLE"),
                        document_id=document["ID_ZAKUPY_TABLE"],
                        ms_company_id=_id(document.get("ID_FIRMA")),
                        document_line_id=line.get("ID_ZAKPOZYCJA_TABLE"),
                        document_kind=_text(document.get("RODZAJ_DOK")),
                        item_id=_id(line.get("ID_MAGAZYN")),
                        name=_text(line.get("NAZWA")) or None,
                        planned_quantity=(
                            _amount(part_rows[0].get("ILOSC")) if len(part_rows) == 1 else None
                        ),
                        source_document_quantity=_amount(line.get("ILOSC")),
                        source_taken_quantity=_amount(line.get("POBRANO")),
                    )
                    self.check_context(data)
                    data["role"] = (
                        "contract"
                        if data["is_contract"] is True
                        else "non_contract" if data["is_contract"] is False else None
                    )
                    if line:
                        link = self.stock_line_links[
                            (document["ID_ZAKUPY_TABLE"], line["ID_ZAKPOZYCJA_TABLE"])
                        ]
                        data["document_link_basis"] = link["basis"]
                        data["document_candidate_ids"] = link["document_ids"]
                        for issue in link["issues"]:
                            _problem(data, issue, "conflict")
                        if (
                            line.get("DATA_PRZY_WYDA") is not None
                            and document.get("DATA_PRZY_WYDA") is not None
                            and _day(line["DATA_PRZY_WYDA"]) != _day(document["DATA_PRZY_WYDA"])
                        ):
                            _problem(data, "conflicting_document_line_date", "conflict")
                    if len(orders) != 1:
                        _problem(data, "ambiguous_document_order", "conflict")
                    elif _order_key(order) != _order_key(document, "ROK_ZLECENIA"):
                        _problem(data, "conflicting_document_order", "conflict")
                    if order and _id(order.get("ID_KLIENT")) != _id(document.get("ID_KLIENT")):
                        _problem(data, "conflicting_document_customer", "conflict")
                    if not line:
                        _problem(data, "missing_document_lines")
                    elif _text(line.get("RODZAJ_DOK")) != _text(document.get("RODZAJ_DOK")):
                        _problem(data, "conflicting_document_kind", "conflict")
                    if line and _id(line.get("ID_KLIENT")) is None:
                        _problem(data, "missing_line_customer")
                    elif line and _id(line.get("ID_KLIENT")) != _id(document.get("ID_KLIENT")):
                        _problem(data, "conflicting_line_customer", "conflict")
                    if _text(document.get("RODZAJ_DOK")) not in {"RW", "WZ"}:
                        _problem(data, "invalid_issue_document_kind", "conflict")
                    if data["is_contract"] is True:
                        self.issue_cost(data, line, part_rows, order)
                    key = (
                        f"ZAKPOZYCJA:{line['ID_ZAKPOZYCJA_TABLE']}"
                        if line
                        else f"ZAKUPY:{document['ID_ZAKUPY_TABLE']}:missing"
                    )
                    if len(data.get("document_candidate_ids", [])) > 1:
                        key += f":ZAKUPY:{document['ID_ZAKUPY_TABLE']}"
                    if len(machine_ids) > 1:
                        key += f":MASZYNA:{machine_id}"
                    result.append(_fact(key, machine_id, "material_issue", data, observed))
        return result

    def line_owner(self, line):
        """Przypisuje linię faktury po ID maszyny albo jednoznacznej parze zlecenia."""
        direct = _id(line.get("ID_MASZYNA"))
        key = _order_key(line, "ROK_ZLECENIA")
        orders = self.orders[key] if all(key) else []
        owners = {_id(row.get("ID_MASZYNA")) for row in orders} - {None}
        if len(orders) > 1 or (direct and owners and owners != {direct}):
            return None, "conflict"
        if direct:
            return direct, "confirmed"
        if len(orders) == 1 and len(owners) == 1:
            return next(iter(owners)), "confirmed"
        return None, "missing"

    def invoice_candidates(self, invoice):
        """Buduje kandydatów dokumentu, nie rozdzielając między nich kwot nagłówka."""
        identifier = invoice["ID_FAKTURA_TABLE"]
        result = {_id(invoice.get("ID_MASZYNA"))}
        result.update(_id(row.get("ID_MASZYNA")) for row in self.invoice_lines[identifier])
        result.update(_id(row.get("ID_MASZYNA")) for row in self.invoice_cpc[identifier])
        result.update(self.line_owner(row)[0] for row in self.invoice_lines[identifier])
        key = _order_key(invoice, "ROK_ZLECENIA")
        if all(key):
            result.update(_id(row.get("ID_MASZYNA")) for row in self.orders[key])
        result.update(_id(row.get("ID_MASZYNA")) for row in self.invoice_references[identifier])
        if contract_id := _id(invoice.get("ID_UMOWACPC")):
            result.update(self.contract_machines[contract_id] & self.first.keys())
        result.discard(None)
        return result

    def invoice_facts(self):
        """Przenosi przypisane linie; niejednoznaczne korekty nigdy nie dają kwoty netto."""
        result = []
        for invoice in self.tables["FAKTURA"]:
            identifier = invoice["ID_FAKTURA_TABLE"]
            correction = _text(invoice.get("RODZAJ_DOK")).upper() == "KOR" or bool(
                _text(invoice.get("NR_KORY"))
            )
            originals = (
                self.invoices_by_number[(invoice.get("ID_FIRMA"), _text(invoice.get("NR_KORY")))]
                if correction
                else []
            )
            candidates = self.invoice_candidates(invoice)
            for original in originals:
                candidates.update(self.invoice_candidates(original))
            lines = self.invoice_lines[identifier]
            ownership = [(line, *self.line_owner(line)) for line in lines]
            for machine_id in sorted(candidates & self.first.keys()):
                if not _in_history(invoice.get("DATA_WYST"), self.first[machine_id]):
                    continue
                assigned = [
                    line
                    for line, owner, status in ownership
                    if owner == machine_id and status == "confirmed"
                ]
                unresolved = [(line, status) for line, owner, status in ownership if owner is None]
                linked_cpc = [
                    row
                    for row in self.invoice_cpc[identifier]
                    if _id(row.get("ID_MASZYNA")) == machine_id
                ]
                contracts = {_id(row.get("ID_UMOWACPC")) for row in linked_cpc} - {None}
                context = self.context(
                    machine_id, invoice.get("DATA_WYST"), _id(invoice.get("ID_KLIENT"))
                )
                if contracts:
                    context.update(
                        contract_id=next(iter(contracts)) if len(contracts) == 1 else None,
                        contract_candidates=sorted(contracts),
                        is_contract=True,
                    )
                data = _payload(
                    _text(invoice.get("NUMER")) or f"Faktura {identifier}",
                    raw={
                        "FAKTURA": [invoice],
                        "FPOZYCJA": assigned + [line for line, _ in unresolved],
                    },
                    **context,
                    invoice_id=identifier,
                    document_kind=_text(invoice.get("RODZAJ_DOK")),
                    currency=_text(invoice.get("CCODE")) or None,
                    net_value=None,
                    assigned_net_value=None,
                    unassigned_line_ids=[line["ID_FPOZYCJA_TABLE"] for line, _ in unresolved],
                    lines=[
                        {
                            "invoice_line_id": line["ID_FPOZYCJA_TABLE"],
                            "name": _text(line.get("NAZWA")),
                            "quantity": _amount(line.get("ILOSC")),
                            "unit_net": _amount(line.get("CENA_NETTO")),
                            "net_value": _amount(line.get("WARTOSC_NETTO")),
                        }
                        for line in assigned
                    ],
                    cpc_ids=[row["ID_CPC_TABLE"] for row in linked_cpc],
                )
                self.check_context(data)
                if not lines or not assigned:
                    _problem(data, "missing_invoice_line_allocation")
                for _, status in unresolved:
                    _problem(
                        data,
                        (
                            "conflicting_invoice_line_allocation"
                            if status == "conflict"
                            else "missing_invoice_line_allocation"
                        ),
                        status,
                    )
                values = [_decimal(line.get("WARTOSC_NETTO")) for line in assigned]
                if assigned and all(value is not None for value in values):
                    data["assigned_net_value"] = _amount(sum(values))
                    if data["status"] == "confirmed":
                        data["net_value"] = data["assigned_net_value"]
                elif assigned:
                    _problem(data, "missing_invoice_line_value")
                if any(
                    _id(line.get("KOREKTA"))
                    or any(
                        line.get(field) is not None for field in ("ILOSC_KOR", "WARTOSC_NETTO_KOR")
                    )
                    for line in assigned
                ):
                    _problem(data, "ambiguous_line_correction", "conflict")
                    data["net_value"] = None
                if correction:
                    _problem(data, "ambiguous_correction_amount", "conflict")
                    data.update(
                        net_value=None,
                        assigned_net_value=None,
                        original_invoice_ids=[row["ID_FAKTURA_TABLE"] for row in originals],
                    )
                    if len(originals) != 1:
                        _problem(
                            data,
                            (
                                "ambiguous_original_invoice"
                                if originals
                                else "missing_original_invoice"
                            ),
                            "conflict" if originals else "missing",
                        )
                result.append(
                    _fact(
                        f"FAKTURA:{identifier}:MASZYNA:{machine_id}",
                        machine_id,
                        "correction" if correction else "invoice",
                        data,
                        invoice.get("DATA_WYST"),
                    )
                )
        return result


def _orphan_part_facts(normalizer):
    """Zachowuje osierocone części, których nie wolno zgubić ani uznać za wydane."""
    result = []
    for row in normalizer.tables["ZPOZYCJA"]:
        machine_id = _id(row.get("ID_MASZYNA"))
        if machine_id not in normalizer.first or normalizer.orders[_order_key(row)]:
            continue
        data = _payload(
            "Część bez jednoznacznego nagłówka zlecenia",
            raw={"ZPOZYCJA": [row]},
            customer_id=_id(row.get("ID_KLIENT")),
            order_id=_id(row.get("ID_ZLECENIE")),
            order_year=_id(row.get("ROK")),
            order_line_id=row["ID_ZPOZYCJA_TABLE"],
            planned_quantity=_amount(row.get("ILOSC")),
            issued_quantity=None,
        )
        _problem(data, "missing_order_header")
        result.append(_fact(f"ZPOZYCJA:{row['ID_ZPOZYCJA_TABLE']}", machine_id, "service", data))
    return result


def _normalize(tables, known_ids):
    """Tworzy kompletny wynik odczytu; jakość poszczególnych faktów pozostaje jawna."""
    normalizer = _Normalizer(tables)
    devices = normalizer.devices(known_ids)
    facts = []
    for device in devices:
        machine_id = device["ms_machine_id"]
        facts.extend(normalizer.contract_facts(machine_id))
        facts.extend(normalizer.cpc_facts(machine_id))
    facts.extend(normalizer.service_facts())
    facts.extend(_orphan_part_facts(normalizer))
    facts.extend(normalizer.material_facts())
    facts.extend(normalizer.invoice_facts())
    facts.sort(
        key=lambda fact: (fact["device_id"], fact["observed_at"] or "", fact["external_key"])
    )
    return {"devices": devices, "facts": facts, "complete": True}


def load_orbit_snapshot(machine_ids=None, *, only_machine_ids=None):
    """Ładuje aktywną flotę i znane ID z PG; kończy wynik dopiero po wyczerpaniu stron.

    Warstwa nadrzędna przekazuje logiczne ID maszyn wcześniej zapisanych w PostgreSQL.
    Funkcja nie łączy się z PostgreSQL, nie zapisuje MS i nie wykonuje żądań HTTP.
    """

    def identifiers(values):
        """Waliduje wszystkie ID przed otwarciem połączenia."""
        result = set()
        for value in values if values is not None else ():
            identifier = _id(value)
            if identifier is None:
                raise ValueError(
                    "Identyfikatory maszyn ORBIT muszą być dodatnimi liczbami całkowitymi."
                )
            result.add(identifier)
        return result

    known_ids = identifiers(machine_ids)
    only_ids = identifiers(only_machine_ids) if only_machine_ids is not None else None
    if only_ids is not None:
        known_ids = only_ids
        if not only_ids:
            return {"devices": [], "facts": [], "complete": True}
    config = TelemetrySettings()
    has_user = bool(config.ms_user)
    has_password = bool(config.ms_password.get_secret_value())
    if has_user != has_password:
        raise ValueError("Niepełne dane konta odczytowego MS dla ORBIT.")
    check_test_host(settings.fb_host)
    with firebird_connection(config=config if has_user else None) as connection:
        cursor = connection.cursor()
        try:
            tables = _collect(cursor, known_ids, only_ids)
        finally:
            cursor.close()
    return _normalize(tables, known_ids)
