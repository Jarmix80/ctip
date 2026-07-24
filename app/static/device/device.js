const DEVICE_TOKEN_KEY = "admin-session-token";

const deviceState = {
  view: document.body.dataset.deviceView || "home",
  models: new Map(),
  suppliers: new Map(),
  intakeItems: [],
  intakeKey: null,
  kp: { prefix: "KP/", nextNumber: 1, width: 4 },
  warehousePage: 1,
  warehousePages: 1,
  activeSourceRow: null,
  activeDetail: null,
  auditRunId: null,
  auditPage: 1,
  auditPages: 1,
  auditPollTimer: null,
};

function debounce(callback, delay = 350) {
  let timeoutId = null;
  return (...args) => {
    window.clearTimeout(timeoutId);
    timeoutId = window.setTimeout(() => callback(...args), delay);
  };
}

function readDeviceToken() {
  return (
    window.localStorage?.getItem(DEVICE_TOKEN_KEY) ||
    window.sessionStorage?.getItem(DEVICE_TOKEN_KEY) ||
    null
  );
}

function clearDeviceToken() {
  window.localStorage?.removeItem(DEVICE_TOKEN_KEY);
  window.sessionStorage?.removeItem(DEVICE_TOKEN_KEY);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function displayValue(value) {
  const text = String(value ?? "").trim();
  return text || "—";
}

function renderWarehouseCounter(item) {
  const counterBw = String(item.counter_bw ?? "").trim() || "bd";
  if (item.is_color !== true) {
    return `<span class="device-counter-value" aria-label="Licznik B/W: ${escapeHtml(
      counterBw
    )}">${escapeHtml(counterBw)}</span>`;
  }
  const counterColor = String(item.counter_color ?? "").trim() || "bd";
  const label = `Licznik B/W: ${counterBw}; licznik kolor: ${counterColor}`;
  return `
    <span class="device-counter-value" aria-label="${escapeHtml(label)}">
      <span>${escapeHtml(counterBw)}</span>
      <span aria-hidden="true">/</span>
      <span class="device-counter-color">${escapeHtml(counterColor)}</span>
    </span>
  `;
}

function renderPurchasePrice(value) {
  const rawValue = String(value ?? "").trim();
  if (!rawValue) {
    return '<span class="device-cell-sub">—</span>';
  }
  const numericValue = Number(rawValue.replace(/\s/g, "").replace(",", "."));
  const formattedValue = Number.isFinite(numericValue)
    ? numericValue.toLocaleString("pl-PL", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      })
    : rawValue;
  return `
    <div class="device-cell-main">${escapeHtml(formattedValue)} zł</div>
    <div class="device-cell-sub">netto</div>
  `;
}

function renderWarehouseNote(value) {
  const note = String(value ?? "").trim();
  if (!note) {
    return '<span class="device-cell-sub">—</span>';
  }
  return `<div class="device-table-note" title="${escapeHtml(note)}">${escapeHtml(
    note
  )}</div>`;
}

function formatDate(value) {
  if (!value) {
    return "—";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return String(value);
  }
  return parsed.toLocaleString("pl-PL");
}

