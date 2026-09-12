"""Sprawdzenie wspólnego frontendu ORBIT na lokalnych odpowiedziach kontraktu API."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static/shipping"
TEMPLATES = ("shipping/index.html", "shipping/v2.html")


def render_template(template):
    """Renderuje rzeczywisty szablon bez uruchamiania backendu i baz danych."""
    environment = Environment(loader=FileSystemLoader(ROOT / "app/templates"))
    return environment.get_template(template).render(
        request=SimpleNamespace(app=SimpleNamespace(version="test")), shipping_default_entry=False
    )


def shipping_order_payload(machine_id=1204):
    """Odtwarza strukturę źródłowego zlecenia MS z GET /admin/shipping/orders/1001."""
    address = {
        "company_name": "Firma testu ORBIT",
        "contact_name": None,
        "street": "Testowa 10",
        "postal_code": "00-001",
        "city": "Warszawa",
        "phone": "500600700",
        "email": "orbit@example.com",
    }
    return {
        "order": {
            "order_table_id": 1001,
            "order_id": 77,
            "order_year": 2026,
            "status": "O",
            "company_id": 1,
            "client_id": 15,
            "machine_id": machine_id,
            "model_id": 900,
            "order_kind": "Umowa",
            "order_location": "Sekretariat, parter",
            "machine_location": None,
            "device_brand": "Ricoh",
            "device_model": "IM C3000",
            "problem": "Proszę wysłać toner czarny",
            "order_company_name": address["company_name"],
            "order_street": address["street"],
            "order_postal_code": address["postal_code"],
            "order_city": address["city"],
            "order_phone": address["phone"],
            "order_email": address["email"],
            "tracking_number": None,
            "technician": None,
            "secondary_technician": None,
        },
        "order_state": None,
        "preferred_address": address,
        "preferred_address_key": "order",
        "address_candidates": [{"key": "order", "label": "Adres ze zlecenia", "address": address}],
        "saved_addresses": [],
        "location_context": {"order_text": "Sekretariat, parter", "verifiable": False},
        "overdue_payment": {"has_overdue_invoices": False},
        "stock": [],
        "case": None,
    }


@pytest.mark.parametrize("template", TEMPLATES)
def test_orbit_shell_in_both_layouts(template):
    """Panel jest niezależny od realizacji, a funkcja i finanse są początkowo ukryte."""
    rendered = render_template(template)
    assert rendered.index('id="shipping-orbit-view"') < rendered.index(
        'id="shipping-dispatch-view"'
    )
    assert rendered.count('data-shipping-view="orbit" hidden') == 1
    assert rendered.count('data-orbit-tab="') == 8
    assert 'data-orbit-tab="billing" hidden' in rendered
    assert 'class="shipping-orbit-link" hidden' in rendered
    assert "/static/shipping/orbit.js?" in rendered
    assert "/static/shipping/orbit.css?" in rendered
    assert all(
        f'value="{scope}"' in rendered
        for scope in ("active", "suspended", "scrapped", "review", "all")
    )


def test_orbit_scripts_are_read_only_except_audited_policy_and_parse():
    """Raporty są odczytowe; jedyny zapis interfejsu dotyczy polityk administratora."""
    script = (STATIC / "orbit.js").read_text()
    assert 'method: "GET"' in script
    assert re.findall(r'method:\s*["\'](POST|PUT|PATCH|DELETE)', script) == ["PUT"]
    assert "shippingJson(`${api}/policies`" in script
    assert "state.capabilityPolicy" in script
    assert "raw_payload" in script
    assert "JSON.stringify(visible" not in script
    assert "Math.random" not in script
    assert "encodeURIComponent(state.device)" in script
    assert "encodeURIComponent(id)" in script
    assert "AbortController" in script
    node = shutil.which("node")
    if node:
        for filename in ("shipping.js", "orbit.js"):
            subprocess.run(
                [node, "--check", str(STATIC / filename)], check=True, capture_output=True
            )


@pytest.fixture(scope="module")
def browser():
    """Uruchamia lokalną przeglądarkę; brak narzędzia nie wyłącza testów statycznych."""
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as runtime:
        try:
            instance = runtime.chromium.launch(
                headless=True, executable_path=os.environ.get("ORBIT_TEST_CHROMIUM")
            )
        except playwright.Error as error:
            if os.environ.get("ORBIT_TEST_CHROMIUM"):
                raise
            pytest.skip(f"Brak lokalnej przeglądarki Chromium: {error}")
        yield instance
        instance.close()


@pytest.fixture
def orbit_page(browser):
    """Mockuje API przy pełnych skryptach Shipping, opcjonalnie z lokalnego podglądu."""
    contexts = []

    def create(
        template=TEMPLATES[0],
        query="?view=orbit&device=ms%3A71",
        *,
        enabled=True,
        finance=False,
        admin=False,
    ):
        settings = {
            "enabled": enabled,
            "can_view_finance": finance,
            "can_manage_policy": admin,
            "policy_writes": [],
            "fail_assessment": False,
            "fail_detail": False,
            "fail_timeline": False,
            "fail_list": False,
            "empty_list": False,
            "orders": {},
            "documented_issues": [],
            "requests": [],
        }
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_init_script("localStorage.setItem('admin-session-token', 'orbit-test')")
        contexts.append(context)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        settings["errors"] = errors
        markup = render_template(template)
        preview = os.environ.get("ORBIT_TEST_PREVIEW", "")
        if preview:
            assert urlsplit(preview).hostname in {"127.0.0.1", "localhost"}
        device = {
            "id": "ms:71",
            "ms_machine_id": 71,
            "serial": "TEST-ORBIT-71",
            "model": "Urządzenie testowe mono",
            "customer": "Klient testu",
            "status": "active",
            "updated_at": "2026-09-10T10:00:00+02:00",
            "synced_at": None,
        }
        contracts = [
            {
                "id": "contract:old",
                "contract_id": "old",
                "contract_number": "UMOWA-TEST-A",
                "start": "2025-01-01",
                "end": None,
                "customer_id": 8,
            },
            {
                "id": "contract:new",
                "contract_id": "new",
                "contract_number": "UMOWA-TEST-B",
                "start": "2026-01-01",
                "end": "2027-12-31",
                "customer_id": 8,
            },
        ]
        events = [
            {
                "id": "reading:1",
                "kind": "reading",
                "observed_at": "2026-09-10T00:00:00Z",
                "time_precision": "date",
                "title": "Odczyt testowy",
                "source": "telemetry-test",
                "status": "confirmed",
                "data": {"counters": {"mono": "110"}},
                "finance": {"net_amount": "TAJNA-KWOTA"},
            },
            {
                "id": "month:1",
                "kind": "billing_period",
                "observed_at": None,
                "time_precision": "month",
                "title": "Okres testowy CPC",
                "source": "ms_cpc",
                "status": "partial",
                "data": {
                    "period": {"start": "2026-08-01", "end": "2026-08-31"},
                    "billing": {"mono": "110", "color": None},
                },
            },
            {
                "id": "unknown:1",
                "kind": "service",
                "observed_at": None,
                "time_precision": "unknown",
                "title": "Zgłoszenie bez daty",
                "source": "ms-test",
                "status": "missing",
                "data": {"order_id": "91"},
            },
        ]
        counters = [
            {
                "metric": "mono",
                "source": "telemetry-test",
                "status": "known",
                "points": [
                    {
                        "event_id": "reading:1",
                        "observed_at": "2026-09-10T00:00:00Z",
                        "time_precision": "date",
                        "value": "110",
                        "delta": "10",
                        "epoch": 0,
                        "status": "known",
                        "reason": "Odczyt testowy.",
                    }
                ],
            }
        ]
        settings["counters"] = counters
        settings["events"] = events
        settings["toners"] = [
            {
                "color": "black",
                "remaining_percent": "0",
                "status": "measured",
                "source": "telemetry-test",
                "observed_at": None,
                "stock_quantity": "0",
            }
        ]

        def route_request(route):
            url = urlsplit(route.request.url)
            settings["requests"].append((route.request.method, url.path, parse_qs(url.query)))
            path = unquote(url.path)
            params = parse_qs(url.query)
            payload = None
            status = 200
            if path.startswith("/shipping"):
                if preview:
                    route.continue_()
                    return
                route.fulfill(status=200, content_type="text/html", body=markup)
                return
            if path.startswith("/static/shipping/"):
                if preview:
                    route.continue_()
                    return
                asset = STATIC / Path(path).name
                content = asset.read_text() if asset.exists() else ""
                route.fulfill(
                    status=200,
                    content_type="text/css" if asset.suffix == ".css" else "application/javascript",
                    body=content,
                )
                return
            if path == "/auth/me":
                payload = {
                    "sections": ["shipping"],
                    "first_name": "Operator",
                    "last_name": "Testowy",
                    "shipping_layout": "v2" if template.endswith("v2.html") else "legacy",
                }
            elif path == "/admin/shipping/config":
                payload = {
                    "shipping": {"fulfillment_enabled": True, "catalog_mutations_enabled": False},
                    "dpd": {"enabled": False, "mode": "mock"},
                    "warehouse_id": 1,
                    "courier_cutoff": "2026-09-10T15:00:00+02:00",
                    "weight_presets_kg": [1, 2],
                    "default_weight_kg": 1,
                }
            elif path == "/admin/shipping/queue":
                payload = {"items": []}
            elif path.startswith("/admin/shipping/orders/"):
                payload = settings["orders"].get(path.rsplit("/", 1)[-1], {})
            elif path.endswith("/capabilities"):
                payload = {
                    key: settings[key]
                    for key in ("enabled", "can_view_finance", "can_manage_policy")
                }
            elif path == "/admin/shipping/orbit/policies":
                settings["policy_writes"].append(route.request.post_data_json)
                payload = {"status": "ok"}
            elif path.startswith("/admin/shipping/orbit/shipment-assessment"):
                status = 503 if settings["fail_assessment"] else 200
                payload = {
                    "devices": [
                        {
                            "device_id": "ms:71",
                            "items": [
                                {
                                    "color": "black",
                                    "reasons": ["Możliwy zapas tonera."],
                                    "policy": {"spare_toners": 0},
                                }
                            ],
                        }
                    ],
                    "blocking": False,
                }
            elif path == "/admin/shipping/orbit":
                status = 503 if settings["fail_list"] else 200
                payload = {
                    "enabled": settings["enabled"],
                    "can_view_finance": settings["can_view_finance"],
                    "items": (
                        []
                        if settings["empty_list"]
                        else [device, {**device, "id": "ms:72", "serial": "TEST-ORBIT-72"}]
                    ),
                    "total": 0 if settings["empty_list"] else 42,
                    "page": int(params.get("page", [1])[0]),
                    "page_size": 20,
                }
            elif "/evidence/" in path:
                payload = {
                    **events[0],
                    "title": '<img src=x onerror="window.orbitXss=true">',
                    "data": {"counters": {"mono": "110"}, "raw": "NIEPUBLICZNY-PAYLOAD"},
                }
                if not settings["can_view_finance"]:
                    payload["finance"] = {"net_amount": "TAJNA-KWOTA"}
                issue = next(
                    (
                        item
                        for item in settings["documented_issues"]
                        if item["event_id"] == path.rsplit("/", 1)[-1]
                    ),
                    None,
                )
                if issue:
                    payload = {
                        "id": issue["event_id"],
                        "kind": "material_issue",
                        "title": "Wydanie materiału",
                        "source": issue["source"],
                        "observed_at": issue["observed_at"],
                        "time_precision": "date",
                        "status": "known",
                        "data": {"document_id": issue["document_id"]},
                        "finance": {
                            "issue_purchase_cost": {
                                key: value
                                for key, value in issue.items()
                                if key not in {"event_id", "observed_at", "source", "document_id"}
                            }
                        },
                    }
            elif path.endswith("/timeline"):
                status = 503 if settings["fail_timeline"] else 200
                payload = {
                    "items": settings["events"],
                    "total": 101,
                    "page": int(params.get("page", [1])[0]),
                    "page_size": 50,
                    "buckets": [
                        {"period": "2026-09", "events": 3},
                        {"period": "month:2026-08", "events": 1},
                        {"period": "unknown", "events": 1},
                    ],
                }
            elif path.startswith("/admin/shipping/orbit/"):
                status = 503 if settings["fail_detail"] else 200
                selected = params.get("contract_id", [None])[0]
                payload = {
                    "device": {
                        **device,
                        "id": path.rsplit("/", 1)[1],
                        "serial": f"TEST-ORBIT-{path.rsplit(':', 1)[-1]}",
                    },
                    "summary": {
                        "events": 3,
                        "contracts": 2,
                        "status": "partial",
                        "margin_available": False,
                    },
                    "counters": settings["counters"],
                    "toners": settings["toners"],
                    "contracts": [
                        item for item in contracts if not selected or item["id"] == selected
                    ],
                    "finance": {
                        "status": "partial",
                        "purchase_costs": {"PLN": "200.00", "EUR": "10.00"},
                        "invoice_revenue": {"PLN": "900.00"},
                        "missing_costs": 1,
                        "margin": None,
                        "documented_issues": settings["documented_issues"],
                    },
                    "coverage": [
                        {
                            "source": "telemetry-test",
                            "count": 1,
                            "first_at": None,
                            "last_at": None,
                            "status": "partial",
                        }
                    ],
                    "narrative": "Opis testowy wynikający z odczytu.",
                    "sources": [{"name": "ms-test", "status": "success", "finished_at": None}],
                    "can_view_finance": settings["can_view_finance"],
                    "toner_scope": "Historia urządzenia do końca zakresu; filtr początku i umowy nie rozpoczyna nowego cyklu.",
                    "policy": {"effective": {}, "overrides": []},
                }
            else:
                status = 404
                payload = {}
            route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))

        page.route("**/*", route_request)
        layout = "v2" if template.endswith("v2.html") else "legacy"
        page.goto(f"{preview or 'http://orbit.test'}/shipping/{layout}{query}")
        if enabled:
            page.locator("#shipping-orbit-view").wait_for(state="visible")
        else:
            page.wait_for_function(
                "document.getElementById('shipping-alert').textContent.includes('KP ORBIT')"
            )
        if enabled and "device=" in query:
            page.locator("#orbit-detail").wait_for(state="visible")
        return page, settings

    yield create
    for context in contexts:
        context.close()


def test_admin_can_save_and_restore_device_policy(orbit_page):
    """Zapisuje zero zapasu jako wartość, a puste pola jako dziedziczenie."""
    page, settings = orbit_page(admin=True)
    page.locator("details:has(#orbit-policy-form) summary").click()
    page.locator('#orbit-policy-form [name="spare_toners"]').fill("0")
    page.locator('#orbit-policy-form button[type="submit"]').click()
    page.wait_for_function(
        "document.getElementById('orbit-feedback').textContent.includes('Zasady zapisane')"
    )
    assert settings["policy_writes"][-1] == {
        "scope": "device",
        "scope_id": "ms:71",
        "color": "all",
        "values": {"spare_toners": 0},
    }
    page.locator("details:has(#orbit-policy-form) summary").click()
    page.locator('#orbit-policy-form button[type="submit"]').click()
    page.wait_for_timeout(100)
    assert settings["policy_writes"][-1]["values"] == {}
    assert not settings["errors"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_shipping_advice_is_nonblocking_for_single_and_combined_parcels(orbit_page, template):
    """Pokazuje wskazówkę i jawną awarię oceny bez blokowania generowania etykiety."""
    page, settings = orbit_page(template)
    assert page.locator("#orbit-policy-form").count() == 0
    before = page.locator("#shipping-consolidated-label-confirm").is_disabled()
    page.evaluate("shippingOrbitEvaluate(null, [1001], true)")
    assert "Możliwy zapas tonera" in page.locator("#shipping-orbit-advice").text_content()
    page.evaluate("document.getElementById('shipping-consolidated-label-dialog').showModal()")
    assert page.locator("#shipping-orbit-consolidated-advice").is_visible()
    assert page.locator("#shipping-consolidated-label-confirm").is_disabled() == before
    settings["fail_assessment"] = True
    page.evaluate("shippingOrbitEvaluate(null, [1001], true)")
    assert "Możesz kontynuować" in page.locator("#shipping-orbit-consolidated-advice").inner_text()
    assert page.locator("#shipping-consolidated-label-confirm").is_disabled() == before
    assert not settings["errors"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_navigation_permissions_and_phone(orbit_page, template):
    """Oba motywy działają klawiaturą i na telefonie, bez nieuprawnionych kwot."""
    page, settings = orbit_page(template)
    assert page.locator('[data-shipping-view="orbit"]').is_visible()
    assert page.locator("#orbit-tab-billing").is_hidden()
    assert "200.00" not in page.locator("#shipping-orbit-view").inner_text()
    assert page.locator('.orbit-ring[data-color="K"] strong').inner_text() == "0%"
    assert page.locator(".orbit-ring").count() == 1
    page.locator("#orbit-tab-overview").focus()
    page.keyboard.press("ArrowRight")
    page.locator("#orbit-events table").wait_for()
    assert "tab=timeline" in page.url
    assert (
        "2026-08-01 — 2026-08-31 (okres miesięczny)" in page.locator("#orbit-events").inner_text()
    )
    assert "precyzja miesięczna źródła" in page.locator("#orbit-buckets").text_content()
    assert "Data nieznana" in page.locator("#orbit-buckets").text_content()
    assert page.locator("#orbit-chart [role=button]").count() == 1
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert not settings["errors"]


def test_finance_contract_and_range_share_url_and_requests(orbit_page):
    """Finanse wymagają zgody, a zmiana umowy i dat obejmuje raport oraz oś."""
    page, settings = orbit_page(
        query="?view=orbit&device=ms%3A71&tab=contractlife&contract=contract%3Aold&from=2026-08-01&to=2026-09-10",
        finance=True,
    )
    page.locator("#orbit-events table").wait_for()
    assert page.locator("#orbit-tab-billing").is_visible()
    assert page.locator("#orbit-contract option").count() == 3
    page.locator("#orbit-contract").select_option("contract:new")
    page.locator("#orbit-detail").wait_for(state="visible")
    page.locator("#orbit-events table").wait_for()
    assert "contract=contract%3Anew" in page.url
    assert "from=2026-08-01" in page.url
    assert any(
        path.endswith("/timeline")
        and params.get("contract_id") == ["contract:new"]
        and params.get("date_from") == ["2026-08-01"]
        for _, path, params in settings["requests"]
    )
    assert "Historia urządzenia do końca zakresu" in page.locator("#orbit-pane").inner_text()
    page.locator("#orbit-tab-billing").click()
    assert "200.00 PLN" in page.locator("#orbit-pane").inner_text()
    assert "10.00 EUR" in page.locator("#orbit-pane").inner_text()
    assert "Niedostępna" in page.locator("#orbit-pane").inner_text()
    page.locator("#orbit-from").fill("2026-10-01")
    requests_before = len(settings["requests"])
    page.locator('#orbit-filters button[type="submit"]').click()
    assert "data od" in page.locator("#orbit-feedback").inner_text()
    assert len(settings["requests"]) == requests_before
    assert all(
        method == "GET"
        for method, path, _ in settings["requests"]
        if path.startswith("/admin/shipping/orbit")
    )


@pytest.mark.parametrize("template", TEMPLATES)
def test_evidence_is_escaped_scoped_and_finance_filtered(orbit_page, template):
    """Dowód jest kodowany w URL i nie ujawnia HTML, surowych pól ani kwot."""
    page, settings = orbit_page(template, query="?view=orbit&device=ms%3A71&tab=timeline")
    page.locator('#orbit-events [data-orbit-evidence="reading:1"]').click()
    page.locator("#orbit-evidence h4").wait_for()
    content = page.locator("#orbit-evidence").inner_text()
    assert "<img" in content
    assert "TAJNA-KWOTA" not in content
    assert "NIEPUBLICZNY-PAYLOAD" not in content
    assert not page.evaluate("Boolean(window.orbitXss)")
    assert any("/ms%3A71/evidence/reading%3A1" in path for _, path, _ in settings["requests"])
    page.keyboard.press("Escape")
    assert not page.locator("#orbit-evidence").is_visible()


def test_disabled_feature_does_not_request_reports(orbit_page):
    """Wyłączona funkcja nie ujawnia zakładki i nie pobiera danych urządzenia."""
    page, settings = orbit_page(enabled=False)
    page.wait_for_function("window.shippingOrbitDashboard !== undefined")
    assert page.locator('[data-shipping-view="orbit"]').is_hidden()
    assert page.locator("#shipping-orbit-view").is_hidden()
    assert not any(
        path.startswith("/admin/shipping/orbit/") and not path.endswith("/capabilities")
        for _, path, _ in settings["requests"]
    )


def test_pagination_scopes_history_and_manual_order_link(orbit_page):
    """Filtry i strony trafiają do API, a odnośnik wymaga jednoznacznego ID MS."""
    page, settings = orbit_page()
    page.locator("#orbit-scope").select_option("scrapped")
    page.locator("#orbit-devices-next").click()
    page.wait_for_function(
        "document.getElementById('orbit-devices-page').textContent.includes('Strona 2')"
    )
    assert any(
        params.get("scope") == ["scrapped"] and params.get("page") == ["2"]
        for _, path, params in settings["requests"]
        if path == "/admin/shipping/orbit"
    )
    page.locator('[data-orbit-device="ms:72"]').click()
    page.wait_for_function(
        "document.getElementById('orbit-identity').textContent.includes('TEST-ORBIT-72')"
    )
    page.go_back()
    page.wait_for_function(
        "document.getElementById('orbit-identity').textContent.includes('TEST-ORBIT-71')"
    )
    page.evaluate("window.shippingOrbitDashboard.updateOrderLink({machine_id: 71})")
    assert "device=ms%3A71" in page.locator("#shipping-orbit-order-link").get_attribute("href")
    page.evaluate(
        "window.shippingOrbitDashboard.updateOrderLink({machine_id: 71, machine_count: 2})"
    )
    assert page.locator("#shipping-orbit-order-link").get_attribute("href") is None
    page.evaluate("window.shippingOrbitDashboard.updateOrderLink({ms_machine_id: 71})")
    assert page.locator("#shipping-orbit-order-link").get_attribute("href") is None


@pytest.mark.parametrize("template", TEMPLATES)
def test_order_machine_id_opens_orbit_from_realistic_shipping_payload(orbit_page, template):
    """Pełne wczytanie zlecenia i kliknięcie linku używa machine_id bez aliasu ORBIT."""
    page, settings = orbit_page(template)
    settings["orders"]["1001"] = shipping_order_payload()
    assert "ms_machine_id" not in settings["orders"]["1001"]["order"]
    page.locator('[data-shipping-view="dispatch"]').click()
    page.evaluate("loadShippingDetail(1001)")
    link = page.locator("#shipping-orbit-order-link")
    assert link.is_visible()
    assert link.get_attribute("href").endswith("?view=orbit&device=ms%3A1204")
    assert page.locator("#shipping-feedback").inner_text() == ""
    link.click()
    page.locator("#orbit-detail").wait_for(state="visible")
    assert "device=ms%3A1204" in page.url
    assert "TEST-ORBIT-1204" in page.locator("#orbit-identity").inner_text()
    assert any(path.endswith("/orbit/ms%3A1204") for _, path, _ in settings["requests"])
    page.locator('[data-shipping-view="dispatch"]').click()
    for machine_id in (None, 0, -1, "", [1204, 1205]):
        settings["orders"]["1001"] = shipping_order_payload(machine_id)
        page.evaluate("loadShippingDetail(1001)")
        assert link.is_hidden()
        assert link.get_attribute("href") is None
    assert not settings["errors"]


def test_source_failure_and_permission_revocation(orbit_page):
    """Błąd osi zachowuje raport, a odebranie uprawnień usuwa kwoty i funkcję."""
    page, settings = orbit_page(finance=True)
    settings["fail_timeline"] = True
    page.locator("#orbit-tab-timeline").click()
    page.wait_for_function(
        "document.getElementById('orbit-timeline-feedback').textContent.includes('503')"
    )
    assert page.locator("#orbit-identity").is_visible()
    settings["fail_timeline"] = False
    page.locator("#orbit-timeline-retry").click()
    page.locator("#orbit-events table").wait_for()
    page.locator("#orbit-tab-billing").click()
    settings["can_view_finance"] = False
    page.locator("#orbit-refresh").click()
    page.wait_for_function("document.getElementById('orbit-tab-billing').hidden")
    assert "200.00 PLN" not in page.locator("#shipping-orbit-view").inner_text()
    settings["enabled"] = False
    page.locator("#orbit-refresh").click()
    page.locator("#shipping-orbit-view").wait_for(state="hidden")
    assert page.locator('[data-shipping-view="orbit"]').is_hidden()


def test_monthly_counter_is_an_observation_without_invented_day(orbit_page):
    """Miesięczny stan zachowuje wartość i okres mimo pustego observed_at."""
    page, settings = orbit_page()
    settings["counters"] = [
        {
            "metric": "mono",
            "source": "ms_cpc",
            "status": "known",
            "points": [
                {
                    "event_id": "month:1",
                    "observed_at": None,
                    "time_precision": "month",
                    "period": {"start": "2026-08-01", "end": "2026-08-31"},
                    "value": "420",
                    "delta": None,
                    "status": "known",
                    "epoch": 0,
                    "reason": "Okres źródłowy.",
                }
            ],
        }
    ]
    settings["toners"] = [{"color": "black", "remaining_percent": None, "status": "unknown"}]
    page.locator("#orbit-refresh").click()
    page.wait_for_function(
        "document.getElementById('orbit-pane').textContent.includes('420 stron')"
    )
    card = page.locator("#orbit-pane .orbit-metric").first
    assert "2026-08-01 — 2026-08-31 (okres miesięczny)" in card.inner_text()
    assert "Brak daty" not in card.inner_text()
    assert page.locator(".orbit-ring strong").inner_text() == "—"
    page.locator("#orbit-tab-timeline").click()
    page.locator("#orbit-events table").wait_for()
    assert page.locator("#orbit-chart rect[height='65']").count() == 0


def test_late_response_and_detail_error_never_show_old_device(orbit_page):
    """Anulowanie opóźnionego odczytu chroni nowe urządzenie i stan błędu."""
    page, settings = orbit_page()
    page.evaluate(
        """() => {
      const fetchOriginal = window.fetch;
      window.fetch = async (...args) => {
        const response = await fetchOriginal(...args);
        if (String(args[0]).includes('/ms%3A71?')) {
          await new Promise(resolve => setTimeout(resolve, 500));
        }
        return response;
      };
    }"""
    )
    page.locator('[data-orbit-device="ms:71"]').click()
    page.locator('[data-orbit-device="ms:72"]').click()
    page.wait_for_function(
        "document.getElementById('orbit-identity').textContent.includes('TEST-ORBIT-72')"
    )
    page.wait_for_timeout(650)
    assert "TEST-ORBIT-72" in page.locator("#orbit-identity").inner_text()
    settings["fail_detail"] = True
    page.locator("#orbit-refresh").click()
    page.wait_for_function("document.getElementById('orbit-empty').textContent.includes('503')")
    assert page.locator("#orbit-detail").is_hidden()
    settings["fail_detail"] = False
    page.locator("#orbit-refresh").click()
    page.locator("#orbit-detail").wait_for(state="visible")
    assert "TEST-ORBIT-72" in page.locator("#orbit-identity").inner_text()


def test_all_tabs_filters_and_layout_link_are_usable(orbit_page):
    """Osiem widoków ma treść, a zmiana motywu zachowuje pełny kontekst URL."""
    page, settings = orbit_page(finance=True)
    for tab in ("overview", "toners", "billing", "service", "contracts", "story"):
        page.locator(f"#orbit-tab-{tab}").click()
        assert page.locator("#orbit-pane").inner_text().strip()
    page.locator("#orbit-tab-contracts").click()
    assert "Brak terminu" in page.locator("#orbit-pane").inner_text()
    assert page.locator("#orbit-pane progress").count() == 1
    page.locator("[data-orbit-contract-life]").first.click()
    page.locator("#orbit-detail").wait_for(state="visible")
    page.locator("#orbit-from").fill("2026-08-01")
    page.locator("#orbit-to").fill("2026-08-31")
    page.locator('#orbit-filters button[type="submit"]').click()
    page.locator("#orbit-events table").wait_for()
    destination = page.evaluate("shippingLayoutDestination('v2')")
    assert "view=orbit" in destination and "contract=contract%3Aold" in destination
    assert "from=2026-08-01" in destination and "to=2026-08-31" in destination
    page.locator("#orbit-bucket").select_option("day")
    page.locator("#orbit-events table").wait_for()
    assert any(
        params.get("bucket") == ["day"]
        for _, path, params in settings["requests"]
        if path.endswith("/timeline")
    )
    page.locator('[data-orbit-zoom="0.5"]').click()
    page.locator("#orbit-detail").wait_for(state="visible")
    assert "from=2026-08-01" not in page.url
    assert not settings["errors"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_empty_and_failed_search_are_distinct(orbit_page, template):
    """Pusta baza i błąd odczytu nie pokazują fikcyjnych urządzeń ani zerowych metryk."""
    page, settings = orbit_page(template, query="?view=orbit")
    settings["empty_list"] = True
    page.locator("#orbit-refresh").click()
    page.wait_for_function(
        "document.getElementById('orbit-search-feedback').textContent.includes('Brak pasujących')"
    )
    assert page.locator("#orbit-devices li").count() == 0
    assert page.locator("#orbit-detail").is_hidden()
    assert page.locator("#orbit-devices-next").is_disabled()
    settings["fail_list"] = True
    page.locator("#orbit-refresh").click()
    page.wait_for_function(
        "document.getElementById('orbit-search-feedback').textContent.includes('503')"
    )
    assert "Brak pasujących" not in page.locator("#orbit-search-feedback").inner_text()
    assert page.locator("#orbit-detail").is_hidden()
    assert not settings["errors"]


@pytest.mark.parametrize("template", TEMPLATES)
def test_documented_issues_preserve_unknown_currency_and_finance_permissions(orbit_page, template):
    """Wydania zachowują cenę zakupu, brak waluty i dowód, bez sumowania oraz wycieku."""
    page, settings = orbit_page(
        template, query="?view=orbit&device=ms%3A71&tab=billing", finance=True
    )
    issue = {
        "event_id": "issue:17",
        "observed_at": "2026-09-10",
        "source": "ms_orbit",
        "document_id": 17001,
        "status": "known",
        "amount": "123.00",
        "quantity": "1",
        "unit_price": "123",
        "currency": None,
        "currency_status": "unverified",
        "currency_scope": "ms:company:1",
        "scope": "before_returns",
        "returns_status": "unverified",
        "basis": "ZPOZYCJA.CENA_ZAKUPU — test wydania",
    }
    settings["documented_issues"] = [issue]
    settings["events"].append(
        {
            "id": issue["event_id"],
            "kind": "material_issue",
            "title": "Wydanie materiału",
            "observed_at": issue["observed_at"],
            "source": issue["source"],
            "status": "known",
            "time_precision": "date",
            "data": {"document_id": issue["document_id"]},
            "finance": {"issue_purchase_cost": issue},
        }
    )
    page.locator("#orbit-refresh").click()
    table = page.locator("[data-orbit-documented-issues]")
    table.locator("tbody tr").wait_for()
    assert (
        table.locator("h3").inner_text()
        == "Potwierdzone wartości wydań — przed uzgodnieniem zwrotów"
    )
    assert table.locator("tbody td").nth(2).inner_text() == "123"
    assert table.locator("tbody td").nth(3).inner_text() == "123.00"
    assert "waluta niepotwierdzona" in table.inner_text()
    assert "PLN" not in table.inner_text()
    assert "200.00 PLN" in page.locator("#orbit-pane .orbit-grid").first.inner_text()
    assert "323.00" not in page.locator("#orbit-pane").inner_text()
    table.locator("[data-orbit-evidence]").click()
    page.locator("#orbit-evidence h4").wait_for()
    assert "123.00" in page.locator("#orbit-evidence").inner_text()
    assert "waluta niepotwierdzona" in page.locator("#orbit-evidence").inner_text()
    assert "PLN" not in page.locator("#orbit-evidence").inner_text()
    assert any(path.endswith("/evidence/issue%3A17") for _, path, _ in settings["requests"])
    page.locator("#orbit-evidence-close").click()
    settings["can_view_finance"] = False
    page.locator("#orbit-refresh").click()
    page.wait_for_function(
        "document.getElementById('orbit-tab-billing').hidden && !document.getElementById('orbit-detail').hidden"
    )
    assert table.count() == 0
    assert "ZPOZYCJA.CENA_ZAKUPU" not in page.locator("#shipping-orbit-view").text_content()
    page.locator("#orbit-tab-timeline").click()
    page.locator('#orbit-events [data-orbit-evidence="issue:17"]').click()
    page.locator("#orbit-evidence h4").wait_for()
    for private in ("123.00", "ZPOZYCJA.CENA_ZAKUPU", "ms:company:1", "waluta niepotwierdzona"):
        assert private not in page.locator("#shipping-orbit-view").text_content()
    assert not settings["errors"]
