const SHIPPING_TOKEN_KEY = "admin-session-token";
const SHIPPING_ORDER_STATE_REFRESH_MS = 30000;

const shippingState = {
  token: null,
  config: null,
  queue: [],
  selectedOrderId: null,
  selectedOrderIds: new Set(),
  printableOrderIds: new Set(),
  bulkBusy: false,
  detail: null,
  liveOrderState: null,
  orderStateRefreshBusy: false,
  runtimeRefreshBusy: false,
  autoRefreshStarted: false,
  selectedItems: new Map(),
  stockScope: "model",
  allowNegativeStock: false,
  priceMode: null,
  selectedAddressKey: "manual",
  catalog: {
    mappings: [],
    page: 1,
    pageSize: 25,
    total: 0,
    stock: [],
    manualItem: null,
    models: [],
    selectedModelIds: new Set(),
  },
  archive: {
    items: [],
    page: 1,
    pageSize: 50,
    total: 0,
    loaded: false,
    operators: [],
    selectedOrderId: null,
  },
  tracking: {
    items: [],
    page: 1,
    pageSize: 50,
    total: 0,
    loaded: false,
    selectedWaybill: null,
  },
};

function shippingCatalogMutationsEnabled() {
  return Boolean(shippingState.config?.shipping?.catalog_mutations_enabled);
}

function shippingFulfillmentEnabled() {
  return Boolean(shippingState.config?.shipping?.fulfillment_enabled);
}

function shippingToken() {
  return window.localStorage?.getItem(SHIPPING_TOKEN_KEY) || window.sessionStorage?.getItem(SHIPPING_TOKEN_KEY) || null;
}

function shippingHeaders(json = false) {
  const headers = shippingState.token ? { "X-Admin-Session": shippingState.token } : {};
  if (json) headers["Content-Type"] = "application/json";
  return headers;
}

