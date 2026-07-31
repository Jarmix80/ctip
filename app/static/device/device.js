const DEVICE_TOKEN_KEY = "admin-session-token";
const DEVICE_THEME_KEY = "ctip-device-theme";
const DEVICE_THEMES = new Set(["blue", "graphite", "mint"]);

const deviceState = {
  view: document.body.dataset.deviceView || "home",
  models: new Map(),
  suppliers: new Map(),
  intakeItems: [],
  intakeKey: null,
  intakeRequestSignature: null,
  kp: { prefix: "KP/", nextNumber: 1, width: 4 },
  warehousePage: 1,
  warehousePages: 1,
  activeSourceRow: null,
  activeDetail: null,
  auditRunId: null,
  auditPage: 1,
  auditPages: 1,
  auditPollTimer: null,
  withdrawalOperation: null,
  bnpLookup: null,
};

function applyDeviceTheme(theme, { persist = false } = {}) {
  const selectedTheme = DEVICE_THEMES.has(theme) ? theme : "blue";
  document.body.dataset.deviceTheme = selectedTheme;
  const select = document.getElementById("device-theme-select");
  if (select) {
    select.value = selectedTheme;
  }
  if (persist) {
    try {
      window.localStorage?.setItem(DEVICE_THEME_KEY, selectedTheme);
    } catch (_error) {}
  }
}

function initializeDeviceTheme() {
  let storedTheme = "blue";
  try {
    storedTheme = window.localStorage?.getItem(DEVICE_THEME_KEY) || "blue";
  } catch (_error) {
    storedTheme = "blue";
  }
  applyDeviceTheme(storedTheme);
}

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

