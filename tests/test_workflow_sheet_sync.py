"""Testy synchronizacji magazynu urządzeń z arkuszem Google."""

from __future__ import annotations

import asyncio
import re
from contextlib import ExitStack, contextmanager
from unittest.mock import patch

import pytest

from app.services import workflow_sheet_sync


class FakeWorkbook:
    """Minimalny skoroszyt gspread używany bez połączenia sieciowego."""

    def __init__(self, worksheet, *, title: str = "Zerowki_test") -> None:
        self.title = title
        self.worksheet = worksheet
        self.requests: list[dict] = []
        worksheet.spreadsheet = self

    def batch_update(self, body: dict) -> None:
        self.requests.append(body)

    def worksheets(self) -> list:
        return [self.worksheet]


class FakeWorksheet:
    """Przechowuje komórki w pamięci i odwzorowuje potrzebne metody gspread."""

    def __init__(
        self,
        values: list[list[str]] | None = None,
        *,
        title: str = "Urzadzenia_magazyn",
    ) -> None:
        self.title = title
        self.id = 12
        self.values = [list(row) for row in (values or [])]
        self.spreadsheet = None
        self.append_calls = 0
        self.updated_ranges: list[str] = []

    def get_all_values(self) -> list[list[str]]:
        return [list(row) for row in self.values]

    def append_row(self, row_values: list[str], value_input_option: str | None = None) -> None:
        del value_input_option
        self.append_calls += 1
        self.values.append(list(row_values))

    def insert_row(
        self,
        row_values: list[str],
        *,
        index: int,
        value_input_option: str | None = None,
    ) -> None:
        del value_input_option
        self.values.insert(index - 1, list(row_values))

    def delete_rows(self, index: int) -> None:
        del self.values[index - 1]

    def batch_update(
        self,
        updates: list[dict],
        value_input_option: str | None = None,
    ) -> None:
        del value_input_option
        for update in updates:
            self.update(
                range_name=update["range"],
                values=update["values"],
                value_input_option="USER_ENTERED",
            )

    def update(
        self,
        *,
        range_name: str,
        values: list[list[str]],
        value_input_option: str | None = None,
    ) -> None:
        del value_input_option
        self.updated_ranges.append(range_name)
        match = re.fullmatch(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?", range_name)
        assert match is not None
        start_column, start_row, end_column, end_row = match.groups()
        start_row_index = int(start_row) - 1
        end_row_index = int(end_row or start_row) - 1
        start_column_index = _column_number(start_column) - 1
        source_row = list(values[0])
        end_column_index = (
            _column_number(end_column) - 1
            if end_column
            else start_column_index + max(0, len(source_row) - 1)
        )
        assert start_row_index == end_row_index
        while len(self.values) <= start_row_index:
            self.values.append([])
        row = self.values[start_row_index]
        while len(row) <= end_column_index:
            row.append("")
        for offset, column_index in enumerate(range(start_column_index, end_column_index + 1)):
            row[column_index] = source_row[offset] if offset < len(source_row) else ""


def _column_number(value: str) -> int:
    number = 0
    for char in value:
        number = number * 26 + ord(char) - 64
    return number


def _headers() -> list[str]:
    return list(workflow_sheet_sync._WORKFLOW_BOOTSTRAP_HEADER_LAYOUT)


def _configured_runtime() -> workflow_sheet_sync.WorkflowSheetRuntimeConfig:
    return workflow_sheet_sync.WorkflowSheetRuntimeConfig(
        enabled=True,
        credentials_path="/srv/google/test.json",
        spreadsheet_id="spreadsheet-test",
        workflow_devices_worksheet="Urzadzenia_magazyn",
        source="test",
    )


def _sheet_patches(workbook: FakeWorkbook, worksheet: FakeWorksheet):
    return (
        patch.object(
            workflow_sheet_sync,
            "_open_workbook",
            return_value=(workbook, "bot@example.com"),
        ),
        patch.object(
            workflow_sheet_sync,
            "_resolve_devices_worksheet",
            return_value=worksheet,
        ),
        patch.object(workflow_sheet_sync.Path, "exists", return_value=True),
        patch.object(
            workflow_sheet_sync.settings,
            "google_sheets_test_spreadsheet_id",
            "spreadsheet-test",
        ),
        patch.object(
            workflow_sheet_sync.settings,
            "google_sheets_test_spreadsheet_title",
            "Zerowki_test",
        ),
        patch.object(workflow_sheet_sync.settings, "ctip_runtime_profile", "test"),
    )


@contextmanager
def _sheet_context(workbook: FakeWorkbook, worksheet: FakeWorksheet):
    """Aktywuje komplet zabezpieczonych mocków skoroszytu testowego."""
    with ExitStack() as stack:
        for patcher in _sheet_patches(workbook, worksheet):
            stack.enter_context(patcher)
        yield


def _device_row() -> list[str]:
    return [
        "Ricoh",
        "MP 401",
        "T605H900327",
        "KP/4066",
        "01. Przed zerówką",
        "1234",
        "5678",
        "1500,00",
        "Ważna uwaga techniczna",
        "brak rezerwacji",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "12922",
        "7634",
        "TEST",
    ]


def test_build_header_index_separates_zeroing_and_reservation_status() -> None:
    header_index = workflow_sheet_sync._build_header_index(
        ["PRODUCENT", "STATUS", "STATUS REZERWACJI"]
    )

    assert header_index["status"] == 1
    assert header_index["reservation_status"] == 2


def test_load_workflow_sheet_runtime_config_uses_env_fallback() -> None:
    with (
        patch.object(workflow_sheet_sync.settings, "google_sheets_enabled", True),
        patch.object(
            workflow_sheet_sync.settings,
            "google_application_credentials",
            "/tmp/google-test.json",
        ),
        patch.object(
            workflow_sheet_sync.settings,
            "google_sheets_spreadsheet_id",
            "https://docs.google.com/spreadsheets/d/spreadsheet-test/edit",
        ),
        patch.object(
            workflow_sheet_sync.settings,
            "google_sheets_workflow_devices_sheet",
            "   ",
        ),
    ):
        config = asyncio.run(workflow_sheet_sync.load_workflow_sheet_runtime_config(object()))

    assert config.source == "env"
    assert config.enabled is True
    assert config.credentials_path == "/tmp/google-test.json"
    assert config.spreadsheet_id == "spreadsheet-test"
    assert config.workflow_devices_worksheet == "Urzadzenia_magazyn"


def test_workflow_sheet_connection_reports_new_missing_headers() -> None:
    worksheet = FakeWorksheet([["PRODUCENT", "MODEL", "INDEKS", "SERIAL", "STATUS"]])
    workbook = FakeWorkbook(worksheet)
    open_patch, resolve_patch, path_patch, *_ = _sheet_patches(workbook, worksheet)

    with open_patch, resolve_patch, path_patch:
        result = workflow_sheet_sync.test_workflow_sheet_connection(_configured_runtime())

    assert result["success"] is False
    assert "CENA" in result["missing_headers"]
    assert "STATUS REZERWACJI" in result["missing_headers"]
    assert "REZERWACJA DO" in result["missing_headers"]
    assert "CTIP_ENV" in result["missing_headers"]


def test_bootstrap_workflow_sheet_headers_creates_canonical_layout() -> None:
    worksheet = FakeWorksheet()
    workbook = FakeWorkbook(worksheet)
    patches = _sheet_patches(workbook, worksheet)

    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(workflow_sheet_sync, "_hide_helper_column", return_value=None),
    ):
        result = workflow_sheet_sync.bootstrap_workflow_sheet_headers(_configured_runtime())

    assert result["success"] is True
    assert worksheet.values[0] == _headers()
    assert "STATUS REZERWACJI" in result["added_headers"]
    assert "CTIP_ENV" in result["added_headers"]


