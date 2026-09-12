"""Sprawdzenie wspólnego panelu wydajności w obu szablonach Shipping."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader


@pytest.mark.parametrize("template", ["shipping/index.html", "shipping/v2.html"])
def test_both_layouts_include_independent_toner_view(template):
    """Panel tonerów poprzedza sekcję realizacji i nie znika razem z nią."""
    environment = Environment(loader=FileSystemLoader("app/templates"))
    rendered = environment.get_template(template).render(
        request=SimpleNamespace(app=SimpleNamespace(version="test")), shipping_default_entry=False
    )
    assert rendered.index('id="shipping-toners-view"') < rendered.index(
        'id="shipping-dispatch-view"'
    )
    assert rendered.count('data-shipping-view="toners"') == 1
    assert '<option value="active">Aktywne umowy</option>' in rendered
    assert '<input id="toner-available" type="checkbox">' in rendered
    script = Path("app/static/shipping/toner-yields.js").read_text()
    assert "revision: state.detail.revision" in script
    assert "escapeShippingHtml" in script
    assert "text.textContent = JSON.stringify(evidence" in script
