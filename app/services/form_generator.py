"""Logika biznesowa generatora bezpiecznych formularzy."""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import urlparse

from cryptography.fernet import Fernet
from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import FormRequest, SmsOut
from app.services import admin_users
from app.services.contracts_dashboard import (
    create_client_from_submitted_payload,
    find_client_in_firebird,
    firebird_writes_enabled,
    load_firebird_runtime_config,
    normalize_nip,
    use_firebird_runtime_config,
)
from app.services.contracts_workflow import (
    WORKFLOW_CLIENT_MODE_BASIC_PROFORMA,
    get_or_create_form_workflow_case,
    set_form_workflow_client,
)
from app.services.email_client import send_smtp_message
from app.services.form_handling_config import FormHandlingConfig, load_form_handling_config
from app.services.form_handling_config import render_template as render_form_template

ACTIVE_STATUSES = {"GENERATED", "DISPATCHED"}
CLIENT_COMMUNICATIONS_BLOCKED_MESSAGE = (
    "Wysyłka powiadomień do klientów jest tymczasowo zablokowana "
    "(BLOCK_CLIENT_COMMUNICATIONS=true)."
)


@dataclass(slots=True)
class FormNotificationResult:
    """Wynik dystrybucji linku formularza kanałem SMS i e-mail."""

    sms_queued: bool = False
    email_sent: bool = False
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FormSubmissionNotificationResult:
    """Wynik powiadomień wysyłanych po zatwierdzeniu formularza klienta."""

    client_email_sent: bool = False
    owner_sms_queued: bool = False
    warnings: list[str] = field(default_factory=list)


def _build_cipher() -> Fernet:
    secret = (settings.admin_secret_key or "").strip()
    if not secret:
        raise RuntimeError(
            "Brak ADMIN_SECRET_KEY. Skonfiguruj klucz szyfrujący przed użyciem generatora formularzy."
        )
    try:
        return Fernet(secret.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Nieprawidłowy ADMIN_SECRET_KEY. Wymagany jest poprawny klucz Fernet."
        ) from exc


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _normalize_required(value: str | None, field_name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        raise ValueError(f"Pole '{field_name}' jest wymagane.")
    return normalized


def _normalize_email(value: str | None) -> str:
    return _normalize_required(value, "customer_email").lower()


def _normalize_phone(value: str | None) -> str:
    normalized = admin_users.normalize_mobile_phone(value)
    if not normalized:
        raise ValueError("Nieprawidłowy numer telefonu klienta.")
    return normalized


def _are_client_notifications_blocked() -> bool:
    return bool(settings.block_client_communications)


def resolve_public_base_url(
    request_base_url: str | None = None,
    *,
    configured_base_url: str | None = None,
) -> str:
    """Wylicza bazowy adres publiczny używany do budowy linków formularza."""
    configured = (configured_base_url or settings.form_public_base_url or "").strip()
    if configured:
        return configured.rstrip("/")

    if request_base_url:
        return request_base_url.rstrip("/")

    panel_url = (settings.admin_panel_url or "").strip()
    if panel_url:
        parsed = urlparse(panel_url)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme}://{parsed.netloc}"

    return "http://localhost:8000"


def build_form_url(
    token: str,
    *,
    request_base_url: str | None = None,
    configured_base_url: str | None = None,
) -> str:
    """Buduje publiczny adres formularza na podstawie jednorazowego tokenu."""
    base_url = resolve_public_base_url(
        request_base_url,
        configured_base_url=configured_base_url,
    )
    return f"{base_url}/formularz/{token}"


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _mark_expired_if_needed(form: FormRequest) -> bool:
    if form.status not in ACTIVE_STATUSES:
        return False
    now = datetime.now(UTC)
    if _to_utc(form.token_expires_at) > now:
        return False
    form.status = "EXPIRED"
    form.updated_at = now
    return True


def build_status_message(form: FormRequest) -> str:
    """Zwraca czytelny opis aktualnego etapu obsługi formularza."""
    if form.status == "SUBMITTED":
        return "Formularz został wypełniony przez klienta i zapisany w systemie."
    if form.status == "DISPATCHED":
        return "Formularz został wysłany, ale nie został jeszcze wypełniony."
    if form.status == "GENERATED":
        return "Formularz został wygenerowany i oczekuje na wysłanie do klienta."
    if form.status == "EXPIRED":
        return "Ważność linku wygasła. Klient nie może już wypełnić formularza."
    return "Status formularza jest nieznany."


