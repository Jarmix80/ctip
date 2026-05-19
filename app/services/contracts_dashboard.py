"""Logika dashboardu obslugi umow (formularze + Firebird + legacy Google Sheets)."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import FormRequest
from app.services.settings_store import build_store


@dataclass(slots=True, frozen=True)
class FirebirdRuntimeConfig:
    """Aktywna konfiguracja Firebird pobrana z panelu administratora lub srodowiska."""

    mode: str
    host: str
    port: int
    database: str
    user: str
    password: str
    charset: str
    role: str | None
    local_copy_path: str
    allow_writes: bool


_settings_store = build_store(settings.admin_secret_key)
_firebird_runtime_config_var: ContextVar[FirebirdRuntimeConfig | None] = ContextVar(
    "firebird_runtime_config",
    default=None,
)


def normalize_nip(value: str | None) -> str:
    """Normalizuje NIP do postaci cyfr."""
    if not value:
        return ""
    return re.sub(r"\D+", "", value)


def normalize_device_key(value: str | None) -> str:
    """Normalizuje klucze urzadzen (serial/ewidencja)."""
    if not value:
        return ""
    return re.sub(r"[^A-Z0-9]", "", value.upper())


@dataclass(slots=True)
class FirebirdClientMatch:
    found: bool
    id_klient: int | None = None
    nazwa: str | None = None
    nip: str | None = None
    telefon: str | None = None
    email: str | None = None
    error: str | None = None


@dataclass(slots=True)
class FirebirdClientDevice:
    """Urządzenie serwisowe przypisane do klienta w Menadżerze Serwisu."""

    id_maszyna: int | None
    id_maszyna_table: int | None
    id_klient: int | None
    id_umowacpc: int | None
    id_model: int | None
    marka: str | None
    model: str | None
    grupa: str | None
    serial: str | None
    serial2: str | None
    ewidencja: str | None
    aktywna: str | None
    synwp: int | None
    rodzaj_us: str | None
    typ: str | None
    kolorowa: str | None

    def as_dict(self) -> dict[str, Any]:
        """Zwraca urządzenie jako słownik bezpieczny do odpowiedzi API."""
        return {
            "id_maszyna": self.id_maszyna,
            "id_maszyna_table": self.id_maszyna_table,
            "id_klient": self.id_klient,
            "id_umowacpc": self.id_umowacpc,
            "id_model": self.id_model,
            "marka": self.marka,
            "model": self.model,
            "grupa": self.grupa,
            "serial": self.serial,
            "serial2": self.serial2,
            "ewidencja": self.ewidencja,
            "aktywna": self.aktywna,
            "synwp": self.synwp,
            "rodzaj_us": self.rodzaj_us,
            "typ": self.typ,
            "kolorowa": self.kolorowa,
        }


@dataclass(slots=True)
class FirebirdClientWriteResult:
    created: bool
    match: FirebirdClientMatch


@dataclass(slots=True)
class FirebirdModelMatch:
    found: bool
    id_model: int | None = None
    marka: str | None = None
    model: str | None = None
    grupa: str | None = None
    rodzaj: str | None = None
    kolor: str | None = None
    plik: str | None = None
    error: str | None = None


@dataclass(slots=True)
class FirebirdWarehouseMatch:
    found: bool
    id_magazyn_table: int | None = None
    indeks: str | None = None
    nazwa: str | None = None
    error: str | None = None


@dataclass(slots=True)
class FirebirdDeviceSyncResult:
    row: int
    serial: str | None
    ewidencja: str | None
    model_source: str | None
    model_id: int | None
    machine_id: int | None
    machine_created: bool
    warehouse_id: int | None
    warehouse_created: bool


@dataclass(slots=True)
class DeviceMatch:
    source_row: int
    serial: str | None
    ewidencja: str | None
    model: str | None
    found_in_firebird: bool
    id_maszyna: int | None
    id_klient: int | None
    id_umowacpc: int | None
    error: str | None = None


def _coerce_firebird_port(value: str | int | None, default: int) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _coerce_firebird_bool(value: str | bool | None, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "t", "yes", "on"}


def _normalize_firebird_mode(value: str | None) -> str:
    mode = (value or "").strip().lower() or settings.fb_mode.lower()
    if mode not in {"network", "local"}:
        return (
            settings.fb_mode.lower()
            if settings.fb_mode.lower() in {"network", "local"}
            else "local"
        )
    return mode


def _default_firebird_runtime_config() -> FirebirdRuntimeConfig:
    return FirebirdRuntimeConfig(
        mode=_normalize_firebird_mode(settings.fb_mode),
        host=(settings.fb_host or "").strip(),
        port=_coerce_firebird_port(settings.fb_port, 3050),
        database=(settings.fb_database or "").strip(),
        user=(settings.fb_user or "").strip(),
        password=settings.fb_password or "",
        charset=(settings.fb_charset or "").strip() or "UTF8",
        role=(settings.fb_role or "").strip() or None,
        local_copy_path=(settings.fb_local_copy_path or "").strip()
        or "inbox/firebird/test_ms_local.fdb",
        allow_writes=bool(settings.fb_allow_writes),
    )


async def load_firebird_runtime_config(session: AsyncSession) -> FirebirdRuntimeConfig:
    """Laduje biezaca konfiguracje Firebird z panelu administratora z fallbackiem do `.env`."""
    defaults = _default_firebird_runtime_config()
    stored = await _settings_store.get_namespace(session, "firebird")
    raw_role = stored.get("role")
    role = defaults.role if raw_role is None else (raw_role.strip() or None)
    return FirebirdRuntimeConfig(
        mode=_normalize_firebird_mode(stored.get("mode") or defaults.mode),
        host=(stored.get("host") or defaults.host).strip(),
        port=_coerce_firebird_port(stored.get("port"), defaults.port),
        database=(stored.get("database") or defaults.database).strip(),
        user=(stored.get("user") or defaults.user).strip(),
        password=stored.get("password") or defaults.password,
        charset=(stored.get("charset") or defaults.charset).strip() or defaults.charset,
        role=role,
        local_copy_path=(stored.get("local_copy_path") or defaults.local_copy_path).strip()
        or defaults.local_copy_path,
        allow_writes=_coerce_firebird_bool(stored.get("allow_writes"), defaults.allow_writes),
    )


def _resolve_firebird_runtime_config() -> FirebirdRuntimeConfig:
    return _firebird_runtime_config_var.get() or _default_firebird_runtime_config()


@contextmanager
def use_firebird_runtime_config(config: FirebirdRuntimeConfig | None):
    """Aktywuje runtime config Firebird dla biezacego kontekstu zadania/watku."""
    if config is None:
        yield
        return
    token = _firebird_runtime_config_var.set(config)
    try:
        yield
    finally:
        _firebird_runtime_config_var.reset(token)


def _extract_submitted_payload(item: FormRequest) -> dict[str, Any] | None:
    from app.services import form_generator

    if item.status != "SUBMITTED":
        return None
    payload, _ = form_generator.decode_submitted_payload(item)
    if payload and isinstance(payload, dict):
        return payload
    return None


async def load_submitted_forms(session: AsyncSession, *, limit: int = 250) -> list[FormRequest]:
    """Pobiera formularze i filtruje tylko wypelnione."""
    return await load_contract_forms(session, limit=limit, submitted_only=True)


async def load_contract_forms(
    session: AsyncSession,
    *,
    limit: int = 250,
    submitted_only: bool = False,
) -> list[FormRequest]:
    """Pobiera formularze workflow, opcjonalnie tylko te wypelnione."""
    from app.services import form_generator

    items = await form_generator.list_form_requests(session, limit=limit)
    if submitted_only:
        return [item for item in items if item.status == "SUBMITTED"]
    return items


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_local_firebird_path() -> Path:
    runtime = _resolve_firebird_runtime_config()
    db_path = Path(runtime.local_copy_path)
    if not db_path.is_absolute():
        db_path = _repo_root() / db_path
    return db_path


def firebird_writes_enabled() -> tuple[bool, str | None]:
    """Sprawdza, czy zapis do aktywnej bazy Firebird jest jawnie odblokowany."""
    runtime = _resolve_firebird_runtime_config()
    if not runtime.allow_writes:
        return (
            False,
            'Zapis do Firebird jest zablokowany w panelu administratora. Wlacz opcje "Odblokuj zapis do Firebird" w konfiguracji Menadzera Serwisu.',
        )

    if runtime.mode == "network":
        if not runtime.host:
            return False, "Brak hosta Firebird w aktywnej konfiguracji."
        if not runtime.database:
            return False, "Brak bazy Firebird w aktywnej konfiguracji."
        return True, None

    db_path = _resolve_local_firebird_path()
    if not db_path.exists():
        return False, f"Brak lokalnej kopii Firebird do zapisu: {db_path}"

    return True, None


def _firebird_connection():
    import firebirdsql  # type: ignore[import-not-found]

    runtime = _resolve_firebird_runtime_config()
    connect_kwargs: dict[str, Any] = {
        "port": runtime.port,
        "user": runtime.user,
        "password": runtime.password,
        "charset": runtime.charset,
    }
    if runtime.role:
        connect_kwargs["role"] = runtime.role

    if runtime.mode == "network":
        if not runtime.host:
            raise FileNotFoundError("Brak hosta Firebird w aktywnej konfiguracji.")
        if not runtime.database:
            raise FileNotFoundError("Brak bazy Firebird w aktywnej konfiguracji.")
        return firebirdsql.connect(
            host=runtime.host,
            database=runtime.database,
            **connect_kwargs,
        )

    db_path = _resolve_local_firebird_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Brak lokalnej kopii Firebird: {db_path}")
    return firebirdsql.connect(
        host="127.0.0.1",
        database=str(db_path),
        **connect_kwargs,
    )


def _truncate_text(value: str | None, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized[:max_length]


def _build_address(payload: dict[str, Any]) -> str | None:
    street = _truncate_text(str(payload.get("registered_street") or ""), 160)
    building = _truncate_text(str(payload.get("registered_building_no") or ""), 20)
    apartment = _truncate_text(str(payload.get("registered_apartment_no") or ""), 20)
    if not street:
        return None

    suffix = building or ""
    if apartment:
        suffix = f"{suffix}/{apartment}" if suffix else apartment
    if suffix:
        return _truncate_text(f"{street} {suffix}", 250)
    return _truncate_text(street, 250)


def _build_notes(payload: dict[str, Any], *, source_name: str | None) -> str | None:
    parts: list[str] = []
    if source_name:
        parts.append(source_name.strip())
    billing_email = _truncate_text(str(payload.get("billing_email") or ""), 200)
    if billing_email:
        parts.append(f"e-faktura: {billing_email}")
    return _truncate_text(" | ".join(parts), 1000)


def _build_device_note(*, row: int) -> str:
    return f"CTIP arkusz Urzadzenia row {row}"


def _build_stock_name(
    *,
    serial: str | None,
    ewidencja: str | None,
    model_name: str | None,
    marka: str | None,
) -> str:
    parts: list[str] = []
    if marka:
        parts.append(marka.strip())
    if model_name:
        parts.append(model_name.strip())
    base = " ".join(part for part in parts if part).strip() or (model_name or "Urzadzenie").strip()
    details: list[str] = []
    if serial:
        details.append(f"S/N:{serial.strip()}")
    if ewidencja:
        details.append(f"nr.wew: {ewidencja.strip()}")
    if details:
        return _truncate_text(f"{base} {', '.join(details)}", 250) or "Urzadzenie"
    return _truncate_text(base, 250) or "Urzadzenie"


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _normalize_model_name_for_brand(brand: str | None, model_name: str | None) -> str:
    """Normalizuje znane warianty zapisu modelu dla producenta."""
    brand_value = _normalize_spaces(str(brand or ""))
    model_value = _normalize_spaces(str(model_name or ""))
    if not brand_value or not model_value:
        return model_value

    if brand_value.upper() != "RICOH":
        return model_value

    condensed = re.sub(r"\s+", "", model_value).upper()
    imc_match = re.match(r"^IMC(\d+[A-Z0-9]*)$", condensed)
    if imc_match:
        return f"IM C{imc_match.group(1)}"

    mpc_match = re.match(r"^MPC(\d+[A-Z0-9]*)$", condensed)
    if mpc_match:
        return f"MP C{mpc_match.group(1)}"

    return model_value


def extract_stock_device_identity(
    raw_name: str | None,
    *,
    index_value: str | None = None,
    producer: str | None = None,
    model: str | None = None,
) -> dict[str, str]:
    """Wyciąga z nazwy pozycji magazynowej producenta, model, serial i numer wew."""
    raw = _normalize_spaces(str(raw_name or ""))
    serial = ""
    ewidencja = ""
    if raw:
        serial_match = re.search(
            r"(?i)\bS\s*/?\s*N\s*[:#-]?\s*([A-Z0-9][A-Z0-9._/-]{3,})",
            raw,
        )
        if serial_match:
            serial = _normalize_spaces(serial_match.group(1)).strip(" ,;")

        ewidencja_match = re.search(
            r"(?i)\bnr\.?\s*wew\.?\s*[:#-]?\s*([^,;]+)",
            raw,
        )
        if ewidencja_match:
            ewidencja = _normalize_spaces(ewidencja_match.group(1)).strip(" ,;")

    cleaned = re.split(r"(?i)\bS\s*/?\s*N\s*[:#-]?", raw, maxsplit=1)[0]
    cleaned = re.split(r"(?i)\bnr\.?\s*wew\.?\s*[:#-]?", cleaned, maxsplit=1)[0]
    cleaned = cleaned.strip(" ,;-")
    cleaned = _normalize_spaces(cleaned)

    producer_value = _normalize_spaces(str(producer or ""))
    model_value = _normalize_spaces(str(model or ""))

    if cleaned:
        if producer_value:
            prefixed = re.match(
                rf"(?i)^{re.escape(producer_value)}\s+(.*)$",
                cleaned,
            )
            if prefixed:
                model_value = _normalize_spaces(prefixed.group(1)) or model_value
            elif not model_value:
                model_value = cleaned
        else:
            tokens = cleaned.split(" ", 1)
            if len(tokens) == 2 and re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]{1,30}", tokens[0]):
                producer_value = tokens[0]
                model_value = _normalize_spaces(tokens[1])
            elif not model_value:
                model_value = cleaned

    model_value = _normalize_model_name_for_brand(producer_value, model_value)

    if not ewidencja:
        ewidencja = _normalize_spaces(str(index_value or ""))

    return {
        "producer": producer_value,
        "model": model_value,
        "serial": serial,
        "ewidencja": ewidencja,
        "name": cleaned or raw,
    }


def _model_search_candidates(model_name: str | None) -> list[str]:
    """Buduje liste kandydatow modelu, czyszczac techniczne koncowki typu S/N."""
    raw = _truncate_text(model_name, 160)
    if not raw:
        return []

    primary = _normalize_spaces(raw)
    candidates: list[str] = [primary]

    stripped = re.split(r"(?i)\bS\s*/?\s*N\s*[:#-]?", primary, maxsplit=1)[0]
    stripped = re.split(r"(?i)\bnr\.?\s*wew\.?\s*[:#-]?", stripped, maxsplit=1)[0]
    stripped = re.split(r"(?i)\bnr\.?\s*ewid", stripped, maxsplit=1)[0]
    stripped = stripped.strip(" ,;-")
    stripped = _normalize_spaces(stripped)
    if stripped and stripped not in candidates:
        candidates.append(stripped)

    if "," in stripped:
        head = _normalize_spaces(stripped.split(",", maxsplit=1)[0].strip(" ,;-"))
        if head and head not in candidates:
            candidates.append(head)

    parsed = extract_stock_device_identity(stripped)
    parsed_model = _normalize_spaces(parsed.get("model") or "")
    parsed_producer = _normalize_spaces(parsed.get("producer") or "")
    if parsed_model and parsed_model not in candidates:
        candidates.append(parsed_model)
    if parsed_producer and parsed_model:
        combined = _normalize_spaces(f"{parsed_producer} {parsed_model}")
        if combined and combined not in candidates:
            candidates.append(combined)

    return [candidate[:100] for candidate in candidates if candidate]


def _build_model_match_from_row(row: tuple[Any, ...]) -> FirebirdModelMatch:
    return FirebirdModelMatch(
        found=True,
        id_model=int(row[0]) if row[0] is not None else None,
        marka=_truncate_text(str(row[1]) if row[1] is not None else "", 50),
        model=_truncate_text(str(row[2]) if row[2] is not None else "", 50),
        grupa=_truncate_text(str(row[3]) if row[3] is not None else "", 50),
        rodzaj=_truncate_text(str(row[4]) if row[4] is not None else "", 50),
        kolor=_truncate_text(str(row[5]) if row[5] is not None else "", 50),
        plik=_truncate_text(str(row[6]) if row[6] is not None else "", 250),
    )


def create_client_from_submitted_payload(
    payload: dict[str, Any],
    *,
    source_name: str | None = None,
    kto: str = "CTIP",
) -> FirebirdClientWriteResult:
    """Tworzy klienta w aktywnej bazie Firebird na podstawie formularza SUBMITTED."""
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")

    company_name = _truncate_text(str(payload.get("company_name") or ""), 500)
    if not company_name:
        raise ValueError("Formularz nie zawiera nazwy klienta.")

    nip = normalize_nip(str(payload.get("company_nip") or ""))
    if not nip:
        raise ValueError("Formularz nie zawiera NIP klienta.")

    existing = find_client_in_firebird(nip)
    if existing.error:
        raise RuntimeError(f"Nie udalo sie zweryfikowac klienta w Firebird: {existing.error}")
    if existing.found:
        return FirebirdClientWriteResult(created=False, match=existing)

    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO KLIENT (
                ID_ODDZIAL,
                ID_FIRMA,
                NAZWA,
                ADRES,
                KOD,
                POCZTA,
                NIP,
                TELEFON,
                E_MAIL,
                UWAGI,
                AKTYWNY,
                KTO
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                0,
                company_name,
                _build_address(payload),
                _truncate_text(str(payload.get("registered_postal_code") or ""), 6),
                _truncate_text(str(payload.get("registered_city") or ""), 150),
                nip,
                _truncate_text(str(payload.get("company_phone") or ""), 100),
                _truncate_text(str(payload.get("company_email") or ""), 200),
                _build_notes(payload, source_name=source_name),
                "TAK",
                _truncate_text(kto, 50),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    created = find_client_in_firebird(nip)
    if created.error:
        raise RuntimeError(f"Klient zapisany, ale odczyt po zapisie nie udal sie: {created.error}")
    if not created.found or created.id_klient is None:
        raise RuntimeError("Klient zostal zapisany, ale nie udalo sie ustalic jego ID w Firebird.")
    return FirebirdClientWriteResult(created=True, match=created)


def find_model_in_firebird(model_name: str | None) -> FirebirdModelMatch:
    """Wyszukuje model po nazwie tekstowej z arkusza."""
    candidates = _model_search_candidates(model_name)
    if not candidates:
        return FirebirdModelMatch(found=False)

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            for candidate in candidates:
                cursor.execute(
                    """
                    SELECT FIRST 1
                        ID_MODEL,
                        MARKA,
                        MODEL,
                        GRUPA,
                        RODZAJ,
                        KOLOR,
                        PLIK
                    FROM MODEL
                    WHERE UPPER(TRIM(MODEL)) = UPPER(TRIM(?))
                       OR UPPER(TRIM(MARKA || ' ' || MODEL)) = UPPER(TRIM(?))
                    ORDER BY ID_MODEL DESC
                    """,
                    (candidate, candidate),
                )
                row = cursor.fetchone()
                if row:
                    return _build_model_match_from_row(row)

                cursor.execute(
                    """
                    SELECT FIRST 1
                        ID_MODEL,
                        MARKA,
                        MODEL,
                        GRUPA,
                        RODZAJ,
                        KOLOR,
                        PLIK
                    FROM MODEL
                    WHERE UPPER(TRIM(MODEL)) CONTAINING UPPER(TRIM(?))
                       OR UPPER(TRIM(MARKA || ' ' || MODEL)) CONTAINING UPPER(TRIM(?))
                    ORDER BY ID_MODEL DESC
                    """,
                    (candidate, candidate),
                )
                row = cursor.fetchone()
                if row:
                    return _build_model_match_from_row(row)

            return FirebirdModelMatch(found=False)
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return FirebirdModelMatch(found=False, error=str(exc))


def find_model_in_firebird_by_id(model_id: int | None) -> FirebirdModelMatch:
    """Wyszukuje model po ID_MODEL."""
    try:
        resolved_id = int(model_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        resolved_id = 0
    if resolved_id <= 0:
        return FirebirdModelMatch(found=False)

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT FIRST 1
                    ID_MODEL,
                    MARKA,
                    MODEL,
                    GRUPA,
                    RODZAJ,
                    KOLOR,
                    PLIK
                FROM MODEL
                WHERE ID_MODEL = ?
                """,
                (resolved_id,),
            )
            row = cursor.fetchone()
            if not row:
                return FirebirdModelMatch(found=False)
            return _build_model_match_from_row(row)
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return FirebirdModelMatch(found=False, error=str(exc))


