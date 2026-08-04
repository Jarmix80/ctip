"""Testy klasyfikacji właściciela urządzenia magazynowego."""

from app.services.workflow_device_ownership import (
    MACHINE_MATCH_AMBIGUOUS,
    MACHINE_MATCH_FOREIGN,
    MACHINE_MATCH_INVALID_OWNER,
    MACHINE_MATCH_MISSING,
    MACHINE_MATCH_WAREHOUSE,
    classify_workflow_machine_ownership,
    snapshot_confirms_current_workflow_binding,
)


def test_missing_machine_is_available_for_new_binding() -> None:
    ownership = classify_workflow_machine_ownership(
        candidate_count=0,
        machine_id=None,
        client_id=None,
        client_name=None,
        warehouse_client_id=656,
    )

    assert ownership.state == MACHINE_MATCH_MISSING
    assert ownership.conflict is False


def test_warehouse_machine_is_available() -> None:
    ownership = classify_workflow_machine_ownership(
        candidate_count=1,
        machine_id=701,
        client_id=656,
        client_name="Ksero Partner",
        warehouse_client_id=656,
    )

    assert ownership.state == MACHINE_MATCH_WAREHOUSE
    assert ownership.conflict is False


def test_foreign_machine_is_blocked_with_owner_label() -> None:
    ownership = classify_workflow_machine_ownership(
        candidate_count=1,
        machine_id=702,
        client_id=1001,
        client_name="Inny klient",
        warehouse_client_id=656,
    )

    assert ownership.state == MACHINE_MATCH_FOREIGN
    assert ownership.conflict is True
    assert "Inny klient (ID 1001)" in ownership.reason
    assert "ID_KLIENT=656" in ownership.reason


def test_machine_without_client_is_blocked() -> None:
    ownership = classify_workflow_machine_ownership(
        candidate_count=1,
        machine_id=703,
        client_id=None,
        client_name=None,
        warehouse_client_id=656,
    )

    assert ownership.state == MACHINE_MATCH_INVALID_OWNER
    assert ownership.conflict is True


def test_ambiguous_machine_match_is_blocked() -> None:
    ownership = classify_workflow_machine_ownership(
        candidate_count=2,
        machine_id=None,
        client_id=None,
        client_name=None,
        warehouse_client_id=656,
    )

    assert ownership.state == MACHINE_MATCH_AMBIGUOUS
    assert ownership.conflict is True


def test_snapshot_allows_only_exact_idempotent_binding() -> None:
    snapshot = {
        "ms_binding_status": "ok",
        "ms_id_maszyna": "704",
        "ms_id_klient": "2897",
    }

    assert snapshot_confirms_current_workflow_binding(
        snapshot,
        machine_id=704,
        client_id=2897,
    )
    assert not snapshot_confirms_current_workflow_binding(
        snapshot,
        machine_id=705,
        client_id=2897,
    )
