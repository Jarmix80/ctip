const DELIVERY_TOKEN_KEY = "admin-session-token";

const state = {
  cases: [],
  contracts: [],
  currentCase: null,
  availableDevices: [],
  templates: [],
};

function readToken() {
  return window.localStorage?.getItem(DELIVERY_TOKEN_KEY) || window.sessionStorage?.getItem(DELIVERY_TOKEN_KEY) || null;
}

function headers(json = false) {
  const token = readToken();
  if (!token) return null;
  const output = { "X-Admin-Session": token };
  if (json) output["Content-Type"] = "application/json";
  return output;
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function formatDate(value) {
  if (!value) return "-";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString("pl-PL");
}

function formJson(form) {
  const data = Object.fromEntries(new FormData(form).entries());
  for (const [key, value] of Object.entries(data)) {
    if (value === "") data[key] = null;
  }
  return data;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    if (typeof detail === "string") throw new Error(detail);
    if (detail?.message) throw new Error(detail.message);
    throw new Error("Błąd żądania CTIP.");
  }
  return data;
}

function setInfo(message, isError = false) {
  const box = document.getElementById("delivery-info");
  if (!box) return;
  box.hidden = !message;
  box.textContent = message || "";
  box.classList.toggle("error", Boolean(isError));
}

function switchView(view) {
  document.querySelectorAll(".delivery-view").forEach((node) => { node.hidden = true; });
  document.getElementById(`view-${view}`)?.removeAttribute("hidden");
  document.querySelectorAll(".delivery-nav-btn[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  if (view === "grenke") loadContracts().catch((err) => setInfo(err.message, true));
}

function switchDetailTab(tab) {
  document.querySelectorAll(".delivery-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.detailTab === tab);
  });
  document.querySelectorAll(".delivery-tab-panel").forEach((panel) => {
    panel.hidden = panel.id !== `detail-tab-${tab}`;
  });
}

function showCaseDetail() {
  document.getElementById("case-empty-state")?.setAttribute("hidden", "hidden");
  document.getElementById("view-detail")?.removeAttribute("hidden");
}

function clearCaseSelection() {
  state.currentCase = null;
  document.getElementById("view-detail")?.setAttribute("hidden", "hidden");
  document.getElementById("case-empty-state")?.removeAttribute("hidden");
  document.querySelectorAll(".delivery-case-card.active").forEach((node) => node.classList.remove("active"));
}

function openDialog(id) {
  const dialog = document.getElementById(id);
  if (!dialog) return;
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "open");
}

function closeDialog(id) {
  const dialog = document.getElementById(id);
  if (!dialog) return;
  if (typeof dialog.close === "function") dialog.close();
  else dialog.removeAttribute("open");
}

function renderCases(items) {
  const body = document.getElementById("delivery-cases-body");
  if (!body) return;
  if (!items.length) {
    body.innerHTML = '<p class="delivery-muted">Brak spraw dostaw lub odbiorów.</p>';
    clearCaseSelection();
    return;
  }
  body.innerHTML = items.map((item) => {
    const devices = Array.isArray(item.devices) ? item.devices : [];
    const deviceText = devices.length ? `${devices.length} urządzeń` : "brak urządzeń";
    const typeLabel = item.case_type === "pickup" ? "odbiór" : "dostawa";
    const active = state.currentCase?.id === item.id ? " active" : "";
    return `<article class="delivery-case-card${active}" data-case-id="${item.id}" data-action="open-case" tabindex="0">
      <div class="delivery-case-card-top">
        <span class="delivery-pill">${escapeHtml(typeLabel)}</span>
        <span class="delivery-case-id">#${item.id}</span>
      </div>
      <strong>${escapeHtml(item.customer_name || item.title || "Klient bez nazwy")}</strong>
      <span>${escapeHtml(item.title || "")}</span>
      <div class="delivery-case-meta">
        <span>${escapeHtml(formatDate(item.delivery_date))}</span>
        <span>${escapeHtml(item.delivery_time_window || item.status || "")}</span>
        <span>${escapeHtml(deviceText)}</span>
      </div>
    </article>`;
  }).join("");
}