def find_warehouse_item_in_firebird(ewidencja: str | None) -> FirebirdWarehouseMatch:
    """Wyszukuje pozycje magazynowa po indeksie / ewidencji."""
    normalized = normalize_device_key(ewidencja)
    if not normalized:
        return FirebirdWarehouseMatch(found=False)

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT FIRST 1 ID_MAGAZYN_TABLE, INDEKS, NAZWA
                FROM MAGAZYN
                WHERE UPPER(REPLACE(REPLACE(REPLACE(INDEKS, '/', ''), '-', ''), ' ', '')) = ?
                ORDER BY ID_MAGAZYN_TABLE DESC
                """,
                (normalized,),
            )
            row = cursor.fetchone()
            if not row:
                return FirebirdWarehouseMatch(found=False)
            return FirebirdWarehouseMatch(
                found=True,
                id_magazyn_table=int(row[0]) if row[0] is not None else None,
                indeks=_truncate_text(str(row[1]) if row[1] is not None else "", 100),
                nazwa=_truncate_text(str(row[2]) if row[2] is not None else "", 250),
            )
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return FirebirdWarehouseMatch(found=False, error=str(exc))


def load_device_from_sheet_row(row: int) -> dict[str, str] | None:
    """Zwraca pojedynczy rekord urzadzenia z arkusza po numerze wiersza."""
    for item in load_devices_from_sheet():
        try:
            current_row = int(item.get("row") or 0)
        except ValueError:
            continue
        if current_row == row:
            return item
    return None


def synchronize_device_from_sheet_row(
    row: int,
    *,
    kto: str = "CTIP",
) -> FirebirdDeviceSyncResult:
    """Synchronizuje urzadzenie z arkusza do aktywnej Firebird (MASZYNA + MAGAZYN)."""
    enabled, reason = firebird_writes_enabled()
    if not enabled:
        raise RuntimeError(reason or "Zapis do Firebird jest zablokowany.")

    device = load_device_from_sheet_row(row)
    if device is None:
        raise ValueError(f"Nie znaleziono wiersza {row} w arkuszu Urzadzenia.")

    serial = _truncate_text(device.get("serial"), 100)
    ewidencja = _truncate_text(device.get("ewidencja"), 100)
    model_source = _truncate_text(device.get("model"), 50)
    if not serial and not ewidencja:
        raise ValueError(f"Wiersz {row} nie zawiera serialu ani ewidencji.")

    machine = find_device_in_firebird(serial, ewidencja)
    if machine.error:
        raise RuntimeError(f"Nie udalo sie sprawdzic urzadzenia w Firebird: {machine.error}")
    warehouse = find_warehouse_item_in_firebird(ewidencja)
    if warehouse.error:
        raise RuntimeError(f"Nie udalo sie sprawdzic pozycji magazynowej: {warehouse.error}")
    model_match = find_model_in_firebird(model_source)
    if model_match.error:
        raise RuntimeError(f"Nie udalo sie sprawdzic modelu w Firebird: {model_match.error}")

    if machine.found_in_firebird and warehouse.found:
        return FirebirdDeviceSyncResult(
            row=row,
            serial=serial,
            ewidencja=ewidencja,
            model_source=model_source,
            model_id=model_match.id_model,
            machine_id=machine.id_maszyna,
            machine_created=False,
            warehouse_id=warehouse.id_magazyn_table,
            warehouse_created=False,
        )

    connection = _firebird_connection()
    cursor = connection.cursor()
    try:
        if not machine.found_in_firebird:
            cursor.execute(
                """
                INSERT INTO MASZYNA (
                    ID_ODDZIAL,
                    ID_FIRMA,
                    ID_KLIENT,
                    ID_MODEL,
                    MARKA,
                    MODEL,
                    SERIAL,
                    EWIDENCJA,
                    AKTYWNA,
                    UWAGI
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    1,
                    settings.fb_warehouse_client_id,
                    model_match.id_model,
                    _truncate_text(model_match.marka, 100),
                    _truncate_text(model_match.model or model_source, 100),
                    serial,
                    ewidencja,
                    "TAK",
                    _build_device_note(row=row),
                ),
            )

        if not warehouse.found:
            stock_index = ewidencja or serial
            cursor.execute(
                """
                INSERT INTO MAGAZYN (
                    ID_ODDZIAL,
                    ID_FIRMA,
                    ID_MAGAZYN,
                    INDEKS,
                    NAZWA,
                    JM,
                    ILOSC,
                    DATA_Z,
                    MARKA,
                    MODEL,
                    ID_MODEL,
                    SERIAL,
                    IDVAT,
                    VAT_STAWKA,
                    UWAGI
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    1,
                    1,
                    settings.fb_warehouse_id,
                    _truncate_text(stock_index, 100),
                    _build_stock_name(
                        serial=serial,
                        ewidencja=ewidencja,
                        model_name=model_match.model or model_source,
                        marka=model_match.marka,
                    ),
                    "szt.",
                    1,
                    date.today(),
                    _truncate_text(model_match.marka, 50),
                    _truncate_text(model_match.model or model_source, 50),
                    model_match.id_model,
                    "NIE",
                    1,
                    "23 %",
                    _build_device_note(row=row),
                ),
            )

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()

    machine_after = find_device_in_firebird(serial, ewidencja)
    warehouse_after = find_warehouse_item_in_firebird(ewidencja)
    if machine_after.error:
        raise RuntimeError(
            f"Urzadzenie zapisane, ale odczyt po zapisie nie udal sie: {machine_after.error}"
        )
    if warehouse_after.error:
        raise RuntimeError(
            f"Pozycja magazynowa zapisana, ale odczyt po zapisie nie udal sie: {warehouse_after.error}"
        )

    return FirebirdDeviceSyncResult(
        row=row,
        serial=serial,
        ewidencja=ewidencja,
        model_source=model_source,
        model_id=model_match.id_model,
        machine_id=machine_after.id_maszyna,
        machine_created=not machine.found_in_firebird,
        warehouse_id=warehouse_after.id_magazyn_table,
        warehouse_created=not warehouse.found,
    )


def find_client_in_firebird(nip: str) -> FirebirdClientMatch:
    """Wyszukuje klienta po NIP w aktywnej konfiguracji Firebird."""
    cleaned = normalize_nip(nip)
    if not cleaned:
        return FirebirdClientMatch(found=False)
    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT FIRST 1 ID_KLIENT, NAZWA, NIP, TELEFON, E_MAIL
                FROM KLIENT
                WHERE REPLACE(
                    REPLACE(
                        REPLACE(
                            REPLACE(REPLACE(UPPER(COALESCE(NIP, '')), 'PL', ''), '-', ''),
                            ' ',
                            ''
                        ),
                        '.',
                        ''
                    ),
                    '/',
                    ''
                ) = ?
                """,
                (cleaned,),
            )
            row = cursor.fetchone()
            if row:
                return FirebirdClientMatch(
                    found=True,
                    id_klient=int(row[0]) if row[0] is not None else None,
                    nazwa=str(row[1]) if row[1] is not None else None,
                    nip=str(row[2]) if row[2] is not None else None,
                    telefon=str(row[3]) if row[3] is not None else None,
                    email=str(row[4]) if row[4] is not None else None,
                )

            cursor.execute(
                """
                SELECT FIRST 1 ID_KLIENT, NAZWA, NIP, TELEFON, E_MAIL
                FROM KLIENT
                WHERE REPLACE(
                    REPLACE(
                        REPLACE(
                            REPLACE(REPLACE(UPPER(COALESCE(NIP, '')), 'PL', ''), '-', ''),
                            ' ',
                            ''
                        ),
                        '.',
                        ''
                    ),
                    '/',
                    ''
                ) CONTAINING ?
                ORDER BY ID_KLIENT ASC
                """,
                (cleaned,),
            )
            row = cursor.fetchone()
            if not row:
                return FirebirdClientMatch(found=False)
            return FirebirdClientMatch(
                found=True,
                id_klient=int(row[0]) if row[0] is not None else None,
                nazwa=str(row[1]) if row[1] is not None else None,
                nip=str(row[2]) if row[2] is not None else None,
                telefon=str(row[3]) if row[3] is not None else None,
                email=str(row[4]) if row[4] is not None else None,
            )
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return FirebirdClientMatch(found=False, error=str(exc))


