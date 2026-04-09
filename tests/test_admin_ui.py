# ruff: noqa: E402

import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.api import deps
from app.main import create_app
from app.models import AdminUser
from app.services import admin_users
from app.services.backup_runner import BackupFileInfo


async def _fake_admin_context():
    class DummySession:
        client_ip = "127.0.0.1"

    class DummyUser:
        id = 1
        role = "admin"
        is_active = True

    return DummySession(), DummyUser()


async def _fake_db_session():
    class DummyDbSession:
        async def commit(self) -> None:
            return None

        async def execute(self, *_args, **_kwargs):
            class DummyResult:
                def scalars(self):
                    return self

                def all(self):
                    return []

                def scalar_one_or_none(self):
                    return None

            return DummyResult()

    yield DummyDbSession()


def test_admin_index_renders_layout():
    app = create_app()
    client = TestClient(app)
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Logowanie administratora" in response.text
    assert "login-form" in response.text
    assert f"/static/admin/admin.js?v={app.version}" in response.text
    assert f"/static/admin/styles.css?v={app.version}" in response.text


def test_dashboard_partial_returns_cards():
    app = create_app()
    app.dependency_overrides[deps.get_admin_session_context] = _fake_admin_context
    app.dependency_overrides[deps.get_db_session] = _fake_db_session
    client = TestClient(app)
    fake_cards = [
        {
            "state": "ok",
            "title": "Baza danych",
            "status": "Połączono",
            "details": "Testowa konfiguracja",
            "variant": "success",
            "cta": {"label": "Ustaw parametry", "action": "open-section:database"},
            "secondary_cta": {"label": "Historia", "action": "open-section:sms-history"},
            "diagnostics_endpoint": "/admin/status/database",
        }
    ]
    with patch("app.web.admin_ui.compute_status_summary", AsyncMock(return_value=fake_cards)):
        response = client.get(
            "/admin/partials/dashboard",
            headers={"X-Admin-Session": "test-token"},
        )
    assert response.status_code == 200
    assert "status-card" in response.text
    assert "Baza danych" in response.text
    assert "window.AdminPanel.handleCardAction" in response.text


def test_database_partial_requires_authentication():
    app = create_app()
    client = TestClient(app)
    response = client.get("/admin/partials/config/database")
    assert response.status_code == 401


