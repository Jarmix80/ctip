import asyncio
import json
from datetime import UTC, datetime, timedelta

from app.models import FormRequest, FormWorkflowCase, FormWorkflowDevice
from app.services import grenke_launch


def test_build_legacy_query_koduje_tylko_niezbedne_separatory():
    query = grenke_launch._build_legacy_query(  # noqa: SLF001
        {
            "p": "Ricoh MP 401 S/N: T605H900327, nr.wew: KP/4066 & A=B#1",
            "c": "2361.60",
            "k": "drukarka",
            "paramsUrl": "1",
        }
    )

    assert "Ricoh%20MP%20401%20S/N:%20T605H900327,%20nr.wew:%20KP/4066" in query
    assert "%26%20A%3DB%231" in query
    assert "+" not in query


def test_fallback_launch_url_zostawia_format_nazwy_zgodny_z_decodeuri():
    url = grenke_launch._fallback_launch_url(  # noqa: SLF001
        "cmwwgdcxivwuhmtr",
        [
            {
                "added": True,
                "name": "Ricoh MP 401 S/N: T605H900327, nr.wew: KP/4066",
                "price": "2 361,60",
            },
            {"added": False},
        ],
    )

    assert url == (
        "https://newonline.leasingoptymalny.pl/kalkulacja/cmwwgdcxivwuhmtr?"
        "p=Ricoh%20MP%20401%20S/N:%20T605H900327,%20nr.wew:%20KP/4066"
        "&c=2361.60&k=Drukarka%20IT&paramsUrl=1"
    )


def test_fallback_launch_url_bez_dodanych_pozycji_zwraca_czysty_link():
    assert grenke_launch._fallback_launch_url("abc123", [{"added": False}]) == (  # noqa: SLF001
        "https://newonline.leasingoptymalny.pl/kalkulacja/abc123"
    )


def test_resolve_provider_from_proforma_pobiera_sprzedawce(monkeypatch):
    def _fake_preview(_: int) -> dict:
        return {
            "seller": {
                "name": "Ksero Partner Sp. z o.o.",
                "postal_code": "62-052",
                "nip": "7792428408",
            }
        }

    monkeypatch.setattr(
        "app.services.contracts_proforma.load_proforma_preview_data",
        _fake_preview,
    )

    provider = grenke_launch._resolve_provider_from_proforma(70035)  # noqa: SLF001
    assert provider.enabled is True
    assert provider.name == "Ksero Partner Sp. z o.o."
    assert provider.postal_code == "62-052"
    assert provider.nip == "7792428408"


def test_resolve_provider_from_proforma_bez_sprzedawcy_zwraca_wylaczone(monkeypatch):
    monkeypatch.setattr(
        "app.services.contracts_proforma.load_proforma_preview_data",
        lambda _: {},
    )

    provider = grenke_launch._resolve_provider_from_proforma(70035)  # noqa: SLF001
    assert provider.enabled is False
    assert provider.name == ""
    assert provider.postal_code == ""
    assert provider.nip == ""


def test_extract_rate_options_i_final_rate_dla_wyniku_calculate():
    calculate_data = {
        "result": {
            "month": "1 234.50",
            "quater": "3 703.50",
        }
    }

    assert grenke_launch._extract_rate_options(calculate_data) == [  # noqa: SLF001
        "kwartalna",
        "miesieczna",
    ]
    assert (
        grenke_launch._extract_final_rate(  # noqa: SLF001
            calculate_data,
            rate_frequency="kwartalnie",
        )
        == "3 703,50 zł"
    )