def find_client_in_firebird_by_id(client_id: int | None) -> FirebirdClientMatch:
    """Wyszukuje klienta po ID_KLIENT w aktywnej konfiguracji Firebird."""
    if not client_id:
        return FirebirdClientMatch(found=False)

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT FIRST 1 ID_KLIENT, NAZWA, NIP, TELEFON, E_MAIL
                FROM KLIENT
                WHERE ID_KLIENT = ?
                """,
                (int(client_id),),
            )
            row = cursor.fetchone()
            if not row:
                return FirebirdClientMatch(found=False)
            return FirebirdClientMatch(
                found=True,
                id_klient=int(row[0]) if row[0] is not None else None,
                nazwa=str(row[1]) if row[1] is not None else None,
                nip=str(row[2]) if row[2] is not None else None,
                telefon=str(row[3]) if row[3] is not None else None,
                email=str(row[4]) if row[4] is not None else None,
            )
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return FirebirdClientMatch(found=False, error=str(exc))


def search_clients_in_firebird(
    query: str | None = None,
    *,
    nip: str | None = None,
    limit: int = 20,
) -> list[FirebirdClientMatch]:
    """Wyszukuje klientów po NIP albo fragmencie nazwy w aktywnej bazie Firebird."""
    cleaned_nip = normalize_nip(nip or query or "")
    text_query = str(query or "").strip()
    safe_limit = max(1, min(int(limit), 100))
    if not cleaned_nip and len(text_query) < 2:
        return []

    conditions: list[str] = []
    params: list[Any] = []
    if cleaned_nip:
        conditions.append(
            """
            REPLACE(
                REPLACE(
                    REPLACE(
                        REPLACE(REPLACE(UPPER(COALESCE(NIP, '')), 'PL', ''), '-', ''),
                        ' ',
                        ''
                    ),
                    '.',
                    ''
                ),
                '/',
                ''
            ) CONTAINING ?
            """
        )
        params.append(cleaned_nip)
    if text_query and not text_query.isdigit():
        conditions.append("UPPER(COALESCE(NAZWA, '')) CONTAINING ?")
        params.append(text_query.upper())

    if not conditions:
        return []

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT FIRST {safe_limit} ID_KLIENT, NAZWA, NIP, TELEFON, E_MAIL
                FROM KLIENT
                WHERE {' OR '.join(conditions)}
                ORDER BY NAZWA ASC, ID_KLIENT ASC
                """,
                tuple(params),
            )
            output: list[FirebirdClientMatch] = []
            for row in cursor.fetchall():
                output.append(
                    FirebirdClientMatch(
                        found=True,
                        id_klient=int(row[0]) if row[0] is not None else None,
                        nazwa=str(row[1]) if row[1] is not None else None,
                        nip=str(row[2]) if row[2] is not None else None,
                        telefon=str(row[3]) if row[3] is not None else None,
                        email=str(row[4]) if row[4] is not None else None,
                    )
                )
            return output
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return [FirebirdClientMatch(found=False, error=str(exc))]