async function loadCases() {
  const type = document.getElementById("case-type-filter")?.value || "";
  const params = new URLSearchParams({ include_done: "false" });
  if (type) params.set("case_type", type);
  const data = await fetchJson(`/admin/delivery/cases?${params.toString()}`, { headers: headers(false) });
  state.cases = data.items || [];
  const selectedExists = state.currentCase && state.cases.some((item) => item.id === state.currentCase.id);
  if (state.currentCase && !selectedExists) {
    clearCaseSelection();
  }
  renderCases(state.cases);
  if (!state.currentCase && state.cases.length) {
    openCase(state.cases[0].id).catch((err) => setInfo(err.message, true));
  }
}

function renderCaseDetail(item) {
  state.currentCase = item;
  document.getElementById("case-detail-title").textContent = item.title || `Sprawa #${item.id}`;
  document.getElementById("case-detail-subtitle").textContent = `${item.customer_name || "Klient bez nazwy"} | ${item.customer_nip || "brak NIP"}`;
  const badge = document.getElementById("case-detail-badge");
  if (badge) badge.textContent = item.case_type === "pickup" ? "odbiór" : "dostawa";

  const form = document.getElementById("case-update-form");
  if (form) {
    form.elements.title.value = item.title || "";
    form.elements.status.value = item.status || "new";
    form.elements.delivery_date.value = item.delivery_date || "";
    form.elements.delivery_time_window.value = item.delivery_time_window || "";
    form.elements.delivery_contact_name.value = item.delivery_contact_name || "";
    form.elements.delivery_contact_phone.value = item.delivery_contact_phone || "";
    form.elements.delivery_notes.value = item.delivery_notes || "";
    form.elements.service_notes.value = item.service_notes || "";
  }

  const loadAvailable = document.getElementById("load-available-devices");
  const help = document.getElementById("case-devices-help");
  if (loadAvailable) loadAvailable.hidden = item.case_type === "pickup";
  if (help) help.textContent = item.case_type === "pickup"
    ? "Odbiory pokazują urządzenia wybrane z klienta MS. Samo planowanie nie zmienia bazy MS."
    : "Urządzenia dostawy są rezerwowane w arkuszu, jeśli synchronizacja Google Sheets jest aktywna.";

  renderCaseDevices(item.devices || []);
  renderCaseTasks(item.tasks || []);
  renderCaseFiles([...(item.files || []), ...(item.mailbox_files || [])]);
  renderTemplates();
}

async function openCase(caseId) {
  const data = await fetchJson(`/admin/delivery/cases/${caseId}`, { headers: headers(false) });
  renderCaseDetail(data.item);
  showCaseDetail();
  switchView("cases");
  document.querySelectorAll(".delivery-case-card").forEach((node) => {
    node.classList.toggle("active", String(node.dataset.caseId) === String(caseId));
  });
}

function renderCaseDevices(devices) {
  const list = document.getElementById("case-devices-list");
  if (!list) return;
  if (!devices.length) {
    list.innerHTML = '<p class="delivery-muted">Brak urządzeń w sprawie.</p>';
    return;
  }
  list.innerHTML = devices.map((device) => `<article class="delivery-list-item">
    <strong>${escapeHtml(`${device.producer || ""} ${device.model || ""}`.trim() || "Urządzenie")}</strong>
    <span>${escapeHtml(device.serial || "brak S/N")} | ${escapeHtml(device.ewidencja || "brak nr ew.")}</span>
    <small>${escapeHtml(device.device_role || "delivery")} ${device.snapshot?.sheet_sync_status ? `| arkusz: ${device.snapshot.sheet_sync_status}` : ""}</small>
  </article>`).join("");
}

function availableDevicePayload(item) {
  return {
    source_type: item.source_type || "firebird_magazyn_28",
    source_row: Number(item.row || item.ms_id_magazyn_table) || null,
    row: Number(item.row || item.ms_id_magazyn_table) || null,
    producer: item.producer || null,
    model: item.model || item.name || null,
    serial: item.serial || null,
    ewidencja: item.ewidencja || item.index || null,
    ms_id_magazyn_table: Number(item.ms_id_magazyn_table || item.row) || null,
    firebird_machine_id: Number(item.ms_id_maszyna || 0) || null,
    snapshot: item,
  };
}

