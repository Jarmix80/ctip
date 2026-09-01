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

  function setOptionalText(node, value) {
    if (!node) return;
    const normalized = String(value || "").trim();
    setText(node, normalized);
    node.hidden = !normalized;
  }

  function hasSelectedOrder() {
    const detail = element("shipping-detail");
    return Boolean(detail && !detail.hidden);
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
    setText(element("shipping-v2-location-short"), location);
  }

  function updateDeliveryAddress() {
    const selected = hasSelectedOrder();
    const company = element("shipping-company")?.value?.trim();
    const contact = element("shipping-contact")?.value?.trim();
    const phone = element("shipping-phone")?.value?.trim();
    const email = element("shipping-email")?.value?.trim();
    const street = element("shipping-street")?.value?.trim();
    const postal = element("shipping-postal")?.value?.trim();
    const city = element("shipping-city")?.value?.trim();
    setText(
      element("shipping-v2-audit-company"),
      selected ? company || "Brak nazwy firmy" : "Brak wybranego zlecenia",
    );
    setOptionalText(element("shipping-v2-audit-contact"), selected && contact ? `Osoba kontaktowa: ${contact}` : "");
    const communication = [
      phone ? `tel. ${phone}` : "telefon nieuzupełniony",
      email || null,
    ].filter(Boolean).join(" • ");
    setOptionalText(element("shipping-v2-audit-communication"), selected ? communication : "");
    setText(
      element("shipping-v2-audit-street"),
      selected ? street || "Brak ulicy i numeru" : "Wybierz zlecenie z kolejki.",
    );
    setOptionalText(
      element("shipping-v2-audit-city"),
      selected ? [postal, city].filter(Boolean).join(" ") || "Brak kodu i miejscowości" : "",
    );
  }

  function updateOrderContent() {
    const problem = hasSelectedOrder()
      ? element("shipping-order-problem")?.textContent?.trim() || "Brak treści zlecenia."
      : "Wybierz zlecenie z kolejki.";
    setText(element("shipping-v2-audit-problem"), problem);
  }

  function updateStatus() {
    const selected = hasSelectedOrder();
    const badge = element("shipping-case-status");
    const status = selected ? badge?.textContent?.trim() || "Nieznany etap" : "Wybierz zlecenie";
    const statusValue = element("shipping-v2-audit-status");
    setText(statusValue, status);
    if (statusValue) {
      const statusClass = [...(badge?.classList || [])].find((name) => name.startsWith("status-"));
      statusValue.className = statusClass || "";
    }
    const trackingNumber = selected ? element("shipping-tracking-open")?.dataset.waybill?.trim() : "";
    setText(
      element("shipping-v2-audit-tracking"),
      trackingNumber ? `Numer DPD: ${trackingNumber}` : "Etykieta DPD nie została jeszcze utworzona.",
    );
    const sourceDpdStatus = document.querySelector("#shipping-queue .shipping-queue-item.active .shipping-dpd-state");
    const dpdStatus = element("shipping-v2-audit-dpd-status");
    setOptionalText(
      dpdStatus,
      trackingNumber
        ? sourceDpdStatus?.textContent?.trim() || "Status DPD nie został jeszcze pobrany."
        : "",
    );
    if (dpdStatus) {
      const category = [...(sourceDpdStatus?.classList || [])].find((name) => name !== "shipping-dpd-state");
      dpdStatus.className = category ? `shipping-dpd-state ${category}` : "";
    }
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

  function updateV2Context() {
    updateProgress();
    updateLocation();
    updateDeliveryAddress();
    updateOrderContent();
    updatePackage();
    updateStatus();
    updateQueueSummary();
  }

  document.addEventListener("DOMContentLoaded", () => {
    const observedNodes = [
      element("shipping-detail"),
      element("shipping-queue"),
      element("shipping-case-status"),
      element("shipping-location"),
      element("shipping-order-problem"),
      element("shipping-tracking-open"),
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