def _build_firebird_client_device(row: tuple[Any, ...]) -> FirebirdClientDevice:
    return FirebirdClientDevice(
        id_maszyna=int(row[0]) if row[0] is not None else None,
        id_maszyna_table=int(row[1]) if row[1] is not None else None,
        id_klient=int(row[2]) if row[2] is not None else None,
        id_umowacpc=int(row[3]) if row[3] is not None else None,
        id_model=int(row[4]) if row[4] is not None else None,
        marka=str(row[5]) if row[5] is not None else None,
        model=str(row[6]) if row[6] is not None else None,
        grupa=str(row[7]) if row[7] is not None else None,
        serial=str(row[8]) if row[8] is not None else None,
        serial2=str(row[9]) if row[9] is not None else None,
        ewidencja=str(row[10]) if row[10] is not None else None,
        aktywna=str(row[11]) if row[11] is not None else None,
        synwp=int(row[12]) if row[12] is not None else None,
        rodzaj_us=str(row[13]) if row[13] is not None else None,
        typ=str(row[14]) if row[14] is not None else None,
        kolorowa=str(row[15]) if row[15] is not None else None,
    )


def load_client_devices_from_firebird(
    client_id: int,
    *,
    include_inactive: bool = False,
    limit: int = 500,
) -> list[FirebirdClientDevice]:
    """Pobiera urządzenia serwisowe klienta z tabeli MASZYNA."""
    safe_limit = max(1, min(int(limit), 2000))
    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            active_filter = ""
            if not include_inactive:
                active_filter = "AND UPPER(COALESCE(AKTYWNA, 'TAK')) <> 'NIE'"
            cursor.execute(
                f"""
                SELECT FIRST {safe_limit}
                    ID_MASZYNA,
                    ID_MASZYNA_TABLE,
                    ID_KLIENT,
                    ID_UMOWACPC,
                    ID_MODEL,
                    MARKA,
                    MODEL,
                    GRUPA,
                    SERIAL,
                    SERIAL2,
                    EWIDENCJA,
                    AKTYWNA,
                    SYNWP,
                    RODZAJ_US,
                    TYP,
                    KOLOROWA
                FROM MASZYNA
                WHERE ID_KLIENT = ?
                {active_filter}
                ORDER BY ID_MASZYNA DESC
                """,
                (int(client_id),),
            )
            return [_build_firebird_client_device(row) for row in cursor.fetchall()]
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Nie udało się pobrać urządzeń klienta z Firebird: {exc}") from exc