function renderAvailableDevices() {
  const list = document.getElementById("available-devices-list");
  if (!list) return;
  const query = (document.getElementById("available-device-search")?.value || "").toLowerCase();
  const currentRows = new Set((state.currentCase?.devices || []).map((device) => String(device.source_row || "")));
  const filtered = state.availableDevices.filter((item) => {
    const haystack = `${item.producer || ""} ${item.model || ""} ${item.serial || ""} ${item.ewidencja || ""} ${item.index || ""} ${item.name || ""}`.toLowerCase();
    return !query || haystack.includes(query);
  });
  if (!filtered.length) {
    list.innerHTML = '<p class="delivery-muted">Brak urządzeń do pokazania.</p>';
    return;
  }
  list.innerHTML = filtered.map((item) => {
    const row = String(item.row || item.ms_id_magazyn_table || "");
    const checked = currentRows.has(row) ? "checked" : "";
    const disabled = item.reserved_in_ctip && !checked ? "disabled" : "";
    const reserved = item.reserved_in_ctip ? `Rezerwacja: ${item.ctip_reservation?.label || "CTIP"}` : item.reservation_status || "";
    return `<label class="delivery-check-row ${disabled ? "disabled" : ""}">
      <input type="checkbox" value="${escapeHtml(row)}" ${checked} ${disabled}>
      <span><strong>${escapeHtml(`${item.producer || ""} ${item.model || item.name || ""}`.trim())}</strong><br>
      ${escapeHtml(item.serial || "brak S/N")} | ${escapeHtml(item.ewidencja || item.index || "brak indeksu")}<br>
      <small>${escapeHtml(reserved)}</small></span>
    </label>`;
  }).join("");
}

function renderCaseTasks(tasks) {
  const list = document.getElementById("case-tasks-list");
  if (!list) return;
  if (!tasks.length) {
    list.innerHTML = '<p class="delivery-muted">Brak zadań.</p>';
    return;
  }
  list.innerHTML = tasks.map((task) => `<article class="delivery-list-item">
    <strong>${escapeHtml(task.title)}</strong>
    <span>${escapeHtml(task.task_type)} | ${escapeHtml(task.status)} | ${escapeHtml(formatDate(task.due_date))} ${escapeHtml(task.due_time_window || "")}</span>
    <small>${escapeHtml(task.notes || "")}</small>
    ${task.status !== "done" ? `<button type="button" data-task-id="${task.id}" data-action="task-done">Oznacz jako wykonane</button>` : ""}
  </article>`).join("");
}

function renderCaseFiles(files) {
  const list = document.getElementById("case-files-list");
  if (!list) return;
  if (!files.length) {
    list.innerHTML = '<p class="delivery-muted">Brak plików.</p>';
    return;
  }
  list.innerHTML = files.map((file) => `<article class="delivery-list-item">
    <strong>${escapeHtml(file.original_name || file.file_name || "plik")}</strong>
    <span>${escapeHtml(file.file_type || file.kind || "plik")} | ${escapeHtml(file.source || "")}</span>
    <small>${file.exists === false ? "Brak pliku na dysku" : escapeHtml(formatDate(file.created_at))}</small>
    <a class="delivery-link" href="${escapeHtml(file.download_url)}" target="_blank" rel="noreferrer">Pobierz</a>
  </article>`).join("");
}

function renderTemplates() {
  const select = document.getElementById("document-template-select");
  if (!select) return;
  if (!state.templates.length) {
    select.innerHTML = '<option value="">Brak szablonów</option>';
    return;
  }
  select.innerHTML = state.templates.map((item) => {
    const ready = item.docx_ready ?? String(item.template_path || "").toLowerCase().endsWith(".docx");
    return `<option value="${escapeHtml(item.template_key)}" ${ready ? "" : "disabled"}>${escapeHtml(item.label)}${ready ? "" : " (wymaga konwersji DOCX)"}</option>`;
  }).join("");
}

async function loadTemplates() {
  const data = await fetchJson("/admin/delivery/document-templates", { headers: headers(false) });
  state.templates = data.items || [];
  renderTemplates();
}

