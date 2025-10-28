"""Operacje pomocnicze związane z konfiguracją e-mail."""

from __future__ import annotations

from email.message import EmailMessage

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.api.routes.admin_config import load_email_config, settings_store
from app.core.config import settings
from app.schemas.admin import EmailTestRequest, EmailTestResponse
from app.services.audit import record_audit
from app.services.email_client import send_smtp_message, test_smtp_connection

router = APIRouter(prefix="/admin/email", tags=["admin-email"])


@router.post(
    "/test", response_model=EmailTestResponse, summary="Sprawdź połączenie / wyślij test SMTP"
)
async def test_email_configuration(
    payload: EmailTestRequest | None = None,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> EmailTestResponse:
    """Weryfikuje konfigurację SMTP; opcjonalnie wysyła wiadomość testową."""
    admin_session, admin_user = admin_context
    config = await load_email_config(session)

    stored = await settings_store.get_namespace(session, "email")
    password = stored.get("password") if stored.get("password") else settings.email_password

    host = payload.host if payload and payload.host is not None else config.host
    port = payload.port if payload and payload.port is not None else config.port
    username = payload.username if payload and payload.username is not None else config.username
    sender_name = (
        payload.sender_name if payload and payload.sender_name is not None else config.sender_name
    )
    sender_address = (
        payload.sender_address
        if payload and payload.sender_address is not None
        else config.sender_address
    )
    use_tls = payload.use_tls if payload and payload.use_tls is not None else config.use_tls
    use_ssl = payload.use_ssl if payload and payload.use_ssl is not None else config.use_ssl
    password_override = payload.password if payload and payload.password is not None else password

    fallback_port = port or settings.email_port or 587

    result = test_smtp_connection(
        host=(host or "").strip(),
        port=fallback_port,
        username=(username or "").strip() or None,
        password=password_override,
        use_tls=bool(use_tls),
        use_ssl=bool(use_ssl),
    )

    audit_payload = {
        "success": result.success,
        "message": result.message,
        "host": host,
        "port": fallback_port,
        "username": username,
        "use_tls": use_tls,
        "use_ssl": use_ssl,
    }

    message_text: str | None = result.message

    if payload and payload.test_recipient:
        if not result.success:
            raise HTTPException(
                status_code=400, detail=result.message or "Test połączenia SMTP nieudany."
            )

        recipient = payload.test_recipient.strip()
        if not recipient:
            raise HTTPException(status_code=400, detail="Adres testowy jest wymagany.")

        if not sender_address:
            raise HTTPException(status_code=400, detail="Brak adresu nadawcy w konfiguracji SMTP.")

        email_message = EmailMessage()
        email_message["From"] = f"{sender_name or 'CTIP Administrator'} <{sender_address}>"
        email_message["To"] = recipient
        email_message["Subject"] = payload.test_subject or "Test konfiguracji SMTP"
        email_message.set_content(
            payload.test_body or "To jest wiadomość testowa wysłana z panelu CTIP."
        )

        send_result = await send_smtp_message(
            host=(host or "").strip(),
            port=fallback_port,
            username=(username or "").strip() or None,
            password=password_override,
            use_tls=bool(use_tls),
            use_ssl=bool(use_ssl),
            message=email_message,
        )

        audit_payload["test_email"] = {
            "recipient": recipient,
            "success": send_result.success,
            "message": send_result.message,
        }

        if send_result.success:
            message_text = send_result.message or "Wiadomość testowa została wysłana."
        else:
            raise HTTPException(
                status_code=400, detail=send_result.message or "Wysyłka testowa zakończona błędem."
            )

    await record_audit(
        session,
        user_id=admin_user.id,
        action="config_email_test",
        client_ip=admin_session.client_ip,
        payload=audit_payload,
    )
    await session.commit()

    return EmailTestResponse(success=result.success, message=message_text or "Test zakończony.")