def test_database_partial_uses_span_labels_in_action_buttons():
    app = create_app()
    app.dependency_overrides[deps.get_admin_session_context] = _fake_admin_context
    app.dependency_overrides[deps.get_db_session] = _fake_db_session
    client = TestClient(app)

    class DummyConfig:
        def model_dump(self) -> dict:
            return {
                "host": "127.0.0.1",
                "port": 5432,
                "database": "ctip",
                "user": "appuser",
                "sslmode": "disable",
                "password_set": True,
            }

    class DummyFirebirdConfig:
        def model_dump(self) -> dict:
            return {
                "mode": "network",
                "host": "192.168.0.8",
                "port": 3050,
                "database": "C:/MS/BAZA/MS.FDB",
                "user": "SYSDBA",
                "charset": "WIN1250",
                "role": None,
                "local_copy_path": "inbox/firebird/ms_local.fdb",
                "password_set": True,
            }

    class DummyFirebirdVConfig:
        def model_dump(self) -> dict:
            return {
                "host": "192.168.0.8",
                "port": 3050,
                "database": "D:/bazavmantenance/BAZA_CPC.FDB",
                "user": "SYSDBA",
                "charset": "WIN1250",
                "role": None,
                "password_set": True,
            }

    class DummyKpSourceConfig:
        def model_dump(self) -> dict:
            return {
                "csv_directory": "inbox/ewidencja",
                "csv_pattern": "DPLAC*.csv",
                "email_lookback_months": 5,
            }

    with (
        patch(
            "app.web.admin_ui.load_database_config",
            AsyncMock(return_value=DummyConfig()),
        ),
        patch(
            "app.web.admin_ui.load_firebird_config",
            AsyncMock(return_value=DummyFirebirdConfig()),
        ),
        patch(
            "app.web.admin_ui.load_firebird_vmaintenance_config",
            AsyncMock(return_value=DummyFirebirdVConfig()),
        ),
        patch(
            "app.web.admin_ui.load_kp_repair_source_config",
            AsyncMock(return_value=DummyKpSourceConfig()),
        ),
    ):
        response = client.get(
            "/admin/partials/config/database",
            headers={"X-Admin-Session": "token"},
        )

    assert response.status_code == 200
    html = response.text
    assert '<template x-if="!saving">' not in html
    assert '<template x-if="saving">' not in html
    assert '<template x-if="!testing">' not in html
    assert '<template x-if="testing">' not in html
    assert "x-text=\"saving ? 'Trwa zapisywanie…' : 'Zapisz konfigurację'\"" in html
    assert "x-text=\"testing ? 'Testowanie…' : 'Testuj połączenie'\"" in html
    assert 'id="firebird-ms-config"' in html
    assert 'x-data="firebirdMsConfig()"' in html
    assert 'data-config-label="Menadżer Serwisu"' in html
    assert 'data-save-endpoint="/admin/config/firebird"' in html
    assert 'data-test-endpoint="/admin/firebird/test"' in html
    assert 'data-save-endpoint="/admin/config/firebird-vmaintenance"' in html
    assert 'data-test-endpoint="/admin/firebird/test-vmaintenance"' in html
    assert "C:/MS/BAZA/MS.FDB" in html
    assert "D:/bazavmantenance/BAZA_CPC.FDB" in html


def test_firebird_partial_requires_authentication():
    app = create_app()
    client = TestClient(app)
    response = client.get("/admin/partials/config/firebird")
    assert response.status_code == 401


def test_firebird_partial_uses_span_labels_in_action_buttons():
    app = create_app()
    app.dependency_overrides[deps.get_admin_session_context] = _fake_admin_context
    app.dependency_overrides[deps.get_db_session] = _fake_db_session
    client = TestClient(app)

    class DummyConfig:
        def model_dump(self) -> dict:
            return {
                "mode": "network",
                "host": "192.168.0.8",
                "port": 3050,
                "database": "C:/MS/BAZA/MS.FDB",
                "user": "SYSDBA",
                "charset": "UTF8",
                "role": None,
                "local_copy_path": "inbox/firebird/ms_local.fdb",
                "password_set": True,
            }

    with patch(
        "app.web.admin_ui.load_firebird_config",
        AsyncMock(return_value=DummyConfig()),
    ):
        response = client.get(
            "/admin/partials/config/firebird",
            headers={"X-Admin-Session": "token"},
        )

    assert response.status_code == 200
    html = response.text
    assert '<template x-if="!saving">' not in html
    assert '<template x-if="saving">' not in html
    assert '<template x-if="!testing">' not in html
    assert '<template x-if="testing">' not in html
    assert "x-text=\"saving ? 'Trwa zapisywanie…' : 'Zapisz konfigurację'\"" in html
    assert "x-text=\"testing ? 'Testowanie…' : 'Testuj połączenie'\"" in html
    assert 'x-data="firebirdMsConfig()"' in html
    assert 'data-config-label="Menadżer Serwisu"' in html
    assert 'data-save-endpoint="/admin/config/firebird"' in html
    assert 'data-test-endpoint="/admin/firebird/test"' in html
    assert '<option value="network">Baza sieciowa</option>' in html
    assert '<option value="local">Baza lokalna</option>' in html
    assert ":disabled=\"mode === 'local'\"" in html


def test_kp_repair_partial_requires_authentication():
    app = create_app()
    client = TestClient(app)
    response = client.get("/admin/partials/kp-repair")
    assert response.status_code == 401