function renderContracts(items) {
  const body = document.getElementById("delivery-contracts-body");
  if (!body) return;
  if (!items.length) {
    body.innerHTML = '<p class="delivery-muted">Brak wpisów końców umów dla wybranych filtrów.</p>';
    return;
  }
  body.innerHTML = items.map((item) => {
    const pending = item.status === "pending_confirmation";
    const schedule = (item.notification_schedule || []).map((row) => `${row.days_left} dni: ${formatDate(row.notify_date)}${row.sent ? " wysłano" : ""}`).join(" | ");
    return `<article class="delivery-contract">
      <span class="delivery-pill">${escapeHtml(item.status)}</span>
      <strong>${escapeHtml(item.customer_name || "Klient bez nazwy")}</strong>
      <p>Poczatek GRENKE: ${escapeHtml(formatDate(item.grenke_contract_start_date))} | Prefill końca: ${escapeHtml(formatDate(item.prefilled_end_date))} | Potwierdzona: ${escapeHtml(formatDate(item.confirmed_end_date))}</p>
      <small>${escapeHtml(item.contract_number || item.source_note || "")}</small>
      ${schedule ? `<small>${escapeHtml(schedule)}</small>` : ""}
      ${pending ? `<form data-contract-id="${item.id}" class="delivery-contract-confirm">
        <input name="confirmed_end_date" type="date" value="${escapeHtml(item.prefilled_end_date || "")}" required>
        <input name="contract_number" placeholder="Nr umowy" value="${escapeHtml(item.contract_number || "")}">
        <button type="submit">Potwierdź</button>
      </form>` : ""}
    </article>`;
  }).join("");
}

async function loadContracts() {
  const form = document.getElementById("grenke-filter-form");
  const params = new URLSearchParams();
  if (form) {
    const data = formJson(form);
    Object.entries(data).forEach(([key, value]) => { if (value) params.set(key, value); });
  }
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const data = await fetchJson(`/admin/delivery/grenke-contracts${suffix}`, { headers: headers(false) });
  state.contracts = data.items || [];
  renderContracts(state.contracts);
}

async function searchClientForDelivery() {
  const form = document.getElementById("delivery-manual-form");
  const resultBox = document.getElementById("delivery-client-search-result");
  if (!form || !resultBox) return;
  const company = form.elements.company_name.value.trim();
  const nip = form.elements.company_nip.value.trim();
  if (!company && !nip) {
    setInfo("Wpisz NIP albo nazwę klienta.", true);
    return;
  }
  const params = new URLSearchParams();
  if (company) params.set("q", company);
  if (nip) params.set("nip", nip);
  const data = await fetchJson(`/admin/delivery/clients/search?${params.toString()}`, { headers: headers(false) });
  const found = (data.items || []).filter((item) => item.found);
  resultBox.hidden = false;
  if (!found.length) {
    form.elements.firebird_client_id.value = "";
    form.elements.create_firebird_client.checked = true;
    resultBox.innerHTML = '<strong>Nie znaleziono klienta.</strong><br>Uzupełnij dane. Klient zostanie utworzony w MSerwisu, jeśli zostawisz zaznaczoną opcję utworzenia klienta.';
    return;
  }
  resultBox.innerHTML = `<strong>Znaleziono klienta:</strong>${found.map((item) => `<button type="button" data-client-id="${item.id_klient}" data-action="select-delivery-client" data-name="${escapeHtml(item.nazwa || "")}" data-nip="${escapeHtml(item.nip || "")}" data-email="${escapeHtml(item.email || "")}" data-phone="${escapeHtml(item.telefon || "")}">${escapeHtml(item.nazwa || `ID ${item.id_klient}`)}<br><small>${escapeHtml(item.nip || "")}</small></button>`).join("")}`;
}