def _sheet_headers_map(headers: list[str]) -> dict[str, int]:
    mapped: dict[str, int] = {}
    for idx, col in enumerate(headers):
        normalized = col.strip().lower()
        mapped[normalized] = idx
    return mapped


def _decimal_or_zero(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _format_decimal_text(value: Any, *, precision: str = "0.01") -> str:
    decimal_value = _decimal_or_zero(value).quantize(Decimal(precision))
    return format(decimal_value, "f")


def _format_quantity_text(value: Any) -> str:
    decimal_value = _decimal_or_zero(value)
    normalized = decimal_value.normalize()
    return format(normalized, "f").rstrip("0").rstrip(".") or "0"


def _extract_vat_rate_text(value: Any) -> str:
    raw = str(value or "").strip().replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)", raw)
    if not match:
        return "23"
    return match.group(1).rstrip("0").rstrip(".") or "23"


def load_available_devices_from_firebird_warehouse(*, limit: int = 500) -> list[dict[str, str]]:
    """Pobiera dostepne pozycje magazynowe z Firebird jako zrodlo wyboru urzadzen dla FLOW."""
    safe_limit = max(1, min(int(limit), 2000))
    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                f"""
                SELECT FIRST {safe_limit}
                    ID_MAGAZYN_TABLE,
                    ID_MODEL,
                    INDEKS,
                    NAZWA,
                    MARKA,
                    MODEL,
                    ILOSC,
                    IL_REZ,
                    CENA_NETTO,
                    CENA_BRUTTO,
                    VAT_STAWKA,
                    SERIAL
                FROM MAGAZYN
                WHERE COALESCE(ID_MAGAZYN, 0) = ?
                  AND COALESCE(ILOSC, 0) > 0
                ORDER BY ID_MAGAZYN_TABLE DESC
                """,
                (settings.fb_warehouse_id,),
            )

            output: list[dict[str, str]] = []
            for row in cursor.fetchall():
                total_qty = _decimal_or_zero(row[6])
                reserved_qty = _decimal_or_zero(row[7])
                available_qty = total_qty - reserved_qty
                if available_qty <= 0:
                    continue

                if reserved_qty > 0:
                    reservation_status = (
                        f"czesciowa rezerwacja ({_format_quantity_text(reserved_qty)} z "
                        f"{_format_quantity_text(total_qty)})"
                    )
                    status = "Dostepne czesciowo"
                else:
                    reservation_status = "brak rezerwacji"
                    status = "Dostepne"

                index_value = _truncate_text(str(row[2] or ""), 100) or ""
                name_value = _truncate_text(str(row[3] or ""), 250) or ""
                parsed_identity = extract_stock_device_identity(
                    name_value,
                    index_value=index_value,
                    producer=str(row[4] or ""),
                    model=str(row[5] or ""),
                )
                producer_value = (
                    _truncate_text(
                        str(row[4] or parsed_identity["producer"] or ""),
                        50,
                    )
                    or ""
                )
                model_value = (
                    _truncate_text(
                        str(row[5] or parsed_identity["model"] or ""),
                        50,
                    )
                    or name_value
                )
                serial_value = _truncate_text(str(parsed_identity["serial"] or ""), 100) or ""
                ewidencja_value = (
                    _truncate_text(
                        str(parsed_identity["ewidencja"] or index_value),
                        100,
                    )
                    or index_value
                )
                raw_serial_flag = _truncate_text(str(row[11] or ""), 10) or ""
                serial_required = "TAK" if serial_value else raw_serial_flag
                model_id_value = str(int(row[1])) if row[1] is not None else ""

                output.append(
                    {
                        "row": str(int(row[0])) if row[0] is not None else "",
                        "ms_id_magazyn_table": str(int(row[0])) if row[0] is not None else "",
                        "ms_id_model": model_id_value,
                        "producer": producer_value,
                        "model": model_value,
                        "serial": serial_value,
                        "ewidencja": ewidencja_value,
                        "index": index_value,
                        "name": name_value,
                        "status": status,
                        "price": _format_decimal_text(row[9]),
                        "price_net": _format_decimal_text(row[8]),
                        "price_gross": _format_decimal_text(row[9]),
                        "vat_rate": _extract_vat_rate_text(row[10]),
                        "reservation": "",
                        "reservation_status": reservation_status,
                        "description": name_value,
                        "available_quantity": _format_quantity_text(available_qty),
                        "reserved_quantity": _format_quantity_text(reserved_qty),
                        "warehouse_quantity": _format_quantity_text(total_qty),
                        "serial_required": serial_required,
                        "source_type": "firebird_magazyn_28",
                    }
                )
            return output
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Nie udalo sie pobrac listy urzadzen z Firebird: {exc}") from exc


