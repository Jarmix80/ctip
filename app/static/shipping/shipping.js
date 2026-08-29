const SHIPPING_TOKEN_KEY = "admin-session-token";

const shippingState = {
  token: null,
  config: null,
  queue: [],
  selectedOrderId: null,
  detail: null,
};

function shippingToken() {
  return window.localStorage?.getItem(SHIPPING_TOKEN_KEY) || window.sessionStorage?.getItem(SHIPPING_TOKEN_KEY) || null;
}

function shippingHeaders(json = false) {
  const headers = shippingState.token ? { "X-Admin-Session": shippingState.token } : {};
  if (json) headers["Content-Type"] = "application/json";
  return headers;
}

function escapeShippingHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function shippingJson(url, options = {}) {
  const response = await fetch(url, { ...options, headers: { ...shippingHeaders(Boolean(options.body)), ...(options.headers || {}) } });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    window.location.replace("/");
    throw new Error("Sesja wygasła.");
  }
  if (!response.ok) {
    const detail = Array.isArray(payload.detail)
      ? payload.detail.map((item) => item.msg || "Błąd danych").join(" ")
      : payload.detail;
    throw new Error(detail || "Operacja zakończyła się błędem.");
  }
  return payload;
}

function shippingFeedback(message, error = false) {
  const node = document.getElementById("shipping-feedback");
  node.textContent = message || "";
  node.hidden = !message;
  node.className = `shipping-feedback${error ? " error" : ""}`;
}

function renderShippingQueue() {
  const query = document.getElementById("shipping-search").value.trim().toLowerCase();
  const queue = shippingState.queue.filter((item) => {
    const text = [item.order_id, item.company_name, item.device_brand, item.device_model, item.problem].join(" ").toLowerCase();
    return !query || text.includes(query);
  });
  document.getElementById("shipping-count").textContent = String(queue.length);
  document.getElementById("shipping-queue").innerHTML = queue.length
    ? queue.map((item) => `
      <button type="button" class="shipping-queue-item ${Number(item.order_table_id) === shippingState.selectedOrderId ? "active" : ""}"
              data-order-id="${Number(item.order_table_id)}">
        <span class="shipping-queue-row"><strong>#${escapeShippingHtml(item.order_id)}/${escapeShippingHtml(item.order_year)}</strong><span class="shipping-state">${escapeShippingHtml(item.ctip_status)}</span></span>
        <span>${escapeShippingHtml(item.company_name || "Bez nazwy klienta")}</span>
        <small>${escapeShippingHtml([item.device_brand, item.device_model].filter(Boolean).join(" ") || "Brak modelu")}</small>
        <small>${escapeShippingHtml(item.problem || "Brak opisu")}</small>
      </button>`).join("")
    : '<p class="shipping-muted">Brak zleceń w wybranym zakresie.</p>';
  document.querySelectorAll("[data-order-id]").forEach((button) => {
    button.addEventListener("click", () => loadShippingDetail(Number(button.dataset.orderId)));
  });
}

function fillShippingAddress(address) {
  document.getElementById("shipping-company").value = address?.company_name || "";
  document.getElementById("shipping-contact").value = address?.contact_name || "";
  document.getElementById("shipping-street").value = address?.street || "";
  document.getElementById("shipping-postal").value = address?.postal_code || "";
  document.getElementById("shipping-city").value = address?.city || "";
  document.getElementById("shipping-phone").value = address?.phone || "";
  document.getElementById("shipping-email").value = address?.email || "";
  document.getElementById("shipping-address-source").value = address?.source || "manual";
}

function renderShippingStock() {
  const search = document.getElementById("shipping-stock-search").value.trim().toLowerCase();
  const selected = new Map((shippingState.detail?.case?.items || []).map((item) => [Number(item.firebird_warehouse_item_id), item]));
  const rows = (shippingState.detail?.stock || []).filter((item) => {
    const text = `${item.item_index || ""} ${item.item_name || ""}`.toLowerCase();
    return !search || text.includes(search);
  });
  document.getElementById("shipping-stock").innerHTML = rows.map((item) => {
    const itemId = Number(item.warehouse_item_id);
    const chosen = selected.get(itemId);
    const available = Number(item.available_after_soft_reservations || 0);
    return `<tr data-stock-row="${itemId}">
      <td><input type="checkbox" data-stock-select="${itemId}" ${chosen ? "checked" : ""} ${available <= 0 && !chosen ? "disabled" : ""}></td>
      <td><strong>${escapeShippingHtml(item.item_index || "—")}</strong><br>${escapeShippingHtml(item.item_name)}${item.compatible ? '<br><span class="shipping-compatible">ZGODNY Z MODELEM</span>' : ""}</td>
      <td>${available.toLocaleString("pl-PL", { maximumFractionDigits: 3 })} ${escapeShippingHtml(item.unit || "szt.")}</td>
      <td><input type="number" min="0.001" max="${available || 1}" step="1" value="${chosen?.quantity || 1}" data-stock-quantity="${itemId}"></td>
      <td><input type="checkbox" data-stock-remember="${itemId}" ${item.compatible ? "checked" : ""}></td>
    </tr>`;
  }).join("");
}