def test_kp_repair_partial_renders_actions():
    app = create_app()
    app.dependency_overrides[deps.get_admin_session_context] = _fake_admin_context
    app.dependency_overrides[deps.get_db_session] = _fake_db_session
    client = TestClient(app)

    class DummyConfig:
        def model_dump(self) -> dict:
            return {
                "csv_directory": "inbox/ewidencja",
                "csv_pattern": "DPLAC*.csv",
                "email_lookback_months": 5,
            }

    with patch(
        "app.web.admin_ui.load_kp_repair_source_config",
        AsyncMock(return_value=DummyConfig()),
    ):
        response = client.get(
            "/admin/partials/kp-repair",
            headers={"X-Admin-Session": "token"},
        )

    assert response.status_code == 200
    html = response.text
    assert "Naprawa KP/xxxx" in html
    assert "Raport ilości /V /E /R" in html
    assert "Usuń wpisy /V /E /R" in html
    assert "Zaktualizuj wg źródeł" in html


def test_ctip_partial_requires_authentication():
    app = create_app()
    client = TestClient(app)
    response = client.get("/admin/partials/config/ctip")
    assert response.status_code == 401


def test_sms_partial_requires_authentication():
    app = create_app()
    client = TestClient(app)
    response = client.get("/admin/partials/config/sms")
    assert response.status_code == 401


def test_ctip_live_partial_requires_authentication():
    app = create_app()
    client = TestClient(app)
    response = client.get("/admin/partials/ctip/live")
    assert response.status_code == 401


def test_call_sms_partial_keeps_bulk_form_inside_call_sms_scope():
    app = create_app()
    app.dependency_overrides[deps.get_admin_session_context] = _fake_admin_context
    app.dependency_overrides[deps.get_db_session] = _fake_db_session
    client = TestClient(app)

    class DummyConfig:
        def model_dump(self) -> dict:
            return {}

    with patch(
        "app.web.admin_ui.load_call_sms_config",
        AsyncMock(return_value=DummyConfig()),
    ):
        response = client.get(
            "/admin/partials/call-sms",
            headers={"X-Admin-Session": "token"},
        )

    assert response.status_code == 200
    html = response.text
    section_start = html.index('x-data="callSmsConfig()"')
    section_end = html.index("</section>")
    bulk_field = html.index('id="bulk-direction"')
    assert section_start < bulk_field < section_end
    assert '<template x-if="!bulkSending">' not in html
    assert '<template x-if="bulkSending">' not in html


def test_email_partial_requires_authentication():
    app = create_app()
    client = TestClient(app)
    response = client.get("/admin/partials/config/email")
    assert response.status_code == 401


def test_form_handling_partial_requires_authentication():
    app = create_app()
    client = TestClient(app)
    response = client.get("/admin/partials/config/form-handling")
    assert response.status_code == 401


def test_form_handling_partial_renders_templates_and_public_url():
    app = create_app()
    app.dependency_overrides[deps.get_admin_session_context] = _fake_admin_context
    app.dependency_overrides[deps.get_db_session] = _fake_db_session
    client = TestClient(app)

    class DummyConfig:
        def model_dump(self) -> dict:
            return {
                "public_base_url": "https://form.example.com",
                "invite_sms_template": "SMS: {form_url}",
                "invite_email_subject": "Formularz dla {customer_name}",
                "invite_email_body": "Link: {form_url}",
                "submission_email_subject": "Potwierdzenie dla {company_name}",
                "submission_email_body": "Firma: {company_name}",
                "owner_sms_template": "Klient {company_name}",
            }

    with patch(
        "app.web.admin_ui.load_form_handling_config",
        AsyncMock(return_value=DummyConfig()),
    ):
        response = client.get(
            "/admin/partials/config/form-handling",
            headers={"X-Admin-Session": "token"},
        )

    assert response.status_code == 200
    html = response.text
    assert "Obsługa formularza" in html
    assert "Adres publiczny formularza" in html
    assert "Interakcje po zapisaniu formularza" in html
    assert "Podgląd SMS do klienta" in html
    assert "Podgląd potwierdzenia e-mail" in html
    assert 'x-data="formHandlingConfig()"' in html


