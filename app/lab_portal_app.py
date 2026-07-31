"""Izolowana brama formularzy, scenariuszy i lokalnego CHAT_KP."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import AsyncIterator
from urllib.parse import urljoin

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.api.routes.crm import lab_router, www_router
from app.core.config import settings
from app.web.crm_ui import router as crm_ui_router

_LAB_COOKIE = "ctip_lab_portal"
_LAB_SESSION_SECONDS = 8 * 60 * 60
_MAX_TICKET_AGE_SECONDS = 10 * 60
_PROXY_REQUEST_HEADERS = {"accept", "authorization", "content-type"}
_PROXY_RESPONSE_HEADERS = {"content-type", "content-language"}

templates = Jinja2Templates(directory="app/templates")


def _safe_lab_environment() -> bool:
    """Sprawdza, czy brama działa wyłącznie na zasobach testowych."""
    return bool(
        settings.crm_enabled
        and settings.crm_lab_mode
        and settings.crm_public_prototype_mode
        and settings.pg_database == "ctip_test"
        and settings.sms_test_mode
        and settings.block_client_communications
        and settings.is_safe_test_firebird
    )


def _sign(value: str, secret: str) -> str:
    """Podpisuje wartość sekretem współdzielonym z wtyczką WordPress."""
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def _decode_ticket(ticket: str, secret: str) -> bool:
    """Weryfikuje krótko ważny bilet iframe bez zapisywania jego wartości."""
    try:
        encoded, signature = ticket.split(".", 1)
        if not hmac.compare_digest(_sign(encoded, secret), signature):
            return False
        padding = "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
        if not isinstance(payload, dict):
            return False
        expires_at = int(payload["exp"])
    except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    now = int(time.time())
    return bool(
        payload.get("aud") == "kp-ctip-lab" and now < expires_at <= now + _MAX_TICKET_AGE_SECONDS
    )


def _new_session_cookie(secret: str) -> str:
    """Tworzy podpisaną sesję bramy po jednorazowym sprawdzeniu biletu."""
    payload = f"{int(time.time()) + _LAB_SESSION_SECONDS}.{secrets.token_hex(16)}"
    return f"{payload}.{_sign(payload, secret)}"


def _valid_session_cookie(value: str | None, secret: str) -> bool:
    """Sprawdza podpis i ważność sesji bramy LAB."""
    try:
        expires_at, nonce, signature = str(value or "").split(".", 2)
        payload = f"{expires_at}.{nonce}"
        return bool(
            int(expires_at) > int(time.time())
            and len(nonce) >= 16
            and hmac.compare_digest(_sign(payload, secret), signature)
        )
    except (TypeError, ValueError):
        return False


async def _close_proxy(
    upstream: httpx.Response,
    client: httpx.AsyncClient,
) -> None:
    """Zamyka odpowiedź strumieniową i klienta HTTP po zakończeniu proxy."""
    await upstream.aclose()
    await client.aclose()


async def _upstream_bytes(upstream: httpx.Response) -> AsyncIterator[bytes]:
    """Przekazuje odkodowane fragmenty odpowiedzi CHAT_KP."""
    async for chunk in upstream.aiter_bytes():
        yield chunk


async def _proxy_chat(request: Request, upstream_path: str) -> Response:
    """Pośredniczy wyłącznie w dozwolonych trasach lokalnego CHAT_KP."""
    base_url = settings.crm_lab_chat_base_url.rstrip("/") + "/"
    target_url = urljoin(base_url, upstream_path.lstrip("/"))
    if request.url.query:
        target_url = f"{target_url}?{request.url.query}"
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in _PROXY_REQUEST_HEADERS
    }
    body = await request.body()
    client = httpx.AsyncClient(
        follow_redirects=False,
        timeout=max(5.0, settings.crm_lab_proxy_timeout_seconds),
    )
    try:
        upstream = await client.send(
            client.build_request(
                request.method,
                target_url,
                headers=headers,
                content=body,
            ),
            stream=True,
        )
    except httpx.HTTPError:
        await client.aclose()
        return JSONResponse(
            status_code=502,
            content={"detail": "Lokalny CHAT_KP jest chwilowo niedostępny."},
        )
    response_headers = {
        name: value
        for name, value in upstream.headers.items()
        if name.lower() in _PROXY_RESPONSE_HEADERS
    }
    return StreamingResponse(
        _upstream_bytes(upstream),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(_close_proxy, upstream, client),
    )


def create_lab_portal_app() -> FastAPI:
    """Tworzy minimalną aplikację przeznaczoną wyłącznie dla strony LAB."""
    application = FastAPI(
        title="CTIP – laboratorium kanałów",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.middleware("http")
    async def secure_lab(request: Request, call_next):
        """Egzekwuje konfigurację testową, dostęp LAN lub podpisany bilet iframe."""
        if not _safe_lab_environment():
            return JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "LAB wymaga ctip_test, testowego Firebird bez zapisu, "
                        "trybu SMS test i jawnego włączenia CRM LAB."
                    )
                },
                headers={"Cache-Control": "no-store"},
            )

        if request.url.path == "/health":
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            return response

        secret = settings.crm_lab_iframe_secret or ""
        set_session = False
        if secret:
            has_session = _valid_session_cookie(
                request.cookies.get(_LAB_COOKIE),
                secret,
            )
            ticket = request.query_params.get("ticket", "")
            has_ticket = bool(ticket and _decode_ticket(ticket, secret))
            if not has_session and not has_ticket:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Brak ważnej sesji laboratorium."},
                    headers={"Cache-Control": "no-store"},
                )
            set_session = has_ticket and not has_session
        else:
            client_host = request.client.host if request.client else None
            if not settings.is_panel_client_allowed(client_host):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "LAB jest dostępny wyłącznie z zaufanej sieci."},
                    headers={"Cache-Control": "no-store"},
                )

        response = await call_next(request)
        if set_session:
            forwarded_scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
            response.set_cookie(
                _LAB_COOKIE,
                _new_session_cookie(secret),
                max_age=_LAB_SESSION_SECONDS,
                httponly=True,
                secure=forwarded_scheme == "https",
                samesite="lax",
                path="/",
            )
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "form-action 'self'; "
            f"frame-ancestors {settings.crm_frame_ancestors}"
        )
        return response

    @application.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        """Przekierowuje do formularzy laboratoryjnych."""
        return RedirectResponse(url="/forms")

    @application.get("/health", include_in_schema=False)
    async def health() -> dict[str, str | bool]:
        """Zwraca bezpieczny stan bramy bez danych konfiguracyjnych i sekretów."""
        return {
            "status": "ok",
            "service": "ctip-lab-portal",
            "safe_lab": _safe_lab_environment(),
            "chat_proxy": "configured",
        }

    @application.get("/chat", include_in_schema=False)
    async def chat() -> RedirectResponse:
        """Otwiera widget CHAT_KP przez proxy tej samej domeny."""
        return RedirectResponse(url="/widget/v1/widget.html")

    @application.get("/forms", response_class=HTMLResponse, include_in_schema=False)
    async def forms(request: Request) -> HTMLResponse:
        """Renderuje warianty formularzy zastępujących obecne integracje Bitrix."""
        return templates.TemplateResponse(
            request=request,
            name="lab/forms.html",
            context={},
        )

    @application.get("/scenarios", response_class=HTMLResponse, include_in_schema=False)
    async def scenarios(request: Request) -> HTMLResponse:
        """Renderuje generator kontrolowanych spraw testowych."""
        return templates.TemplateResponse(
            request=request,
            name="lab/scenarios.html",
            context={},
        )

    @application.api_route(
        "/widget/v1/{path:path}",
        methods=["GET"],
        include_in_schema=False,
    )
    async def proxy_widget(request: Request, path: str) -> Response:
        """Przekazuje statyczny widget CHAT_KP."""
        return await _proxy_chat(request, f"/widget/v1/{path}")

    @application.api_route(
        "/api/v1/{path:path}",
        methods=["GET", "POST", "OPTIONS"],
        include_in_schema=False,
    )
    async def proxy_chat_api(request: Request, path: str) -> Response:
        """Przekazuje wyłącznie publiczne API rozmowy CHAT_KP."""
        return await _proxy_chat(request, f"/api/v1/{path}")

    @application.get("/privacy-notice", include_in_schema=False)
    async def proxy_privacy_notice(request: Request) -> Response:
        """Przekazuje informację o prywatności z CHAT_KP."""
        return await _proxy_chat(request, "/privacy-notice")

    application.mount("/static", StaticFiles(directory="app/static"), name="static")
    application.include_router(lab_router)
    application.include_router(www_router)
    application.include_router(crm_ui_router)
    return application


app = create_lab_portal_app()


__all__ = ["app", "create_lab_portal_app"]
