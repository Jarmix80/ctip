"""Deterministyczny katalog tożsamości wspólny dla voice, CHAT_KP i CRM."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from cryptography.fernet import InvalidToken
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    BotDisclosureGrant,
    BotIdentityBinding,
    BotIdentityCustomer,
    BotIdentityDevice,
    BotIdentityOverride,
    BotIdentityPhone,
    BotIdentityResolution,
    BotIdentitySmsChallenge,
    BotIdentitySubject,
    BotIdentitySyncRun,
)
from app.schemas.bot_identity import (
    IdentityCandidate,
    IdentityConfirmResponse,
    IdentityDevice,
    IdentityDevicesResponse,
    IdentityDuplicateGroup,
    IdentityNipVerifyResponse,
    IdentityResolveResponse,
    IdentitySmsChallengeResponse,
    IdentitySmsVerifyResponse,
    IdentitySyncStatusResponse,
    PromoteSmsBindingRequest,
)
from app.schemas.crm import (
    ChatCustomerResolveResponse,
    ChatMaskedDevice,
    ChatMaskedDevicesResponse,
    ChatSmsChallengeResponse,
    ChatSmsChallengeVerifyResponse,
)
from app.services.audit import record_audit
from app.services.bot_identity_crypto import (
    BotIdentityCrypto,
    normalize_customer_nip,
    normalize_customer_phone,
)
from app.services.bot_identity_images import safe_device_image_url

FIREBIRD_ACCOUNT_SOURCE = "firebird_mobile_account"
FIREBIRD_CONTACT_SOURCE = "firebird_contact"
FIREBIRD_CUSTOMER_SOURCE = "firebird_customer"
TRUSTED_STATES = {"trusted", "operator_approved"}
MATCHABLE_STATES = TRUSTED_STATES | {"self_declared"}
RESOLUTION_TTL = timedelta(minutes=5)
GRANT_TTL = timedelta(minutes=30)

# Źródło reguł:
# bazams@9e2d36073f943bb9b2926edbf00e55458ddc2cf9
# Wartość LOCK_USER jest wyłącznie predykatem i celowo nie występuje w SELECT.
MOBILE_ACCOUNTS_SQL = """
SELECT
    c.ID_KONTAKT_TABLE
FROM KONTAKT c
WHERE c.NAZWA_S IS NOT NULL
  AND TRIM(c.NAZWA_S) <> ''
  AND c.LOCK_USER IS NOT NULL
  AND TRIM(c.LOCK_USER) <> ''
  AND (c.AKTYWNY IS NULL OR c.AKTYWNY <> 'NIE')
"""

CUSTOMERS_SQL = """
SELECT
    k.ID_KLIENT,
    k.NAZWA AS FIRMA,
    k.NIP,
    k.TELEFON,
    k.AKTYWNY
FROM KLIENT k
WHERE k.AKTYWNY IS NULL OR k.AKTYWNY <> 'NIE'
"""

CONTACTS_SQL = """
SELECT
    c.ID_KONTAKT_TABLE,
    c.ID_KLIENT,
    c.NAZWA AS IMIE_NAZWISKO,
    c.TEL_K,
    c.TEL_S,
    c.TEL_D,
    c.AKTYWNY
FROM KONTAKT c
JOIN KLIENT k ON k.ID_KLIENT = c.ID_KLIENT
WHERE (c.AKTYWNY IS NULL OR c.AKTYWNY <> 'NIE')
  AND (k.AKTYWNY IS NULL OR k.AKTYWNY <> 'NIE')
"""

DEVICES_SQL = """
SELECT
    m.ID_MASZYNA,
    m.ID_KLIENT,
    COALESCE(mi.MARKA, mn.MARKA, m.MARKA) AS PRODUCER,
    COALESCE(mi.MODEL, mn.MODEL, m.MODEL) AS DEVICE_MODEL,
    COALESCE(mi.PLIK, mn.PLIK) AS IMAGE_SOURCE,
    m.SERIAL,
    m.SERIAL2,
    m.EWIDENCJA,
    m.AKTYWNA
