(() => {
  "use strict";

  const statusProgress = {
    review_pending: { current: 1, percent: 10, label: "Weryfikacja danych" },
    ready: { current: 3, percent: 50, label: "Gotowe do etykiety" },
    shipment_created: { current: 4, percent: 75, label: "Etykieta wygenerowana" },
    reconcile_required: { current: 3, percent: 65, label: "Wymaga uzgodnienia" },
    handed_over: { current: 5, percent: 100, label: "Przekazane kurierowi" },
    closed: { current: 5, percent: 100, label: "Realizacja zamknięta" },
    manual_billing: { current: 5, percent: 100, label: "Przekazane do faktury" },
  };

  function element(id) {
    return document.getElementById(id);
  }

  function setText(node, value) {
    if (node && node.textContent !== value) node.textContent = value;
  }

  function currentStatus() {
    const badge = element("shipping-case-status");
    const statusClass = [...(badge?.classList || [])].find((name) => name.startsWith("status-"));
    return statusClass?.slice(7) || "review_pending";
  }

  function selectedPackageItems() {
    return [...document.querySelectorAll("[data-stock-select]:checked")].map((checkbox) => {
      const itemId = checkbox.dataset.stockSelect;
      const row = checkbox.closest("tr");
      const quantity = Number(document.querySelector(`[data-stock-quantity="${itemId}"]`)?.value || 0);
      return {
        index: row?.dataset.stockItemIndex || "—",
        name: row?.dataset.stockItemName || "Nieznana część",
        unit: row?.dataset.stockItemUnit || "szt.",
        quantity,
      };
    });
  }

  function packageSummary(items) {
    const quantity = items.reduce((total, item) => total + item.quantity, 0);
    if (!items.length) {
      return "Brak wybranych części";
    }
    const positionLabel = items.length === 1 ? "1 pozycja" : `${items.length} pozycji`;
    return `${positionLabel} · ${quantity.toLocaleString("pl-PL", { maximumFractionDigits: 3 })} szt.`;
  }

  function updatePackageItems(items) {
    const list = element("shipping-v2-package-items");
    if (!list) return;
    const signature = JSON.stringify(items);
    if (list.dataset.signature === signature) return;
    list.dataset.signature = signature;
    list.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("li");
      empty.className = "empty";
      empty.textContent = "Nie dodano jeszcze żadnych części.";
      list.append(empty);
      return;
    }
    items.forEach((item) => {
      const row = document.createElement("li");
      const identity = document.createElement("span");
      const index = document.createElement("strong");
      const name = document.createElement("small");
      const quantity = document.createElement("b");
      index.textContent = item.index;
      name.textContent = item.name;
      quantity.textContent = `${item.quantity.toLocaleString("pl-PL", { maximumFractionDigits: 3 })} ${item.unit}`;
      identity.append(index, name);
      row.append(identity, quantity);
      list.append(row);
    });
  }

  function updateProgress() {
    const progress = statusProgress[currentStatus()] || statusProgress.review_pending;
    const label = element("shipping-v2-progress-label");
    const bar = element("shipping-v2-progress-bar");
    setText(label, progress.label);
    if (bar) bar.style.width = `${progress.percent}%`;
    document.querySelectorAll("[data-v2-step]").forEach((step) => {
      const stepNumber = Number(step.dataset.v2Step);
      step.classList.toggle("done", stepNumber < progress.current);
      step.classList.toggle("active", stepNumber === progress.current);
      const marker = step.querySelector(":scope > span");
      setText(marker, stepNumber < progress.current ? "✓" : String(stepNumber));
    });
  }

  function updateLocation() {
    const location = element("shipping-location")?.textContent?.trim() || "Brak lokalizacji";
    const message = element("shipping-location-message")?.textContent?.trim() || "System porówna lokalizację z Menadżerem Serwisu.";
    setText(element("shipping-v2-location-short"), location);
    setText(element("shipping-v2-audit-location"), location);
    setText(element("shipping-v2-audit-location-note"), message);
  }

  function updatePackage() {
    const items = selectedPackageItems();
    const summary = packageSummary(items);
    const documentMode = document.body.dataset.shippingDocumentMode || "rw";
    const billing = {
      invoice_wz: "Rozliczenie: faktura sprzedaży i dokument WZ",
      wz: "Rozliczenie: dokument WZ bez faktury",
      rw: "Rozliczenie umowne: dokument RW",
    }[documentMode];
    const billingLabel = {
      invoice_wz: "FV + WZ",
      wz: "Dokument WZ",
      rw: "Dokument RW",
    }[documentMode];
    const weight = element("shipping-weight")?.selectedOptions?.[0]?.textContent || "—";
    setText(element("shipping-v2-package-label"), summary);
    setText(element("shipping-v2-audit-package"), summary);
    setText(element("shipping-v2-audit-billing"), billing);
    setText(element("shipping-v2-billing-label"), billingLabel);
    setText(element("shipping-v2-weight-label"), weight);
    updatePackageItems(items);
  }

  function updateQueueSummary() {
    const readyCount = document.querySelectorAll("#shipping-queue .status-shipment_created").length;
    if (element("shipping-v2-ready-count")) {
      setText(element("shipping-v2-ready-count"), readyCount === 1 ? "1 przesyłka" : `${readyCount} przesyłek`);
    }
  }

  function updateOperator() {
    const user = element("shipping-user")?.textContent?.trim() || "—";
    setText(element("shipping-v2-audit-user"), user);
  }

  function updateV2Context() {
    updateProgress();
    updateLocation();
    updatePackage();
    updateQueueSummary();
    updateOperator();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const observedNodes = [
      element("shipping-detail"),
      element("shipping-queue"),
      element("shipping-case-status"),
      element("shipping-location"),
      element("shipping-location-message"),
      element("shipping-user"),
    ].filter(Boolean);
    const observer = new MutationObserver(updateV2Context);
    observedNodes.forEach((node) => observer.observe(node, {
      attributes: true,
      childList: true,
      characterData: true,
      subtree: true,
    }));
    document.addEventListener("change", updateV2Context);
    document.addEventListener("input", updateV2Context);
    updateV2Context();
  });
})();
