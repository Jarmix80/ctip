"""Powiadomienia pracowników o nowych sprawach Centrum Obsługi."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import urlencode, urlsplit, urlunsplit

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AdminUser, CrmCase, CrmCaseEvent, SmsOut
from app.services import admin_users
from app.services.email_client import send_smtp_message


@dataclass(slots=True)
class CrmNotificationResult:
    """Podsumowanie przygotowania powiadomień dla jednej sprawy."""

    recipients: int = 0
    sms_simulated: int = 0
    sms_queued: int = 0
    email_simulated: int = 0
    email_sent: int = 0
    skipped: int = 0


def crm_case_url(case_ref: str) -> str:
    """Buduje bezpośredni link do sprawy na podstawie adresu panelu."""

    configured = (settings.admin_panel_url or "http://localhost:8000/admin").strip()
    parsed = urlsplit(configured)
    if not parsed.scheme or not parsed.netloc:
        return f"/crm?{urlencode({'case': case_ref})}"
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            "/crm",
            urlencode({"case": case_ref}),
            "",
        )
    )


def _is_simulation(case: CrmCase) -> bool:
    return bool(
        case.is_lab or settings.crm_lab_mode or settings.pg_database.strip().lower() == "ctip_test"
    )


def _channels(user: AdminUser, queue: str) -> tuple[bool, bool]:
    if queue == "sales":
        return user.crm_sales_sms_enabled, user.crm_sales_email_enabled
    return user.crm_operations_sms_enabled, user.crm_operations_email_enabled


async def _recipients(session: AsyncSession, queue: str) -> list[AdminUser]:
    if queue == "sales":
        channel_filter = or_(
            AdminUser.crm_sales_sms_enabled.is_(True),
            AdminUser.crm_sales_email_enabled.is_(True),
        )
    else:
        channel_filter = or_(
            AdminUser.crm_operations_sms_enabled.is_(True),
            AdminUser.crm_operations_email_enabled.is_(True),
        )
    result = await session.scalars(
        select(AdminUser)
        .where(AdminUser.is_active.is_(True), channel_filter)
        .order_by(AdminUser.last_name, AdminUser.first_name, AdminUser.email)
    )
    return list(result)


def _recipient_name(user: AdminUser) -> str:
    return " ".join(value for value in (user.first_name, user.last_name) if value) or user.email


def _notification_text(case: CrmCase, link: str) -> tuple[str, str, str]:
    queue_label = {
        "sales": "Handel",
        "service_it": "Serwis + IT",
        "contracts": "Umowy i liczniki",
        "other": "Inne",
    }.get(case.queue, case.queue)
    subject = f"[CTIP CRM] Nowa sprawa {case.ref}: {case.subject}"
    body = (
        f"Do kolejki {queue_label} wpłynęła nowa sprawa.\n\n"
        f"Numer: {case.ref}\n"
        f"Firma: {case.company_name}\n"
        f"Kontakt: {case.contact_name}\n"
        f"Temat: {case.subject}\n"
        f"Sprawa: {link}\n"
    )
    sms = (
        f"CTIP CRM: nowa sprawa {case.ref}, {queue_label}. "
        f"{case.company_name}: {case.subject}. {link}"
    )
    return subject, body, sms[:600]


async def dispatch_new_case_notifications(
    session: AsyncSession,
    case: CrmCase,
) -> CrmNotificationResult:
    """Przygotowuje SMS i e-mail zgodnie z ustawieniami aktywnych użytkowników."""

    result = CrmNotificationResult()
    recipients = await _recipients(session, case.queue)
    result.recipients = len(recipients)
    link = crm_case_url(case.ref)
    subject, body, sms_text = _notification_text(case, link)
    simulation = _is_simulation(case)
    delivery = None if simulation else await admin_users.resolve_email_delivery_settings(session)
    event_recipients: list[dict[str, object]] = []

    for recipient in recipients:
        sms_enabled, email_enabled = _channels(recipient, case.queue)
        recipient_status: dict[str, object] = {
            "user_id": recipient.id,
            "name": _recipient_name(recipient),
            "channels": {},
        }
        channel_status = recipient_status["channels"]
        assert isinstance(channel_status, dict)

        if sms_enabled:
            if not recipient.mobile_phone:
                channel_status["sms"] = "brak_numeru"
                result.skipped += 1
            elif simulation:
                channel_status["sms"] = "symulacja"
                result.sms_simulated += 1
            else:
                session.add(
                    SmsOut(
                        dest=recipient.mobile_phone,
                        text=sms_text,
                        source="crm",
                        origin="crm_case_notification",
                        status="NEW",
                        created_by=None,
                        meta={
                            "type": "crm_case_notification",
                            "case_ref": case.ref,
                            "user_id": recipient.id,
                            "case_url": link,
                        },
                        created_at=datetime.now(UTC),
                    )
                )
                channel_status["sms"] = "kolejka"
                result.sms_queued += 1

        if email_enabled:
            if not recipient.email:
                channel_status["email"] = "brak_adresu"
                result.skipped += 1
            elif simulation:
                channel_status["email"] = "symulacja"
                result.email_simulated += 1
            elif delivery is None:
                channel_status["email"] = "brak_smtp"
                result.skipped += 1
            else:
                message = EmailMessage()
                message["From"] = formataddr(
                    (delivery.sender_name or "CTIP CRM", delivery.sender_address)
                )
                message["To"] = recipient.email
                message["Subject"] = subject
                message.set_content(body)
                send_result = await send_smtp_message(
                    host=delivery.host,
                    port=delivery.port,
                    username=delivery.username,
                    password=delivery.password,
                    use_tls=delivery.use_tls,
                    use_ssl=delivery.use_ssl,
                    message=message,
                )
                if send_result.success:
                    channel_status["email"] = "wysłano"
                    result.email_sent += 1
                else:
                    channel_status["email"] = "błąd"
                    recipient_status["email_error"] = send_result.message
                    result.skipped += 1

        event_recipients.append(recipient_status)

    if recipients:
        description = (
            "Tryb LAB: przygotowano podgląd powiadomień bez wysyłki."
            if simulation
            else "Przetworzono powiadomienia dla wskazanych użytkowników."
        )
        title = "Powiadomienia o nowej sprawie"
    else:
        description = "Brak aktywnych użytkowników z włączonym kanałem dla tej kolejki."
        title = "Brak odbiorców powiadomień"

    case.events.append(
        CrmCaseEvent(
            event_type="notification",
            title=title,
            description=description,
            payload={
                "simulation": simulation,
                "case_url": link,
                "recipients": event_recipients,
            },
        )
    )
    await session.flush()
    return result


__all__ = [
    "CrmNotificationResult",
    "crm_case_url",
    "dispatch_new_case_notifications",
]