def test_backups_partial_requires_authentication():
    app = create_app()
    client = TestClient(app)
    response = client.get("/admin/partials/backups")
    assert response.status_code == 401


def test_backups_partial_renders_listing():
    app = create_app()
    app.dependency_overrides[deps.get_admin_session_context] = _fake_admin_context
    client = TestClient(app)

    now = datetime.now(UTC)
    fake_entries = [
        BackupFileInfo(
            name="backup_2025-10-11.dump",
            size_bytes=1024,
            modified_at=now,
            status="READY",
        )
    ]

    with patch("app.web.admin_ui.list_backup_files", return_value=fake_entries):
        response = client.get(
            "/admin/partials/backups",
            headers={"X-Admin-Session": "token"},
        )

    assert response.status_code == 200
    assert "Kopie zapasowe" in response.text
    assert "backup_2025-10-11.dump" in response.text


def test_users_partial_renders_listing():
    app = create_app()
    app.dependency_overrides[deps.get_admin_session_context] = _fake_admin_context
    app.dependency_overrides[deps.get_db_session] = _fake_db_session
    client = TestClient(app)

    now = datetime.now(UTC)
    user = AdminUser(
        id=5,
        email="panel@example.com",
        first_name="Pawel",
        last_name="Serwis",
        internal_ext="150",
        role="operator",
        password_hash="hash",
        is_active=True,
        is_salesperson=True,
        created_at=now,
        updated_at=now,
        mobile_phone="+48600900900",
    )
    row = admin_users.UserRow(user=user, sessions_active=2, last_login_at=now)

    with patch("app.web.admin_ui.admin_users.list_users", AsyncMock(return_value=[row])):
        response = client.get(
            "/admin/partials/users",
            headers={"X-Admin-Session": "token"},
        )

    assert response.status_code == 200
    assert "users-table" in response.text
    assert "panel@example.com" in response.text
    assert "data-can-manage='true'" in response.text
    assert "Telefon" in response.text
    assert "Handlowiec" in response.text
    assert "+48600900900" in response.text


def test_root_index_renders_portal_login():
    app = create_app()
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "root-login-form" in response.text
    assert "Dostępne sekcje" not in response.text


def test_choice_page_renders_sections_view():
    app = create_app()
    client = TestClient(app)
    response = client.get("/choice")
    assert response.status_code == 200
    assert "Dostępne sekcje" in response.text
    assert "root-sections-card" in response.text
    assert 'href="/flow"' in response.text
    assert 'href="/device"' in response.text


def test_flow_page_renders_sections_layout():
    app = create_app()
    client = TestClient(app)
    response = client.get("/flow")
    assert response.status_code == 200
    assert "Operacyjny widok procesu" in response.text
    assert "Obsluga umow" in response.text
    assert "Obsluga urzadzen" in response.text
    assert "Harmonogram dowozow" in response.text
    assert "flow-nav-btn" in response.text
    assert "Wybor sekcji" in response.text
    assert "flow-user-chip" in response.text
    assert "flow-form-detail-modal" in response.text
    assert "Podglad formularza" in response.text
    assert "flow-workflow-modal" in response.text
    assert "Workflow formularza" in response.text
    assert "Proforma na bank (domyslnie aktywna)" in response.text
    assert "Status sprawy po proformie" in response.text
    assert "Uzgodnienia dostawy" in response.text
    assert "Dane dla handlowca" in response.text


def test_flow_script_exposes_delete_form_action_with_confirmation():
    app = create_app()
    client = TestClient(app)
    response = client.get("/static/flow/flow.js")
    assert response.status_code == 200
    assert "data-delete-form-id" in response.text
    assert 'method: "DELETE"' in response.text
    assert "Czy na pewno chcesz usunac formularz" in response.text