async function createManualDelivery(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = formJson(form);
  body.create_firebird_client = new FormData(form).get("create_firebird_client") === "on";
  body.firebird_client_id = body.firebird_client_id ? Number(body.firebird_client_id) : null;
  body.devices = [];
  const data = await fetchJson("/admin/delivery/cases", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
  form.reset();
  document.getElementById("delivery-client-search-result")?.setAttribute("hidden", "hidden");
  closeDialog("new-delivery-dialog");
  setInfo("Utworzono dostawę. Wejdź w szczegóły, aby dodać urządzenia, zadania i dokumenty.");
  await loadCases();
  await openCase(data.item.id);
}

async function searchPickupClients(event) {
  event.preventDefault();
  const query = new FormData(event.currentTarget).get("query");
  const box = document.getElementById("pickup-client-results");
  const data = await fetchJson(`/admin/delivery/clients/search?q=${encodeURIComponent(query)}`, { headers: headers(false) });
  const found = (data.items || []).filter((item) => item.found);
  if (!box) return;
  if (!found.length) {
    box.innerHTML = '<p class="delivery-muted">Nie znaleziono klienta w MS.</p>';
    return;
  }
  box.innerHTML = found.map((item) => `<article class="delivery-list-item">
    <strong>${escapeHtml(item.nazwa || `ID ${item.id_klient}`)}</strong>
    <span>${escapeHtml(item.nip || "")} | ${escapeHtml(item.telefon || "")}</span>
    <button type="button" data-action="select-pickup-client" data-client-id="${item.id_klient}" data-name="${escapeHtml(item.nazwa || "")}" data-nip="${escapeHtml(item.nip || "")}">Wybierz</button>
  </article>`).join("");
}

function pickupDevicePayload(item) {
  return {
    source_type: "firebird_serial",
    source_row: Number(item.id_maszyna_table || item.id_maszyna) || null,
    producer: item.marka || null,
    model: item.model || null,
    serial: item.serial || item.serial2 || null,
    ewidencja: item.ewidencja || null,
    firebird_machine_id: Number(item.id_maszyna || 0) || null,
    id_maszyna: Number(item.id_maszyna || 0) || null,
    id_maszyna_table: Number(item.id_maszyna_table || 0) || null,
    snapshot: item,
  };
}

async function selectPickupClient(button) {
  const clientId = button.dataset.clientId;
  const form = document.getElementById("pickup-form");
  const clientBox = document.getElementById("pickup-selected-client");
  const devicesBox = document.getElementById("pickup-device-list");
  if (!form || !clientBox || !devicesBox) return;
  form.hidden = false;
  form.elements.firebird_client_id.value = clientId;
  clientBox.innerHTML = `<strong>${escapeHtml(button.dataset.name || `ID ${clientId}`)}</strong><br>${escapeHtml(button.dataset.nip || "")}`;
  const data = await fetchJson(`/admin/delivery/clients/${clientId}/devices`, { headers: headers(false) });
  const devices = data.items || [];
  devicesBox.dataset.devices = JSON.stringify(devices.map(pickupDevicePayload));
  if (!devices.length) {
    devicesBox.innerHTML = '<p class="delivery-muted">Klient nie ma aktywnych urządzeń w MS.</p>';
    return;
  }
  devicesBox.innerHTML = devices.map((device, index) => `<label class="delivery-check-row">
    <input type="checkbox" value="${index}">
    <span><strong>${escapeHtml(`${device.marka || ""} ${device.model || ""}`.trim() || "Urządzenie")}</strong><br>
    ${escapeHtml(device.serial || device.serial2 || "brak S/N")} | ${escapeHtml(device.ewidencja || "brak nr ew.")}<br>
    <small>ID: ${escapeHtml(device.id_maszyna || "")}</small></span>
  </label>`).join("");
}

async function createPickup(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const body = formJson(form);
  body.firebird_client_id = Number(body.firebird_client_id);
  const devices = JSON.parse(document.getElementById("pickup-device-list")?.dataset.devices || "[]");
  const selected = Array.from(document.querySelectorAll('#pickup-device-list input[type="checkbox"]:checked')).map((input) => devices[Number(input.value)]).filter(Boolean);
  body.devices = selected;
  const data = await fetchJson("/admin/delivery/pickups", {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
  form.reset();
  form.hidden = true;
  document.getElementById("pickup-client-results").innerHTML = "";
  closeDialog("pickup-dialog");
  setInfo("Utworzono sprawę odbioru.");
  await loadCases();
  await openCase(data.item.id);
}

async function updateCurrentCase(event) {
  event.preventDefault();
  if (!state.currentCase) return;
  const body = formJson(event.currentTarget);
  const data = await fetchJson(`/admin/delivery/cases/${state.currentCase.id}`, {
    method: "PATCH",
    headers: headers(true),
    body: JSON.stringify(body),
  });
  setInfo("Zapisano ustalenia sprawy.");
  renderCaseDetail(data.item);
  await loadCases();
}

async function loadAvailableDevices() {
  const data = await fetchJson("/admin/delivery/devices/available?limit=1000", { headers: headers(false) });
  state.availableDevices = data.items || [];
  document.getElementById("available-devices-panel").hidden = false;
  renderAvailableDevices();
}

async function saveCurrentCaseDevices() {
  if (!state.currentCase) return;
  const checkedRows = new Set(Array.from(document.querySelectorAll('#available-devices-list input[type="checkbox"]:checked')).map((input) => input.value));
  const devices = state.availableDevices.filter((item) => checkedRows.has(String(item.row || item.ms_id_magazyn_table || ""))).map(availableDevicePayload);
  const data = await fetchJson(`/admin/delivery/cases/${state.currentCase.id}/devices`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify({ mode: "replace", device_role: "delivery", sync_sheet: true, devices }),
  });
  if (data.sheet_warning) setInfo(`Zapisano urządzenia, ale arkusz zgłosił problem: ${data.sheet_warning}`, true);
  else setInfo("Zapisano urządzenia sprawy.");
  renderCaseDetail(data.item);
  await loadCases();
}

async function createTask(event) {
  event.preventDefault();
  if (!state.currentCase) return;
  const body = formJson(event.currentTarget);
  const data = await fetchJson(`/admin/delivery/cases/${state.currentCase.id}/tasks`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
  event.currentTarget.reset();
  state.currentCase.tasks = [...(state.currentCase.tasks || []), data.item];
  renderCaseTasks(state.currentCase.tasks);
  setInfo("Dodano zadanie.");
}

async function markTaskDone(taskId) {
  if (!state.currentCase) return;
  const data = await fetchJson(`/admin/delivery/cases/${state.currentCase.id}/tasks/${taskId}`, {
    method: "PATCH",
    headers: headers(true),
    body: JSON.stringify({ status: "done" }),
  });
  state.currentCase.tasks = (state.currentCase.tasks || []).map((task) => task.id === data.item.id ? data.item : task);
  renderCaseTasks(state.currentCase.tasks);
}

async function uploadFile(event) {
  event.preventDefault();
  if (!state.currentCase) return;
  const form = event.currentTarget;
  const formData = new FormData(form);
  const fileType = encodeURIComponent(formData.get("file_type") || "other");
  const response = await fetch(`/admin/delivery/cases/${state.currentCase.id}/files?file_type=${fileType}`, {
    method: "POST",
    headers: headers(false),
    body: formData,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Nie udało się dodać pliku.");
  form.reset();
  const detail = await fetchJson(`/admin/delivery/cases/${state.currentCase.id}`, { headers: headers(false) });
  renderCaseDetail(detail.item);
  setInfo("Dodano plik do sprawy.");
}

async function generateDocument(event) {
  event.preventDefault();
  if (!state.currentCase) return;
  const body = formJson(event.currentTarget);
  const data = await fetchJson(`/admin/delivery/cases/${state.currentCase.id}/documents/generate`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(body),
  });
  state.currentCase.files = [...(state.currentCase.files || []), data.item];
  renderCaseFiles([...(state.currentCase.files || []), ...(state.currentCase.mailbox_files || [])]);
  setInfo("Wygenerowano dokument DOCX.");
}

async function confirmContract(event) {
  const form = event.target.closest("form[data-contract-id]");
  if (!form) return;
  event.preventDefault();
  const id = form.dataset.contractId;
  await fetchJson(`/admin/delivery/grenke-contracts/${id}/confirm`, {
    method: "POST",
    headers: headers(true),
    body: JSON.stringify(formJson(form)),
  });
  setInfo("Potwierdzono datę końca umowy.");
  await loadContracts();
}

async function init() {
  const app = document.getElementById("delivery-app");
  const login = document.getElementById("delivery-login");
  try {
    const tokenHeaders = headers(false);
    if (!tokenHeaders) throw new Error("Brak sesji");
    const me = await fetchJson("/auth/me", { headers: tokenHeaders });
    const sections = new Set(Array.isArray(me.sections) ? me.sections : []);
    if (!sections.has("delivery")) throw new Error("Brak uprawnień");
    app.hidden = false;
    login.hidden = true;
    await Promise.all([loadCases(), loadTemplates()]);
  } catch (err) {
    app.hidden = true;
    login.hidden = false;
    return;
  }

  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  document.querySelectorAll("[data-open-dialog]").forEach((button) => button.addEventListener("click", () => openDialog(button.dataset.openDialog)));
  document.querySelector("[data-action='back-to-list']")?.addEventListener("click", clearCaseSelection);
  document.querySelectorAll("[data-detail-tab]").forEach((button) => {
    button.addEventListener("click", () => switchDetailTab(button.dataset.detailTab));
  });
  document.getElementById("delivery-refresh")?.addEventListener("click", () => loadCases().then(() => setInfo("Odświeżono dane.")));
  document.getElementById("case-type-filter")?.addEventListener("change", loadCases);
  document.getElementById("delivery-cases-body")?.addEventListener("click", (event) => {
    const item = event.target.closest("[data-action='open-case']");
    if (item) openCase(item.dataset.caseId).catch((err) => setInfo(err.message, true));
  });
  document.getElementById("delivery-cases-body")?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const item = event.target.closest("[data-action='open-case']");
    if (!item) return;
    event.preventDefault();
    openCase(item.dataset.caseId).catch((err) => setInfo(err.message, true));
  });
  document.querySelector("[data-action='search-delivery-client']")?.addEventListener("click", () => searchClientForDelivery().catch((err) => setInfo(err.message, true)));
  document.getElementById("delivery-client-search-result")?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action='select-delivery-client']");
    if (!button) return;
    const form = document.getElementById("delivery-manual-form");
    form.elements.firebird_client_id.value = button.dataset.clientId;
    form.elements.company_name.value = button.dataset.name || form.elements.company_name.value;
    form.elements.company_nip.value = button.dataset.nip || form.elements.company_nip.value;
    form.elements.company_email.value = button.dataset.email || form.elements.company_email.value;
    form.elements.company_phone.value = button.dataset.phone || form.elements.company_phone.value;
    form.elements.create_firebird_client.checked = false;
    document.getElementById("delivery-client-search-result").innerHTML = `<strong>Wybrano klienta MS:</strong> ${escapeHtml(button.dataset.name || button.dataset.clientId)}`;
  });
  document.getElementById("delivery-manual-form")?.addEventListener("submit", (event) => createManualDelivery(event).catch((err) => setInfo(err.message, true)));
  document.getElementById("pickup-search-form")?.addEventListener("submit", (event) => searchPickupClients(event).catch((err) => setInfo(err.message, true)));
  document.getElementById("pickup-client-results")?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action='select-pickup-client']");
    if (button) selectPickupClient(button).catch((err) => setInfo(err.message, true));
  });
  document.getElementById("pickup-form")?.addEventListener("submit", (event) => createPickup(event).catch((err) => setInfo(err.message, true)));
  document.getElementById("case-update-form")?.addEventListener("submit", (event) => updateCurrentCase(event).catch((err) => setInfo(err.message, true)));
  document.getElementById("load-available-devices")?.addEventListener("click", () => loadAvailableDevices().catch((err) => setInfo(err.message, true)));
  document.getElementById("available-device-search")?.addEventListener("input", renderAvailableDevices);
  document.getElementById("save-case-devices")?.addEventListener("click", () => saveCurrentCaseDevices().catch((err) => setInfo(err.message, true)));
  document.getElementById("task-form")?.addEventListener("submit", (event) => createTask(event).catch((err) => setInfo(err.message, true)));
  document.getElementById("case-tasks-list")?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action='task-done']");
    if (button) markTaskDone(Number(button.dataset.taskId)).catch((err) => setInfo(err.message, true));
  });
  document.getElementById("file-upload-form")?.addEventListener("submit", (event) => uploadFile(event).catch((err) => setInfo(err.message, true)));
  document.getElementById("document-form")?.addEventListener("submit", (event) => generateDocument(event).catch((err) => setInfo(err.message, true)));
  document.getElementById("grenke-filter-form")?.addEventListener("submit", (event) => { event.preventDefault(); loadContracts().catch((err) => setInfo(err.message, true)); });
  document.getElementById("delivery-contracts-body")?.addEventListener("submit", (event) => confirmContract(event).catch((err) => setInfo(err.message, true)));
  document.getElementById("run-grenke-reminders")?.addEventListener("click", async () => {
    try {
      const data = await fetchJson("/admin/delivery/grenke-contracts/reminders/run", { method: "POST", headers: headers(true), body: "{}" });
      setInfo(`Przypomnienia: sprawdzone ${data.result.checked}, SMS ${data.result.sms_queued}, e-mail ${data.result.emails_sent}, bez odbiorcow ${data.result.skipped_no_recipients || 0}.`);
      await loadContracts();
    } catch (err) {
      setInfo(err.message, true);
    }
  });
}

window.addEventListener("DOMContentLoaded", init);
