"""Klasyfikacja właściciela urządzenia magazynowego dla workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MACHINE_MATCH_MISSING = "missing"
MACHINE_MATCH_WAREHOUSE = "warehouse"
MACHINE_MATCH_FOREIGN = "foreign"
MACHINE_MATCH_INVALID_OWNER = "invalid_owner"
MACHINE_MATCH_AMBIGUOUS = "ambiguous"
MACHINE_MATCH_BOUND_CURRENT_WORKFLOW = "bound_current_workflow"
MACHINE_MATCH_TARGET = "target"
MACHINE_BATCH_MIXED_HOLD = "mixed_hold"


def _coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class WorkflowMachineOwnership:
    """Wynik oceny powiązania pozycji magazynowej z rekordem MASZYNA."""

    state: str
    conflict: bool
    reason: str


def classify_workflow_ownership_batch(states: set[str]) -> str:
    """Klasyfikuje pakiet bezpieczny do zapisu lub wymagający ręcznej kontroli."""
    normalized = {str(state or "").strip() for state in states if str(state or "").strip()}
    if MACHINE_MATCH_TARGET in normalized and MACHINE_MATCH_WAREHOUSE in normalized:
        return MACHINE_BATCH_MIXED_HOLD
    if normalized == {MACHINE_MATCH_TARGET}:
        return MACHINE_MATCH_TARGET
    if normalized == {MACHINE_MATCH_WAREHOUSE}:
        return MACHINE_MATCH_WAREHOUSE
    return "standard"


def classify_workflow_machine_ownership(
    *,
    candidate_count: int,
    machine_id: int | None,
    client_id: int | None,
    client_name: str | None,
    warehouse_client_id: int,
    target_client_id: int | None = None,
) -> WorkflowMachineOwnership:
    """Klasyfikuje dostępność według właściciela zapisanego w Firebird."""
    if candidate_count <= 0:
        return WorkflowMachineOwnership(
            state=MACHINE_MATCH_MISSING,
            conflict=False,
            reason="",
        )
    if candidate_count > 1:
        return WorkflowMachineOwnership(
            state=MACHINE_MATCH_AMBIGUOUS,
            conflict=True,
            reason=(
                "Dopasowano kilka rekordów MASZYNA. Popraw serial lub ewidencję "
                "w Menadżerze Serwisu przed użyciem urządzenia w FLOW."
            ),
        )
    if machine_id is None or client_id is None:
        return WorkflowMachineOwnership(
            state=MACHINE_MATCH_INVALID_OWNER,
            conflict=True,
            reason=(
                "Rekord MASZYNA nie ma poprawnego ID_KLIENT. Przypisz urządzenie "
                f"do klienta magazynowego {warehouse_client_id}."
            ),
        )
    if client_id == warehouse_client_id:
        return WorkflowMachineOwnership(
            state=MACHINE_MATCH_WAREHOUSE,
            conflict=False,
            reason="",
        )
    if target_client_id is not None and client_id == target_client_id:
        return WorkflowMachineOwnership(
            state=MACHINE_MATCH_TARGET,
            conflict=False,
            reason="Urządzenie jest już przypisane do klienta docelowego.",
        )

    client_label = str(client_name or "").strip()
    owner = f"{client_label} (ID {client_id})" if client_label else f"ID {client_id}"
    return WorkflowMachineOwnership(
        state=MACHINE_MATCH_FOREIGN,
        conflict=True,
        reason=(
            f"Urządzenie jest przypisane do klienta {owner}. "
            f"Dostępne urządzenie magazynowe musi mieć ID_KLIENT={warehouse_client_id}."
        ),
    )


def snapshot_confirms_current_workflow_binding(
    snapshot: dict[str, Any] | None,
    *,
    machine_id: int | None,
    client_id: int | None,
) -> bool:
    """Potwierdza idempotentne wiązanie wykonane wcześniej przez tę samą sprawę."""
    if not isinstance(snapshot, dict):
        return False
    if str(snapshot.get("ms_binding_status") or "").strip().lower() != "ok":
        return False
    return (
        machine_id is not None
        and client_id is not None
        and _coerce_int(snapshot.get("ms_id_maszyna")) == machine_id
        and _coerce_int(snapshot.get("ms_id_klient")) == client_id
    )


__all__ = [
    "MACHINE_MATCH_AMBIGUOUS",
    "MACHINE_BATCH_MIXED_HOLD",
    "MACHINE_MATCH_BOUND_CURRENT_WORKFLOW",
    "MACHINE_MATCH_FOREIGN",
    "MACHINE_MATCH_INVALID_OWNER",
    "MACHINE_MATCH_MISSING",
    "MACHINE_MATCH_TARGET",
    "MACHINE_MATCH_WAREHOUSE",
    "WorkflowMachineOwnership",
    "classify_workflow_ownership_batch",
    "classify_workflow_machine_ownership",
    "snapshot_confirms_current_workflow_binding",
]
