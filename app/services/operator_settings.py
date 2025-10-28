"""Logika ustawień panelu operatora (profil, hasła, szablony SMS)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AdminSession, AdminUser, Contact, SmsTemplate
from app.services import admin_users
from app.services.audit import record_audit
from app.services.security import hash_password, verify_password


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


async def update_profile(
    session: AsyncSession,
    user: AdminUser,
    *,
    email: str,
    first_name: str | None,
    last_name: str | None,
    internal_ext: str | None,
    mobile_phone: str | None,
) -> AdminUser:
    """Aktualizuje dane operatora, pilnując unikalności e-mail."""
    email_normalized = email.strip().lower()
    if email_normalized != user.email:
        await admin_users.ensure_unique_email(session, email_normalized, exclude_user_id=user.id)

    normalized_phone = admin_users.normalize_mobile_phone(mobile_phone)

    user.email = email_normalized
    user.first_name = _normalize(first_name)
    user.last_name = _normalize(last_name)
    user.internal_ext = _normalize(internal_ext)
    user.mobile_phone = normalized_phone
    user.updated_at = datetime.now(UTC)
    await session.flush()
    return user


async def change_password(
    session: AsyncSession,
    user: AdminUser,
    *,
    current_password: str,
    new_password: str,
    exclude_token: str | None = None,
) -> None:
    """Zmienia hasło operatora po weryfikacji starego hasła i unieważnia pozostałe sesje."""
    if not verify_password(current_password, user.password_hash):
        raise ValueError("Nieprawidłowe aktualne hasło.")
    if verify_password(new_password, user.password_hash):
        raise ValueError("Nowe hasło nie może być identyczne z dotychczasowym.")

    user.password_hash = hash_password(new_password)
    user.updated_at = datetime.now(UTC)

    stmt = (
        update(AdminSession)
        .where(AdminSession.user_id == user.id, AdminSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    if exclude_token:
        stmt = stmt.where(AdminSession.token != exclude_token)
    await session.execute(stmt)
    await session.flush()


async def list_templates(session: AsyncSession, user_id: int) -> Sequence[SmsTemplate]:
    """Zwraca listę szablonów dostępnych dla operatora."""
    stmt: Select[tuple[SmsTemplate]] = (
        select(SmsTemplate)
        .where(
            (SmsTemplate.scope == "global")
            | ((SmsTemplate.scope == "user") & (SmsTemplate.owner_id == user_id))
        )
        .order_by(SmsTemplate.scope.desc(), SmsTemplate.name.asc())
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def fetch_template(session: AsyncSession, template_id: int) -> SmsTemplate | None:
    """Zwraca pojedynczy szablon SMS."""
    stmt: Select[tuple[SmsTemplate]] = select(SmsTemplate).where(SmsTemplate.id == template_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_template(
    session: AsyncSession,
    *,
    user_id: int,
    name: str,
    body: str,
    is_active: bool,
) -> SmsTemplate:
    """Tworzy szablon przypisany do operatora."""
    template = SmsTemplate(
        name=name,
        body=body,
        scope="user",
        owner_id=user_id,
        is_active=is_active,
        created_by=user_id,
        updated_by=user_id,
    )
    session.add(template)
    await session.flush()
    return template


async def update_template(
    session: AsyncSession,
    template: SmsTemplate,
    *,
    user_id: int,
    name: str,
    body: str,
    is_active: bool,
) -> SmsTemplate:
    """Aktualizuje szablon operatora."""
    if template.scope != "user" or template.owner_id != user_id:
        raise ValueError("Brak uprawnień do modyfikacji szablonu.")

    template.name = name
    template.body = body
    template.is_active = is_active
    template.updated_by = user_id
    template.updated_at = datetime.now(UTC)
    await session.flush()
    return template


async def delete_template(session: AsyncSession, template: SmsTemplate, *, user_id: int) -> None:
    """Usuwa szablon operatora."""
    if template.scope != "user" or template.owner_id != user_id:
        raise ValueError("Brak uprawnień do usunięcia szablonu.")
    await session.delete(template)
    await session.flush()


async def record_contact_audit(
    session: AsyncSession,
    *,
    admin_session: AdminSession,
    admin_user: AdminUser,
    action: str,
    contact: Contact,
) -> None:
    """Zapisuje wpis audytu dla operacji na kontaktach wykonanych w panelu operatora."""
    await record_audit(
        session,
        user_id=admin_user.id,
        action=action,
        client_ip=admin_session.client_ip,
        payload={"contact_id": contact.id, "number": contact.number, "source": contact.source},
    )