def test_launch_grenke_prefill_mapuje_reprezentantow_do_kroku3(monkeypatch):
    captured_payload: dict[str, object] = {}

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def _fake_post_json(_client, url: str, payload: dict):
        if url.endswith("/setSession.php"):
            return True, {"ok": True}
        if url.endswith("/calculate.php"):
            return True, {"result": {"month": "1234.50", "quater": "3703.50"}}
        if url.endswith("/saveCalculation.php"):
            captured_payload["save_payload"] = payload
            return True, {"ok": True}
        return True, {}

    monkeypatch.setattr(grenke_launch.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(grenke_launch, "_post_json", _fake_post_json)
    monkeypatch.setattr(grenke_launch.secrets, "token_hex", lambda _size: "0ddba11f0ddba11f")
    monkeypatch.setattr(
        "app.services.form_generator.decode_submitted_payload",
        lambda _form: (
            {
                "registered_street": "Fabianowska",
                "registered_building_no": "165",
                "registered_postal_code": "62-052",
                "registered_city": "Komorniki",
                "representatives": [
                    {
                        "first_name": "Jan",
                        "last_name": "Kowalski",
                        "representative_email": "jan@example.com",
                        "representative_phone": "+48 500 111 222",
                        "pesel": "90010112345",
                        "document_type": "Dowód osobisty",
                        "document_number": "ABA123456",
                        "document_issue_date": "10-05-2021",
                        "document_expiry_date": "10-05-2031",
                    },
                    {
                        "first_name": "Anna",
                        "last_name": "Nowak",
                        "representative_email": "anna@example.com",
                        "representative_phone": "500222333",
                        "pesel": "85010154321",
                        "document_type": "Paszport",
                        "document_number": "PAS998877",
                        "document_issue_date": "2022-01-03",
                        "document_expiry_date": "2032-01-03",
                    },
                ],
            },
            None,
        ),
    )

    form = FormRequest(
        id=37,
        customer_name="Test Sp. z o.o.",
        customer_email="test@example.com",
        customer_phone="+48500100200",
        status="SUBMITTED",
        token_hash="token-hash-test",
        token_expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    workflow_case = FormWorkflowCase(
        id=104,
        form_request_id=37,
        stage="PROFORMA_CREATED",
        business_status="WAITING_SIGNATURE",
        proforma_firebird_id=None,
    )
    workflow_devices = [
        FormWorkflowDevice(
            workflow_case_id=104,
            source_type="firebird_magazyn_28",
            source_row=223,
            producer="Ricoh",
            model="MP C3004",
            serial="T605H900888",
            ewidencja="KP/7001",
            price_net="6000.00",
        )
    ]

    result = asyncio.run(
        grenke_launch.launch_grenke_prefill(
            form=form,
            workflow_case=workflow_case,
            workflow_devices=workflow_devices,
        )
    )

    assert result.prefill_state == "full"
    save_payload = captured_payload["save_payload"]
    assert isinstance(save_payload, dict)
    authorized_persons = save_payload["authorizedPersons"]
    assert isinstance(authorized_persons, list)
    assert len(authorized_persons) == 2
    first = authorized_persons[0]
    assert first["name"] == "Jan"
    assert first["surname"] == "Kowalski"
    assert first["pesel"] == "90010112345"
    assert first["document"] == "Dowód osobisty"
    assert first["id"] == "ABA123456"
    assert first["idReleaseDate"] == "2021-05-10"
    assert first["idExpirationDate"] == "2031-05-10"
    assert first["address"] == "Fabianowska 165 62-052 Komorniki"
    assert first["email"] == "jan@example.com"
    assert first["phone"] == "+48500111222"

    assert save_payload["authName"] == "Jan"
    assert save_payload["authSurname"] == "Kowalski"
    assert save_payload["authPesel"] == "90010112345"
    assert save_payload["authId"] == "ABA123456"
    assert save_payload["representatives"] == authorized_persons

    esign_persons = save_payload["esignPersons"]
    assert isinstance(esign_persons, list)
    assert esign_persons[1] == {
        "name": "Anna",
        "surname": "Nowak",
        "email": "anna@example.com",
        "phone": "500222333",
    }


def test_launch_grenke_prefill_wysyla_rate_jako_liste_opcji(monkeypatch):
    captured_payload: dict[str, object] = {}

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def _fake_post_json(_client, url: str, payload: dict):
        if url.endswith("/setSession.php"):
            return True, {"ok": True}
        if url.endswith("/calculate.php"):
            return True, {"result": {"month": "1234.50", "quater": "3703.50"}}
        if url.endswith("/saveCalculation.php"):
            captured_payload["save_payload"] = payload
            return True, {"ok": True}
        return True, {}

    monkeypatch.setattr(grenke_launch.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(grenke_launch, "_post_json", _fake_post_json)
    monkeypatch.setattr(grenke_launch.secrets, "token_hex", lambda _size: "ad077a4834f1a3e4")

    form = FormRequest(
        id=33,
        customer_name="Test Sp. z o.o.",
        customer_email="test@example.com",
        customer_phone="+48500100200",
        status="SUBMITTED",
        token_hash="token-hash-test",
        token_expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    workflow_case = FormWorkflowCase(
        id=101,
        form_request_id=33,
        stage="PROFORMA_CREATED",
        business_status="WAITING_SIGNATURE",
        proforma_firebird_id=None,
    )
    workflow_devices = [
        FormWorkflowDevice(
            workflow_case_id=101,
            source_type="firebird_magazyn_28",
            source_row=220,
            producer="Ricoh",
            model="MP 401",
            serial="T605H900327",
            ewidencja="KP/4066",
            price_net="2361.60",
        )
    ]

    result = asyncio.run(
        grenke_launch.launch_grenke_prefill(
            form=form,
            workflow_case=workflow_case,
            workflow_devices=workflow_devices,
        )
    )

    assert result.prefill_state == "full"
    assert result.url == "https://newonline.leasingoptymalny.pl/kalkulacja/ad077a4834f1a3e4"
    save_payload = captured_payload["save_payload"]
    assert isinstance(save_payload, dict)
    assert json.loads(save_payload["rate"]) == ["kwartalna", "miesieczna"]
    assert save_payload["finalRate"] == "3 703,50 zł"
    assert save_payload["initialCharge"] == "0%"
    assert save_payload["paramsUrl"] == 1
    assert save_payload["leasingTime"] == "60"


def test_launch_grenke_prefill_wybiera_najwiekszy_poprawny_okres(monkeypatch):
    captured_payload: dict[str, object] = {}
    tried_months: list[str] = []

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def _fake_post_json(_client, url: str, payload: dict):
        if url.endswith("/setSession.php"):
            return True, {"ok": True}
        if url.endswith("/calculate.php"):
            month = str(payload.get("months"))
            tried_months.append(month)
            if month == "60":
                return True, {"result": {"month": "--", "quater": "--"}}
            if month == "48":
                return True, {"result": {"month": "1500.00", "quater": "4500.00"}}
            return True, {"result": {"month": "--", "quater": "--"}}
        if url.endswith("/saveCalculation.php"):
            captured_payload["save_payload"] = payload
            return True, {"ok": True}
        return True, {}

    monkeypatch.setattr(grenke_launch.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(grenke_launch, "_post_json", _fake_post_json)
    monkeypatch.setattr(grenke_launch.secrets, "token_hex", lambda _size: "baddcafe12345678")

    form = FormRequest(
        id=35,
        customer_name="Test Sp. z o.o.",
        customer_email="test@example.com",
        customer_phone="+48500100200",
        status="SUBMITTED",
        token_hash="token-hash-test",
        token_expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    workflow_case = FormWorkflowCase(
        id=102,
        form_request_id=35,
        stage="PROFORMA_CREATED",
        business_status="WAITING_SIGNATURE",
        proforma_firebird_id=None,
    )
    workflow_devices = [
        FormWorkflowDevice(
            workflow_case_id=102,
            source_type="firebird_magazyn_28",
            source_row=221,
            producer="Ricoh",
            model="MP 501",
            serial="T605H900555",
            ewidencja="KP/5001",
            price_net="4800.00",
        )
    ]

    result = asyncio.run(
        grenke_launch.launch_grenke_prefill(
            form=form,
            workflow_case=workflow_case,
            workflow_devices=workflow_devices,
        )
    )

    assert result.prefill_state == "full"
    assert tried_months[:2] == ["60", "48"]
    save_payload = captured_payload["save_payload"]
    assert isinstance(save_payload, dict)
    assert save_payload["leasingTime"] == "48"
    assert save_payload["finalRate"] == "4 500,00 zł"


def test_launch_grenke_prefill_ogranicza_okres_do_remote_max(monkeypatch):
    captured_payload: dict[str, object] = {}
    tried_months: list[str] = []

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    async def _fake_post_json(_client, url: str, payload: dict):
        if url.endswith("/setSession.php"):
            return True, {"ok": True}
        if url.endswith("/calculate.php"):
            month = str(payload.get("months"))
            tried_months.append(month)
            return True, {"result": {"month": "1500.00", "quater": "4500.00"}}
        if url.endswith("/saveCalculation.php"):
            captured_payload["save_payload"] = payload
            return True, {"ok": True}
        return True, {}

    async def _fake_fetch_remote_bounds(_client, *, app_base: str):
        _ = app_base
        return 48, 18, 48

    monkeypatch.setattr(grenke_launch.httpx, "AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr(grenke_launch, "_post_json", _fake_post_json)
    monkeypatch.setattr(grenke_launch, "_fetch_remote_lease_bounds", _fake_fetch_remote_bounds)
    monkeypatch.setattr(grenke_launch.secrets, "token_hex", lambda _size: "feedface12345678")

    form = FormRequest(
        id=36,
        customer_name="Test Sp. z o.o.",
        customer_email="test@example.com",
        customer_phone="+48500100200",
        status="SUBMITTED",
        token_hash="token-hash-test",
        token_expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    workflow_case = FormWorkflowCase(
        id=103,
        form_request_id=36,
        stage="PROFORMA_CREATED",
        business_status="WAITING_SIGNATURE",
        proforma_firebird_id=None,
    )
    workflow_devices = [
        FormWorkflowDevice(
            workflow_case_id=103,
            source_type="firebird_magazyn_28",
            source_row=222,
            producer="Ricoh",
            model="MP 601",
            serial="T605H900777",
            ewidencja="KP/6001",
            price_net="5200.00",
        )
    ]

    result = asyncio.run(
        grenke_launch.launch_grenke_prefill(
            form=form,
            workflow_case=workflow_case,
            workflow_devices=workflow_devices,
        )
    )

    assert result.prefill_state == "full"
    assert tried_months == ["48"]
    save_payload = captured_payload["save_payload"]
    assert isinstance(save_payload, dict)
    assert save_payload["leasingTime"] == "48"
    assert save_payload["minMonth"] == "18"
    assert save_payload["maxMonth"] == "48"
    assert save_payload["minMonths"] == 18
    assert save_payload["maxMonths"] == 48
    assert save_payload["defaultMonths"] == "48"
    assert save_payload["monthsList"] == ["48", "36", "30", "24", "18"]