def test_device_page_renders_devices_layout():
    app = create_app()
    client = TestClient(app)
    response = client.get("/device")
    assert response.status_code == 200
    assert "Operacyjna obsluga urzadzen" in response.text
    assert "Obsluga urzadzen" in response.text
    assert "device-user-chip" in response.text
    assert "device-refresh" in response.text
    assert "device-intakes-body" in response.text
    assert "device-model-duplicates-body" in response.text
    assert "device-process-rules" in response.text
    assert "Status i nastepny krok" in response.text
    assert "Reguly procesu" in response.text
    assert "Wnioski operacyjne" in response.text
    assert "PZ urzadzen" in response.text


def test_flow_invoice_preview_page_renders_sample_document():
    app = create_app()
    client = TestClient(app)
    response = client.get("/flow/proforma-wizualizacja")
    assert response.status_code == 200
    assert "Faktura Pro Forma" in response.text
    assert "2/proforma/2026" in response.text
    assert "ZANOX SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ" in response.text
    assert "IMCTEST" in response.text
    assert "Zapisz PDF A4" in response.text
    assert "data-print-a4" in response.text


def test_flow_invoice_preview_page_v1_renders_original_like_layout():
    app = create_app()
    client = TestClient(app)
    response = client.get("/flow/proforma-wizualizacja1")
    assert response.status_code == 200
    assert "Wizualizacja 1" in response.text
    assert "Faktura Pro Forma nr:" in response.text
    assert "Strona 1 z 1" in response.text
    assert "Menadżer Serwisu i Fakturka - Serwisoft.pl" in response.text
    assert "ZMIANA NUMERU KONTA ! UWAGA:" in response.text
    assert "Zapisz PDF A4" in response.text
    assert "data-print-a4" in response.text


def test_flow_invoice_preview_live_page_renders_document_from_firebird_data():
    app = create_app()
    client = TestClient(app)
    preview_payload = {
        "document_title": "Faktura Pro Forma",
        "document_number": "4/proforma/2026",
        "place_of_issue": "Komorniki",
        "service_date": "17.03.2026",
        "issue_date": "17.03.2026",
        "payment_due_date": "31.03.2026",
        "payment_method": "Gotówka",
        "buyer": {
            "name": "FLOW TEST NOWY KLIENT SP. Z O.O.",
            "street": "ul. Testowa 10",
            "postal_code": "00-010",
            "city": "Poznan",
            "country_code": "PL",
            "nip": "6112998877",
        },
        "seller": {
            "name": "KSERO - PARTNER MIKOŁAJ FRĄSZCZAK SPÓŁKA KOMANDYTOWA",
            "street": "ul. Fabianowska 165",
            "postal_code": "62-052",
            "city": "Komorniki",
            "nip": "7773404157",
            "bank_account": "PKO BP S.A. 33102040270000190218474209",
        },
        "line_items": [
            {
                "lp": 1,
                "name": "RICOH IMC 3500",
                "serial_number": "3111RB80109",
                "quantity": "1,00",
                "unit": "szt.",
                "net_price": "3 024,39 zł",
                "net_value": "3 024,39 zł",
                "vat_rate": "23 %",
                "vat_value": "695,61 zł",
                "gross_value": "3 720,00 zł",
            }
        ],
        "totals": {
            "net": "3 024,39 zł",
            "vat": "695,61 zł",
            "gross": "3 720,00 zł",
            "paid": "0,00 zł",
            "remaining": "3 720,00 zł",
            "gross_words": "trzy tysiace siedemset dwadziescia zlotych 00/100 gr.",
        },
        "notes": ["FLOW formularz 4"],
        "issuer": "Operator Testowy",
    }

    with patch("app.web.flow_ui.load_proforma_preview_data", return_value=preview_payload):
        response = client.get("/flow/proforma/70001?variant=v1")

    assert response.status_code == 200
    assert "4/proforma/2026" in response.text
    assert "FLOW TEST NOWY KLIENT SP. Z O.O." in response.text
    assert "RICOH IMC 3500" in response.text
    assert "Zapisz PDF A4" in response.text


