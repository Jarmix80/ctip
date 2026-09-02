"""Funkcje pomocnicze dla modułu użytkowników administracyjnych."""

from __future__ import annotations

import logging
import re
import secrets
import string
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formataddr

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import AdminSession, AdminUser, SmsOut
from app.services.email_client import send_smtp_message
from app.services.security import hash_password
from app.services.settings_store import build_store

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EmailDeliverySettings:
    host: str
    port: int
    username: str | None
    password: str | None
    sender_name: str | None
    sender_address: str
    use_tls: bool
    use_ssl: bool


_settings_store = build_store(settings.admin_secret_key)


@dataclass(slots=True)
class UserRow:
    user: AdminUser
    sessions_active: int
    last_login_at: datetime | None


def generate_password(length: int = 12) -> str:
    """Generuje bezpieczne hasło (litery/digits/znaki specjalne)."""
    if length < 8:
        length = 8
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    secure_random = secrets.SystemRandom()
    # zapewnij minimalne klasy znaków
    password = [
        secure_random.choice(string.ascii_lowercase),
        secure_random.choice(string.ascii_uppercase),
        secure_random.choice(string.digits),
        secure_random.choice("!@#$%^&*"),
    ]
    password.extend(secure_random.choice(alphabet) for _ in range(length - len(password)))
    secure_random.shuffle(password)
    return "".join(password)


