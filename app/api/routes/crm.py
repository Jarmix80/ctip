"""API spraw Centrum Obsługi dla operatorów, LAB i zaufanych kanałów."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, get_operator_user
from app.core.config import settings
from app.models import AdminUser
from app.schemas.crm import (
    ChatCaseCreateRequest,
    ChatCaseResponse,
    ChatCustomerResolveRequest,
    ChatCustomerResolveResponse,
    ChatMaskedDevicesRequest,
    ChatMaskedDevicesResponse,
    ChatSmsChallengeCreateRequest,
    ChatSmsChallengeResponse,
    ChatSmsChallengeVerifyRequest,
    ChatSmsChallengeVerifyResponse,
    CrmCapabilitiesResponse,
    CrmCaseActionRequest,
    CrmCaseCreateRequest,
    CrmCaseCreateResponse,
    CrmCaseListResponse,
    CrmCaseResponse,
    CrmLabResetRequest,
    CrmLabResetResponse,
    CrmOperatorResponse,
)
from app.services.bot_identity_directory import (
    authenticate_service_token,
    create_chat_sms_challenge,
    list_chat_masked_devices,
    resolve_chat_customer,
    verify_chat_sms_challenge,
)
from app.services.bot_identity_images import (
    ModelImageNotFoundError,
    ModelImageTooLargeError,
    load_model_image,
)
from app.services.crm_cases import (
    apply_case_action,
    create_case,
    create_chat_case,
    get_case,
    list_active_operators,
    list_cases,
    reset_lab_cases,
    serialize_case,
    serialize_chat_case,
)

operator_router = APIRouter(prefix="/api/crm/v1", tags=["crm"])
lab_router = APIRouter(prefix="/api/crm/v1", tags=["crm-lab"])
service_router = APIRouter(prefix="/v1", tags=["crm-service"])


def _require_crm_enabled() -> None:
    if not settings.crm_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Centrum Obsługi jest wyłączone.",
        )


def _require_safe_lab() -> None:
    unsafe = (
        not settings.crm_enabled
        or not settings.crm_lab_mode
        or not settings.crm_public_prototype_mode
        or settings.pg_database != "ctip_test"
        or not settings.sms_test_mode
        or not settings.is_safe_test_firebird
    )
    if unsafe:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "LAB wymaga lokalnej bazy ctip_test, testowego Firebird bez zapisu "
                "i trybu SMS test."
            ),
        )


def _service_channel(authorization: str | None = Header(default=None)) -> str:
    _require_crm_enabled()
    try:
        return authenticate_service_token(authorization)
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nieprawidłowe uwierzytelnienie usługi.",
        ) from exc


def _raise_bad_request(exc: ValueError) -> None:
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _operators(session: AsyncSession) -> list[CrmOperatorResponse]:
    return await list_active_operators(session)


async def _cases(
    session: AsyncSession,
    *,
    lab_only: bool | None,
    include_archived: bool,
    limit: int,
) -> CrmCaseListResponse:
    items = await list_cases(
        session,
        include_archived=include_archived,
        lab_only=lab_only,
        limit=limit,
    )
    await session.commit()
    return CrmCaseListResponse(
        items=[serialize_case(item) for item in items],
        total=len(items),
    )


async def _manual_create(
    session: AsyncSession,
    payload: CrmCaseCreateRequest,
    *,
    force_lab: bool,
) -> CrmCaseCreateResponse:
    if payload.channel not in {"manual", "form", "configurator", "scenario"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Panel może tworzyć tylko sprawy ręczne, formularzowe i scenariusze.",
        )
    operator_id = payload.metadata.get("declared_operator_id")
    if not isinstance(operator_id, int):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Wymagany jest metadata.declared_operator_id.",
        )
    try:
        from app.services.crm_cases import get_active_operator

        operator = await get_active_operator(session, operator_id)
        item, created = await create_case(
            session,
            payload,
            idempotency_key=None,
            service_channel=None,
            declared_operator=operator,
            force_lab=force_lab,
        )
    except ValueError as exc:
        _raise_bad_request(exc)
    await session.commit()
    return CrmCaseCreateResponse(case=serialize_case(item), created=created)


async def _case_action(
    session: AsyncSession,
    case_ref: str,
    payload: CrmCaseActionRequest,
    *,
    lab_only: bool,
) -> CrmCaseResponse:
    item = await get_case(session, case_ref)
    if item is None or (lab_only and not item.is_lab):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nie znaleziono sprawy.")
    try:
        await apply_case_action(session, item, payload)
    except ValueError as exc:
        _raise_bad_request(exc)
    await session.commit()
    refreshed = await get_case(session, case_ref)
    if refreshed is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono sprawy.")
    return serialize_case(refreshed)


@operator_router.get("/operators", response_model=list[CrmOperatorResponse])
async def get_crm_operators(
    _: AdminUser = Depends(get_operator_user),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[CrmOperatorResponse]:
    _require_crm_enabled()
    return await _operators(session)


@operator_router.get("/cases", response_model=CrmCaseListResponse)
async def get_crm_cases(
    include_archived: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=1000),
    _: AdminUser = Depends(get_operator_user),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CrmCaseListResponse:
    _require_crm_enabled()
    return await _cases(
        session,
        lab_only=True if settings.crm_lab_mode else None,
        include_archived=include_archived,
        limit=limit,
    )


@operator_router.post("/cases", response_model=CrmCaseCreateResponse)
async def post_crm_case(
    payload: CrmCaseCreateRequest,
    _: AdminUser = Depends(get_operator_user),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CrmCaseCreateResponse:
    _require_crm_enabled()
    return await _manual_create(session, payload, force_lab=settings.crm_lab_mode)


@operator_router.get("/cases/{case_ref}", response_model=CrmCaseResponse)
async def get_crm_case(
    case_ref: str,
    _: AdminUser = Depends(get_operator_user),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CrmCaseResponse:
    _require_crm_enabled()
    item = await get_case(session, case_ref)
    if item is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono sprawy.")
    return serialize_case(item)


@operator_router.post("/cases/{case_ref}/actions", response_model=CrmCaseResponse)
async def post_crm_case_action(
    case_ref: str,
    payload: CrmCaseActionRequest,
    _: AdminUser = Depends(get_operator_user),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CrmCaseResponse:
    _require_crm_enabled()
    return await _case_action(session, case_ref, payload, lab_only=False)


@operator_router.post("/lab/reset", response_model=CrmLabResetResponse)
async def post_operator_lab_reset(
    payload: CrmLabResetRequest,
    user: AdminUser = Depends(get_operator_user),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CrmLabResetResponse:
    _require_crm_enabled()
    if not settings.crm_lab_mode or user.role != "admin":
        raise HTTPException(status_code=403, detail="Reset LAB wymaga administratora w trybie LAB.")
    deleted_cases, deleted_events = await reset_lab_cases(
        session,
        declared_operator_id=payload.declared_operator_id,
        reason=payload.reason,
    )
    await session.commit()
    return CrmLabResetResponse(
        deleted_cases=deleted_cases,
        deleted_events=deleted_events,
    )


@lab_router.get(
    "/operators",
    response_model=list[CrmOperatorResponse],
    dependencies=[Depends(_require_safe_lab)],
)
async def get_lab_operators(
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> list[CrmOperatorResponse]:
    return await _operators(session)


@lab_router.get(
    "/cases",
    response_model=CrmCaseListResponse,
    dependencies=[Depends(_require_safe_lab)],
)
async def get_lab_cases(
    include_archived: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=1000),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CrmCaseListResponse:
    return await _cases(
        session,
        lab_only=True,
        include_archived=include_archived,
        limit=limit,
    )


@lab_router.post(
    "/cases",
    response_model=CrmCaseCreateResponse,
    dependencies=[Depends(_require_safe_lab)],
)
async def post_lab_case(
    payload: CrmCaseCreateRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CrmCaseCreateResponse:
    return await _manual_create(session, payload, force_lab=True)


@lab_router.post(
    "/intake",
    response_model=CrmCaseCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_require_safe_lab)],
)
async def post_lab_intake(
    payload: CrmCaseCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CrmCaseCreateResponse:
    """Przyjmuje formularze i scenariusze wyłącznie do izolowanego CRM LAB."""
    try:
        item, created = await create_case(
            session,
            payload,
            idempotency_key=idempotency_key,
            service_channel=None,
            declared_operator=None,
            force_lab=True,
        )
    except ValueError as exc:
        _raise_bad_request(exc)
    await session.commit()
    return CrmCaseCreateResponse(case=serialize_case(item), created=created)


@lab_router.get(
    "/cases/{case_ref}",
    response_model=CrmCaseResponse,
    dependencies=[Depends(_require_safe_lab)],
)
async def get_lab_case(
    case_ref: str,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CrmCaseResponse:
    item = await get_case(session, case_ref)
    if item is None or not item.is_lab:
        raise HTTPException(status_code=404, detail="Nie znaleziono sprawy LAB.")
    return serialize_case(item)


@lab_router.post(
    "/cases/{case_ref}/actions",
    response_model=CrmCaseResponse,
    dependencies=[Depends(_require_safe_lab)],
)
async def post_lab_case_action(
    case_ref: str,
    payload: CrmCaseActionRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CrmCaseResponse:
    return await _case_action(session, case_ref, payload, lab_only=True)


@lab_router.post(
    "/lab/reset",
    response_model=CrmLabResetResponse,
    dependencies=[Depends(_require_safe_lab)],
)
async def post_lab_reset(
    payload: CrmLabResetRequest,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> CrmLabResetResponse:
    deleted_cases, deleted_events = await reset_lab_cases(
        session,
        declared_operator_id=payload.declared_operator_id,
        reason=payload.reason,
    )
    await session.commit()
    return CrmLabResetResponse(
        deleted_cases=deleted_cases,
        deleted_events=deleted_events,
    )


@service_router.get("/capabilities", response_model=CrmCapabilitiesResponse)
async def get_crm_capabilities(
    _: str = Depends(_service_channel),  # noqa: B008
) -> CrmCapabilitiesResponse:
    return CrmCapabilitiesResponse()


@service_router.get("/device-model-images/{image_ref}", response_class=Response)
async def get_device_model_image(image_ref: str) -> Response:
    """Udostępnia wyłącznie kontrolowany plik obrazu po nieprzewidywalnej referencji."""
    try:
        image = load_model_image(image_ref)
    except ModelImageTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Plik obrazu przekracza dozwolony rozmiar.",
        ) from exc
    except ModelImageNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Nie znaleziono obrazu.") from exc
    return Response(
        content=image.content,
        media_type=image.content_type,
        headers={
            "Cache-Control": (
                f"public, max-age={max(0, settings.bot_identity_image_cache_seconds)}, immutable"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )


@service_router.post("/customers/resolve", response_model=ChatCustomerResolveResponse)
async def post_customer_resolve(
    payload: ChatCustomerResolveRequest,
    _: str = Depends(_service_channel),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ChatCustomerResolveResponse:
    try:
        result = await resolve_chat_customer(
            session,
            nip=payload.nip,
            name=payload.name,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        _raise_bad_request(ValueError(str(exc)))
    await session.commit()
    return result


@service_router.post(
    "/sms/challenges",
    response_model=ChatSmsChallengeResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_sms_challenge(
    payload: ChatSmsChallengeCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    channel: str = Depends(_service_channel),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ChatSmsChallengeResponse:
    if channel != "chat":
        raise HTTPException(status_code=403, detail="Wyzwanie SMS jest dostępne dla kanału chat.")
    try:
        result = await create_chat_sms_challenge(
            session,
            phone=payload.phone,
            customer_ref=payload.customer_ref,
            idempotency_key=idempotency_key,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        _raise_bad_request(exc)
    await session.commit()
    return result


@service_router.post(
    "/sms/challenges/{challenge_id}/verify",
    response_model=ChatSmsChallengeVerifyResponse,
)
async def post_sms_challenge_verify(
    challenge_id: str,
    payload: ChatSmsChallengeVerifyRequest,
    channel: str = Depends(_service_channel),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ChatSmsChallengeVerifyResponse:
    if channel != "chat":
        raise HTTPException(status_code=403, detail="Wyzwanie SMS jest dostępne dla kanału chat.")
    result = await verify_chat_sms_challenge(
        session,
        challenge_id=challenge_id,
        code=payload.code,
    )
    await session.commit()
    return result


@service_router.post(
    "/customers/{customer_ref}/devices/masked",
    response_model=ChatMaskedDevicesResponse,
)
async def post_masked_customer_devices(
    customer_ref: str,
    payload: ChatMaskedDevicesRequest,
    channel: str = Depends(_service_channel),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ChatMaskedDevicesResponse:
    if channel != "chat":
        raise HTTPException(status_code=403, detail="Urządzenia maskowane są dostępne dla chat.")
    try:
        result = await list_chat_masked_devices(
            session,
            customer_ref=customer_ref,
            challenge_id=payload.challenge_id,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await session.commit()
    return result


@service_router.post(
    "/cases",
    response_model=ChatCaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_service_case(
    payload: ChatCaseCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    channel: str = Depends(_service_channel),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ChatCaseResponse:
    try:
        item = await create_chat_case(
            session,
            payload,
            idempotency_key=idempotency_key,
            service_channel=channel,
        )
    except ValueError as exc:
        _raise_bad_request(exc)
    await session.commit()
    return serialize_chat_case(item)


@service_router.get("/cases/{case_id}", response_model=ChatCaseResponse)
async def get_service_case(
    case_id: str,
    _: str = Depends(_service_channel),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> ChatCaseResponse:
    item = await get_case(session, case_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Nie znaleziono sprawy.")
    return serialize_chat_case(item)


__all__ = ["lab_router", "operator_router", "service_router"]