def load_devices_from_sheet() -> list[dict[str, str]]:
    """Pobiera urzadzenia z arkusza Urzadzenia."""
    credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    spreadsheet_id = os.environ.get("GOOGLE_SHEETS_SPREADSHEET_ID")
    if not credentials_path or not spreadsheet_id:
        return []

    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    credentials = Credentials.from_service_account_file(credentials_path, scopes=scopes)
    client = gspread.authorize(credentials)
    workbook = client.open_by_key(spreadsheet_id)
    worksheet = workbook.worksheet("Urzadzenia")
    values = worksheet.get_all_values()
    if len(values) < 2:
        return []
    headers = _sheet_headers_map(values[0])

    producer_idx = headers.get("producent", 0)
    serial_idx = headers.get("serial", headers.get("sn", 2))
    ewidencja_idx = headers.get("ewidencja", headers.get("nr wewnetrzny", 3))
    model_idx = headers.get("model", 1)
    status_idx = headers.get("status")
    price_idx = headers.get("cena")
    reservation_idx = headers.get("rezerwacja")
    reservation_status_idx = headers.get("status rezerwacji")
    description_idx = headers.get("opis")
    ms_id_maszyna_idx = headers.get("ms_id_maszyna")
    ms_id_model_idx = headers.get("ms_id_model")
    ms_id_klient_idx = headers.get("ms_id_klient")
    ms_nazwa_klienta_idx = headers.get("ms_nazwa_klienta")
    ms_nip_idx = headers.get("ms_nip")

    def _cell(row: list[str], idx: int | None) -> str:
        if idx is None or idx >= len(row):
            return ""
        return row[idx].strip()

    output: list[dict[str, str]] = []
    for row_no, row in enumerate(values[1:], start=2):
        if not any((cell or "").strip() for cell in row):
            continue
        serial = _cell(row, serial_idx)
        ewidencja = _cell(row, ewidencja_idx)
        model = _cell(row, model_idx)
        output.append(
            {
                "row": str(row_no),
                "producer": _cell(row, producer_idx),
                "serial": serial,
                "ewidencja": ewidencja,
                "model": model,
                "status": _cell(row, status_idx),
                "price": _cell(row, price_idx),
                "reservation": _cell(row, reservation_idx),
                "reservation_status": _cell(row, reservation_status_idx),
                "description": _cell(row, description_idx),
                "ms_id_maszyna": _cell(row, ms_id_maszyna_idx),
                "ms_id_model": _cell(row, ms_id_model_idx),
                "ms_id_klient": _cell(row, ms_id_klient_idx),
                "ms_nazwa_klienta": _cell(row, ms_nazwa_klienta_idx),
                "ms_nip": _cell(row, ms_nip_idx),
            }
        )
    return output


