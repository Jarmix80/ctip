"""Testy izolowanej bramy formularzy i CHAT_KP."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from app.core.config import settings
from app.lab_portal_app import create_lab_portal_app


def _enable_safe_lab(monkeypatch) -> None:
    monkeypatch.setattr(settings, "crm_enabled", True)
    monkeypatch.setattr(settings, "crm_lab_mode", True)
    monkeypatch.setattr(settings, "crm_public_prototype_mode", True)
    monkeypatch.setattr(settings, "pg_database", "ctip_test")
    monkeypatch.setattr(settings, "sms_test_mode", True)
    monkeypatch.setattr(settings, "fb_host", "127.0.0.1")
    monkeypatch.setattr(settings, "fb_database", "/tmp/test_ms.fdb")
    monkeypatch.setattr(settings, "fb_mode", "local")
    monkeypatch.setattr(settings, "fb_allow_writes", False)


def _ticket(secret: str) -> str:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "exp": int(time.time()) + 300,
                    "nonce": "testowy-nonce",
                    "aud": "kp-ctip-lab",
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def test_lab_portal_renders_forms_only_in_safe_environment(monkeypatch) -> None:
    _enable_safe_lab(monkeypatch)
    monkeypatch.setattr(settings, "crm_lab_iframe_secret", None)
    client = TestClient(create_lab_portal_app())

    response = client.get("/forms")

    assert response.status_code == 200
    assert "Jedno wejście dla wszystkich spraw klientów" in response.text
    assert "Bez SMS, e-mail i zapisów do Firebird" in response.text
    assert response.headers["x-robots-tag"].startswith("noindex")
    assert "frame-ancestors" in response.headers["content-security-policy"]


def test_lab_portal_rejects_unsafe_database(monkeypatch) -> None:
    _enable_safe_lab(monkeypatch)
    monkeypatch.setattr(settings, "pg_database", "ctip")
    monkeypatch.setattr(settings, "crm_lab_iframe_secret", None)
    client = TestClient(create_lab_portal_app())

    response = client.get("/forms")

    assert response.status_code == 503
    assert "ctip_test" in response.json()["detail"]


def test_lab_portal_accepts_test_firebird_docker_alias(monkeypatch) -> None:
    _enable_safe_lab(monkeypatch)
    monkeypatch.setattr(settings, "fb_host", "firebird")
    monkeypatch.setattr(settings, "fb_database", "/data/BAZAMS_TEST.FDB")
    monkeypatch.setattr(settings, "crm_lab_iframe_secret", None)
    client = TestClient(create_lab_portal_app())

    response = client.get("/forms")

    assert response.status_code == 200


def test_lab_portal_rejects_firebird_with_writes_enabled(monkeypatch) -> None:
    _enable_safe_lab(monkeypatch)
    monkeypatch.setattr(settings, "fb_allow_writes", True)
    monkeypatch.setattr(settings, "crm_lab_iframe_secret", None)
    client = TestClient(create_lab_portal_app())

    response = client.get("/forms")

    assert response.status_code == 503
    assert "bez zapisu" in response.json()["detail"]


def test_signed_iframe_ticket_creates_session(monkeypatch) -> None:
    _enable_safe_lab(monkeypatch)
    secret = "wspoldzielony-sekret-testowy"
    monkeypatch.setattr(settings, "crm_lab_iframe_secret", secret)
    client = TestClient(create_lab_portal_app())

    denied = client.get("/scenarios")
    allowed = client.get("/scenarios", params={"ticket": _ticket(secret)})
    session = client.get("/scenarios")

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert "ctip_lab_portal" in allowed.cookies
    assert session.status_code == 200


def test_chat_entry_redirects_to_local_proxy(monkeypatch) -> None:
    _enable_safe_lab(monkeypatch)
    monkeypatch.setattr(settings, "crm_lab_iframe_secret", None)
    client = TestClient(create_lab_portal_app())

    response = client.get("/chat", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/widget/v1/widget.html"