def test_sync_workflow_updates_only_reservation_and_flow_columns() -> None:
    worksheet = FakeWorksheet([_headers(), _device_row()])
    workbook = FakeWorkbook(worksheet)
    patches = _sheet_patches(workbook, worksheet)

    with (
        workflow_sheet_sync.use_workflow_sheet_runtime_config(_configured_runtime()),
        patches[0],
        patches[1],
        patches[2],
    ):
        result = workflow_sheet_sync.sync_workflow_devices_to_sheet(
            devices=[
                {
                    "source_row": 12922,
                    "row": 12922,
                    "index": "KP/4066",
                    "ewidencja": "KP/4066",
                    "serial": "T605H900327",
                    "producer": "Ricoh",
                    "model": "MP 401",
                    "ms_id_maszyna": 7634,
                }
            ],
            assignee_label="Marcin Jarmuszkiewicz (marcin@example.com)",
            reservation_client_name="Klient Testowy",
            proforma_number="",
            form_request_id=23,
            workflow_case_id=8,
            business_status_label="Robocza",
        )

    header_index = workflow_sheet_sync._build_header_index(worksheet.values[0])
    row = worksheet.values[1]
    assert result["rows"][0]["previous_status"] == "brak rezerwacji"
    assert row[header_index["status"]] == "01. Przed zerówką"
    assert row[header_index["notes"]] == "Ważna uwaga techniczna"
    assert (
        row[header_index["reservation_status"]] == workflow_sheet_sync.WORKFLOW_RESERVATION_STATUS
    )
    assert row[header_index["reservation_grenke"]] == ("Marcin Jarmuszkiewicz\nKlient Testowy")
    assert row[header_index["form_ctip"]] == "23"
    assert row[header_index["ctip_form_id"]] == "23"
    assert row[header_index["ctip_workflow_case_id"]] == "8"
    assert row[header_index["ctip_env"]] == "TEST"


