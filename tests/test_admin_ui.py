from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from app.api import deps
from app.main import create_app
from app.models import AdminUser
from app.services import admin_users


async def _fake_admin_context():
    class DummySession:
        client_ip = "127.0.0.1"

    class DummyUser:
        id = 1
        role = "admin"
        is_active = True

    return DummySession(), DummyUser()


async def _fake_db_session():
    yield None


def test_admin_index_renders_layout():
    app = create_app()
    client = TestClient(app)
    response = client.get("/admin")
    assert response.status_code == 200
    assert "Logowanie administratora" in response.text
    assert "login-form" in response.text


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


def test_email_partial_requires_authentication():
    app = create_app()
    client = TestClient(app)
    response = client.get("/admin/partials/config/email")
    assert response.status_code == 401


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
    assert "+48600900900" in response.text
