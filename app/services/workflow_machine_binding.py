"""Automatyczne wiązanie urządzeń workflow z klientem w Menadżerze Serwisu."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AdminUser, FormRequest, FormWorkflowCase, FormWorkflowDevice, SmsOut
from app.services import admin_users
from app.services.contracts_dashboard import (
    extract_stock_device_identity,
    find_model_in_firebird,
    find_model_in_firebird_by_id,
    firebird_writes_enabled,
    normalize_device_key,
)
from app.services.email_client import send_smtp_message
from app.services.workflow_device_ownership import (
    MACHINE_BATCH_MIXED_HOLD,
    MACHINE_MATCH_BOUND_CURRENT_WORKFLOW,
    MACHINE_MATCH_MISSING,
    MACHINE_MATCH_TARGET,
    MACHINE_MATCH_WAREHOUSE,
    classify_workflow_machine_ownership,
    classify_workflow_ownership_batch,
    snapshot_confirms_current_workflow_binding,
)

DEFAULT_MACHINE_GROUP = "Druk"
DEFAULT_MACHINE_SERVICE_KIND = "Platne"
DEFAULT_MACHINE_PLACE = "CTIP FLOW"
DEFAULT_MACHINE_IDVAT = 0


@dataclass(slots=True)
class WorkflowDeviceBindingItem:
    """Wynik próby powiązania jednego urządzenia."""

    workflow_device_id: int
    source_row: int | None
    source_type: str
    ok: bool
    message: str
    producer: str | None
    model: str | None
    serial: str | None
    machine_id: int | None = None
    previous_client_id: int | None = None
    current_client_id: int | None = None
    previous_ewidencja: str | None = None
    current_ewidencja: str | None = None
    ewidencja_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "workflow_device_id": self.workflow_device_id,
            "source_row": self.source_row,
            "source_type": self.source_type,
            "ok": self.ok,
            "message": self.message,
            "producer": self.producer,
            "model": self.model,
            "serial": self.serial,
            "machine_id": self.machine_id,
            "previous_client_id": self.previous_client_id,
            "current_client_id": self.current_client_id,
            "previous_ewidencja": self.previous_ewidencja,
            "current_ewidencja": self.current_ewidencja,
            "ewidencja_changed": self.ewidencja_changed,
        }


@dataclass(frozen=True, slots=True)
class WorkflowDeviceOwnershipConflictItem:
    """Pojedyncze urządzenie z właścicielem innym niż magazyn Ksero Partner."""

    workflow_device_id: int
    source_row: int | None
    machine_id: int | None
    current_client_id: int | None
    current_client_name: str | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        """Zwraca szczegóły konfliktu do odpowiedzi API i audytu."""
        return {
            "workflow_device_id": self.workflow_device_id,
            "source_row": self.source_row,
            "machine_id": self.machine_id,
            "current_client_id": self.current_client_id,
            "current_client_name": self.current_client_name,
            "reason": self.reason,
        }


class WorkflowDeviceOwnershipConflict(RuntimeError):
    """Sygnalizuje konflikt właściciela blokujący cały pakiet urządzeń."""

    def __init__(self, conflicts: list[WorkflowDeviceOwnershipConflictItem]) -> None:
        self.conflicts = conflicts
        details = "; ".join(f"wiersz {item.source_row or '?'}: {item.reason}" for item in conflicts)
        super().__init__("Urządzenia nie są dostępne na magazynie Ksero Partner. " + details)


class WorkflowDeviceMixedOwnershipHold(WorkflowDeviceOwnershipConflict):
    """Wstrzymuje mieszany pakiet urządzeń bez wykonywania zapisów w MS."""

    def __init__(self, conflicts: list[WorkflowDeviceOwnershipConflictItem]) -> None:
        self.conflicts = conflicts
        details = "; ".join(f"wiersz {item.source_row or '?'}: {item.reason}" for item in conflicts)
        RuntimeError.__init__(
            self,
            "Pakiet zawiera jednocześnie urządzenia magazynowe i już przypisane "
            "do klienta docelowego. Sprawę pozostawiono do ręcznego wyjaśnienia. " + details,
        )


class _WorkflowMachineOwnerChanged(RuntimeError):
    """Wewnętrzny sygnał zmiany właściciela pomiędzy walidacją i zapisem."""

    def __init__(self, *, client_id: int | None, client_name: str | None) -> None:
        self.client_id = client_id
        self.client_name = client_name
        super().__init__("Właściciel urządzenia zmienił się podczas operacji.")


@dataclass(slots=True)
class WorkflowDeviceSourceContext:
    """Znormalizowany kontekst źródłowy urządzenia workflow."""

    source_type: str
    source_row: int | None
    producer: str | None
    model: str | None
    serial: str | None
    ewidencja: str | None
    raw_name: str | None = None
    warehouse_model_id: int | None = None
    warehouse_index: str | None = None


@dataclass(slots=True)
class _PreparedWorkflowDeviceBinding:
    device: FormWorkflowDevice
    snapshot: dict[str, Any]
    source_context: WorkflowDeviceSourceContext
    machine_id: int | None
    machine_model_id: int | None
    expected_client_id: int | None
    ownership_state: str


def _truncate_text(value: Any, max_length: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:max_length]


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_bool_tak(value: Any) -> bool:
    token = str(value or "").strip().upper()
    return token in {"1", "T", "TAK", "Y", "YES", "TRUE"}


def _normalize_kp_grenke_ewidencja(value: Any) -> tuple[str | None, str | None]:
    """Normalizuje EWIDENCJA do formatu KP/<numer>/GRENKE/<reszta>."""
    raw = str(value or "").strip()
    if not raw:
        return None, "Brak pola EWIDENCJA w rekordzie MASZYNA."

    parts = [part.strip() for part in raw.split("/") if part and part.strip()]
    if len(parts) < 2 or parts[0].upper() != "KP":
        return None, "Pole EWIDENCJA nie ma formatu KP/<numer>/..."

    number_part = parts[1]
    if not number_part:
        return None, "Pole EWIDENCJA nie zawiera numeru po prefiksie KP."

    tail = [part for part in parts[2:] if part.upper() != "GRENKE"]
    normalized = "/".join(["KP", number_part, "GRENKE", *tail])
    return normalized, None


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return None


def _build_bound_device_label(device: FormWorkflowDevice, snapshot: dict[str, Any]) -> str:
    producer = _first_non_empty(snapshot.get("producer"), device.producer)
    model = _first_non_empty(snapshot.get("model"), snapshot.get("name"), device.model)
    serial = _first_non_empty(snapshot.get("serial"), device.serial)
    base = " ".join(part for part in [producer, model] if part).strip() or "Urządzenie"
    if serial:
        return f"{base} | Nr seryjny: {serial}"
    return base


def _build_binding_error_label(device: FormWorkflowDevice, snapshot: dict[str, Any]) -> str:
    producer = _first_non_empty(snapshot.get("producer"), device.producer)
    model = _first_non_empty(snapshot.get("model"), snapshot.get("name"), device.model)
    serial = _first_non_empty(snapshot.get("serial"), device.serial)
    ewidencja = _first_non_empty(snapshot.get("ewidencja"), snapshot.get("index"), device.ewidencja)

    base = " ".join(part for part in [producer, model] if part).strip() or "Urządzenie"
    if serial:
        return f"{base} | Nr seryjny: {serial}"
    if ewidencja:
        return f"{base} | Ewidencja: {ewidencja}"
    if device.source_row is not None:
        return f"{base} | Wiersz: {device.source_row}"
    return base


def _format_binding_error_message(device: FormWorkflowDevice, snapshot: dict[str, Any]) -> str:
    detail = (
        str(snapshot.get("ms_binding_message") or "").strip()
        or "Nieznany błąd wiązania urządzenia."
    )
    label = _build_binding_error_label(device, snapshot)
    return f"{label}: {detail}"


def _build_binding_error_summary(errors: list[str], *, max_items: int = 2) -> str:
    if not errors:
        return "Brak szczegółów błędów."

    preview = errors[:max_items]
    if len(preview) == 1:
        summary = f"Błąd: {preview[0]}"
    else:
        summary = "Błędy: " + "; ".join(
            f"{index}. {message}" for index, message in enumerate(preview, start=1)
        )
    remaining = len(errors) - len(preview)
    if remaining > 0:
        summary = f"{summary}; +{remaining} kolejnych."
    return summary


def _fetch_warehouse_row(cursor, source_row: int) -> tuple[Any, ...] | None:
    cursor.execute(
        """
        SELECT FIRST 1
            ID_MAGAZYN_TABLE,
            ID_MAGAZYN,
            INDEKS,
            NAZWA,
            MARKA,
            MODEL,
            SERIAL,
            ID_MODEL
        FROM MAGAZYN
        WHERE ID_MAGAZYN_TABLE = ?
        """,
        (source_row,),
    )
    return cursor.fetchone()


def _resolve_source_context(
    cursor,
    *,
    device: FormWorkflowDevice,
    snapshot: dict[str, Any],
) -> WorkflowDeviceSourceContext:
    warehouse_row = None
    if device.source_type == "firebird_magazyn_28" and device.source_row is not None:
        warehouse_row = _fetch_warehouse_row(cursor, int(device.source_row))

    warehouse_index = _truncate_text(warehouse_row[2], 100) if warehouse_row else None
    warehouse_name = _truncate_text(warehouse_row[3], 250) if warehouse_row else None
    warehouse_producer = _truncate_text(warehouse_row[4], 50) if warehouse_row else None
    warehouse_model = _truncate_text(warehouse_row[5], 50) if warehouse_row else None
    warehouse_model_id = _coerce_int(warehouse_row[7] if warehouse_row else None)
    if warehouse_model_id is None:
        warehouse_model_id = _coerce_int(snapshot.get("ms_id_model"))

    snapshot_name = _first_non_empty(snapshot.get("name"), snapshot.get("description"))
    parsed_snapshot = extract_stock_device_identity(
        snapshot_name,
        index_value=_first_non_empty(
            snapshot.get("index"), snapshot.get("ewidencja"), device.ewidencja
        ),
        producer=_first_non_empty(snapshot.get("producer"), device.producer),
        model=_first_non_empty(snapshot.get("model"), device.model),
    )
    parsed_warehouse = extract_stock_device_identity(
        warehouse_name,
        index_value=warehouse_index,
        producer=warehouse_producer,
        model=warehouse_model,
    )

    producer = _first_non_empty(
        snapshot.get("producer"),
        parsed_snapshot.get("producer"),
        warehouse_producer,
        parsed_warehouse.get("producer"),
        device.producer,
    )
    model = _first_non_empty(
        parsed_snapshot.get("model"),
        parsed_warehouse.get("model"),
        warehouse_model,
        snapshot.get("model"),
        device.model,
        snapshot_name,
    )
    serial = _first_non_empty(
        snapshot.get("serial"),
        device.serial,
        parsed_snapshot.get("serial"),
        parsed_warehouse.get("serial"),
    )
    ewidencja = _first_non_empty(
        snapshot.get("ewidencja"),
        snapshot.get("index"),
        device.ewidencja,
        parsed_snapshot.get("ewidencja"),
        parsed_warehouse.get("ewidencja"),
        warehouse_index,
    )

    return WorkflowDeviceSourceContext(
        source_type=device.source_type,
        source_row=device.source_row,
        producer=_truncate_text(producer, 50),
        model=_truncate_text(model, 100),
        serial=_truncate_text(serial, 100),
        ewidencja=_truncate_text(ewidencja, 100),
        raw_name=warehouse_name or snapshot_name,
        warehouse_model_id=warehouse_model_id,
        warehouse_index=warehouse_index,
    )


def _get_firebird_connection():
    # Import lokalny, aby nie wymuszać zależności przy samym imporcie modułu.
    from app.services.contracts_dashboard import _firebird_connection  # noqa: PLC2701

    return _firebird_connection()


def _fetch_machine_row(cursor, machine_id: int) -> tuple[Any, ...] | None:
    cursor.execute(
        """
        SELECT FIRST 1
            maszyna.ID_MASZYNA,
            maszyna.ID_KLIENT,
            maszyna.EWIDENCJA,
            maszyna.AKTYWNA,
            maszyna.SYNWP,
            maszyna.SERIAL,
            maszyna.ID_MODEL,
            maszyna.MARKA,
            maszyna.MODEL,
            maszyna.GRUPA,
            maszyna.TYP,
            maszyna.RODZAJ_US,
            maszyna.KOLOROWA,
            klient.NAZWA
        FROM MASZYNA maszyna
        LEFT JOIN KLIENT klient ON klient.ID_KLIENT = maszyna.ID_KLIENT
        WHERE maszyna.ID_MASZYNA = ?
        """,
        (machine_id,),
    )
    return cursor.fetchone()


def _fetch_machine_query_rows(cursor) -> list[tuple[Any, ...]]:
    fetchall = getattr(cursor, "fetchall", None)
    if callable(fetchall):
        return list(fetchall())
    row = cursor.fetchone()
    return [row] if row is not None else []


def _find_machine_rows_by_serial(cursor, serial: str | None) -> list[tuple[Any, ...]]:
    serial_key = normalize_device_key(serial)
    if not serial_key:
        return []
    cursor.execute(
        """
        SELECT FIRST 2
            maszyna.ID_MASZYNA,
            maszyna.ID_KLIENT,
            maszyna.EWIDENCJA,
            maszyna.AKTYWNA,
            maszyna.SYNWP,
            maszyna.SERIAL,
            maszyna.ID_MODEL,
            maszyna.MARKA,
            maszyna.MODEL,
            maszyna.GRUPA,
            maszyna.TYP,
            maszyna.RODZAJ_US,
            maszyna.KOLOROWA,
            klient.NAZWA
        FROM MASZYNA maszyna
        LEFT JOIN KLIENT klient ON klient.ID_KLIENT = maszyna.ID_KLIENT
        WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(maszyna.SERIAL, ''), '/', ''), '-', ''), ' ', '')) = ?
           OR UPPER(REPLACE(REPLACE(REPLACE(COALESCE(maszyna.SERIAL2, ''), '/', ''), '-', ''), ' ', '')) = ?
        ORDER BY maszyna.ID_MASZYNA DESC
        """,
        (serial_key, serial_key),
    )
    return _fetch_machine_query_rows(cursor)


def _find_machine_rows_by_ewidencja(
    cursor,
    ewidencja: str | None,
) -> list[tuple[Any, ...]]:
    ewidencja_key = normalize_device_key(ewidencja)
    if not ewidencja_key:
        return []
    cursor.execute(
        """
        SELECT FIRST 2
            maszyna.ID_MASZYNA,
            maszyna.ID_KLIENT,
            maszyna.EWIDENCJA,
            maszyna.AKTYWNA,
            maszyna.SYNWP,
            maszyna.SERIAL,
            maszyna.ID_MODEL,
            maszyna.MARKA,
            maszyna.MODEL,
            maszyna.GRUPA,
            maszyna.TYP,
            maszyna.RODZAJ_US,
            maszyna.KOLOROWA,
            klient.NAZWA
        FROM MASZYNA maszyna
        LEFT JOIN KLIENT klient ON klient.ID_KLIENT = maszyna.ID_KLIENT
        WHERE UPPER(REPLACE(REPLACE(REPLACE(COALESCE(maszyna.EWIDENCJA, ''), '/', ''), '-', ''), ' ', '')) = ?
        ORDER BY maszyna.ID_MASZYNA DESC
        """,
        (ewidencja_key,),
    )
    return _fetch_machine_query_rows(cursor)


def _resolve_model_match_for_device(
    *,
    source_context: WorkflowDeviceSourceContext,
    machine_model_id: int | None = None,
) -> tuple[Any, str]:
    """Dopasowuje MODEL, korzystając bezpiecznie z danych MAGAZYN i MASZYNA."""
    explicit_model_id = source_context.warehouse_model_id
    if explicit_model_id is not None and explicit_model_id > 0:
        model_match = find_model_in_firebird_by_id(explicit_model_id)
        if model_match.error:
            raise RuntimeError(
                f"Nie udalo sie odczytac MODEL.ID_MODEL={explicit_model_id}: {model_match.error}"
            )
        if model_match.found and model_match.id_model is not None:
            return model_match, "id_model"

    if (
        machine_model_id is not None
        and machine_model_id > 0
        and machine_model_id != explicit_model_id
    ):
        model_match = find_model_in_firebird_by_id(machine_model_id)
        if model_match.error:
            raise RuntimeError(
                f"Nie udalo sie odczytac MODEL dla MASZYNA.ID_MODEL={machine_model_id}: "
                f"{model_match.error}"
            )
        if model_match.found and model_match.id_model is not None:
            return model_match, "machine_id_model"

    model_match = find_model_in_firebird(source_context.model or source_context.raw_name)
    if model_match.error:
        raise RuntimeError(f"Nie udalo sie odczytac modelu Firebird: {model_match.error}")
    if model_match.found and model_match.id_model is not None:
        return model_match, "name"

    if explicit_model_id is not None and explicit_model_id > 0:
        raise RuntimeError(
            f"Nie znaleziono modelu w tabeli MODEL dla ID_MODEL={explicit_model_id}."
        )
    if machine_model_id is not None and machine_model_id > 0:
        raise RuntimeError(
            f"Nie znaleziono modelu w tabeli MODEL dla MASZYNA.ID_MODEL={machine_model_id}."
        )
    raise RuntimeError("Nie znaleziono modelu w tabeli MODEL dla wybranego urządzenia.")


def _resolve_machine_id_for_device(
    cursor,
    *,
    device: FormWorkflowDevice,
    snapshot: dict[str, Any],
    source_context: WorkflowDeviceSourceContext,
) -> list[int]:
    explicit = _coerce_int(snapshot.get("ms_id_maszyna"))
    if explicit is not None and explicit > 0:
        return [explicit]
    if device.firebird_machine_id is not None and int(device.firebird_machine_id) > 0:
        return [int(device.firebird_machine_id)]

    candidate_rows = _find_machine_rows_by_serial(cursor, source_context.serial)
    ewidencja_rows = _find_machine_rows_by_ewidencja(cursor, source_context.ewidencja)
    for machine_row in ewidencja_rows:
        current_serial = _truncate_text(machine_row[5], 100)
        if (
            source_context.serial
            and current_serial
            and normalize_device_key(current_serial) != normalize_device_key(source_context.serial)
        ):
            continue
        candidate_rows.append(machine_row)

    return sorted(
        {
            machine_id
            for machine_id in (_coerce_int(row[0]) for row in candidate_rows)
            if machine_id is not None and machine_id > 0
        }
    )


def _validate_workflow_device_ownership_with_cursor(
    cursor,
    *,
    workflow_case: FormWorkflowCase,
    devices: list[FormWorkflowDevice],
) -> list[_PreparedWorkflowDeviceBinding]:
    """Sprawdza pakiet i rozpoznaje bezpieczne ponowienie częściowego wiązania."""
    prepared: list[_PreparedWorkflowDeviceBinding] = []
    conflicts: list[WorkflowDeviceOwnershipConflictItem] = []
    target_client_id = _coerce_int(workflow_case.firebird_client_id)

    for device in devices:
        snapshot = dict(device.snapshot or {})
        source_context = _resolve_source_context(
            cursor,
            device=device,
            snapshot=snapshot,
        )
        machine_ids = _resolve_machine_id_for_device(
            cursor,
            device=device,
            snapshot=snapshot,
            source_context=source_context,
        )
        if len(machine_ids) > 1:
            ownership = classify_workflow_machine_ownership(
                candidate_count=len(machine_ids),
                machine_id=None,
                client_id=None,
                client_name=None,
                warehouse_client_id=settings.fb_warehouse_client_id,
            )
            conflicts.append(
                WorkflowDeviceOwnershipConflictItem(
                    workflow_device_id=device.id,
                    source_row=device.source_row,
                    machine_id=None,
                    current_client_id=None,
                    current_client_name=None,
                    reason=ownership.reason,
                )
            )
            continue
        machine_id = machine_ids[0] if machine_ids else None
        if machine_id is None:
            prepared.append(
                _PreparedWorkflowDeviceBinding(
                    device=device,
                    snapshot=snapshot,
                    source_context=source_context,
                    machine_id=None,
                    machine_model_id=None,
                    expected_client_id=None,
                    ownership_state=MACHINE_MATCH_MISSING,
                )
            )
            continue

        current_row = _fetch_machine_row(cursor, machine_id)
        current_client_id = _coerce_int(current_row[1] if current_row else None)
        current_client_name = (
            _truncate_text(current_row[13], 250)
            if current_row is not None and len(current_row) > 13
            else None
        )
        ownership = classify_workflow_machine_ownership(
            candidate_count=1 if current_row is not None else 0,
            machine_id=machine_id if current_row is not None else None,
            client_id=current_client_id,
            client_name=current_client_name,
            warehouse_client_id=settings.fb_warehouse_client_id,
            target_client_id=target_client_id,
        )
        idempotent_binding = bool(
            current_client_id == target_client_id
            and snapshot_confirms_current_workflow_binding(
                snapshot,
                machine_id=machine_id,
                client_id=current_client_id,
            )
        )
        if ownership.conflict and not idempotent_binding:
            conflicts.append(
                WorkflowDeviceOwnershipConflictItem(
                    workflow_device_id=device.id,
                    source_row=device.source_row,
                    machine_id=machine_id,
                    current_client_id=current_client_id,
                    current_client_name=current_client_name,
                    reason=ownership.reason,
                )
            )
            continue

        prepared.append(
            _PreparedWorkflowDeviceBinding(
                device=device,
                snapshot=snapshot,
                source_context=source_context,
                machine_id=machine_id,
                machine_model_id=_coerce_int(current_row[6] if current_row else None),
                expected_client_id=current_client_id,
                ownership_state=(
                    MACHINE_MATCH_BOUND_CURRENT_WORKFLOW if idempotent_binding else ownership.state
                ),
            )
        )

    if conflicts:
        raise WorkflowDeviceOwnershipConflict(conflicts)
    ownership_states = {item.ownership_state for item in prepared}
    if classify_workflow_ownership_batch(ownership_states) == MACHINE_BATCH_MIXED_HOLD:
        mixed_items = [
            WorkflowDeviceOwnershipConflictItem(
                workflow_device_id=item.device.id,
                source_row=item.device.source_row,
                machine_id=item.machine_id,
                current_client_id=item.expected_client_id,
                current_client_name=None,
                reason=(
                    "urządzenie jest już u klienta docelowego"
                    if item.ownership_state == MACHINE_MATCH_TARGET
                    else "urządzenie pozostaje na magazynie"
                ),
            )
            for item in prepared
            if item.ownership_state in {MACHINE_MATCH_TARGET, MACHINE_MATCH_WAREHOUSE}
        ]
        raise WorkflowDeviceMixedOwnershipHold(mixed_items)
    return prepared


async def validate_no_active_workflow_device_duplicates(
    session: AsyncSession,
    *,
    workflow_case: FormWorkflowCase,
    devices: list[FormWorkflowDevice],
) -> None:
    """Blokuje urządzenie użyte równolegle w innej aktywnej sprawie FLOW."""
    if not devices:
        return
    rows = (
        await session.execute(
            select(FormWorkflowDevice, FormWorkflowCase)
            .join(FormWorkflowCase, FormWorkflowCase.id == FormWorkflowDevice.workflow_case_id)
            .join(FormRequest, FormRequest.id == FormWorkflowCase.form_request_id)
            .where(
                FormWorkflowCase.id != workflow_case.id,
                FormRequest.status == "SUBMITTED",
                FormRequest.archive_bucket.is_(None),
            )
        )
    ).all()
    selected_keys = {
        (str(device.source_type or ""), int(device.source_row))
        for device in devices
        if device.source_row is not None
    }
    selected_serials = {
        normalize_device_key(device.serial)
        for device in devices
        if normalize_device_key(device.serial)
    }
    selected_indexes = {
        normalize_device_key(device.ewidencja)
        for device in devices
        if normalize_device_key(device.ewidencja)
    }
    conflicts: list[WorkflowDeviceOwnershipConflictItem] = []
    for other_device, other_case in rows:
        other_key = (
            str(other_device.source_type or ""),
            int(other_device.source_row) if other_device.source_row is not None else None,
        )
        serial_key = normalize_device_key(other_device.serial)
        index_key = normalize_device_key(other_device.ewidencja)
        if not (
            other_key in selected_keys
            or (serial_key and serial_key in selected_serials)
            or (index_key and index_key in selected_indexes)
        ):
            continue
        conflicts.append(
            WorkflowDeviceOwnershipConflictItem(
                workflow_device_id=other_device.id,
                source_row=other_device.source_row,
                machine_id=other_device.firebird_machine_id,
                current_client_id=other_device.firebird_client_id,
                current_client_name=None,
                reason=f"urządzenie jest używane w aktywnej sprawie FLOW {other_case.id}",
            )
        )
    if conflicts:
        raise WorkflowDeviceOwnershipConflict(conflicts)


def validate_workflow_device_ownership(
    *,
    workflow_case: FormWorkflowCase,
    devices: list[FormWorkflowDevice],
) -> None:
    """Weryfikuje właścicieli urządzeń bez wykonywania zapisów w Firebird."""
    if not devices:
        return
    enabled, _ = firebird_writes_enabled()
    if not enabled:
        return
    connection = _get_firebird_connection()
    cursor = connection.cursor()
    try:
        _validate_workflow_device_ownership_with_cursor(
            cursor,
            workflow_case=workflow_case,
            devices=devices,
        )
    finally:
        connection.rollback()
        cursor.close()
        connection.close()


def _create_machine_for_device(
    cursor,
    *,
    workflow_case: FormWorkflowCase,
    device: FormWorkflowDevice,
    snapshot: dict[str, Any],
    actor_label: str,
    model_match: Any | None = None,
    source_context: WorkflowDeviceSourceContext | None = None,
) -> int:
    resolved_model_match = model_match
    if resolved_model_match is None:
        effective_context = source_context or WorkflowDeviceSourceContext(
            source_type=getattr(device, "source_type", "google_sheet"),
            source_row=getattr(device, "source_row", None),
            producer=_first_non_empty(snapshot.get("producer"), device.producer),
            model=_first_non_empty(snapshot.get("model"), device.model, snapshot.get("name")),
            serial=_first_non_empty(snapshot.get("serial"), device.serial),
            ewidencja=_first_non_empty(
                snapshot.get("ewidencja"),
                snapshot.get("index"),
                device.ewidencja,
            ),
            raw_name=_first_non_empty(snapshot.get("name"), snapshot.get("description")),
            warehouse_model_id=_coerce_int(snapshot.get("ms_id_model")),
        )
        resolved_model_match, _ = _resolve_model_match_for_device(source_context=effective_context)

    effective_context = source_context or WorkflowDeviceSourceContext(
        source_type=getattr(device, "source_type", "google_sheet"),
        source_row=getattr(device, "source_row", None),
        producer=_first_non_empty(snapshot.get("producer"), device.producer),
        model=_first_non_empty(snapshot.get("model"), device.model, snapshot.get("name")),
        serial=_first_non_empty(snapshot.get("serial"), device.serial),
        ewidencja=_first_non_empty(
            snapshot.get("ewidencja"), snapshot.get("index"), device.ewidencja
        ),
        raw_name=_first_non_empty(snapshot.get("name"), snapshot.get("description")),
        warehouse_model_id=_coerce_int(snapshot.get("ms_id_model")),
    )

    serial = effective_context.serial
    ewidencja = effective_context.ewidencja
    if not serial and not ewidencja:
        raise RuntimeError("Brak serialu i ewidencji, nie można utworzyć rekordu MASZYNA.")

    model_name = _first_non_empty(
        effective_context.model, snapshot.get("model"), device.model, snapshot.get("name")
    )
    marka_value = _truncate_text(
        resolved_model_match.marka or effective_context.producer or device.producer, 100
    )
    model_value = _truncate_text(resolved_model_match.model or model_name, 100)
    grupa_value = _truncate_text(resolved_model_match.grupa or DEFAULT_MACHINE_GROUP, 50)
    service_kind = _truncate_text(resolved_model_match.rodzaj or DEFAULT_MACHINE_SERVICE_KIND, 50)
    kolorowa_value = "TAK" if _normalize_bool_tak(resolved_model_match.kolor) else "NIE"

    cursor.execute(
        """
        INSERT INTO MASZYNA (
            ID_ODDZIAL,
            ID_FIRMA,
            ID_KLIENT,
            ID_MODEL,
            MARKA,
            MODEL,
            GRUPA,
            SERIAL,
            SERIAL2,
            EWIDENCJA,
            STOI,
            AKTYWNA,
            KOLOROWA,
            SYNWP,
            V_2010A,
            TYP,
            TECHNIK,
            RODZAJ_US,
            IDVAT,
            UWAGI
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING ID_MASZYNA
        """,
        (
            1,
            1,
            int(workflow_case.firebird_client_id),
            resolved_model_match.id_model,
            marka_value,
            model_value,
            grupa_value,
            _truncate_text(serial, 100),
            None,
            _truncate_text(ewidencja, 100),
            _truncate_text(DEFAULT_MACHINE_PLACE, 1000),
            "TAK",
            kolorowa_value,
            1,
            "TAK",
            service_kind,
            _truncate_text(actor_label, 100),
            service_kind,
            DEFAULT_MACHINE_IDVAT,
            _truncate_text(f"CTIP FLOW {workflow_case.form_request_id} / {actor_label}", 250),
        ),
    )
    row = cursor.fetchone()
    machine_id = _coerce_int(row[0] if row else None)
    if machine_id is None:
        raise RuntimeError("Nie udało się ustalić ID nowo utworzonej maszyny.")
    return machine_id


def _same_text(left: str | None, right: str | None) -> bool:
    left_value = " ".join(str(left or "").split()).upper()
    right_value = " ".join(str(right or "").split()).upper()
    return left_value == right_value


def _apply_machine_updates(
    cursor,
    *,
    machine_id: int,
    target_client_id: int,
    target_ewidencja: str | None,
    target_serial: str | None,
    target_model_match: Any | None,
    expected_client_id: int | None,
) -> tuple[int | None, str | None, bool, bool]:
    row = _fetch_machine_row(cursor, machine_id)
    if row is None:
        raise RuntimeError(f"Nie znaleziono rekordu MASZYNA ID {machine_id}.")

    previous_client_id = _coerce_int(row[1])
    current_client_name = _truncate_text(row[13], 250) if len(row) > 13 else None
    if previous_client_id != expected_client_id:
        raise _WorkflowMachineOwnerChanged(
            client_id=previous_client_id,
            client_name=current_client_name,
        )
    previous_ewidencja = _truncate_text(row[2], 100)
    aktywna = row[3]
    synwp = row[4]
    current_serial = _truncate_text(row[5], 100)
    current_id_model = _coerce_int(row[6])
    current_marka = _truncate_text(row[7], 100)
    current_model = _truncate_text(row[8], 100)
    current_grupa = _truncate_text(row[9], 50)
    current_typ = _truncate_text(row[10], 50)
    current_rodzaj_us = _truncate_text(row[11], 50)
    current_kolorowa = _truncate_text(row[12], 3)

    updates: list[str] = []
    params: list[Any] = []
    model_enriched = False

    if previous_client_id != target_client_id:
        updates.append("ID_KLIENT = ?")
        params.append(target_client_id)

    if target_ewidencja and previous_ewidencja != target_ewidencja:
        updates.append("EWIDENCJA = ?")
        params.append(target_ewidencja)

    if target_serial and not current_serial:
        updates.append("SERIAL = ?")
        params.append(target_serial)

    if not _normalize_bool_tak(aktywna):
        updates.append("AKTYWNA = ?")
        params.append("TAK")

    if _coerce_int(synwp) != 1:
        updates.append("SYNWP = ?")
        params.append(1)

    if target_model_match is not None and target_model_match.id_model is not None:
        desired_marka = _truncate_text(target_model_match.marka, 100)
        desired_model = _truncate_text(target_model_match.model, 100)
        desired_grupa = _truncate_text(target_model_match.grupa or DEFAULT_MACHINE_GROUP, 50)
        desired_kind = _truncate_text(target_model_match.rodzaj or DEFAULT_MACHINE_SERVICE_KIND, 50)
        desired_kolorowa = "TAK" if _normalize_bool_tak(target_model_match.kolor) else "NIE"

        if current_id_model != int(target_model_match.id_model):
            updates.append("ID_MODEL = ?")
            params.append(int(target_model_match.id_model))
            model_enriched = True
        if desired_marka and not _same_text(current_marka, desired_marka):
            updates.append("MARKA = ?")
            params.append(desired_marka)
            model_enriched = True
        if desired_model and not _same_text(current_model, desired_model):
            updates.append("MODEL = ?")
            params.append(desired_model)
            model_enriched = True
        if desired_grupa and not _same_text(current_grupa, desired_grupa):
            updates.append("GRUPA = ?")
            params.append(desired_grupa)
            model_enriched = True
        if desired_kind and not _same_text(current_typ, desired_kind):
            updates.append("TYP = ?")
            params.append(desired_kind)
            model_enriched = True
        if desired_kind and not _same_text(current_rodzaj_us, desired_kind):
            updates.append("RODZAJ_US = ?")
            params.append(desired_kind)
            model_enriched = True
        if desired_kolorowa and not _same_text(current_kolorowa, desired_kolorowa):
            updates.append("KOLOROWA = ?")
            params.append(desired_kolorowa)
            model_enriched = True

    if updates:
        params.extend((machine_id, expected_client_id))
        cursor.execute(
            (f"UPDATE MASZYNA SET {', '.join(updates)} " "WHERE ID_MASZYNA = ? AND ID_KLIENT = ?"),
            tuple(params),
        )
        if getattr(cursor, "rowcount", None) == 0:
            current_row = _fetch_machine_row(cursor, machine_id)
            raise _WorkflowMachineOwnerChanged(
                client_id=_coerce_int(current_row[1] if current_row else None),
                client_name=(
                    _truncate_text(current_row[13], 250)
                    if current_row is not None and len(current_row) > 13
                    else None
                ),
            )

    ewidencja_changed = bool(target_ewidencja and previous_ewidencja != target_ewidencja)
    return previous_client_id, previous_ewidencja, ewidencja_changed, model_enriched


def bind_devices_to_workflow_client(
    *,
    workflow_case: FormWorkflowCase,
    devices: list[FormWorkflowDevice],
    actor_label: str,
) -> tuple[list[WorkflowDeviceBindingItem], list[str]]:
    """Wiąże urządzenia workflow z klientem sprawy i aktualizuje rekordy MASZYNA."""
    if workflow_case.firebird_client_id is None:
        message = "Brak ID klienta MS w sprawie workflow."
        return (
            [
                WorkflowDeviceBindingItem(
                    workflow_device_id=device.id,
                    source_row=device.source_row,
                    source_type=device.source_type,
                    ok=False,
                    message=message,
                    producer=device.producer,
                    model=device.model,
                    serial=device.serial,
                )
                for device in devices
            ],
            [message],
        )

    if not devices:
        return ([], [])

    enabled, reason = firebird_writes_enabled()
    if not enabled:
        message = reason or "Zapis do Firebird jest zablokowany."
        return (
            [
                WorkflowDeviceBindingItem(
                    workflow_device_id=device.id,
                    source_row=device.source_row,
                    source_type=device.source_type,
                    ok=False,
                    message=message,
                    producer=device.producer,
                    model=device.model,
                    serial=device.serial,
                )
                for device in devices
            ],
            [message],
        )

    connection = _get_firebird_connection()
    cursor = connection.cursor()
    items: list[WorkflowDeviceBindingItem] = []
    global_errors: list[str] = []

    try:
        prepared_devices = _validate_workflow_device_ownership_with_cursor(
            cursor,
            workflow_case=workflow_case,
            devices=devices,
        )
        for prepared in prepared_devices:
            device = prepared.device
            snapshot = prepared.snapshot
            source_context = prepared.source_context
            try:
                if (
                    prepared.ownership_state
                    in {
                        MACHINE_MATCH_BOUND_CURRENT_WORKFLOW,
                        MACHINE_MATCH_TARGET,
                    }
                    and prepared.machine_id
                ):
                    current_row = _fetch_machine_row(cursor, prepared.machine_id)
                    items.append(
                        WorkflowDeviceBindingItem(
                            workflow_device_id=device.id,
                            source_row=device.source_row,
                            source_type=device.source_type,
                            ok=True,
                            message=(
                                "Urządzenie było już przypisane do klienta docelowego; "
                                "nie wykonano zapisu właściciela w MS."
                            ),
                            producer=(
                                _truncate_text(current_row[7], 100)
                                if current_row
                                else device.producer
                            ),
                            model=(
                                _truncate_text(current_row[8], 100) if current_row else device.model
                            ),
                            serial=(
                                _truncate_text(current_row[5], 100)
                                if current_row
                                else device.serial
                            ),
                            machine_id=prepared.machine_id,
                            previous_client_id=prepared.expected_client_id,
                            current_client_id=prepared.expected_client_id,
                            previous_ewidencja=(
                                _truncate_text(current_row[2], 100)
                                if current_row
                                else device.ewidencja
                            ),
                            current_ewidencja=(
                                _truncate_text(current_row[2], 100)
                                if current_row
                                else device.ewidencja
                            ),
                            ewidencja_changed=False,
                        )
                    )
                    continue
                model_match, model_match_source = _resolve_model_match_for_device(
                    source_context=source_context,
                    machine_model_id=prepared.machine_model_id,
                )
                machine_id = prepared.machine_id
                machine_created = False
                if machine_id is None:
                    machine_id = _create_machine_for_device(
                        cursor,
                        workflow_case=workflow_case,
                        device=device,
                        snapshot=snapshot,
                        actor_label=actor_label,
                        model_match=model_match,
                        source_context=source_context,
                    )
                    machine_created = True
                    expected_client_id = int(workflow_case.firebird_client_id)
                else:
                    expected_client_id = prepared.expected_client_id

                current_row = _fetch_machine_row(cursor, machine_id)
                if current_row is None:
                    raise RuntimeError(f"Nie znaleziono rekordu MASZYNA ID {machine_id}.")
                current_ewidencja = _first_non_empty(
                    current_row[2],
                    source_context.ewidencja,
                    snapshot.get("ewidencja"),
                    device.ewidencja,
                )
                normalized_ewidencja, ewidencja_error = _normalize_kp_grenke_ewidencja(
                    current_ewidencja
                )
                previous_client_id, previous_ewidencja, changed, model_enriched = (
                    _apply_machine_updates(
                        cursor,
                        machine_id=machine_id,
                        target_client_id=int(workflow_case.firebird_client_id),
                        target_ewidencja=normalized_ewidencja if not ewidencja_error else None,
                        target_serial=source_context.serial,
                        target_model_match=model_match,
                        expected_client_id=expected_client_id,
                    )
                )

                message = "Powiązano urządzenie z klientem MS."
                if machine_created:
                    message = "Utworzono i powiązano urządzenie z klientem MS."
                elif model_enriched:
                    message = "Powiązano urządzenie z klientem MS i zsynchronizowano dane MODEL."
                if model_match_source == "id_model":
                    message = f"{message} Model dopasowano po ID_MODEL."
                elif model_match_source == "machine_id_model":
                    message = f"{message} Model dopasowano po MASZYNA.ID_MODEL."
                if ewidencja_error:
                    message = (
                        f"{message} Pominięto normalizację EWIDENCJA "
                        f"({current_ewidencja or 'brak'}): {ewidencja_error}"
                    )

                resolved_ewidencja = (
                    normalized_ewidencja
                    if normalized_ewidencja
                    else _truncate_text(previous_ewidencja or current_ewidencja, 100)
                )

                items.append(
                    WorkflowDeviceBindingItem(
                        workflow_device_id=device.id,
                        source_row=device.source_row,
                        source_type=device.source_type,
                        ok=True,
                        message=message,
                        producer=model_match.marka or source_context.producer or device.producer,
                        model=model_match.model or source_context.model or device.model,
                        serial=source_context.serial or device.serial,
                        machine_id=machine_id,
                        previous_client_id=previous_client_id,
                        current_client_id=int(workflow_case.firebird_client_id),
                        previous_ewidencja=previous_ewidencja,
                        current_ewidencja=resolved_ewidencja,
                        ewidencja_changed=changed,
                    )
                )
            except _WorkflowMachineOwnerChanged as exc:
                ownership = classify_workflow_machine_ownership(
                    candidate_count=1,
                    machine_id=machine_id,
                    client_id=exc.client_id,
                    client_name=exc.client_name,
                    warehouse_client_id=settings.fb_warehouse_client_id,
                )
                raise WorkflowDeviceOwnershipConflict(
                    [
                        WorkflowDeviceOwnershipConflictItem(
                            workflow_device_id=device.id,
                            source_row=device.source_row,
                            machine_id=machine_id,
                            current_client_id=exc.client_id,
                            current_client_name=exc.client_name,
                            reason=ownership.reason or str(exc),
                        )
                    ]
                ) from exc
            except Exception as exc:  # noqa: BLE001
                items.append(
                    WorkflowDeviceBindingItem(
                        workflow_device_id=device.id,
                        source_row=device.source_row,
                        source_type=device.source_type,
                        ok=False,
                        message=str(exc),
                        producer=source_context.producer if source_context else device.producer,
                        model=source_context.model if source_context else device.model,
                        serial=source_context.serial if source_context else device.serial,
                    )
                )
    except WorkflowDeviceOwnershipConflict:
        connection.rollback()
        raise
    except Exception as exc:  # noqa: BLE001
        connection.rollback()
        raise RuntimeError(f"Błąd wiązania urządzeń workflow z klientem: {exc}") from exc
    else:
        connection.commit()
    finally:
        cursor.close()
        connection.close()

    for item in items:
        if not item.ok:
            global_errors.append(item.message)
    return items, global_errors


async def _load_active_admin_recipients(
    session: AsyncSession,
) -> list[AdminUser]:
    result = await session.execute(
        select(AdminUser)
        .where(AdminUser.role == "admin", AdminUser.is_active.is_(True))
        .order_by(AdminUser.id.asc())
    )
    return list(result.scalars())


async def notify_binding_issues_to_admins(
    session: AsyncSession,
    *,
    workflow_case: FormWorkflowCase,
    form_request_id: int,
    failures: list[WorkflowDeviceBindingItem],
    triggered_by_user_id: int | None,
) -> dict[str, Any]:
    """Wysyła alerty SMS/e-mail do aktywnych administratorów o problemach wiązania."""
    if not failures:
        return {"sent": False, "sms_queued": 0, "email_sent": 0, "recipients": 0}

    recipients = await _load_active_admin_recipients(session)
    if not recipients:
        return {"sent": False, "sms_queued": 0, "email_sent": 0, "recipients": 0}

    header = f"[CTIP] Błąd wiązania urządzeń FLOW (formularz {form_request_id}, sprawa {workflow_case.id})"
    lines: list[str] = []
    for idx, failure in enumerate(failures, start=1):
        lines.append(
            f"{idx}. urz.#{failure.workflow_device_id} row={failure.source_row or '-'}: {failure.message}"
        )
    plain_text = header + "\n" + "\n".join(lines)

    sms_queued = 0
    for recipient in recipients:
        if not recipient.mobile_phone:
            continue
        sms = SmsOut(
            dest=recipient.mobile_phone,
            text=plain_text[:600],
            source="admin",
            origin="workflow_device_binding_alert",
            status="NEW",
            created_by=triggered_by_user_id,
            meta={
                "type": "workflow_device_binding_alert",
                "workflow_case_id": workflow_case.id,
                "form_request_id": form_request_id,
                "failure_count": len(failures),
            },
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
        session.add(sms)
        sms_queued += 1

    email_delivery = await admin_users.resolve_email_delivery_settings(session)
    email_sent = 0
    if email_delivery is not None:
        sender_name = (email_delivery.sender_name or "").strip() or "CTIP Administrator"
        sender_address = (email_delivery.sender_address or "").strip()
        if sender_address:
            for recipient in recipients:
                target = (recipient.email or "").strip()
                if not target:
                    continue
                message = EmailMessage()
                message["From"] = formataddr((sender_name, sender_address))
                message["To"] = target
                message["Subject"] = header
                message.set_content(plain_text)
                result = await send_smtp_message(
                    host=email_delivery.host,
                    port=email_delivery.port,
                    username=email_delivery.username,
                    password=email_delivery.password,
                    use_tls=email_delivery.use_tls,
                    use_ssl=email_delivery.use_ssl,
                    message=message,
                    source="workflow_machine_binding",
                )
                if result.success:
                    email_sent += 1

    return {
        "sent": sms_queued > 0 or email_sent > 0,
        "sms_queued": sms_queued,
        "email_sent": email_sent,
        "recipients": len(recipients),
    }


def apply_binding_snapshot(
    *,
    device: FormWorkflowDevice,
    item: WorkflowDeviceBindingItem | None,
) -> None:
    """Utrwala wynik automatu wiązania urządzenia w snapshot urządzenia."""
    snapshot = dict(device.snapshot or {})
    timestamp = datetime.now(UTC).isoformat()
    if item is None:
        snapshot["ms_binding_status"] = "none"
        snapshot["ms_binding_message"] = "Brak wyniku wiązania urządzenia."
        snapshot["ms_binding_updated_at"] = timestamp
        device.snapshot = snapshot
        return

    snapshot["ms_binding_status"] = "ok" if item.ok else "error"
    snapshot["ms_binding_message"] = item.message
    snapshot["ms_binding_updated_at"] = timestamp
    snapshot["ms_id_maszyna"] = (
        item.machine_id if item.machine_id is not None else snapshot.get("ms_id_maszyna")
    )
    if item.producer:
        snapshot["producer"] = item.producer
    if item.model:
        snapshot["model"] = item.model
    if item.serial:
        snapshot["serial"] = item.serial
    if item.current_client_id is not None:
        snapshot["ms_id_klient"] = item.current_client_id
    if item.current_ewidencja:
        snapshot["ewidencja"] = item.current_ewidencja
        snapshot["index"] = item.current_ewidencja
    if item.ok:
        snapshot["ms_binding_error"] = None
    else:
        snapshot["ms_binding_error"] = item.message

    if item.machine_id is not None:
        device.firebird_machine_id = item.machine_id
    if item.current_client_id is not None:
        device.firebird_client_id = item.current_client_id
    if item.producer:
        device.producer = item.producer
    if item.model:
        device.model = item.model
    if item.serial:
        device.serial = item.serial
    if item.current_ewidencja:
        device.ewidencja = item.current_ewidencja
    device.snapshot = snapshot


def build_binding_status_payload(devices: list[FormWorkflowDevice]) -> dict[str, Any]:
    """Buduje syntetyczny status wiązania urządzeń dla UI `/genform`."""
    success_labels: list[str] = []
    errors: list[str] = []
    last_updated_at: str | None = None

    for device in devices:
        snapshot = device.snapshot if isinstance(device.snapshot, dict) else {}
        status = str(snapshot.get("ms_binding_status") or "").strip().lower()
        if not status:
            continue
        updated_at = str(snapshot.get("ms_binding_updated_at") or "").strip()
        if updated_at and (last_updated_at is None or updated_at > last_updated_at):
            last_updated_at = updated_at
        if status == "ok":
            success_labels.append(_build_bound_device_label(device, snapshot))
        elif status == "error":
            errors.append(_format_binding_error_message(device, snapshot))

    state = "none"
    if errors and success_labels:
        state = "warning"
    elif errors:
        state = "error"
    elif success_labels:
        state = "ok"

    success_labels = sorted(dict.fromkeys(success_labels))
    errors = sorted(dict.fromkeys(errors))
    success_count = len(success_labels)
    error_count = len(errors)
    total_count = success_count + error_count

    if state == "ok":
        text = f"Powiązano urządzenie/a z klientem ({success_count}): " + ", ".join(
            success_labels[:6]
        )
    elif state == "warning":
        text = (
            f"Częściowo powiązano urządzenia ({success_count}/{total_count}). "
            f"{_build_binding_error_summary(errors)}"
        )
    elif state == "error":
        text = (
            f"Nie powiązano urządzeń z klientem (0/{total_count}). "
            f"{_build_binding_error_summary(errors)}"
        )
    else:
        text = "Brak uruchomionego automatu wiązania urządzeń."

    return {
        "state": state,
        "text": text,
        "success_count": success_count,
        "error_count": error_count,
        "success_devices": success_labels,
        "errors": errors,
        "updated_at": last_updated_at,
    }


__all__ = [
    "WorkflowDeviceBindingItem",
    "WorkflowDeviceOwnershipConflict",
    "WorkflowDeviceOwnershipConflictItem",
    "WorkflowDeviceMixedOwnershipHold",
    "apply_binding_snapshot",
    "bind_devices_to_workflow_client",
    "build_binding_status_payload",
    "notify_binding_issues_to_admins",
    "validate_no_active_workflow_device_duplicates",
    "validate_workflow_device_ownership",
]
