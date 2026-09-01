from starlette.testclient import TestClient

from app.main import create_app


def _report_directory(tmp_path):
    """Tworzy minimalny raport statyczny niezależny od produkcyjnego junctiona."""
    (tmp_path / "index.html").write_text(
        "<html><body>Podsumowanie miesięczne CPC</body></html>",
        encoding="utf-8",
    )
    (tmp_path / "cpc_monthly_summary_2024.csv").write_text(
        "Rok;Miesiąc;Wartość\n2024;01;1\n",
        encoding="utf-8",
    )
    return tmp_path


def test_raport_index_served_without_authentication(tmp_path):
    app = create_app(report_directory=_report_directory(tmp_path))
    client = TestClient(app)

    response = client.get("/raport")
    assert response.status_code == 200
    assert "Podsumowanie miesięczne CPC" in response.text


def test_raport_csv_files_are_available_for_reading(tmp_path):
    app = create_app(report_directory=_report_directory(tmp_path))
    client = TestClient(app)

    response = client.get("/raport/cpc_monthly_summary_2024.csv")
    assert response.status_code == 200
    assert response.text.splitlines()[0].startswith("Rok;Miesiąc")
