import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import workflow_sheet_sync


def test_build_header_index_prefers_status_rezerwacji_over_generic_status():
    headers = [
        "PRODUCENT",
        "STATUS",
        "STATUS REZERWACJI",
        "REZERWACJA GRENKE",
    ]

    header_index = workflow_sheet_sync._build_header_index(headers)

    assert header_index["status"] == 2


def test_default_release_status_value_returns_brak_rezerwacji_for_reservation_column():
    headers = [
        "PRODUCENT",
        "STATUS",
        "STATUS REZERWACJI",
    ]
    header_index = workflow_sheet_sync._build_header_index(headers)

    release_status = workflow_sheet_sync._default_release_status_value(
        headers,
        header_index,
        "DOSTEPNE",
    )

    assert release_status == "brak rezerwacji"


def test_default_release_status_value_keeps_fallback_for_generic_status_column():
    headers = [
        "PRODUCENT",
        "STATUS",
        "REZERWACJA GRENKE",
    ]
    header_index = workflow_sheet_sync._build_header_index(headers)

    release_status = workflow_sheet_sync._default_release_status_value(
        headers,
        header_index,
        "DOSTEPNE",
    )

    assert release_status == "DOSTEPNE"


def test_load_workflow_sheet_runtime_config_uses_env_fallback():
    fake_session = object()
    with (
        patch.object(
            workflow_sheet_sync._settings_store,
            "get_namespace",
            AsyncMock(return_value={}),
        ),
        patch.object(
            workflow_sheet_sync.settings,
            "google_application_credentials",
            "/tmp/google-test.json",
        ),
        patch.object(
            workflow_sheet_sync.settings,
            "google_sheets_spreadsheet_id",
            "spreadsheet-test",
        ),
        patch.object(
            workflow_sheet_sync.settings,
            "google_sheets_workflow_devices_sheet",
            "Urzadzenia_magazyn",
        ),
    ):
        config = asyncio.run(workflow_sheet_sync.load_workflow_sheet_runtime_config(fake_session))

    assert config.source == "env"
    assert config.enabled is True
    assert config.credentials_path == "/tmp/google-test.json"
    assert config.spreadsheet_id == "spreadsheet-test"
    assert config.workflow_devices_worksheet == "Urzadzenia_magazyn"


def test_load_workflow_sheet_runtime_config_uses_admin_namespace():
    fake_session = object()
    with patch.object(
        workflow_sheet_sync._settings_store,
        "get_namespace",
        AsyncMock(
            return_value={
                "enabled": "false",
                "credentials_path": "/srv/google/admin.json",
                "spreadsheet_id": "sheet-admin",
                "workflow_devices_worksheet": "Urzadzenia_magazyn",
            }
        ),
    ):
        config = asyncio.run(workflow_sheet_sync.load_workflow_sheet_runtime_config(fake_session))

    assert config.source == "admin"
    assert config.enabled is False
    assert config.credentials_path == "/srv/google/admin.json"
    assert config.spreadsheet_id == "sheet-admin"
    assert config.workflow_devices_worksheet == "Urzadzenia_magazyn"


def test_load_workflow_sheet_runtime_config_normalizes_url_and_blank_worksheet():
    fake_session = object()
    with patch.object(
        workflow_sheet_sync._settings_store,
        "get_namespace",
        AsyncMock(
            return_value={
                "enabled": "true",
                "credentials_path": "/srv/google/admin.json",
                "spreadsheet_id": "https://docs.google.com/spreadsheets/d/sheet-admin/edit#gid=0",
                "workflow_devices_worksheet": "   ",
            }
        ),
    ):
        config = asyncio.run(workflow_sheet_sync.load_workflow_sheet_runtime_config(fake_session))

    assert config.spreadsheet_id == "sheet-admin"
    assert config.workflow_devices_worksheet == "Urzadzenia_magazyn"


