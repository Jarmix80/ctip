"""Testy widoku webowego raportu MM."""

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.web.mm_ui import router as mm_ui_router


def test_mm_page_renders_template() -> None:
    app = FastAPI()
    app.include_router(mm_ui_router)
    client = TestClient(app)

    response = client.get("/mm")

    assert response.status_code == 200
    assert "Przesuniecia miedzymagazynowe" in response.text
    assert 'id="mm-filter-form"' in response.text
