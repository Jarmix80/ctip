"""Testy stabilnego runtime serwera testowego."""

from __future__ import annotations

from scripts.test_server_compose_check import collect_issues


def _config() -> dict:
    services = {}
    for name in (
        "mock-ctip",
        "web",
        "forms-public",
        "collector",
        "sms-sender",
        "bot-identity-api",
        "bot-identity-sync",
        "crm",
        "lab",
    ):
        services[name] = {
            "image": "ctip/test-runtime:0123456789abcdef0123456789abcdef01234567",
            "environment": {
                "CTIP_RUNTIME_PROFILE": "test",
                "PGDATABASE": "ctip_test",
                "SMS_TEST_MODE": "true",
                "OUTBOUND_DELIVERY_MODE": "capture",
                "BLOCK_CLIENT_COMMUNICATIONS": "true",
                "FB_ALLOW_WRITES": "false",
            },
            "volumes": [],
        }
    services["web"]["volumes"] = [
        {
            "type": "bind",
            "source": "/srv/ctip-test/secrets",
            "target": "/run/secrets",
        }
    ]
    services["test-gateway"] = {"ports": [{"published": "8000", "target": 8000}]}
    return {"name": "ctip-test", "services": services}


def test_server_compose_accepts_immutable_test_services() -> None:
    """Poprawny stos nie zgłasza naruszeń."""
    image = "ctip/test-runtime:0123456789abcdef0123456789abcdef01234567"
    assert collect_issues(_config(), expected_image=image) == []


def test_server_compose_rejects_source_mount_and_firebird_write() -> None:
    """Kontrola blokuje montowanie kodu i zapis Firebird."""
    config = _config()
    config["services"]["sms-sender"]["volumes"] = [
        {"type": "bind", "source": "/repo", "target": "/app"}
    ]
    config["services"]["sms-sender"]["environment"]["FB_ALLOW_WRITES"] = "true"

    issues = collect_issues(
        config,
        expected_image="ctip/test-runtime:0123456789abcdef0123456789abcdef01234567",
    )

    assert any("montuje kod źródłowy" in issue for issue in issues)
    assert any("FB_ALLOW_WRITES=false" in issue for issue in issues)


def test_server_compose_rejects_old_port_and_unsafe_secret_path() -> None:
    """Kontrola odrzuca port 8002 i sekret umieszczony w katalogu roboczym."""
    config = _config()
    config["services"]["web"]["volumes"] = [
        {
            "type": "bind",
            "source": "/home/marcin/projects/ctip/.codex/worktree/runtime/secrets",
            "target": "/run/secrets",
        }
    ]
    config["services"]["web"]["ports"] = [{"published": "8002", "target": 8000}]

    issues = collect_issues(
        config,
        expected_image="ctip/test-runtime:0123456789abcdef0123456789abcdef01234567",
    )

    assert any("inbox ani .codex" in issue for issue in issues)
    assert any("8002" in issue for issue in issues)