def test_workflow_sheet_sync_configured_returns_disabled_reason_for_admin_switch():
    config = workflow_sheet_sync.WorkflowSheetRuntimeConfig(
        enabled=False,
        credentials_path="/srv/google/admin.json",
        spreadsheet_id="sheet-admin",
        workflow_devices_worksheet="Urzadzenia_magazyn",
        source="admin",
    )

    enabled, reason = workflow_sheet_sync.workflow_sheet_sync_configured(config)

    assert enabled is False
    assert reason == "Synchronizacja arkusza jest wyłączona w panelu administratora."


def test_test_workflow_sheet_connection_reports_missing_headers():
    config = workflow_sheet_sync.WorkflowSheetRuntimeConfig(
        enabled=True,
        credentials_path="/srv/google/admin.json",
        spreadsheet_id="sheet-admin",
        workflow_devices_worksheet="Urzadzenia_magazyn",
        source="admin",
    )
    worksheet = SimpleNamespace(
        title="Urzadzenia_magazyn",
        get_all_values=lambda: [["PRODUCENT", "MODEL", "INDEKS"]],
    )
    workbook = SimpleNamespace(title="zerowki_testowy")

    with (
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
    ):
        result = workflow_sheet_sync.test_workflow_sheet_connection(config)

    assert result["success"] is False
    assert result["spreadsheet_title"] == "zerowki_testowy"
    assert result["worksheet_title"] == "Urzadzenia_magazyn"
    assert "SERIAL" in result["missing_headers"]


def test_normalize_workflow_sheet_spreadsheet_id_accepts_full_url():
    raw_value = (
        "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890/edit#gid=0"
    )

    normalized = workflow_sheet_sync.normalize_workflow_sheet_spreadsheet_id(raw_value)

    assert normalized == "1AbCdEfGhIjKlMnOpQrStUvWxYz1234567890"


def test_bootstrap_workflow_sheet_headers_inserts_header_row_and_repairs_mixed_first_row():
    class FakeWorksheet:
        title = "Urzadzenia_magazyn"

        def __init__(self):
            self.values = [
                [
                    "Ricoh",
                    "IM C3500",
                    "12345",
                    "KP/5001",
                    "01. Przed zerowka",
                    "",
                    "",
                    "",
                    "1900",
                    "01.Magazyn KP",
                    "brak rezerwacji",
                    "",
                    "",
                    "PRODUCENT",
                    "MODEL",
                    "INDEKS",
                    "SERIAL",
                    "STATUS",
                    "MS_ID_MAGAZYN_TABLE",
                    "REZERWACJA GRENKE",
                    "FAKTURA PROFORMA GRENKE",
                ],
                ["Ricoh", "IM C3500", "23456", "KP/5002", "01. Przed zerowka"],
            ]

        def get_all_values(self):
            return [list(row) for row in self.values]

        def insert_row(self, values, index, value_input_option):
            assert index == 1
            assert value_input_option == "USER_ENTERED"
            self.values.insert(0, list(values))

        def update(self, range_name, values, value_input_option):
            assert value_input_option == "USER_ENTERED"
            if range_name == "A1":
                self.values[0] = list(values[0])
                return
            assert range_name == "A2:U2"
            self.values[1] = list(values[0])

    worksheet = FakeWorksheet()
    workbook = SimpleNamespace(title="zerowki_testowy")
    config = workflow_sheet_sync.WorkflowSheetRuntimeConfig(
        enabled=True,
        credentials_path="/srv/google/admin.json",
        spreadsheet_id="sheet-admin",
        workflow_devices_worksheet="Urzadzenia_magazyn",
        source="admin",
    )

    with (
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
        patch.object(workflow_sheet_sync, "_hide_helper_column", return_value=None),
        patch.object(workflow_sheet_sync.Path, "exists", return_value=True),
    ):
        result = workflow_sheet_sync.bootstrap_workflow_sheet_headers(config)

    assert result["success"] is True
    assert result["spreadsheet_title"] == "zerowki_testowy"
    assert result["worksheet_title"] == "Urzadzenia_magazyn"
    assert result["existing_headers"] == []
    assert "Osoba obsługująca" in result["added_headers"]
    assert worksheet.values[0][:17] == [
        "PRODUCENT",
        "MODEL",
        "SERIAL",
        "EWIDENCJA",
        "STATUS",
        "LICZNIK B/W",
        "LICZNIK KOLOR",
        "CENA",
        "UWAGI",
        "REZERWACJA GRENKE",
        "Osoba obsługująca",
        "FORMULARZ CTIP",
        "FAKTURA PROFORMA GRENKE",
        "CTIP_FORM_ID",
        "CTIP_WORKFLOW_CASE_ID",
        "STATUS HANDLOWY (LEGACY)",
        "MS_ID_MAGAZYN_TABLE",
    ]
    assert worksheet.values[0][17:] == [""] * 4
    assert worksheet.values[1][:11] == [
        "Ricoh",
        "IM C3500",
        "12345",
        "KP/5001",
        "01. Przed zerowka",
        "",
        "",
        "",
        "1900",
        "01.Magazyn KP",
        "brak rezerwacji",
    ]
    assert len(worksheet.values[1]) == 21
    assert worksheet.values[1][11:] == [""] * 10


