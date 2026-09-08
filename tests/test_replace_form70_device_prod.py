"""Testy zabezpieczeń jednorazowej korekty formularza 70."""

from __future__ import annotations

from copy import deepcopy
from unittest.mock import patch

import pytest

from scripts import replace_form70_device_prod as repair


def _device(source_row: int, serial: str, price_net: str, price_gross: str) -> dict:
    return {
        "id": source_row,
        "source_type": "firebird_magazyn_28",
        "source_row": source_row,
        "producer": "Ricoh",
        "model": "IM C300" if source_row == repair.OLD_SOURCE_ROW else "IM 430",
        "serial": serial,
        "ewidencja": "",
        "device_status": "Dostępne",
        "reservation_status": "brak rezerwacji",
        "price": price_gross,
        "price_net": price_net,
        "price_gross": price_gross,
        "firebird_machine_id": None,
        "firebird_client_id": repair.TARGET_CLIENT_ID,
        "snapshot": {},
    }


def _sheet_row(
    source_row: int,
    serial: str,
    index: str,
    *,
    selected: bool,
) -> dict:
    return {
        "sheet_row": str(source_row),
        "producer": "Ricoh",
        "model": "IM C300",
        "serial": serial,
        "index": index,
        "status": "02. Po zerówce",
        "notes": "uwaga",
        "reservation_status": (
            repair.WORKFLOW_RESERVATION_STATUS if selected else "brak rezerwacji"
        ),
        "reservation_until": "",
        "reservation_grenke": "Kamil Gruszczyński\nPROCENT Justyna Figlarek",
        "form_ctip": str(repair.FORM_ID) if selected else "",
        "proforma_grenke": repair.OLD_PROFORMA_NUMBER if selected else "",
        "ctip_form_id": str(repair.FORM_ID) if selected else "",
        "ctip_workflow_case_id": str(repair.CASE_ID) if selected else "",
        "business_status_legacy": "Umowa zaakceptowana" if selected else "",
        "ms_id_magazyn_table": str(source_row),
        "ms_id_maszyna": "",
        "ctip_env": "PRODUCTION",
    }


def _proforma(*, new: bool = False) -> dict:
    rows = (
        [repair.NEW_SOURCE_ROW, repair.KEPT_SOURCE_ROW]
        if new
        else [repair.OLD_SOURCE_ROW, repair.KEPT_SOURCE_ROW]
    )
    prices = {
        repair.OLD_SOURCE_ROW: ("7150.0000", "8794.5000"),
        repair.NEW_SOURCE_ROW: ("7150.0000", "8794.5000"),
        repair.KEPT_SOURCE_ROW: ("6250.0000", "7687.5000"),
    }
    return {
        "id_faktura_table": 65000 if new else repair.OLD_PROFORMA_ID,
        "numer": "53/proforma/2026" if new else repair.OLD_PROFORMA_NUMBER,
        "id_klient": repair.BANK_CLIENT_ID,
        "wystawil": "Kamil Gruszczynski",
        "suma_netto": "13400.0000",
        "suma_vat": "3082.0000",
        "suma_brutto": "16482.0000",
        "lines": [
            {
                "id_magpoz": source_row,
                "cena_netto": prices[source_row][0],
                "cena_brutto": prices[source_row][1],
            }
            for source_row in rows
        ],
    }


def _initial_state() -> dict:
    return {
        "postgres": {
            "form": {"id": 70, "status": "SUBMITTED", "archive_bucket": None},
            "case": {
                "id": 40,
                "form_request_id": 70,
                "stage": "PROFORMA_CREATED",
                "business_status": "APPROVED_ORDER",
                "firebird_client_id": 1462,
                "proforma_firebird_id": repair.OLD_PROFORMA_ID,
                "proforma_number": repair.OLD_PROFORMA_NUMBER,
                "status_source": "mailbox",
                "status_history": [{"status": "APPROVED_ORDER"}],
            },
            "devices": [
                _device(
                    repair.OLD_SOURCE_ROW,
                    repair.OLD_SERIAL,
                    "7150.0000",
                    "8794.5000",
                ),
                _device(
                    repair.KEPT_SOURCE_ROW,
                    repair.KEPT_SERIAL,
                    "6250.0000",
                    "7687.5000",
                ),
            ],
            "actor_id": 10,
            "source_audit_payload": {"sheet_assignee_id": 10},
        },
        "firebird": {
            "warehouse": [
                {
                    "id_magazyn_table": repair.OLD_SOURCE_ROW,
                    "indeks": repair.OLD_INDEX,
                    "nazwa": f"Ricoh IM C300 S/N:{repair.OLD_SERIAL}",
                },
                {
                    "id_magazyn_table": repair.KEPT_SOURCE_ROW,
                    "indeks": repair.KEPT_INDEX,
                    "nazwa": f"Ricoh IM 430 S/N:{repair.KEPT_SERIAL}",
                },
                {
                    "id_magazyn_table": repair.NEW_SOURCE_ROW,
                    "indeks": repair.NEW_INDEX,
                    "nazwa": f"Ricoh IM C300 S/N:{repair.NEW_SERIAL}",
                },
            ],
            "machines": [
                {
                    "id_maszyna": repair.OLD_MACHINE_ID,
                    "id_klient": repair.TARGET_CLIENT_ID,
                    "ewidencja": repair.OLD_BOUND_EWIDENCJA,
                },
                {
                    "id_maszyna": repair.KEPT_MACHINE_ID,
                    "id_klient": repair.TARGET_CLIENT_ID,
                    "ewidencja": "KP/5152/GRENKE/R/E",
                },
                {
                    "id_maszyna": repair.NEW_MACHINE_ID,
                    "id_klient": repair.WAREHOUSE_CLIENT_ID,
                    "ewidencja": repair.NEW_WAREHOUSE_EWIDENCJA,
                },
            ],
            "old_proforma": _proforma(),
            "planned_proforma_number": "53/proforma/2026",
            "planned_proforma": None,
        },
        "sheet": {
            "worksheet_title": "Urzadzenia_magazyn",
            "rows": {
                str(repair.OLD_SOURCE_ROW): _sheet_row(
                    repair.OLD_SOURCE_ROW,
                    repair.OLD_SERIAL,
                    repair.OLD_INDEX,
                    selected=True,
                ),
                str(repair.KEPT_SOURCE_ROW): _sheet_row(
                    repair.KEPT_SOURCE_ROW,
                    repair.KEPT_SERIAL,
                    repair.KEPT_INDEX,
                    selected=True,
                ),
                str(repair.NEW_SOURCE_ROW): _sheet_row(
                    repair.NEW_SOURCE_ROW,
                    repair.NEW_SERIAL,
                    repair.NEW_INDEX,
                    selected=False,
                ),
            },
        },
    }


