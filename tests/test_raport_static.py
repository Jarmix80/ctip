from starlette.testclient import TestClient

from app.main import create_app


def test_raport_index_served_without_authentication():
    app = create_app()
    client = TestClient(app)

    response = client.get("/raport")
    assert response.status_code == 200
    assert "Podsumowanie miesięczne CPC" in response.text


def test_raport_csv_files_are_available_for_reading():
    app = create_app()
    client = TestClient(app)

    response = client.get("/raport/cpc_monthly_summary_2024.csv")
    assert response.status_code == 200
    assert response.text.splitlines()[0].startswith("Rok;Miesiąc")