def test_release_workflow_preserves_zeroing_status_and_note() -> None:
    row = _device_row()
    header_index = workflow_sheet_sync._build_header_index(_headers())
    row[header_index["reservation_status"]] = "04. Rezerwacja GRENKE"
    row[header_index["reservation_until"]] = "2026-08-15"
    row[header_index["reservation_grenke"]] = "Marcin\nKlient"
    row[header_index["form_ctip"]] = "23"
    row[header_index["proforma_grenke"]] = "21/proforma/2026"
    row[header_index["ctip_form_id"]] = "23"
    row[header_index["ctip_workflow_case_id"]] = "8"
    worksheet = FakeWorksheet([_headers(), row])
    workbook = FakeWorkbook(worksheet)
    patches = _sheet_patches(workbook, worksheet)

    with (
        workflow_sheet_sync.use_workflow_sheet_runtime_config(_configured_runtime()),
        patches[0],
        patches[1],
        patches[2],
    ):
        result = workflow_sheet_sync.release_workflow_devices_from_sheet(
            devices=[
                {
                    "source_row": 12922,
                    "sheet_row": 2,
                    "index": "KP/4066",
                    "serial": "T605H900327",
                }
            ]
        )

    released = worksheet.values[1]
    assert result["released_count"] == 1
    assert released[header_index["status"]] == "01. Przed zerówką"
    assert released[header_index["notes"]] == "Ważna uwaga techniczna"
    assert released[header_index["reservation_status"]] == "brak rezerwacji"
    assert released[header_index["reservation_until"]] == ""
    assert released[header_index["reservation_grenke"]] == ""
    assert released[header_index["form_ctip"]] == ""
    assert released[header_index["proforma_grenke"]] == ""


def test_release_workflow_finds_row_when_saved_sheet_row_is_stale() -> None:
    unrelated = _device_row()
    unrelated[3] = "KP/9999"
    unrelated[18] = "99999"
    target = _device_row()
    target[9] = "04. Rezerwacja GRENKE"
    target[11] = "Marcin"
    worksheet = FakeWorksheet([_headers(), unrelated, target])
    workbook = FakeWorkbook(worksheet)
    patches = _sheet_patches(workbook, worksheet)

    with (
        workflow_sheet_sync.use_workflow_sheet_runtime_config(_configured_runtime()),
        patches[0],
        patches[1],
        patches[2],
    ):
        result = workflow_sheet_sync.release_workflow_devices_from_sheet(
            devices=[
                {
                    "source_row": 12922,
                    "sheet_row": 2,
                    "index": "KP/4066",
                    "serial": "T605H900327",
                }
            ]
        )

    assert result["rows"][0]["sheet_row"] == 3
    assert worksheet.values[1][9] == "brak rezerwacji"
    assert worksheet.values[2][9] == "brak rezerwacji"
    assert worksheet.values[2][11] == ""


