"""Testy deklaratywnego wdrożenia usług katalogu tożsamości."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _compose() -> dict:
    """Wczytuje konfigurację Compose bez rozwijania zmiennych i sekretów."""

    return yaml.safe_load((ROOT / "compose.bot-identity.yml").read_text(encoding="utf-8"))


def test_bot_identity_image_contains_application_without_repository_mount() -> None:
    dockerfile = (ROOT / "Dockerfile.bot-identity").read_text(encoding="utf-8")

    assert "FROM python@sha256:" in dockerfile
    assert "COPY --chown=10001:10001 app ./app" in dockerfile
    assert "USER 10001:10001" in dockerfile

    for service in _compose()["services"].values():
        assert "volumes" not in service
        assert service["read_only"] is True
        assert service["env_file"] == ["${BOT_IDENTITY_ENV_FILE:-.env.bot-identity.runtime}"]


def test_compose_preserves_service_names_commands_and_network_isolation() -> None:
    compose = _compose()
    api = compose["services"]["api"]
    worker = compose["services"]["sync"]

    assert api["container_name"] == "ctip-bot-identity-api"
    assert worker["container_name"] == "ctip-bot-identity-sync"
    assert "app.bot_identity_api_app:app" in api["command"]
    assert "app.bot_identity_worker" in worker["command"]
    assert set(api["networks"]) == {"chat_kp", "ctip_test_internal"}
    assert worker["networks"] == ["ctip_test_internal"]
    assert api["networks"]["chat_kp"]["aliases"] == ["ctip-bot-api"]
    assert compose["networks"]["chat_kp"]["name"] == "chat_kp_chat_kp"
    assert compose["networks"]["ctip_test_internal"]["name"] == ("ctip-test_ctip_test_internal")


def test_runtime_example_enforces_test_database_and_read_only_firebird() -> None:
    example = (ROOT / "deploy/bot-identity.runtime.example").read_text(encoding="utf-8")

    assert "PGDATABASE=ctip_test" in example
    assert "FB_DATABASE=/data/BAZAMS_TEST.FDB" in example
    assert "FB_ALLOW_WRITES=false" in example
    assert "CRM_LAB_MODE=true" in example
    assert "SMS_TEST_MODE=true" in example
    assert "BOT_IDENTITY_TEST_SMS_CODE=123456" in example
    assert "192.168.0.8" not in example
    assert "192.168.0.11" not in example