def test_bootstrap_workflow_sheet_headers_removes_duplicate_header_row():
    class FakeWorksheet:
        title = "Urzadzenia_magazyn"

        def __init__(self):
            self.values = [
                [
                    "PRODUCENT",
                    "MODEL",
                    "SERIAL",
                    "EWIDENCJA",
                    "STATUS",
                    "LICZNIK B/W",
                    "LICZNIK KOLOR",
                    "CENA",
                    "UWAGI",
                    "REZERWACJA GRENKE",
                    "Osoba obsługująca",
                    "FORMULARZ CTIP",
                    "FAKTURA PROFORMA GRENKE",
                    "CTIP_FORM_ID",
                    "CTIP_WORKFLOW_CASE_ID",
                    "STATUS HANDLOWY (LEGACY)",
                    "MS_ID_MAGAZYN_TABLE",
                ],
                [
                    "PRODUCENT",
                    "MODEL",
                    "SERIAL",
                    "EWIDENCJA",
                    "STATUS",
                    "LICZNIK B/W",
                    "LICZNIK KOLOR",
                    "CENA",
                    "UWAGI",
                    "REZERWACJA GRENKE",
                    "Osoba obsługująca",
                ],
                ["Ricoh", "IM C3500", "23456", "KP/5002", "01. Przed zerowka"],
            ]

        def get_all_values(self):
            return [list(row) for row in self.values]

        def delete_rows(self, index):
            assert index == 2
            del self.values[index - 1]

    worksheet = FakeWorksheet()
    workbook = SimpleNamespace(title="zerowki_testowy")
    config = workflow_sheet_sync.WorkflowSheetRuntimeConfig(
        enabled=True,
        credentials_path="/srv/google/admin.json",
        spreadsheet_id="sheet-admin",
        workflow_devices_worksheet="Urzadzenia_magazyn",
        source="admin",
    )

    with (
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
        patch.object(workflow_sheet_sync, "_hide_helper_column", return_value=None),
        patch.object(workflow_sheet_sync.Path, "exists", return_value=True),
    ):
        result = workflow_sheet_sync.bootstrap_workflow_sheet_headers(config)

    assert result["success"] is True
    assert result["added_headers"] == []
    assert len(worksheet.values) == 2
    assert worksheet.values[1][:5] == ["Ricoh", "IM C3500", "23456", "KP/5002", "01. Przed zerowka"]


