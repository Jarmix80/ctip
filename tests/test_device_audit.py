"""Testy klasyfikacji tylko-odczytowego audytu urządzeń."""

from app.services.device_audit import build_device_audit_items, summarize_device_audit


def test_audit_detects_sheet_serial_typo_and_missing_sources():
    items = build_device_audit_items(
        sheet_rows=[
            {
                "sheet_row": 60,
                "ms_id": 16332,
                "producer": "Ricoh",
                "model": "P502",
                "serial": "5381P80615",
                "index": "KP/4617",
            }
        ],
        warehouse_rows=[
            {
                "source_row": 16332,
                "producer": "Ricoh",
                "model": "P502",
                "serial": "5381P800615",
                "ewidencja": "KP/4617",
            }
        ],
        machine_rows=[],
        ctip_rows=[],
    )

    assert len(items) == 1
    item = items[0]
    assert item["result_status"] == "discrepancy"
    assert item["sheet_present"] is True
    assert item["warehouse_present"] is True
    assert item["machine_present"] is False
    assert item["ctip_present"] is False
    assert set(item["issue_codes"]) == {
        "MISSING_MACHINE",
        "MISSING_CTIP",
        "SERIAL_MISMATCH",
    }


def test_audit_prefers_duplicate_result_over_other_issues():
    items = build_device_audit_items(
        sheet_rows=[],
        warehouse_rows=[
            {
                "source_row": 10,
                "serial": "ABC123",
                "ewidencja": "KP/10",
            }
        ],
        machine_rows=[
            {"machine_id": 1, "serial": "ABC123", "ewidencja": "KP/10"},
            {"machine_id": 2, "serial": "ABC123", "ewidencja": "KP/10"},
        ],
        ctip_rows=[
            {
                "ctip_unit_id": 5,
                "source_row": 10,
                "serial": "ABC123",
                "ewidencja": "KP/10",
            }
        ],
    )

    assert len(items) == 1
    assert items[0]["result_status"] == "duplicate"
    assert "DUPLICATE_MACHINE" in items[0]["issue_codes"]
    assert summarize_device_audit(items)["duplicate"] == 1


def test_audit_marks_complete_consistent_device_as_ok():
    common = {
        "producer": "Ricoh",
        "model": "P502",
        "serial": "ABC123",
        "ewidencja": "KP/10",
    }
    items = build_device_audit_items(
        sheet_rows=[{"sheet_row": 2, "ms_id": 10, "index": "KP/10", **common}],
        warehouse_rows=[{"source_row": 10, **common}],
        machine_rows=[{"machine_id": 7, **common}],
        ctip_rows=[{"ctip_unit_id": 8, "source_row": 10, **common}],
    )

    assert len(items) == 1
    assert items[0]["result_status"] == "ok"
    assert items[0]["issue_codes"] == []


def test_audit_detects_duplicate_serial_across_different_warehouse_rows():
    items = build_device_audit_items(
        sheet_rows=[],
        warehouse_rows=[
            {"source_row": 10, "serial": "ABC123", "ewidencja": "KP/10"},
            {"source_row": 11, "serial": "ABC123", "ewidencja": "KP/11"},
        ],
        machine_rows=[],
        ctip_rows=[],
    )

    assert len(items) == 2
    assert all(item["result_status"] == "duplicate" for item in items)
    assert all("DUPLICATE_WAREHOUSE" in item["issue_codes"] for item in items)
