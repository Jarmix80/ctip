"""Sprawdza kontrakt API i świeżą synchronizację Bot Identity w `ctip_test`."""

from __future__ import annotations

import argparse
import asyncio
import json
import urllib.request
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal, engine
from app.models import BotIdentitySyncRun

EXPECTED_CATEGORIES = (
    "sales",
    "service",
    "accounting",
    "other",
    "contracts_settlements",
)
EXPECTED_FLAGS = {
    "customer_resolution",
    "sms_verification",
    "masked_devices",
    "idempotent_sms",
    "idempotent_cases",
}


def validate_environment() -> None:
    """Blokuje kontrolę przy niepełnej albo niebezpiecznej konfiguracji."""

    problems: list[str] = []
    if settings.ctip_runtime_profile != "test" or settings.pg_database != "ctip_test":
        problems.append("wymagany jest profil test i baza ctip_test")
    if settings.fb_allow_writes:
        problems.append("FB_ALLOW_WRITES musi mieć wartość false")
    if not settings.bot_identity_secret_key:
        problems.append("brak BOT_IDENTITY_SECRET_KEY")
    if not settings.bot_identity_chat_token or not settings.bot_identity_voice_token:
        problems.append("brak odrębnych tokenów chat/voice")
    if settings.bot_identity_chat_token == settings.bot_identity_voice_token:
        problems.append("tokeny chat i voice nie mogą być identyczne")
    if settings.bot_identity_test_sms_code != "123456" or not settings.crm_lab_mode:
        problems.append("kod 123456 jest dozwolony wyłącznie w aktywnym LAB")
    if problems:
        raise RuntimeError("; ".join(problems))


def is_ctip_v1_capabilities(payload: dict[str, object]) -> bool:
    """Rozpoznaje pełny kontrakt `ctip-v1` używany przez adapter CHAT_KP."""

    categories = payload.get("categories")
    return (
        payload.get("service") == "ctip"
        and payload.get("contract_version") == "1.0"
        and isinstance(categories, list)
        and categories == list(EXPECTED_CATEGORIES)
        and all(payload.get(flag) is True for flag in EXPECTED_FLAGS)
    )


def check_capabilities() -> None:
    """Sprawdza uwierzytelniony kontrakt `ctip-v1` bez ujawniania tokenu."""

    request = urllib.request.Request(
        "http://127.0.0.1:8082/v1/capabilities",
        headers={"Authorization": f"Bearer {settings.bot_identity_chat_token}"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.load(response)
        if response.status != 200 or not is_ctip_v1_capabilities(payload):
            raise RuntimeError("API Bot Identity nie potwierdziło kontraktu ctip-v1.")


async def latest_sync() -> BotIdentitySyncRun | None:
    """Pobiera najnowszy przebieg synchronizacji katalogu."""

    async with AsyncSessionLocal() as session:
        return await session.scalar(
            select(BotIdentitySyncRun).order_by(BotIdentitySyncRun.started_at.desc()).limit(1)
        )


def is_recent_completed(run: BotIdentitySyncRun | None, *, earliest: datetime) -> bool:
    """Potwierdza świeże i poprawnie zakończone wykonanie workera."""

    if run is None or run.status != "completed" or run.ended_at is None:
        return False
    ended_at = run.ended_at
    if ended_at.tzinfo is None:
        ended_at = ended_at.replace(tzinfo=UTC)
    return ended_at >= earliest


async def wait_for_recent_sync(max_wait_seconds: int) -> None:
    """Czeka na synchronizację rozpoczętą podczas bieżącego cutoveru."""

    earliest = datetime.now(UTC) - timedelta(minutes=5)
    for _ in range(max_wait_seconds // 2 + 1):
        if is_recent_completed(await latest_sync(), earliest=earliest):
            return
        await asyncio.sleep(2)
    raise RuntimeError("Brak świeżej, poprawnie zakończonej synchronizacji Bot Identity.")


def parse_args() -> argparse.Namespace:
    """Buduje parametry limitu oczekiwania."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-wait-seconds", type=int, default=120)
    return parser.parse_args()


async def run(max_wait_seconds: int) -> None:
    """Wykonuje kompletną kontrolę odbiorczą Bot Identity."""

    validate_environment()
    try:
        check_capabilities()
        await wait_for_recent_sync(max_wait_seconds)
    finally:
        await engine.dispose()
    print("[OK] Bot Identity: kontrakt ctip-v1 i świeża synchronizacja Firebird read-only.")


def main() -> int:
    """Zwraca niezerowy kod przy niespełnieniu kontraktu lub synchronizacji."""

    arguments = parse_args()
    try:
        asyncio.run(run(arguments.max_wait_seconds))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[BŁĄD] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