def test_sync_workflow_devices_to_sheet_updates_expected_business_columns():
    workbook_requests = []
    worksheet = SimpleNamespace(
        title="Urzadzenia_magazyn",
        id=12,
        values=[
            [
                "PRODUCENT",
                "MODEL",
                "SERIAL",
                "EWIDENCJA",
                "STATUS",
                "LICZNIK B/W",
                "LICZNIK KOLOR",
                "CENA",
                "UWAGI",
                "REZERWACJA GRENKE",
                "Osoba obsługująca",
                "FORMULARZ CTIP",
                "FAKTURA PROFORMA GRENKE",
                "CTIP_FORM_ID",
                "CTIP_WORKFLOW_CASE_ID",
                "STATUS HANDLOWY (LEGACY)",
                "MS_ID_MAGAZYN_TABLE",
            ],
            [
                "Ricoh",
                "MP 401",
                "T605H900327",
                "KP/4066",
                "01. Przed zerówką",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "12922",
            ],
        ],
    )
    worksheet.get_all_values = lambda: [list(row) for row in worksheet.values]
    worksheet.batch_update = lambda updates, value_input_option=None: _apply_batch_update(
        worksheet, updates
    )
    worksheet.append_row = lambda row_values, value_input_option=None: worksheet.values.append(
        list(row_values)
    )
    workbook = SimpleNamespace(
        title="zerowki_testowy",
        batch_update=lambda body: workbook_requests.append(body),
    )

    with (
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
    ):
        result = workflow_sheet_sync.sync_workflow_devices_to_sheet(
            devices=[
                {
                    "source_row": 12922,
                    "row": 12922,
                    "index": "KP/4066",
                    "ewidencja": "KP/4066",
                    "producer": "Ricoh",
                    "model": "MP 401",
                }
            ],
            assignee_label="Marcin Jarmuszkiewicz (marcin@ksero-partner.com.pl)",
            proforma_number="",
            form_request_id=23,
            workflow_case_id=8,
            business_status_label="Robocza",
        )

    assert result["enabled"] is True
    assert result["rows"][0]["sheet_row"] == 2
    assert result["rows"][0]["previous_status"] == ""
    assert worksheet.values[1][4] == "01. Przed zerówką"
    assert worksheet.values[1][8] == "Rezerwacja zalozona automatycznie przez CTIP."
    assert worksheet.values[1][9] == "Marcin Jarmuszkiewicz"
    assert worksheet.values[1][11] == "23"
    assert worksheet.values[1][12] == ""
    assert worksheet.values[1][13] == "23"
    assert worksheet.values[1][14] == "8"
    assert worksheet.values[1][15] == "Robocza"
    assert any(
        request.get("repeatCell", {}).get("range", {}).get("startRowIndex") == 1
        and request.get("repeatCell", {}).get("range", {}).get("endRowIndex") == 2
        and request.get("repeatCell", {})
        .get("cell", {})
        .get("userEnteredFormat", {})
        .get("backgroundColor")
        == workflow_sheet_sync.WORKFLOW_RESERVED_ROW_COLOR
        for body in workbook_requests
        for request in body.get("requests", [])
    )