def _format_ms_status_time(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")


def _truncate_ms_status_details(value: str | None, *, limit: int = 180) -> str:
    details = (value or "").strip().replace("\n", " ")
    if len(details) <= limit:
        return details
    return f"{details[: limit - 3].rstrip()}..."


def build_ms_status_message(
    *,
    state: str,
    event_at: datetime,
    client_id: int | None = None,
    details: str | None = None,
    automatic: bool = True,
) -> str:
    """Buduje czytelny status integracji formularza z Menadzerem Serwisu."""
    prefix = "Automat MS" if automatic else "MS"
    stamp = _format_ms_status_time(event_at)
    short_details = _truncate_ms_status_details(details)
    client_label = str(client_id) if client_id is not None else "nieustalone"

    if state == "LINKED":
        return f"{prefix}: powiazano z klientem ID {client_label} ({stamp})."
    if state == "CREATED":
        return f"{prefix}: dodano klienta ID {client_label} ({stamp})."
    if state == "BLOCKED":
        suffix = f" {short_details}" if short_details else ""
        return f"{prefix}: brak klienta w MS, auto-dodanie zablokowane ({stamp}).{suffix}"
    if state == "LOOKUP_ERROR":
        suffix = f" {short_details}" if short_details else ""
        return f"{prefix}: blad weryfikacji klienta ({stamp}).{suffix}"
    if state == "CREATE_ERROR":
        suffix = f" {short_details}" if short_details else ""
        return f"{prefix}: blad dodawania klienta ({stamp}).{suffix}"
    if state == "SKIPPED":
        suffix = f" {short_details}" if short_details else ""
        return f"{prefix}: pominieto integracje z MS ({stamp}).{suffix}"
    return f"{prefix}: status nieznany ({stamp})."


async def _store_ms_client_link(
    session: AsyncSession,
    *,
    form: FormRequest,
    payload: dict,
    firebird_client_id: int,
    firebird_client_status: str,
    updated_by: int | None,
) -> None:
    workflow_case = await get_or_create_form_workflow_case(
        session,
        form=form,
        user_id=updated_by,
        payload_snapshot=payload,
    )
    await set_form_workflow_client(
        session,
        workflow_case=workflow_case,
        firebird_client_id=firebird_client_id,
        firebird_client_status=firebird_client_status,
        client_mode=WORKFLOW_CLIENT_MODE_BASIC_PROFORMA,
        payload_snapshot=payload,
        updated_by=updated_by,
    )


async def _sync_submitted_form_with_firebird_ms(
    session: AsyncSession,
    *,
    form: FormRequest,
    payload: dict,
    submitted_at: datetime,
) -> None:
    """Automatycznie sprawdza lub tworzy klienta w Menadzerze Serwisu po SUBMITTED."""
    nip = normalize_nip(str(payload.get("company_nip") or ""))
    if not nip:
        form.ms_status = build_ms_status_message(
            state="SKIPPED",
            event_at=submitted_at,
            details="Brak NIP klienta w formularzu.",
        )
        return

    firebird_config = await load_firebird_runtime_config(session)
    with use_firebird_runtime_config(firebird_config):
        existing = await asyncio.to_thread(find_client_in_firebird, nip)
        if existing.error:
            form.ms_status = build_ms_status_message(
                state="LOOKUP_ERROR",
                event_at=submitted_at,
                details=existing.error,
            )
            return

        if existing.found and existing.id_klient:
            await _store_ms_client_link(
                session,
                form=form,
                payload=payload,
                firebird_client_id=existing.id_klient,
                firebird_client_status="linked",
                updated_by=form.created_by,
            )
            form.ms_status = build_ms_status_message(
                state="LINKED",
                event_at=submitted_at,
                client_id=existing.id_klient,
            )
            return

        writes_enabled, reason = firebird_writes_enabled()
        if not writes_enabled:
            form.ms_status = build_ms_status_message(
                state="BLOCKED",
                event_at=submitted_at,
                details=reason,
            )
            return

        try:
            result = await asyncio.to_thread(
                create_client_from_submitted_payload,
                payload,
                source_name=f"CTIP formularz {form.id}",
            )
        except Exception as exc:  # noqa: BLE001
            form.ms_status = build_ms_status_message(
                state="CREATE_ERROR",
                event_at=submitted_at,
                details=str(exc),
            )
            return

    if result.match.id_klient:
        await _store_ms_client_link(
            session,
            form=form,
            payload=payload,
            firebird_client_id=result.match.id_klient,
            firebird_client_status="created" if result.created else "linked",
            updated_by=form.created_by,
        )

    form.ms_status = build_ms_status_message(
        state="CREATED" if result.created else "LINKED",
        event_at=submitted_at,
        client_id=result.match.id_klient,
    )


def decode_submitted_payload(
    form: FormRequest,
) -> tuple[dict[str, object] | None, dict[str, str | None] | None]:
    """Odszyfrowuje payload formularza i zwraca dane wraz z metadanymi wysyłki."""
    if not form.submitted_payload:
        return None, None

    cipher = _build_cipher()
    try:
        decrypted = cipher.decrypt(form.submitted_payload.encode("utf-8")).decode("utf-8")
        envelope = json.loads(decrypted)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Nie udało się odszyfrować danych formularza.") from exc

    payload: dict[str, object] | None = None
    if isinstance(envelope, dict) and isinstance(envelope.get("payload"), dict):
        payload = envelope["payload"]

    meta: dict[str, str | None] = {
        "submitted_at": form.submitted_at.isoformat() if form.submitted_at else None,
        "client_ip": None,
        "user_agent": None,
    }
    if isinstance(envelope, dict) and isinstance(envelope.get("meta"), dict):
        raw_meta = envelope["meta"]
        raw_submitted_at = raw_meta.get("submitted_at")
        if raw_submitted_at:
            meta["submitted_at"] = str(raw_submitted_at)
        meta["client_ip"] = (
            str(raw_meta.get("client_ip")) if raw_meta.get("client_ip") is not None else None
        )
        meta["user_agent"] = (
            str(raw_meta.get("user_agent")) if raw_meta.get("user_agent") is not None else None
        )
    return payload, meta


async def expire_outdated_forms(session: AsyncSession) -> None:
    """Aktualizuje status formularzy, których link wygasł."""
    now = datetime.now(UTC)
    await session.execute(
        update(FormRequest)
        .where(
            FormRequest.status.in_(ACTIVE_STATUSES),
            FormRequest.token_expires_at <= now,
        )
        .values(status="EXPIRED", updated_at=now)
    )


async def list_form_requests(session: AsyncSession, *, limit: int = 200) -> list[FormRequest]:
    """Zwraca listę najnowszych formularzy wraz ze zaktualizowanym statusem."""
    await expire_outdated_forms(session)
    stmt = (
        select(FormRequest)
        .options(selectinload(FormRequest.created_by_user))
        .order_by(desc(FormRequest.created_at))
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def get_form_request_by_id(session: AsyncSession, form_id: int) -> FormRequest | None:
    """Zwraca formularz po identyfikatorze z aktualizacją statusu wygaśnięcia."""
    await expire_outdated_forms(session)
    stmt = (
        select(FormRequest)
        .options(selectinload(FormRequest.created_by_user))
        .where(FormRequest.id == form_id)
    )
    item = (await session.execute(stmt)).scalar_one_or_none()
    return item


async def delete_form_request(session: AsyncSession, form: FormRequest) -> None:
    """Usuwa wpis formularza z bazy."""
    await session.delete(form)


async def _dispatch_notifications(
    session: AsyncSession,
    *,
    form: FormRequest,
    form_url: str,
    config: FormHandlingConfig,
) -> FormNotificationResult:
    result = FormNotificationResult()
    now = datetime.now(UTC)
    expires_local = _to_utc(form.token_expires_at).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    invite_context = {
        "customer_name": form.customer_name,
        "expires_at": expires_local,
        "form_url": form_url,
        "sender_name": "CTIP Administrator",
    }

    if _are_client_notifications_blocked():
        form.sms_status = "SKIPPED"
        form.email_status = "SKIPPED"
        result.warnings.append(CLIENT_COMMUNICATIONS_BLOCKED_MESSAGE)
    else:
        sms_text = render_form_template(
            config.invite_sms_template,
            invite_context,
        )
        sms = SmsOut(
            dest=form.customer_phone,
            text=sms_text[:600],
            source="form-generator",
            origin="form_link_generated",
            status="NEW",
            created_by=form.created_by,
            meta={"type": "form_link_generated", "form_request_id": form.id},
            created_at=now,
        )
        session.add(sms)
        form.sms_status = "QUEUED"
        result.sms_queued = True

        email_delivery = await admin_users.resolve_email_delivery_settings(session)
        if email_delivery is None:
            form.email_status = "SKIPPED"
            result.warnings.append(
                "Brak konfiguracji SMTP. Link został zapisany, ale e-mail nie został wysłany."
            )
        else:
            message = EmailMessage()
            sender_title = email_delivery.sender_name or "CTIP Administrator"
            invite_context["sender_name"] = sender_title
            message["From"] = formataddr((sender_title, email_delivery.sender_address))
            message["To"] = form.customer_email
            message["Subject"] = render_form_template(config.invite_email_subject, invite_context)
            message.set_content(render_form_template(config.invite_email_body, invite_context))
            send_result = await send_smtp_message(
                host=email_delivery.host,
                port=email_delivery.port,
                username=email_delivery.username,
                password=email_delivery.password,
                use_tls=email_delivery.use_tls,
                use_ssl=email_delivery.use_ssl,
                message=message,
                source="form_invitation",
            )
            if send_result.success:
                form.email_status = "SENT"
                result.email_sent = True
            else:
                form.email_status = "ERROR"
                result.warnings.append(send_result.message)

    if result.sms_queued or result.email_sent:
        form.status = "DISPATCHED"
    if result.warnings:
        form.notification_error = "; ".join(result.warnings)[:1000]
    else:
        form.notification_error = None
    form.updated_at = now
    return result


def _merge_notification_warnings(form: FormRequest, warnings: list[str]) -> None:
    if not warnings:
        return
    existing = (form.notification_error or "").strip()
    merged = "; ".join(warnings)
    if existing:
        form.notification_error = f"{existing}; {merged}"[:1000]
    else:
        form.notification_error = merged[:1000]


async def _dispatch_submission_notifications(
    session: AsyncSession,
    *,
    form: FormRequest,
    payload: dict,
    submitted_at: datetime,
    config: FormHandlingConfig,
) -> FormSubmissionNotificationResult:
    result = FormSubmissionNotificationResult()

    company_name = str(payload.get("company_name") or "").strip() or form.customer_name
    company_email = str(payload.get("company_email") or "").strip().lower()
    target_email = company_email or form.customer_email

    if _are_client_notifications_blocked():
        result.warnings.append(CLIENT_COMMUNICATIONS_BLOCKED_MESSAGE)
    else:
        email_delivery = await admin_users.resolve_email_delivery_settings(session)
        if email_delivery is None:
            result.warnings.append(
                "Brak konfiguracji SMTP. Klient nie otrzymał potwierdzenia e-mail."
            )
        elif target_email:
            message = EmailMessage()
            sender_title = email_delivery.sender_name or "CTIP Administrator"
            submission_context = {
                "company_name": company_name,
                "customer_name": form.customer_name,
                "sender_name": sender_title,
            }
            message["From"] = formataddr((sender_title, email_delivery.sender_address))
            message["To"] = target_email
            message["Subject"] = render_form_template(
                config.submission_email_subject,
                submission_context,
            )
            message.set_content(
                render_form_template(
                    config.submission_email_body,
                    submission_context,
                )
            )
            send_result = await send_smtp_message(
                host=email_delivery.host,
                port=email_delivery.port,
                username=email_delivery.username,
                password=email_delivery.password,
                use_tls=email_delivery.use_tls,
                use_ssl=email_delivery.use_ssl,
                message=message,
                source="form_submission_confirmation",
            )
            if send_result.success:
                result.client_email_sent = True
            else:
                result.warnings.append(send_result.message)
        else:
            result.warnings.append("Brak adresu e-mail klienta do wysłania potwierdzenia.")

    salespeople = await admin_users.list_active_salespeople(session)
    sales_recipients: list[tuple[int, str]] = []
    seen_numbers: set[str] = set()
    invalid_salespeople = 0
    missing_salespeople = 0

    for salesperson in salespeople:
        if not salesperson.mobile_phone:
            missing_salespeople += 1
            continue
        try:
            normalized_phone = admin_users.normalize_mobile_phone(salesperson.mobile_phone)
        except ValueError:
            invalid_salespeople += 1
            continue
        if not normalized_phone or normalized_phone in seen_numbers:
            continue
        seen_numbers.add(normalized_phone)
        sales_recipients.append((salesperson.id, normalized_phone))

    if sales_recipients:
        sms_text = render_form_template(
            config.owner_sms_template,
            {
                "company_name": company_name,
                "customer_name": form.customer_name,
            },
        )
        for recipient_user_id, recipient_phone in sales_recipients:
            sms = SmsOut(
                dest=recipient_phone,
                text=sms_text[:600],
                source="form-generator",
                origin="form_submission_completed",
                status="NEW",
                created_by=form.created_by,
                meta={
                    "type": "form_submission_completed",
                    "form_request_id": form.id,
                    "company_name": company_name,
                    "recipient_user_id": recipient_user_id,
                    "recipient_group": "salespeople",
                },
                created_at=submitted_at,
            )
            session.add(sms)
        result.owner_sms_queued = True
    else:
        result.warnings.append(
            "Brak aktywnych handlowców z poprawnym numerem telefonu. Powiadomienie SMS zostało pominięte."
        )

    if missing_salespeople:
        result.warnings.append("Część handlowców nie ma uzupełnionego telefonu komórkowego.")
    if invalid_salespeople:
        result.warnings.append("Część handlowców ma nieprawidłowy numer telefonu.")

    _merge_notification_warnings(form, result.warnings)
    return result


async def create_form_request(
    session: AsyncSession,
    *,
    created_by: int | None,
    customer_name: str,
    customer_email: str,
    customer_phone: str,
    expires_on: date | None = None,
    request_base_url: str | None = None,
) -> tuple[FormRequest, str, FormNotificationResult]:
    """Tworzy wpis formularza, buduje bezpieczny link i uruchamia dystrybucję."""
    _build_cipher()

    now = datetime.now(UTC)
    if expires_on is not None:
        if expires_on < now.date():
            raise ValueError("Data ważności formularza nie może być z przeszłości.")
        expires_at = datetime(
            year=expires_on.year,
            month=expires_on.month,
            day=expires_on.day,
            hour=23,
            minute=59,
            second=59,
            tzinfo=UTC,
        )
    else:
        expires_at = now + timedelta(days=7)

    token = secrets.token_urlsafe(32)
    form = FormRequest(
        created_by=created_by,
        customer_name=_normalize_required(customer_name, "customer_name"),
        customer_email=_normalize_email(customer_email),
        customer_phone=_normalize_phone(customer_phone),
        status="GENERATED",
        token_hash=_hash_token(token),
        token_expires_at=expires_at,
        sms_status=None,
        email_status=None,
        created_at=now,
        updated_at=now,
    )
    session.add(form)
    await session.flush()

    try:
        config = await load_form_handling_config(session)
    except ValueError as exc:
        raise RuntimeError(
            "Nieprawidłowa konfiguracja sekcji obsługi formularza. Sprawdź adres publiczny i szablony wiadomości."
        ) from exc

    form_url = build_form_url(
        token,
        request_base_url=request_base_url,
        configured_base_url=config.public_base_url,
    )
    notifications = await _dispatch_notifications(
        session,
        form=form,
        form_url=form_url,
        config=config,
    )
    return form, form_url, notifications


async def get_form_by_token(session: AsyncSession, token: str) -> FormRequest | None:
    """Wyszukuje formularz po hashu tokenu i aktualizuje status wygasania."""
    token_value = (token or "").strip()
    if not token_value:
        return None

    stmt = select(FormRequest).where(FormRequest.token_hash == _hash_token(token_value))
    form = (await session.execute(stmt)).scalar_one_or_none()
    if form is None:
        return None
    _mark_expired_if_needed(form)
    return form


async def submit_form_payload(
    session: AsyncSession,
    *,
    form: FormRequest,
    payload: dict,
    client_ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Zapisuje zaszyfrowane dane przesłane przez klienta i zamyka token."""
    _mark_expired_if_needed(form)
    if form.status == "EXPIRED":
        raise ValueError("LINK_EXPIRED")
    if form.status == "SUBMITTED":
        raise ValueError("ALREADY_SUBMITTED")

    cipher = _build_cipher()
    now = datetime.now(UTC)
    envelope = {
        "payload": payload,
        "meta": {
            "submitted_at": now.isoformat(),
            "client_ip": (client_ip or "").strip() or None,
            "user_agent": (user_agent or "").strip()[:512] or None,
        },
    }
    encoded = cipher.encrypt(json.dumps(envelope, ensure_ascii=False).encode("utf-8")).decode(
        "utf-8"
    )

    form.submitted_payload = encoded
    form.submitted_at = now
    form.token_used_at = now
    form.status = "SUBMITTED"
    form.updated_at = now
    await _sync_submitted_form_with_firebird_ms(
        session,
        form=form,
        payload=payload,
        submitted_at=now,
    )
    try:
        config = await load_form_handling_config(session)
        await _dispatch_submission_notifications(
            session,
            form=form,
            payload=payload,
            submitted_at=now,
            config=config,
        )
    except Exception as exc:  # noqa: BLE001
        _merge_notification_warnings(
            form,
            [f"Błąd wysyłki powiadomień po zatwierdzeniu formularza: {exc}"],
        )


__all__ = [
    "FormNotificationResult",
    "FormSubmissionNotificationResult",
    "build_status_message",
    "build_form_url",
    "build_ms_status_message",
    "create_form_request",
    "decode_submitted_payload",
    "delete_form_request",
    "expire_outdated_forms",
    "get_form_request_by_id",
    "get_form_by_token",
    "list_form_requests",
    "resolve_public_base_url",
    "submit_form_payload",
]