def test_validate_initial_state_accepts_expected_production_snapshot() -> None:
    repair.validate_initial_state(_initial_state())


def test_validate_initial_state_rejects_changed_grenke_status() -> None:
    state = _initial_state()
    state["postgres"]["case"]["business_status"] = "REJECTED_GRENKE"

    with pytest.raises(repair.Form70ReplacementError, match="APPROVED_ORDER"):
        repair.validate_initial_state(state)


def test_state_token_changes_after_device_owner_change() -> None:
    state = _initial_state()
    original = repair._state_token(state)
    changed = deepcopy(state)
    changed["firebird"]["machines"][0]["id_klient"] = 656

    assert repair._state_token(changed) != original


def test_service_note_is_appended_only_once() -> None:
    first = repair._append_service_note("Istniejąca uwaga")
    second = repair._append_service_note(first)

    assert first == second
    assert first.startswith("Istniejąca uwaga\n")


def test_production_guard_allows_loopback_only_on_production_host() -> None:
    with (
        patch.object(repair, "SETTINGS_ENV_FILE", "/srv/.env"),
        patch.object(repair.settings, "ctip_runtime_profile", "production"),
        patch.object(repair.settings, "pg_host", "127.0.0.1"),
        patch.object(repair.settings, "pg_database", "ctip"),
        patch.object(repair.settings, "fb_host", "192.168.0.8"),
        patch.object(repair.settings, "fb_port", 3050),
        patch.object(repair.settings, "fb_allow_writes", True),
        patch.object(repair, "_local_ipv4_addresses", return_value={"192.168.0.8"}),
    ):
        repair._assert_production_target(require_writes=True)


def test_production_guard_rejects_loopback_from_wsl_host() -> None:
    with (
        patch.object(repair, "SETTINGS_ENV_FILE", "/srv/.env"),
        patch.object(repair.settings, "ctip_runtime_profile", "production"),
        patch.object(repair.settings, "pg_host", "127.0.0.1"),
        patch.object(repair.settings, "pg_database", "ctip"),
        patch.object(repair.settings, "fb_host", "192.168.0.8"),
        patch.object(repair.settings, "fb_port", 3050),
        patch.object(repair.settings, "fb_allow_writes", True),
        patch.object(repair, "_local_ipv4_addresses", return_value={"192.168.0.9"}),
        pytest.raises(repair.Form70ReplacementError, match="lokalny loopback"),
    ):
        repair._assert_production_target(require_writes=True)


def test_validate_new_proforma_requires_replacement_first() -> None:
    proforma = _proforma(new=True)
    repair._validate_new_proforma(proforma, expected_number="53/proforma/2026")
    proforma["lines"].reverse()

    with pytest.raises(repair.Form70ReplacementError, match="kolejność"):
        repair._validate_new_proforma(proforma, expected_number="53/proforma/2026")


def test_sheet_changes_preserve_grenke_label_and_block_old_device() -> None:
    journal = {
        "before": _initial_state(),
        "new_proforma": {"number": "53/proforma/2026"},
        "sheet_reservation_label": "Kamil Gruszczyński\nPROCENT Justyna Figlarek",
        "sheet_business_status_label": "Umowa zaakceptowana",
    }

    changes = repair._sheet_target_changes(journal)
    by_source = {item["device"]["source_row"]: item for item in changes}

    assert (
        by_source[repair.OLD_SOURCE_ROW]["target_fields"]["reservation_status"]
        == repair.WORKFLOW_SERVICE_BLOCK_STATUS
    )
    assert by_source[repair.OLD_SOURCE_ROW]["target_fields"]["proforma_grenke"] == ""
    assert (
        by_source[repair.NEW_SOURCE_ROW]["target_fields"]["reservation_grenke"]
        == "Kamil Gruszczyński\nPROCENT Justyna Figlarek"
    )
    assert (
        by_source[repair.KEPT_SOURCE_ROW]["target_fields"]["proforma_grenke"] == "53/proforma/2026"
    )
