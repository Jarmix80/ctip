"""Zarządzanie kartoteką kontaktów w panelu administratora."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.schemas.admin_contacts import (
    AdminContactCreate,
    AdminContactDetail,
    AdminContactListResponse,
    AdminContactSummary,
    AdminContactUpdate,
)
from app.services import admin_contacts
from app.services.audit import record_audit

router = APIRouter(prefix="/admin/contacts", tags=["admin-contacts"])


def _ensure_admin(role: str) -> None:
    if role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Operacja wymaga roli administratora."
        )


def _ensure_can_edit(role: str) -> None:
    if role not in {"admin", "operator"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora lub operatora.",
        )


def _to_summary(contact) -> AdminContactSummary:
    return AdminContactSummary(
        id=contact.id,
        number=contact.number,
        ext=contact.ext,
        firebird_id=contact.firebird_id,
        first_name=contact.first_name,
        last_name=contact.last_name,
        company=contact.company,
        email=contact.email,
        nip=contact.nip,
        notes=contact.notes,
        source=contact.source,
        created_at=contact.created_at,
        updated_at=contact.updated_at,
    )


@router.get("", response_model=AdminContactListResponse, summary="Lista kontaktów")
async def list_admin_contacts(
    search: str | None = Query(default=None, min_length=1, max_length=160),
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminContactListResponse:
    items = await admin_contacts.list_contacts(session, search=search)
    summaries = [_to_summary(item) for item in items]
    return AdminContactListResponse(items=summaries)


@router.get("/{contact_id}", response_model=AdminContactDetail, summary="Szczegóły kontaktu")
async def get_admin_contact(
    contact_id: int = Path(..., ge=1),
    _: tuple = Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminContactDetail:
    contact = await admin_contacts.fetch_contact(session, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono kontaktu."
        )
    return _to_summary(contact)


@router.post(
    "",
    response_model=AdminContactDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Dodaj kontakt",
)
async def create_admin_contact(
    payload: AdminContactCreate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminContactDetail:
    admin_session, admin_user = admin_context
    _ensure_can_edit(admin_user.role)
    try:
        contact = await admin_contacts.create_contact(
            session,
            number=payload.number,
            ext=payload.ext,
            firebird_id=payload.firebird_id,
            first_name=payload.first_name,
            last_name=payload.last_name,
            company=payload.company,
            email=str(payload.email) if payload.email else None,
            nip=payload.nip,
            notes=payload.notes,
            source=payload.source or "manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="contact_create",
        client_ip=admin_session.client_ip,
        payload={
            "contact_id": contact.id,
            "number": contact.number,
            "firebird_id": contact.firebird_id,
        },
    )
    await session.commit()
    return _to_summary(contact)


@router.put("/{contact_id}", response_model=AdminContactDetail, summary="Aktualizuj kontakt")
async def update_admin_contact(
    payload: AdminContactUpdate,
    contact_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminContactDetail:
    admin_session, admin_user = admin_context
    _ensure_can_edit(admin_user.role)
    contact = await admin_contacts.fetch_contact(session, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono kontaktu."
        )
    try:
        updated = await admin_contacts.update_contact(
            session,
            contact,
            number=payload.number,
            ext=payload.ext,
            firebird_id=payload.firebird_id,
            first_name=payload.first_name,
            last_name=payload.last_name,
            company=payload.company,
            email=str(payload.email) if payload.email else None,
            nip=payload.nip,
            notes=payload.notes,
            source=payload.source or "manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await record_audit(
        session,
        user_id=admin_user.id,
        action="contact_update",
        client_ip=admin_session.client_ip,
        payload={
            "contact_id": updated.id,
            "number": updated.number,
            "firebird_id": updated.firebird_id,
        },
    )
    await session.commit()
    return _to_summary(updated)


@router.delete(
    "/{contact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Usuń kontakt",
    response_class=Response,
)
async def delete_admin_contact(
    contact_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    admin_session, admin_user = admin_context
    _ensure_admin(admin_user.role)
    contact = await admin_contacts.fetch_contact(session, contact_id)
    if contact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono kontaktu."
        )
    await admin_contacts.delete_contact(session, contact)
    await record_audit(
        session,
        user_id=admin_user.id,
        action="contact_delete",
        client_ip=admin_session.client_ip,
        payload={
            "contact_id": contact.id,
            "number": contact.number,
            "firebird_id": contact.firebird_id,
        },
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/by-number/{number}", response_model=AdminContactDetail, summary="Znajdź kontakt po numerze"
)
async def get_contact_by_number(
    number: str,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AdminContactDetail:
    """Zwraca kontakt na podstawie numeru MSISDN."""
    _, admin_user = admin_context
    _ensure_can_edit(admin_user.role)
    contact = await admin_contacts.fetch_contact_by_number(session, number)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Kontakt nie istnieje.")
    return _to_summary(contact)