function applyShippingCase(caseData) {
  const status = caseData?.status || "review_pending";
  document.getElementById("shipping-case-status").textContent = status.replaceAll("_", " ");
  const ready = status === "ready";
  const shipment = caseData?.shipment;
  document.getElementById("shipping-create").disabled = !ready;
  document.getElementById("shipping-manual").disabled = !ready;
  const label = document.getElementById("shipping-label");
  label.hidden = !shipment?.label_available;
  label.href = shipment?.label_available ? `/admin/shipping/shipments/${shipment.id}/label` : "#";
  if (shipment?.tracking_number) {
    shippingFeedback(`Numer przesyłki: ${shipment.tracking_number}. Status Firebird: ${shipment.firebird_status}.`);
  }
}

async function loadShippingDetail(orderId) {
  shippingState.selectedOrderId = orderId;
  renderShippingQueue();
  shippingFeedback("");
  try {
    const detail = await shippingJson(`/admin/shipping/orders/${orderId}`);
    shippingState.detail = detail;
    const order = detail.order;
    document.getElementById("shipping-detail").hidden = false;
    document.getElementById("shipping-empty").hidden = true;
    document.getElementById("shipping-order-title").textContent = `Zlecenie #${order.order_id}/${order.order_year}`;
    document.getElementById("shipping-order-subtitle").textContent = [order.order_company_name || order.client_company_name, order.device_brand, order.device_model].filter(Boolean).join(" • ");
    document.getElementById("shipping-location").textContent = order.order_location || order.machine_location || "Brak lokalizacji — użyto danych firmy";
    fillShippingAddress(detail.case?.address || detail.preferred_address);
    if (detail.case?.weight_kg) document.getElementById("shipping-weight").value = String(detail.case.weight_kg);
    renderShippingStock();
    applyShippingCase(detail.case);
  } catch (error) {
    shippingFeedback(error.message, true);
  }
}

function selectedShippingItems() {
  return Array.from(document.querySelectorAll("[data-stock-select]:checked")).map((checkbox) => {
    const itemId = Number(checkbox.dataset.stockSelect);
    return {
      firebird_warehouse_item_id: itemId,
      quantity: Number(document.querySelector(`[data-stock-quantity="${itemId}"]`).value),
      remember_for_model: Boolean(document.querySelector(`[data-stock-remember="${itemId}"]`).checked),
    };
  });
}

async function reviewShipping() {
  if (!shippingState.selectedOrderId) return;
  const payload = {
    address: {
      company_name: document.getElementById("shipping-company").value.trim(),
      contact_name: document.getElementById("shipping-contact").value.trim() || null,
      street: document.getElementById("shipping-street").value.trim(),
      postal_code: document.getElementById("shipping-postal").value.trim(),
      city: document.getElementById("shipping-city").value.trim(),
      country_code: "PL",
      phone: document.getElementById("shipping-phone").value.trim(),
      email: document.getElementById("shipping-email").value.trim() || null,
      source: document.getElementById("shipping-address-source").value,
      location_text: shippingState.detail.order.order_location || shippingState.detail.order.machine_location || null,
    },
    weight_kg: Number(document.getElementById("shipping-weight").value),
    items: selectedShippingItems(),
    save_address: document.getElementById("shipping-save-address").checked,
  };
  try {
    const result = await shippingJson(`/admin/shipping/orders/${shippingState.selectedOrderId}/review`, { method: "POST", body: JSON.stringify(payload) });
    shippingState.detail.case = result;
    applyShippingCase(result);
    shippingFeedback("Dane i pozycje zostały zatwierdzone. Można wygenerować etykietę.");
    await loadShippingQueue(false);
  } catch (error) {
    shippingFeedback(error.message, true);
  }
}