def test_flow_invoice_pdf_file_returns_backend_pdf():
    app = create_app()
    client = TestClient(app)
    pdf_path = Path("inbox/faktura/generated/test_flow_invoice_70001.pdf")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n")

    try:
        with patch("app.web.flow_ui.ensure_proforma_pdf_file", return_value=pdf_path):
            response = client.get("/flow/proforma/70001/pdf")
    finally:
        pdf_path.unlink(missing_ok=True)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")


def test_genform_page_renders_layout():
    app = create_app()
    client = TestClient(app)
    response = client.get("/genform")
    assert response.status_code == 200
    assert "Generator formularzy" in response.text
    assert f"/static/root/genform.js?v={app.version}" in response.text
    assert f"/static/root/genform.css?v={app.version}" in response.text
    assert "genform-login-form" in response.text
    assert "genform-password-toggle" in response.text
    assert "genform-detail-modal" in response.text
    assert "genform-detail-summary" in response.text
    assert "genform-detail-company" in response.text
    assert "genform-detail-representatives" in response.text
    assert "genform-detail-print" in response.text
    assert "genform-detail-pdf" in response.text
    assert "Utworzone przez" in response.text
    assert "Status MS" in response.text
    assert f"Wersja {app.version}" in response.text


def test_genform_js_has_copy_fallback_for_non_secure_context():
    js_path = Path("app/static/root/genform.js")
    content = js_path.read_text(encoding="utf-8")
    assert 'document.execCommand("copy")' in content
    assert "window.isSecureContext" in content
    assert "function renderDetailSections(detailData)" in content
    assert "button[data-copy-value]" in content
    assert "window.print();" in content
    assert 'item.ms_status || "—"' in content
    assert '{ label: "Ważny do"' not in content
    assert 'label: "E-mail firmowy"' in content
    assert 'label: "Telefon reprezentanta"' in content


def test_genform_print_css_keeps_first_page_compact_with_inner_margins():
    css_path = Path("app/static/root/genform.css")
    content = css_path.read_text(encoding="utf-8")
    assert "size: A4 portrait;" in content
    assert "#genform-detail-summary" in content
    assert "grid-template-columns: repeat(4, minmax(0, 1fr));" in content
    assert "margin: 7mm;" in content
    assert "padding: 4mm 5mm 5mm;" in content
    assert "break-inside: avoid;" in content
    assert "page-break-before: always;" not in content


def test_admin_users_modal_edit_form_is_not_hidden_by_x_cloak():
    template = Path("app/templates/admin/partials/users.html").read_text(encoding="utf-8")
    assert '@submit.prevent="saveModal()"' in template
    assert "modal-user-is-salesperson" in template
    assert '@submit.prevent="saveModal()" x-show="canManage"' not in template


def test_public_form_template_has_date_mask_and_auto_expiry_logic():
    template = Path("app/templates/public/form_fill.html").read_text(encoding="utf-8")
    assert 'id="rep_document_issue_date" type="text" inputmode="numeric" maxlength="10"' in template
    assert (
        'id="rep_document_expiry_date" type="text" inputmode="numeric" maxlength="10"' in template
    )
    assert "E-mail firmowy" in template
    assert "Nr telefonu firmowy" in template
    assert 'id="rep_representative_email"' in template
    assert 'id="rep_representative_phone"' in template
    assert "function formatDateTyping(value)" in template
    assert "function closeDatePicker(pickerInput, textInput)" in template
    assert "function syncDocumentExpiryFromIssue(force = false)" in template
    assert "addYearsToDisplayDate(issueDate, 10)" in template