function shippingRequestUuid() {
  const cryptoApi = globalThis.crypto;
  if (typeof cryptoApi?.randomUUID === "function") return cryptoApi.randomUUID();
  const bytes = new Uint8Array(16);
  if (typeof cryptoApi?.getRandomValues === "function") {
    cryptoApi.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function escapeShippingHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeShippingUrl(value) {
  try {
    const url = new URL(String(value));
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch (error) {
    return null;
  }
}

function shippingStatusLabel(status) {
  return {
    confirmed: "Potwierdzone",
    suggested: "Sugestia",
    rejected: "Odrzucone",
    stale: "Nieaktualne",
  }[status] || status || "Brak";
}

function shippingCaseStatusLabel(status) {
  return {
    review_pending: "Do weryfikacji",
    ready: "Gotowe do etykiety",
    shipment_created: "Etykieta wygenerowana",
    handed_over: "Przekazane kurierowi",
    closed: "Zamknięte",
    manual_billing: "Do wystawienia FV",
    reconcile_required: "Wymaga uzgodnienia",
  }[status] || "Nieznany etap";
}

function shippingDpdStatusMarkup(tracking, compact = false) {
  if (!tracking) return "";
  const title = [tracking.description, tracking.business_code ? `Kod ${tracking.business_code}` : null]
    .filter(Boolean).join(" • ");
  return `<span class="shipping-dpd-state ${escapeShippingHtml(tracking.category || "other")}" title="${escapeShippingHtml(title)}">${escapeShippingHtml(compact ? tracking.status_label : `${tracking.status_label}${tracking.business_code ? ` · ${tracking.business_code}` : ""}`)}</span>`;
}

function shippingOrderSourceLabel(source) {
  return source === "mobile" ? "Aplikacja mobilna" : "Wpisane ręcznie w MS";
}

function shippingFirebirdStatusLabel(status) {
  return {
    pending: "oczekuje na zapis w MS",
    simulated: "symulacja — bez zapisu w MS",
    written: "zapisano w MS",
    reconcile_required: "wymaga uzgodnienia z MS",
  }[status] || "brak informacji";
}

function shippingDayCloseStatusLabel(status) {
  return {
    completed: "zakończone",
    partial: "zakończone częściowo",
    processing: "w trakcie",
    failed: "nieudane",
  }[status] || "stan nieznany";
}

function shippingOrdersCountLabel(count) {
  const value = Number(count);
  if (value === 1) return "1 zlecenie";
  if (value % 10 >= 2 && value % 10 <= 4 && (value % 100 < 12 || value % 100 > 14)) {
    return `${value} zlecenia`;
  }
  return `${value} zleceń`;
}

function shippingCurrencyLabel(value) {
  return Number(value || 0).toLocaleString("pl-PL", {
    style: "currency",
    currency: "PLN",
  });
}

function shippingDateLabel(value) {
  if (!value) return "brak daty";
  return new Date(`${value}T00:00:00`).toLocaleDateString("pl-PL");
}

function shippingDateTimeLabel(value) {
  if (!value) return "brak daty";
  return new Date(value).toLocaleString("pl-PL", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function shippingDaysOverdueLabel(days) {
  const value = Number(days || 0);
  if (value === 1) return "1 dzień po terminie";
  return `${value} dni po terminie`;
}

function shippingInvoicesCountLabel(count) {
  const value = Number(count || 0);
  if (value === 1) return "1 faktura";
  if (value % 10 >= 2 && value % 10 <= 4 && (value % 100 < 12 || value % 100 > 14)) {
    return `${value} faktury`;
  }
  return `${value} faktur`;
}

function shippingSearchText(value) {
  return String(value ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

function shippingMatchesQuery(values, query) {
  const terms = shippingSearchText(query).split(" ").filter(Boolean);
  if (!terms.length) return true;
  const searchable = shippingSearchText(values.filter(Boolean).join(" "));
  const compactSearchable = searchable.replaceAll(" ", "");
  return terms.every((term) => searchable.includes(term) || compactSearchable.includes(term));
}

function shippingDebounce(callback, delay = 350) {
  let timer = null;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => callback(...args), delay);
  };
}

async function shippingJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { ...shippingHeaders(Boolean(options.body)), ...(options.headers || {}) },
  });
  const payload = await response.json().catch(() => ({}));
  if (response.status === 401) {
    window.location.replace("/");
    throw new Error("Sesja wygasła.");
  }
  if (!response.ok) {
    const detail = Array.isArray(payload.detail)
      ? payload.detail.map((item) => {
        const field = Array.isArray(item.loc) ? item.loc.at(-1) : null;
        const label = {
          company_name: "Firma",
          street: "Ulica i numer",
          postal_code: "Kod pocztowy",
          city: "Miejscowość",
          phone: "Telefon",
          email: "E-mail",
          items: "Wybrane części",
        }[field];
        return label ? `Pole „${label}” jest nieprawidłowe.` : item.msg || "Błąd danych";
      }).join(" ")
      : payload.detail;
    const error = new Error(detail || "Operacja zakończyła się błędem.");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function shippingFeedback(message, error = false) {
  const node = document.getElementById("shipping-feedback");
  node.textContent = message || "";
  node.hidden = !message;
  node.className = `shipping-feedback${error ? " error" : ""}`;
}

function shippingAlert(message, error = false) {
  const node = document.getElementById("shipping-alert");
  node.textContent = message || "";
  node.hidden = !message;
  node.className = `shipping-alert${error ? " error" : ""}`;
}

function shippingQueueDateValue(item) {
  const parsed = Date.parse(item?.order_date || "");
  return Number.isFinite(parsed) ? parsed : 0;
}

function shippingQueueWorkflowRank(item) {
  if (item?.overdue_payment?.has_overdue_invoices) return 3;
  return {
    review_pending: 0,
    reconcile_required: 0,
    ready: 1,
    shipment_created: 2,
    handed_over: 2,
    closed: 2,
    manual_billing: 2,
  }[item?.ctip_status] ?? 0;
}

function sortShippingQueue(items) {
  const sortMode = document.getElementById("shipping-sort")?.value || "workflow";
  return [...items].sort((left, right) => {
    if (sortMode === "workflow") {
      const rankDifference = shippingQueueWorkflowRank(left) - shippingQueueWorkflowRank(right);
      if (rankDifference) return rankDifference;
    }
    if (sortMode === "company") {
      const companyDifference = String(left.company_name || "").localeCompare(
        String(right.company_name || ""),
        "pl",
        { sensitivity: "base" },
      );
      if (companyDifference) return companyDifference;
    }
    const dateDifference = shippingQueueDateValue(left) - shippingQueueDateValue(right);
    if (dateDifference) return sortMode === "newest" ? -dateDifference : dateDifference;
    return Number(left.order_table_id || 0) - Number(right.order_table_id || 0);
  });
}

function renderShippingQueue() {
  const query = document.getElementById("shipping-search").value.trim();
  const queue = sortShippingQueue(shippingState.queue.filter((item) => {
    return shippingMatchesQuery(
      [item.order_id, item.order_year, item.company_name, item.device_brand, item.device_model, item.problem, item.location, item.tracking_number],
      query,
    );
  }));
  document.getElementById("shipping-count").textContent = String(queue.length);
  queue.forEach((item) => {
    const orderTableId = Number(item.order_table_id);
    if (item.label_available) shippingState.printableOrderIds.add(orderTableId);
    else shippingState.printableOrderIds.delete(orderTableId);
  });
  document.getElementById("shipping-queue").innerHTML = queue.length
    ? queue.map((item) => {
      const orderTableId = Number(item.order_table_id);
      const selectable = item.can_generate_label || item.label_available;
      const selectionLabel = item.can_generate_label
        ? "Wybierz do generowania etykiety"
        : item.label_available
          ? "Wybierz do wydruku"
          : "Najpierw zatwierdź dane i części";
      const statusClass = `status-${escapeShippingHtml(item.ctip_status || "review_pending")}`;
      const sourceClass = item.order_source === "mobile" ? "source-mobile" : "source-manual";
      const overduePayment = item.overdue_payment;
      const hasOverduePayment = Boolean(overduePayment?.has_overdue_invoices);
      return `<div class="shipping-queue-entry ${statusClass} ${item.consolidation || item.consolidated_shipment ? "has-consolidation" : ""} ${hasOverduePayment ? "has-overdue-payment" : ""}">
        <label class="shipping-queue-select" title="${escapeShippingHtml(selectionLabel)}">
          <input type="checkbox" data-order-select="${orderTableId}"
            ${shippingState.selectedOrderIds.has(orderTableId) ? "checked" : ""}
            ${selectable ? "" : "disabled"}>
        </label>
        <button type="button" class="shipping-queue-item ${orderTableId === shippingState.selectedOrderId ? "active" : ""}"
                data-order-id="${orderTableId}">
          <span class="shipping-queue-row"><strong>#${escapeShippingHtml(item.order_id)}/${escapeShippingHtml(item.order_year)}</strong><span class="shipping-state">${escapeShippingHtml(shippingCaseStatusLabel(item.ctip_status))}</span></span>
          <span>${escapeShippingHtml(item.company_name || "Bez nazwy klienta")}</span>
          <span class="shipping-queue-meta">
            ${hasOverduePayment ? `<span class="shipping-overdue-badge">Nieopłacone FV: ${Number(overduePayment.invoice_count)} • ${escapeShippingHtml(shippingCurrencyLabel(overduePayment.total_overdue_amount))}</span>` : ""}
            <span class="shipping-source-badge ${sourceClass}">${escapeShippingHtml(shippingOrderSourceLabel(item.order_source))}</span>
            ${item.invoice_required ? '<span class="shipping-invoice-badge">Wystaw FV</span>' : ""}
            ${item.consolidation ? `<span class="shipping-consolidation-badge">Wspólny adres: ${escapeShippingHtml(shippingOrdersCountLabel(item.consolidation.count))}</span>` : ""}
            ${item.consolidated_shipment ? `<span class="shipping-consolidation-badge">Wspólna paczka: ${escapeShippingHtml(shippingOrdersCountLabel(item.consolidated_shipment.count))}</span>` : ""}
            ${shippingDpdStatusMarkup(item.dpd_tracking, true)}
          </span>
          <small>${escapeShippingHtml([item.device_brand, item.device_model].filter(Boolean).join(" ") || "Brak modelu")}</small>
          <small>${escapeShippingHtml(item.problem || "Brak opisu")}</small>
          ${hasOverduePayment ? `<small class="shipping-overdue-note">Najstarsza FV: ${escapeShippingHtml(shippingDaysOverdueLabel(overduePayment.max_days_overdue))}</small>` : ""}
        </button>
      </div>`;
    }).join("")
    : '<p class="shipping-muted">Brak zleceń w wybranym zakresie.</p>';
  document.querySelectorAll("[data-order-id]").forEach((button) => {
    button.addEventListener("click", () => loadShippingDetail(Number(button.dataset.orderId)));
  });
  document.querySelectorAll("[data-order-select]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const orderTableId = Number(checkbox.dataset.orderSelect);
      if (checkbox.checked) shippingState.selectedOrderIds.add(orderTableId);
      else shippingState.selectedOrderIds.delete(orderTableId);
      renderShippingBulkActions();
    });
  });
  renderShippingBulkActions();
  renderShippingConsolidationWarning();
}

function renderShippingConsolidationWarning() {
  const node = document.getElementById("shipping-consolidation-warning");
  if (!node) return;
  const queueItem = shippingState.queue.find((item) => Number(item.order_table_id) === shippingState.selectedOrderId);
  const consolidation = queueItem?.consolidation;
  node.hidden = !consolidation;
  if (!consolidation) return;
  document.getElementById("shipping-consolidation-message").textContent = `Wykryto ${shippingOrdersCountLabel(consolidation.count)} tej samej firmy na identyczny adres: ${consolidation.order_numbers.join(", ")}. Można spakować je w jedną paczkę i wygenerować jeden numer przesyłki.`;
  document.getElementById("shipping-consolidation-address").textContent = `${consolidation.company_name || "Klient"} • ${consolidation.address || "brak adresu"}. Zaznacz grupę, a następnie użyj „Jedna paczka / jedna etykieta”.`;
}

function selectShippingConsolidationGroup() {
  const queueItem = shippingState.queue.find((item) => Number(item.order_table_id) === shippingState.selectedOrderId);
  const consolidation = queueItem?.consolidation;
  if (!consolidation) return;
  shippingState.selectedOrderIds.clear();
  (consolidation.order_table_ids || []).forEach((orderId) => shippingState.selectedOrderIds.add(Number(orderId)));
  renderShippingQueue();
  shippingFeedback(`Zaznaczono ${shippingOrdersCountLabel(consolidation.count)} do jednej wspólnej paczki.`);
}

function renderShippingOverduePayment(payment) {
  const node = document.getElementById("shipping-payment-warning");
  if (!node) return;
  const hasOverdueInvoices = Boolean(payment?.has_overdue_invoices);
  node.hidden = !hasOverdueInvoices;
  if (!hasOverdueInvoices) {
    document.getElementById("shipping-payment-summary").textContent = "";
    document.getElementById("shipping-overdue-invoices").innerHTML = "";
    return;
  }
  document.getElementById("shipping-payment-summary").textContent = `${shippingInvoicesCountLabel(payment.invoice_count)} po terminie • łącznie do zapłaty ${shippingCurrencyLabel(payment.total_overdue_amount)}.`;
  document.getElementById("shipping-overdue-invoices").innerHTML = (payment.invoices || []).map((invoice) => `
    <article>
      <strong>FV ${escapeShippingHtml(invoice.invoice_number || invoice.invoice_id)}</strong>
      <span>Wartość faktury: ${escapeShippingHtml(shippingCurrencyLabel(invoice.amount_gross))}</span>
      <span>Zapłacono: ${escapeShippingHtml(shippingCurrencyLabel(invoice.amount_paid))}</span>
      <span>Do zapłaty: <b>${escapeShippingHtml(shippingCurrencyLabel(invoice.amount_due))}</b></span>
      <span>Termin: ${escapeShippingHtml(shippingDateLabel(invoice.due_date))}</span>
      <span class="shipping-overdue-days">${escapeShippingHtml(shippingDaysOverdueLabel(invoice.days_overdue))}</span>
    </article>
  `).join("");
}

function renderShippingBulkActions() {
  const selectedIds = Array.from(shippingState.selectedOrderIds);
  const selectedItems = shippingState.queue.filter((item) => shippingState.selectedOrderIds.has(Number(item.order_table_id)));
  const readyItems = selectedItems.filter((item) => item.can_generate_label);
  const readyCount = readyItems.length;
  const printableCount = selectedIds.filter((orderId) => shippingState.printableOrderIds.has(orderId)).length;
  const consolidationKeys = new Set(readyItems.map((item) => item.consolidation?.group_key).filter(Boolean));
  const canConsolidate = selectedItems.length >= 2
    && readyItems.length === selectedItems.length
    && readyItems.every((item) => item.consolidation?.group_key)
    && consolidationKeys.size === 1;
  const fulfillmentLocked = !shippingFulfillmentEnabled();
  const consolidationText = canConsolidate ? " • możliwa 1 wspólna paczka" : "";
  document.getElementById("shipping-selected-count").textContent = `${selectedIds.length} wybranych • ${readyCount} do wygenerowania • ${printableCount} do druku${consolidationText}`;
  document.getElementById("shipping-generate-selected").disabled = fulfillmentLocked || shippingState.bulkBusy || readyCount === 0;
  document.getElementById("shipping-generate-consolidated").disabled = fulfillmentLocked || shippingState.bulkBusy || !canConsolidate;
  document.getElementById("shipping-generate-ready").disabled = fulfillmentLocked || shippingState.bulkBusy;
  document.getElementById("shipping-print-selected").disabled = shippingState.bulkBusy || printableCount === 0;
  document.getElementById("shipping-print-packing").disabled = shippingState.bulkBusy || printableCount === 0;
  document.getElementById("shipping-clear-selection").disabled = shippingState.bulkBusy || selectedIds.length === 0;
}

async function generateConsolidatedShipping() {
  const selectedItems = shippingState.queue.filter((item) => shippingState.selectedOrderIds.has(Number(item.order_table_id)));
  const orderTableIds = selectedItems.map((item) => Number(item.order_table_id));
  const consolidationKeys = new Set(selectedItems.map((item) => item.consolidation?.group_key).filter(Boolean));
  if (orderTableIds.length < 2 || selectedItems.some((item) => !item.can_generate_label || !item.consolidation?.group_key) || consolidationKeys.size !== 1) {
    shippingFeedback("Zaznacz co najmniej dwa gotowe zlecenia z tym samym klientem i adresem.", true);
    return;
  }
  const orderNumbers = selectedItems.map((item) => `${item.order_id}/${item.order_year}`);
  if (!window.confirm(`Wygenerować jedną paczkę i jedną etykietę dla zleceń: ${orderNumbers.join(", ")}?`)) return;
  shippingState.bulkBusy = true;
  renderShippingBulkActions();
  shippingFeedback(`Generowanie jednej etykiety dla ${shippingOrdersCountLabel(orderTableIds.length)}…`);
  try {
    const result = await shippingJson("/admin/shipping/shipments/consolidated", {
      method: "POST",
      body: JSON.stringify({ order_table_ids: orderTableIds, idempotency_key: shippingRequestUuid() }),
    });
    (result.printable_order_ids || []).forEach((orderId) => {
      shippingState.selectedOrderIds.add(Number(orderId));
      shippingState.printableOrderIds.add(Number(orderId));
    });
    await loadShippingQueue(false);
    if (shippingState.selectedOrderId && orderTableIds.includes(shippingState.selectedOrderId)) {
      await loadShippingDetail(shippingState.selectedOrderId);
    }
    const warnings = result.primary_case?.shipment?.provider_warnings || [];
    const warningText = warnings.length ? ` Ostrzeżenia etykiety: ${warnings.join(" ")}` : "";
    shippingFeedback(`Utworzono jedną wspólną paczkę dla ${shippingOrdersCountLabel(orderTableIds.length)}. Numer przesyłki: ${result.tracking_number || "brak"}.${warningText}`);
  } catch (error) {
    shippingFeedback(error.message, true);
  } finally {
    shippingState.bulkBusy = false;
    renderShippingBulkActions();
  }
}

async function generateShippingBulk(allReady = false) {
  const orderTableIds = shippingState.queue
    .filter((item) => shippingState.selectedOrderIds.has(Number(item.order_table_id)) && item.can_generate_label)
    .map((item) => Number(item.order_table_id));
  if (!allReady && !orderTableIds.length) {
    shippingFeedback("Zaznacz co najmniej jedno zatwierdzone zlecenie bez etykiety.", true);
    return;
  }
  shippingState.bulkBusy = true;
  renderShippingBulkActions();
  shippingFeedback(allReady ? "Generowanie etykiet dla wszystkich gotowych zleceń…" : `Generowanie ${orderTableIds.length} etykiet…`);
  try {
    const result = await shippingJson("/admin/shipping/shipments/bulk", {
      method: "POST",
      body: JSON.stringify({ order_table_ids: allReady ? [] : orderTableIds, all_ready: allReady }),
    });
    (result.printable_order_ids || []).forEach((orderId) => {
      shippingState.selectedOrderIds.add(Number(orderId));
      shippingState.printableOrderIds.add(Number(orderId));
    });
    await loadShippingQueue(false);
    if (shippingState.selectedOrderId && (result.printable_order_ids || []).includes(shippingState.selectedOrderId)) {
      await loadShippingDetail(shippingState.selectedOrderId);
    }
    const errors = result.errors || [];
    const warnings = result.warnings || [];
    const errorText = errors.length ? ` Błędy: ${errors.map((item) => `${item.order_table_id}: ${item.error}`).join("; ")}` : "";
    const warningText = warnings.length ? ` Ostrzeżenia etykiet: ${warnings.join(" ")}` : "";
    shippingFeedback(`Wygenerowano etykiety: ${result.created_count}/${result.requested_count}.${errorText}${warningText}`, errors.length > 0);
  } catch (error) {
    shippingFeedback(error.message, true);
  } finally {
    shippingState.bulkBusy = false;
    renderShippingBulkActions();
  }
}

function printSelectedShippingDocument(path, label) {
  const orderTableIds = Array.from(shippingState.selectedOrderIds).filter((orderId) => shippingState.printableOrderIds.has(orderId));
  if (!orderTableIds.length) {
    shippingFeedback("Zaznacz zlecenia z wygenerowanymi etykietami.", true);
    return;
  }
  const url = `${path}?order_table_ids=${encodeURIComponent(orderTableIds.join(","))}`;
  const printWindow = window.open(url, "_blank", "noopener");
  if (!printWindow) {
    shippingFeedback("Przeglądarka zablokowała okno wydruku. Zezwól na wyskakujące okna dla CTIP.", true);
    return;
  }
  shippingFeedback(`Otwarto osobny dokument: ${label}.`);
}

function printSelectedShippingLabels() {
  printSelectedShippingDocument("/admin/shipping/shipments/labels-sheet", "etykiety DPD");
}

function printSelectedShippingPackingList() {
  printSelectedShippingDocument("/admin/shipping/shipments/packing-list", "lista części do paczek");
}

async function runDpdDemoDiagnostic() {
  if (!window.confirm("Utworzyć jedną przesyłkę testową Ksero-Partner → Ksero-Partner w środowisku DPD Demo?")) return;
  const preview = window.open("", "_blank");
  if (!preview) {
    shippingAlert("Przeglądarka zablokowała okno etykiety testowej.", true);
    return;
  }
  try {
    const response = await fetch("/admin/shipping/dpd/demo-diagnostic", {
      method: "POST",
      headers: shippingHeaders(),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "Test DPD Demo zakończył się błędem.");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    preview.location.replace(url);
    window.setTimeout(() => URL.revokeObjectURL(url), 60000);
    const waybill = response.headers.get("X-DPD-Waybill") || "brak numeru";
    shippingAlert(`DPD Demo zwróciło etykietę. Numer testowy: ${waybill}.`);
  } catch (error) {
    preview.close();
    shippingAlert(error.message, true);
  }
}

function clearShippingSelection() {
  shippingState.selectedOrderIds.clear();
  renderShippingQueue();
}

function fillShippingAddress(address, preserveExisting = false) {
  const fields = [
    ["shipping-company", "company_name"],
    ["shipping-contact", "contact_name"],
    ["shipping-street", "street"],
    ["shipping-postal", "postal_code"],
    ["shipping-city", "city"],
    ["shipping-phone", "phone"],
    ["shipping-email", "email"],
  ];
  fields.forEach(([inputId, addressField]) => {
    const incomingValue = String(address?.[addressField] || "").trim();
    if (incomingValue || !preserveExisting) {
      document.getElementById(inputId).value = incomingValue;
    }
  });
  const containsContactData = [address?.contact_name, address?.phone, address?.email].some((value) => String(value || "").trim());
  if (!preserveExisting || containsContactData) {
    document.getElementById("shipping-contact-select").value = "";
  }
  document.querySelectorAll(".shipping-form-grid input.invalid").forEach((input) => {
    input.classList.remove("invalid");
    input.removeAttribute("aria-invalid");
  });
  document.getElementById("shipping-address-source").value = address?.source || "manual";
  document.getElementById("shipping-address-source-label").textContent = {
    location: "Lokalizacja urządzenia",
    saved: "Zweryfikowany adres",
    order: "Dane zlecenia / oddziału",
    client: "Kartoteka klienta",
    manual: "Wpisano ręcznie",
  }[address?.source] || "Wpisano ręcznie";
}

function fillShippingContact(contact) {
  [
    ["shipping-contact", contact?.name],
    ["shipping-phone", contact?.phone],
    ["shipping-email", contact?.email],
  ].forEach(([inputId, value]) => {
    const incomingValue = String(value || "").trim();
    if (incomingValue) document.getElementById(inputId).value = incomingValue;
  });
  ["shipping-contact", "shipping-phone", "shipping-email"].forEach((id) => {
    const input = document.getElementById(id);
    input.classList.remove("invalid");
    input.removeAttribute("aria-invalid");
  });
}

function renderShippingContacts() {
  const contacts = shippingState.detail?.order?.contacts || [];
  const mobileContact = shippingState.detail?.order?.mobile_contact || null;
  const problemPhone = String(shippingState.detail?.order?.problem_phone || "").trim();
  const select = document.getElementById("shipping-contact-select");
  const note = document.getElementById("shipping-contact-note");
  const phoneNote = document.getElementById("shipping-phone-note");
  phoneNote.textContent = "";
  select.innerHTML = "";
  const manualOption = document.createElement("option");
  manualOption.value = "";
  manualOption.textContent = contacts.length ? "Wpisz dane ręcznie" : "Brak aktywnych kontaktów w MS";
  select.appendChild(manualOption);
  contacts.forEach((contact) => {
    const option = document.createElement("option");
    option.value = String(contact.id);
    option.textContent = [
      contact.name,
      contact.role,
      contact.phone,
      contact.email,
      contact.is_mobile_user ? "użytkownik aplikacji" : null,
    ].filter(Boolean).join(" • ");
    select.appendChild(option);
  });
  select.disabled = !contacts.length;
  note.textContent = contacts.length
    ? "Wybór kontaktu uzupełni osobę, telefon i e-mail."
    : "Klient nie ma aktywnych osób kontaktowych w MS.";
  if (mobileContact) {
    select.value = String(mobileContact.id);
    fillShippingContact(mobileContact);
    note.textContent = "Telefon i e-mail pobrano z użytkownika aplikacji mobilnej.";
    if (mobileContact.phone) phoneNote.textContent = "Telefon pobrano z użytkownika aplikacji mobilnej.";
  }
  if (problemPhone) {
    document.getElementById("shipping-phone").value = problemPhone;
    phoneNote.textContent = "Telefon pobrano z treści zlecenia.";
  }
}

function applyShippingContact(contactId) {
  const contact = (shippingState.detail?.order?.contacts || []).find((item) => String(item.id) === String(contactId));
  if (!contact) {
    markShippingAddressManual();
    return;
  }
  fillShippingContact(contact);
  markShippingAddressManual();
  document.getElementById("shipping-contact-select").value = String(contact.id);
  document.getElementById("shipping-phone-note").textContent = contact.phone
    ? "Telefon pobrano z wybranego kontaktu MS."
    : "";
}

function shippingAddressCandidateLine(candidate) {
  if (!candidate.address) return String(candidate.description || "").trim();
  return [
    candidate.address.street,
    `${candidate.address.postal_code || ""} ${candidate.address.city || ""}`.trim(),
  ].filter(Boolean).join(", ");
}

function renderShippingAddressCandidates() {
  const candidates = (shippingState.detail?.address_candidates || []).filter((candidate) => shippingAddressCandidateLine(candidate));
  const container = document.getElementById("shipping-address-candidates");
  container.innerHTML = candidates.length
    ? candidates.map((candidate) => {
      const addressLine = shippingAddressCandidateLine(candidate);
      const usable = Boolean((candidate.usable ?? candidate.selectable) && candidate.address);
      return `<article class="shipping-address-candidate ${candidate.key === shippingState.selectedAddressKey ? "active" : ""}">
        <strong>${escapeShippingHtml(candidate.label)}</strong>
        <span>${escapeShippingHtml(addressLine)}</span>
        <small>${escapeShippingHtml(candidate.description || "")}</small>
        ${candidate.warning ? `<small class="warning ${usable ? "partial" : ""}">${escapeShippingHtml(candidate.warning)}</small>` : ""}
        ${usable ? `<button type="button" class="shipping-button secondary compact shipping-address-use" data-address-candidate="${escapeShippingHtml(candidate.key)}">Użyj danych</button>` : ""}
      </article>`;
    }).join("")
    : '<p class="shipping-muted">Brak źródeł adresu. Wprowadź dane ręcznie.</p>';
  container.querySelectorAll("[data-address-candidate]").forEach((button) => {
    button.addEventListener("click", () => {
      const candidate = candidates.find((item) => item.key === button.dataset.addressCandidate);
      if (!(candidate?.usable ?? candidate?.selectable) || !candidate.address) return;
      shippingState.selectedAddressKey = candidate.key;
      fillShippingAddress(candidate.address, true);
      if (String(candidate.address.phone || "").trim()) {
        document.getElementById("shipping-phone-note").textContent = "Telefon pobrano z wybranego źródła adresu.";
      }
      renderShippingAddressCandidates();
      shippingFeedback("Użyto dostępnych danych źródłowych. Puste wartości nie zastąpiły informacji wpisanych już w formularzu.");
    });
  });
}

function renderShippingLocationContext() {
  const context = shippingState.detail?.location_context || {};
  const node = document.getElementById("shipping-location-warning");
  document.getElementById("shipping-location-title").textContent = context.source_label || "Lokalizacja urządzenia w MS";
  document.getElementById("shipping-location").textContent = context.machine_text || context.current_text || "Brak lokalizacji";
  document.getElementById("shipping-order-location").textContent = `Lokalizacja zapisana na zleceniu: ${context.order_text || "brak"}`;
  let message = "Lokalizacja jest zgodna z ostatnią akceptacją.";
  let stateClass = "safe";
  if (context.case_location_changed) {
    message = "Lokalizacja zmieniła się po akceptacji. Poprzedni adres został zablokowany; wymagana jest ponowna weryfikacja.";
    stateClass = "blocked";
  } else if (context.machine_differs_from_order) {
    message = "Bieżąca lokalizacja urządzenia różni się od lokalizacji zapisanej na zleceniu. Zweryfikuj wybrane źródło adresu.";
    stateClass = "";
  } else if (!context.verifiable) {
    message = "MS nie zawiera lokalizacji urządzenia. Użyj kompletnego adresu firmy i zweryfikuj go ręcznie.";
    stateClass = "";
  } else if (!context.reviewed_for_current_location) {
    message = "Ta wersja lokalizacji nie została jeszcze zweryfikowana w CTIP.";
    stateClass = "";
  }
  const themeClass = document.body.classList.contains("shipping-v2-body") ? "shipping-v2-location-note" : "";
  node.className = `shipping-source-note ${themeClass} ${stateClass}`.trim();
  document.getElementById("shipping-location-message").textContent = message;
}

function markShippingAddressManual(event) {
  if (event?.currentTarget) {
    event.currentTarget.classList.remove("invalid");
    event.currentTarget.removeAttribute("aria-invalid");
  }
  shippingState.selectedAddressKey = "manual";
  if (["shipping-contact", "shipping-phone", "shipping-email"].includes(event?.currentTarget?.id)) {
    document.getElementById("shipping-contact-select").value = "";
  }
  if (event?.currentTarget?.id === "shipping-phone") {
    document.getElementById("shipping-phone-note").textContent = "";
  }
  document.getElementById("shipping-address-source").value = "manual";
  document.getElementById("shipping-address-source-label").textContent = "Wpisano ręcznie";
  renderShippingAddressCandidates();
}

function shippingPriceMode() {
  const order = shippingState.detail?.order || {};
  const contractOrder = String(order.order_kind || "").trim().toLowerCase() === "umowa";
  const invoiceRequired = Boolean(document.getElementById("shipping-invoice-required")?.checked);
  return !contractOrder || invoiceRequired ? "sale" : "purchase";
}

function shippingDocumentMode() {
  const order = shippingState.detail?.order || {};
  const contractOrder = String(order.order_kind || "").trim().toLowerCase() === "umowa";
  const invoiceRequired = Boolean(document.getElementById("shipping-invoice-required")?.checked);
  return invoiceRequired ? "invoice_wz" : contractOrder ? "rw" : "wz";
}

function updateShippingDocumentMode() {
  document.body.dataset.shippingDocumentMode = shippingDocumentMode();
}

function shippingDefaultItemPricing(item) {
  const catalogPrice = Number(item?.price_net || 0);
  const purchasePrice = Number(item?.purchase_price_net || 0);
  if (shippingPriceMode() === "purchase") {
    return { value: purchasePrice, source: "purchase_contract" };
  }
  return catalogPrice > 0
    ? { value: catalogPrice, source: "sale" }
    : { value: purchasePrice, source: "purchase_fallback" };
}

function shippingItemPricing(chosen, item) {
  const fallback = shippingDefaultItemPricing(item);
  if (!chosen) return fallback;
  const source = String(chosen.price_source || "");
  const compatibleSource = shippingPriceMode() === "purchase"
    ? source === "purchase_contract"
    : source !== "purchase_contract";
  const chosenValue = Number(chosen.unit_price_net ?? chosen.price_net ?? 0);
  return compatibleSource && chosenValue > 0
    ? { value: chosenValue, source: source || fallback.source }
    : fallback;
}

function shippingPriceSourceLabel(source) {
  return {
    sale: "Cena sprzedaży z MS",
    purchase_fallback: "Brak ceny sprzedaży — użyto ceny zakupu",
    purchase_contract: "Cena zakupu do RW",
    manual: "Cena zmieniona ręcznie",
  }[source] || "Cena netto";
}

function resetSelectedShippingPrices() {
  shippingState.selectedItems.forEach((selected, itemId) => {
    const stockItem = (shippingState.detail?.stock || []).find((item) => Number(item.warehouse_item_id) === itemId) || selected.stockItem;
    const pricing = shippingDefaultItemPricing(stockItem);
    selected.unit_price_net = pricing.value;
    selected.price_net = pricing.value;
    selected.price_source = pricing.source;
  });
}

function bindShippingStockRows() {
  document.querySelectorAll("[data-stock-select]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const itemId = Number(checkbox.dataset.stockSelect);
      if (!checkbox.checked) {
        shippingState.selectedItems.delete(itemId);
        return;
      }
      const stockItem = (shippingState.detail?.stock || []).find((item) => Number(item.warehouse_item_id) === itemId);
      const pricing = shippingItemPricing(null, stockItem);
      const zeroStock = Number(stockItem?.available_quantity || 0) <= 0;
      if (zeroStock && !shippingState.allowNegativeStock) {
        checkbox.checked = false;
        shippingFeedback("Najpierw potwierdź zezwolenie na ujemny stan magazynowy.", true);
        return;
      }
      shippingState.selectedItems.set(itemId, {
        firebird_warehouse_item_id: itemId,
        quantity: Number(document.querySelector(`[data-stock-quantity="${itemId}"]`).value),
        unit_price_net: Number(document.querySelector(`[data-stock-price="${itemId}"]`).value),
        price_net: Number(document.querySelector(`[data-stock-price="${itemId}"]`).value),
        price_source: pricing.source,
        remember_for_model: Boolean(document.querySelector(`[data-stock-remember="${itemId}"]`).checked),
        allow_negative_stock: zeroStock && shippingState.allowNegativeStock,
        stockItem,
      });
    });
  });
  document.querySelectorAll("[data-stock-quantity], [data-stock-price], [data-stock-remember]").forEach((input) => {
    input.addEventListener("change", () => {
      const itemId = Number(input.dataset.stockQuantity || input.dataset.stockPrice || input.dataset.stockRemember);
      const selected = shippingState.selectedItems.get(itemId);
      if (!selected) return;
      selected.quantity = Number(document.querySelector(`[data-stock-quantity="${itemId}"]`).value);
      selected.unit_price_net = Number(document.querySelector(`[data-stock-price="${itemId}"]`).value);
      selected.price_net = selected.unit_price_net;
      if (input.dataset.stockPrice) selected.price_source = "manual";
      selected.remember_for_model = Boolean(document.querySelector(`[data-stock-remember="${itemId}"]`).checked);
      if (input.dataset.stockPrice) renderShippingStock();
    });
  });
}