def normalize_mobile_phone(value: str | None) -> str | None:
    """Normalizuje numer telefonu (cyfry + opcjonalny prefiks +)."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError("Nieprawidłowy numer telefonu.")
    phone = digits
    if raw.startswith("+"):
        phone = f"+{digits}"
    if len(phone) < 6 or len(phone) > 32:
        raise ValueError("Numer telefonu ma nieprawidłową długość.")
    return phone


async def fetch_user(session: AsyncSession, user_id: int) -> AdminUser | None:
    stmt = select(AdminUser).where(AdminUser.id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def ensure_unique_email(
    session: AsyncSession, email: str, exclude_user_id: int | None = None
) -> None:
    stmt = select(AdminUser).where(AdminUser.email == email)
    if exclude_user_id is not None:
        stmt = stmt.where(AdminUser.id != exclude_user_id)
    exists = (await session.execute(stmt)).first()
    if exists:
        raise ValueError("Adres e-mail jest już zajęty.")


async def list_users(session: AsyncSession) -> list[UserRow]:
    stmt: Select[tuple[AdminUser, int, datetime | None]] = (
        select(
            AdminUser,
            func.count(AdminSession.id)
            .filter(AdminSession.revoked_at.is_(None))
            .label("sessions_active"),
            func.max(AdminSession.created_at).label("last_login_at"),
        )
        .outerjoin(AdminSession, AdminSession.user_id == AdminUser.id)
        .group_by(AdminUser.id)
        .order_by(AdminUser.created_at.desc())
    )
    rows = await session.execute(stmt)
    results: list[UserRow] = []
    for user, sessions_active, last_login in rows:
        results.append(
            UserRow(
                user=user,
                sessions_active=sessions_active or 0,
                last_login_at=last_login,
            )
        )
    return results


async def list_sessions(session: AsyncSession, user_id: int) -> Iterable[AdminSession]:
    stmt = (
        select(AdminSession)
        .where(AdminSession.user_id == user_id)
        .order_by(AdminSession.created_at.desc())
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def create_user(
    session: AsyncSession,
    *,
    email: str,
    first_name: str | None,
    last_name: str | None,
    internal_ext: str | None,
    role: str,
    is_salesperson: bool = False,
    can_withdraw_device_pz: bool = False,
    crm_sales_sms_enabled: bool = False,
    crm_sales_email_enabled: bool = False,
    crm_operations_sms_enabled: bool = False,
    crm_operations_email_enabled: bool = False,
    password: str | None = None,
    mobile_phone: str | None = None,
    firebird_app_user_id: int | None = None,
    firebird_app_user_login: str | None = None,
) -> tuple[AdminUser, str]:
    email_normalized = email.strip().lower()
    await ensure_unique_email(session, email_normalized)

    generated_password = password or generate_password()
    hashed = hash_password(generated_password)
    normalized_phone = normalize_mobile_phone(mobile_phone)

    user = AdminUser(
        email=email_normalized,
        first_name=(first_name or "").strip() or None,
        last_name=(last_name or "").strip() or None,
        internal_ext=(internal_ext or "").strip() or None,
        role=role,
        password_hash=hashed,
        is_active=True,
        is_salesperson=bool(is_salesperson),
        can_withdraw_device_pz=bool(can_withdraw_device_pz),
        crm_sales_sms_enabled=bool(crm_sales_sms_enabled),
        crm_sales_email_enabled=bool(crm_sales_email_enabled),
        crm_operations_sms_enabled=bool(crm_operations_sms_enabled),
        crm_operations_email_enabled=bool(crm_operations_email_enabled),
        mobile_phone=normalized_phone,
        firebird_app_user_id=firebird_app_user_id,
        firebird_app_user_login=(firebird_app_user_login or "").strip() or None,
    )
    session.add(user)
    await session.flush()
    return user, generated_password


async def update_user(
    session: AsyncSession,
    user: AdminUser,
    *,
    email: str,
    first_name: str | None,
    last_name: str | None,
    internal_ext: str | None,
    role: str,
    is_salesperson: bool,
    can_withdraw_device_pz: bool,
    crm_sales_sms_enabled: bool,
    crm_sales_email_enabled: bool,
    crm_operations_sms_enabled: bool,
    crm_operations_email_enabled: bool,
    mobile_phone: str | None,
    firebird_app_user_id: int | None,
    firebird_app_user_login: str | None,
) -> AdminUser:
    email_normalized = email.strip().lower()
    if email_normalized != user.email:
        await ensure_unique_email(session, email_normalized, exclude_user_id=user.id)

    normalized_phone = normalize_mobile_phone(mobile_phone)

    user.email = email_normalized
    user.first_name = (first_name or "").strip() or None
    user.last_name = (last_name or "").strip() or None
    user.internal_ext = (internal_ext or "").strip() or None
    user.role = role
    user.is_salesperson = bool(is_salesperson)
    user.can_withdraw_device_pz = bool(can_withdraw_device_pz)
    user.crm_sales_sms_enabled = bool(crm_sales_sms_enabled)
    user.crm_sales_email_enabled = bool(crm_sales_email_enabled)
    user.crm_operations_sms_enabled = bool(crm_operations_sms_enabled)
    user.crm_operations_email_enabled = bool(crm_operations_email_enabled)
    user.mobile_phone = normalized_phone
    user.firebird_app_user_id = firebird_app_user_id
    user.firebird_app_user_login = (firebird_app_user_login or "").strip() or None
    user.updated_at = datetime.now(UTC)
    return user


async def reset_password(
    session: AsyncSession, user: AdminUser, *, password: str | None = None
) -> str:
    new_password = password or generate_password()
    user.password_hash = hash_password(new_password)
    user.updated_at = datetime.now(UTC)
    await revoke_sessions(session, user.id)
    return new_password


async def set_user_active(session: AsyncSession, user: AdminUser, *, is_active: bool) -> AdminUser:
    user.is_active = is_active
    user.updated_at = datetime.now(UTC)
    if not is_active:
        await revoke_sessions(session, user.id)
    return user


async def revoke_sessions(session: AsyncSession, user_id: int) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(AdminSession)
        .where(AdminSession.user_id == user_id, AdminSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )


def _coerce_bool(value: str | bool | None, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in {"1", "true", "t", "yes", "on"}
    return default


async def resolve_email_delivery_settings(session: AsyncSession) -> EmailDeliverySettings | None:
    """Pobiera SMTP, ignorując ustawienia bazodanowe w profilu testowym."""
    stored = (
        {} if settings.is_test_runtime else await _settings_store.get_namespace(session, "email")
    )
    host = (stored.get("host") or settings.email_host or "").strip()
    sender_address = (settings.email_sender_address or "").strip()
    if not host or not sender_address:
        return None

    port_value = stored.get("port") or settings.email_port
    try:
        port = int(port_value)
    except (TypeError, ValueError):
        port = settings.email_port or 587

    username = (stored.get("username") or settings.email_username or "").strip() or None
    password = stored.get("password") or settings.email_password
    sender_name = (stored.get("sender_name") or settings.email_sender_name or "").strip() or None
    use_tls = _coerce_bool(stored.get("use_tls"), settings.email_use_tls)
    use_ssl = _coerce_bool(stored.get("use_ssl"), settings.email_use_ssl)
    if use_tls and use_ssl:
        # STARTTLS ma pierwszeństwo – unikamy konfliktu konfiguracji
        use_ssl = False

    return EmailDeliverySettings(
        host=host,
        port=port,
        username=username,
        password=password,
        sender_name=sender_name,
        sender_address=sender_address,
        use_tls=use_tls,
        use_ssl=use_ssl,
    )


async def send_credentials_email(
    delivery: EmailDeliverySettings | None,
    user: AdminUser,
    password: str,
    login_url: str,
    *,
    reason: str = "create",
) -> None:
    """Wysyła wiadomość z danymi logowania po utworzeniu konta lub resecie hasła."""
    if delivery is None:
        logger.info("Pomijam wysyłkę e-maila z danymi logowania – brak konfiguracji SMTP.")
        return
    if not user.email:
        logger.warning("Pomijam wysyłkę e-maila – użytkownik %s nie ma adresu.", user.id)
        return

    reset_mode = reason == "password_reset"
    message = EmailMessage()
    message["Subject"] = (
        "Reset hasła do panelu CTIP" if reset_mode else "Dane logowania do panelu CTIP"
    )
    sender_title = delivery.sender_name or "CTIP Administrator"
    message["From"] = formataddr((sender_title, delivery.sender_address))
    message["To"] = user.email
    greeting = user.first_name or user.last_name or "Administratorze"
    intro = (
        "Hasło Twojego konta w panelu administracyjnym CTIP zostało zresetowane przez administratora."
        if reset_mode
        else "Utworzono nowe konto w panelu administracyjnym CTIP."
    )
    password_label = "Nowe hasło tymczasowe" if reset_mode else "Hasło tymczasowe"
    message.set_content(
        f"Dzień dobry {greeting},\n\n"
        f"{intro}\n\n"
        f"Adres logowania: {login_url}\n"
        f"Nazwa użytkownika: {user.email}\n"
        f"{password_label}: {password}\n\n"
        "Zalecamy zmianę hasła po pierwszym logowaniu.\n\n"
        "Pozdrawiamy,\nZespół CTIP"
    )

    result = await send_smtp_message(
        host=delivery.host,
        port=delivery.port,
        username=delivery.username,
        password=delivery.password,
        use_tls=delivery.use_tls,
        use_ssl=delivery.use_ssl,
        message=message,
        timeout=15,
        source="admin_user_credentials",
    )
    if result.success:
        logger.info(
            "Wysłano e-mail (%s) z danymi logowania do użytkownika %s.",
            "reset hasła" if reset_mode else "utworzenie konta",
            user.email,
        )
    else:
        logger.warning(
            "Nie wysłano e-maila (%s) z danymi logowania do %s: %s",
            "reset hasła" if reset_mode else "utworzenie konta",
            user.email,
            result.message,
        )


async def queue_credentials_sms(
    session: AsyncSession,
    user: AdminUser,
    password: str,
    *,
    created_by: int | None,
    login_url: str,
    reason: str = "create",
) -> bool:
    """Dodaje SMS z danymi logowania do kolejki po utworzeniu konta lub resecie hasła."""
    if not user.mobile_phone:
        return False
    reset_mode = reason == "password_reset"
    password_label = "Nowe hasło" if reset_mode else "Hasło"
    text = f"Panel CTIP: {login_url}\n" f"Login: {user.email}\n" f"{password_label}: {password}"
    sms = SmsOut(
        dest=user.mobile_phone,
        text=text[:600],
        source="admin",
        origin="admin_user_credentials",
        status="NEW",
        created_by=created_by,
        meta={"type": "admin_user_credentials", "user_id": user.id, "action": reason},
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )
    session.add(sms)
    return True


async def count_active_admins(session: AsyncSession, *, exclude_user_id: int | None = None) -> int:
    """Zlicza aktywnych administratorów (opcjonalnie z wyłączeniem wskazanego ID)."""
    stmt = select(func.count(AdminUser.id)).where(
        AdminUser.role == "admin", AdminUser.is_active.is_(True)
    )
    if exclude_user_id is not None:
        stmt = stmt.where(AdminUser.id != exclude_user_id)
    result = await session.execute(stmt)
    return int(result.scalar() or 0)


async def list_active_salespeople(session: AsyncSession) -> list[AdminUser]:
    """Zwraca aktywnych użytkowników oznaczonych jako handlowcy."""
    stmt = (
        select(AdminUser)
        .where(AdminUser.is_active.is_(True), AdminUser.is_salesperson.is_(True))
        .order_by(AdminUser.last_name.asc(), AdminUser.first_name.asc(), AdminUser.email.asc())
    )
    result = await session.execute(stmt)
    return list(result.scalars())


async def delete_user(session: AsyncSession, user: AdminUser) -> None:
    """Usuwa użytkownika wraz z sesjami."""
    await revoke_sessions(session, user.id)
    await session.delete(user)


__all__ = [
    "UserRow",
    "create_user",
    "list_users",
    "list_sessions",
    "fetch_user",
    "update_user",
    "reset_password",
    "set_user_active",
    "revoke_sessions",
    "generate_password",
    "normalize_mobile_phone",
    "resolve_email_delivery_settings",
    "send_credentials_email",
    "queue_credentials_sms",
    "count_active_admins",
    "list_active_salespeople",
    "delete_user",
]