function prepareDeviceLayout() {
  const auditPanel = document.querySelector(".device-audit-panel");
  const auditHost = document.getElementById("device-audit-host");
  if (auditPanel && auditHost) {
    auditHost.append(auditPanel);
  }
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

function formatCurrency(value) {
  return Number(value || 0).toLocaleString("pl-PL", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function updateIntakeSummary() {
  const items = deviceState.intakeItems;
  const complete = items.filter((item) => {
    const price = String(item.price ?? "").trim();
    return String(item.serial ?? "").trim() && String(item.ewidencja ?? "").trim() && price;
  }).length;
  const net = items.reduce((sum, item) => {
    const price = Number(String(item.price ?? "").replace(",", "."));
    return sum + (Number.isFinite(price) ? price : 0);
  }, 0);
  const vat = net * 0.23;
  const values = {
    "device-intake-summary-count": String(items.length),
    "device-intake-summary-complete": `${complete} / ${items.length}`,
    "device-intake-summary-net": `${formatCurrency(net)} zł`,
    "device-intake-summary-vat": `${formatCurrency(vat)} zł`,
    "device-intake-summary-gross": `${formatCurrency(net + vat)} zł`,
  };
  for (const [id, value] of Object.entries(values)) {
    const element = document.getElementById(id);
    if (element) {
      element.textContent = value;
    }
  }
  const status = document.getElementById("device-intake-summary-status");
  if (!status) {
    return;
  }
  status.classList.toggle("is-ready", items.length > 0 && complete === items.length);
  if (!items.length) {
    status.textContent = "Dodaj pierwsze urządzenie.";
  } else if (complete === items.length) {
    status.textContent = "Wszystkie urządzenia są uzupełnione.";
  } else {
    status.textContent = `Uzupełnij dane w ${items.length - complete} ${
      items.length - complete === 1 ? "urządzeniu" : "urządzeniach"
    }.`;
  }
}

function renderIntakeItems() {
  const body = document.getElementById("device-intake-items");
  if (!body) {
    return;
  }
  if (!deviceState.intakeItems.length) {
    body.innerHTML = '<tr><td colspan="9" class="device-empty">Dodaj co najmniej jeden egzemplarz.</td></tr>';
    updateIntakeSummary();
    return;
  }
  body.innerHTML = deviceState.intakeItems
    .map(
      (item, index) => `
        <tr data-intake-index="${index}" data-intake-search="${escapeHtml(
          `${item.model.marka || ""} ${item.model.model || ""} ${item.serial || ""} ${
            item.ewidencja || ""
          }`.toLocaleLowerCase("pl-PL")
        )}">
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
          <td><input data-item-field="counterBw" type="number" min="0" step="1" value="${escapeHtml(
            item.counterBw
          )}" aria-label="Licznik B/W"></td>
          <td><input data-item-field="counterColor" type="number" min="0" step="1" value="${escapeHtml(
            item.counterColor
          )}" aria-label="Licznik kolor"></td>
          <td><input data-item-field="counterScan" type="number" min="0" step="1" value="${escapeHtml(
            item.counterScan
          )}" aria-label="Licznik skan"></td>
          <td><button type="button" class="flow-secondary" data-item-remove="${index}">Usuń</button></td>
        </tr>
      `
    )
    .join("");
  updateIntakeSummary();
  filterIntakeItems();
}

function filterIntakeItems() {
  const query = document
    .getElementById("device-intake-list-search")
    ?.value.trim()
    .toLocaleLowerCase("pl-PL");
  for (const row of document.querySelectorAll("[data-intake-index]")) {
    row.hidden = Boolean(query) && !row.dataset.intakeSearch.includes(query);
  }
}

function resetIntake() {
  deviceState.intakeItems = [];
  deviceState.intakeKey = null;
  deviceState.intakeRequestSignature = null;
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
      counterBw: "",
      counterColor: "",
      counterScan: "",
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
  const documentDateInput = document.getElementById("device-intake-document-date");
  const exceptionInput = document.getElementById("device-exception-enabled");
  const exceptionReasonInput = document.getElementById("device-exception-reason");
  const externalDocument = externalDocumentInput.value.trim();
  const documentDate = documentDateInput.value.trim();
  const allowException = exceptionInput.checked;
  const exceptionReason = exceptionReasonInput.value.trim();
  if (!documentDate) {
    addIntakeIssue(issues, "Podaj datę przyjęcia.", documentDateInput);
  }
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

  const requestPayload = {
    supplier_id: Number(supplier.id_klient),
    external_document: externalDocument || null,
    document_date: documentDate,
    allow_exception: allowException,
    exception_reason: allowException ? exceptionReason : null,
    ewidencja_prefix: "KP/",
    items: deviceState.intakeItems.map((item) => ({
      model_id: Number(item.model.id_model),
      serial: item.serial.trim(),
      ewidencja: item.ewidencja.trim(),
      purchase_price_netto: Number(item.price || 0),
      counter_bw: item.counterBw === "" ? null : Number(item.counterBw),
      counter_color: item.counterColor === "" ? null : Number(item.counterColor),
      counter_scan: item.counterScan === "" ? null : Number(item.counterScan),
    })),
  };
  const requestSignature = JSON.stringify(requestPayload);
  if (
    !deviceState.intakeKey ||
    deviceState.intakeRequestSignature !== requestSignature
  ) {
    deviceState.intakeKey = createUuid();
    deviceState.intakeRequestSignature = requestSignature;
  }
  document.getElementById(
    "device-intake-key"
  ).textContent = `UUID operacji: ${deviceState.intakeKey}`;
  const payload = {
    idempotency_key: deviceState.intakeKey,
    ...requestPayload,
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
    documentDateInput.value = localIsoDate();
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
    body.innerHTML = '<tr><td colspan="10" class="device-empty">Brak pozycji dla wybranych filtrów.</td></tr>';
    return;
  }
  body.innerHTML = items
    .map(
      (item) => `
        <tr class="device-warehouse-row" data-device-detail-row="${
          item.source_row
        }" tabindex="0" role="button" aria-label="Kliknij dwukrotnie, aby otworzyć szczegóły urządzenia ${escapeHtml(
          `${item.producer || ""} ${item.model || ""} ${item.serial || ""}`.trim()
        )}">
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
    if (deviceState.view !== "audit") {
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
  await loadModelOptions();
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
  return response;
}

async function loadAuditView() {
  await loadModelOptions();
  await loadAuditHistory({ preserveSelection: true });
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
  document.getElementById("device-counter-date").value = localDateTimeValue(new Date());
  document.getElementById("device-counter-bw").value = item.counter_bw || "";
  document.getElementById("device-counter-color").value = item.counter_color || "";
  document.getElementById("device-counter-scan").value = item.counter_scan || "";
  document.getElementById("device-counter-note").value = "";
  document.getElementById("device-counter-allow-lower").checked = false;
  document.getElementById("device-counter-reason").value = "";
  document.getElementById("device-counter-reason-wrap").hidden = true;
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

async function saveDeviceCounters() {
  if (!deviceState.activeSourceRow) {
    return;
  }
  const valueOrNull = (id) => {
    const value = document.getElementById(id).value.trim();
    return value === "" ? null : Number(value);
  };
  const readingAt = document.getElementById("device-counter-date").value;
  const allowLower = document.getElementById("device-counter-allow-lower").checked;
  const reason = document.getElementById("device-counter-reason").value.trim();
  const payload = {
    reading_at: new Date(readingAt).toISOString(),
    counter_bw: valueOrNull("device-counter-bw"),
    counter_color: valueOrNull("device-counter-color"),
    counter_scan: valueOrNull("device-counter-scan"),
    allow_lower: allowLower,
    override_reason: allowLower ? reason : null,
    note: document.getElementById("device-counter-note").value.trim() || null,
  };
  if ([payload.counter_bw, payload.counter_color, payload.counter_scan].every(
    (value) => value === null
  )) {
    throw new Error("Podaj co najmniej jeden licznik.");
  }
  if (allowLower && reason.length < 10) {
    throw new Error("Uzasadnienie niższego odczytu musi mieć co najmniej 10 znaków.");
  }
  const response = await fetchDeviceJson(
    `/admin/device/warehouse/${deviceState.activeSourceRow}/counters`,
    { method: "POST", body: payload }
  );
  setInfo(response.message || "Zapisano odczyt liczników.");
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
              <td>
                <details class="device-audit-details">
                  <summary>Otwórz</summary>
                  <pre>${escapeHtml(JSON.stringify(item.result || {}, null, 2))}</pre>
                </details>
              </td>
              <td>${
                response.can_withdraw && item.status === "completed"
                  ? `<button type="button" class="flow-secondary" data-withdraw-operation="${
                      item.id
                    }">Usuń</button>`
                  : item.status === "withdrawn"
                    ? '<span class="device-badge warn">Wycofano</span>'
                    : "—"
              }</td>
            </tr>
          `
        )
        .join("")
    : '<tr><td colspan="8" class="device-empty">Brak operacji przyjęcia.</td></tr>';
}

function renderWithdrawalPreview(data) {
  const preview = data.preview || {};
  const differences = preview.differences || [];
  const dependencies = Object.entries(preview.dependencies || {});
  const lines = [
    ["Dokument", preview.pz_number || "—"],
    ["Pozycje PZ", preview.positions ?? 0],
    ["Urządzenia do usunięcia", preview.expected_positions ?? 0],
    ["Kartoteki MAGAZYN", preview.expected_positions ?? 0],
    ["Kartoteki MASZYNA", preview.expected_positions ?? 0],
    ["Wiersze arkusza", preview.expected_positions ?? 0],
  ];
  document.getElementById("device-withdraw-preview").innerHTML = `
    <p>Wycofanie usunie elementy utworzone przez ten dokument i zachowa historię operacji w CTIP.</p>
    <dl>${lines
      .map(
        ([label, value]) =>
          `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd></div>`
      )
      .join("")}</dl>
    <section class="${differences.length ? "device-withdraw-warning" : ""}">
      <strong>Różnice:</strong>
      ${
        differences.length
          ? `<ul>${differences.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
          : "<p>Nie wykryto zmian względem zapisu początkowego.</p>"
      }
    </section>
    <section class="${dependencies.length ? "device-withdraw-warning" : ""}">
      <strong>Późniejsze powiązania:</strong>
      ${
        dependencies.length
          ? `<ul>${dependencies
              .map(([table, count]) => `<li>${escapeHtml(table)}: ${escapeHtml(count)}</li>`)
              .join("")}</ul>`
          : "<p>Nie wykryto późniejszych powiązań.</p>"
      }
    </section>
  `;
  document.getElementById("device-withdraw-force-wrap").hidden = !data.can_force;
}

async function openWithdrawalDialog(operationId) {
  const data = await fetchDeviceJson(
    `/admin/device/history/${operationId}/withdrawal-preview`
  );
  deviceState.withdrawalOperation = operationId;
  renderWithdrawalPreview(data);
  document.getElementById("device-withdraw-confirmation").value = "";
  document.getElementById("device-withdraw-reason").value = "";
  document.getElementById("device-withdraw-force").checked = false;
  const dialog = document.getElementById("device-withdraw-dialog");
  dialog.showModal();
}

async function submitWithdrawal() {
  const reason = document.getElementById("device-withdraw-reason").value.trim();
  if (reason.length < 10) {
    throw new Error("Uzasadnienie musi mieć co najmniej 10 znaków.");
  }
  const response = await fetchDeviceJson(
    `/admin/device/history/${deviceState.withdrawalOperation}`,
    {
      method: "DELETE",
      body: {
        confirmation: document.getElementById("device-withdraw-confirmation").value.trim(),
        reason,
        force: document.getElementById("device-withdraw-force").checked,
      },
      timeoutMs: 120000,
    }
  );
  document.getElementById("device-withdraw-dialog").close();
  setInfo(response.message);
  await loadHistory();
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
  const syncBody = document.getElementById("device-sync-history");
  const syncItems = response.recent_outbox || [];
  syncBody.innerHTML = syncItems.length
    ? syncItems
        .map(
          (item) => `
            <tr>
              <td>${escapeHtml(formatDate(item.created_at))}</td>
              <td>#${escapeHtml(item.unit_id)}</td>
              <td>${escapeHtml(item.operation_type)}</td>
              <td><span class="device-badge ${
                item.status === "completed"
                  ? "ok"
                  : item.status === "failed"
                    ? "error"
                    : "warn"
              }">${escapeHtml(item.status)}</span></td>
              <td>${escapeHtml(item.attempt_count)}/${escapeHtml(item.max_attempts)}</td>
              <td>${escapeHtml(displayValue(item.last_error))}</td>
            </tr>
          `
        )
        .join("")
    : '<tr><td colspan="6" class="device-empty">Brak historii synchronizacji.</td></tr>';
  const eventBody = document.getElementById("device-event-history");
  const eventItems = response.recent_events || [];
  eventBody.innerHTML = eventItems.length
    ? eventItems
        .map(
          (item) => `
            <tr>
              <td>${escapeHtml(formatDate(item.created_at))}</td>
              <td>#${escapeHtml(item.unit_id)}</td>
              <td>${escapeHtml(item.event_type)}</td>
              <td><code>${escapeHtml(JSON.stringify(item.payload || {}))}</code></td>
            </tr>
          `
        )
        .join("")
    : '<tr><td colspan="4" class="device-empty">Brak historii zmian urządzeń.</td></tr>';
}

async function loadHome() {
  const response = await fetchDeviceJson("/admin/device/warehouse?page=1&page_size=10");
  const summary = response.summary || {};
  document.querySelectorAll("#device-home-summary [data-summary]").forEach((element) => {
    element.textContent = String(summary[element.dataset.summary] ?? 0);
  });
}

async function loadIntake() {
  const documentDateInput = document.getElementById("device-intake-document-date");
  if (documentDateInput && !documentDateInput.value) {
    documentDateInput.value = localIsoDate();
  }
  await Promise.all([
    loadModels(),
    loadSuppliers(),
    loadModelOptions(),
    loadKpSuggestion(),
  ]);
  renderIntakeItems();
}

function setBnpStatus(message = "", variant = "info") {
  const element = document.getElementById("device-bnp-status");
  if (!element) {
    return;
  }
  element.textContent = message;
  element.hidden = !message;
  element.classList.toggle("flow-message-error", variant === "error");
  element.classList.toggle("flow-message-info", variant !== "error");
}

function renderBnpMessageList(elementId, messages) {
  const element = document.getElementById(elementId);
  if (!element) {
    return;
  }
  const rows = Array.isArray(messages) ? messages.filter(Boolean) : [];
  element.innerHTML = rows.length
    ? `<ul>${rows.map((message) => `<li>${escapeHtml(message)}</li>`).join("")}</ul>`
    : "";
  element.hidden = rows.length === 0;
}

function localIsoDate() {
  const now = new Date();
  const offset = now.getTimezoneOffset() * 60000;
  return new Date(now.getTime() - offset).toISOString().slice(0, 10);
}

function updateBnpGross() {
  const input = document.getElementById("device-bnp-price-netto");
  const output = document.getElementById("device-bnp-price-brutto");
  if (!input || !output) {
    return;
  }
  const value = Number(String(input.value || "").trim().replace(",", "."));
  output.value =
    Number.isFinite(value) && value > 0
      ? (value * 1.23).toLocaleString("pl-PL", {
          minimumFractionDigits: 2,
          maximumFractionDigits: 2,
        })
      : "";
}

function updateBnpCompleteState() {
  const button = document.getElementById("device-bnp-complete-btn");
  const confirmation = document.getElementById("device-bnp-confirm");
  const fieldIds = [
    "device-bnp-target-ewidencja",
    "device-bnp-warehouse-index",
    "device-bnp-document",
    "device-bnp-document-date",
    "device-bnp-item-name",
    "device-bnp-price-netto",
  ];
  if (!button || !confirmation) {
    return;
  }
  const fieldsComplete = fieldIds.every(
    (fieldId) => String(document.getElementById(fieldId)?.value || "").trim().length > 0
  );
  button.disabled = !(
    deviceState.bnpLookup?.can_complete &&
    deviceState.bnpLookup?.target_item &&
    confirmation.checked &&
    fieldsComplete
  );
}

function clearBnpFinalResult() {
  const result = document.getElementById("device-bnp-final-result");
  const text = document.getElementById("device-bnp-final-result-text");
  if (text) {
    text.textContent = "";
  }
  if (result) {
    result.hidden = true;
  }
}

function buildBnpDefaultItemName(machine, fallbackSerial = "") {
  if (!machine) {
    return "";
  }
  const deviceName = [machine.marka, machine.model].filter(Boolean).join(" ").trim();
  const serial = String(machine.serial || machine.serial2 || fallbackSerial).trim();
  return [deviceName, serial ? `S/N:${serial}` : ""].filter(Boolean).join(" ");
}

function renderBnpLookup(lookup, preserved = {}) {
  deviceState.bnpLookup = lookup || null;
  const result = document.getElementById("device-bnp-result");
  const summary = document.getElementById("device-bnp-machine-summary");
  const warehouseBody = document.getElementById("device-bnp-warehouse-body");
  const machine = lookup?.machine;
  const supplier = lookup?.supplier;
  if (!result || !summary || !warehouseBody) {
    return;
  }

  if (machine) {
    const serial = machine.serial || machine.serial2 || lookup.serial;
    summary.innerHTML = `
      <header class="device-bnp-summary-head">
        <span>Znalezione urządzenie</span>
        <strong>${escapeHtml(
          `${machine.marka || ""} ${machine.model || ""}`.trim() || "Urządzenie"
        )}</strong>
        <small>Dane powiązane z numerem seryjnym w lokalnym Firebird.</small>
      </header>
      <article><span>Klient</span><strong>${escapeHtml(
        machine.client_name || `ID ${machine.id_klient || "—"}`
      )}</strong></article>
      <article><span>Urządzenie</span><strong>${escapeHtml(
        `${machine.marka || ""} ${machine.model || ""}`.trim() || "—"
      )}</strong></article>
      <article><span>Serial / KP</span><strong>${escapeHtml(serial || "—")}<br>${escapeHtml(
        machine.ewidencja || "—"
      )}</strong></article>
      <article><span>Dostawca wykupu</span><strong>${escapeHtml(
        supplier?.name || "Nie znaleziono BNP"
      )}</strong></article>
    `;
  } else {
    summary.innerHTML = `
      <header class="device-bnp-summary-head">
        <span>Wynik wyszukiwania</span>
        <strong>Brak jednoznacznego urządzenia</strong>
        <small>Sprawdź numer seryjny albo wyjaśnij znalezione blokady.</small>
      </header>
      <article><span>Wynik</span><strong>Brak jednoznacznego urządzenia</strong></article>
    `;
  }

  renderBnpMessageList("device-bnp-blockers", lookup?.blockers);
  renderBnpMessageList("device-bnp-warnings", lookup?.warnings);
  const warehouseRows = Array.isArray(lookup?.warehouse_rows) ? lookup.warehouse_rows : [];
  warehouseBody.innerHTML = warehouseRows.length
    ? warehouseRows
        .map(
          (row) => `
            <tr>
              <td>${escapeHtml(
                `${row.id_magazyn ?? "—"}${row.warehouse_name ? ` / ${row.warehouse_name}` : ""}`
              )}</td>
              <td>${escapeHtml(row.index || "—")}</td>
              <td>${escapeHtml(row.name || "—")}</td>
              <td>${escapeHtml(row.quantity ?? 0)}</td>
              <td>${escapeHtml(row.purchase_price ?? row.net_price ?? 0)} zł</td>
            </tr>
          `
        )
        .join("")
    : '<tr><td colspan="5">Brak kartotek magazynowych.</td></tr>';

  document.getElementById("device-bnp-target-ewidencja").value =
    preserved.targetEwidencja || lookup?.suggested_ewidencja || "";
  document.getElementById("device-bnp-warehouse-index").value =
    preserved.warehouseIndex || lookup?.suggested_index || "";
  document.getElementById("device-bnp-document").value = preserved.document || "";
  document.getElementById("device-bnp-document-date").value =
    preserved.documentDate || localIsoDate();
  document.getElementById("device-bnp-item-name").value =
    preserved.itemName || buildBnpDefaultItemName(machine, lookup?.serial);
  document.getElementById("device-bnp-price-netto").value = preserved.priceNetto || "";
  document.getElementById("device-bnp-confirm").checked = false;
  document.getElementById("device-bnp-create-catalog-action").hidden =
    !lookup?.can_create_catalog;
  result.hidden = false;
  updateBnpGross();
  updateBnpCompleteState();
}

async function loadBnpLookup({ preserveForm = false } = {}) {
  const serialInput = document.getElementById("device-bnp-serial");
  const serial = String(serialInput?.value || "").trim();
  if (!serial) {
    throw new Error("Podaj numer seryjny z dokumentu BNP.");
  }
  const preserved = preserveForm
    ? {
        targetEwidencja: document.getElementById("device-bnp-target-ewidencja")?.value,
        warehouseIndex: document.getElementById("device-bnp-warehouse-index")?.value,
        document: document.getElementById("device-bnp-document")?.value,
        documentDate: document.getElementById("device-bnp-document-date")?.value,
        itemName: document.getElementById("device-bnp-item-name")?.value,
        priceNetto: document.getElementById("device-bnp-price-netto")?.value,
      }
    : {};
  const response = await fetchDeviceJson(
    `/admin/device/bnp-buyout/lookup?serial=${encodeURIComponent(serial)}`
  );
  renderBnpLookup(response.lookup || {}, preserved);
  return response.lookup || {};
}

function validateBnpIdentifiers() {
  const sourceValue = String(deviceState.bnpLookup?.machine?.ewidencja || "")
    .trim()
    .toUpperCase();
  const sourceMatch = sourceValue.match(/^KP\/(\d+)(?:\/.*)?$/);
  if (!sourceMatch) {
    throw new Error("Źródłowa ewidencja urządzenia nie ma formatu KP/<numer>/...");
  }
  [
    ["Docelowa ewidencja", "device-bnp-target-ewidencja"],
    ["Indeks magazynowy", "device-bnp-warehouse-index"],
  ].forEach(([label, fieldId]) => {
    const match = String(document.getElementById(fieldId)?.value || "")
      .trim()
      .toUpperCase()
      .match(/^WKP\/(\d+)(?:\/.*)?$/);
    if (!match) {
      throw new Error(`${label} musi mieć format WKP/<numer>/...`);
    }
    if (match[1] !== sourceMatch[1]) {
      throw new Error(`${label} musi zachować numer KP/${sourceMatch[1]}.`);
    }
  });
}

function readBnpDocumentFields() {
  const itemName = String(document.getElementById("device-bnp-item-name")?.value || "").trim();
  const externalDocument = String(
    document.getElementById("device-bnp-document")?.value || ""
  ).trim();
  const documentDate = String(
    document.getElementById("device-bnp-document-date")?.value || ""
  ).trim();
  const priceNetto = Number(
    String(document.getElementById("device-bnp-price-netto")?.value || "")
      .trim()
      .replace(",", ".")
  );
  if (!itemName) {
    throw new Error("Podaj nazwę pozycji z dokumentu BNP.");
  }
  if (!externalDocument) {
    throw new Error("Podaj numer dokumentu BNP.");
  }
  if (!documentDate) {
    throw new Error("Podaj datę dokumentu BNP.");
  }
  if (!Number.isFinite(priceNetto) || priceNetto <= 0) {
    throw new Error("Cena netto wykupu musi być większa od 0.");
  }
  return {
    itemName,
    externalDocument,
    documentDate,
    priceNetto: Number(priceNetto.toFixed(4)),
  };
}

async function loadBnpView() {
  const dateInput = document.getElementById("device-bnp-document-date");
  if (dateInput && !dateInput.value) {
    dateInput.value = localIsoDate();
  }
}

function bindBnpEvents() {
  document.getElementById("device-bnp-lookup-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    setError("");
    setInfo("");
    clearBnpFinalResult();
    const button = document.getElementById("device-bnp-lookup-btn");
    setButtonBusy(button, true, "Wyszukiwanie…", "Wyszukaj urządzenie");
    setBnpStatus("Wyszukiwanie urządzenia i kartotek…");
    try {
      const lookup = await loadBnpLookup();
      if (Array.isArray(lookup.blockers) && lookup.blockers.length) {
        setBnpStatus("Wyszukiwanie zakończone z blokadami.", "error");
      } else if (lookup.can_create_catalog) {
        setBnpStatus(
          "Brak kartoteki magazynu 27. Uzupełnij nazwę i utwórz pozycję ze stanem 0."
        );
      } else {
        setBnpStatus("Urządzenie i kartoteka są gotowe do wykupu.");
      }
    } catch (error) {
      setError(error.message);
      setBnpStatus(error.message, "error");
    } finally {
      setButtonBusy(button, false, "Wyszukiwanie…", "Wyszukaj urządzenie");
    }
  });

  document
    .getElementById("device-bnp-create-catalog-btn")
    ?.addEventListener("click", async () => {
      setError("");
      setInfo("");
      clearBnpFinalResult();
      const button = document.getElementById("device-bnp-create-catalog-btn");
      try {
        if (!deviceState.bnpLookup?.machine || !deviceState.bnpLookup?.can_create_catalog) {
          throw new Error("Brak urządzenia gotowego do utworzenia kartoteki.");
        }
        validateBnpIdentifiers();
        const itemName = String(
          document.getElementById("device-bnp-item-name")?.value || ""
        ).trim();
        if (!itemName) {
          throw new Error("Podaj nazwę pozycji z dokumentu BNP.");
        }
        setButtonBusy(button, true, "Tworzenie…", "Stwórz pozycję ze stanem 0");
        const response = await fetchDeviceJson("/admin/device/bnp-buyout/catalog", {
          method: "POST",
          timeoutMs: 60000,
          body: {
            serial: document.getElementById("device-bnp-serial").value.trim(),
            machine_table_id: Number(deviceState.bnpLookup.machine.id_maszyna_table),
            expected_ewidencja: String(deviceState.bnpLookup.machine.ewidencja || "").trim(),
            warehouse_index: document
              .getElementById("device-bnp-warehouse-index")
              .value.trim(),
            item_name: itemName,
          },
        });
        setInfo(response.message || "Kartoteka wykupu BNP została przygotowana.");
        await loadBnpLookup({ preserveForm: true });
        setBnpStatus("Kartoteka istnieje na magazynie 27 ze stanem 0.");
      } catch (error) {
        setError(error.message);
        setBnpStatus(error.message, "error");
      } finally {
        setButtonBusy(button, false, "Tworzenie…", "Stwórz pozycję ze stanem 0");
      }
    });

  document.getElementById("device-bnp-complete-btn")?.addEventListener("click", async () => {
    setError("");
    setInfo("");
    clearBnpFinalResult();
    const button = document.getElementById("device-bnp-complete-btn");
    try {
      if (
        !deviceState.bnpLookup?.machine ||
        !deviceState.bnpLookup?.target_item ||
        !deviceState.bnpLookup?.can_complete
      ) {
        throw new Error("Urządzenie lub kartoteka nie są gotowe do finalizacji wykupu.");
      }
      if (!document.getElementById("device-bnp-confirm").checked) {
        throw new Error("Potwierdź dane i zakres operacji wykupu BNP.");
      }
      validateBnpIdentifiers();
      const documentFields = readBnpDocumentFields();
      setButtonBusy(button, true, "Zapisywanie…", "Zatwierdź wykup BNP");
      const response = await fetchDeviceJson("/admin/device/bnp-buyout/complete", {
        method: "POST",
        timeoutMs: 60000,
        body: {
          serial: document.getElementById("device-bnp-serial").value.trim(),
          machine_table_id: Number(deviceState.bnpLookup.machine.id_maszyna_table),
          warehouse_item_id: Number(deviceState.bnpLookup.target_item.id_magazyn_table),
          expected_ewidencja: String(deviceState.bnpLookup.machine.ewidencja || "").trim(),
          target_ewidencja: document
            .getElementById("device-bnp-target-ewidencja")
            .value.trim(),
          warehouse_index: document
            .getElementById("device-bnp-warehouse-index")
            .value.trim(),
          item_name: documentFields.itemName,
          external_document: documentFields.externalDocument,
          document_date: documentFields.documentDate,
          purchase_price_netto: documentFields.priceNetto,
        },
      });
      const buyout = response.buyout || {};
      deviceState.bnpLookup.can_complete = false;
      document.getElementById("device-bnp-confirm").checked = false;
      document.getElementById("device-bnp-create-catalog-action").hidden = true;
      document.getElementById("device-bnp-final-result-text").textContent =
        `${buyout.pz_number || "PZ"} | ${buyout.previous_ewidencja || "KP"} → ` +
        `${buyout.target_ewidencja || "WKP"} | magazyn 27: ${
          buyout.warehouse_quantity ?? 1
        }.`;
      document.getElementById("device-bnp-final-result").hidden = false;
      setInfo(response.message || "Wykup BNP został zapisany.");
      setBnpStatus("Wykup BNP zapisany poprawnie.");
    } catch (error) {
      setError(error.message);
      setBnpStatus(error.message, "error");
    } finally {
      setButtonBusy(button, false, "Zapisywanie…", "Zatwierdź wykup BNP");
      updateBnpCompleteState();
    }
  });

  [
    "device-bnp-target-ewidencja",
    "device-bnp-warehouse-index",
    "device-bnp-document",
    "device-bnp-document-date",
    "device-bnp-item-name",
    "device-bnp-price-netto",
  ].forEach((fieldId) => {
    document.getElementById(fieldId)?.addEventListener("input", () => {
      updateBnpGross();
      updateBnpCompleteState();
    });
  });
  document
    .getElementById("device-bnp-confirm")
    ?.addEventListener("change", updateBnpCompleteState);
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
    } else if (deviceState.view === "bnp-buyout") {
      await loadBnpView();
    } else if (deviceState.view === "warehouse") {
      await loadWarehouse();
    } else if (deviceState.view === "audit") {
      await loadAuditView();
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
      row.dataset.intakeSearch = `${
        deviceState.intakeItems[index].model.marka || ""
      } ${deviceState.intakeItems[index].model.model || ""} ${
        deviceState.intakeItems[index].serial || ""
      } ${deviceState.intakeItems[index].ewidencja || ""}`.toLocaleLowerCase("pl-PL");
      clearInvalidField(event.target);
      updateIntakeSummary();
      filterIntakeItems();
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
  document
    .getElementById("device-intake-list-search")
    ?.addEventListener("input", filterIntakeItems);
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
  document
    .getElementById("device-intake-document-date")
    ?.addEventListener("input", (event) => {
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
  const warehouseBody = document.getElementById("device-warehouse-body");
  warehouseBody?.addEventListener("dblclick", async (event) => {
    const row = event.target.closest("[data-device-detail-row]");
    if (!row) {
      return;
    }
    if (event.target.closest("button, a, input, select, textarea")) {
      return;
    }
    try {
      setError("");
      await openDeviceDetail(row.dataset.deviceDetailRow);
    } catch (error) {
      setError(error.message);
    }
  });
  warehouseBody?.addEventListener("keydown", async (event) => {
    if (!["Enter", " "].includes(event.key)) {
      return;
    }
    const row = event.target.closest("[data-device-detail-row]");
    if (!row) {
      return;
    }
    event.preventDefault();
    try {
      setError("");
      await openDeviceDetail(row.dataset.deviceDetailRow);
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
  document.getElementById("device-counter-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await saveDeviceCounters();
    } catch (error) {
      setError(error.message);
    }
  });
  document
    .getElementById("device-counter-allow-lower")
    ?.addEventListener("change", (event) => {
      document.getElementById("device-counter-reason-wrap").hidden = !event.target.checked;
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

function bindHistoryEvents() {
  document.getElementById("device-history-body")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-withdraw-operation]");
    if (!button) {
      return;
    }
    try {
      await openWithdrawalDialog(Number(button.dataset.withdrawOperation));
    } catch (error) {
      setError(error.message);
    }
  });
  document.getElementById("device-withdraw-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await submitWithdrawal();
    } catch (error) {
      setError(error.message);
    }
  });
}

async function initializeDevicePage() {
  initializeDeviceTheme();
  const token = readDeviceToken();
  if (!token) {
    window.location.replace("/");
    return;
  }
  prepareDeviceLayout();
  activateView();
  try {
    const me = await fetchDeviceJson("/auth/me");
    const sections = new Set(Array.isArray(me.sections) ? me.sections : []);
    if (!sections.has("device")) {
      throw new Error("Konto nie ma prawa „Obsługa urządzeń”.");
    }
    applyDeviceTheme(me.device_theme || "blue", { persist: true });
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
  bindBnpEvents();
  bindWarehouseEvents();
  bindHistoryEvents();
  bindIssueEvents();
  document
    .getElementById("device-theme-select")
    ?.addEventListener("change", async (event) => {
      const previousTheme = document.body.dataset.deviceTheme || "blue";
      const selectedTheme = event.target.value;
      applyDeviceTheme(selectedTheme, { persist: true });
      try {
        const response = await fetchDeviceJson("/auth/preferences/device-theme", {
          method: "PUT",
          body: { theme: selectedTheme },
        });
        applyDeviceTheme(response.theme, { persist: true });
      } catch (error) {
        applyDeviceTheme(previousTheme, { persist: true });
        setError(error.message);
      }
    });
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
