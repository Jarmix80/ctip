"""Testy działania walidacji i ostrzeżeń formularza BNP w silniku JavaScript."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


def _function_source(name: str) -> str:
    """Odczytuje rzeczywistą funkcję formularza bez uruchamiania całej aplikacji."""
    source = Path("app/static/device/device.js").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^function {re.escape(name)}\(.*?^}}", source)
    assert match is not None
    return match.group(0)


def _run_javascript(script: str, payload: dict) -> dict:
    """Uruchamia izolowany scenariusz formularza bez połączeń sieciowych."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Test formularza wymaga lokalnego Node.js.")
    result = subprocess.run(
        [node, "-e", script],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "mode,value,target,index,error",
    [
        ("kp", "4579", "WKP/4579/SRS", "WKP/4579/BNP", None),
        ("serial", "B630205697", "WKP/B630205697", "WKP/B630205697", None),
        ("serial", "C630099408", "wkp/c630099408/Serwis", "WKP/C630099408/BNP", None),
        ("serial", "12345", "WKP/12345", "WKP/12345/BNP", None),
        ("serial", "ABC123", "WKP/ABC124", "WKP/ABC123", "numer seryjny ABC123"),
        ("serial", "ABC123", "WKP/ABC123", "WKP/ABC1234/BNP", "numer seryjny ABC123"),
        ("kp", "4579", "WKP/4580", "WKP/4579", "numer KP/4579"),
        ("serial", "ABC123", "KP/ABC123", "WKP/ABC123", "format WKP"),
        ("serial", "ABC123", "WKP/ABC123/" + "X" * 100, "WKP/ABC123", "100 znaków"),
        (None, None, "WKP/ABC123", "WKP/ABC123", "Wyszukaj urządzenie ponownie"),
    ],
)
def test_walidacja_formularza_chroni_identyfikator_i_dopuszcza_dopiski(
    mode, value, target, index, error
) -> None:
    """Walidacja UI obsługuje oba tryby i nie pozwala podmienić numeru urządzenia."""
    result = _run_javascript(
        """
        const payload = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
        const fields = {
          "device-bnp-target-ewidencja": {value: payload.target},
          "device-bnp-warehouse-index": {value: payload.index},
        };
        const state = {bnpLookup: {identifier_mode: payload.mode, identifier_value: payload.value}};
        const document = {getElementById: (fieldId) => fields[fieldId]};
        const validate = new Function("deviceState", "document", payload.source + "; return validateBnpIdentifiers;")(state, document);
        let error = null;
        try { validate(); } catch (failure) { error = failure.message; }
        process.stdout.write(JSON.stringify({error}));
        """,
        {
            "source": _function_source("validateBnpIdentifiers"),
            "mode": mode,
            "value": value,
            "target": target,
            "index": index,
        },
    )
    if error is None:
        assert result["error"] is None
    else:
        assert error in result["error"]


def test_formularz_pokazuje_ostrzezenie_i_pozwala_zatwierdzic_po_przygotowaniu() -> None:
    """Odświeżenie podglądu zachowuje ostrzeżenie i edycję dopisków, a stan 0 odblokowuje PZ."""
    source = "\n".join(
        _function_source(name)
        for name in (
            "renderBnpMessageList",
            "buildBnpDefaultItemName",
            "updateBnpCompleteState",
            "renderBnpLookup",
        )
    )
    result = _run_javascript(
        """
        const payload = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
        const fields = new Map();
        const document = {getElementById: (fieldId) => {
          if (!fields.has(fieldId)) fields.set(fieldId, {value: "", innerHTML: "", hidden: false, checked: false});
          return fields.get(fieldId);
        }};
        const state = {};
        const functions = new Function("deviceState", "document", "escapeHtml", "localIsoDate", "updateBnpGross",
          payload.source + "; return {renderBnpLookup, updateBnpCompleteState};"
        )(state, document, String, () => "2026-09-10", () => {});
        const lookup = {
          serial: "B630205697", machine: {serial: "B630205697", ewidencja: "B630205697/BNP", marka: "HSM", model: "AF500"},
          supplier: {name: "BNP"}, warnings: ["MASZYNA.EWIDENCJA nie ma formatu KP/<numer>/..."], blockers: [],
          warehouse_rows: [], can_create_catalog: true, can_complete: false,
          suggested_ewidencja: "WKP/B630205697", suggested_index: "WKP/B630205697",
        };
        functions.renderBnpLookup(lookup);
        const before = {warning: document.getElementById("device-bnp-warnings").hidden,
          create: document.getElementById("device-bnp-create-catalog-action").hidden,
          completeDisabled: document.getElementById("device-bnp-complete-btn").disabled};
        lookup.target_item = {id_magazyn_table: 1, quantity: 0};
        lookup.can_create_catalog = false;
        lookup.can_complete = true;
        functions.renderBnpLookup(lookup, {targetEwidencja: "WKP/B630205697/Serwis", warehouseIndex: "WKP/B630205697/BNP",
          document: "FWK26/09/00025", documentDate: "2026-09-10", itemName: "Niszczarka HSM", priceNetto: "99.40"});
        const confirmationReset = !document.getElementById("device-bnp-confirm").checked;
        document.getElementById("device-bnp-confirm").checked = true;
        functions.updateBnpCompleteState();
        process.stdout.write(JSON.stringify({before, confirmationReset,
          warningHidden: document.getElementById("device-bnp-warnings").hidden,
          createHidden: document.getElementById("device-bnp-create-catalog-action").hidden,
          completeDisabled: document.getElementById("device-bnp-complete-btn").disabled,
          target: document.getElementById("device-bnp-target-ewidencja").value,
          index: document.getElementById("device-bnp-warehouse-index").value}));
        """,
        {"source": source},
    )
    assert result["before"] == {"warning": False, "create": False, "completeDisabled": True}
    assert result["confirmationReset"] is True
    assert result["warningHidden"] is False
    assert result["createHidden"] is True
    assert result["completeDisabled"] is False
    assert result["target"] == "WKP/B630205697/Serwis"
    assert result["index"] == "WKP/B630205697/BNP"
