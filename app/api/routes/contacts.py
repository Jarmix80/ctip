"""Operacje związane z kartoteką kontaktów."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.models import Contact, ContactDevice
from app.schemas.contact import ContactDeviceSchema, ContactSchema

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("/{number}", response_model=ContactSchema)
async def get_contact(
    number: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ContactSchema:
    """Zwraca dane kontaktowe powiązane z numerem MSISDN."""
    stmt = select(Contact).where(Contact.number == number)
    result = await session.execute(stmt)
    contact = result.scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Kontakt nie istnieje")

    devices_result = await session.execute(
        select(ContactDevice).where(ContactDevice.contact_id == contact.id)
    )
    devices = devices_result.scalars().all()

    return ContactSchema(
        id=contact.id,
        number=contact.number,
        ext=contact.ext,
        first_name=contact.first_name,
        last_name=contact.last_name,
        company=contact.company,
        nip=contact.nip,
        email=contact.email,
        notes=contact.notes,
        source=contact.source,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
        devices=[
            ContactDeviceSchema(
                id=device.id,
                device_name=device.device_name,
                serial_number=device.serial_number,
                location=device.location,
                notes=device.notes,
                created_at=device.created_at,
            )
            for device in devices
        ],
    )


@router.get("", response_model=list[ContactSchema])
async def search_contacts(
    search: str = Query(..., min_length=1),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[ContactSchema]:
    """Prosta wyszukiwarka kontaktów po numerze, nazwie firmy lub osobie."""
    needle = f"%{search.lower()}%"
    stmt = select(Contact).where(
        (Contact.number.ilike(needle))
        | (Contact.company.ilike(needle))
        | (Contact.first_name.ilike(needle))
        | (Contact.last_name.ilike(needle))
    )
    result = await session.execute(stmt)
    contacts = result.scalars().all()

    return [
        ContactSchema(
            id=contact.id,
            number=contact.number,
            ext=contact.ext,
            first_name=contact.first_name,
            last_name=contact.last_name,
            company=contact.company,
            nip=contact.nip,
            email=contact.email,
            notes=contact.notes,
            source=contact.source,
            created_at=contact.created_at,
            updated_at=contact.updated_at,
            devices=[],
        )
        for contact in contacts
    ]