def find_device_in_firebird(serial: str | None, ewidencja: str | None) -> DeviceMatch:
    """Wyszukuje urzadzenie po SERIAL/SERIAL2/EWIDENCJA."""
    serial_value = normalize_device_key(serial)
    ewidencja_value = normalize_device_key(ewidencja)
    if not serial_value and not ewidencja_value:
        return DeviceMatch(
            source_row=0,
            serial=serial,
            ewidencja=ewidencja,
            model=None,
            found_in_firebird=False,
            id_maszyna=None,
            id_klient=None,
            id_umowacpc=None,
            error=None,
        )

    try:
        connection = _firebird_connection()
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                SELECT FIRST 1 ID_MASZYNA, SERIAL, SERIAL2, EWIDENCJA, ID_KLIENT, ID_UMOWACPC
                FROM MASZYNA
                WHERE UPPER(REPLACE(REPLACE(REPLACE(SERIAL, '/', ''), '-', ''), ' ', '')) = ?
                   OR UPPER(REPLACE(REPLACE(REPLACE(SERIAL2, '/', ''), '-', ''), ' ', '')) = ?
                   OR UPPER(REPLACE(REPLACE(REPLACE(EWIDENCJA, '/', ''), '-', ''), ' ', '')) = ?
                """,
                (
                    serial_value or "___NO_MATCH___",
                    serial_value or "___NO_MATCH___",
                    ewidencja_value or "___NO_MATCH___",
                ),
            )
            row = cursor.fetchone()
            if not row:
                return DeviceMatch(
                    source_row=0,
                    serial=serial,
                    ewidencja=ewidencja,
                    model=None,
                    found_in_firebird=False,
                    id_maszyna=None,
                    id_klient=None,
                    id_umowacpc=None,
                    error=None,
                )
            return DeviceMatch(
                source_row=0,
                serial=str(row[1]) if row[1] is not None else serial,
                ewidencja=str(row[3]) if row[3] is not None else ewidencja,
                model=None,
                found_in_firebird=True,
                id_maszyna=int(row[0]) if row[0] is not None else None,
                id_klient=int(row[4]) if row[4] is not None else None,
                id_umowacpc=int(row[5]) if row[5] is not None else None,
                error=None,
            )
        finally:
            cursor.close()
            connection.close()
    except Exception as exc:  # noqa: BLE001
        return DeviceMatch(
            source_row=0,
            serial=serial,
            ewidencja=ewidencja,
            model=None,
            found_in_firebird=False,
            id_maszyna=None,
            id_klient=None,
            id_umowacpc=None,
            error=str(exc),
        )


__all__ = [
    "DeviceMatch",
    "FirebirdClientWriteResult",
    "FirebirdClientDevice",
    "FirebirdClientMatch",
    "FirebirdDeviceSyncResult",
    "FirebirdModelMatch",
    "FirebirdRuntimeConfig",
    "FirebirdWarehouseMatch",
    "create_client_from_submitted_payload",
    "find_model_in_firebird",
    "find_model_in_firebird_by_id",
    "find_warehouse_item_in_firebird",
    "find_client_in_firebird",
    "find_client_in_firebird_by_id",
    "find_device_in_firebird",
    "firebird_writes_enabled",
    "extract_stock_device_identity",
    "load_contract_forms",
    "load_available_devices_from_firebird_warehouse",
    "load_client_devices_from_firebird",
    "load_device_from_sheet_row",
    "load_devices_from_sheet",
    "load_firebird_runtime_config",
    "load_submitted_forms",
    "normalize_device_key",
    "normalize_nip",
    "search_clients_in_firebird",
    "synchronize_device_from_sheet_row",
    "use_firebird_runtime_config",
]
