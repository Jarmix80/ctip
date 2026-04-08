from starlette.testclient import TestClient

from app.public_forms_app import create_public_forms_app


def test_public_forms_app_exposes_health_and_root_only():
    app = create_public_forms_app()
    client = TestClient(app)

    health_response = client.get("/health")
    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"

    root_response = client.get("/")
    assert root_response.status_code == 200
    assert "Publiczny formularz CTIP" in root_response.text
    assert root_response.headers["cache-control"] == "no-store"


def test_public_forms_app_does_not_expose_admin_panel():
    app = create_public_forms_app()
    client = TestClient(app)

    response = client.get("/admin")
    assert response.status_code == 404


def test_public_forms_app_rejects_untrusted_host():
    app = create_public_forms_app()
    client = TestClient(app)

    response = client.get("/health", headers={"host": "evil.example"})
    assert response.status_code == 400
