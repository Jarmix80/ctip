# ruff: noqa: E402

"""Testy galerii niefunkcjonalnych prototypów KP Orbit."""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.testclient import TestClient

from app.core.config import settings
from app.web.orbit_ui import router as orbit_ui_router

EXPECTED_MODULE_PATHS = (
    "/choice",
    "/operator",
    "/genform",
    "/flow",
    "/device",
    "/contracts",
    "/shipping",
    "/assistant",
    "/admin",
)
EXPECTED_MODULE_GROUPS = ("start", "customer", "process", "logistics", "system")


def _build_client() -> TestClient:
    """Buduje izolowaną aplikację testową z routerem i zasobami statycznymi."""
    app = FastAPI()
    app.include_router(orbit_ui_router)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    return TestClient(app)


def test_orbit_prototypes_show_five_complete_noninteractive_variants() -> None:
    """Galeria zawiera pięć makiet i nie uruchamia funkcji modułów."""
    previous_profile = settings.ctip_runtime_profile
    try:
        settings.ctip_runtime_profile = "test"
        client = _build_client()

        response = client.get("/orbit/prototypes")

        assert response.status_code == 200
        assert response.text.count('data-prototype="') == 5
        assert "Klasyczny Orbit" in response.text
        assert "Centrum Operacyjne" in response.text
        assert "Proces 360" in response.text
        assert "Kafelki Modułowe" in response.text
        assert "Minimal Focus" in response.text
        assert "Makiety niefunkcjonalne" in response.text
        assert "Brak połączeń z PostgreSQL, Firebird" in response.text
        for module_path in EXPECTED_MODULE_PATHS:
            assert response.text.count(f'data-route="{module_path}"') == 5
            assert f'href="{module_path}"' not in response.text
        assert "<script" not in response.text
        assert "fetch(" not in response.text
        assert "<form" not in response.text
        assert response.text.count("<button") == response.text.count(" disabled")
    finally:
        settings.ctip_runtime_profile = previous_profile


def test_orbit_prototypes_are_unavailable_outside_test_runtime() -> None:
    """Profil produkcyjny nie udostępnia testowej galerii."""
    previous_profile = settings.ctip_runtime_profile
    try:
        settings.ctip_runtime_profile = "production"
        client = _build_client()

        response = client.get("/orbit/prototypes")

        assert response.status_code == 404
    finally:
        settings.ctip_runtime_profile = previous_profile


def test_orbit_simple_prototypes_show_scalable_choice_and_login_variants() -> None:
    """Galeria prezentuje pięć konsol bocznych i wybrane logowanie dzielone."""
    previous_profile = settings.ctip_runtime_profile
    try:
        settings.ctip_runtime_profile = "test"
        client = _build_client()

        response = client.get("/orbit/simple-prototypes")

        assert response.status_code == 200
        assert response.text.count('data-choice-prototype="') == 5
        assert response.text.count('data-login-prototype="') == 1
        assert "Pulpit dnia" in response.text
        assert "Kolejki operacyjne" in response.text
        assert "Oś dnia" in response.text
        assert "Radar kierownika" in response.text
        assert "Monitor kompaktowy" in response.text
        assert "Ekran dzielony" in response.text
        assert "Konsola boczna z informacją operacyjną" in response.text
        assert response.text.count('class="choice-date-time"') == 5
        assert response.text.count('datetime="2026-08-31T11:54:00+02:00"') == 5
        assert response.text.count('data-domain="forms"') == 5
        assert response.text.count('data-domain="shipping"') == 5
        assert response.text.count('data-domain="devices"') == 5
        assert 'data-login-prototype="split-screen-selected"' in response.text
        assert "login-orbit-hero" in response.text
        assert "login-wordmark" not in response.text
        for module_path in EXPECTED_MODULE_PATHS:
            assert response.text.count(f'data-route="{module_path}"') == 5
            assert f'href="{module_path}"' not in response.text
        for group_key in EXPECTED_MODULE_GROUPS:
            assert response.text.count(f'data-group="{group_key}"') == 5
        assert "<script" not in response.text
        assert "fetch(" not in response.text
        assert "<form" not in response.text
        assert response.text.count("<button") == response.text.count(" disabled")
    finally:
        settings.ctip_runtime_profile = previous_profile


def test_orbit_simple_prototypes_are_unavailable_outside_test_runtime() -> None:
    """Profil produkcyjny ukrywa również galerię prostego rebrandingu."""
    previous_profile = settings.ctip_runtime_profile
    try:
        settings.ctip_runtime_profile = "production"
        client = _build_client()

        response = client.get("/orbit/simple-prototypes")

        assert response.status_code == 404
    finally:
        settings.ctip_runtime_profile = previous_profile


def test_orbit_router_is_registered_in_main_application() -> None:
    """Główna aplikacja udostępnia galerię i oznacza odpowiedź jako niebuforowaną."""
    from app.main import create_app

    previous_profile = settings.ctip_runtime_profile
    try:
        settings.ctip_runtime_profile = "test"
        client = TestClient(create_app())

        response = client.get("/orbit/prototypes")
        simple_response = client.get("/orbit/simple-prototypes")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert simple_response.status_code == 200
        assert simple_response.headers["cache-control"] == "no-store"
    finally:
        settings.ctip_runtime_profile = previous_profile


def test_orbit_prototype_assets_are_local_and_responsive() -> None:
    """Arkusz stylów i pliki marki są serwowane lokalnie."""
    previous_profile = settings.ctip_runtime_profile
    try:
        settings.ctip_runtime_profile = "test"
        client = _build_client()

        page_response = client.get("/orbit/prototypes")
        css_response = client.get("/static/orbit/prototypes.css")
        simple_css_response = client.get("/static/orbit/simple_prototypes.css")
        logo_response = client.get("/static/orbit/brand/kp-orbit-horizontal-color.png")
        icon_response = client.get("/static/orbit/brand/kp-orbit-icon-32.png")

        assert page_response.status_code == 200
        assert 'src="http' not in page_response.text
        assert 'href="http' not in page_response.text
        assert css_response.status_code == 200
        assert "@media (max-width: 860px)" in css_response.text
        assert "@media (max-width: 560px)" in css_response.text
        assert "@media (prefers-reduced-motion: reduce)" in css_response.text
        assert simple_css_response.status_code == 200
        assert "grid-template-columns: repeat(auto-fit" in simple_css_response.text
        assert "@keyframes orbit-ring-blue" in simple_css_response.text
        assert "@keyframes orbit-ring-red" in simple_css_response.text
        assert ".login-orbit-hero" in simple_css_response.text
        assert ".operation-queue-grid" in simple_css_response.text
        assert ".compact-table" in simple_css_response.text
        assert "@media (max-width: 620px)" in simple_css_response.text
        assert "@media (prefers-reduced-motion: reduce)" in simple_css_response.text
        assert logo_response.status_code == 200
        assert logo_response.headers["content-type"] == "image/png"
        assert icon_response.status_code == 200
        assert icon_response.headers["content-type"] == "image/png"
    finally:
        settings.ctip_runtime_profile = previous_profile