function renderShippingStock() {
  const rows = shippingState.detail?.stock || [];
  const order = shippingState.detail?.order || {};
  const modelLabel = [order.device_brand || order.machine_brand, order.device_model || order.machine_model].filter(Boolean).join(" ");
  document.getElementById("shipping-stock-scope-note").textContent = shippingState.stockScope === "model"
    ? `Zakres: wyłącznie potwierdzone części dla modelu ${modelLabel || "bez rozpoznanego modelu"}. Wyszukiwanie nie rozszerza zakresu.`
    : "Zakres: wszystkie fizyczne części i towary z magazynu głównego. Zgodność z modelem wymaga potwierdzenia.";
  document.getElementById("shipping-stock").innerHTML = rows.length
    ? rows.map((item) => {
      const itemId = Number(item.warehouse_item_id);
      const chosen = shippingState.selectedItems.get(itemId);
      const pricing = shippingItemPricing(chosen, item);
      const editablePrice = shippingPriceMode() === "sale";
      const priceWarning = pricing.source === "purchase_fallback";
      const physicalAvailable = Number(item.available_quantity || 0);
      const available = Number(item.available_after_soft_reservations || 0);
      const zeroStock = physicalAvailable <= 0;
      const zeroAllowed = zeroStock && shippingState.allowNegativeStock;
      const quantityMaximum = zeroAllowed ? 100 : Math.max(available, Number(chosen?.quantity || 1));
      const negativeStockBadge = zeroStock
        ? `<br><span class="shipping-negative-stock-badge ${zeroAllowed ? "allowed" : ""}">${zeroAllowed ? "UJEMNY STAN DOZWOLONY" : "STAN ZEROWY — WYMAGA ZGODY"}</span>`
        : "";
      return `<tr data-stock-row="${itemId}" data-stock-item-index="${escapeShippingHtml(item.item_index || "—")}" data-stock-item-name="${escapeShippingHtml(item.item_name)}" data-stock-item-unit="${escapeShippingHtml(item.unit || "szt.")}" class="${chosen?.allow_negative_stock ? "shipping-negative-stock-row" : ""} ${priceWarning ? "shipping-purchase-fallback-row" : ""}">
        <td><input type="checkbox" data-stock-select="${itemId}" ${chosen ? "checked" : ""} ${available <= 0 && !chosen && !zeroAllowed ? "disabled" : ""}></td>
        <td><strong>${escapeShippingHtml(item.item_index || "—")}</strong><br>${escapeShippingHtml(item.item_name)}${item.compatible ? '<br><span class="shipping-compatible">POTWIERDZONY DLA MODELU</span>' : ""}${negativeStockBadge}</td>
        <td>${available.toLocaleString("pl-PL", { maximumFractionDigits: 3 })} ${escapeShippingHtml(item.unit || "szt.")}</td>
        <td><input type="number" min="0.001" max="${quantityMaximum}" step="1" value="${chosen?.quantity || 1}" data-stock-quantity="${itemId}"></td>
        <td class="shipping-price-cell"><input type="number" min="0.01" max="1000000" step="0.01" value="${Number(pricing.value || 0).toFixed(2)}" data-stock-price="${itemId}" ${editablePrice ? "" : "readonly"}><small>netto / ${escapeShippingHtml(item.unit || "szt.")}</small><span class="shipping-price-source ${priceWarning ? "warning" : ""}">${escapeShippingHtml(shippingPriceSourceLabel(pricing.source))}</span></td>
        <td><input type="checkbox" data-stock-remember="${itemId}" ${chosen?.remember_for_model || item.compatible ? "checked" : ""}></td>
      </tr>`;
    }).join("")
    : `<tr><td colspan="6" class="shipping-muted">${shippingState.stockScope === "model"
      ? 'Brak potwierdzonych części dla tego modelu i zapytania. Użyj przełącznika „Cały magazyn”, aby rozszerzyć zakres.'
      : "Brak części w całym magazynie dla podanego zapytania."}</td></tr>`;
  bindShippingStockRows();
}