function createUuid() {
  if (window.crypto?.randomUUID) {
    return window.crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  window.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(
    16,
    20
  )}-${hex.slice(20)}`;
}

function localDateTimeValue(date) {
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function setMessage(elementId, message) {
  const element = document.getElementById(elementId);
  if (!element) {
    return;
  }
  element.textContent = message || "";
  element.hidden = !message;
}

function setError(message = "") {
  setMessage("device-error", message);
}

function setInfo(message = "") {
  setMessage("device-info", message);
}

function setIntakeItemsMessage(message = "", variant = "error") {
  const element = document.getElementById("device-items-message");
  if (!element) {
    return;
  }
  element.textContent = message;
  element.hidden = !message;
  element.classList.toggle("flow-message-error", variant === "error");
  element.classList.toggle("flow-message-info", variant === "info");
}

function clearInvalidField(element) {
  if (!element) {
    return;
  }
  element.classList.remove("device-field-invalid");
  element.removeAttribute("aria-invalid");
}

function clearIntakeValidation() {
  document.querySelectorAll(".device-field-invalid").forEach(clearInvalidField);
  const summary = document.getElementById("device-intake-validation");
  if (!summary) {
    return;
  }
  summary.textContent = "";
  summary.hidden = true;
  summary.classList.remove("flow-message-error", "flow-message-info");
}

function addIntakeIssue(issues, message, element = null) {
  if (element) {
    element.classList.add("device-field-invalid");
    element.setAttribute("aria-invalid", "true");
  }
  issues.push({ message, element });
}

function showIntakeValidation(issues, variant = "error") {
  const summary = document.getElementById("device-intake-validation");
  if (!summary) {
    return;
  }
  const normalized = issues.filter((issue) => issue?.message);
  summary.classList.toggle("flow-message-error", variant === "error");
  summary.classList.toggle("flow-message-info", variant === "info");
  if (!normalized.length) {
    summary.textContent = "";
    summary.hidden = true;
    return;
  }
  if (variant === "info") {
    summary.textContent = normalized[0].message;
  } else {
    summary.innerHTML = `
      <strong>Nie można utworzyć dokumentu PZ. Uzupełnij:</strong>
      <ul>${normalized
        .map((issue) => `<li>${escapeHtml(issue.message)}</li>`)
        .join("")}</ul>
    `;
  }
  summary.hidden = false;
  if (variant !== "error") {
    return;
  }
  const target = normalized.find((issue) => issue.element)?.element || summary;
  target.focus?.();
  target.scrollIntoView?.({ behavior: "smooth", block: "center" });
}

function setButtonBusy(button, busy, busyText, normalText) {
  if (!button) {
    return;
  }
  button.disabled = busy;
  button.textContent = busy ? busyText : normalText;
}

async function fetchDeviceJson(
  url,
  { method = "GET", body = null, timeoutMs = 30000 } = {}
) {
  const token = readDeviceToken();
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      method,
      headers: {
        "X-Admin-Session": token || "",
        ...(body === null ? {} : { "Content-Type": "application/json" }),
      },
      body: body === null ? null : JSON.stringify(body),
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.detail || `Błąd HTTP ${response.status}.`);
    }
    return payload;
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error("Przekroczono czas oczekiwania na odpowiedź.");
    }
    throw error;
  } finally {
    window.clearTimeout(timeout);
  }
}

function activateView() {
  document.querySelectorAll("[data-device-screen]").forEach((screen) => {
    screen.hidden = screen.dataset.deviceScreen !== deviceState.view;
  });
  document.querySelectorAll("[data-device-nav]").forEach((link) => {
    link.classList.toggle("active", link.dataset.deviceNav === deviceState.view);
  });
}

function modelLabel(model) {
  return `${model.id_model} | ${model.marka || ""} ${model.model || ""}`.trim();
}

function supplierLabel(supplier) {
  const nip = supplier.nip ? ` | NIP ${supplier.nip}` : "";
  return `${supplier.id_klient} | ${supplier.nazwa || ""}${nip}`.trim();
}

function selectedId(inputId, values) {
  const value = document.getElementById(inputId)?.value.trim() || "";
  const exact = values.get(value);
  if (exact) {
    return exact;
  }
  const candidate = Number.parseInt(value.split("|", 1)[0].trim(), 10);
  if (!Number.isInteger(candidate) || candidate <= 0) {
    return null;
  }
  return Array.from(values.values()).find(
    (item) => Number(item.id_model || item.id_klient) === candidate
  );
}

async function loadModels(query = "") {
  const response = await fetchDeviceJson(
    `/admin/device/models?query=${encodeURIComponent(query)}&limit=300`
  );
  const list = document.getElementById("device-model-list");
  if (!list) {
    return;
  }
  deviceState.models.clear();
  list.innerHTML = "";
  for (const model of response.rows || []) {
    const label = modelLabel(model);
    deviceState.models.set(label, model);
    const option = document.createElement("option");
    option.value = label;
    list.append(option);
  }
}

async function loadSuppliers(query = "") {
  const response = await fetchDeviceJson(
    `/admin/device/suppliers?query=${encodeURIComponent(query)}&limit=300`
  );
  const list = document.getElementById("device-supplier-list");
  if (!list) {
    return;
  }
  deviceState.suppliers.clear();
  list.innerHTML = "";
  for (const supplier of response.rows || []) {
    const label = supplierLabel(supplier);
    deviceState.suppliers.set(label, supplier);
    const option = document.createElement("option");
    option.value = label;
    list.append(option);
  }
}

async function loadModelOptions() {
  const response = await fetchDeviceJson("/admin/device/model-form-options");
  const options = response.options || {};
  const fields = [
    ["device-model-brand", options.brands || [], options.default_brand],
    ["device-model-group", options.groups || [], null],
    ["device-model-kind", options.kinds || [], null],
  ];
  for (const [elementId, values, preferred] of fields) {
    const select = document.getElementById(elementId);
    if (!select) {
      continue;
    }
    select.innerHTML = '<option value="">-- wybierz --</option>';
    for (const value of values) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      option.selected = value === preferred;
      select.append(option);
    }
  }
}

function kpNumber(offset = 0) {
  const number = deviceState.kp.nextNumber + offset;
  return `${deviceState.kp.prefix}${String(number).padStart(deviceState.kp.width, "0")}`;
}

async function loadKpSuggestion() {
  const response = await fetchDeviceJson("/admin/device/intake/defaults?ewidencja_prefix=KP/");
  const defaults = response.defaults || {};
  deviceState.kp = {
    prefix: defaults.prefix || "KP/",
    nextNumber: Number(defaults.next_number || 1),
    width: Number(defaults.width || 4),
  };
  const label = document.getElementById("device-kp-suggestion");
  if (label) {
    label.textContent = `Kolejny numer sugerowany przez Firebird: ${
      defaults.suggested || kpNumber()
    }.`;
  }
}

function renderIntakeItems() {
  const body = document.getElementById("device-intake-items");
  if (!body) {
    return;
  }
  if (!deviceState.intakeItems.length) {
    body.innerHTML = '<tr><td colspan="6" class="device-empty">Dodaj co najmniej jeden egzemplarz.</td></tr>';
    return;
  }
  body.innerHTML = deviceState.intakeItems
    .map(
      (item, index) => `
        <tr data-intake-index="${index}">
          <td>${index + 1}</td>
          <td class="device-model-cell">
            <div class="device-cell-main">${escapeHtml(
              `${item.model.marka || ""} ${item.model.model || ""}`.trim()
            )}</div>
            <div class="device-cell-sub">MODEL ID ${escapeHtml(item.model.id_model)}</div>
          </td>
          <td><input data-item-field="serial" value="${escapeHtml(item.serial)}" maxlength="100"></td>
          <td><input data-item-field="ewidencja" value="${escapeHtml(
            item.ewidencja
          )}" maxlength="100"></td>
          <td><input data-item-field="price" type="number" min="0" step="0.01" value="${escapeHtml(
            item.price
          )}"></td>
          <td><button type="button" class="flow-secondary" data-item-remove="${index}">Usuń</button></td>
        </tr>
      `
    )
    .join("");
}

function resetIntake() {
  deviceState.intakeItems = [];
  deviceState.intakeKey = null;
  setIntakeItemsMessage("");
  clearIntakeValidation();
  const key = document.getElementById("device-intake-key");
  if (key) {
    key.textContent = "";
  }
  renderIntakeItems();
}

async function createSupplier() {
  const button = document.getElementById("device-supplier-create");
  const payload = {
    name: document.getElementById("device-supplier-name").value.trim(),
    nip: document.getElementById("device-supplier-nip").value.trim() || null,
    address: document.getElementById("device-supplier-address").value.trim() || null,
    postal_code: document.getElementById("device-supplier-postal").value.trim() || null,
    city: document.getElementById("device-supplier-city").value.trim() || null,
    phone: document.getElementById("device-supplier-phone").value.trim() || null,
    email: document.getElementById("device-supplier-email").value.trim() || null,
  };
  if (!payload.name) {
    throw new Error("Podaj nazwę dostawcy.");
  }
  setButtonBusy(button, true, "Zapisywanie…", "Dodaj dostawcę");
  try {
    const response = await fetchDeviceJson("/admin/device/suppliers", {
      method: "POST",
      body: payload,
      timeoutMs: 60000,
    });
    await loadSuppliers(String(response.supplier?.id_klient || payload.name));
    const matched = Array.from(deviceState.suppliers.entries()).find(
      ([, supplier]) =>
        Number(supplier.id_klient) === Number(response.supplier?.id_klient)
    );
    if (matched) {
      document.getElementById("device-supplier-search").value = matched[0];
    }
    setInfo("Dodano dostawcę w Menadżerze Serwisu.");
  } finally {
    setButtonBusy(button, false, "Zapisywanie…", "Dodaj dostawcę");
  }
}

async function createModel() {
  const button = document.getElementById("device-model-create");
  const payload = {
    marka: document.getElementById("device-model-brand").value,
    model: document.getElementById("device-model-name").value.trim(),
    grupa: document.getElementById("device-model-group").value,
    rodzaj: document.getElementById("device-model-kind").value,
    kolor: document.getElementById("device-model-color").checked,
    plik: document.getElementById("device-model-image").value.trim() || null,
  };
  if (!payload.marka || !payload.model || !payload.grupa || !payload.rodzaj) {
    throw new Error("Marka, model, grupa i rodzaj są wymagane.");
  }
  setButtonBusy(button, true, "Zapisywanie…", "Dodaj model");
  try {
    const response = await fetchDeviceJson("/admin/device/models", {
      method: "POST",
      body: payload,
      timeoutMs: 60000,
    });
    await loadModels(String(response.model?.id_model || payload.model));
    const matched = Array.from(deviceState.models.entries()).find(
      ([, model]) => Number(model.id_model) === Number(response.model?.id_model)
    );
    if (matched) {
      document.getElementById("device-model-search").value = matched[0];
    }
    setInfo("Dodano kompletny model urządzenia.");
  } finally {
    setButtonBusy(button, false, "Zapisywanie…", "Dodaj model");
  }
}

function addIntakeItems() {
  const model = selectedId("device-model-search", deviceState.models);
  if (!model) {
    throw new Error("Wybierz model z listy.");
  }
  const quantity = Number(document.getElementById("device-item-quantity").value || 1);
  if (!Number.isInteger(quantity) || quantity < 1 || quantity > 50) {
    throw new Error("Liczba egzemplarzy musi mieścić się w zakresie 1–50.");
  }
  const price = document.getElementById("device-item-price").value.trim();
  const offset = deviceState.intakeItems.length;
  for (let index = 0; index < quantity; index += 1) {
    deviceState.intakeItems.push({
      model,
      serial: "",
      ewidencja: kpNumber(offset + index),
      price,
    });
  }
  renderIntakeItems();
  document
    .querySelector(`[data-intake-index="${offset}"] [data-item-field="serial"]`)
    ?.focus();
  return quantity;
}

async function submitIntake() {
  clearIntakeValidation();
  const issues = [];
  const supplierInput = document.getElementById("device-supplier-search");
  const supplier = selectedId("device-supplier-search", deviceState.suppliers);
  if (!supplier) {
    addIntakeIssue(issues, "Wybierz dostawcę z listy.", supplierInput);
  }
  if (!deviceState.intakeItems.length) {
    addIntakeIssue(
      issues,
      "Dodaj co najmniej jeden egzemplarz.",
      document.getElementById("device-items-add")
    );
  }
  const serials = new Set();
  const ewidencje = new Set();
  for (const [index, item] of deviceState.intakeItems.entries()) {
    const row = document.querySelector(`[data-intake-index="${index}"]`);
    const serialInput = row?.querySelector('[data-item-field="serial"]');
    const ewidencjaInput = row?.querySelector('[data-item-field="ewidencja"]');
    const priceInput = row?.querySelector('[data-item-field="price"]');
    const serial = String(item.serial ?? "").trim();
    const ewidencja = String(item.ewidencja ?? "").trim();
    const priceText = String(item.price ?? "").trim();
    const priceValue = Number(priceText);
    if (!serial) {
      addIntakeIssue(issues, `Pozycja ${index + 1}: wpisz serial.`, serialInput);
    }
    if (!ewidencja) {
      addIntakeIssue(
        issues,
        `Pozycja ${index + 1}: wpisz numer KP.`,
        ewidencjaInput
      );
    }
    if (!priceText) {
      addIntakeIssue(
        issues,
        `Pozycja ${index + 1}: wpisz cenę netto, również gdy wynosi 0.`,
        priceInput
      );
    } else if (!Number.isFinite(priceValue) || priceValue < 0) {
      addIntakeIssue(
        issues,
        `Pozycja ${index + 1}: cena netto musi być liczbą nieujemną.`,
        priceInput
      );
    }
    const serialKey = serial.replace(/[^a-z0-9]/gi, "").toUpperCase();
    const kpKey = ewidencja.replace(/[^a-z0-9]/gi, "").toUpperCase();
    if (serialKey && serials.has(serialKey)) {
      addIntakeIssue(
        issues,
        `Pozycja ${index + 1}: serial jest powtórzony.`,
        serialInput
      );
    }
    if (kpKey && ewidencje.has(kpKey)) {
      addIntakeIssue(
        issues,
        `Pozycja ${index + 1}: numer KP jest powtórzony.`,
        ewidencjaInput
      );
    }
    if (serialKey) {
      serials.add(serialKey);
    }
    if (kpKey) {
      ewidencje.add(kpKey);
    }
  }

  const externalDocumentInput = document.getElementById("device-external-document");
  const exceptionInput = document.getElementById("device-exception-enabled");
  const exceptionReasonInput = document.getElementById("device-exception-reason");
  const externalDocument = externalDocumentInput.value.trim();
  const allowException = exceptionInput.checked;
  const exceptionReason = exceptionReasonInput.value.trim();
  const hasExplicitZeroPrice = deviceState.intakeItems.some(
    (item) => String(item.price ?? "").trim() && Number(item.price) === 0
  );
  const requiresException = !externalDocument || hasExplicitZeroPrice;
  if (requiresException && !allowException) {
    if (!externalDocument) {
      addIntakeIssue(
        issues,
        "Podaj numer dokumentu zewnętrznego albo zaznacz wyjątek.",
        externalDocumentInput
      );
    }
    addIntakeIssue(
      issues,
      "Brak dokumentu lub cena 0 wymagają zaznaczenia wyjątku.",
      exceptionInput
    );
  }
  if (allowException && exceptionReason.length < 10) {
    document.getElementById("device-exception-reason-wrap").hidden = false;
    addIntakeIssue(
      issues,
      "Wpisz uzasadnienie wyjątku mające co najmniej 10 znaków.",
      exceptionReasonInput
    );
  }
  if (issues.length) {
    showIntakeValidation(issues);
    return false;
  }

  if (!deviceState.intakeKey) {
    deviceState.intakeKey = createUuid();
  }
  document.getElementById(
    "device-intake-key"
  ).textContent = `UUID operacji: ${deviceState.intakeKey}`;
  const payload = {
    idempotency_key: deviceState.intakeKey,
    supplier_id: Number(supplier.id_klient),
    external_document: externalDocument || null,
    allow_exception: allowException,
    exception_reason: allowException ? exceptionReason : null,
    ewidencja_prefix: "KP/",
    items: deviceState.intakeItems.map((item) => ({
      model_id: Number(item.model.id_model),
      serial: item.serial.trim(),
      ewidencja: item.ewidencja.trim(),
      purchase_price_netto: Number(item.price || 0),
    })),
  };

  const button = document.getElementById("device-intake-submit");
  setButtonBusy(button, true, "Tworzenie PZ…", "Utwórz dokument PZ");
  try {
    const response = await fetchDeviceJson("/admin/device/intake/batch", {
      method: "POST",
      body: payload,
      timeoutMs: 120000,
    });
    const successMessage = `${
      response.message || "Utworzono dokument PZ."
    } Synchronizacja arkusza została dodana do kolejki.`;
    resetIntake();
    document.getElementById("device-external-document").value = "";
    document.getElementById("device-exception-enabled").checked = false;
    document.getElementById("device-exception-reason").value = "";
    document.getElementById("device-exception-reason-wrap").hidden = true;
    showIntakeValidation([{ message: successMessage }], "info");
    setInfo(successMessage);
    await loadKpSuggestion();
    return true;
  } finally {
    setButtonBusy(button, false, "Tworzenie PZ…", "Utwórz dokument PZ");
  }
}

function renderWarehouseSummary(summary) {
  const container = document.getElementById("device-warehouse-summary");
  if (!container) {
    return;
  }
  const labels = {
    available: "Dostępne",
    flow_reserved: "FLOW",
    manual_reserved: "Ręczne",
    sheet_errors: "Błędy arkusza",
    audit_only: "Historyczne",
  };
  container.innerHTML = Object.entries(labels)
    .map(
      ([key, label]) =>
        `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(
          summary?.[key] ?? 0
        )}</strong></article>`
    )
    .join("");
}

function reservationBadge(item) {
  const classes = {
    flow: "flow",
    manual: "warn",
    sheet: "warn",
    none: "ok",
  };
  return `<span class="device-badge ${classes[item.reservation_kind] || ""}">${escapeHtml(
    item.reservation_status
  )}</span>${
    item.reservation_for
      ? `<div class="device-cell-sub">${escapeHtml(item.reservation_for)}</div>`
      : ""
  }`;
}

function sheetBadge(item) {
  const classes = {
    synced: "ok",
    failed: "error",
    pending: "warn",
    processing: "warn",
    history_not_registered: "",
  };
  const labels = {
    synced: "Zsynchronizowano",
    failed: "Błąd",
    pending: "Oczekuje",
    processing: "W trakcie",
    history_not_registered: "Tylko audyt",
  };
  return `<span class="device-badge ${classes[item.sheet_sync_status] || ""}">${escapeHtml(
    labels[item.sheet_sync_status] || item.sheet_sync_status
  )}</span>`;
}

function sourcePresenceBadges(presence = {}) {
  const sources = [
    ["sheet", "Arkusz"],
    ["warehouse", "Magazyn"],
    ["machine", "Urządzenie"],
    ["ctip", "CTIP"],
  ];
  return `<div class="device-source-presence" aria-label="Obecność w źródłach">${sources
    .map(([key, label]) => {
      const present = presence[key] === true;
      return `<span title="${escapeHtml(label)}" class="device-badge ${
        present ? "ok" : "error"
      }">${present ? "OK" : "BD"}</span>`;
    })
    .join('<span class="device-source-separator" aria-hidden="true">/</span>')}</div>`;
}

function renderWarehouseRows(items) {
  const body = document.getElementById("device-warehouse-body");
  if (!body) {
    return;
  }
  if (!items.length) {
    body.innerHTML = '<tr><td colspan="11" class="device-empty">Brak pozycji dla wybranych filtrów.</td></tr>';
    return;
  }
  body.innerHTML = items
    .map(
      (item) => `
        <tr>
          <td>
            <div class="device-cell-main">${escapeHtml(
              `${item.producer || ""} ${item.model || ""}`.trim()
            )}</div>
            <div class="device-cell-sub">MAGAZYN ID ${escapeHtml(item.source_row)}</div>
          </td>
          <td>
            <div class="device-cell-main">${escapeHtml(displayValue(item.serial))}</div>
            <div class="device-cell-sub">${escapeHtml(displayValue(item.ewidencja))}</div>
          </td>
          <td>
            <span class="device-badge ok">${escapeHtml(item.warehouse_status)}</span>
            <div class="device-cell-sub">Dostępne: ${escapeHtml(
              item.available_quantity
            )}</div>
          </td>
          <td>${renderWarehouseCounter(item)}</td>
          <td class="device-price-cell">${renderPurchasePrice(item.purchase_price_net)}</td>
          <td>${renderWarehouseNote(item.note)}</td>
          <td>${escapeHtml(displayValue(item.zeroing_status))}</td>
          <td>${reservationBadge(item)}</td>
          <td>${sheetBadge(item)}</td>
          <td>${sourcePresenceBadges(item.source_presence)}</td>
          <td><button type="button" class="flow-secondary" data-device-detail="${
            item.source_row
          }">Szczegóły</button></td>
        </tr>
      `
    )
    .join("");
}

function auditResultBadge(status) {
  const labels = {
    ok: "OK",
    missing: "BRAKI",
    discrepancy: "ROZBIEŻNOŚĆ",
    duplicate: "DUPLIKAT",
  };
  const classes = {
    ok: "ok",
    missing: "warn",
    discrepancy: "error",
    duplicate: "error",
  };
  return `<span class="device-badge ${classes[status] || ""}">${escapeHtml(
    labels[status] || status || "—"
  )}</span>`;
}

function renderAuditSummary(summary = {}) {
  const container = document.getElementById("device-audit-summary");
  if (!container) {
    return;
  }
  const labels = {
    total: "Wszystkie",
    ok: "Poprawne",
    missing: "Braki",
    discrepancy: "Rozbieżności",
    duplicate: "Duplikaty",
  };
  container.innerHTML = Object.entries(labels)
    .map(
      ([key, label]) =>
        `<article><span>${escapeHtml(label)}</span><strong>${escapeHtml(
          summary[key] ?? 0
        )}</strong></article>`
    )
    .join("");
}

function renderAuditRunStatus(run) {
  const progress = document.getElementById("device-audit-progress");
  if (!progress) {
    return;
  }
  if (!run) {
    progress.hidden = true;
    progress.textContent = "";
    return;
  }
  const labels = {
    pending: "Oczekuje",
    running: "W trakcie",
    completed: "Zakończono",
    failed: "Błąd",
  };
  const counts =
    Number(run.total_items || 0) > 0
      ? ` (${Number(run.processed_items || 0)} z ${Number(run.total_items || 0)})`
      : "";
  progress.textContent = `${labels[run.status] || run.status}: ${
    run.phase || "brak etapu"
  }${counts}${run.error_text ? ` — ${run.error_text}` : ""}`;
  progress.hidden = false;
  progress.classList.toggle("error", run.status === "failed");
}

function renderAuditRows(items) {
  const body = document.getElementById("device-audit-body");
  if (!body) {
    return;
  }
  if (!items.length) {
    body.innerHTML =
      '<tr><td colspan="5" class="device-empty">Brak wyników dla wybranych filtrów.</td></tr>';
    return;
  }
  body.innerHTML = items
    .map(
      (item) => `
        <tr>
          <td>
            <div class="device-cell-main">${escapeHtml(
              `${item.producer || ""} ${item.model || ""}`.trim() || "—"
            )}</div>
            <div class="device-cell-sub">${escapeHtml(item.canonical_key)}</div>
          </td>
          <td>
            <div class="device-cell-main">${escapeHtml(displayValue(item.serial))}</div>
            <div class="device-cell-sub">${escapeHtml(displayValue(item.ewidencja))}</div>
          </td>
          <td>${sourcePresenceBadges(item.source_presence)}</td>
          <td>
            ${auditResultBadge(item.result_status)}
            <div class="device-audit-issues">${escapeHtml(
              item.issue_summary || "Brak rozbieżności."
            )}</div>
          </td>
          <td>
            <details class="device-audit-details">
              <summary>Dane źródłowe</summary>
              <pre>${escapeHtml(JSON.stringify(item.source_details || {}, null, 2))}</pre>
            </details>
          </td>
        </tr>
      `
    )
    .join("");
}

function scheduleAuditPoll(run) {
  window.clearTimeout(deviceState.auditPollTimer);
  deviceState.auditPollTimer = null;
  if (!run || !["pending", "running"].includes(run.status)) {
    return;
  }
  deviceState.auditPollTimer = window.setTimeout(async () => {
    if (deviceState.view !== "warehouse") {
      return;
    }
    try {
      await loadAuditHistory({ preserveSelection: true });
    } catch (error) {
      setError(error.message);
    }
  }, 2500);
}

async function loadAuditDetail(runId) {
  if (!runId) {
    renderAuditSummary({});
    renderAuditRunStatus(null);
    renderAuditRows([]);
    return null;
  }
  const query = document.getElementById("device-audit-query")?.value.trim() || "";
  const result = document.getElementById("device-audit-result")?.value || "all";
  const source =
    document.getElementById("device-audit-source")?.value || "operational";
  const params = new URLSearchParams({
    query,
    result,
    source,
    page: String(deviceState.auditPage),
    page_size: "100",
  });
  const response = await fetchDeviceJson(
    `/admin/device/audits/${runId}?${params.toString()}`
  );
  deviceState.auditRunId = Number(runId);
  deviceState.auditPage = Number(response.page || 1);
  deviceState.auditPages = Number(response.pages || 1);
  renderAuditRunStatus(response.run);
  renderAuditSummary(response.filtered_summary || response.run?.summary || {});
  renderAuditRows(response.items || []);
  const pageLabel = document.getElementById("device-audit-page-label");
  if (pageLabel) {
    pageLabel.textContent = `Strona ${deviceState.auditPage} z ${
      deviceState.auditPages || 1
    }`;
  }
  const previous = document.getElementById("device-audit-prev");
  const next = document.getElementById("device-audit-next");
  if (previous) {
    previous.disabled = deviceState.auditPage <= 1;
  }
  if (next) {
    next.disabled =
      deviceState.auditPages === 0 || deviceState.auditPage >= deviceState.auditPages;
  }
  scheduleAuditPoll(response.run);
  return response.run;
}

async function loadAuditHistory({ preserveSelection = false } = {}) {
  const response = await fetchDeviceJson("/admin/device/audits");
  const runs = response.items || [];
  const select = document.getElementById("device-audit-run");
  const currentId = preserveSelection ? Number(deviceState.auditRunId || 0) : 0;
  const selected =
    runs.find((run) => run.id === currentId) ||
    runs.find((run) => ["pending", "running"].includes(run.status)) ||
    runs.find((run) => run.status === "completed") ||
    runs[0] ||
    null;
  if (select) {
    select.innerHTML = runs.length
      ? runs
          .map(
            (run) =>
              `<option value="${run.id}" ${
                selected?.id === run.id ? "selected" : ""
              }>#${run.id} · ${escapeHtml(formatDate(run.created_at))} · ${escapeHtml(
                run.status
              )}</option>`
          )
          .join("")
      : '<option value="">Brak historii audytów</option>';
  }
  deviceState.auditRunId = selected?.id || null;
  return loadAuditDetail(deviceState.auditRunId);
}

async function startDeviceAudit() {
  const button = document.getElementById("device-audit-start");
  setButtonBusy(button, true, "Uruchamianie…", "Uruchom audyt");
  try {
    const response = await fetchDeviceJson("/admin/device/audits", { method: "POST" });
    deviceState.auditRunId = response.run.id;
    deviceState.auditPage = 1;
    setInfo("Audyt został dodany do kolejki. Dane źródłowe pozostają bez zmian.");
    await loadAuditHistory({ preserveSelection: true });
  } finally {
    setButtonBusy(button, false, "Uruchamianie…", "Uruchom audyt");
  }
}

async function loadWarehouse() {
  const query = document.getElementById("device-warehouse-query")?.value.trim() || "";
  const reservation =
    document.getElementById("device-warehouse-reservation")?.value || "all";
  const sheetSync = document.getElementById("device-warehouse-sheet")?.value || "all";
  const params = new URLSearchParams({
    query,
    reservation,
    sheet_sync: sheetSync,
    page: String(deviceState.warehousePage),
    page_size: "100",
  });
  const response = await fetchDeviceJson(`/admin/device/warehouse?${params.toString()}`);
  deviceState.warehousePage = Number(response.page || 1);
  deviceState.warehousePages = Number(response.pages || 1);
  renderWarehouseSummary(response.summary || {});
  renderWarehouseRows(response.items || []);
  const pageLabel = document.getElementById("device-page-label");
  if (pageLabel) {
    pageLabel.textContent = `Strona ${deviceState.warehousePage} z ${
      deviceState.warehousePages || 1
    }`;
  }
  const previous = document.getElementById("device-page-prev");
  const next = document.getElementById("device-page-next");
  if (previous) {
    previous.disabled = deviceState.warehousePage <= 1;
  }
  if (next) {
    next.disabled =
      deviceState.warehousePages === 0 ||
      deviceState.warehousePage >= deviceState.warehousePages;
  }
  await loadAuditHistory({ preserveSelection: true });
  return response;
}

function renderDetail(item, events) {
  deviceState.activeSourceRow = Number(item.source_row);
  deviceState.activeDetail = item;
  document.getElementById(
    "device-detail-title"
  ).textContent = `${item.producer || ""} ${item.model || ""}`.trim() || "Urządzenie";
  const meta = [
    ["Serial", item.serial],
    ["Numer KP", item.ewidencja],
    ["Status zerówki", item.zeroing_status],
    ["Stan arkusza", item.sheet_sync_status],
    ["MAGAZYN ID", item.source_row],
    ["MASZYNA ID", item.firebird_machine_id],
    ["PZ ID", item.firebird_pz_id],
    ["Cena zakupu netto", item.purchase_price_net],
  ];
  document.getElementById("device-detail-meta").innerHTML = meta
    .map(
      ([label, value]) =>
        `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(
          displayValue(value)
        )}</strong></div>`
    )
    .join("");
  document.getElementById("device-note-text").value = item.note || "";
  document.getElementById("device-reserved-for").value = item.reservation_for || "";
  document.getElementById("device-reservation-reason").value =
    item.reservation_reason || "";
  const until = document.getElementById("device-reservation-until");
  until.value = item.reservation_until
    ? localDateTimeValue(new Date(item.reservation_until))
    : localDateTimeValue(new Date(Date.now() + 14 * 86400000));
  const reservationForm = document.getElementById("device-reservation-form");
  const locked = Boolean(item.reservation_locked);
  reservationForm.querySelectorAll("input, textarea, button[type='submit']").forEach((field) => {
    field.disabled = locked;
  });
  const releaseButton = document.getElementById("device-reservation-release");
  releaseButton.hidden = item.reservation_kind !== "manual";
  releaseButton.disabled = locked;
  const list = document.getElementById("device-event-list");
  list.innerHTML = events.length
    ? events
        .map(
          (event) => `
            <li>
              <div class="device-cell-main">${escapeHtml(event.event_type)}</div>
              <div class="device-cell-sub">${escapeHtml(formatDate(event.created_at))}</div>
              <div class="device-cell-sub">${escapeHtml(
                JSON.stringify(event.payload || {})
              )}</div>
            </li>
          `
        )
        .join("")
    : '<li class="device-empty">Brak historii CTIP dla danych historycznych.</li>';
}

async function openDeviceDetail(sourceRow) {
  const response = await fetchDeviceJson(`/admin/device/warehouse/${sourceRow}`);
  renderDetail(response.item, response.events || []);
  const dialog = document.getElementById("device-detail-dialog");
  if (typeof dialog.showModal === "function" && !dialog.open) {
    dialog.showModal();
  } else if (!dialog.open) {
    dialog.setAttribute("open", "");
  }
}

async function saveDeviceNote() {
  if (!deviceState.activeSourceRow) {
    return;
  }
  const note = document.getElementById("device-note-text").value.trim();
  if (note.length < 3) {
    throw new Error("Uwaga musi zawierać co najmniej 3 znaki.");
  }
  await fetchDeviceJson(
    `/admin/device/warehouse/${deviceState.activeSourceRow}/notes`,
    { method: "POST", body: { note } }
  );
  setInfo("Zapisano uwagę i dodano aktualizację arkusza do kolejki.");
  await openDeviceDetail(deviceState.activeSourceRow);
}

async function saveDeviceReservation() {
  if (!deviceState.activeSourceRow) {
    return;
  }
  const reservedFor = document.getElementById("device-reserved-for").value.trim();
  const reason = document.getElementById("device-reservation-reason").value.trim();
  const until = document.getElementById("device-reservation-until").value;
  if (reason.length < 10) {
    throw new Error("Uzasadnienie rezerwacji musi zawierać co najmniej 10 znaków.");
  }
  await fetchDeviceJson(
    `/admin/device/warehouse/${deviceState.activeSourceRow}/reservation`,
    {
      method: "PUT",
      body: {
        reserved_for: reservedFor,
        reason,
        expires_at: until ? new Date(until).toISOString() : null,
      },
    }
  );
  setInfo("Zapisano rezerwację ręczną.");
  await openDeviceDetail(deviceState.activeSourceRow);
  await loadWarehouse();
}

async function releaseDeviceReservation() {
  if (!deviceState.activeSourceRow) {
    return;
  }
  const reason = document.getElementById("device-reservation-reason").value.trim();
  if (reason.length < 10) {
    throw new Error("Podaj uzasadnienie zwolnienia zawierające co najmniej 10 znaków.");
  }
  await fetchDeviceJson(
    `/admin/device/warehouse/${deviceState.activeSourceRow}/reservation`,
    { method: "DELETE", body: { reason } }
  );
  setInfo("Zwolniono rezerwację ręczną.");
  await openDeviceDetail(deviceState.activeSourceRow);
  await loadWarehouse();
}

async function loadHistory() {
  const response = await fetchDeviceJson("/admin/device/history?limit=300");
  const body = document.getElementById("device-history-body");
  const items = response.items || [];
  body.innerHTML = items.length
    ? items
        .map(
          (item) => `
            <tr>
              <td>${escapeHtml(formatDate(item.created_at))}</td>
              <td>${escapeHtml(displayValue(item.firebird_pz_number))}</td>
              <td><span class="device-badge ${
                item.status === "completed" ? "ok" : "error"
              }">${escapeHtml(item.status)}</span></td>
              <td>${escapeHtml(
                displayValue(item.result?.supplier_id || item.supplier_firebird_id)
              )}</td>
              <td>${escapeHtml(item.result?.items?.length || 0)}</td>
              <td>${escapeHtml(displayValue(item.error))}</td>
            </tr>
          `
        )
        .join("")
    : '<tr><td colspan="6" class="device-empty">Brak operacji przyjęcia.</td></tr>';
}

function renderOperationIssues(items) {
  const container = document.getElementById("device-operation-issues");
  container.innerHTML = items.length
    ? items
        .map(
          (item) => `
            <article>
              <div class="device-cell-main">${escapeHtml(
                item.firebird_pz_number || item.idempotency_key
              )}</div>
              <div class="device-cell-sub">Status: ${escapeHtml(
                item.status
              )}; UUID: ${escapeHtml(item.idempotency_key)}</div>
              <div>${escapeHtml(displayValue(item.error))}</div>
            </article>
          `
        )
        .join("")
    : '<p class="device-empty">Brak problemów operacji PZ.</p>';
}

function renderSheetIssues(items) {
  const container = document.getElementById("device-sheet-issues");
  container.innerHTML = items.length
    ? items
        .map(
          (item) => `
            <article>
              <div class="device-cell-main">Zadanie #${escapeHtml(item.id)} – ${escapeHtml(
                item.operation_type
              )}</div>
              <div class="device-cell-sub">Próby: ${escapeHtml(
                item.attempt_count
              )}/${escapeHtml(item.max_attempts)}</div>
              <div>${escapeHtml(displayValue(item.last_error))}</div>
              <button type="button" class="flow-secondary" data-outbox-retry="${
                item.id
              }">Ponów</button>
            </article>
          `
        )
        .join("")
    : '<p class="device-empty">Brak błędów synchronizacji arkusza.</p>';
}

async function loadIssues() {
  const response = await fetchDeviceJson("/admin/device/issues");
  renderOperationIssues(response.operations || []);
  renderSheetIssues(response.sheet_outbox || []);
}

async function loadHome() {
  const response = await fetchDeviceJson("/admin/device/warehouse?page=1&page_size=10");
  const summary = response.summary || {};
  document.querySelectorAll("#device-home-summary [data-summary]").forEach((element) => {
    element.textContent = String(summary[element.dataset.summary] ?? 0);
  });
}

async function loadIntake() {
  await Promise.all([
    loadModels(),
    loadSuppliers(),
    loadModelOptions(),
    loadKpSuggestion(),
  ]);
  renderIntakeItems();
}

async function loadCurrentView() {
  setError("");
  const refreshButton = document.getElementById("device-refresh");
  setButtonBusy(refreshButton, true, "Odświeżanie…", "Odśwież");
  try {
    if (deviceState.view === "home") {
      await loadHome();
    } else if (deviceState.view === "intake") {
      await loadIntake();
    } else if (deviceState.view === "warehouse") {
      await loadWarehouse();
    } else if (deviceState.view === "history") {
      await loadHistory();
    } else if (deviceState.view === "issues") {
      await loadIssues();
    }
  } catch (error) {
    setError(error instanceof Error ? error.message : "Nie udało się pobrać danych.");
  } finally {
    setButtonBusy(refreshButton, false, "Odświeżanie…", "Odśwież");
  }
}

function bindIntakeEvents() {
  document.getElementById("device-model-search")?.addEventListener(
    "input",
    debounce(async (event) => {
      try {
        const query = event.target.value.trim();
        clearInvalidField(event.target);
        if (deviceState.models.has(query)) {
          setIntakeItemsMessage("");
          return;
        }
        await loadModels(query);
      } catch (error) {
        setError(error.message);
      }
    })
  );
  document.getElementById("device-supplier-search")?.addEventListener(
    "input",
    debounce(async (event) => {
      try {
        const query = event.target.value.trim();
        clearInvalidField(event.target);
        if (deviceState.suppliers.has(query)) {
          return;
        }
        await loadSuppliers(query);
      } catch (error) {
        setError(error.message);
      }
    })
  );
  document.getElementById("device-items-add")?.addEventListener("click", () => {
    try {
      const quantity = addIntakeItems();
      clearInvalidField(document.getElementById("device-items-add"));
      setError("");
      setIntakeItemsMessage(
        `Dodano ${quantity} ${quantity === 1 ? "egzemplarz" : "egzemplarzy"}. Uzupełnij serial.`,
        "info"
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : "Nie udało się dodać pozycji.";
      setError("");
      setIntakeItemsMessage(message, "error");
    }
  });
  document.getElementById("device-items-clear")?.addEventListener("click", resetIntake);
  document.getElementById("device-supplier-create")?.addEventListener("click", async () => {
    try {
      setError("");
      await createSupplier();
    } catch (error) {
      setError(error.message);
    }
  });
  document.getElementById("device-model-create")?.addEventListener("click", async () => {
    try {
      setError("");
      await createModel();
    } catch (error) {
      setError(error.message);
    }
  });
  document.getElementById("device-intake-submit")?.addEventListener("click", async () => {
    try {
      setError("");
      await submitIntake();
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Nie udało się utworzyć dokumentu PZ.";
      setError("");
      showIntakeValidation([{ message }]);
    }
  });
  document.getElementById("device-intake-items")?.addEventListener("input", (event) => {
    const row = event.target.closest("[data-intake-index]");
    if (!row) {
      return;
    }
    const index = Number(row.dataset.intakeIndex);
    const field = event.target.dataset.itemField;
    if (deviceState.intakeItems[index] && field) {
      deviceState.intakeItems[index][field] = event.target.value;
      clearInvalidField(event.target);
    }
  });
  document.getElementById("device-intake-items")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-item-remove]");
    if (!button) {
      return;
    }
    deviceState.intakeItems.splice(Number(button.dataset.itemRemove), 1);
    renderIntakeItems();
  });
  document.getElementById("device-exception-enabled")?.addEventListener("change", (event) => {
    document.getElementById("device-exception-reason-wrap").hidden = !event.target.checked;
    clearInvalidField(event.target);
    if (event.target.checked) {
      clearInvalidField(document.getElementById("device-external-document"));
    }
  });
  document.getElementById("device-external-document")?.addEventListener("input", (event) => {
    clearInvalidField(event.target);
  });
  document.getElementById("device-exception-reason")?.addEventListener("input", (event) => {
    clearInvalidField(event.target);
  });
}

function bindWarehouseEvents() {
  document.getElementById("device-warehouse-search")?.addEventListener("click", async () => {
    deviceState.warehousePage = 1;
    await loadCurrentView();
  });
  document.getElementById("device-warehouse-query")?.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    deviceState.warehousePage = 1;
    await loadCurrentView();
  });
  document.getElementById("device-page-prev")?.addEventListener("click", async () => {
    deviceState.warehousePage = Math.max(1, deviceState.warehousePage - 1);
    await loadCurrentView();
  });
  document.getElementById("device-page-next")?.addEventListener("click", async () => {
    deviceState.warehousePage = Math.min(
      deviceState.warehousePages,
      deviceState.warehousePage + 1
    );
    await loadCurrentView();
  });
  document.getElementById("device-audit-start")?.addEventListener("click", async () => {
    try {
      await startDeviceAudit();
    } catch (error) {
      setError(error.message);
      await loadAuditHistory({ preserveSelection: true });
    }
  });
  document.getElementById("device-audit-run")?.addEventListener("change", async (event) => {
    deviceState.auditRunId = Number(event.target.value) || null;
    deviceState.auditPage = 1;
    await loadAuditDetail(deviceState.auditRunId);
  });
  document.getElementById("device-audit-filter")?.addEventListener("click", async () => {
    deviceState.auditPage = 1;
    await loadAuditDetail(deviceState.auditRunId);
  });
  document.getElementById("device-audit-query")?.addEventListener("keydown", async (event) => {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    deviceState.auditPage = 1;
    await loadAuditDetail(deviceState.auditRunId);
  });
  document.getElementById("device-audit-prev")?.addEventListener("click", async () => {
    deviceState.auditPage = Math.max(1, deviceState.auditPage - 1);
    await loadAuditDetail(deviceState.auditRunId);
  });
  document.getElementById("device-audit-next")?.addEventListener("click", async () => {
    deviceState.auditPage = Math.min(
      deviceState.auditPages,
      deviceState.auditPage + 1
    );
    await loadAuditDetail(deviceState.auditRunId);
  });
  document.getElementById("device-warehouse-body")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-device-detail]");
    if (!button) {
      return;
    }
    try {
      setError("");
      await openDeviceDetail(button.dataset.deviceDetail);
    } catch (error) {
      setError(error.message);
    }
  });
  document.getElementById("device-note-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await saveDeviceNote();
    } catch (error) {
      setError(error.message);
    }
  });
  document
    .getElementById("device-reservation-form")
    ?.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        await saveDeviceReservation();
      } catch (error) {
        setError(error.message);
      }
    });
  document
    .getElementById("device-reservation-release")
    ?.addEventListener("click", async () => {
      try {
        await releaseDeviceReservation();
      } catch (error) {
        setError(error.message);
      }
    });
}

function bindIssueEvents() {
  document.getElementById("device-sheet-issues")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-outbox-retry]");
    if (!button) {
      return;
    }
    try {
      await fetchDeviceJson(
        `/admin/device/sheet-outbox/${button.dataset.outboxRetry}/retry`,
        { method: "POST" }
      );
      setInfo("Zadanie przywrócono do kolejki.");
      await loadIssues();
    } catch (error) {
      setError(error.message);
    }
  });
}

async function initializeDevicePage() {
  const token = readDeviceToken();
  if (!token) {
    window.location.replace("/");
    return;
  }
  activateView();
  try {
    const me = await fetchDeviceJson("/auth/me");
    const sections = new Set(Array.isArray(me.sections) ? me.sections : []);
    if (!sections.has("device")) {
      throw new Error("Konto nie ma prawa „Obsługa urządzeń”.");
    }
    const displayName = [me.first_name, me.last_name].filter(Boolean).join(" ").trim();
    document.getElementById("device-user-chip").textContent =
      displayName || me.email || "Użytkownik";
  } catch (error) {
    clearDeviceToken();
    setError(error.message);
    window.setTimeout(() => window.location.replace("/"), 1200);
    return;
  }

  bindIntakeEvents();
  bindWarehouseEvents();
  bindIssueEvents();
  document.getElementById("device-refresh").addEventListener("click", loadCurrentView);
  document.getElementById("device-logout").addEventListener("click", async () => {
    try {
      await fetchDeviceJson("/auth/logout", { method: "POST" });
    } catch (_error) {
      setInfo("Sesja lokalna została zakończona.");
    } finally {
      clearDeviceToken();
      window.location.replace("/");
    }
  });
  await loadCurrentView();
}

document.addEventListener("DOMContentLoaded", initializeDevicePage);