def test_release_workflow_devices_from_sheet_restores_previous_status_and_clears_helper_columns():
    workbook_requests = []
    worksheet = SimpleNamespace(
        title="Urzadzenia_magazyn",
        id=12,
        values=[
            [
                "PRODUCENT",
                "MODEL",
                "SERIAL",
                "EWIDENCJA",
                "STATUS",
                "LICZNIK B/W",
                "LICZNIK KOLOR",
                "CENA",
                "UWAGI",
                "REZERWACJA GRENKE",
                "Osoba obsługująca",
                "FORMULARZ CTIP",
                "FAKTURA PROFORMA GRENKE",
                "CTIP_FORM_ID",
                "CTIP_WORKFLOW_CASE_ID",
                "STATUS HANDLOWY (LEGACY)",
                "MS_ID_MAGAZYN_TABLE",
            ],
            [
                "Ricoh",
                "MP 401",
                "T605H900327",
                "KP/4066",
                "01. Przed zerówką",
                "",
                "",
                "",
                "Rezerwacja zalozona automatycznie przez CTIP.",
                "Marcin Jarmuszkiewicz",
                "",
                "23",
                "",
                "23",
                "8",
                "Robocza",
                "12922",
            ],
        ],
    )
    worksheet.get_all_values = lambda: [list(row) for row in worksheet.values]
    worksheet.batch_update = lambda updates, value_input_option=None: _apply_batch_update(
        worksheet, updates
    )
    workbook = SimpleNamespace(
        title="zerowki_testowy",
        batch_update=lambda body: workbook_requests.append(body),
    )

    with (
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
    ):
        result = workflow_sheet_sync.release_workflow_devices_from_sheet(
            devices=[
                {
                    "source_row": 12922,
                    "row": 12922,
                    "sheet_row": 2,
                    "index": "KP/4066",
                    "ewidencja": "KP/4066",
                    "sheet_previous_status": "01. Przed zerówką",
                }
            ]
        )

    assert result["enabled"] is True
    assert result["released_count"] == 1
    assert worksheet.values[1][4] == "01. Przed zerówką"
    assert worksheet.values[1][8] == ""
    assert worksheet.values[1][9] == ""
    assert worksheet.values[1][11] == ""
    assert worksheet.values[1][12] == ""
    assert worksheet.values[1][13] == ""
    assert worksheet.values[1][14] == ""
    assert worksheet.values[1][15] == ""
    assert any(
        request.get("repeatCell", {}).get("range", {}).get("startRowIndex") == 1
        and request.get("repeatCell", {}).get("range", {}).get("endRowIndex") == 2
        and request.get("repeatCell", {})
        .get("cell", {})
        .get("userEnteredFormat", {})
        .get("backgroundColor")
        == workflow_sheet_sync.WORKFLOW_DEFAULT_ROW_COLOR
        for body in workbook_requests
        for request in body.get("requests", [])
    )


def test_release_workflow_devices_from_sheet_falls_back_when_sheet_row_is_stale():
    workbook_requests = []
    worksheet = SimpleNamespace(
        title="Urzadzenia_magazyn",
        id=12,
        values=[
            [
                "PRODUCENT",
                "MODEL",
                "SERIAL",
                "EWIDENCJA",
                "STATUS",
                "LICZNIK B/W",
                "LICZNIK KOLOR",
                "CENA",
                "UWAGI",
                "REZERWACJA GRENKE",
                "Osoba obsługująca",
                "FORMULARZ CTIP",
                "FAKTURA PROFORMA GRENKE",
                "CTIP_FORM_ID",
                "CTIP_WORKFLOW_CASE_ID",
                "STATUS HANDLOWY (LEGACY)",
                "MS_ID_MAGAZYN_TABLE",
            ],
            [
                "Ricoh",
                "IM 350",
                "",
                "KP/9999",
                "Dostepne",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "99999",
            ],
            [
                "Ricoh",
                "MP 401",
                "T605H900327",
                "KP/4066",
                "01. Przed zerówką",
                "",
                "",
                "",
                "Rezerwacja zalozona automatycznie przez CTIP.",
                "Marcin Jarmuszkiewicz",
                "",
                "23",
                "",
                "23",
                "8",
                "Robocza",
                "12922",
            ],
        ],
    )
    worksheet.get_all_values = lambda: [list(row) for row in worksheet.values]
    worksheet.batch_update = lambda updates, value_input_option=None: _apply_batch_update(
        worksheet, updates
    )
    workbook = SimpleNamespace(
        title="zerowki_testowy",
        batch_update=lambda body: workbook_requests.append(body),
    )

    with (
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
    ):
        result = workflow_sheet_sync.release_workflow_devices_from_sheet(
            devices=[
                {
                    "source_row": 12922,
                    "row": 12922,
                    "sheet_row": 2,
                    "index": "KP/4066",
                    "ewidencja": "KP/4066",
                    "sheet_previous_status": "01. Przed zerówką",
                }
            ]
        )

    assert result["enabled"] is True
    assert result["released_count"] == 1
    assert result["rows"][0]["sheet_row"] == 3
    assert worksheet.values[1][4] == "Dostepne"
    assert worksheet.values[2][4] == "01. Przed zerówką"
    assert worksheet.values[2][8] == ""
    assert worksheet.values[2][9] == ""
    assert worksheet.values[2][11] == ""
    assert worksheet.values[2][12] == ""
    assert worksheet.values[2][13] == ""
    assert worksheet.values[2][14] == ""
    assert worksheet.values[2][15] == ""
    assert any(
        request.get("repeatCell", {}).get("range", {}).get("startRowIndex") == 2
        and request.get("repeatCell", {}).get("range", {}).get("endRowIndex") == 3
        and request.get("repeatCell", {})
        .get("cell", {})
        .get("userEnteredFormat", {})
        .get("backgroundColor")
        == workflow_sheet_sync.WORKFLOW_DEFAULT_ROW_COLOR
        for body in workbook_requests
        for request in body.get("requests", [])
    )


