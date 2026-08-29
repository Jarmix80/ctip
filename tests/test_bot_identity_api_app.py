"""Testy izolowanej aplikacji API katalogu tożsamości botów."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.deps import get_db_session
from app.bot_identity_api_app import create_bot_identity_api_app
from app.core.config import settings


def test_bot_identity_api_exposes_only_service_routes() -> None:
    app = create_bot_identity_api_app()
    paths = {route.path for route in app.routes}

    assert "/health" in paths
    assert "/internal/v1/identities/resolve-phone" in paths
    assert "/v1/capabilities" in paths
    assert "/v1/device-model-images/{image_ref}" in paths
    assert "/v1/cases" in paths
    assert "/admin" not in paths
    assert "/api/crm/v1/cases" not in paths


def test_bot_identity_api_health_does_not_require_token() -> None:
    client = TestClient(create_bot_identity_api_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "ctip-bot-identity-api",
    }


def test_capabilities_match_chat_kp_contract(monkeypatch) -> None:
    monkeypatch.setattr(settings, "crm_enabled", True)
    monkeypatch.setattr(settings, "bot_identity_chat_token", "chat-test-token")
    monkeypatch.setattr(settings, "bot_identity_voice_token", "voice-test-token")
    client = TestClient(create_bot_identity_api_app())

    response = client.get(
        "/v1/capabilities",
        headers={"Authorization": "Bearer chat-test-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "service": "ctip",
        "contract_version": "1.0",
        "categories": [
            "sales",
            "service",
            "accounting",
            "other",
            "contracts_settlements",
        ],
        "customer_resolution": True,
        "sms_verification": True,
        "masked_devices": True,
        "idempotent_sms": True,
        "idempotent_cases": True,
    }


def test_case_endpoint_returns_403_for_expired_sms_challenge(monkeypatch) -> None:
    class DummySession:
        async def commit(self) -> None:
            raise AssertionError("Odrzucona sprawa nie może zostać zatwierdzona.")

    async def session_override():
        yield DummySession()

    async def reject_expired_challenge(*args, **kwargs):
        raise PermissionError("Weryfikacja SMS jest nieważna albo wygasła.")

    monkeypatch.setattr(settings, "crm_enabled", True)
    monkeypatch.setattr(settings, "bot_identity_chat_token", "chat-test-token")
    monkeypatch.setattr(
        "app.api.routes.crm.create_chat_case",
        reject_expired_challenge,
    )
    app = create_bot_identity_api_app()
    app.dependency_overrides[get_db_session] = session_override
    client = TestClient(app)

    response = client.post(
        "/v1/cases",
        headers={"Authorization": "Bearer chat-test-token"},
        json={
            "source_channel": "chat",
            "source_system": "chat_kp",
            "conversation_ref": "expired-challenge-case",
            "category": "service",
            "summary": "Test wygasłego wyzwania",
            "customer_ref": "739",
            "customer_match_status": "exact",
            "customer_name": "Firma testowa",
            "device_refs": ["device-ref-1"],
            "sms_challenge_id": "expired-challenge",
            "phone_verification_status": "sms_verified_known",
            "customer_confirmed": True,
            "privacy_notice_accepted": True,
            "privacy_notice_version": "2026-07",
            "privacy_notice_checksum": "f" * 64,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Weryfikacja SMS jest nieważna albo wygasła."


def test_model_image_endpoint_returns_only_registered_image(
    monkeypatch,
    tmp_path: Path,
) -> None:
    image_ref = "a" * 64
    image_path = tmp_path / f"{image_ref}.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test-image")
    monkeypatch.setattr(settings, "bot_identity_model_image_root", str(tmp_path))
    monkeypatch.setattr(settings, "bot_identity_image_max_bytes", 1024)
    monkeypatch.setattr(settings, "bot_identity_image_cache_seconds", 600)
    client = TestClient(create_bot_identity_api_app())

    response = client.get(f"/v1/device-model-images/{image_ref}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "public, max-age=600, immutable"
    assert response.content == image_path.read_bytes()


def test_model_image_endpoint_blocks_path_traversal(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "bot_identity_model_image_root", str(tmp_path))
    client = TestClient(create_bot_identity_api_app())

    invalid_ref = client.get("/v1/device-model-images/not-a-safe-reference")
    traversal = client.get("/v1/device-model-images/..%2Fsecret.png")

    assert invalid_ref.status_code == 404
    assert traversal.status_code == 404


def test_model_image_endpoint_rejects_too_large_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    image_ref = "b" * 64
    (tmp_path / f"{image_ref}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 128)
    monkeypatch.setattr(settings, "bot_identity_model_image_root", str(tmp_path))
    monkeypatch.setattr(settings, "bot_identity_image_max_bytes", 32)
    client = TestClient(create_bot_identity_api_app())

    response = client.get(f"/v1/device-model-images/{image_ref}")

    assert response.status_code == 413
