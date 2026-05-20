"""API modułu CTIP AI Asystent (tryb tylko odczytu)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_admin_session_context, get_db_session
from app.core.config import settings
from app.models import (
    AdminUser,
    AssistantChangeRequest,
    AssistantChatMessage,
    AssistantChatThread,
    AssistantToolCallLog,
    AssistantUserProfile,
    AssistantWeeklyInsight,
)
from app.schemas.assistant import (
    AssistantActionExecutionResponse,
    AssistantChangeRequestCreate,
    AssistantChangeRequestDecision,
    AssistantChangeRequestRead,
    AssistantChatCreateRequest,
    AssistantChatDetail,
    AssistantChatMessageRead,
    AssistantChatMessageResponse,
    AssistantChatPromptRequest,
    AssistantChatSummary,
    AssistantLearningProfileRead,
    AssistantLearningProfileUpdate,
    AssistantPendingActionInfo,
    AssistantSourceInfo,
    AssistantWeeklyInsightRead,
    AssistantWorkerInfo,
)
from app.services.assistant_learning import update_user_learning_profile
from app.services.assistant_runtime import AssistantRuntime
from app.services.assistant_workers import (
    DEFAULT_WORKER_KEY,
    get_worker_profile,
    list_worker_profiles,
)
from app.services.assistant_workflow_devices import (
    WORKFLOW_DEVICES_STAGE_REQUEST_TYPE,
    execute_workflow_devices_chat_sheet_stage,
)

router = APIRouter(prefix="/assistant", tags=["assistant"])


def _assert_admin(user: AdminUser) -> None:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operacja wymaga roli administratora.",
        )


def _map_chat_summary(item: AssistantChatThread) -> AssistantChatSummary:
    worker = get_worker_profile(item.worker_key)
    return AssistantChatSummary(
        id=item.id,
        title=item.title,
        worker_key=worker.key,  # type: ignore[arg-type]
        status=item.status,  # type: ignore[arg-type]
        created_at=item.created_at,
        updated_at=item.updated_at,
        last_activity_at=item.last_activity_at,
    )


def _map_chat_message(item: AssistantChatMessage) -> AssistantChatMessageRead:
    return AssistantChatMessageRead(
        id=item.id,
        thread_id=item.thread_id,
        role=item.role,  # type: ignore[arg-type]
        content=item.content,
        created_at=item.created_at,
        openai_response_id=item.openai_response_id,
        model_name=item.model_name,
        input_tokens=item.input_tokens,
        output_tokens=item.output_tokens,
    )


def _map_change_request(item: AssistantChangeRequest) -> AssistantChangeRequestRead:
    return AssistantChangeRequestRead(
        id=item.id,
        created_by=item.created_by,
        thread_id=item.thread_id,
        message_id=item.message_id,
        request_text=item.request_text,
        justification=item.justification,
        payload=item.payload,
        status=item.status,  # type: ignore[arg-type]
        decided_by=item.decided_by,
        decided_at=item.decided_at,
        decision_note=item.decision_note,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


def _map_weekly_insight(item: AssistantWeeklyInsight) -> AssistantWeeklyInsightRead:
    return AssistantWeeklyInsightRead(
        id=item.id,
        week_start=item.week_start,
        generated_at=item.generated_at,
        generated_by=item.generated_by,
        summary=item.summary,
        details=item.details,
    )


def _map_learning_profile(item: AssistantUserProfile) -> AssistantLearningProfileRead:
    return AssistantLearningProfileRead(
        user_id=item.user_id,
        personalization_enabled=bool(item.personalization_enabled),
        preferences=item.preferences if isinstance(item.preferences, dict) else {},
        memory_notes=item.memory_notes,
        updated_at=item.updated_at,
    )


def _chunk_text(text: str, *, chunk_size: int = 180) -> list[str]:
    if not text:
        return [""]
    chunks: list[str] = []
    index = 0
    while index < len(text):
        chunks.append(text[index : index + chunk_size])
        index += chunk_size
    return chunks


async def _load_thread_for_user(
    session: AsyncSession,
    *,
    thread_id: int,
    user_id: int,
) -> AssistantChatThread:
    stmt = select(AssistantChatThread).where(
        AssistantChatThread.id == thread_id,
        AssistantChatThread.owner_user_id == user_id,
    )
    thread = (await session.execute(stmt)).scalar_one_or_none()
    if thread is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Brak dostępu do wątku.")
    return thread


async def _build_stream_response(
    payload: AssistantChatMessageResponse,
) -> StreamingResponse:
    async def _stream():
        meta = {
            "user_message_id": payload.user_message_id,
            "assistant_message_id": payload.assistant_message.id,
            "blocked_as_change_request": payload.blocked_as_change_request,
            "change_request_id": payload.change_request_id,
            "pending_action": (
                payload.pending_action.model_dump(mode="json")
                if payload.pending_action is not None
                else None
            ),
        }
        yield f"event: meta\ndata: {json.dumps(meta, ensure_ascii=False)}\n\n"
        for chunk in _chunk_text(payload.assistant_message.content):
            yield f"event: chunk\ndata: {json.dumps({'text': chunk}, ensure_ascii=False)}\n\n"
        done_payload = payload.model_dump(mode="json")
        yield f"event: done\ndata: {json.dumps(done_payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.post("/chats", response_model=AssistantChatSummary, summary="Utwórz nowy chat asystenta")
async def create_chat(
    payload: AssistantChatCreateRequest | None = None,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AssistantChatSummary:
    """Tworzy nowy wątek rozmowy dla zalogowanego użytkownika."""
    _, admin_user = admin_context
    now = datetime.now(UTC)
    title = (payload.title.strip() if payload and payload.title else "") or "Nowa rozmowa"
    worker_key = payload.worker_key if payload and payload.worker_key else DEFAULT_WORKER_KEY
    worker = get_worker_profile(worker_key)
    thread = AssistantChatThread(
        owner_user_id=admin_user.id,
        title=title,
        worker_key=worker.key,
        status="active",
        created_at=now,
        updated_at=now,
        last_activity_at=now,
    )
    session.add(thread)
    await session.commit()
    await session.refresh(thread)
    return _map_chat_summary(thread)


@router.get(
    "/workers",
    response_model=list[AssistantWorkerInfo],
    summary="Lista dostępnych pracowników AI",
)
async def list_assistant_workers(
    _admin_context=Depends(get_admin_session_context),  # noqa: B008
) -> list[AssistantWorkerInfo]:
    """Zwraca listę profili pracowników AI dostępnych w module czatu."""
    return [
        AssistantWorkerInfo(
            key=profile.key,  # type: ignore[arg-type]
            name=profile.name,
            description=profile.description,
        )
        for profile in list_worker_profiles()
    ]


@router.get("/chats", response_model=list[AssistantChatSummary], summary="Lista chatów użytkownika")
async def list_chats(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AssistantChatSummary]:
    """Zwraca listę czatów bieżącego użytkownika."""
    _, admin_user = admin_context
    stmt = (
        select(AssistantChatThread)
        .where(
            AssistantChatThread.owner_user_id == admin_user.id,
            AssistantChatThread.status != "deleted",
        )
        .order_by(desc(AssistantChatThread.last_activity_at))
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_map_chat_summary(item) for item in rows]


@router.get(
    "/chats/{chat_id}",
    response_model=AssistantChatDetail,
    summary="Historia wskazanego chatu",
)
async def get_chat_history(
    chat_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    limit: int = Query(default=300, ge=1, le=2000),
) -> AssistantChatDetail:
    """Zwraca wątek i historię wiadomości dla zalogowanego użytkownika."""
    _, admin_user = admin_context
    thread = await _load_thread_for_user(
        session,
        thread_id=chat_id,
        user_id=admin_user.id,
    )
    stmt_messages = (
        select(AssistantChatMessage)
        .where(AssistantChatMessage.thread_id == thread.id)
        .order_by(AssistantChatMessage.created_at.asc())
        .limit(limit)
    )
    messages = (await session.execute(stmt_messages)).scalars().all()
    return AssistantChatDetail(
        thread=_map_chat_summary(thread),
        messages=[_map_chat_message(item) for item in messages],
    )


@router.post(
    "/chats/{chat_id}/messages",
    response_model=AssistantChatMessageResponse,
    summary="Wyślij prompt do asystenta",
)
async def send_chat_message(
    payload: AssistantChatPromptRequest,
    chat_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> Response:
    """Obsługuje prompt użytkownika i zwraca odpowiedź asystenta (JSON albo SSE)."""
    _, admin_user = admin_context
    thread = await _load_thread_for_user(
        session,
        thread_id=chat_id,
        user_id=admin_user.id,
    )
    now = datetime.now(UTC)
    user_message = AssistantChatMessage(
        thread_id=thread.id,
        user_id=admin_user.id,
        role="user",
        content=payload.prompt,
        created_at=now,
    )
    session.add(user_message)
    await session.flush()

    history_stmt = (
        select(AssistantChatMessage)
        .where(
            AssistantChatMessage.thread_id == thread.id,
            AssistantChatMessage.id != user_message.id,
        )
        .order_by(AssistantChatMessage.created_at.asc())
        .limit(50)
    )
    history_items = (await session.execute(history_stmt)).scalars().all()
    history = [
        {"role": item.role, "content": item.content}
        for item in history_items
        if item.role in {"user", "assistant"}
    ]

    runtime = AssistantRuntime(session=session, secret_key=settings.admin_secret_key)
    generation = await runtime.generate(
        user_id=admin_user.id,
        prompt=payload.prompt,
        history=history,
        worker_key=thread.worker_key,
    )

    assistant_message = AssistantChatMessage(
        thread_id=thread.id,
        user_id=admin_user.id,
        role="assistant",
        content=generation.answer_text,
        openai_response_id=generation.response_id,
        model_name=generation.model_name,
        input_tokens=generation.input_tokens,
        output_tokens=generation.output_tokens,
        created_at=datetime.now(UTC),
    )
    session.add(assistant_message)
    await session.flush()

    for tool_result in generation.tool_results:
        tool_log = AssistantToolCallLog(
            thread_id=thread.id,
            message_id=assistant_message.id,
            user_id=admin_user.id,
            tool_name=tool_result.tool_name,
            status=tool_result.status,
            tool_input=None,
            tool_output=tool_result.payload,
            generated_sql=tool_result.generated_sql,
            row_count=tool_result.row_count,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            duration_ms=tool_result.duration_ms,
            error_message=tool_result.error_message,
        )
        session.add(tool_log)

    await update_user_learning_profile(
        session,
        user_id=admin_user.id,
        prompt=payload.prompt,
        tool_results=generation.tool_results,
    )

    change_request_id: int | None = None
    pending_action_info: AssistantPendingActionInfo | None = None
    if generation.blocked_as_change_request:
        change_request = AssistantChangeRequest(
            created_by=admin_user.id,
            thread_id=thread.id,
            message_id=user_message.id,
            request_text=payload.prompt,
            justification="Utworzono automatycznie po wykryciu prośby o zapis/modyfikację.",
            payload={"source": "assistant_auto_block"},
            status="pending",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(change_request)
        await session.flush()
        change_request_id = change_request.id
    elif generation.pending_action:
        action_payload = dict(generation.pending_action)
        change_request = AssistantChangeRequest(
            created_by=admin_user.id,
            thread_id=thread.id,
            message_id=user_message.id,
            request_text=str(
                action_payload.get("request_text") or "Akcja asystenta oczekująca na wykonanie."
            ),
            justification=str(action_payload.get("justification") or ""),
            payload=action_payload,
            status="pending",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        session.add(change_request)
        await session.flush()
        change_request_id = change_request.id
        pending_action_info = AssistantPendingActionInfo(
            id=change_request.id,
            type=action_payload.get("type"),  # type: ignore[arg-type]
            label=str(action_payload.get("label") or "Wykonaj akcję"),
            description=str(action_payload.get("justification") or "") or None,
            summary=(
                action_payload.get("summary")
                if isinstance(action_payload.get("summary"), dict)
                else None
            ),
        )

    thread.updated_at = datetime.now(UTC)
    thread.last_activity_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(assistant_message)

    response_payload = AssistantChatMessageResponse(
        user_message_id=user_message.id,
        assistant_message=_map_chat_message(assistant_message),
        sources=[
            AssistantSourceInfo(
                tool=source["tool"],  # type: ignore[arg-type]
                row_count=source.get("row_count"),
                duration_ms=source.get("duration_ms"),
            )
            for source in generation.sources
            if source.get("tool")
            in {
                "firebird_read",
                "firebird_business_read",
                "firebird_knowledge_read",
                "sheets_read",
                "imap_read",
                "ctip_schema_read",
                "email_send_report",
                "workflow_devices_audit",
            }
        ],
        blocked_as_change_request=generation.blocked_as_change_request,
        change_request_id=change_request_id,
        pending_action=pending_action_info,
    )

    if payload.stream:
        return await _build_stream_response(response_payload)
    return response_payload


@router.post(
    "/change-requests",
    response_model=AssistantChangeRequestRead,
    summary="Utwórz wniosek o zmianę",
)
async def create_change_request(
    payload: AssistantChangeRequestCreate,
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AssistantChangeRequestRead:
    """Tworzy wniosek o zmianę (zmiany są realizowane dopiero po akceptacji)."""
    _, admin_user = admin_context
    if payload.thread_id is not None:
        await _load_thread_for_user(session, thread_id=payload.thread_id, user_id=admin_user.id)
    if payload.message_id is not None:
        msg_stmt = select(AssistantChatMessage).where(
            AssistantChatMessage.id == payload.message_id,
        )
        message = (await session.execute(msg_stmt)).scalar_one_or_none()
        if message is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Wiadomość nie istnieje.",
            )
        await _load_thread_for_user(session, thread_id=message.thread_id, user_id=admin_user.id)

    now = datetime.now(UTC)
    item = AssistantChangeRequest(
        created_by=admin_user.id,
        thread_id=payload.thread_id,
        message_id=payload.message_id,
        request_text=payload.request_text,
        justification=payload.justification,
        payload=payload.payload,
        status="pending",
        created_at=now,
        updated_at=now,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return _map_change_request(item)


@router.get(
    "/change-requests",
    response_model=list[AssistantChangeRequestRead],
    summary="Lista wniosków o zmianę",
)
async def list_change_requests(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AssistantChangeRequestRead]:
    """Zwraca listę wniosków (admin: wszystkie, użytkownik: własne)."""
    _, admin_user = admin_context
    where_clauses = []
    if admin_user.role != "admin":
        where_clauses.append(AssistantChangeRequest.created_by == admin_user.id)
    if status_filter:
        where_clauses.append(AssistantChangeRequest.status == status_filter)
    stmt = (
        select(AssistantChangeRequest)
        .order_by(desc(AssistantChangeRequest.created_at))
        .limit(limit)
    )
    if where_clauses:
        stmt = stmt.where(and_(*where_clauses))
    rows = (await session.execute(stmt)).scalars().all()
    return [_map_change_request(item) for item in rows]


@router.post(
    "/change-requests/{request_id}/approve",
    response_model=AssistantChangeRequestRead,
    summary="Zatwierdź wniosek o zmianę",
)
async def approve_change_request(
    payload: AssistantChangeRequestDecision | None = None,
    request_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AssistantChangeRequestRead:
    """Oznacza wniosek jako zatwierdzony (realizacja poza modułem czatu)."""
    _, admin_user = admin_context
    _assert_admin(admin_user)
    stmt = select(AssistantChangeRequest).where(AssistantChangeRequest.id == request_id)
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wniosek nie istnieje.")
    if item.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Wniosek nie jest w statusie pending.",
        )
    item.status = "approved"
    item.decided_by = admin_user.id
    item.decided_at = datetime.now(UTC)
    item.decision_note = payload.note if payload else None
    item.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(item)
    return _map_change_request(item)


@router.post(
    "/change-requests/{request_id}/reject",
    response_model=AssistantChangeRequestRead,
    summary="Odrzuć wniosek o zmianę",
)
async def reject_change_request(
    payload: AssistantChangeRequestDecision | None = None,
    request_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AssistantChangeRequestRead:
    """Oznacza wniosek jako odrzucony."""
    _, admin_user = admin_context
    _assert_admin(admin_user)
    stmt = select(AssistantChangeRequest).where(AssistantChangeRequest.id == request_id)
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wniosek nie istnieje.")
    if item.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Wniosek nie jest w statusie pending.",
        )
    item.status = "rejected"
    item.decided_by = admin_user.id
    item.decided_at = datetime.now(UTC)
    item.decision_note = payload.note if payload else None
    item.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(item)
    return _map_change_request(item)


@router.post(
    "/change-requests/{request_id}/execute-workflow-devices-chat-sheet",
    response_model=AssistantActionExecutionResponse,
    summary="Wykonaj staging urządzeń do urzadzenia_chat",
)
async def execute_workflow_devices_chat_sheet_request(
    request_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AssistantActionExecutionResponse:
    """Czyści i zapisuje zakładkę roboczą `urzadzenia_chat` na podstawie zatwierdzonej paczki."""

    _, admin_user = admin_context
    stmt = select(AssistantChangeRequest).where(AssistantChangeRequest.id == request_id)
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wniosek nie istnieje.")
    if item.created_by != admin_user.id and admin_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Brak uprawnień do wykonania tej akcji.",
        )
    if item.status not in {"pending", "approved"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Wniosek nie jest gotowy do wykonania.",
        )
    payload = item.payload if isinstance(item.payload, dict) else {}
    if payload.get("type") != WORKFLOW_DEVICES_STAGE_REQUEST_TYPE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Wniosek nie dotyczy stagingu urządzeń do urzadzenia_chat.",
        )

    try:
        result = await execute_workflow_devices_chat_sheet_stage(session, payload=payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc).strip() or type(exc).__name__,
        ) from exc

    item.status = "executed"
    item.decided_by = admin_user.id
    item.decided_at = datetime.now(UTC)
    item.decision_note = (
        f"Zapisano {result.get('written_rows', 0)} wierszy do zakładki urzadzenia_chat."
    )
    item.payload = {**payload, "execution_result": result}
    item.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(item)
    return AssistantActionExecutionResponse(
        change_request=_map_change_request(item),
        result=result,
    )


@router.get(
    "/insights/weekly",
    response_model=list[AssistantWeeklyInsightRead],
    summary="Tygodniowy raport usprawnień",
)
async def get_weekly_insights(
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
    limit: int = Query(default=20, ge=1, le=100),
) -> list[AssistantWeeklyInsightRead]:
    """Zwraca tygodniowe podsumowania dla administratora."""
    _, admin_user = admin_context
    _assert_admin(admin_user)

    utc_now = datetime.now(UTC)
    week_start = utc_now.date() - timedelta(days=utc_now.weekday())
    current_stmt = select(AssistantWeeklyInsight).where(
        AssistantWeeklyInsight.week_start == week_start
    )
    current = (await session.execute(current_stmt)).scalar_one_or_none()
    if current is None:
        period_start = datetime.combine(week_start, datetime.min.time(), tzinfo=UTC)
        period_end = period_start + timedelta(days=7)

        tool_stats_stmt = (
            select(
                AssistantToolCallLog.tool_name,
                AssistantToolCallLog.status,
                func.count(AssistantToolCallLog.id),
            )
            .where(
                AssistantToolCallLog.started_at >= period_start,
                AssistantToolCallLog.started_at < period_end,
            )
            .group_by(AssistantToolCallLog.tool_name, AssistantToolCallLog.status)
        )
        tool_stats_rows = (await session.execute(tool_stats_stmt)).all()
        tools_summary = [
            {"tool": row[0], "status": row[1], "count": int(row[2] or 0)} for row in tool_stats_rows
        ]

        change_stats_stmt = (
            select(
                AssistantChangeRequest.status,
                func.count(AssistantChangeRequest.id),
            )
            .where(
                AssistantChangeRequest.created_at >= period_start,
                AssistantChangeRequest.created_at < period_end,
            )
            .group_by(AssistantChangeRequest.status)
        )
        change_rows = (await session.execute(change_stats_stmt)).all()
        change_summary = {str(row[0]): int(row[1] or 0) for row in change_rows}

        pending_count = int(change_summary.get("pending", 0))
        rejected_count = int(change_summary.get("rejected", 0))
        approved_count = int(change_summary.get("approved", 0))
        summary_text = (
            "Raport tygodniowy asystenta: "
            f"wnioski pending={pending_count}, approved={approved_count}, rejected={rejected_count}. "
            "Zalecenie: przejrzeć wnioski pending i zweryfikować najczęstsze pytania użytkowników."
        )
        current = AssistantWeeklyInsight(
            week_start=week_start,
            generated_at=utc_now,
            generated_by=admin_user.id,
            summary=summary_text,
            details={
                "change_requests": change_summary,
                "tool_calls": tools_summary,
                "generated_for_period": {
                    "from": period_start.isoformat(),
                    "to": period_end.isoformat(),
                },
            },
        )
        session.add(current)
        await session.commit()

    stmt = (
        select(AssistantWeeklyInsight)
        .order_by(
            desc(AssistantWeeklyInsight.week_start), desc(AssistantWeeklyInsight.generated_at)
        )
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [_map_weekly_insight(item) for item in rows]


@router.get(
    "/users/{user_id}/learning-profile",
    response_model=AssistantLearningProfileRead,
    summary="Profil uczenia asystenta użytkownika (admin)",
)
async def get_learning_profile(
    user_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AssistantLearningProfileRead:
    """Zwraca profil uczenia asystenta wskazanego użytkownika (admin-only)."""
    _, admin_user = admin_context
    _assert_admin(admin_user)
    target_user = (
        await session.execute(select(AdminUser).where(AdminUser.id == user_id))
    ).scalar_one_or_none()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Użytkownik nie istnieje."
        )

    profile = (
        await session.execute(
            select(AssistantUserProfile).where(AssistantUserProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = AssistantUserProfile(
            user_id=user_id,
            personalization_enabled=True,
            preferences={},
            memory_notes=None,
            updated_at=datetime.now(UTC),
        )
        session.add(profile)
        await session.commit()
        await session.refresh(profile)
    return _map_learning_profile(profile)


@router.put(
    "/users/{user_id}/learning-profile",
    response_model=AssistantLearningProfileRead,
    summary="Aktualizacja profilu uczenia asystenta użytkownika (admin)",
)
async def update_learning_profile(
    payload: AssistantLearningProfileUpdate,
    user_id: int = Path(..., ge=1),
    admin_context=Depends(get_admin_session_context),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> AssistantLearningProfileRead:
    """Aktualizuje pamięć uczenia asystenta dla wskazanego użytkownika (admin-only)."""
    _, admin_user = admin_context
    _assert_admin(admin_user)
    target_user = (
        await session.execute(select(AdminUser).where(AdminUser.id == user_id))
    ).scalar_one_or_none()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Użytkownik nie istnieje."
        )

    profile = (
        await session.execute(
            select(AssistantUserProfile).where(AssistantUserProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        profile = AssistantUserProfile(
            user_id=user_id,
            personalization_enabled=True,
            preferences={},
            memory_notes=None,
            updated_at=datetime.now(UTC),
        )
        session.add(profile)

    if payload.personalization_enabled is not None:
        profile.personalization_enabled = payload.personalization_enabled
    if payload.preferences is not None:
        profile.preferences = payload.preferences
    if payload.memory_notes is not None:
        profile.memory_notes = payload.memory_notes
    profile.updated_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(profile)
    return _map_learning_profile(profile)


__all__ = ["router"]