def test_clear_workflow_proforma_clears_only_proforma_column() -> None:
    row = _device_row()
    header_index = workflow_sheet_sync._build_header_index(_headers())
    row[header_index["reservation_status"]] = "04. Rezerwacja GRENKE"
    row[header_index["reservation_grenke"]] = "Marcin"
    row[header_index["proforma_grenke"]] = "21/proforma/2026"
    worksheet = FakeWorksheet([_headers(), row])
    workbook = FakeWorkbook(worksheet)
    patches = _sheet_patches(workbook, worksheet)

    with (
        workflow_sheet_sync.use_workflow_sheet_runtime_config(_configured_runtime()),
        patches[0],
        patches[1],
        patches[2],
    ):
        result = workflow_sheet_sync.clear_workflow_proforma_from_sheet(
            devices=[
                {
                    "source_row": 12922,
                    "sheet_row": 2,
                    "index": "KP/4066",
                    "serial": "T605H900327",
                }
            ]
        )

    updated = worksheet.values[1]
    assert result["cleared_count"] == 1
    assert updated[header_index["proforma_grenke"]] == ""
    assert updated[header_index["reservation_status"]] == "04. Rezerwacja GRENKE"
    assert updated[header_index["reservation_grenke"]] == "Marcin"
    assert updated[header_index["status"]] == "01. Przed zerówką"
    assert updated[header_index["notes"]] == "Ważna uwaga techniczna"


def test_inventory_upsert_appends_complete_test_row() -> None:
    worksheet = FakeWorksheet([_headers()])
    workbook = FakeWorkbook(worksheet)

    with (
        workflow_sheet_sync.use_workflow_sheet_runtime_config(_configured_runtime()),
        _sheet_context(workbook, worksheet),
    ):
        result = workflow_sheet_sync.sync_device_inventory_to_sheet(
            operation_type="upsert_device",
            payload={
                "source_row": 19001,
                "producer": "Ricoh",
                "model": "IM C3000",
                "serial": "SN-NEW-001",
                "ewidencja": "KP/6001",
                "price": "1200.0000",
                "status": "01. Przed zerówką",
                "notes": "dodana automatem PZ z CTIP",
                "notes_red": True,
                "reservation_status": "brak rezerwacji",
                "reservation_until": "",
                "reservation_grenke": "",
                "ms_id_maszyna": 7701,
                "ctip_env": "TEST",
            },
        )

    header_index = workflow_sheet_sync._build_header_index(worksheet.values[0])
    row = worksheet.values[1]
    assert result["sheet_row"] == 2
    assert result["action"] == "appended"
    assert row[header_index["status"]] == "01. Przed zerówką"
    assert row[header_index["notes"]] == "dodana automatem PZ z CTIP"
    assert row[header_index["reservation_status"]] == "brak rezerwacji"
    assert row[header_index["ms_id_magazyn_table"]] == "19001"
    assert row[header_index["ms_id_maszyna"]] == "7701"
    assert row[header_index["ctip_env"]] == "TEST"
    assert worksheet.append_calls == 0
    assert "A2:U2" in worksheet.updated_ranges
    note_format_request = next(
        request["repeatCell"]
        for body in workbook.requests
        for request in body["requests"]
        if request.get("repeatCell", {})
        .get("cell", {})
        .get("userEnteredFormat", {})
        .get("textFormat", {})
        .get("foregroundColor")
        == {"red": 1.0, "green": 0.0, "blue": 0.0}
    )
    assert note_format_request["range"] == {
        "sheetId": 12,
        "startRowIndex": 1,
        "endRowIndex": 2,
        "startColumnIndex": header_index["notes"],
        "endColumnIndex": header_index["notes"] + 1,
    }


def test_inventory_manual_note_restores_black_text() -> None:
    worksheet = FakeWorksheet([_headers(), _device_row()])
    workbook = FakeWorkbook(worksheet)

    with (
        workflow_sheet_sync.use_workflow_sheet_runtime_config(_configured_runtime()),
        _sheet_context(workbook, worksheet),
    ):
        workflow_sheet_sync.sync_device_inventory_to_sheet(
            operation_type="update_note",
            payload={
                "source_row": 12922,
                "serial": "T605H900327",
                "notes": "Uwaga wpisana ręcznie",
                "ctip_env": "TEST",
            },
        )

    header_index = workflow_sheet_sync._build_header_index(worksheet.values[0])
    assert worksheet.values[1][header_index["notes"]] == "Uwaga wpisana ręcznie"
    note_format_request = next(
        request["repeatCell"]
        for body in workbook.requests
        for request in body["requests"]
        if request.get("repeatCell", {})
        .get("cell", {})
        .get("userEnteredFormat", {})
        .get("textFormat", {})
        .get("foregroundColor")
        == {"red": 0.0, "green": 0.0, "blue": 0.0}
    )
    assert note_format_request["range"]["startColumnIndex"] == header_index["notes"]


