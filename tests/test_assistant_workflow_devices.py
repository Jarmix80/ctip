"""Testy audytu urządzeń workflow dla CTIP AI Asystenta."""

from __future__ import annotations

from app.services.assistant_workflow_devices import (
    CHAT_WORKSHEET_TITLE,
    build_workflow_devices_audit_payload,
    is_workflow_devices_audit_prompt,
)

HEADERS = [
    "PRODUCENT",
    "MODEL",
    "SERIAL",
    "EWIDENCJA",
    "STATUS",
    "UWAGI",
    "REZERWACJA GRENKE",
    "FAKTURA PROFORMA GRENKE",
    "MS_ID_MAGAZYN_TABLE",
]


def test_is_workflow_devices_audit_prompt_detects_polish_question() -> None:
    assert is_workflow_devices_audit_prompt(
        "Sprawdź urządzenia w arkuszu Google Zerowki_prod ze stanem w Firebird MS."
    )


def test_is_workflow_devices_audit_prompt_ignores_generic_write_request() -> None:
    assert not is_workflow_devices_audit_prompt("Dodaj urządzenie do arkusza bez porównania FB.")


def test_audit_payload_builds_fill_ms_id_stage_row() -> None:
    payload = build_workflow_devices_audit_payload(
        sheet_values=[
            HEADERS,
            ["Ricoh", "IM C3000", "", "KP/5137", "01. Przed zerówką", "", "", "", ""],
        ],
        firebird_devices={
            18408: {
                "ms_id_magazyn_table": 18408,
                "index": "KP/5137",
                "name": "Ricoh IM C3000 S/N:123456",
                "producer": "Ricoh",
                "model": "IM C3000",
                "serial": "123456",
                "available_qty": 1,
                "reserved_qty": 0,
                "warehouse_qty": 1,
            }
        },
        spreadsheet_id="sheet-1",
        spreadsheet_title="Zerowki_prod",
    )

    assert payload["spreadsheet"]["chat_worksheet"] == CHAT_WORKSHEET_TITLE
    assert payload["summary"]["stage_rows_count"] == 1
    assert payload["summary"]["stage_fill_ms_id_count"] == 1
    assert payload["summary"]["stage_append_count"] == 0

    row = payload["stage"]["rows"][0]
    assert row["action"] == "fill_ms_id"
    assert row["source_sheet_row"] == 2
    assert row["target_values"][8] == "18408"
    assert "UZUPELNIJ_MS_ID" in row["target_values"][5]


def test_audit_payload_builds_append_row_from_firebird_device() -> None:
    payload = build_workflow_devices_audit_payload(
        sheet_values=[HEADERS],
        firebird_devices={
            19001: {
                "ms_id_magazyn_table": 19001,
                "index": "KP/6001",
                "name": "Ricoh MP 2555 S/N:C390P600704",
                "producer": "",
                "model": "",
                "serial": "NIE",
                "available_qty": 1,
                "reserved_qty": 0,
                "warehouse_qty": 1,
            }
        },
        spreadsheet_id="sheet-1",
        spreadsheet_title="Zerowki_prod",
    )

    assert payload["summary"]["stage_rows_count"] == 1
    assert payload["summary"]["stage_fill_ms_id_count"] == 0
    assert payload["summary"]["stage_append_count"] == 1

    row = payload["stage"]["rows"][0]
    assert row["action"] == "append_row"
    values = row["target_values"]
    assert values[0] == "Ricoh"
    assert values[1] == "MP 2555"
    assert values[2] == "C390P600704"
    assert values[3] == "KP/6001"
    assert values[4] == "01. Przed zerówką"
    assert values[8] == "19001"
    assert "DOPISZ_WIERSZ" in values[5]
