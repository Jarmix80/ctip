"""Obsługa trwałego rejestru wiadomości mailboxa GRENKE."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContractsMailboxHistoryCase, ContractsMailboxMessage
from app.services.contracts_mailbox import normalize_application_number

MAILBOX_STATUS_PENDING = "pending"
MAILBOX_STATUS_LINKED_FORM = "linked_form"
MAILBOX_STATUS_HISTORICAL_ARCHIVED = "historical_archived"
MAILBOX_STATUS_IGNORED = "ignored"
MAILBOX_STATUS_MANUAL_HOLD = "manual_hold"
MAILBOX_STATUS_ERROR = "error"

MAILBOX_TERMINAL_STATUSES = {
    MAILBOX_STATUS_LINKED_FORM,
    MAILBOX_STATUS_HISTORICAL_ARCHIVED,
    MAILBOX_STATUS_IGNORED,
    MAILBOX_STATUS_MANUAL_HOLD,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _safe_attachment_manifest(value: Any) -> list[dict[str, Any]]:
    """Zwraca publiczne metadane załączników bez ścieżek serwera."""
    if not isinstance(value, list):
        return []
    output: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        output.append(
            {
                "index": index,
                "file_name": str(
                    item.get("original_name")
                    or item.get("file_name")
                    or item.get("saved_name")
                    or ""
                ),
                "content_type": str(item.get("content_type") or "application/octet-stream"),
                "size_bytes": int(item.get("size_bytes") or 0),
                "sha256": str(item.get("sha256") or "") or None,
            }
        )
    return output


async def get_mailbox_message(
    session: AsyncSession,
    *,
    message_id: str,
) -> ContractsMailboxMessage | None:
    """Pobiera wiadomość po stabilnym identyfikatorze Message-ID."""
    return (
        await session.execute(
            select(ContractsMailboxMessage).where(
                ContractsMailboxMessage.message_id == str(message_id)
            )
        )
    ).scalar_one_or_none()


async def register_mailbox_message(
    session: AsyncSession,
    *,
    message_id: str,
    mailbox_folder: str,
    imap_id: str | None,
    subject: str,
    sender: str,
    body_text: str,
    received_at: datetime,
    application_no_raw: str | None,
    application_no_normalized: str | None,
    proforma_no_raw: str | None,
    proforma_no_normalized: str | None,
) -> ContractsMailboxMessage:
    """Tworzy lub odświeża wpis wiadomości przed właściwą klasyfikacją."""
    now = _utc_now()
    item = await get_mailbox_message(session, message_id=message_id)
    if item is None:
        item = ContractsMailboxMessage(
            message_id=str(message_id),
            first_seen_at=now,
            attempts=0,
        )
        session.add(item)
    item.mailbox_folder = str(mailbox_folder or "INBOX")
    item.imap_id = str(imap_id) if imap_id is not None else None
    item.subject = str(subject or "")
    item.sender = str(sender or "")
    item.body_text = str(body_text or "")
    item.received_at = received_at
    item.application_no_raw = application_no_raw
    item.application_no_normalized = application_no_normalized
    item.proforma_no_raw = proforma_no_raw
    item.proforma_no_normalized = proforma_no_normalized
    item.last_seen_at = now
    item.updated_at = now
    item.attempts = int(item.attempts or 0) + 1
    if item.processing_status not in MAILBOX_TERMINAL_STATUSES:
        item.processing_status = MAILBOX_STATUS_PENDING
        item.last_error = None
    await session.flush()
    return item


async def get_or_create_history_case(
    session: AsyncSession,
    *,
    application_no_raw: str,
    application_no_normalized: str,
    subject: str,
) -> ContractsMailboxHistoryCase:
    """Zwraca zamkniętą sprawę historyczną dla kanonicznego numeru wniosku."""
    item = (
        await session.execute(
            select(ContractsMailboxHistoryCase).where(
                ContractsMailboxHistoryCase.application_no_normalized == application_no_normalized
            )
        )
    ).scalar_one_or_none()
    now = _utc_now()
    if item is None:
        item = ContractsMailboxHistoryCase(
            application_no_raw=application_no_raw,
            application_no_normalized=application_no_normalized,
            title=subject or f"Wniosek {application_no_raw}",
            status="historical_closed",
            source="mailbox_backfill",
            archived_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(item)
        await session.flush()
    return item


async def finalize_mailbox_message(
    session: AsyncSession,
    *,
    item: ContractsMailboxMessage,
    processing_status: str,
    classification: str,
    event_type: str | None,
    details: str | None,
    attachment_manifest: list[dict[str, Any]] | None = None,
    form_request_id: int | None = None,
    history_case_id: int | None = None,
    error: str | None = None,
) -> ContractsMailboxMessage:
    """Kończy obsługę wiadomości jednym jawnym stanem przetwarzania."""
    allowed = MAILBOX_TERMINAL_STATUSES | {MAILBOX_STATUS_ERROR, MAILBOX_STATUS_PENDING}
    if processing_status not in allowed:
        raise ValueError(f"Nieobsługiwany status mailboxa: {processing_status}")
    if form_request_id is not None and history_case_id is not None:
        raise ValueError("Wiadomość nie może należeć jednocześnie do formularza i historii.")
    now = _utc_now()
    item.processing_status = processing_status
    item.classification = classification
    item.event_type = event_type
    item.details = details
    item.attachment_manifest = list(attachment_manifest or [])
    item.form_request_id = form_request_id
    item.history_case_id = history_case_id
    item.last_error = error
    item.processed_at = now if processing_status in allowed - {MAILBOX_STATUS_PENDING} else None
    item.updated_at = now
    await session.flush()
    if history_case_id is not None:
        await refresh_history_case_stats(session, history_case_id=history_case_id)
    return item


async def refresh_history_case_stats(session: AsyncSession, *, history_case_id: int) -> None:
    """Przelicza daty i liczbę wiadomości przypiętych do sprawy historycznej."""
    count_value, first_value, last_value = (
        await session.execute(
            select(
                func.count(ContractsMailboxMessage.id),
                func.min(ContractsMailboxMessage.received_at),
                func.max(ContractsMailboxMessage.received_at),
            ).where(ContractsMailboxMessage.history_case_id == history_case_id)
        )
    ).one()
    history_case = await session.get(ContractsMailboxHistoryCase, history_case_id)
    if history_case is None:
        return
    history_case.message_count = int(count_value or 0)
    history_case.first_message_at = first_value
    history_case.last_message_at = last_value
    history_case.updated_at = _utc_now()
    await session.flush()


def serialize_mailbox_message(
    item: ContractsMailboxMessage, *, include_body: bool
) -> dict[str, Any]:
    """Serializuje wiadomość bez ujawniania ścieżek lokalnego systemu plików."""
    payload = {
        "id": item.id,
        "message_id": item.message_id,
        "processing_status": item.processing_status,
        "classification": item.classification,
        "event_type": item.event_type,
        "application_no": item.application_no_raw,
        "application_no_normalized": item.application_no_normalized,
        "proforma_no": item.proforma_no_raw,
        "subject": item.subject,
        "sender": item.sender,
        "received_at": item.received_at.isoformat() if item.received_at else None,
        "details": item.details,
        "form_request_id": item.form_request_id,
        "history_case_id": item.history_case_id,
        "attachments": _safe_attachment_manifest(item.attachment_manifest),
    }
    if include_body:
        payload["body_text"] = item.body_text
    return payload


async def list_form_mailbox_messages(
    session: AsyncSession,
    *,
    form_request_id: int,
) -> list[dict[str, Any]]:
    """Zwraca pełną korespondencję przypiętą do formularza."""
    rows = list(
        (
            await session.execute(
                select(ContractsMailboxMessage)
                .where(ContractsMailboxMessage.form_request_id == form_request_id)
                .order_by(
                    ContractsMailboxMessage.received_at.desc(),
                    ContractsMailboxMessage.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return [serialize_mailbox_message(item, include_body=True) for item in rows]


async def list_history_cases(
    session: AsyncSession,
    *,
    query: str | None,
    date_from: date | None,
    date_to: date | None,
    event_type: str | None,
    has_attachments: bool | None,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """Wyszukuje zamknięte sprawy historyczne wraz z filtrami korespondencji."""
    conditions = []
    text_query = str(query or "").strip()
    if text_query:
        pattern = f"%{text_query}%"
        normalized_application = normalize_application_number(text_query)
        conditions.append(
            or_(
                ContractsMailboxHistoryCase.application_no_raw.ilike(pattern),
                ContractsMailboxHistoryCase.application_no_normalized.ilike(pattern),
                ContractsMailboxHistoryCase.title.ilike(pattern),
                ContractsMailboxMessage.subject.ilike(pattern),
                ContractsMailboxMessage.sender.ilike(pattern),
                ContractsMailboxMessage.body_text.ilike(pattern),
                *(
                    [
                        ContractsMailboxHistoryCase.application_no_normalized
                        == normalized_application
                    ]
                    if normalized_application
                    else []
                ),
            )
        )
    if date_from is not None:
        conditions.append(
            ContractsMailboxMessage.received_at >= datetime.combine(date_from, time.min, tzinfo=UTC)
        )
    if date_to is not None:
        conditions.append(
            ContractsMailboxMessage.received_at <= datetime.combine(date_to, time.max, tzinfo=UTC)
        )
    if event_type:
        conditions.append(ContractsMailboxMessage.event_type == event_type)
    if has_attachments is True:
        conditions.append(func.json_array_length(ContractsMailboxMessage.attachment_manifest) > 0)
    elif has_attachments is False:
        conditions.append(func.json_array_length(ContractsMailboxMessage.attachment_manifest) == 0)

    base = (
        select(ContractsMailboxHistoryCase)
        .join(
            ContractsMailboxMessage,
            ContractsMailboxMessage.history_case_id == ContractsMailboxHistoryCase.id,
        )
        .where(*conditions)
        .distinct()
    )
    total = int(
        (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    )
    cases = list(
        (
            await session.execute(
                base.order_by(
                    ContractsMailboxHistoryCase.last_message_at.desc(),
                    ContractsMailboxHistoryCase.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": item.id,
                "application_no": item.application_no_raw,
                "application_no_normalized": item.application_no_normalized,
                "title": item.title,
                "status": item.status,
                "message_count": item.message_count,
                "first_message_at": (
                    item.first_message_at.isoformat() if item.first_message_at else None
                ),
                "last_message_at": (
                    item.last_message_at.isoformat() if item.last_message_at else None
                ),
                "archived_at": item.archived_at.isoformat() if item.archived_at else None,
            }
            for item in cases
        ],
    }


async def get_history_case_detail(
    session: AsyncSession,
    *,
    history_case_id: int,
) -> dict[str, Any] | None:
    """Zwraca sprawę historyczną i pełną, bezpiecznie serializowaną korespondencję."""
    item = await session.get(ContractsMailboxHistoryCase, history_case_id)
    if item is None:
        return None
    messages = list(
        (
            await session.execute(
                select(ContractsMailboxMessage)
                .where(ContractsMailboxMessage.history_case_id == history_case_id)
                .order_by(
                    ContractsMailboxMessage.received_at.asc(),
                    ContractsMailboxMessage.id.asc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return {
        "id": item.id,
        "application_no": item.application_no_raw,
        "application_no_normalized": item.application_no_normalized,
        "title": item.title,
        "status": item.status,
        "message_count": item.message_count,
        "first_message_at": item.first_message_at.isoformat() if item.first_message_at else None,
        "last_message_at": item.last_message_at.isoformat() if item.last_message_at else None,
        "archived_at": item.archived_at.isoformat() if item.archived_at else None,
        "messages": [serialize_mailbox_message(message, include_body=True) for message in messages],
    }


async def mailbox_ledger_counts(session: AsyncSession) -> dict[str, int]:
    """Zwraca liczniki stanów potrzebne do audytu i wdrożenia."""
    rows = (
        await session.execute(
            select(
                ContractsMailboxMessage.processing_status,
                func.count(ContractsMailboxMessage.id),
            ).group_by(ContractsMailboxMessage.processing_status)
        )
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    counts["total"] = sum(counts.values())
    counts["unresolved_open"] = counts.get(MAILBOX_STATUS_PENDING, 0) + counts.get(
        MAILBOX_STATUS_ERROR, 0
    )
    counts["manual_hold"] = counts.get(MAILBOX_STATUS_MANUAL_HOLD, 0)
    return counts


__all__ = [
    "MAILBOX_STATUS_ERROR",
    "MAILBOX_STATUS_HISTORICAL_ARCHIVED",
    "MAILBOX_STATUS_IGNORED",
    "MAILBOX_STATUS_LINKED_FORM",
    "MAILBOX_STATUS_MANUAL_HOLD",
    "MAILBOX_STATUS_PENDING",
    "MAILBOX_TERMINAL_STATUSES",
    "finalize_mailbox_message",
    "get_history_case_detail",
    "get_mailbox_message",
    "get_or_create_history_case",
    "list_form_mailbox_messages",
    "list_history_cases",
    "mailbox_ledger_counts",
    "register_mailbox_message",
    "serialize_mailbox_message",
]
