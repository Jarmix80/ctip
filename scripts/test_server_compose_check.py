"""Kontrola wyrenderowanej konfiguracji serwerowego stosu testowego."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

APPLICATION_SERVICES = (
    "mock-ctip",
    "web",
    "forms-public",
    "collector",
    "sms-sender",
    "bot-identity-api",
    "bot-identity-sync",
    "crm",
    "lab",
)
ALLOWED_PUBLISHED_PORTS = {3050, 8000, 8001, 8790, 8100, 8025}


def _is_true(value: object) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def _is_false(value: object) -> bool:
    return value is False or str(value).strip().lower() in {"0", "false", "no", "off"}


def _published_port(entry: object) -> int | None:
    if isinstance(entry, dict):
        value = entry.get("published")
    else:
        value = str(entry).rsplit(":", maxsplit=1)[-1].split("/", maxsplit=1)[0]
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _published_host_ip(entry: object) -> str:
    if isinstance(entry, dict):
        return str(entry.get("host_ip") or "")
    parts = str(entry).split(":")
    return parts[0] if len(parts) >= 3 else ""


def _target_port(entry: object) -> int | None:
    if isinstance(entry, dict):
        value = entry.get("target")
    else:
        value = str(entry).rsplit(":", maxsplit=1)[-1].split("/", maxsplit=1)[0]
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _mount_target(mount: object) -> str:
    if isinstance(mount, dict):
        return str(mount.get("target") or "")
    parts = str(mount).split(":")
    return parts[1] if len(parts) > 1 else ""


def _mount_source(mount: object) -> str:
    if isinstance(mount, dict):
        return str(mount.get("source") or "")
    return str(mount).split(":", maxsplit=1)[0]


def firebird_file(config: dict[str, Any]) -> Path:
    """Wyznacza plik testowej bazy Firebird z konfiguracji Compose."""
    services = config.get("services") or {}
    firebird = services.get("firebird") or {}
    for mount in firebird.get("volumes") or []:
        if _mount_target(mount) == "/data":
            return Path(_mount_source(mount)) / "BAZAMS_TEST.FDB"
    raise ValueError("Usługa firebird nie ma montowania katalogu /data.")


def collect_issues(
    config: dict[str, Any],
    *,
    expected_image: str,
    check_filesystem: bool = False,
) -> list[str]:
    """Zwraca naruszenia izolacji i niezmienności obrazu testowego."""
    issues: list[str] = []
    if config.get("name") != "ctip-test":
        issues.append("Projekt Compose musi mieć nazwę ctip-test.")

    services = config.get("services") or {}
    log_init = services.get("log-init") or {}
    if log_init.get("image") != expected_image:
        issues.append("Usługa log-init nie używa przypiętego obrazu.")
    if str(log_init.get("user")) != "0:0":
        issues.append("Usługa log-init musi działać jednorazowo jako root.")
    if not any(_mount_target(mount) == "/app/docs/LOG" for mount in log_init.get("volumes") or []):
        issues.append("Usługa log-init nie inicjalizuje wolumenu logów.")

    for name in APPLICATION_SERVICES:
        service = services.get(name)
        if not isinstance(service, dict):
            issues.append(f"Brak usługi {name}.")
            continue
        if service.get("image") != expected_image:
            issues.append(f"Usługa {name} nie używa obrazu {expected_image}.")
        if service.get("build"):
            issues.append(f"Usługa {name} nie może budować obrazu podczas startu serwera.")
        for mount in service.get("volumes") or []:
            if _mount_target(mount) == "/app":
                issues.append(f"Usługa {name} montuje kod źródłowy do /app.")
        environment = service.get("environment") or {}
        if environment.get("CTIP_RUNTIME_PROFILE") != "test":
            issues.append(f"Usługa {name} nie ma profilu testowego.")
        if environment.get("PGDATABASE") != "ctip_test":
            issues.append(f"Usługa {name} nie wskazuje bazy ctip_test.")
        if environment.get("PGHOST") != "ctip-test-postgres":
            issues.append(f"Usługa {name} nie używa jednoznacznego hosta ctip-test-postgres.")
        if not _is_true(environment.get("SMS_TEST_MODE")):
            issues.append(f"Usługa {name} nie ma SMS_TEST_MODE=true.")
        if environment.get("OUTBOUND_DELIVERY_MODE") not in ("capture", "disabled"):
            issues.append(f"Usługa {name} nie ma bezpiecznego trybu komunikacji.")
        if not _is_true(environment.get("BLOCK_CLIENT_COMMUNICATIONS")):
            issues.append(f"Usługa {name} nie blokuje komunikacji z klientami.")
        if _is_true(environment.get("FB_ALLOW_WRITES")):
            issues.append(f"Usługa {name} musi mieć FB_ALLOW_WRITES=false.")

    for name in ("web", "collector", "sms-sender"):
        dependencies = (services.get(name) or {}).get("depends_on") or {}
        log_dependency = dependencies.get("log-init") or {}
        if log_dependency.get("condition") != "service_completed_successfully":
            issues.append(f"Usługa {name} nie czeka na inicjalizację logów.")

    firebird = services.get("firebird") or {}
    firebird_config_mounts = [
        mount for mount in firebird.get("volumes") or [] if _mount_target(mount) == "/firebird"
    ]
    if len(firebird_config_mounts) != 1 or _mount_source(firebird_config_mounts[0]).startswith("/"):
        issues.append("Firebird musi używać stabilnego nazwanego wolumenu konfiguracji.")

    postgres_network = ((services.get("postgres") or {}).get("networks") or {}).get(
        "ctip_test_internal"
    ) or {}
    if "ctip-test-postgres" not in (postgres_network.get("aliases") or []):
        issues.append("PostgreSQL nie ma jednoznacznego aliasu ctip-test-postgres.")

    web = services.get("web") or {}
    web_environment = web.get("environment") or {}
    for key in (
        "DPD_ENABLED",
        "SHIPPING_ENABLED",
        "SHIPPING_CATALOG_MUTATIONS_ENABLED",
        "SHIPPING_FULFILLMENT_ENABLED",
    ):
        if not _is_true(web_environment.get(key)):
            issues.append(f"Usługa web wymaga {key}=true w środowisku testowym.")
    if web_environment.get("DPD_MODE") != "mock":
        issues.append("Usługa web wymaga DPD_MODE=mock w środowisku testowym.")
    if not _is_true(web_environment.get("SHIPPING_GEOCODER_ENABLED")):
        issues.append("Usługa web wymaga SHIPPING_GEOCODER_ENABLED=true.")
    if web_environment.get("ADDRESY_APP_API_URL") != "https://api.adresy.app/api/v1":
        issues.append("Usługa web musi wskazywać oficjalny endpoint Adresy.app.")
    for key in (
        "DPD_INFO_ENABLED",
        "SHIPPING_COMPATIBILITY_WEB_ENABLED",
        "SHIPPING_TEST_FIREBIRD_WRITES",
    ):
        if not _is_false(web_environment.get(key)):
            issues.append(f"Usługa web wymaga {key}=false w środowisku testowym.")

    addresy_egress = services.get("addresy-egress") or {}
    if addresy_egress.get("image") != "haproxy:3.0-alpine":
        issues.append("Brama Adresy.app musi używać przypiętego obrazu HAProxy 3.0.")
    if addresy_egress.get("ports"):
        issues.append("Brama Adresy.app nie może publikować portów hosta.")
    addresy_networks = set((addresy_egress.get("networks") or {}).keys())
    if addresy_networks != {"ctip_test_internal", "ctip_test_edge"}:
        issues.append("Brama Adresy.app musi łączyć wyłącznie sieć wewnętrzną i brzegową.")
    addresy_mounts = [
        mount
        for mount in addresy_egress.get("volumes") or []
        if _mount_target(mount) == "/usr/local/etc/haproxy/haproxy.cfg"
    ]
    if len(addresy_mounts) != 1 or not _mount_source(addresy_mounts[0]).replace("\\", "/").endswith(
        "/ops/addresy-egress/haproxy.cfg"
    ):
        issues.append("Brama Adresy.app nie używa dedykowanej konfiguracji HAProxy.")
    extra_hosts = web.get("extra_hosts") or []
    if isinstance(extra_hosts, dict):
        addresy_host = extra_hosts.get("api.adresy.app")
    else:
        addresy_host = next(
            (
                str(entry).split("=", maxsplit=1)[-1].split(":", maxsplit=1)[-1]
                for entry in extra_hosts
                if str(entry).startswith(("api.adresy.app=", "api.adresy.app:"))
            ),
            None,
        )
    if addresy_host != "172.28.252.21":
        issues.append("api.adresy.app musi być kierowane wyłącznie przez bramę testową.")
    addresy_dependency = (web.get("depends_on") or {}).get("addresy-egress") or {}
    if addresy_dependency.get("condition") != "service_started":
        issues.append("Usługa web musi czekać na uruchomienie bramy Adresy.app.")

    for name in ("bot-identity-api", "bot-identity-sync"):
        environment = (services.get(name) or {}).get("environment") or {}
        for key in (
            "BOT_IDENTITY_SECRET_KEY",
            "BOT_IDENTITY_CHAT_TOKEN",
            "BOT_IDENTITY_VOICE_TOKEN",
        ):
            if not str(environment.get(key) or "").strip():
                issues.append(f"Usługa {name} nie ma wymaganej zmiennej {key}.")
        if environment.get("BOT_IDENTITY_TEST_SMS_CODE") != "123456":
            issues.append(f"Usługa {name} wymaga kodu LAB BOT_IDENTITY_TEST_SMS_CODE=123456.")

    secret_mounts = [
        mount for mount in web.get("volumes") or [] if _mount_target(mount) == "/run/secrets"
    ]
    if len(secret_mounts) != 1:
        issues.append("Usługa web musi mieć dokładnie jeden katalog /run/secrets.")
    else:
        source = Path(_mount_source(secret_mounts[0])).expanduser()
        normalized = source.resolve(strict=False).as_posix().lower()
        if "/inbox/" in f"{normalized}/" or "/.codex/" in f"{normalized}/":
            issues.append("Katalog sekretów nie może znajdować się w inbox ani .codex.")
        if check_filesystem:
            if source.is_symlink():
                issues.append("Katalog sekretów nie może być dowiązaniem symbolicznym.")
            elif not source.is_dir():
                issues.append("Katalog sekretów nie istnieje lub nie jest katalogiem.")
            else:
                for secret_file in source.iterdir():
                    if secret_file.is_file() and secret_file.stat().st_mode & 0o077:
                        issues.append(
                            f"Plik sekretu {secret_file.name} musi mieć uprawnienia 0600."
                        )

    published_8000: list[str] = []
    published_3050: list[tuple[str, str, int | None]] = []
    for name, service in services.items():
        for port in service.get("ports") or []:
            published = _published_port(port)
            if published not in ALLOWED_PUBLISHED_PORTS:
                issues.append(f"Usługa {name} publikuje niedozwolony port {published}.")
            if published == 8000:
                published_8000.append(name)
            if published == 3050:
                published_3050.append((name, _published_host_ip(port), _target_port(port)))
            if published == 8002:
                issues.append(f"Usługa {name} nie może publikować historycznego portu 8002.")
    if published_8000 != ["test-gateway"]:
        issues.append("Port 8000 może publikować wyłącznie usługa test-gateway.")
    if published_3050 != [("test-gateway", "192.168.0.9", 3050)]:
        issues.append("Port 3050 musi publikować wyłącznie test-gateway na 192.168.0.9:3050.")

    if check_filesystem:
        try:
            database_file = firebird_file(config)
        except ValueError as exc:
            issues.append(str(exc))
        else:
            if not database_file.is_file():
                issues.append("Brak testowej bazy Firebird BAZAMS_TEST.FDB.")

    return issues


def main() -> int:
    """Czyta JSON Compose i kończy się błędem przy naruszeniu izolacji."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-image")
    parser.add_argument("--check-filesystem", action="store_true")
    parser.add_argument("--print-firebird-file", action="store_true")
    args = parser.parse_args()
    config = json.load(sys.stdin)
    if args.print_firebird_file:
        try:
            print(firebird_file(config))
        except ValueError as exc:
            print(f"[BŁĄD] {exc}", file=sys.stderr)
            return 1
        return 0
    if not args.expected_image:
        parser.error("--expected-image jest wymagane bez --print-firebird-file")
    issues = collect_issues(
        config,
        expected_image=args.expected_image,
        check_filesystem=args.check_filesystem,
    )
    if issues:
        for issue in issues:
            print(f"[BŁĄD] {issue}", file=sys.stderr)
        return 1
    print("[OK] Stos ctip-test używa przypiętego obrazu bez montowania kodu.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