function applyShippingStockScope() {
  document.querySelectorAll("[data-stock-scope]").forEach((button) => {
    const active = button.dataset.stockScope === shippingState.stockScope;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

async function setShippingStockScope(scope) {
  shippingState.stockScope = scope === "all" ? "all" : "model";
  applyShippingStockScope();
  await searchShippingStock();
}

async function searchShippingStock() {
  if (!shippingState.detail?.order) return;
  const query = document.getElementById("shipping-stock-search").value.trim();
  const modelId = shippingState.detail.order.model_id;
  const params = new URLSearchParams({
    compatible_only: shippingState.stockScope === "model" ? "true" : "false",
    only_available: "false",
    limit: "200",
  });
  if (modelId) params.set("model_id", String(modelId));
  if (query) params.set("query", query);
  try {
    const payload = await shippingJson(`/admin/shipping/stock?${params.toString()}`);
    const oldStock = shippingState.detail.stock || [];
    const byId = new Map((payload.items || []).map((item) => [Number(item.warehouse_item_id), item]));
    shippingState.selectedItems.forEach((selected, itemId) => {
      const previous = oldStock.find((item) => Number(item.warehouse_item_id) === itemId) || selected.stockItem;
      if (previous && !byId.has(itemId)) byId.set(itemId, previous);
    });
    shippingState.detail.stock = Array.from(byId.values());
    renderShippingStock();
  } catch (error) {
    shippingFeedback(error.message, true);
  }
}

function applyShippingCase(caseData, showTrackingFeedback = true) {
  const status = caseData?.status || "review_pending";
  const badge = document.getElementById("shipping-case-status");
  badge.textContent = shippingCaseStatusLabel(status);
  badge.className = `shipping-badge shipping-case-status status-${status}`;
  const ready = status === "ready";
  const shipment = caseData?.shipment;
  const locationBlocked = Boolean(shippingState.detail?.location_context?.case_location_changed && !shipment);
  const orderState = shippingState.liveOrderState;
  const trackingMatches = !orderState || !shipment?.tracking_number
    || orderState.tracking_number === shipment.tracking_number;
  const fulfillmentLocked = !shippingFulfillmentEnabled();
  document.getElementById("shipping-review").disabled = fulfillmentLocked || Boolean(shipment)
    || Boolean(orderState && !orderState.can_review);
  document.getElementById("shipping-create").disabled = fulfillmentLocked || !ready || locationBlocked
    || Boolean(orderState && !orderState.can_prepare_shipment);
  document.getElementById("shipping-manual").disabled = fulfillmentLocked || !ready || locationBlocked
    || Boolean(orderState && !orderState.can_prepare_shipment);
  document.getElementById("shipping-close-order").disabled = fulfillmentLocked || status !== "shipment_created"
    || !shipment?.tracking_number
    || Boolean(orderState && (!orderState.can_finalize || !trackingMatches));
  const label = document.getElementById("shipping-label");
  label.hidden = !shipment?.label_available;
  label.href = shipment?.label_available ? `/admin/shipping/shipments/${shipment.id}/label` : "#";
  const trackingButton = document.getElementById("shipping-tracking-open");
  trackingButton.hidden = !shipment?.tracking_number;
  trackingButton.dataset.waybill = shipment?.tracking_number || "";
  if (showTrackingFeedback && shipment?.tracking_number) {
    const warnings = Array.isArray(shipment.provider_warnings) ? shipment.provider_warnings : [];
    const warningText = warnings.length ? ` Ostrzeżenia etykiety: ${warnings.join(" ")}` : "";
    shippingFeedback(`Numer przesyłki: ${shipment.tracking_number}. Menadżer Serwisu: ${shippingFirebirdStatusLabel(shipment.firebird_status)}.${warningText}`);
  }
}

function shippingOrderStateWarningMessage(orderState) {
  if (!orderState) return "";
  const orderNumber = `${orderState.order_id}/${orderState.order_year}`;
  const shipment = shippingState.detail?.case?.shipment;
  const localTracking = shipment?.tracking_number || null;
  if (orderState.completed) {
    return `Zlecenie #${orderNumber} zostało już zrealizowane w MS. CTIP nie utworzy FV, WZ ani RW i nie zmieni stanu magazynu.`;
  }
  if (orderState.has_assigned_technician) {
    return `Zlecenie ma przypisanego technika: ${orderState.assigned_technician}. Materiał dostarcza pracownik, dlatego wysyłka magazynowa jest zablokowana.`;
  }
  if (!orderState.eligible_for_shipping) {
    return `Stan zlecenia w MS to „${orderState.status_label}” (${orderState.status || "brak"}). Zlecenie nie spełnia warunków kolejki wysyłkowej.`;
  }
  if (localTracking && orderState.status !== "ZR") {
    return `Etykieta ${localTracking} istnieje w CTIP, ale stan zlecenia w MS to „${orderState.status_label}”. Wymagane jest ręczne uzgodnienie.`;
  }
  if (localTracking && orderState.tracking_number !== localTracking) {
    return `Numer przesyłki w MS (${orderState.tracking_number || "brak"}) różni się od etykiety CTIP (${localTracking}). Wymagane jest ręczne uzgodnienie.`;
  }
  if (!localTracking && orderState.tracking_number) {
    return `MS zawiera już numer przesyłki ${orderState.tracking_number}, którego nie ma w bieżącej sprawie CTIP. Wymagane jest ręczne uzgodnienie.`;
  }
  return "";
}

function renderShippingOrderState(orderState) {
  shippingState.liveOrderState = orderState || null;
  const warning = document.getElementById("shipping-order-state-warning");
  const message = shippingOrderStateWarningMessage(orderState);
  warning.hidden = !message;
  document.getElementById("shipping-order-state-message").textContent = message;
  if (shippingState.detail) applyShippingCase(shippingState.detail.case, false);
}

async function refreshShippingOrderState(showFeedback = false) {
  if (!shippingState.selectedOrderId || shippingState.orderStateRefreshBusy) {
    return shippingState.liveOrderState;
  }
  const orderId = shippingState.selectedOrderId;
  shippingState.orderStateRefreshBusy = true;
  try {
    const orderState = await shippingJson(`/admin/shipping/orders/${orderId}/state`);
    if (shippingState.selectedOrderId !== orderId) return shippingState.liveOrderState;
    renderShippingOrderState(orderState);
    return orderState;
  } catch (error) {
    if (showFeedback) shippingFeedback(`Nie udało się potwierdzić bieżącego stanu zlecenia: ${error.message}`, true);
    return null;
  } finally {
    shippingState.orderStateRefreshBusy = false;
  }
}

async function ensureShippingOrderState(action) {
  const orderState = await refreshShippingOrderState(true);
  if (!orderState) return false;
  const allowed = {
    review: orderState.can_review,
    create: orderState.can_prepare_shipment,
    close: orderState.can_finalize,
  }[action];
  const warningMessage = shippingOrderStateWarningMessage(orderState);
  if (allowed && !warningMessage) return true;
  const operationLabel = {
    review: "zatwierdzenie danych",
    create: "generowanie etykiety",
    close: "zamknięcie zlecenia",
  }[action] || "operacja";
  shippingFeedback(warningMessage || `Bieżący stan MS nie pozwala na: ${operationLabel}.`, true);
  return false;
}

function applyShippingBilling(order, caseData) {
  const invoiceInput = document.getElementById("shipping-invoice-required");
  const note = document.getElementById("shipping-invoice-note");
  const contractOrder = String(order?.order_kind || "").trim().toLowerCase() === "umowa";
  invoiceInput.checked = caseData ? Boolean(caseData.invoice_required) : !contractOrder;
  invoiceInput.disabled = Boolean(caseData?.shipment);
  shippingState.priceMode = shippingPriceMode();
  updateShippingDocumentMode();
  note.textContent = contractOrder
    ? "Zaznacz, jeżeli zlecenie umowne ma zostać rozliczone fakturą zamiast dokumentem RW."
    : "Dla zlecenia poza umową FV jest zaznaczona domyślnie. Odznaczenie zakończy wysyłkę dokumentem WZ bez faktury.";
}

async function loadShippingDetail(orderId) {
  shippingState.selectedOrderId = orderId;
  shippingState.liveOrderState = null;
  renderShippingQueue();
  renderShippingOverduePayment(null);
  renderShippingOrderState(null);
  shippingFeedback("");
  try {
    const detail = await shippingJson(`/admin/shipping/orders/${orderId}`);
    shippingState.detail = detail;
    shippingState.selectedItems = new Map((detail.case?.items || []).map((item) => [
      Number(item.firebird_warehouse_item_id),
      {
        ...item,
        quantity: Number(item.quantity),
        unit_price_net: Number(item.price_net),
        remember_for_model: false,
        allow_negative_stock: Boolean(item.allow_negative_stock),
      },
    ]));
    shippingState.allowNegativeStock = Array.from(shippingState.selectedItems.values()).some((item) => item.allow_negative_stock);
    document.getElementById("shipping-allow-negative-stock").checked = shippingState.allowNegativeStock;
    const order = detail.order;
    document.getElementById("shipping-detail").hidden = false;
    document.getElementById("shipping-empty").hidden = true;
    document.getElementById("shipping-order-title").textContent = `Zlecenie #${order.order_id}/${order.order_year}`;
    document.getElementById("shipping-order-subtitle").textContent = [order.order_company_name || order.client_company_name, order.device_brand, order.device_model].filter(Boolean).join(" • ");
    document.getElementById("shipping-order-problem").textContent = order.problem || "Brak treści zlecenia.";
    renderShippingOverduePayment(detail.overdue_payment);
    document.getElementById("shipping-stock-search").value = "";
    shippingState.stockScope = order.model_id ? "model" : "all";
    shippingState.selectedAddressKey = detail.preferred_address_key || "manual";
    applyShippingStockScope();
    renderShippingLocationContext();
    renderShippingAddressCandidates();
    fillShippingAddress(detail.preferred_address);
    renderShippingContacts();
    renderShippingConsolidationWarning();
    applyShippingBilling(order, detail.case);
    if (detail.case?.weight_kg) document.getElementById("shipping-weight").value = String(detail.case.weight_kg);
    renderShippingStock();
    applyShippingCase(detail.case);
    renderShippingOrderState(detail.order_state);
  } catch (error) {
    shippingFeedback(error.message, true);
  }
}

function selectedShippingItems() {
  return Array.from(shippingState.selectedItems.values()).map((item) => ({
    firebird_warehouse_item_id: item.firebird_warehouse_item_id,
    quantity: Number(item.quantity),
    unit_price_net: Number(item.unit_price_net ?? item.price_net),
    remember_for_model: Boolean(item.remember_for_model),
    allow_negative_stock: Boolean(item.allow_negative_stock),
  }));
}

function shippingAddressFormData() {
  const postalInput = document.getElementById("shipping-postal");
  const postalDigits = postalInput.value.replace(/\D/g, "");
  if (postalDigits.length === 5) {
    postalInput.value = `${postalDigits.slice(0, 2)}-${postalDigits.slice(2)}`;
  }
  return {
    company_name: document.getElementById("shipping-company").value.trim(),
    contact_name: document.getElementById("shipping-contact").value.trim() || null,
    street: document.getElementById("shipping-street").value.trim(),
    postal_code: postalInput.value.trim(),
    city: document.getElementById("shipping-city").value.trim(),
    country_code: "PL",
    phone: document.getElementById("shipping-phone").value.trim(),
    email: document.getElementById("shipping-email").value.trim() || null,
    source: document.getElementById("shipping-address-source").value,
    location_text: shippingState.detail.location_context?.current_text || null,
  };
}

function validateShippingAddress(address) {
  const checks = [
    ["shipping-company", "firma", address.company_name.length >= 2],
    ["shipping-street", "ulica i numer", address.street.length >= 3],
    ["shipping-postal", "kod pocztowy w formacie 00-000", /^\d{2}-\d{3}$/.test(address.postal_code)],
    ["shipping-city", "miejscowość", address.city.length >= 2],
    ["shipping-phone", "telefon odbiorcy", address.phone.replace(/\D/g, "").length >= 9],
    ["shipping-email", "poprawny adres e-mail", !address.email || document.getElementById("shipping-email").checkValidity()],
  ];
  const invalid = checks.filter(([, , valid]) => !valid);
  checks.forEach(([id, , valid]) => {
    const input = document.getElementById(id);
    input.classList.toggle("invalid", !valid);
    if (valid) input.removeAttribute("aria-invalid");
    else input.setAttribute("aria-invalid", "true");
  });
  if (!invalid.length) return true;
  shippingFeedback(`Uzupełnij dane odbiorcy: ${invalid.map(([, label]) => label).join(", ")}.`, true);
  document.getElementById(invalid[0][0]).focus();
  document.getElementById(invalid[0][0]).scrollIntoView({ behavior: "smooth", block: "center" });
  return false;
}

async function reviewShipping() {
  if (!shippingState.selectedOrderId) return;
  if (!await ensureShippingOrderState("review")) return;
  const address = shippingAddressFormData();
  if (!validateShippingAddress(address)) return;
  const items = selectedShippingItems();
  if (!items.length) {
    shippingFeedback("Wybierz co najmniej jedną część i podaj jej ilość.", true);
    document.getElementById("shipping-stock-search").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  if (items.some((item) => !Number.isFinite(item.unit_price_net) || item.unit_price_net <= 0)) {
    shippingFeedback("Uzupełnij prawidłową cenę netto każdej wybranej części.", true);
    document.getElementById("shipping-stock-search").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  const payload = {
    address,
    location_fingerprint: shippingState.detail.location_context.fingerprint,
    weight_kg: Number(document.getElementById("shipping-weight").value),
    items,
    save_address: document.getElementById("shipping-save-address").checked,
    invoice_required: document.getElementById("shipping-invoice-required").checked,
  };
  try {
    const result = await shippingJson(`/admin/shipping/orders/${shippingState.selectedOrderId}/review`, { method: "POST", body: JSON.stringify(payload) });
    shippingState.detail.case = result;
    shippingState.detail.location_context.case_location_changed = false;
    shippingState.detail.location_context.reviewed_for_current_location = true;
    renderShippingLocationContext();
    applyShippingCase(result);
    applyShippingBilling(shippingState.detail.order, result);
    shippingFeedback("Dane i pozycje zostały zatwierdzone. Można wygenerować etykietę.");
    await loadShippingQueue(false);
  } catch (error) {
    if (error.status === 409) await loadShippingDetail(shippingState.selectedOrderId);
    shippingFeedback(error.message, true);
  }
}

async function createShipping(manual = false) {
  if (!shippingState.selectedOrderId) return;
  if (!await ensureShippingOrderState("create")) return;
  try {
    const payload = { order_table_id: shippingState.selectedOrderId, idempotency_key: shippingRequestUuid() };
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
    const result = await shippingJson(endpoint, { method: "POST", body: JSON.stringify(payload) });
    shippingState.detail.case = result;
    if (result.shipment?.label_available) {
      shippingState.selectedOrderIds.add(shippingState.selectedOrderId);
      shippingState.printableOrderIds.add(shippingState.selectedOrderId);
    }
    applyShippingCase(result);
    await loadShippingQueue(false);
    await refreshShippingOrderState();
  } catch (error) {
    if (error.status === 409) await loadShippingDetail(shippingState.selectedOrderId);
    else applyShippingCase(shippingState.detail.case);
    shippingFeedback(error.message, true);
  }
}

async function loadShippingQueue(clearSelection = false, silent = false) {
  if (clearSelection) {
    shippingState.selectedOrderId = null;
    shippingState.selectedOrderIds.clear();
    shippingState.liveOrderState = null;
  }
  if (!silent) document.getElementById("shipping-loading").hidden = false;
  try {
    const days = document.getElementById("shipping-days").value;
    const payload = await shippingJson(`/admin/shipping/queue?days=${encodeURIComponent(days)}`);
    shippingState.queue = payload.items || [];
    const currentOrderIds = new Set(
      shippingState.queue.map((item) => Number(item.order_table_id)),
    );
    shippingState.selectedOrderIds = new Set(
      [...shippingState.selectedOrderIds].filter((orderId) => currentOrderIds.has(orderId)),
    );
    shippingState.printableOrderIds = new Set(
      [...shippingState.printableOrderIds].filter((orderId) => currentOrderIds.has(orderId)),
    );
    if (shippingState.selectedOrderId && !currentOrderIds.has(shippingState.selectedOrderId)) {
      shippingState.selectedOrderId = null;
      shippingState.detail = null;
      shippingState.liveOrderState = null;
      shippingState.selectedItems.clear();
      document.getElementById("shipping-detail").hidden = true;
      document.getElementById("shipping-empty").hidden = false;
      renderShippingOverduePayment(null);
      renderShippingOrderState(null);
    }
    renderShippingQueue();
    return shippingState.queue;
  } catch (error) {
    if (!silent) shippingAlert(error.message, true);
    return null;
  } finally {
    if (!silent) document.getElementById("shipping-loading").hidden = true;
  }
}

async function refreshShippingQueueManually() {
  const button = document.getElementById("shipping-queue-refresh");
  const previousOrderIds = new Set(
    shippingState.queue.map((item) => Number(item.order_table_id)),
  );
  button.disabled = true;
  button.textContent = "Odświeżanie…";
  try {
    const queue = await loadShippingQueue(false);
    if (!queue) return;
    const currentOrderIds = new Set(queue.map((item) => Number(item.order_table_id)));
    const added = [...currentOrderIds].filter((orderId) => !previousOrderIds.has(orderId)).length;
    const removed = [...previousOrderIds].filter((orderId) => !currentOrderIds.has(orderId)).length;
    shippingAlert(`Kolejka MS odświeżona. Nowe: ${added}, usunięte z kolejki: ${removed}, razem: ${queue.length}.`);
  } finally {
    button.disabled = false;
    button.textContent = "Odśwież kolejkę ↻";
  }
}

async function refreshShippingRuntimeState() {
  if (!shippingState.token || shippingState.runtimeRefreshBusy) return;
  shippingState.runtimeRefreshBusy = true;
  try {
    await loadShippingQueue(false, true);
    await refreshShippingOrderState();
  } finally {
    shippingState.runtimeRefreshBusy = false;
  }
}

function startShippingAutoRefresh() {
  if (shippingState.autoRefreshStarted) return;
  shippingState.autoRefreshStarted = true;
  window.setInterval(refreshShippingRuntimeState, SHIPPING_ORDER_STATE_REFRESH_MS);
  window.addEventListener("focus", refreshShippingRuntimeState);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshShippingRuntimeState();
  });
}

async function closeShippingDay() {
  if (!window.confirm("Potwierdzasz, że kurier fizycznie odebrał wszystkie przygotowane dziś paczki?")) return;
  try {
    const result = await shippingJson("/admin/shipping/day-close", {
      method: "POST",
      body: JSON.stringify({ business_date: new Date().toLocaleDateString("sv-SE"), confirm_handover: true }),
    });
    window.alert(`Zamknięcie dnia: ${shippingDayCloseStatusLabel(result.status)}. Zamknięto: ${result.closed_count}, RW: ${result.rw_count || 0}, WZ: ${result.wz_count || 0}, FV: ${result.invoice_count || 0}, błędy: ${result.error_count}.`);
    shippingState.archive.loaded = false;
    await loadShippingQueue(true);
  } catch (error) {
    window.alert(error.message);
  }
}

async function closeShippingOrder() {
  if (!shippingState.selectedOrderId) return;
  if (!await ensureShippingOrderState("close")) return;
  const consolidation = shippingState.detail?.case?.shipment?.consolidation;
  const confirmation = consolidation
    ? `Potwierdzasz, że kurier odebrał wspólną paczkę i można zakończyć ${shippingOrdersCountLabel(consolidation.count)}: ${consolidation.order_numbers.join(", ")}?`
    : "Potwierdzasz, że kurier odebrał tę paczkę i można zakończyć wybrane zlecenie?";
  if (!window.confirm(confirmation)) return;
  const button = document.getElementById("shipping-close-order");
  button.disabled = true;
  shippingFeedback("Tworzenie dokumentów MS i zamykanie wybranego zlecenia…");
  try {
    const result = await shippingJson(`/admin/shipping/orders/${shippingState.selectedOrderId}/close`, {
      method: "POST",
      body: JSON.stringify({ confirm_handover: true }),
    });
    shippingState.detail.case = result.case;
    applyShippingCase(result.case);
    const documentResults = result.order_results || [{ order_number: null, documents: result.documents || {} }];
    const labels = documentResults.flatMap((orderResult) => {
      const documents = orderResult.documents || {};
      const prefix = result.consolidated ? `${orderResult.order_number}: ` : "";
      return [
        documents.rw_number && `${prefix}RW ${documents.rw_number}`,
        documents.wz_number && `${prefix}WZ ${documents.wz_number}`,
        documents.invoice_number && `${prefix}FV ${documents.invoice_number}`,
      ].filter(Boolean);
    });
    const closedText = result.consolidated
      ? `Zamknięto ${shippingOrdersCountLabel(result.closed_count)} wspólnej paczki`
      : "Zlecenie zakończone";
    shippingFeedback(`${closedText}${labels.length ? ` — ${labels.join(", ")}` : ""}.`);
    shippingState.archive.loaded = false;
    await loadShippingQueue(false);
    await refreshShippingOrderState();
  } catch (error) {
    await loadShippingDetail(shippingState.selectedOrderId);
    shippingFeedback(error.message, true);
  }
}

function shippingArchiveProviderLabel(mode) {
  return {
    production: "DPD produkcja",
    demo: "DPD Demo",
    mock: "Symulacja lokalna",
    manual: "Numer ręczny",
  }[mode] || mode || "Nieznany tryb";
}

function shippingArchiveDocumentModeLabel(mode) {
  return {
    rw: "Umowa / RW",
    wz: "Sprzedaż / WZ",
    invoice: "Sprzedaż / FV + WZ",
  }[mode] || "Brak dokumentu";
}

function shippingArchiveEventLabel(type) {
  return {
    review_accepted: "Zatwierdzono dane i części",
    shipment_started: "Rozpoczęto tworzenie przesyłki",
    shipment_created: "Utworzono etykietę DPD",
    consolidated_shipment_started: "Rozpoczęto wspólną paczkę",
    consolidated_shipment_created: "Utworzono wspólną etykietę",
    courier_handover: "Kurier odebrał paczkę",
    firebird_rw_reconciled: "Uzgodniono dokument RW",
    external_order_state_conflict: "Wykryto konflikt stanu MS",
  }[type] || type || "Zdarzenie systemowe";
}

function shippingArchiveDocumentChips(documents = {}) {
  const entries = [
    ["rw", "RW", documents.rw?.number],
    ["wz", "WZ", documents.wz?.number],
    ["invoice", "FV", documents.invoice?.number],
  ].filter(([, , number]) => number);
  return entries.length
    ? entries.map(([kind, label, number]) => `<span class="shipping-v2-archive-doc ${kind}">${label} ${escapeShippingHtml(number)}</span>`).join("")
    : '<span class="shipping-muted">Brak numeru dokumentu</span>';
}

function renderShippingArchiveOperators() {
  const select = document.getElementById("shipping-archive-operator");
  if (!select) return;
  const selected = select.value;
  select.innerHTML = '<option value="">Wszyscy operatorzy</option>' + shippingState.archive.operators
    .map((operator) => `<option value="${Number(operator.id)}">${escapeShippingHtml(operator.name)}</option>`)
    .join("");
  select.value = selected;
}

function renderShippingArchive() {
  const container = document.getElementById("shipping-archive-rows");
  if (!container) return;
  const rows = shippingState.archive.items;
  document.getElementById("shipping-archive-tab-count").textContent = String(shippingState.archive.total);
  document.getElementById("shipping-archive-count").textContent = `${shippingState.archive.total} rekordów`;
  document.getElementById("shipping-archive-page").textContent = `Strona ${shippingState.archive.page} • rekordy ${shippingState.archive.total}`;
  document.getElementById("shipping-archive-prev").disabled = shippingState.archive.page <= 1;
  document.getElementById("shipping-archive-next").disabled = shippingState.archive.page * shippingState.archive.pageSize >= shippingState.archive.total;
  container.innerHTML = rows.length
    ? rows.map((item) => {
      const closedAt = new Date(item.closed_at);
      const dateValid = Number.isFinite(closedAt.getTime());
      const day = dateValid ? closedAt.toLocaleDateString("pl-PL", { day: "2-digit" }) : "—";
      const month = dateValid ? closedAt.toLocaleDateString("pl-PL", { month: "short" }).replace(".", "").toUpperCase() : "";
      const time = dateValid ? closedAt.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" }) : "";
      const closer = item.operators?.closed?.name || item.operators?.reviewed?.name || "Nieznany operator";
      const device = [item.device?.brand, item.device?.model].filter(Boolean).join(" ") || "Brak modelu";
      const items = item.items || [];
      const itemPreview = items.map((part) => `<small><strong>${escapeShippingHtml(part.index || "—")}</strong> ${escapeShippingHtml(part.name || "Nieznana część")}</small>`).join("");
      const remaining = Math.max(0, Number(item.item_count || 0) - items.length);
      const shipment = item.shipment || {};
      return `<button type="button" class="shipping-v2-archive-row ${Number(item.order_table_id) === shippingState.archive.selectedOrderId ? "active" : ""}" data-archive-order="${Number(item.order_table_id)}">
        <time><b>${escapeShippingHtml(day)}</b><span>${escapeShippingHtml(month)}</span><small>${escapeShippingHtml(time)}</small></time>
        <span><strong>#${escapeShippingHtml(item.order_number || "—")}</strong><small>${escapeShippingHtml(item.company_name || "Bez nazwy klienta")} • ${escapeShippingHtml(item.city || "brak miasta")}</small><small>${escapeShippingHtml(device)}</small><em class="shipping-source-badge ${item.source === "mobile" ? "source-mobile" : "source-manual"}">${escapeShippingHtml(shippingOrderSourceLabel(item.source))}</em></span>
        <span><strong>${escapeShippingHtml(closer)}</strong><small>Weryfikacja, kompletacja i zamknięcie</small><small>${escapeShippingHtml(shippingArchiveProviderLabel(shipment.provider_mode))}</small></span>
        <span><strong>${Number(item.item_count || 0)} poz. • ${Number(item.quantity || 0).toLocaleString("pl-PL", { maximumFractionDigits: 3 })} szt.</strong>${itemPreview}${remaining ? `<small>+ ${remaining} kolejnych pozycji</small>` : ""}</span>
        <span>${shippingArchiveDocumentChips(item.documents)}<small>DPD ${escapeShippingHtml(shipment.tracking_number || "brak numeru")}</small>${shippingDpdStatusMarkup(item.dpd_tracking, true)}${shipment.consolidation ? `<em>Wspólna paczka: ${Number(shipment.consolidation.count)} zlecenia</em>` : ""}</span>
      </button>`;
    }).join("")
    : '<div class="shipping-v2-archive-empty"><strong>Brak wyników</strong><span>Zmień wyszukiwaną frazę albo wyczyść filtry.</span></div>';
  document.querySelectorAll("[data-archive-order]").forEach((button) => {
    button.addEventListener("click", () => openShippingArchiveDetail(Number(button.dataset.archiveOrder)));
  });
}

function shippingArchiveQueryParams() {
  const params = new URLSearchParams({
    page: String(shippingState.archive.page),
    page_size: String(shippingState.archive.pageSize),
    sort: document.getElementById("shipping-archive-sort").value,
  });
  const fields = [
    ["shipping-archive-query", "query"],
    ["shipping-archive-date-from", "date_from"],
    ["shipping-archive-date-to", "date_to"],
    ["shipping-archive-operator", "operator_id"],
    ["shipping-archive-document", "document_type"],
    ["shipping-archive-source", "source"],
    ["shipping-archive-provider", "provider_mode"],
    ["shipping-archive-consolidated", "consolidated"],
  ];
  fields.forEach(([elementId, parameter]) => {
    const value = document.getElementById(elementId).value.trim();
    if (value) params.set(parameter, value);
  });
  return params;
}

async function loadShippingArchive(resetPage = false) {
  const view = document.getElementById("shipping-archive-view");
  if (!view) return;
  if (resetPage) shippingState.archive.page = 1;
  document.getElementById("shipping-archive-loading").hidden = false;
  try {
    const payload = await shippingJson(`/admin/shipping/archive?${shippingArchiveQueryParams()}`);
    shippingState.archive.items = payload.items || [];
    shippingState.archive.total = Number(payload.total || 0);
    shippingState.archive.operators = payload.filters?.operators || [];
    shippingState.archive.loaded = true;
    renderShippingArchiveOperators();
    renderShippingArchive();
  } catch (error) {
    document.getElementById("shipping-archive-rows").innerHTML = `<div class="shipping-v2-archive-empty error"><strong>Nie udało się wczytać Archiwum</strong><span>${escapeShippingHtml(error.message)}</span></div>`;
  } finally {
    document.getElementById("shipping-archive-loading").hidden = true;
  }
}

function renderShippingArchiveDetail(payload) {
  const snapshot = payload.snapshot || {};
  const order = snapshot.order || {};
  const recipient = snapshot.recipient || {};
  const device = snapshot.device || {};
  const shipment = snapshot.shipment || {};
  const documents = snapshot.documents || {};
  const operators = snapshot.operators || {};
  const items = snapshot.items || [];
  document.getElementById("shipping-archive-detail-title").textContent = `Zlecenie #${order.order_number || "—"}`;
  document.getElementById("shipping-archive-detail-subtitle").textContent = `${recipient.company_name || "Bez nazwy klienta"} • ${shippingDateTimeLabel(snapshot.archived_at)}`;
  const address = [recipient.street, [recipient.postal_code, recipient.city].filter(Boolean).join(" ")].filter(Boolean).join(", ");
  const contact = [recipient.contact_name, recipient.phone, recipient.email].filter(Boolean).join(" • ") || "Brak danych kontaktowych";
  const deviceLabel = [device.brand, device.model].filter(Boolean).join(" ") || "Brak modelu";
  const operatorCards = [
    ["Weryfikacja", operators.reviewed],
    ["Etykieta", operators.label_created],
    ["Zamknięcie", operators.closed],
  ].map(([label, operator]) => `<span><small>${label}</small><strong>${escapeShippingHtml(operator?.name || "Brak operatora")}</strong></span>`).join("");
  const itemRows = items.length
    ? items.map((item) => `<tr><td><strong>${escapeShippingHtml(item.index || "—")}</strong><small>${escapeShippingHtml(item.name || "Nieznana część")}</small></td><td>${Number(item.quantity || 0).toLocaleString("pl-PL", { maximumFractionDigits: 3 })} ${escapeShippingHtml(item.unit || "szt.")}</td><td>${escapeShippingHtml(shippingCurrencyLabel(item.price_net))}</td><td>${Number(item.vat_rate || 0).toLocaleString("pl-PL", { maximumFractionDigits: 2 })}%</td></tr>`).join("")
    : '<tr><td colspan="4" class="shipping-muted">Brak zapisanych części.</td></tr>';
  const events = payload.events || [];
  const eventRows = events.length
    ? events.map((event) => `<li><time>${escapeShippingHtml(shippingDateTimeLabel(event.created_at))}</time><span><strong>${escapeShippingHtml(shippingArchiveEventLabel(event.type))}</strong><small>${escapeShippingHtml(event.operator?.name || "System CTIP")}</small></span></li>`).join("")
    : '<li><span><strong>Brak historii zdarzeń</strong></span></li>';
  const labelLink = payload.label_url
    ? `<a class="shipping-button dark" href="${escapeShippingHtml(payload.label_url)}" target="_blank">Otwórz etykietę DPD</a>`
    : '<span class="shipping-muted">Brak zapisanej etykiety PDF.</span>';
  const trackingLink = payload.dpd_tracking
    ? `<button type="button" class="shipping-button primary" data-open-tracking="${escapeShippingHtml(payload.dpd_tracking.waybill)}">Status przesyłki: ${escapeShippingHtml(payload.dpd_tracking.status_label)}</button>`
    : '<span class="shipping-muted">Status InfoServices nie został jeszcze pobrany.</span>';
  document.getElementById("shipping-archive-detail-content").innerHTML = `
    <section class="shipping-v2-archive-summary"><span><small>ROZLICZENIE</small><strong>${escapeShippingHtml(shippingArchiveDocumentModeLabel(documents.mode))}</strong></span><span><small>NUMER DPD</small><strong>${escapeShippingHtml(shipment.tracking_number || "Brak")}</strong></span><span><small>URZĄDZENIE</small><strong>${escapeShippingHtml(deviceLabel)}</strong></span><span><small>ŹRÓDŁO</small><strong>${escapeShippingHtml(shippingOrderSourceLabel(order.source))}</strong></span></section>
    <section class="shipping-v2-archive-section"><header><small>TREŚĆ ZLECENIA</small></header><p class="shipping-v2-archive-problem">${escapeShippingHtml(order.problem || "Brak treści zlecenia.")}</p></section>
    <section class="shipping-v2-archive-section"><header><small>ODBIORCA</small></header><strong>${escapeShippingHtml(recipient.company_name || "Bez nazwy firmy")}</strong><p>${escapeShippingHtml(address || "Brak adresu")}</p><p>${escapeShippingHtml(contact)}</p></section>
    <section class="shipping-v2-archive-section"><header><small>CZĘŚCI I CENY NETTO</small></header><div class="shipping-table-wrap"><table class="shipping-table"><thead><tr><th>Indeks / część</th><th>Ilość</th><th>Cena netto</th><th>VAT</th></tr></thead><tbody>${itemRows}</tbody></table></div></section>
    <section class="shipping-v2-archive-section"><header><small>DOKUMENTY MS</small></header><div class="shipping-v2-archive-documents">${shippingArchiveDocumentChips(documents)}</div><small>Identyfikatory: RW ${escapeShippingHtml(documents.rw?.id || "—")} • WZ ${escapeShippingHtml(documents.wz?.id || "—")} • FV ${escapeShippingHtml(documents.invoice?.id || "—")}</small></section>
    <section class="shipping-v2-archive-section"><header><small>ODPOWIEDZIALNOŚĆ</small></header><div class="shipping-v2-archive-operators">${operatorCards}</div></section>
    <section class="shipping-v2-archive-section"><header><small>HISTORIA SPRAWY</small></header><ol class="shipping-v2-archive-events">${eventRows}</ol></section>
    <footer class="shipping-v2-archive-detail-actions">${labelLink}${trackingLink}</footer>`;
  document.querySelectorAll("[data-open-tracking]").forEach((button) => {
    button.addEventListener("click", () => openShippingTracking(button.dataset.openTracking));
  });
}

async function openShippingArchiveDetail(orderTableId) {
  shippingState.archive.selectedOrderId = orderTableId;
  renderShippingArchive();
  const detail = document.getElementById("shipping-archive-detail");
  detail.hidden = false;
  document.getElementById("shipping-archive-detail-content").innerHTML = '<div class="shipping-v2-archive-empty">Wczytywanie szczegółów…</div>';
  try {
    const payload = await shippingJson(`/admin/shipping/archive/${orderTableId}`);
    renderShippingArchiveDetail(payload);
  } catch (error) {
    document.getElementById("shipping-archive-detail-content").innerHTML = `<div class="shipping-v2-archive-empty error">${escapeShippingHtml(error.message)}</div>`;
  }
}

function clearShippingArchiveFilters() {
  [
    "shipping-archive-query",
    "shipping-archive-date-from",
    "shipping-archive-date-to",
    "shipping-archive-operator",
    "shipping-archive-document",
    "shipping-archive-source",
    "shipping-archive-provider",
    "shipping-archive-consolidated",
  ].forEach((elementId) => {
    document.getElementById(elementId).value = "";
  });
  document.getElementById("shipping-archive-sort").value = "newest";
  loadShippingArchive(true);
}

function shippingTrackingQueryParams() {
  const params = new URLSearchParams({
    page: String(shippingState.tracking.page),
    page_size: String(shippingState.tracking.pageSize),
    sort: document.getElementById("shipping-tracking-sort").value,
  });
  [
    ["shipping-tracking-query", "query"],
    ["shipping-tracking-category", "category"],
    ["shipping-tracking-linked", "linked"],
    ["shipping-tracking-terminal", "terminal"],
    ["shipping-tracking-date-from", "date_from"],
    ["shipping-tracking-date-to", "date_to"],
  ].forEach(([elementId, parameter]) => {
    const value = document.getElementById(elementId).value.trim();
    if (value) params.set(parameter, value);
  });
  if (document.getElementById("shipping-tracking-attention-only").checked) {
    params.set("attention", "true");
  }
  return params;
}

function shippingTrackingSyncLabel(sync) {
  if (!sync) return "Brak wykonanej synchronizacji";
  const when = sync.completed_at || sync.started_at;
  const status = {
    success: "Synchronizacja poprawna",
    partial: "Synchronizacja częściowa",
    failed: "Błąd synchronizacji",
    processing: "Synchronizacja w toku",
  }[sync.status] || "Stan nieznany";
  return `${status} • ${shippingDateTimeLabel(when)}${sync.error_text ? ` • ${sync.error_text}` : ""}`;
}

function renderShippingTracking(payload) {
  const summary = payload.summary || {};
  const categories = summary.categories || {};
  const rows = shippingState.tracking.items;
  document.getElementById("shipping-tracking-tab-count").textContent = String(summary.active || 0);
  document.getElementById("shipping-tracking-total").textContent = String(summary.total || 0);
  document.getElementById("shipping-tracking-active").textContent = String(summary.active || 0);
  document.getElementById("shipping-tracking-delivery").textContent = String(categories.out_for_delivery || 0);
  document.getElementById("shipping-tracking-delivered").textContent = String(categories.delivered || 0);
  document.getElementById("shipping-tracking-attention").textContent = String(summary.attention || 0);
  document.getElementById("shipping-tracking-sync-status").textContent = shippingTrackingSyncLabel(payload.sync);
  document.getElementById("shipping-tracking-page").textContent = `Strona ${shippingState.tracking.page} • rekordy ${shippingState.tracking.total}`;
  document.getElementById("shipping-tracking-prev").disabled = shippingState.tracking.page <= 1;
  document.getElementById("shipping-tracking-next").disabled = shippingState.tracking.page * shippingState.tracking.pageSize >= shippingState.tracking.total;
  document.getElementById("shipping-tracking-rows").innerHTML = rows.length
    ? rows.map((item) => {
      const links = item.links || [];
      const linkPreview = links.length
        ? links.slice(0, 2).map((link) => `<small><strong>#${escapeShippingHtml(link.order_number)}</strong> ${escapeShippingHtml(link.company_name || "Bez nazwy klienta")}</small>`).join("")
        : '<small>Przesyłka spoza CTIP</small>';
      const remaining = Math.max(0, links.length - 2);
      const eventTime = item.event_time ? shippingDateTimeLabel(item.event_time) : "Brak czasu zdarzenia";
      return `<button type="button" class="shipping-v2-tracking-row ${item.waybill === shippingState.tracking.selectedWaybill ? "active" : ""}" data-tracking-waybill="${escapeShippingHtml(item.waybill)}">
        <span><time>${escapeShippingHtml(eventTime)}</time><small>Synchronizacja: ${escapeShippingHtml(shippingDateTimeLabel(item.last_synced_at))}</small></span>
        <span><strong>${escapeShippingHtml(item.waybill)}</strong><em>${item.linked ? "Powiązana z CTIP" : "Spoza CTIP"}</em>${item.replacement_waybill ? `<small>Nowy list: ${escapeShippingHtml(item.replacement_waybill)}</small>` : ""}</span>
        <span>${shippingDpdStatusMarkup(item)}<small>${escapeShippingHtml(item.description || "Brak opisu zdarzenia")}</small></span>
        <span>${linkPreview}${remaining ? `<small>+ ${remaining} kolejnych zleceń</small>` : ""}</span>
        <span><strong>${escapeShippingHtml(item.depot_name || item.depot || "Brak oddziału")}</strong><small>${item.depot_name && item.depot ? `${escapeShippingHtml(item.depot)} • ` : ""}Kod: ${escapeShippingHtml(item.business_code || "—")} • ${escapeShippingHtml(item.country || "—")}</small></span>
      </button>`;
    }).join("")
    : '<div class="shipping-v2-tracking-empty"><strong>Brak przesyłek</strong><span>Zmień filtry albo uruchom synchronizację InfoServices.</span></div>';
  document.querySelectorAll("[data-tracking-waybill]").forEach((button) => {
    button.addEventListener("click", () => openShippingTrackingDetail(button.dataset.trackingWaybill));
  });
}

async function loadShippingTracking(resetPage = false) {
  if (resetPage) shippingState.tracking.page = 1;
  document.getElementById("shipping-tracking-loading").hidden = false;
  try {
    const payload = await shippingJson(`/admin/shipping/tracking?${shippingTrackingQueryParams()}`);
    shippingState.tracking.items = payload.items || [];
    shippingState.tracking.total = Number(payload.total || 0);
    shippingState.tracking.loaded = true;
    renderShippingTracking(payload);
  } catch (error) {
    document.getElementById("shipping-tracking-rows").innerHTML = `<div class="shipping-v2-tracking-empty error"><strong>Nie udało się wczytać statusów</strong><span>${escapeShippingHtml(error.message)}</span></div>`;
  } finally {
    document.getElementById("shipping-tracking-loading").hidden = true;
  }
}

function renderShippingTrackingDetail(payload) {
  const parcel = payload.parcel || {};
  const links = payload.links || [];
  const events = payload.events || [];
  document.getElementById("shipping-tracking-detail-title").textContent = `DPD ${parcel.waybill || "—"}`;
  document.getElementById("shipping-tracking-detail-subtitle").textContent = `${parcel.status_label || "Inny status DPD"} • ${shippingDateTimeLabel(parcel.event_time)}`;
  const linkRows = links.length
    ? links.map((link) => `<a href="${escapeShippingHtml(link.target_url)}"><strong>Zlecenie #${escapeShippingHtml(link.order_number)}</strong><small>${escapeShippingHtml(link.company_name || "Bez nazwy klienta")} • ${escapeShippingHtml(link.city || "brak miasta")}</small><small>${link.is_archived ? "Otwórz w Archiwum" : "Otwórz w Realizacji wysyłek"}</small></a>`).join("")
    : '<span class="shipping-muted">Brak powiązania z przesyłką utworzoną w CTIP.</span>';
  const replacement = payload.replacement
    ? `<a href="/shipping?view=tracking&waybill=${encodeURIComponent(payload.replacement.waybill)}"><strong>Kontynuuj śledzenie: ${escapeShippingHtml(payload.replacement.waybill)}</strong><small>${escapeShippingHtml(payload.replacement.status_label)} • ${escapeShippingHtml(payload.replacement.description || "brak opisu")}</small></a>`
    : parcel.replacement_waybill
      ? `<span class="shipping-muted">DPD nadało nowy numer ${escapeShippingHtml(parcel.replacement_waybill)}, ale nie pobrano jeszcze jego zdarzeń.</span>`
      : "";
  const eventRows = events.length
    ? events.map((event) => {
      const extra = (event.event_data || []).filter((value) => value?.value).map((value) => `${value.description || value.code || "Dane"}: ${value.value}`).join(" • ");
      const cancelled = event.is_cancelled || event.operation_type === "CANCEL";
      const cancellationLabel = event.operation_type === "CANCEL"
        ? "DPD przesłało polecenie anulowania wcześniejszego zdarzenia"
        : event.is_cancelled ? "Zdarzenie anulowane przez DPD" : "";
      const depot = [event.depot_name, event.depot].filter(Boolean).join(" / ") || "—";
      return `<li class="${cancelled ? "cancelled" : ""}"><time>${escapeShippingHtml(shippingDateTimeLabel(event.event_time))}</time><span><strong>${escapeShippingHtml(event.description || "Zdarzenie DPD")}</strong><small>Kod ${escapeShippingHtml(event.business_code || "—")} • ${escapeShippingHtml(event.group || "Bez grupy")} • oddział ${escapeShippingHtml(depot)}</small>${extra ? `<small class="shipping-v2-tracking-event-data">${escapeShippingHtml(extra)}</small>` : ""}${cancellationLabel ? `<small>${escapeShippingHtml(cancellationLabel)}</small>` : ""}</span></li>`;
    }).join("")
    : '<li><span><strong>Brak zapisanych zdarzeń</strong></span></li>';
  document.getElementById("shipping-tracking-detail-content").innerHTML = `
    <section class="shipping-v2-tracking-current"><span><small>STATUS</small><strong>${shippingDpdStatusMarkup(parcel)}</strong></span><span><small>KOD DPD</small><strong>${escapeShippingHtml(parcel.business_code || "—")}</strong></span><span><small>ODDZIAŁ</small><strong>${escapeShippingHtml(parcel.depot_name || parcel.depot || "—")}</strong></span><span><small>OSTATNIE ZDARZENIE</small><strong>${escapeShippingHtml(shippingDateTimeLabel(parcel.event_time))}</strong></span></section>
    <section class="shipping-v2-tracking-section"><header><small>OPIS DPD</small></header><p>${escapeShippingHtml(parcel.description || "Brak opisu zdarzenia.")}</p></section>
    <section class="shipping-v2-tracking-section"><header><small>POWIĄZANE ZLECENIA CTIP</small></header><div class="shipping-v2-tracking-links">${linkRows}${replacement}</div></section>
    <section class="shipping-v2-tracking-section"><header><small>PEŁNA HISTORIA INFOSERVICES</small></header><ol class="shipping-v2-tracking-events">${eventRows}</ol></section>`;
}

async function openShippingTrackingDetail(waybill) {
  const normalized = String(waybill || "").trim();
  if (!normalized) return;
  shippingState.tracking.selectedWaybill = normalized;
  const detail = document.getElementById("shipping-tracking-detail");
  detail.hidden = false;
  document.getElementById("shipping-tracking-detail-content").innerHTML = '<div class="shipping-v2-tracking-empty">Wczytywanie historii…</div>';
  try {
    const payload = await shippingJson(`/admin/shipping/tracking/${encodeURIComponent(normalized)}`);
    renderShippingTrackingDetail(payload);
    const params = new URLSearchParams({ view: "tracking", waybill: normalized });
    window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
  } catch (error) {
    document.getElementById("shipping-tracking-detail-content").innerHTML = `<div class="shipping-v2-tracking-empty error">${escapeShippingHtml(error.message)}</div>`;
  }
}

async function openShippingTracking(waybill) {
  switchShippingView("tracking", false);
  if (!shippingState.tracking.loaded) await loadShippingTracking();
  await openShippingTrackingDetail(waybill);
}

function clearShippingTrackingFilters() {
  [
    "shipping-tracking-query",
    "shipping-tracking-category",
    "shipping-tracking-linked",
    "shipping-tracking-terminal",
    "shipping-tracking-date-from",
    "shipping-tracking-date-to",
  ].forEach((elementId) => { document.getElementById(elementId).value = ""; });
  document.getElementById("shipping-tracking-sort").value = "newest";
  document.getElementById("shipping-tracking-attention-only").checked = false;
  loadShippingTracking(true);
}

async function synchronizeShippingTracking() {
  const button = document.getElementById("shipping-tracking-sync");
  button.disabled = true;
  document.getElementById("shipping-tracking-sync-status").textContent = "Synchronizacja w toku…";
  try {
    const result = await shippingJson("/admin/shipping/tracking/sync", { method: "POST" });
    shippingAlert(result.message || "Synchronizacja InfoServices zakończona.");
    await Promise.all([loadShippingTracking(true), loadShippingQueue(false)]);
  } catch (error) {
    shippingAlert(error.message, true);
    document.getElementById("shipping-tracking-sync-status").textContent = error.message;
  } finally {
    button.disabled = !(shippingState.config?.dpd_info?.enabled && shippingState.config?.dpd_info?.api_ready);
  }
}

async function applyShippingDeepLink() {
  const params = new URLSearchParams(window.location.search);
  const view = params.get("view");
  if (view === "tracking") {
    switchShippingView("tracking", false);
    await loadShippingTracking();
    if (params.get("waybill")) await openShippingTrackingDetail(params.get("waybill"));
    return;
  }
  if (view === "archive") {
    switchShippingView("archive", false);
    await loadShippingArchive();
    if (params.get("order")) await openShippingArchiveDetail(Number(params.get("order")));
    return;
  }
  if (view === "dispatch" && params.get("order")) {
    switchShippingView("dispatch", false);
    await loadShippingDetail(Number(params.get("order")));
  }
}

function switchShippingView(view, updateUrl = true) {
  document.getElementById("shipping-dispatch-view").hidden = view !== "dispatch";
  document.getElementById("shipping-catalog-view").hidden = view !== "catalog";
  const trackingView = document.getElementById("shipping-tracking-view");
  if (trackingView) trackingView.hidden = view !== "tracking";
  const archiveView = document.getElementById("shipping-archive-view");
  if (archiveView) archiveView.hidden = view !== "archive";
  document.querySelectorAll("[data-shipping-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.shippingView === view);
  });
  if (view === "catalog" && !shippingState.catalog.stock.length) {
    loadCatalogStock();
    loadCatalogMappings();
  }
  if (view === "tracking") loadShippingTracking();
  if (view === "archive") loadShippingArchive();
  if (updateUrl) {
    const params = new URLSearchParams({ view });
    window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
  }
}

function renderCatalogStock() {
  const rows = shippingState.catalog.stock;
  document.getElementById("shipping-catalog-stock-count").textContent = String(rows.length);
  document.getElementById("shipping-catalog-stock").innerHTML = rows.length
    ? rows.map((item) => {
      const itemId = Number(item.warehouse_item_id);
      const available = Number(item.available_after_soft_reservations || 0);
      const mappings = item.model_mappings || [];
      const statuses = Object.entries(item.mapping_counts || {}).map(([status, count]) => `<span class="shipping-status ${escapeShippingHtml(status)}">${escapeShippingHtml(shippingStatusLabel(status))}: ${Number(count)}</span>`).join(" ");
      const modelPreview = mappings.slice(0, 3).map((mapping) => `<span class="shipping-model-chip ${escapeShippingHtml(mapping.status)}">${escapeShippingHtml(mapping.label)}</span>`).join(" ");
      const remainingModels = Math.max(0, mappings.length - 3);
      return `<tr>
        <td><input type="checkbox" data-catalog-stock-select="${itemId}"></td>
        <td><small>ID ${itemId}</small><br><strong>${escapeShippingHtml(item.item_index || "—")}</strong><br>${escapeShippingHtml(item.item_name)}</td>
        <td>${available.toLocaleString("pl-PL", { maximumFractionDigits: 3 })} ${escapeShippingHtml(item.unit || "szt.")}</td>
        <td>${statuses || '<span class="shipping-muted">Brak</span>'}<div class="shipping-model-preview">${modelPreview}${remainingModels ? ` <small>+${remainingModels}</small>` : ""}</div></td>
        <td><button type="button" class="shipping-button secondary compact" data-manual-item="${itemId}" ${shippingCatalogMutationsEnabled() ? "" : "disabled"}>Mapuj</button></td>
      </tr>`;
    }).join("")
    : '<tr><td colspan="5" class="shipping-muted">Brak kartotek dla podanego filtra.</td></tr>';
  document.querySelectorAll("[data-manual-item]").forEach((button) => {
    button.addEventListener("click", () => openManualMapping(Number(button.dataset.manualItem)));
  });
}

async function loadCatalogStock() {
  const params = new URLSearchParams({
    limit: "200",
    only_available: document.getElementById("shipping-catalog-stock-available").checked ? "true" : "false",
  });
  const query = document.getElementById("shipping-catalog-stock-query").value.trim();
  if (query) params.set("query", query);
  try {
    const payload = await shippingJson(`/admin/shipping/stock?${params.toString()}`);
    shippingState.catalog.stock = payload.items || [];
    renderCatalogStock();
  } catch (error) {
    shippingAlert(error.message, true);
  }
}

function renderEvidence(evidence) {
  return (evidence || []).map((entry) => {
    const url = safeShippingUrl(entry.url);
    const label = escapeShippingHtml(entry.label || entry.source || "Dowód");
    const details = entry.order_count ? ` (${Number(entry.order_count)} zleceń)` : "";
    return url
      ? `<a href="${escapeShippingHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeShippingHtml(entry.title || label)}</a>`
      : `<span>${label}${escapeShippingHtml(details)}</span>`;
  }).join("<br>");
}

function renderCatalogMappings() {
  const rows = shippingState.catalog.mappings;
  document.getElementById("shipping-catalog-mapping-count").textContent = String(shippingState.catalog.total);
  document.getElementById("shipping-catalog-page").textContent = `Strona ${shippingState.catalog.page} • części: ${shippingState.catalog.total}`;
  document.getElementById("shipping-catalog-prev").disabled = shippingState.catalog.page <= 1;
  document.getElementById("shipping-catalog-next").disabled = shippingState.catalog.page * shippingState.catalog.pageSize >= shippingState.catalog.total;
  document.getElementById("shipping-catalog-mappings").innerHTML = rows.length
    ? rows.map((item) => {
      const models = (item.models || []).map((mapping) => `<label class="shipping-model-relation">
        <input type="checkbox" data-catalog-mapping-select="${Number(mapping.id)}">
        <span class="shipping-model-identity"><strong>${escapeShippingHtml(mapping.model_label)}</strong><small>ID ${Number(mapping.firebird_model_id)}</small></span>
        <span><span class="shipping-status ${escapeShippingHtml(mapping.status)}">${escapeShippingHtml(shippingStatusLabel(mapping.status))}</span><small>Pewność: ${escapeShippingHtml(mapping.confidence || "—")}</small></span>
        <span class="shipping-evidence">${renderEvidence(mapping.evidence)}</span>
      </label>`).join("");
      return `<tr>
        <td class="shipping-catalog-item"><strong>${escapeShippingHtml(item.item_index || "—")}</strong><br>${escapeShippingHtml(item.item_name)}<br><small>ID ${Number(item.firebird_warehouse_item_id)} • modeli: ${Number(item.mapping_count || 0)}</small></td>
        <td><div class="shipping-model-relations">${models}</div></td>
      </tr>`;
    }).join("")
    : '<tr><td colspan="2" class="shipping-muted">Brak części i modeli dla podanego filtra.</td></tr>';
}

async function loadCatalogMappings(resetPage = false) {
  if (resetPage) shippingState.catalog.page = 1;
  const params = new URLSearchParams({
    page: String(shippingState.catalog.page),
    page_size: String(shippingState.catalog.pageSize),
  });
  const status = document.getElementById("shipping-catalog-status").value;
  const confidence = document.getElementById("shipping-catalog-confidence").value;
  const query = document.getElementById("shipping-catalog-mapping-query").value.trim();
  if (status) params.set("status", status);
  if (confidence) params.set("confidence", confidence);
  if (query) params.set("query", query);
  try {
    const payload = await shippingJson(`/admin/shipping/compatibility/items?${params.toString()}`);
    shippingState.catalog.mappings = payload.items || [];
    shippingState.catalog.total = Number(payload.total || 0);
    renderCatalogMappings();
  } catch (error) {
    shippingAlert(error.message, true);
  }
}

function selectedCatalogMappingIds() {
  return Array.from(document.querySelectorAll("[data-catalog-mapping-select]:checked")).map((item) => Number(item.dataset.catalogMappingSelect));
}

function setCatalogMappingSelection(selected) {
  document.querySelectorAll("[data-catalog-mapping-select]").forEach((item) => {
    item.checked = selected;
  });
}

async function reviewCatalogMappings(action) {
  const mappingIds = selectedCatalogMappingIds();
  if (!mappingIds.length) {
    shippingAlert("Zaznacz co najmniej jedno mapowanie.", true);
    return;
  }
  try {
    await shippingJson("/admin/shipping/compatibility/review", {
      method: "POST",
      body: JSON.stringify({ mapping_ids: mappingIds, action, note: null }),
    });
    shippingAlert(action === "confirm" ? "Wybrane mapowania zostały potwierdzone." : "Wybrane mapowania zostały odrzucone.");
    await Promise.all([loadCatalogMappings(), loadCatalogStock()]);
  } catch (error) {
    shippingAlert(error.message, true);
  }
}

async function scanCatalog() {
  const button = document.getElementById("shipping-catalog-scan");
  button.disabled = true;
  shippingAlert("Trwa odczyt magazynu, modeli i historii zleceń…");
  try {
    const result = await shippingJson("/admin/shipping/compatibility/scan", { method: "POST" });
    shippingAlert(`Skan zakończony. Kandydaci: ${result.candidates}, nowe: ${result.created}, odświeżone: ${result.refreshed}, nieaktualne: ${result.stale}.`);
    await Promise.all([loadCatalogMappings(true), loadCatalogStock()]);
  } catch (error) {
    shippingAlert(error.message, true);
  } finally {
    button.disabled = !shippingCatalogMutationsEnabled();
  }
}

async function enrichCatalogWithWeb() {
  const itemIds = Array.from(document.querySelectorAll("[data-catalog-stock-select]:checked")).map((item) => Number(item.dataset.catalogStockSelect));
  if (!itemIds.length) {
    shippingAlert("Zaznacz kartoteki magazynowe do sprawdzenia w WWW.", true);
    return;
  }
  if (!window.confirm(`Przekazać publiczne dane ${itemIds.length} kartotek do OpenAI Web Search?`)) return;
  const button = document.getElementById("shipping-catalog-web");
  button.disabled = true;
  shippingAlert("Trwa ręcznie uruchomione wyszukiwanie WWW…");
  try {
    const result = await shippingJson("/admin/shipping/compatibility/web", {
      method: "POST",
      body: JSON.stringify({ warehouse_item_ids: itemIds }),
    });
    shippingAlert(`Wyszukiwanie zakończone. Nowe sugestie: ${result.created}, odświeżone: ${result.refreshed}, pominięte: ${result.skipped}.`);
    await Promise.all([loadCatalogMappings(true), loadCatalogStock()]);
  } catch (error) {
    shippingAlert(error.message, true);
  } finally {
    button.disabled = !shippingCatalogMutationsEnabled() || !shippingState.config?.compatibility_web?.enabled;
  }
}

async function loadManualModels() {
  const query = document.getElementById("shipping-manual-model-query").value.trim();
  const params = new URLSearchParams({ limit: "100" });
  if (query) params.set("query", query);
  try {
    const payload = await shippingJson(`/admin/shipping/models?${params.toString()}`);
    shippingState.catalog.models = payload.items || [];
    renderManualModels();
  } catch (error) {
    shippingAlert(error.message, true);
  }
}

function renderManualSelection() {
  const count = shippingState.catalog.selectedModelIds.size;
  document.getElementById("shipping-manual-selected").textContent = count
    ? `Wybrano nowych modeli: ${count}. Wybór pozostaje zachowany podczas wyszukiwania.`
    : "Nie wybrano nowych modeli.";
  document.getElementById("shipping-manual-save").disabled = !shippingCatalogMutationsEnabled() || count === 0;
}

function renderManualModels() {
  const itemMappings = shippingState.catalog.manualItem?.model_mappings || [];
  const existingByModel = new Map(itemMappings.map((mapping) => [Number(mapping.id), mapping]));
  const container = document.getElementById("shipping-manual-models");
  container.innerHTML = shippingState.catalog.models.length
    ? shippingState.catalog.models.map((model) => {
      const modelId = Number(model.id);
      const existing = existingByModel.get(modelId);
      const alreadyConfirmed = existing?.status === "confirmed";
      const checked = alreadyConfirmed || shippingState.catalog.selectedModelIds.has(modelId);
      const status = existing ? `<span class="shipping-status ${escapeShippingHtml(existing.status)}">${escapeShippingHtml(shippingStatusLabel(existing.status))}</span>` : '<span class="shipping-muted">Nowe mapowanie</span>';
      return `<label class="shipping-model-option ${alreadyConfirmed ? "confirmed" : ""}">
        <input type="checkbox" data-manual-model-id="${modelId}" ${checked ? "checked" : ""} ${alreadyConfirmed ? "disabled" : ""}>
        <span><strong>${escapeShippingHtml(model.label)}</strong><small>ID ${modelId}${model.kind ? ` • ${escapeShippingHtml(model.kind)}` : ""}</small></span>
        ${status}
      </label>`;
    }).join("")
    : '<p class="shipping-muted">Brak modeli dla podanego wyszukiwania.</p>';
  container.querySelectorAll("[data-manual-model-id]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const modelId = Number(checkbox.dataset.manualModelId);
      if (checkbox.checked) shippingState.catalog.selectedModelIds.add(modelId);
      else shippingState.catalog.selectedModelIds.delete(modelId);
      renderManualSelection();
    });
  });
  renderManualSelection();
}

