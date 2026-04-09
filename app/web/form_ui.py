"""Publiczny interfejs bezpiecznego formularza klienta."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.schemas.form_generator import PublicFormSubmission
from app.services import form_generator

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["form-ui"])


REPRESENTATIVE_FIELDS = (
    "first_name",
    "last_name",
    "representative_email",
    "representative_phone",
    "pesel",
    "birth_date",
    "document_type",
    "document_number",
    "document_issue_date",
    "document_expiry_date",
)


def _empty_representative() -> dict[str, str]:
    return {field: "" for field in REPRESENTATIVE_FIELDS}


def _empty_values() -> dict[str, Any]:
    return {
        "company_name": "",
        "company_nip": "",
        "company_phone": "",
        "company_email": "",
        "billing_email": "",
        "registered_street": "",
        "registered_building_no": "",
        "registered_apartment_no": "",
        "registered_postal_code": "",
        "registered_city": "",
        "correspondence_same_as_registered": False,
        "correspondence_street": "",
        "correspondence_building_no": "",
        "correspondence_apartment_no": "",
        "correspondence_postal_code": "",
        "correspondence_city": "",
        "representatives": [_empty_representative()],
        "consent": False,
        "website": "",
    }


def _parse_representatives(raw_value: str | None) -> list[dict[str, str]]:
    if not raw_value:
        return []
    try:
        loaded = json.loads(raw_value)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []

    parsed: list[dict[str, str]] = []
    for candidate in loaded:
        if not isinstance(candidate, Mapping):
            continue
        parsed.append(
            {field: str(candidate.get(field, "") or "").strip() for field in REPRESENTATIVE_FIELDS}
        )
    return parsed


def _payload_from_form(raw: Mapping[str, str]) -> dict[str, Any]:
    return {
        "company_name": raw.get("company_name", ""),
        "company_nip": raw.get("company_nip", ""),
        "company_phone": raw.get("company_phone", ""),
        "company_email": raw.get("company_email", ""),
        "billing_email": raw.get("billing_email", ""),
        "registered_street": raw.get("registered_street", ""),
        "registered_building_no": raw.get("registered_building_no", ""),
        "registered_apartment_no": raw.get("registered_apartment_no", ""),
        "registered_postal_code": raw.get("registered_postal_code", ""),
        "registered_city": raw.get("registered_city", ""),
        "correspondence_same_as_registered": raw.get("correspondence_same_as_registered", ""),
        "correspondence_street": raw.get("correspondence_street", ""),
        "correspondence_building_no": raw.get("correspondence_building_no", ""),
        "correspondence_apartment_no": raw.get("correspondence_apartment_no", ""),
        "correspondence_postal_code": raw.get("correspondence_postal_code", ""),
        "correspondence_city": raw.get("correspondence_city", ""),
        "representatives": _parse_representatives(raw.get("representatives_json")),
        "consent": raw.get("consent", ""),
        "website": raw.get("website", ""),
    }


def _validation_errors(exc: ValidationError) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for error in exc.errors():
        location = tuple(error.get("loc", ()))
        message = error.get("msg", "Nieprawidłowa wartość.")
        if not location:
            mapped.setdefault("general", message)
            continue

        key = str(location[0])
        if key == "representatives":
            if len(location) >= 3:
                idx = location[1]
                field = location[2]
                if isinstance(idx, int):
                    key = f"representatives.{idx}.{field}"
                else:
                    key = "representatives"
            else:
                key = "representatives"

        if key in mapped:
            continue
        mapped[key] = message
    return mapped


def _initial_values_from_request(item) -> dict[str, Any]:
    values = _empty_values()
    values["company_name"] = item.customer_name or ""
    values["company_email"] = item.customer_email or ""
    values["company_phone"] = item.customer_phone or ""
    return values


@router.get("/formularz/{token}", response_class=HTMLResponse)
async def public_form_page(
    token: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Wyświetla publiczny formularz na podstawie jednorazowego linku."""
    item = await form_generator.get_form_by_token(session, token)
    if item is None:
        return templates.TemplateResponse(
            "public/form_invalid.html",
            {"request": request},
            status_code=404,
        )
    if item.status == "EXPIRED":
        await session.commit()
        return templates.TemplateResponse(
            "public/form_invalid.html",
            {"request": request},
            status_code=410,
        )
    if item.status == "SUBMITTED":
        await session.commit()
        return templates.TemplateResponse(
            "public/form_success.html",
            {"request": request, "already_submitted": True},
        )

    values = _initial_values_from_request(item)
    await session.commit()
    return templates.TemplateResponse(
        "public/form_fill.html",
        {
            "request": request,
            "values": values,
            "errors": {},
        },
    )


@router.post("/formularz/{token}", response_class=HTMLResponse)
async def public_form_submit(
    token: str,
    request: Request,
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> HTMLResponse:
    """Przyjmuje dane klienta i zapisuje je jako zaszyfrowany payload."""
    item = await form_generator.get_form_by_token(session, token)
    if item is None:
        return templates.TemplateResponse(
            "public/form_invalid.html",
            {"request": request},
            status_code=404,
        )
    if item.status == "EXPIRED":
        await session.commit()
        return templates.TemplateResponse(
            "public/form_invalid.html",
            {"request": request},
            status_code=410,
        )
    if item.status == "SUBMITTED":
        await session.commit()
        return templates.TemplateResponse(
            "public/form_success.html",
            {"request": request, "already_submitted": True},
        )

    raw_form = await request.form()
    payload = _payload_from_form({key: str(value) for key, value in raw_form.items()})

    try:
        validated = PublicFormSubmission.model_validate(payload)
    except ValidationError as exc:
        return templates.TemplateResponse(
            "public/form_fill.html",
            {
                "request": request,
                "values": payload,
                "errors": _validation_errors(exc),
            },
            status_code=422,
        )

    try:
        await form_generator.submit_form_payload(
            session,
            form=item,
            payload=validated.model_dump(exclude={"website"}),
            client_ip=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
        await session.commit()
    except RuntimeError as exc:
        await session.rollback()
        return templates.TemplateResponse(
            "public/form_fill.html",
            {
                "request": request,
                "values": payload,
                "errors": {"general": str(exc)},
            },
            status_code=503,
        )
    except ValueError as exc:
        await session.commit()
        if str(exc) == "ALREADY_SUBMITTED":
            return templates.TemplateResponse(
                "public/form_success.html",
                {"request": request, "already_submitted": True},
            )
        return templates.TemplateResponse(
            "public/form_invalid.html",
            {"request": request},
            status_code=410,
        )

    return templates.TemplateResponse(
        "public/form_success.html",
        {"request": request, "already_submitted": False},
    )


__all__ = ["router"]
