"""Logika biznesowa generatora bezpiecznych formularzy."""

from __future__ import annotations

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
from app.services.email_client import send_smtp_message

ACTIVE_STATUSES = {"GENERATED", "DISPATCHED"}


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


def resolve_public_base_url(request_base_url: str | None = None) -> str:
    """Wylicza bazowy adres publiczny używany do budowy linków formularza."""
    configured = (settings.form_public_base_url or "").strip()
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


def build_form_url(token: str, *, request_base_url: str | None = None) -> str:
    """Buduje publiczny adres formularza na podstawie jednorazowego tokenu."""
    base_url = resolve_public_base_url(request_base_url)
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
) -> FormNotificationResult:
    result = FormNotificationResult()
    now = datetime.now(UTC)
    expires_local = _to_utc(form.token_expires_at).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    sms_text = (
        f"CTIP: wygenerowano bezpieczny formularz. Link: {form_url} " f"(wazny do {expires_local})."
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
        message["From"] = formataddr((sender_title, email_delivery.sender_address))
        message["To"] = form.customer_email
        message["Subject"] = "Bezpieczny formularz do uzupełnienia"
        message.set_content(
            "Dzień dobry,\n\n"
            "Przygotowaliśmy bezpieczny formularz wymagany do obsługi zgłoszenia.\n\n"
            f"Link: {form_url}\n"
            f"Ważność linku: {expires_local}\n\n"
            "Jeśli nie oczekiwali Państwo tej wiadomości, prosimy o jej zignorowanie.\n\n"
            "Pozdrawiamy,\nZespół CTIP"
        )
        send_result = await send_smtp_message(
            host=email_delivery.host,
            port=email_delivery.port,
            username=email_delivery.username,
            password=email_delivery.password,
            use_tls=email_delivery.use_tls,
            use_ssl=email_delivery.use_ssl,
            message=message,
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
) -> FormSubmissionNotificationResult:
    result = FormSubmissionNotificationResult()

    company_name = str(payload.get("company_name") or "").strip() or form.customer_name
    company_email = str(payload.get("company_email") or "").strip().lower()
    target_email = company_email or form.customer_email

    email_delivery = await admin_users.resolve_email_delivery_settings(session)
    if email_delivery is None:
        result.warnings.append("Brak konfiguracji SMTP. Klient nie otrzymał potwierdzenia e-mail.")
    elif target_email:
        message = EmailMessage()
        sender_title = email_delivery.sender_name or "CTIP Administrator"
        message["From"] = formataddr((sender_title, email_delivery.sender_address))
        message["To"] = target_email
        message["Subject"] = "Potwierdzenie przyjęcia formularza"
        message.set_content(
            "Dzień dobry,\n\n"
            f"Potwierdzamy poprawne przyjęcie formularza dla firmy: {company_name}.\n"
            "Dane zostały zapisane w systemie CTIP.\n\n"
            "Pozdrawiamy,\nZespół CTIP"
        )
        send_result = await send_smtp_message(
            host=email_delivery.host,
            port=email_delivery.port,
            username=email_delivery.username,
            password=email_delivery.password,
            use_tls=email_delivery.use_tls,
            use_ssl=email_delivery.use_ssl,
            message=message,
        )
        if send_result.success:
            result.client_email_sent = True
        else:
            result.warnings.append(send_result.message)
    else:
        result.warnings.append("Brak adresu e-mail klienta do wysłania potwierdzenia.")

    creator_phone: str | None = None
    if form.created_by is not None:
        creator = await admin_users.fetch_user(session, form.created_by)
        if creator and creator.mobile_phone:
            try:
                creator_phone = admin_users.normalize_mobile_phone(creator.mobile_phone)
            except ValueError:
                creator_phone = None
                result.warnings.append(
                    "Nieprawidłowy numer telefonu użytkownika tworzącego formularz."
                )
    if creator_phone:
        sms_text = f"CTIP: klient {company_name} wypełnił formularz."
        sms = SmsOut(
            dest=creator_phone,
            text=sms_text[:600],
            source="form-generator",
            origin="form_submission_completed",
            status="NEW",
            created_by=form.created_by,
            meta={
                "type": "form_submission_completed",
                "form_request_id": form.id,
                "company_name": company_name,
            },
            created_at=submitted_at,
        )
        session.add(sms)
        result.owner_sms_queued = True
    else:
        result.warnings.append(
            "Brak numeru telefonu osoby tworzącej formularz. Powiadomienie SMS zostało pominięte."
        )

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

    form_url = build_form_url(token, request_base_url=request_base_url)
    notifications = await _dispatch_notifications(session, form=form, form_url=form_url)
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
    try:
        await _dispatch_submission_notifications(
            session,
            form=form,
            payload=payload,
            submitted_at=now,
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