FROM MASZYNA m
LEFT JOIN MODEL mi ON mi.ID_MODEL = m.ID_MODEL
LEFT JOIN MODEL mn ON mn.MARKA = m.MARKA AND mn.MODEL = m.MODEL
WHERE COALESCE(m.ID_KLIENT, 0) <> 0
"""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _active(value: Any) -> bool:
    return _clean(value).upper() not in {"N", "NIE", "NO", "0", "FALSE"}


def _extract_phones(value: Any) -> list[str]:
    raw = _clean(value)
    if not raw:
        return []
    normalized = normalize_customer_phone(raw)
    if normalized:
        return [normalized]
    output: list[str] = []
    for part in re.split(r"[,;/|\n]+", raw):
        normalized = normalize_customer_phone(part)
        if normalized and normalized not in output:
            output.append(normalized)
    if output:
        return output
    digits = re.sub(r"\D+", "", raw)
    if len(digits) >= 18 and len(digits) % 9 == 0:
        return [digits[index : index + 9] for index in range(0, len(digits), 9)]
    return []


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sms_code_hash(challenge_ref: str, code: str) -> str:
    secret = settings.bot_identity_secret_key or ""
    return _token_hash(f"{challenge_ref}:{code}:{secret}")


def _decrypted_serial(
    device: BotIdentityDevice,
    crypto: BotIdentityCrypto,
) -> str | None:
    try:
        serial = _clean(crypto.decrypt(device.serial_enc))
    except (InvalidToken, ValueError):
        return None
    return serial if len(serial) >= 4 else None


def authenticate_service_token(authorization: str | None) -> str:
    """Zwraca kanał przypisany do odrębnego tokenu usługi."""

    if not authorization or not authorization.startswith("Bearer "):
        raise PermissionError("Brak tokenu usługi.")
    supplied = authorization[7:].strip()
    configured = {
        "voice": settings.bot_identity_voice_token or "",
        "chat": settings.bot_identity_chat_token or "",
    }
    if configured["voice"] and hmac.compare_digest(configured["voice"], configured["chat"]):
        raise PermissionError("Kanały voice i chat muszą używać różnych tokenów.")
    for channel, token in configured.items():
        if token and hmac.compare_digest(supplied, token):
            return channel
    raise PermissionError("Nieprawidłowy token usługi.")


async def directory_status(session: AsyncSession) -> IdentitySyncStatusResponse:
    last = await session.scalar(
        select(BotIdentitySyncRun).order_by(BotIdentitySyncRun.started_at.desc())
    )
    completed = await session.scalar(
        select(BotIdentitySyncRun)
        .where(BotIdentitySyncRun.status == "completed")
        .order_by(BotIdentitySyncRun.ended_at.desc())
    )
    age: int | None = None
    if completed and completed.ended_at:
        age = max(0, int((_utc_now() - _as_utc(completed.ended_at)).total_seconds()))
    if not settings.bot_identity_enabled:
        status = "disabled"
    elif completed is None:
        status = "missing"
    elif age is not None and age > settings.bot_identity_block_after_seconds:
        status = "stale"
    elif age is not None and age > settings.bot_identity_warn_after_seconds:
        status = "warning"
    else:
        status = "fresh"
    source = completed or last
    return IdentitySyncStatusResponse(
        configured=bool(
            settings.bot_identity_enabled
            and settings.bot_identity_secret_key
            and settings.fb_database
        ),
        status=status,
        last_completed_at=completed.ended_at if completed else None,
        age_seconds=age,
        accounts_seen=source.accounts_seen if source else 0,
        customers_seen=source.customers_seen if source else 0,
        devices_seen=source.devices_seen if source else 0,
        duplicate_phones=source.duplicate_phones if source else 0,
        source_revision=settings.bot_identity_source_revision,
        error_message=last.error_message if last and last.status != "completed" else None,
    )


async def resolve_phone(
    session: AsyncSession,
    *,
    channel: str,
    conversation_ref: str,
    phone: str,
) -> IdentityResolveResponse:
    crypto = BotIdentityCrypto()
    normalized = normalize_customer_phone(phone)
    if not normalized:
        raise ValueError("Nieprawidłowy numer telefonu.")
    phone_hmac = crypto.phone_hmac(normalized)
    status_info = await directory_status(session)
    matches = await _matchable_matches(session, phone_hmac)
    selected: tuple[BotIdentitySubject, BotIdentityBinding, BotIdentityCustomer] | None = None
    status = "not_found"

    if status_info.status in {"missing", "stale", "disabled"}:
        status = "stale"
    else:
        override = await session.scalar(
            select(BotIdentityOverride).where(
                BotIdentityOverride.phone_hmac == phone_hmac,
                BotIdentityOverride.active.is_(True),
            )
        )
        if override is not None:
            selected = next(
                (
                    item
                    for item in matches
                    if item[0].id == override.subject_id and item[1].id == override.binding_id
                ),
                None,
            )
        unique_pairs = {(item[0].id, item[1].customer_id) for item in matches}
        if selected is not None or len(unique_pairs) == 1:
            selected = selected or matches[0]
            status = "exact"
        elif matches:
            status = "ambiguous"

    expires_at = _utc_now() + RESOLUTION_TTL
    resolution = BotIdentityResolution(
        phone_hmac=phone_hmac,
        subject_id=selected[0].id if selected else None,
        binding_id=selected[1].id if selected else None,
        channel=channel,
        conversation_ref=conversation_ref,
        status=status,
        candidate_count=len(matches),
        expires_at=expires_at,
    )
    session.add(resolution)
    await session.flush()
    return IdentityResolveResponse(
        status=status,  # type: ignore[arg-type]
        resolution_ref=resolution.ref,
        candidate_count=len(matches),
        identity_ref=f"identity:{selected[0].id}" if selected else None,
        customer_ref=selected[2].customer_ref if selected else None,
        display_name=selected[0].display_name if selected else None,
        company_name=selected[2].company_name if selected else None,
        binding_source=selected[1].source if selected else None,
        authorization_state=selected[1].trust_state if selected else None,
        requires_nip_verification=bool(selected and selected[1].trust_state == "self_declared"),
        expires_at=expires_at,
    )


async def _matchable_matches(
    session: AsyncSession,
    phone_hmac: str,
) -> list[tuple[BotIdentitySubject, BotIdentityBinding, BotIdentityCustomer]]:
    rows = await session.execute(
        select(BotIdentitySubject, BotIdentityBinding, BotIdentityCustomer)
        .join(BotIdentityPhone, BotIdentityPhone.subject_id == BotIdentitySubject.id)
        .join(BotIdentityBinding, BotIdentityBinding.subject_id == BotIdentitySubject.id)
        .join(BotIdentityCustomer, BotIdentityCustomer.id == BotIdentityBinding.customer_id)
        .where(
            BotIdentityPhone.phone_hmac == phone_hmac,
            BotIdentityPhone.active.is_(True),
            BotIdentitySubject.active.is_(True),
            BotIdentityBinding.active.is_(True),
            BotIdentityBinding.trust_state.in_(MATCHABLE_STATES),
            BotIdentityCustomer.active.is_(True),
        )
    )
    return list(rows.tuples().all())


async def confirm_current(
    session: AsyncSession,
    *,
    resolution_ref: str,
    conversation_ref: str,
    confirmed: bool,
) -> IdentityConfirmResponse:
    resolution = await session.get(BotIdentityResolution, resolution_ref)
    if resolution is None or resolution.conversation_ref != conversation_ref:
        return IdentityConfirmResponse(confirmed=False, status="expired")
    if _as_utc(resolution.expires_at) <= _utc_now():
        return IdentityConfirmResponse(confirmed=False, status="expired")
    if resolution.status != "exact" or not resolution.binding_id:
        return IdentityConfirmResponse(confirmed=False, status="not_exact")
    binding = await session.get(BotIdentityBinding, resolution.binding_id)
    if binding is None or not binding.active or binding.trust_state not in MATCHABLE_STATES:
        return IdentityConfirmResponse(confirmed=False, status="not_exact")
    if binding.trust_state == "self_declared" and resolution.nip_verified_at is None:
        return IdentityConfirmResponse(confirmed=False, status="nip_required")
    if not confirmed:
        await record_audit(
            session,
            user_id=None,
            action="bot_identity_current_rejected",
            payload={
                "resolution_ref": resolution.ref,
                "channel": resolution.channel,
                "binding_id": binding.id,
            },
        )
        return IdentityConfirmResponse(confirmed=False, status="rejected")

    raw_grant = secrets.token_urlsafe(32)
    expires_at = _utc_now() + GRANT_TTL
    disclosure_level = "masked" if binding.trust_state == "operator_approved" else "full_serial"
    grant = BotDisclosureGrant(
        token_hash=_token_hash(raw_grant),
        resolution_ref=resolution.ref,
        customer_id=binding.customer_id,
        channel=resolution.channel,
        conversation_ref=conversation_ref,
        disclosure_level=disclosure_level,
        expires_at=expires_at,
    )
    binding.last_confirmed_at = _utc_now()
    session.add(grant)
    await record_audit(
        session,
        user_id=None,
        action="bot_identity_current_confirmed",
        payload={
            "resolution_ref": resolution.ref,
            "channel": resolution.channel,
            "binding_id": binding.id,
        },
    )
    await session.flush()
    return IdentityConfirmResponse(
        confirmed=True,
        status="confirmed",
        disclosure_grant=raw_grant,
        disclosure_level=disclosure_level,  # type: ignore[arg-type]
        expires_at=expires_at,
    )


async def verify_resolution_nip(
    session: AsyncSession,
    *,
    resolution_ref: str,
    conversation_ref: str,
    nip: str,
) -> IdentityNipVerifyResponse:
    """Weryfikuje NIP bez odszyfrowywania katalogu i blokuje trzecią błędną próbę."""
    resolution = await session.get(BotIdentityResolution, resolution_ref)
    if (
        resolution is None
        or resolution.conversation_ref != conversation_ref
        or _as_utc(resolution.expires_at) <= _utc_now()
    ):
        return IdentityNipVerifyResponse(
            verified=False,
            status="expired",
            attempts_remaining=0,
        )
    if resolution.status != "exact" or not resolution.binding_id:
        return IdentityNipVerifyResponse(
            verified=False,
            status="expired",
            attempts_remaining=0,
        )
    binding = await session.get(BotIdentityBinding, resolution.binding_id)
    if binding is None or not binding.active:
        return IdentityNipVerifyResponse(
            verified=False,
            status="expired",
            attempts_remaining=0,
        )
    if binding.trust_state != "self_declared":
        return IdentityNipVerifyResponse(
            verified=True,
            status="not_required",
            attempts_remaining=max(0, 3 - resolution.nip_failure_count),
        )
    customer = await session.get(BotIdentityCustomer, binding.customer_id)
    normalized = normalize_customer_nip(nip)
    crypto = BotIdentityCrypto()
    is_valid = bool(
        normalized
        and customer
        and customer.nip_hmac
        and hmac.compare_digest(customer.nip_hmac, crypto.nip_hmac(normalized))
    )
    if is_valid:
        resolution.nip_verified_at = _utc_now()
        await record_audit(
            session,
            user_id=None,
            action="bot_identity_nip_verified",
            payload={
                "resolution_ref": resolution.ref,
                "channel": resolution.channel,
                "binding_id": binding.id,
            },
        )
        return IdentityNipVerifyResponse(
            verified=True,
            status="verified",
            attempts_remaining=max(0, 3 - resolution.nip_failure_count),
        )
    resolution.nip_failure_count += 1
    remaining = max(0, 3 - resolution.nip_failure_count)
    if remaining == 0:
        resolution.expires_at = _utc_now()
        await record_audit(
            session,
            user_id=None,
            action="bot_identity_nip_blocked",
            payload={
                "resolution_ref": resolution.ref,
                "channel": resolution.channel,
                "binding_id": binding.id,
            },
        )
        return IdentityNipVerifyResponse(
            verified=False,
            status="blocked",
            attempts_remaining=0,
        )
    return IdentityNipVerifyResponse(
        verified=False,
        status="invalid",
        attempts_remaining=remaining,
    )


async def create_test_sms_challenge(
    session: AsyncSession,
    *,
    channel: str,
    conversation_ref: str,
    phone: str,
    challenge_ref: str | None = None,
) -> IdentitySmsChallengeResponse:
    """Tworzy haszowane wyzwanie i ujawnia kod wyłącznie w izolowanym LAB."""
    if (
        not settings.crm_lab_mode
        or settings.pg_database != "ctip_test"
        or not settings.sms_test_mode
    ):
        raise RuntimeError("Testowe wyzwanie SMS jest dostępne wyłącznie w LAB.")
    normalized = normalize_customer_phone(phone)
    if not normalized:
        raise ValueError("Nieprawidłowy numer telefonu.")
    if challenge_ref:
        existing = await session.get(BotIdentitySmsChallenge, challenge_ref)
        if existing is not None:
            return IdentitySmsChallengeResponse(
                challenge_ref=existing.ref,
                expires_at=existing.expires_at,
            )
    crypto = BotIdentityCrypto()
    configured_code = _clean(settings.bot_identity_test_sms_code)
    if configured_code and not re.fullmatch(r"\d{6}", configured_code):
        raise RuntimeError("BOT_IDENTITY_TEST_SMS_CODE musi zawierać dokładnie 6 cyfr.")
    code = configured_code or f"{secrets.randbelow(1_000_000):06d}"
    challenge = BotIdentitySmsChallenge(
        ref=challenge_ref or str(uuid4()),
        phone_hmac=crypto.phone_hmac(normalized),
        channel=channel,
        conversation_ref=conversation_ref,
        code_hash="pending",
        expires_at=_utc_now() + timedelta(minutes=5),
    )
    session.add(challenge)
    await session.flush()
    challenge.code_hash = _sms_code_hash(challenge.ref, code)
    return IdentitySmsChallengeResponse(
        challenge_ref=challenge.ref,
        expires_at=challenge.expires_at,
        test_code=code,
    )


async def verify_test_sms_challenge(
    session: AsyncSession,
    *,
    challenge_ref: str,
    channel: str,
    conversation_ref: str,
    code: str,
) -> IdentitySmsVerifyResponse:
    """Weryfikuje kod LAB i blokuje wyzwanie po trzech błędnych próbach."""
    challenge = await session.get(BotIdentitySmsChallenge, challenge_ref)
    if (
        challenge is None
        or challenge.channel != channel
        or challenge.conversation_ref != conversation_ref
        or _as_utc(challenge.expires_at) <= _utc_now()
    ):
        return IdentitySmsVerifyResponse(
            verified=False,
            status="expired",
            attempts_remaining=0,
        )
    if challenge.verified_at is not None:
        return IdentitySmsVerifyResponse(
            verified=True,
            status="verified",
            attempts_remaining=max(0, 3 - challenge.attempts),
        )
    if hmac.compare_digest(challenge.code_hash, _sms_code_hash(challenge.ref, code)):
        challenge.verified_at = _utc_now()
        return IdentitySmsVerifyResponse(
            verified=True,
            status="verified",
            attempts_remaining=max(0, 3 - challenge.attempts),
        )
    challenge.attempts += 1
    remaining = max(0, 3 - challenge.attempts)
    if remaining == 0:
        challenge.expires_at = _utc_now()
        return IdentitySmsVerifyResponse(
            verified=False,
            status="blocked",
            attempts_remaining=0,
        )
    return IdentitySmsVerifyResponse(
        verified=False,
        status="invalid",
        attempts_remaining=remaining,
    )


async def resolve_chat_customer(
    session: AsyncSession,
    *,
    nip: str | None,
    name: str | None,
) -> ChatCustomerResolveResponse:
    """Rozpoznaje firmę po dokładnym NIP-ie albo pełnej nazwie bez ujawniania danych."""
    status_info = await directory_status(session)
    if status_info.status in {"missing", "stale", "disabled"}:
        raise RuntimeError("Katalog tożsamości jest niedostępny albo nieświeży.")

    matched_by: str | None = None
    statement = select(BotIdentityCustomer).where(BotIdentityCustomer.active.is_(True))
    normalized_nip = normalize_customer_nip(nip)
    normalized_name = _clean(name)
    if nip:
        if not normalized_nip:
            raise ValueError("Nieprawidłowy NIP.")
        matched_by = "nip"
        statement = statement.where(
            BotIdentityCustomer.nip_hmac == BotIdentityCrypto().nip_hmac(normalized_nip)
        )
    elif normalized_name:
        matched_by = "name"
        statement = statement.where(
            func.lower(func.trim(BotIdentityCustomer.company_name)) == normalized_name.lower()
        )
    else:
        return ChatCustomerResolveResponse(
            status="not_found",
            candidate_count=0,
        )

    matches = list((await session.scalars(statement.limit(3))).all())
    candidate_count = len(matches)
    if candidate_count == 1:
        return ChatCustomerResolveResponse(
            status="exact" if matched_by == "nip" else "unique",
            candidate_count=1,
            customer_ref=matches[0].customer_ref,
            company_name=matches[0].company_name[:300],
            matched_by=matched_by,  # type: ignore[arg-type]
        )
    return ChatCustomerResolveResponse(
        status="ambiguous" if candidate_count > 1 else "not_found",
        candidate_count=candidate_count,
        matched_by=matched_by,  # type: ignore[arg-type]
    )


def _chat_sms_conversation_ref(customer_ref: str | None) -> str:
    return f"chat-kp:{customer_ref or 'unresolved'}"


def _chat_sms_customer_ref(challenge: BotIdentitySmsChallenge) -> str | None:
    prefix = "chat-kp:"
    if not challenge.conversation_ref.startswith(prefix):
        return None
    customer_ref = challenge.conversation_ref.removeprefix(prefix)
    return customer_ref if customer_ref and customer_ref != "unresolved" else None


async def create_chat_sms_challenge(
    session: AsyncSession,
    *,
    phone: str,
    customer_ref: str | None,
    idempotency_key: str | None,
) -> ChatSmsChallengeResponse:
    """Tworzy wyzwanie CHAT_KP bez zwracania kodu w odpowiedzi API."""
    challenge_ref = (
        f"sms-{hashlib.sha256(f'chat:{idempotency_key}'.encode()).hexdigest()[:32]}"
        if idempotency_key
        else None
    )
    result = await create_test_sms_challenge(
        session,
        channel="chat",
        conversation_ref=_chat_sms_conversation_ref(customer_ref),
        phone=phone,
        challenge_ref=challenge_ref,
    )
    challenge = await session.get(BotIdentitySmsChallenge, result.challenge_ref)
    return ChatSmsChallengeResponse(
        challenge_id=result.challenge_ref,
        expires_at=result.expires_at,
        attempts_remaining=max(0, 3 - (challenge.attempts if challenge else 0)),
    )


async def _chat_verified_customer(
    session: AsyncSession,
    challenge: BotIdentitySmsChallenge,
) -> tuple[str | None, str | None]:
    requested_ref = _chat_sms_customer_ref(challenge)
    matching_refs = list(
        (
            await session.scalars(
                select(BotIdentityCustomer.customer_ref)
                .join(
                    BotIdentityBinding,
                    BotIdentityBinding.customer_id == BotIdentityCustomer.id,
                )
                .join(
                    BotIdentityPhone,
                    BotIdentityPhone.subject_id == BotIdentityBinding.subject_id,
                )
                .where(
                    BotIdentityPhone.phone_hmac == challenge.phone_hmac,
                    BotIdentityPhone.active.is_(True),
                    BotIdentityBinding.active.is_(True),
                    BotIdentityCustomer.active.is_(True),
                )
                .distinct()
                .limit(3)
            )
        ).all()
    )
    if len(matching_refs) == 1:
        return matching_refs[0], "sms_verified_known"
    if requested_ref:
        customer_exists = await session.scalar(
            select(BotIdentityCustomer.id).where(
                BotIdentityCustomer.customer_ref == requested_ref,
                BotIdentityCustomer.active.is_(True),
            )
        )
        if customer_exists is not None:
            status = (
                "sms_verified_known"
                if requested_ref in matching_refs
                else "sms_verified_self_declared"
            )
            return requested_ref, status
    return None, "sms_verified_self_declared"


async def verify_chat_sms_challenge(
    session: AsyncSession,
    *,
    challenge_id: str,
    code: str,
) -> ChatSmsChallengeVerifyResponse:
    """Weryfikuje wyzwanie CHAT_KP i zwraca wyłącznie bezpieczne powiązanie firmy."""
    challenge = await session.get(BotIdentitySmsChallenge, challenge_id)
    if challenge is None or challenge.channel != "chat":
        return ChatSmsChallengeVerifyResponse(
            challenge_id=challenge_id,
            status="expired",
            attempts_remaining=0,
        )
    result = await verify_test_sms_challenge(
        session,
        challenge_ref=challenge_id,
        channel="chat",
        conversation_ref=challenge.conversation_ref,
        code=code,
    )
    status_map = {
        "verified": "verified",
        "invalid": "invalid_code",
        "expired": "expired",
        "blocked": "attempts_exceeded",
    }
    customer_ref = None
    verification_status = None
    if result.verified:
        customer_ref, verification_status = await _chat_verified_customer(
            session,
            challenge,
        )
    return ChatSmsChallengeVerifyResponse(
        challenge_id=challenge_id,
        status=status_map[result.status],  # type: ignore[arg-type]
        attempts_remaining=result.attempts_remaining,
        customer_ref=customer_ref,
        verification_status=verification_status,  # type: ignore[arg-type]
    )


async def list_chat_masked_devices(
    session: AsyncSession,
    *,
    customer_ref: str,
    challenge_id: str,
) -> ChatMaskedDevicesResponse:
    """Zwraca aktywne urządzenia z pełnym serialem po ważnej weryfikacji SMS."""
    customer = await _require_verified_chat_customer(
        session,
        customer_ref=customer_ref,
        challenge_id=challenge_id,
    )
    rows = (
        await session.scalars(
            select(BotIdentityDevice)
            .where(
                BotIdentityDevice.customer_id == customer.id,
                BotIdentityDevice.active.is_(True),
                BotIdentityDevice.device_ref.is_not(None),
            )
            .order_by(
                BotIdentityDevice.location,
                BotIdentityDevice.model,
                BotIdentityDevice.device_ref,
            )
        )
    ).all()
    crypto = BotIdentityCrypto()
    devices: list[ChatMaskedDevice] = []
    for row in rows:
        serial = _decrypted_serial(row, crypto)
        if serial is None or row.device_ref is None:
            continue
        devices.append(
            ChatMaskedDevice(
                device_ref=row.device_ref,
                producer=_clean(row.producer) or None,
                model=_clean(row.model) or None,
                serial=serial,
                serial_last4=serial[-4:],
                image_url=safe_device_image_url(row.image_url),
                location=_clean(row.location) or None,
                active=True,
            )
        )
    return ChatMaskedDevicesResponse(
        customer_ref=customer_ref,
        devices=devices,
    )


async def _require_verified_chat_customer(
    session: AsyncSession,
    *,
    customer_ref: str,
    challenge_id: str,
) -> BotIdentityCustomer:
    status_info = await directory_status(session)
    if status_info.status in {"missing", "stale", "disabled"}:
        raise PermissionError("Katalog tożsamości jest niedostępny albo nieświeży.")
    challenge = await session.get(BotIdentitySmsChallenge, challenge_id)
    if (
        challenge is None
        or challenge.channel != "chat"
        or challenge.verified_at is None
        or _as_utc(challenge.expires_at) <= _utc_now()
    ):
        raise PermissionError("Weryfikacja SMS jest nieważna albo wygasła.")
    verified_customer_ref, _ = await _chat_verified_customer(session, challenge)
    if verified_customer_ref != customer_ref:
        raise PermissionError("Weryfikacja SMS nie dotyczy wskazanego klienta.")
    customer = await session.scalar(
        select(BotIdentityCustomer).where(
            BotIdentityCustomer.customer_ref == customer_ref,
            BotIdentityCustomer.active.is_(True),
        )
    )
    if customer is None:
        raise PermissionError("Nie znaleziono aktywnego klienta.")
    return customer


async def validate_chat_device_selection(
    session: AsyncSession,
    *,
    customer_ref: str,
    challenge_id: str,
    device_refs: list[str],
) -> None:
    """Potwierdza, że wszystkie wybrane urządzenia należą do klienta z SMS."""
    if not device_refs:
        return
    customer = await _require_verified_chat_customer(
        session,
        customer_ref=customer_ref,
        challenge_id=challenge_id,
    )
    matched = set(
        (
            await session.scalars(
                select(BotIdentityDevice.device_ref).where(
                    BotIdentityDevice.customer_id == customer.id,
                    BotIdentityDevice.device_ref.in_(device_refs),
                    BotIdentityDevice.active.is_(True),
                )
            )
        ).all()
    )
    if matched != set(device_refs):
        raise ValueError("Co najmniej jedno urządzenie jest nieaktywne albo nie należy do klienta.")


async def disclose_devices(
    session: AsyncSession,
    *,
    customer_ref: str,
    disclosure_grant: str,
    channel: str,
    conversation_ref: str,
) -> IdentityDevicesResponse:
    status_info = await directory_status(session)
    if status_info.status in {"missing", "stale", "disabled"}:
        raise PermissionError("Katalog tożsamości jest niedostępny albo nieświeży.")
    grant = await session.scalar(
        select(BotDisclosureGrant).where(
            BotDisclosureGrant.token_hash == _token_hash(disclosure_grant)
        )
    )
    customer = await session.scalar(
        select(BotIdentityCustomer).where(
            BotIdentityCustomer.customer_ref == customer_ref,
            BotIdentityCustomer.active.is_(True),
        )
    )
    if (
        grant is None
        or customer is None
        or grant.customer_id != customer.id
        or grant.channel != channel
        or grant.conversation_ref != conversation_ref
        or grant.revoked_at is not None
        or _as_utc(grant.expires_at) <= _utc_now()
    ):
        raise PermissionError("Grant ujawnienia jest nieważny.")
    rows = (
        await session.scalars(
            select(BotIdentityDevice)
            .where(
                BotIdentityDevice.customer_id == customer.id,
                BotIdentityDevice.active.is_(True),
                BotIdentityDevice.device_ref.is_not(None),
            )
            .order_by(
                BotIdentityDevice.location,
                BotIdentityDevice.model,
                BotIdentityDevice.external_ref,
            )
        )
    ).all()
    disclosure_level = grant.disclosure_level
    grant.revoked_at = _utc_now()
    crypto = BotIdentityCrypto()
    devices: list[IdentityDevice] = []
    for row in rows:
        if row.device_ref is None:
            continue
        serial = _decrypted_serial(row, crypto) if disclosure_level == "full_serial" else None
        serial_last4 = serial[-4:] if serial else row.serial_last4
        devices.append(
            IdentityDevice(
                device_ref=row.device_ref,
                producer=row.producer,
                model=row.model,
                serial=serial,
                serial_last4=serial_last4,
                image_url=safe_device_image_url(row.image_url),
                location=row.location,
                active=row.active,
            )
        )
    return IdentityDevicesResponse(
        customer_ref=customer.customer_ref,
        disclosure_level=disclosure_level,  # type: ignore[arg-type]
        devices=devices,
    )


async def list_duplicate_groups(session: AsyncSession) -> list[IdentityDuplicateGroup]:
    duplicate_hashes = (
        await session.execute(
            select(BotIdentityPhone.phone_hmac)
            .where(BotIdentityPhone.active.is_(True))
            .group_by(BotIdentityPhone.phone_hmac)
            .having(func.count(func.distinct(BotIdentityPhone.subject_id)) > 1)
        )
    ).scalars()
    output: list[IdentityDuplicateGroup] = []
    for phone_hmac in duplicate_hashes:
        matches = await _all_matches(session, phone_hmac)
        override = await session.scalar(
            select(BotIdentityOverride).where(
                BotIdentityOverride.phone_hmac == phone_hmac,
                BotIdentityOverride.active.is_(True),
            )
        )
        phone = await session.scalar(
            select(BotIdentityPhone).where(BotIdentityPhone.phone_hmac == phone_hmac)
        )
        output.append(
            IdentityDuplicateGroup(
                phone_ref=phone_hmac,
                phone_last4=phone.phone_last4 if phone else "",
                candidate_count=len(matches),
                has_override=override is not None,
                candidates=[
                    IdentityCandidate(
                        subject_id=subject.id,
                        binding_id=binding.id,
                        identity_ref=f"identity:{subject.id}",
                        customer_ref=customer.customer_ref,
                        display_name=subject.display_name,
                        company_name=customer.company_name,
                        source=binding.source,
                        authorization_state=binding.trust_state,
                        active=bool(subject.active and binding.active and customer.active),
                    )
                    for subject, binding, customer in matches
                ],
            )
        )
    return output


async def _all_matches(
    session: AsyncSession,
    phone_hmac: str,
) -> list[tuple[BotIdentitySubject, BotIdentityBinding, BotIdentityCustomer]]:
    rows = await session.execute(
        select(BotIdentitySubject, BotIdentityBinding, BotIdentityCustomer)
        .join(BotIdentityPhone, BotIdentityPhone.subject_id == BotIdentitySubject.id)
        .join(BotIdentityBinding, BotIdentityBinding.subject_id == BotIdentitySubject.id)
        .join(BotIdentityCustomer, BotIdentityCustomer.id == BotIdentityBinding.customer_id)
        .where(BotIdentityPhone.phone_hmac == phone_hmac)
    )
    return list(rows.tuples().all())


async def set_override(
    session: AsyncSession,
    *,
    phone_ref: str,
    subject_id: int,
    binding_id: int,
    reason: str,
    user_id: int,
) -> BotIdentityOverride:
    phone_hmac = phone_ref
    valid = await session.scalar(
        select(BotIdentityBinding)
        .join(BotIdentityPhone, BotIdentityPhone.subject_id == BotIdentityBinding.subject_id)
        .where(
            BotIdentityBinding.id == binding_id,
            BotIdentityBinding.subject_id == subject_id,
            BotIdentityBinding.active.is_(True),
            BotIdentityPhone.phone_hmac == phone_hmac,
            BotIdentityPhone.active.is_(True),
        )
    )
    if valid is None:
        raise ValueError("Wybrane powiązanie nie należy do wskazanego numeru.")
    item = await session.scalar(
        select(BotIdentityOverride).where(BotIdentityOverride.phone_hmac == phone_hmac)
    )
    if item is None:
        item = BotIdentityOverride(
            phone_hmac=phone_hmac,
            subject_id=subject_id,
            binding_id=binding_id,
            reason=reason,
            set_by_user_id=user_id,
        )
        session.add(item)
    else:
        item.subject_id = subject_id
        item.binding_id = binding_id
        item.reason = reason
        item.set_by_user_id = user_id
        item.active = True
        item.updated_at = _utc_now()
    await session.flush()
    return item


async def promote_sms_binding(
    session: AsyncSession,
    payload: PromoteSmsBindingRequest,
) -> BotIdentityBinding:
    """Promuje telefon–firma po jawnej akceptacji zgłoszenia przez operatora."""

    crypto = BotIdentityCrypto()
    normalized = normalize_customer_phone(payload.phone)
    if not normalized:
        raise ValueError("Nieprawidłowy numer telefonu.")
    customer = await session.scalar(
        select(BotIdentityCustomer).where(BotIdentityCustomer.customer_ref == payload.customer_ref)
    )
    if customer is None:
        customer = BotIdentityCustomer(
            customer_ref=payload.customer_ref,
            company_name=payload.company_name,
            active=True,
        )
        session.add(customer)
        await session.flush()
    else:
        customer.company_name = payload.company_name
        customer.active = True
    source = "operator_approved"
    external_ref = f"chat-case:{payload.case_ref}"
    subject = await session.scalar(
        select(BotIdentitySubject).where(
            BotIdentitySubject.source == source,
            BotIdentitySubject.external_ref == external_ref,
        )
    )
    if subject is None:
        subject = BotIdentitySubject(
            source=source,
            external_ref=external_ref,
            display_name=payload.display_name,
            active=True,
        )
        session.add(subject)
        await session.flush()
    phone_hmac = crypto.phone_hmac(normalized)
    phone = await session.scalar(
        select(BotIdentityPhone).where(
            BotIdentityPhone.subject_id == subject.id,
            BotIdentityPhone.phone_hmac == phone_hmac,
        )
    )
    if phone is None:
        session.add(
            BotIdentityPhone(
                subject_id=subject.id,
                phone_enc=crypto.encrypt(normalized),
                phone_hmac=phone_hmac,
                phone_last4=normalized[-4:],
                active=True,
            )
        )
    binding = await session.scalar(
        select(BotIdentityBinding).where(
            BotIdentityBinding.subject_id == subject.id,
            BotIdentityBinding.customer_id == customer.id,
            BotIdentityBinding.source == source,
        )
    )
    if binding is None:
        binding = BotIdentityBinding(
            subject_id=subject.id,
            customer_id=customer.id,
            source=source,
            trust_state="operator_approved",
            source_case_ref=payload.case_ref,
            active=True,
        )
        session.add(binding)
    else:
        binding.trust_state = "operator_approved"
        binding.active = True
    await session.flush()
    return binding


_sync_lock = asyncio.Lock()


async def sync_firebird_directory(session: AsyncSession) -> BotIdentitySyncRun:
    """Synchronizuje pełny snapshot bez zapisu do Firebirda i bez równoległych przebiegów."""
    if _sync_lock.locked():
        raise RuntimeError("Synchronizacja katalogu tożsamości już trwa.")
    async with _sync_lock:
        return await _sync_firebird_directory_locked(session)


async def _sync_firebird_directory_locked(session: AsyncSession) -> BotIdentitySyncRun:
    if not settings.bot_identity_enabled:
        raise RuntimeError("BOT_IDENTITY_ENABLED jest wyłączone.")
    crypto = BotIdentityCrypto()
    run = BotIdentitySyncRun(
        id=str(uuid4()),
        source="firebird_customer_directory",
        source_revision=settings.bot_identity_source_revision,
    )
    session.add(run)
    await session.commit()
    try:
        customer_rows, contact_rows, mobile_rows, device_rows = await asyncio.to_thread(
            _read_firebird_snapshot
        )
        if not customer_rows or not contact_rows or not mobile_rows:
            raise RuntimeError("Snapshot klientów, kontaktów albo kont mobilnych jest pusty.")
        for label, rows in (
            ("klientów", customer_rows),
            ("kontaktów", contact_rows),
            ("kont mobilnych", mobile_rows),
            ("urządzeń", device_rows),
        ):
            if len(rows) > settings.bot_identity_row_limit:
                raise RuntimeError(f"Snapshot {label} przekracza BOT_IDENTITY_ROW_LIMIT.")
        previous = await session.scalar(
            select(BotIdentitySyncRun)
            .where(BotIdentitySyncRun.status == "completed")
            .order_by(BotIdentitySyncRun.ended_at.desc())
        )
        review_required = bool(
            previous
            and previous.accounts_seen
            and len(mobile_rows) < int(previous.accounts_seen * 0.8)
        )
        await _upsert_snapshot(
            session,
            run,
            customer_rows,
            contact_rows,
            mobile_rows,
            device_rows,
            crypto,
        )
        if not review_required:
            await _deactivate_missing_snapshot_rows(session, run.id)
            run.status = "completed"
        else:
            run.status = "review_required"
            run.error_message = (
                "Liczba kont mobilnych spadła o ponad 20%; pominięto masową dezaktywację."
            )
        run.ended_at = _utc_now()
        await session.commit()
    except Exception as exc:
        await session.rollback()
        run = await session.get(BotIdentitySyncRun, run.id)
        if run is None:
            raise
        run.status = "failed"
        run.error_message = str(exc)[:2000]
        run.ended_at = _utc_now()
        await session.commit()
    return run


def _read_firebird_snapshot() -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    import firebirdsql  # type: ignore[import-not-found]

    if settings.fb_allow_writes:
        raise RuntimeError("Synchronizacja katalogu wymaga FB_ALLOW_WRITES=false.")
    connection = firebirdsql.connect(
        host=settings.fb_host,
        port=settings.fb_port,
        database=settings.fb_database,
        user=settings.fb_user,
        password=settings.fb_password,
        charset=settings.fb_charset,
        role=settings.fb_role or None,
        isolation_level=firebirdsql.ISOLATION_LEVEL_READ_COMMITED_RO,
    )
    cursor = connection.cursor()
    try:
        customers = _execute_rows(
            cursor,
            CUSTOMERS_SQL,
            settings.bot_identity_row_limit + 1,
        )
        contacts = _execute_rows(
            cursor,
            CONTACTS_SQL,
            settings.bot_identity_row_limit + 1,
        )
        mobile_accounts = _execute_rows(
            cursor,
            MOBILE_ACCOUNTS_SQL,
            settings.bot_identity_row_limit + 1,
        )
        devices = _execute_rows(
            cursor,
            DEVICES_SQL,
            settings.bot_identity_row_limit + 1,
        )
        try:
            connection.rollback()
        except Exception:
            pass
        return customers, contacts, mobile_accounts, devices
    finally:
        cursor.close()
        connection.close()


def _execute_rows(cursor: Any, sql: str, limit: int) -> list[dict[str, Any]]:
    cursor.execute(f"SELECT * FROM ({sql.strip()}) ROWS {int(limit)}")
    columns = [str(item[0]).upper() for item in cursor.description or []]
    return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]


async def _upsert_snapshot(
    session: AsyncSession,
    run: BotIdentitySyncRun,
    customer_rows: list[dict[str, Any]],
    contact_rows: list[dict[str, Any]],
    mobile_rows: list[dict[str, Any]],
    device_rows: list[dict[str, Any]],
    crypto: BotIdentityCrypto,
) -> None:
    customer_cache: dict[str, BotIdentityCustomer] = {}
    phone_counts: Counter[str] = Counter()
    mobile_ids = {
        _clean(row.get("ID_KONTAKT_TABLE"))
        for row in mobile_rows
        if _clean(row.get("ID_KONTAKT_TABLE"))
    }

    for row in customer_rows:
        customer_ref = _clean(row.get("ID_KLIENT"))
        company_name = _clean(row.get("FIRMA"))
        if not customer_ref or not company_name:
            continue
        customer = await session.scalar(
            select(BotIdentityCustomer).where(BotIdentityCustomer.customer_ref == customer_ref)
        )
        if customer is None:
            customer = BotIdentityCustomer(
                customer_ref=customer_ref,
                company_name=company_name,
            )
            session.add(customer)
            await session.flush()
        customer_cache[customer_ref] = customer
        customer.company_name = company_name
        normalized_nip = normalize_customer_nip(_clean(row.get("NIP")))
        customer.nip_enc = crypto.encrypt(normalized_nip) if normalized_nip else None
        customer.nip_hmac = crypto.nip_hmac(normalized_nip) if normalized_nip else None
        customer.active = True
        customer.last_seen_sync_id = run.id
        customer.last_synced_at = _utc_now()
        customer_phones = set(_extract_phones(row.get("TELEFON")))
        if customer_phones:
            subject = await _upsert_firebird_subject(
                session,
                run=run,
                customer=customer,
                source=FIREBIRD_CUSTOMER_SOURCE,
                external_ref=customer_ref,
                display_name=None,
                trust_state="self_declared",
            )
            await _upsert_subject_phones(
                session,
                run=run,
                subject=subject,
                normalized_phones=customer_phones,
                phone_counts=phone_counts,
                crypto=crypto,
            )

    for row in contact_rows:
        external_ref = _clean(row.get("ID_KONTAKT_TABLE"))
        customer_ref = _clean(row.get("ID_KLIENT"))
        customer = customer_cache.get(customer_ref)
        if not external_ref or customer is None:
            continue
        is_mobile = external_ref in mobile_ids
        source = FIREBIRD_ACCOUNT_SOURCE if is_mobile else FIREBIRD_CONTACT_SOURCE
        trust_state = "trusted" if is_mobile else "self_declared"
        subject = await _upsert_firebird_subject(
            session,
            run=run,
            customer=customer,
            source=source,
            external_ref=external_ref,
            display_name=_clean(row.get("IMIE_NAZWISKO")) or None,
            trust_state=trust_state,
        )
        normalized_phones: set[str] = set()
        for key in ("TEL_K", "TEL_S", "TEL_D"):
            normalized_phones.update(_extract_phones(row.get(key)))
        await _upsert_subject_phones(
            session,
            run=run,
            subject=subject,
            normalized_phones=normalized_phones,
            phone_counts=phone_counts,
            crypto=crypto,
        )

    await session.flush()
    for row in device_rows:
        customer = customer_cache.get(_clean(row.get("ID_KLIENT")))
        external_ref = _clean(row.get("ID_MASZYNA"))
        if customer is None or not external_ref:
            continue
        device = await session.scalar(
            select(BotIdentityDevice).where(
                BotIdentityDevice.customer_id == customer.id,
                BotIdentityDevice.external_ref == external_ref,
            )
        )
        if device is None:
            device = BotIdentityDevice(
                customer_id=customer.id,
                external_ref=external_ref,
            )
            session.add(device)
        if not device.device_ref:
            device.device_ref = str(uuid4())
        serial = _clean(row.get("SERIAL")) or _clean(row.get("SERIAL2"))
        device.producer = _clean(row.get("PRODUCER")) or None
        device.model = _clean(row.get("DEVICE_MODEL")) or None
        device.serial_enc = crypto.encrypt(serial) if serial else None
        device.serial_last4 = serial[-4:] if len(serial) >= 4 else None
        device.image_url = safe_device_image_url(row.get("IMAGE_SOURCE"))
        device.location = _clean(row.get("EWIDENCJA")) or None
        device.active = _active(row.get("AKTYWNA"))
        device.last_seen_sync_id = run.id
        device.last_synced_at = _utc_now()
    await session.flush()
    run.accounts_seen = len(mobile_rows)
    run.customers_seen = len(customer_cache)
    run.devices_seen = sum(
        1 for row in device_rows if _clean(row.get("ID_KLIENT")) in customer_cache
    )
    run.duplicate_phones = sum(1 for count in phone_counts.values() if count > 1)


async def _upsert_firebird_subject(
    session: AsyncSession,
    *,
    run: BotIdentitySyncRun,
    customer: BotIdentityCustomer,
    source: str,
    external_ref: str,
    display_name: str | None,
    trust_state: str,
) -> BotIdentitySubject:
    subject = await session.scalar(
        select(BotIdentitySubject).where(
            BotIdentitySubject.source == source,
            BotIdentitySubject.external_ref == external_ref,
        )
    )
    if subject is None:
        subject = BotIdentitySubject(
            source=source,
            external_ref=external_ref,
        )
        session.add(subject)
        await session.flush()
    subject.display_name = display_name
    subject.active = True
    subject.source_revision = settings.bot_identity_source_revision
    subject.last_seen_sync_id = run.id
    subject.last_synced_at = _utc_now()
    binding = await session.scalar(
        select(BotIdentityBinding).where(
            BotIdentityBinding.subject_id == subject.id,
            BotIdentityBinding.customer_id == customer.id,
            BotIdentityBinding.source == source,
        )
    )
    if binding is None:
        binding = BotIdentityBinding(
            subject_id=subject.id,
            customer_id=customer.id,
            source=source,
            trust_state=trust_state,
        )
        session.add(binding)
    binding.active = True
    binding.trust_state = trust_state
    binding.last_seen_sync_id = run.id
    binding.last_synced_at = _utc_now()
    return subject


async def _upsert_subject_phones(
    session: AsyncSession,
    *,
    run: BotIdentitySyncRun,
    subject: BotIdentitySubject,
    normalized_phones: set[str],
    phone_counts: Counter[str],
    crypto: BotIdentityCrypto,
) -> None:
    for normalized in normalized_phones:
        phone_hmac = crypto.phone_hmac(normalized)
        phone_counts[phone_hmac] += 1
        phone = await session.scalar(
            select(BotIdentityPhone).where(
                BotIdentityPhone.subject_id == subject.id,
                BotIdentityPhone.phone_hmac == phone_hmac,
            )
        )
        if phone is None:
            phone = BotIdentityPhone(
                subject_id=subject.id,
                phone_enc=crypto.encrypt(normalized),
                phone_hmac=phone_hmac,
                phone_last4=normalized[-4:],
            )
            session.add(phone)
        phone.active = True
        phone.last_seen_sync_id = run.id
        phone.last_synced_at = _utc_now()


async def _deactivate_missing_snapshot_rows(session: AsyncSession, run_id: str) -> None:
    await session.execute(
        update(BotIdentitySubject)
        .where(
            BotIdentitySubject.source.in_(
                {
                    FIREBIRD_ACCOUNT_SOURCE,
                    FIREBIRD_CONTACT_SOURCE,
                    FIREBIRD_CUSTOMER_SOURCE,
                }
            ),
            or_(
                BotIdentitySubject.last_seen_sync_id.is_(None),
                BotIdentitySubject.last_seen_sync_id != run_id,
            ),
        )
        .values(active=False)
    )
    for model in (BotIdentityPhone, BotIdentityBinding):
        await session.execute(
            update(model)
            .where(
                model.last_seen_sync_id.is_not(None),
                model.last_seen_sync_id != run_id,
            )
            .values(active=False)
        )
    await session.execute(
        update(BotIdentityCustomer)
        .where(
            BotIdentityCustomer.last_seen_sync_id.is_not(None),
            BotIdentityCustomer.last_seen_sync_id != run_id,
        )
        .values(active=False)
    )
    await session.execute(
        update(BotIdentityDevice)
        .where(
            BotIdentityDevice.last_seen_sync_id.is_not(None),
            BotIdentityDevice.last_seen_sync_id != run_id,
        )
        .values(active=False)
    )