def test_inventory_reservation_update_never_overwrites_note_or_zeroing_status() -> None:
    worksheet = FakeWorksheet([_headers(), _device_row()])
    workbook = FakeWorkbook(worksheet)
    with (
        workflow_sheet_sync.use_workflow_sheet_runtime_config(_configured_runtime()),
        _sheet_context(workbook, worksheet),
    ):
        workflow_sheet_sync.sync_device_inventory_to_sheet(
            operation_type="update_reservation",
            payload={
                "source_row": 12922,
                "serial": "T605H900327",
                "ewidencja": "KP/4066",
                "reservation_status": "03. Rezerwacja ręczna",
                "reservation_until": "2026-08-20",
                "reservation_grenke": "Klient testowy",
                "ctip_env": "TEST",
            },
        )

    header_index = workflow_sheet_sync._build_header_index(worksheet.values[0])
    row = worksheet.values[1]
    assert row[header_index["status"]] == "01. Przed zerówką"
    assert row[header_index["notes"]] == "Ważna uwaga techniczna"
    assert row[header_index["reservation_status"]] == "03. Rezerwacja ręczna"
    assert row[header_index["reservation_until"]] == "2026-08-20"


def test_inventory_update_blocks_production_row_in_test_workbook() -> None:
    row = _device_row()
    row[-1] = "PRODUCTION"
    worksheet = FakeWorksheet([_headers(), row])
    workbook = FakeWorkbook(worksheet)
    with (
        workflow_sheet_sync.use_workflow_sheet_runtime_config(_configured_runtime()),
        _sheet_context(workbook, worksheet),
        pytest.raises(RuntimeError, match="spoza TEST"),
    ):
        workflow_sheet_sync.sync_device_inventory_to_sheet(
            operation_type="update_note",
            payload={
                "source_row": 12922,
                "serial": "T605H900327",
                "ewidencja": "KP/4066",
                "notes": "Nowa uwaga",
                "ctip_env": "TEST",
            },
        )


def test_inventory_update_blocks_wrong_test_workbook() -> None:
    worksheet = FakeWorksheet([_headers(), _device_row()])
    workbook = FakeWorkbook(worksheet, title="Zerowki_produkcyjne")
    config = _configured_runtime()

    with (
        workflow_sheet_sync.use_workflow_sheet_runtime_config(config),
        patch.object(
            workflow_sheet_sync,
            "_open_workbook",
            return_value=(workbook, "bot@example.com"),
        ),
        patch.object(
            workflow_sheet_sync,
            "_resolve_devices_worksheet",
            return_value=worksheet,
        ),
        patch.object(workflow_sheet_sync.Path, "exists", return_value=True),
        patch.object(
            workflow_sheet_sync.settings,
            "google_sheets_test_spreadsheet_id",
            "spreadsheet-test",
        ),
        patch.object(
            workflow_sheet_sync.settings,
            "google_sheets_test_spreadsheet_title",
            "Zerowki_test",
        ),
        patch.object(workflow_sheet_sync.settings, "ctip_runtime_profile", "test"),
        pytest.raises(RuntimeError, match="Tytuł skoroszytu"),
    ):
        workflow_sheet_sync.sync_device_inventory_to_sheet(
            operation_type="update_note",
            payload={
                "source_row": 12922,
                "serial": "T605H900327",
                "ewidencja": "KP/4066",
                "notes": "Nowa uwaga",
                "ctip_env": "TEST",
            },
        )


def test_lookup_returns_separate_status_note_price_and_machine_fields() -> None:
    worksheet = FakeWorksheet([_headers(), _device_row()])
    workbook = FakeWorkbook(worksheet)
    patches = _sheet_patches(workbook, worksheet)

    with (
        workflow_sheet_sync.use_workflow_sheet_runtime_config(_configured_runtime()),
        patches[0],
        patches[1],
        patches[2],
    ):
        result = workflow_sheet_sync.load_workflow_sheet_devices_lookup()

    entry = result["by_source_key"]["firebird_magazyn_28:12922"]
    assert entry["status"] == "01. Przed zerówką"
    assert entry["counter_bw"] == "1234"
    assert entry["counter_color"] == "5678"
    assert entry["notes"] == "Ważna uwaga techniczna"
    assert entry["reservation_status"] == "brak rezerwacji"
    assert entry["price"] == "1500,00"
    assert entry["ms_id_maszyna"] == "7634"
    assert entry["ctip_env"] == "TEST"