def test_clear_workflow_proforma_from_sheet_clears_only_proforma_column():
    workbook_requests = []
    worksheet = SimpleNamespace(
        title="zerowki_testowy",
        values=[
            [
                "PRODUCENT",
                "MODEL",
                "SERIAL",
                "EWIDENCJA",
                "STATUS",
                "LICZNIK B/W",
                "LICZNIK KOLOR",
                "CENA",
                "UWAGI",
                "REZERWACJA GRENKE",
                "Osoba obsługująca",
                "FORMULARZ CTIP",
                "FAKTURA PROFORMA GRENKE",
                "CTIP_FORM_ID",
                "CTIP_WORKFLOW_CASE_ID",
                "STATUS HANDLOWY (LEGACY)",
                "MS_ID_MAGAZYN_TABLE",
            ],
            [
                "Ricoh",
                "MP 401",
                "T605H900327",
                "KP/4066",
                "04. Rezerwacja GRENKE",
                "",
                "",
                "",
                "Rezerwacja zalozona automatycznie przez CTIP.",
                "Marcin Jarmuszkiewicz",
                "",
                "23",
                "21/proforma/2026",
                "23",
                "8",
                "Robocza",
                "12922",
            ],
        ],
    )
    worksheet.get_all_values = lambda: [list(row) for row in worksheet.values]
    worksheet.batch_update = lambda updates, value_input_option=None: _apply_batch_update(
        worksheet, updates
    )
    workbook = SimpleNamespace(
        title="zerowki_testowy",
        batch_update=lambda body: workbook_requests.append(body),
    )

    with (
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
    ):
        result = workflow_sheet_sync.clear_workflow_proforma_from_sheet(
            devices=[
                {
                    "source_row": 12922,
                    "row": 12922,
                    "sheet_row": 2,
                    "index": "KP/4066",
                    "ewidencja": "KP/4066",
                }
            ]
        )

    assert result["enabled"] is True
    assert result["cleared_count"] == 1
    assert worksheet.values[1][4] == "04. Rezerwacja GRENKE"
    assert worksheet.values[1][8] == "Rezerwacja zalozona automatycznie przez CTIP."
    assert worksheet.values[1][9] == "Marcin Jarmuszkiewicz"
    assert worksheet.values[1][11] == "23"
    assert worksheet.values[1][12] == ""
    assert worksheet.values[1][13] == "23"
    assert workbook_requests == []


def _apply_batch_update(worksheet, updates):
    for update in updates:
        range_name = update["range"]
        value = update["values"][0][0]
        match = workflow_sheet_sync.re.match(r"^([A-Z]+)(\d+)$", range_name)
        assert match is not None
        column_letters, row_number = match.groups()
        row_idx = int(row_number) - 1
        col_idx = 0
        for char in column_letters:
            col_idx = (col_idx * 26) + (ord(char) - 64)
        col_idx -= 1
        while len(worksheet.values) <= row_idx:
            worksheet.values.append([])
        while len(worksheet.values[row_idx]) <= col_idx:
            worksheet.values[row_idx].append("")
        worksheet.values[row_idx][col_idx] = value