async function openManualMapping(itemId) {
  shippingState.catalog.manualItem = shippingState.catalog.stock.find((item) => Number(item.warehouse_item_id) === itemId) || null;
  if (!shippingState.catalog.manualItem) return;
  document.getElementById("shipping-manual-item-title").textContent = `${shippingState.catalog.manualItem.item_index || "Bez indeksu"} — ${shippingState.catalog.manualItem.item_name}`;
  document.getElementById("shipping-manual-mapping").hidden = false;
  document.getElementById("shipping-manual-model-query").value = "";
  document.getElementById("shipping-manual-note").value = "";
  shippingState.catalog.selectedModelIds = new Set();
  renderManualSelection();
  await loadManualModels();
  document.getElementById("shipping-manual-mapping").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function saveManualMapping() {
  const item = shippingState.catalog.manualItem;
  const modelIds = Array.from(shippingState.catalog.selectedModelIds);
  if (!item || !modelIds.length) {
    shippingAlert("Wybierz co najmniej jeden nowy model.", true);
    return;
  }
  try {
    const result = await shippingJson("/admin/shipping/compatibility/manual-batch", {
      method: "POST",
      body: JSON.stringify({
        firebird_model_ids: modelIds,
        firebird_warehouse_item_id: Number(item.warehouse_item_id),
        note: document.getElementById("shipping-manual-note").value.trim() || null,
      }),
    });
    document.getElementById("shipping-manual-mapping").hidden = true;
    shippingAlert(`Potwierdzono modele dla części: ${Number(result.updated || 0)}.`);
    await Promise.all([loadCatalogMappings(true), loadCatalogStock()]);
  } catch (error) {
    shippingAlert(error.message, true);
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
    const dpdLabel = {
      mock: "Symulacja lokalna",
      demo: config.dpd.api_ready ? "DPD Demo" : "DPD Demo — brak konfiguracji",
      production: config.dpd.api_ready ? "Produkcja" : "Produkcja — brak konfiguracji",
    }[config.dpd.mode] || "Brak konfiguracji";
    document.getElementById("shipping-dpd-status").textContent = config.dpd.enabled ? dpdLabel : "Wyłączone";
    document.getElementById("shipping-tracking-sync").disabled = !(config.dpd_info?.enabled && config.dpd_info?.api_ready);
    document.getElementById("shipping-tracking-tab-count").textContent = String(config.dpd_info?.counts?.active || 0);
    const dpdDemoButton = document.getElementById("shipping-dpd-demo-test");
    dpdDemoButton.hidden = !(shippingFulfillmentEnabled() && config.dpd.enabled && config.dpd.mode === "demo" && config.dpd.api_ready && config.dpd.sender_ready);
    document.getElementById("shipping-warehouse").textContent = `Magazyn ${config.warehouse_id}`;
    document.getElementById("shipping-cutoff").textContent = new Date(config.courier_cutoff).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
    document.getElementById("shipping-catalog-scan").disabled = !shippingCatalogMutationsEnabled();
    document.getElementById("shipping-catalog-confirm").disabled = !shippingCatalogMutationsEnabled();
    document.getElementById("shipping-catalog-reject").disabled = !shippingCatalogMutationsEnabled();
    document.getElementById("shipping-catalog-web").disabled = !shippingCatalogMutationsEnabled() || !config.compatibility_web?.enabled;
    document.getElementById("shipping-manual-save").disabled = !shippingCatalogMutationsEnabled();
    document.getElementById("shipping-day-close").disabled = !shippingFulfillmentEnabled();
    document.getElementById("shipping-generate-ready").disabled = !shippingFulfillmentEnabled();
    const weight = document.getElementById("shipping-weight");
    weight.innerHTML = config.weight_presets_kg.map((value) => `<option value="${value}" ${Number(value) === Number(config.default_weight_kg) ? "selected" : ""}>${Number(value).toLocaleString("pl-PL")} kg</option>`).join("");
    if (!shippingFulfillmentEnabled()) {
      shippingAlert("Tryb wdrożeniowy: kolejka jest dostępna do odczytu, ale realizacja wysyłek pozostaje zablokowana.");
    } else if (config.after_cutoff) {
      shippingAlert("Jest po godzinie granicznej. Upewnij się, że kurier nie zakończył dzisiejszego odbioru.");
    }
    await loadShippingQueue(true);
    await applyShippingDeepLink();
    startShippingAutoRefresh();
  } catch (error) {
    shippingAlert(error.message, true);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const searchStockDebounced = shippingDebounce(searchShippingStock);
  const catalogStockDebounced = shippingDebounce(loadCatalogStock);
  const catalogMappingsDebounced = shippingDebounce(() => loadCatalogMappings(true));
  const manualModelsDebounced = shippingDebounce(loadManualModels);
  const archiveSearchDebounced = shippingDebounce(() => loadShippingArchive(true), 300);
  const trackingSearchDebounced = shippingDebounce(() => loadShippingTracking(true), 300);
  document.querySelectorAll("[data-shipping-view]").forEach((button) => button.addEventListener("click", () => switchShippingView(button.dataset.shippingView)));
  document.getElementById("shipping-refresh").addEventListener("click", () => {
    const archiveActive = document.querySelector('[data-shipping-view="archive"]')?.classList.contains("active");
    const trackingActive = document.querySelector('[data-shipping-view="tracking"]')?.classList.contains("active");
    if (trackingActive) loadShippingTracking();
    else if (archiveActive) loadShippingArchive();
    else refreshShippingQueueManually();
  });
  document.getElementById("shipping-queue-refresh").addEventListener("click", refreshShippingQueueManually);
  document.getElementById("shipping-days").addEventListener("change", () => loadShippingQueue(true));
  document.getElementById("shipping-sort").addEventListener("change", renderShippingQueue);
  document.getElementById("shipping-search").addEventListener("input", renderShippingQueue);
  document.getElementById("shipping-stock-search").addEventListener("input", searchStockDebounced);
  document.getElementById("shipping-generate-selected").addEventListener("click", () => generateShippingBulk(false));
  document.getElementById("shipping-generate-consolidated").addEventListener("click", generateConsolidatedShipping);
  document.getElementById("shipping-generate-ready").addEventListener("click", () => generateShippingBulk(true));
  document.getElementById("shipping-print-packing").addEventListener("click", printSelectedShippingPackingList);
  document.getElementById("shipping-print-selected").addEventListener("click", printSelectedShippingLabels);
  document.getElementById("shipping-dpd-demo-test").addEventListener("click", runDpdDemoDiagnostic);
  document.getElementById("shipping-clear-selection").addEventListener("click", clearShippingSelection);
  document.getElementById("shipping-consolidation-select").addEventListener("click", selectShippingConsolidationGroup);
  document.getElementById("shipping-contact-select").addEventListener("change", (event) => applyShippingContact(event.currentTarget.value));
  document.querySelectorAll("[data-stock-scope]").forEach((button) => {
    button.addEventListener("click", () => setShippingStockScope(button.dataset.stockScope));
  });
  document.getElementById("shipping-allow-negative-stock").addEventListener("change", (event) => {
    shippingState.allowNegativeStock = event.currentTarget.checked;
    if (!shippingState.allowNegativeStock) {
      shippingState.selectedItems.forEach((item, itemId) => {
        if (item.allow_negative_stock) shippingState.selectedItems.delete(itemId);
      });
    }
    renderShippingStock();
    shippingFeedback(shippingState.allowNegativeStock
      ? "Dozwolono wybór pozycji ze stanem zerowym. Decyzja zostanie zapisana przy wybranej części."
      : "Wyłączono zgodę na ujemny stan i usunięto takie pozycje z paczki.");
  });
  document.getElementById("shipping-invoice-required").addEventListener("change", () => {
    const nextMode = shippingPriceMode();
    if (shippingState.priceMode && shippingState.priceMode !== nextMode) {
      resetSelectedShippingPrices();
      shippingFeedback(nextMode === "sale"
        ? "Przełączono na cenę sprzedaży. Możesz zmienić cenę netto wybranych części."
        : "Przełączono na rozliczenie RW według ceny zakupu.");
    }
    shippingState.priceMode = nextMode;
    updateShippingDocumentMode();
    renderShippingStock();
  });
  ["shipping-company", "shipping-contact", "shipping-street", "shipping-postal", "shipping-city", "shipping-phone", "shipping-email"].forEach((id) => {
    document.getElementById(id).addEventListener("input", markShippingAddressManual);
  });
  document.getElementById("shipping-review").addEventListener("click", reviewShipping);
  document.getElementById("shipping-create").addEventListener("click", () => createShipping(false));
  document.getElementById("shipping-manual").addEventListener("click", () => createShipping(true));
  document.getElementById("shipping-close-order").addEventListener("click", closeShippingOrder);
  document.getElementById("shipping-tracking-open").addEventListener("click", (event) => {
    openShippingTracking(event.currentTarget.dataset.waybill);
  });
  document.getElementById("shipping-day-close").addEventListener("click", closeShippingDay);
  document.getElementById("shipping-catalog-scan").addEventListener("click", scanCatalog);
  document.getElementById("shipping-catalog-web").addEventListener("click", enrichCatalogWithWeb);
  document.getElementById("shipping-catalog-stock-query").addEventListener("input", catalogStockDebounced);
  document.getElementById("shipping-catalog-stock-available").addEventListener("change", loadCatalogStock);
  document.getElementById("shipping-catalog-mapping-query").addEventListener("input", catalogMappingsDebounced);
  document.getElementById("shipping-catalog-status").addEventListener("change", () => loadCatalogMappings(true));
  document.getElementById("shipping-catalog-confidence").addEventListener("change", () => loadCatalogMappings(true));
  document.getElementById("shipping-catalog-select-all").addEventListener("click", () => setCatalogMappingSelection(true));
  document.getElementById("shipping-catalog-unselect-all").addEventListener("click", () => setCatalogMappingSelection(false));
  document.getElementById("shipping-catalog-confirm").addEventListener("click", () => reviewCatalogMappings("confirm"));
  document.getElementById("shipping-catalog-reject").addEventListener("click", () => reviewCatalogMappings("reject"));
  document.getElementById("shipping-catalog-prev").addEventListener("click", () => { shippingState.catalog.page -= 1; loadCatalogMappings(); });
  document.getElementById("shipping-catalog-next").addEventListener("click", () => { shippingState.catalog.page += 1; loadCatalogMappings(); });
  document.getElementById("shipping-manual-model-query").addEventListener("input", manualModelsDebounced);
  document.getElementById("shipping-manual-save").addEventListener("click", saveManualMapping);
  document.getElementById("shipping-manual-close").addEventListener("click", () => { document.getElementById("shipping-manual-mapping").hidden = true; });
  if (document.getElementById("shipping-archive-view")) {
    document.getElementById("shipping-archive-query").addEventListener("input", archiveSearchDebounced);
    [
      "shipping-archive-date-from",
      "shipping-archive-date-to",
      "shipping-archive-operator",
      "shipping-archive-document",
      "shipping-archive-source",
      "shipping-archive-provider",
      "shipping-archive-consolidated",
      "shipping-archive-sort",
    ].forEach((elementId) => {
      document.getElementById(elementId).addEventListener("change", () => loadShippingArchive(true));
    });
    document.getElementById("shipping-archive-clear").addEventListener("click", clearShippingArchiveFilters);
    document.getElementById("shipping-archive-prev").addEventListener("click", () => {
      shippingState.archive.page -= 1;
      loadShippingArchive();
    });
    document.getElementById("shipping-archive-next").addEventListener("click", () => {
      shippingState.archive.page += 1;
      loadShippingArchive();
    });
    document.getElementById("shipping-archive-detail-close").addEventListener("click", () => {
      shippingState.archive.selectedOrderId = null;
      document.getElementById("shipping-archive-detail").hidden = true;
      renderShippingArchive();
    });
  }
  if (document.getElementById("shipping-tracking-view")) {
    document.getElementById("shipping-tracking-query").addEventListener("input", trackingSearchDebounced);
    [
      "shipping-tracking-category",
      "shipping-tracking-linked",
      "shipping-tracking-terminal",
      "shipping-tracking-date-from",
      "shipping-tracking-date-to",
      "shipping-tracking-sort",
      "shipping-tracking-attention-only",
    ].forEach((elementId) => {
      document.getElementById(elementId).addEventListener("change", () => loadShippingTracking(true));
    });
    document.getElementById("shipping-tracking-clear").addEventListener("click", clearShippingTrackingFilters);
    document.getElementById("shipping-tracking-sync").addEventListener("click", synchronizeShippingTracking);
    document.getElementById("shipping-tracking-prev").addEventListener("click", () => {
      shippingState.tracking.page -= 1;
      loadShippingTracking();
    });
    document.getElementById("shipping-tracking-next").addEventListener("click", () => {
      shippingState.tracking.page += 1;
      loadShippingTracking();
    });
    document.getElementById("shipping-tracking-detail-close").addEventListener("click", () => {
      shippingState.tracking.selectedWaybill = null;
      document.getElementById("shipping-tracking-detail").hidden = true;
      loadShippingTracking();
    });
  }
  document.getElementById("shipping-logout").addEventListener("click", async () => {
    await fetch("/auth/logout", { method: "POST", headers: shippingHeaders() }).catch(() => null);
    window.localStorage?.removeItem(SHIPPING_TOKEN_KEY);
    window.sessionStorage?.removeItem(SHIPPING_TOKEN_KEY);
    window.location.replace("/");
  });
  initializeShipping();
});
