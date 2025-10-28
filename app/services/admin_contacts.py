"""Operacje na kartotece kontaktów wykonywane z panelu administratora."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


async def list_contacts(
    session: AsyncSession,
    search: str | None = None,
) -> Sequence[Contact]:
    """Zwraca uporządkowaną listę kontaktów spełniających warunek wyszukiwania."""
    stmt: Select[tuple[Contact]] = select(Contact).order_by(Contact.number)
    if search:
        needle = f"%{search.lower()}%"
        stmt = stmt.where(
            (Contact.number.ilike(needle))
            | (Contact.first_name.ilike(needle))
            | (Contact.last_name.ilike(needle))
            | (Contact.company.ilike(needle))
            | (Contact.email.ilike(needle))
            | (Contact.firebird_id.ilike(needle))
        )
    result = await session.execute(stmt)
    return result.scalars().all()


async def fetch_contact(session: AsyncSession, contact_id: int) -> Contact | None:
    """Zwraca pojedynczy kontakt po identyfikatorze."""
    stmt: Select[tuple[Contact]] = select(Contact).where(Contact.id == contact_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def fetch_contact_by_number(session: AsyncSession, number: str) -> Contact | None:
    """Zwraca kontakt na podstawie numeru MSISDN."""
    stmt: Select[tuple[Contact]] = select(Contact).where(Contact.number == number)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def create_contact(
    session: AsyncSession,
    *,
    number: str,
    ext: str | None = None,
    firebird_id: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    company: str | None = None,
    email: str | None = None,
    nip: str | None = None,
    notes: str | None = None,
    source: str = "manual",
) -> Contact:
    """Tworzy nowy rekord kontaktu, pilnując unikalności numeru."""
    contact = Contact(
        number=number.strip(),
        ext=_normalize(ext),
        firebird_id=_normalize(firebird_id),
        first_name=_normalize(first_name),
        last_name=_normalize(last_name),
        company=_normalize(company),
        email=_normalize(email),
        nip=_normalize(nip),
        notes=_normalize(notes),
        source=source.strip() if source else "manual",
    )
    session.add(contact)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ValueError("Kontakt z takim numerem już istnieje.") from exc
    return contact


async def update_contact(
    session: AsyncSession,
    contact: Contact,
    *,
    number: str,
    ext: str | None = None,
    firebird_id: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    company: str | None = None,
    email: str | None = None,
    nip: str | None = None,
    notes: str | None = None,
    source: str = "manual",
) -> Contact:
    """Aktualizuje istniejący kontakt i odświeża znaczniki czasu."""
    contact.number = number.strip()
    contact.ext = _normalize(ext)
    contact.firebird_id = _normalize(firebird_id)
    contact.first_name = _normalize(first_name)
    contact.last_name = _normalize(last_name)
    contact.company = _normalize(company)
    contact.email = _normalize(email)
    contact.nip = _normalize(nip)
    contact.notes = _normalize(notes)
    contact.source = source.strip() if source else "manual"
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ValueError("Kontakt z takim numerem już istnieje.") from exc
    return contact


async def delete_contact(session: AsyncSession, contact: Contact) -> None:
    """Usuwa rekord kontaktu."""
    await session.delete(contact)
    await session.flush()
