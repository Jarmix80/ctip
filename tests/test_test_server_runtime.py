"""Testy stabilnego runtime serwera testowego."""

from __future__ import annotations

from pathlib import Path

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
    services["web"]["environment"].update(
        {
            "DPD_ENABLED": "true",
            "DPD_MODE": "mock",
            "DPD_INFO_ENABLED": "false",
            "SHIPPING_ENABLED": "true",
            "SHIPPING_CATALOG_MUTATIONS_ENABLED": "true",
            "SHIPPING_FULFILLMENT_ENABLED": "true",
            "SHIPPING_COMPATIBILITY_WEB_ENABLED": "false",
            "SHIPPING_TEST_FIREBIRD_WRITES": "false",
        }
    )
    for name in ("bot-identity-api", "bot-identity-sync"):
        services[name]["environment"].update(
            {
                "BOT_IDENTITY_SECRET_KEY": "test-secret-key",
                "BOT_IDENTITY_CHAT_TOKEN": "test-chat-token",
                "BOT_IDENTITY_VOICE_TOKEN": "test-voice-token",
                "BOT_IDENTITY_TEST_SMS_CODE": "123456",
            }
        )
    services["log-init"] = {
        "image": "ctip/test-runtime:0123456789abcdef0123456789abcdef01234567",
        "user": "0:0",
        "volumes": [{"type": "volume", "source": "logs", "target": "/app/docs/LOG"}],
    }
    for name in ("web", "collector", "sms-sender"):
        services[name]["depends_on"] = {"log-init": {"condition": "service_completed_successfully"}}
    services["firebird"] = {
        "volumes": [{"type": "volume", "source": "firebird-config", "target": "/firebird"}]
    }
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


def test_server_compose_rejects_unsafe_shipping_and_missing_bot_secret() -> None:
    """Kontrola blokuje prawdziwe DPD oraz niepełne sekrety Bot Identity."""
    config = _config()
    config["services"]["web"]["environment"]["DPD_MODE"] = "production"
    config["services"]["web"]["environment"]["SHIPPING_ENABLED"] = "false"
    config["services"]["bot-identity-sync"]["environment"]["BOT_IDENTITY_SECRET_KEY"] = ""

    issues = collect_issues(
        config,
        expected_image="ctip/test-runtime:0123456789abcdef0123456789abcdef01234567",
    )

    assert any("DPD_MODE=mock" in issue for issue in issues)
    assert any("SHIPPING_ENABLED=true" in issue for issue in issues)
    assert any("bot-identity-sync" in issue and "SECRET_KEY" in issue for issue in issues)


def test_server_commands_do_not_depend_on_legacy_network_name() -> None:
    """Obsługa serwera wykrywa sieć bazy zamiast używać nazwy starego stosu."""
    script = Path("ctiptest").read_text(encoding="utf-8")

    assert "--network ctip-prod-mirror_ctip_test_internal" not in script
    assert "test_database_network" in script
    assert "server-migrate)" in script
    assert "python -m alembic upgrade head" in script
    assert "server-reconcile)" in script
    assert "scripts/reconcile_test_alembic_state.py" in script
    assert "--backup-manifest /backup/SHA256SUMS" in script
    assert "-e PYTHONPATH=/app" in script
    assert "wait_for_persistent_services" in script
    assert "scripts/check_bot_identity_test_runtime.py" in script
    assert "ensure_no_recent_runtime_errors" in script
    assert "env -i" in script
    assert 'CTIP_ENV_FILE="${ENV_FILE}"' in script