async function createShipping(manual = false) {
  if (!shippingState.selectedOrderId) return;
  const payload = { order_table_id: shippingState.selectedOrderId, idempotency_key: crypto.randomUUID() };
  let endpoint = "/admin/shipping/shipments";
  if (manual) {
    const tracking = window.prompt("Wpisz numer przesyłki utworzonej ręcznie w DPD:");
    if (!tracking) return;
    payload.tracking_number = tracking.trim();
    endpoint = "/admin/shipping/shipments/manual-tracking";
  }
  document.getElementById("shipping-create").disabled = true;
  document.getElementById("shipping-manual").disabled = true;
  shippingFeedback(manual ? "Zapisywanie numeru…" : "Tworzenie przesyłki i etykiety DPD…");
  try {
    const result = await shippingJson(endpoint, { method: "POST", body: JSON.stringify(payload) });
    shippingState.detail.case = result;
    applyShippingCase(result);
    await loadShippingQueue(false);
  } catch (error) {
    shippingFeedback(error.message, true);
    applyShippingCase(shippingState.detail.case);
  }
}

async function loadShippingQueue(clearSelection = false) {
  if (clearSelection) shippingState.selectedOrderId = null;
  document.getElementById("shipping-loading").hidden = false;
  try {
    const days = document.getElementById("shipping-days").value;
    const payload = await shippingJson(`/admin/shipping/queue?days=${encodeURIComponent(days)}`);
    shippingState.queue = payload.items || [];
    renderShippingQueue();
  } catch (error) {
    document.getElementById("shipping-alert").textContent = error.message;
    document.getElementById("shipping-alert").hidden = false;
  } finally {
    document.getElementById("shipping-loading").hidden = true;
  }
}

async function closeShippingDay() {
  if (!window.confirm("Potwierdzasz, że kurier fizycznie odebrał wszystkie przygotowane dziś paczki?")) return;
  try {
    const result = await shippingJson("/admin/shipping/day-close", {
      method: "POST",
      body: JSON.stringify({ business_date: new Date().toLocaleDateString("sv-SE"), confirm_handover: true }),
    });
    window.alert(`Zamknięcie dnia: ${result.status}. Zamknięto: ${result.closed_count}, do rozliczenia ręcznego: ${result.manual_billing_count}, błędy: ${result.error_count}.`);
    await loadShippingQueue(true);
  } catch (error) {
    window.alert(error.message);
  }
}

async function initializeShipping() {
  shippingState.token = shippingToken();
  if (!shippingState.token) {
    window.location.replace("/");
    return;
  }
  try {
    const user = await shippingJson("/auth/me");
    if (!Array.isArray(user.sections) || !user.sections.includes("shipping")) throw new Error("Brak uprawnień do wysyłek.");
    document.getElementById("shipping-user").textContent = [user.first_name, user.last_name].filter(Boolean).join(" ") || user.email;
    shippingState.config = await shippingJson("/admin/shipping/config");
    const config = shippingState.config;
    document.getElementById("shipping-dpd-status").textContent = !config.dpd.enabled ? "Wyłączone" : config.dpd.test_mode ? "Tryb testowy" : config.dpd.api_ready ? "Produkcja" : "Brak konfiguracji";
    document.getElementById("shipping-warehouse").textContent = `Magazyn ${config.warehouse_id}`;
    document.getElementById("shipping-cutoff").textContent = new Date(config.courier_cutoff).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
    const weight = document.getElementById("shipping-weight");
    weight.innerHTML = config.weight_presets_kg.map((value) => `<option value="${value}" ${Number(value) === Number(config.default_weight_kg) ? "selected" : ""}>${Number(value).toLocaleString("pl-PL")} kg</option>`).join("");
    if (config.after_cutoff) {
      document.getElementById("shipping-alert").textContent = "Jest po 14:30. Upewnij się, że kurier nie zakończył już dzisiejszego odbioru.";
      document.getElementById("shipping-alert").hidden = false;
    }
    await loadShippingQueue(true);
  } catch (error) {
    document.getElementById("shipping-alert").textContent = error.message;
    document.getElementById("shipping-alert").hidden = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("shipping-refresh").addEventListener("click", () => loadShippingQueue(false));
  document.getElementById("shipping-days").addEventListener("change", () => loadShippingQueue(true));
  document.getElementById("shipping-search").addEventListener("input", renderShippingQueue);
  document.getElementById("shipping-stock-search").addEventListener("input", renderShippingStock);
  document.getElementById("shipping-review").addEventListener("click", reviewShipping);
  document.getElementById("shipping-create").addEventListener("click", () => createShipping(false));
  document.getElementById("shipping-manual").addEventListener("click", () => createShipping(true));
  document.getElementById("shipping-day-close").addEventListener("click", closeShippingDay);
  document.getElementById("shipping-logout").addEventListener("click", async () => {
    await fetch("/auth/logout", { method: "POST", headers: shippingHeaders() }).catch(() => null);
    window.localStorage?.removeItem(SHIPPING_TOKEN_KEY);
    window.sessionStorage?.removeItem(SHIPPING_TOKEN_KEY);
    window.location.replace("/");
  });
  initializeShipping();
});
