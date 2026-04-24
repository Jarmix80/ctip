const GENFORM_TOKEN_KEY = "admin-session-token";

function readToken() {
  return (
    window.localStorage?.getItem(GENFORM_TOKEN_KEY) ||
    window.sessionStorage?.getItem(GENFORM_TOKEN_KEY) ||
    null
  );
}

function storeToken(token, remember) {
  try {
    window.localStorage?.removeItem(GENFORM_TOKEN_KEY);
    window.sessionStorage?.removeItem(GENFORM_TOKEN_KEY);
    if (!token) {
      return;
    }
    if (remember) {
      window.localStorage?.setItem(GENFORM_TOKEN_KEY, token);
    } else {
      window.sessionStorage?.setItem(GENFORM_TOKEN_KEY, token);
    }
    window.dispatchEvent(new Event("ctip:session-changed"));
  } catch (err) {
    console.error("Nie udało się zapisać tokenu generatora", err);
  }
}

function formatDate(value) {
  if (!value) {
    return "—";
  }
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return date.toLocaleString("pl-PL", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch (err) {
    return value;
  }
}

function statusLabel(status) {
  const mapped = {
    GENERATED: "Wygenerowany",
    DISPATCHED: "Wysłany",
    SUBMITTED: "Wypełniony",
    EXPIRED: "Wygasły",
  };
  return mapped[status] || status || "Nieznany";
}

function statusClass(status) {
  if (status === "SUBMITTED") {
    return "success";
  }
  if (status === "EXPIRED") {
    return "warning";
  }
  return "info";
}

function workflowStageLabel(stage) {
  const mapped = {
    FORM_SUBMITTED: "Formularz wypelniony",
    CLIENT_READY: "Klient gotowy",
    DEVICES_SELECTED: "Urzadzenia wybrane",
    PROFORMA_CREATED: "Proforma gotowa",
  };
  return mapped[stage] || stage || "Brak";
}

function workflowBusinessStatusLabel(status) {
  const mapped = {
    DRAFT: "Robocza",
    PENDING_APPROVAL: "Umowa wyslana do podpisu klienta",
    APPROVED: "Akceptacja umowy - mozna dostarczac",
    ZEROWKA: "Zerowka",
    REJECTED: "Odrzucono",
  };
  return mapped[status] || status || "Brak";
}

function parsePriceValue(value) {
  const normalized = String(value ?? "")
    .trim()
    .replace(/\s+/g, "")
    .replace(",", ".");
  if (!normalized) {
    return null;
  }
  const parsed = Number(normalized);
  if (!Number.isFinite(parsed)) {
    return null;
  }
  return parsed;
}

function formatPriceValue(value) {
  if (!Number.isFinite(value)) {
    return "";
  }
  return value.toFixed(2);
}

function grossToNet(value, vatRate = 23) {
  const parsed = parsePriceValue(value);
  if (parsed === null) {
    return "";
  }
  return formatPriceValue(parsed / (1 + vatRate / 100));
}

function netToGross(value, vatRate = 23) {
  const parsed = parsePriceValue(value);
  if (parsed === null) {
    return "";
  }
  return formatPriceValue(parsed * (1 + vatRate / 100));
}

function uniqueValues(items, key) {
  const values = new Set();
  (items || []).forEach((item) => {
    const value = String(item?.[key] || "").trim();
    if (value) {
      values.add(value);
    }
  });
  return Array.from(values).sort((left, right) => left.localeCompare(right, "pl"));
}

function workflowDeviceSourceType(item) {
  return String(item?.source_type || "firebird_magazyn_28");
}

function workflowDeviceKey(item) {
  const explicitKey = String(item?.source_key || "").trim();
  if (explicitKey) {
    return explicitKey;
  }
  const row = Number(item?.row || 0);
  if (!Number.isFinite(row) || row <= 0) {
    return "";
  }
  return `${workflowDeviceSourceType(item)}:${row}`;
}

const BANK_BUYER = {
  name: '"GRENKELEASING" SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ',
  street: "ul. abpa Antoniego Baraniaka 88",
  postalCode: "61-131",
  city: "Poznań",
  nip: "782-22-75-815",
};

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function initializeGenForm() {
  const shell = document.querySelector(".genform-shell");
  const loginSection = document.getElementById("genform-login");
  const appSection = document.getElementById("genform-app");
  if (!loginSection || !appSection) {
    return;
  }

  const loginForm = document.getElementById("genform-login-form");
  const loginError = document.getElementById("genform-login-error");
  const passwordInput = document.getElementById("genform-password");
  const passwordToggleBtn = document.getElementById("genform-password-toggle");
  const createForm = document.getElementById("genform-create-form");
  const refreshBtn = document.getElementById("genform-refresh");
  const logoutBtn = document.getElementById("genform-logout");
  const userLine = document.getElementById("genform-user-line");
  const tableBody = document.getElementById("genform-table-body");
  const errorBox = document.getElementById("genform-error");
  const successBox = document.getElementById("genform-success");
  const generatedBox = document.getElementById("genform-generated-box");
  const generatedLink = document.getElementById("genform-generated-link");
  const copyLinkBtn = document.getElementById("genform-copy-link");
  const openLink = document.getElementById("genform-open-link");
  const expiresOnInput = document.getElementById("gf-expires-on");
  const detailModal = document.getElementById("genform-detail-modal");
  const detailCloseBtn = document.getElementById("genform-detail-close");
  const detailStatus = document.getElementById("genform-detail-status");
  const detailSummary = document.getElementById("genform-detail-summary");
  const detailContent = document.getElementById("genform-detail-content");
  const detailEmpty = document.getElementById("genform-detail-empty");
  const detailCompany = document.getElementById("genform-detail-company");
  const detailRepresentatives = document.getElementById("genform-detail-representatives");
  const detailPrintBtn = document.getElementById("genform-detail-print");
  const detailPdfBtn = document.getElementById("genform-detail-pdf");
  const workflowModal = document.getElementById("genform-workflow-modal");
  const workflowCloseBtn = document.getElementById("genform-workflow-close");
  const workflowSubtitle = document.getElementById("genform-workflow-subtitle");
  const workflowSummary = document.getElementById("genform-workflow-summary");
  const workflowDeviceSearch = document.getElementById("genform-workflow-device-search");
  const workflowDeviceStatusFilter = document.getElementById("genform-workflow-device-status-filter");
  const workflowDeviceReservationFilter = document.getElementById(
    "genform-workflow-device-reservation-filter"
  );
  const workflowDeviceNote = document.getElementById("genform-workflow-device-note");
  const workflowSheetCacheNote = document.getElementById("genform-workflow-sheet-cache-note");
  const workflowSelectionSummary = document.getElementById("genform-workflow-selection-summary");
  const workflowDeviceBody = document.getElementById("genform-workflow-device-body");
  const workflowSheetAssignee = document.getElementById("genform-workflow-sheet-assignee");
  const workflowSaveDevicesBtn = document.getElementById("genform-workflow-save-devices");
  const workflowSaveDevicesBtnBottom = document.getElementById("genform-workflow-save-devices-bottom");
  const workflowRefreshSheetBtn = document.getElementById("genform-workflow-refresh-sheet-status");
  const workflowRefreshSheetBtnBottom = document.getElementById(
    "genform-workflow-refresh-sheet-status-bottom"
  );
  const workflowReleaseSheetBtn = document.getElementById("genform-workflow-release-sheet");
  const workflowReleaseSheetBtnBottom = document.getElementById(
    "genform-workflow-release-sheet-bottom"
  );
  const workflowNote = document.getElementById("genform-workflow-note");
  const proformaModal = document.getElementById("genform-proforma-modal");
  const proformaCloseBtn = document.getElementById("genform-proforma-close");
  const proformaSubtitle = document.getElementById("genform-proforma-subtitle");
  const proformaSummary = document.getElementById("genform-proforma-summary");
  const proformaCustomerCard = document.getElementById("genform-proforma-customer-card");
  const proformaBuyerCard = document.getElementById("genform-proforma-buyer-card");
  const proformaDevicesStatus = document.getElementById("genform-proforma-devices-status");
  const proformaNumberStatus = document.getElementById("genform-proforma-number-status");
  const proformaClientStatus = document.getElementById("genform-proforma-client-status");
  const proformaBank = document.getElementById("genform-proforma-bank");
  const proformaBankNote = document.getElementById("genform-proforma-bank-note");
  const proformaSheetAssignee = document.getElementById("genform-proforma-sheet-assignee");
  const proformaCreateBtn = document.getElementById("genform-proforma-create");
  const proformaCreateBtnTop = document.getElementById("genform-proforma-create-top");
  const proformaPdfLink = document.getElementById("genform-proforma-pdf");
  const proformaPdfLinkTop = document.getElementById("genform-proforma-pdf-top");
  const proformaResetBtn = document.getElementById("genform-proforma-reset");
  const proformaDeviceBody = document.getElementById("genform-proforma-device-body");
  const proformaNote = document.getElementById("genform-proforma-note");

  let token = readToken();
  let openedFormId = null;
  let currentDetailData = null;
  let latestForms = [];
  let activeWorkflowFormId = null;
  let activeWorkflowData = null;
  setDefaultExpiresOn();

  function setBusy(element, busy, labelBusy, labelIdle) {
    if (!element) {
      return;
    }
    element.disabled = busy;
    if (labelBusy && labelIdle) {
      element.textContent = busy ? labelBusy : labelIdle;
    }
  }

  function setAuthLayout(isAuth) {
    if (!shell) {
      return;
    }
    shell.classList.toggle("is-auth", isAuth);
  }

  function closeDetailModal() {
    if (!detailModal) {
      return;
    }
    detailModal.hidden = true;
    openedFormId = null;
    currentDetailData = null;
    clearPrintMode();
    if (detailSummary) {
      detailSummary.innerHTML = "";
    }
    if (detailCompany) {
      detailCompany.innerHTML = "";
    }
    if (detailRepresentatives) {
      detailRepresentatives.innerHTML = "";
    }
    if (detailContent) {
      detailContent.hidden = true;
    }
    if (detailEmpty) {
      detailEmpty.hidden = true;
      detailEmpty.textContent = "";
    }
  }

  function closeWorkflowModal() {
    if (!workflowModal) {
      return;
    }
    workflowModal.hidden = true;
    activeWorkflowFormId = null;
    activeWorkflowData = null;
    if (workflowSummary) {
      workflowSummary.innerHTML = "";
    }
    if (workflowDeviceBody) {
      workflowDeviceBody.innerHTML = "<tr><td colspan='9'>Brak danych urządzeń.</td></tr>";
    }
    if (workflowNote) {
      workflowNote.textContent = "";
    }
  }

  function closeProformaModal() {
    if (!proformaModal) {
      return;
    }
    proformaModal.hidden = true;
    if (proformaSummary) {
      proformaSummary.innerHTML = "";
    }
    if (proformaCustomerCard) {
      proformaCustomerCard.innerHTML = "";
    }
    if (proformaBuyerCard) {
      proformaBuyerCard.innerHTML = "";
    }
    if (proformaDeviceBody) {
      proformaDeviceBody.innerHTML = "<tr><td colspan='5'>Brak wybranych urządzeń.</td></tr>";
    }
    if (proformaNote) {
      proformaNote.textContent = "";
    }
    if (proformaPdfLink) {
      proformaPdfLink.hidden = true;
      proformaPdfLink.href = "#";
    }
    if (proformaPdfLinkTop) {
      proformaPdfLinkTop.hidden = true;
      proformaPdfLinkTop.href = "#";
    }
  }

  function showLogin(message = "") {
    loginSection.hidden = false;
    appSection.hidden = true;
    setAuthLayout(true);
    closeDetailModal();
    closeWorkflowModal();
    closeProformaModal();
    if (message) {
      loginError.textContent = message;
      loginError.hidden = false;
    } else {
      loginError.hidden = true;
      loginError.textContent = "";
    }
  }

  function showApp(user) {
    loginSection.hidden = true;
    appSection.hidden = false;
    closeDetailModal();
    closeWorkflowModal();
    closeProformaModal();
    setAuthLayout(false);
    const fullName = [user.first_name, user.last_name].filter(Boolean).join(" ");
    userLine.textContent = `${fullName || user.email} (${user.role})`;
    loginError.hidden = true;
    loginError.textContent = "";
  }

  function clearMessages() {
    errorBox.hidden = true;
    errorBox.textContent = "";
    successBox.hidden = true;
    successBox.textContent = "";
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  }

  function showSuccess(message) {
    successBox.textContent = message;
    successBox.hidden = false;
  }

  function headers(includeJson = false) {
    const requestHeaders = {};
    if (token) {
      requestHeaders["X-Admin-Session"] = token;
    }
    if (includeJson) {
      requestHeaders["Content-Type"] = "application/json";
    }
    return requestHeaders;
  }

  function clearToken() {
    token = null;
    storeToken(null, false);
  }

  function setDefaultExpiresOn() {
    if (!expiresOnInput) {
      return;
    }
    const target = new Date();
    target.setDate(target.getDate() + 7);
    const yyyy = target.getFullYear();
    const mm = String(target.getMonth() + 1).padStart(2, "0");
    const dd = String(target.getDate()).padStart(2, "0");
    expiresOnInput.value = `${yyyy}-${mm}-${dd}`;
  }

  function normalizeText(value) {
    if (value === null || value === undefined) {
      return "";
    }
    return String(value).trim();
  }

  function escapeHtmlAttribute(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/\r?\n/g, "&#10;");
  }

  function formatDetailValue(value) {
    return normalizeText(value) || "—";
  }

  function buildAddress(data, prefix) {
    const street = normalizeText(data?.[`${prefix}_street`]);
    const buildingNo = normalizeText(data?.[`${prefix}_building_no`]);
    const apartmentNo = normalizeText(data?.[`${prefix}_apartment_no`]);
    const postalCode = normalizeText(data?.[`${prefix}_postal_code`]);
    const city = normalizeText(data?.[`${prefix}_city`]);
    const lineOne = [street, buildingNo, apartmentNo ? `lok. ${apartmentNo}` : ""]
      .filter(Boolean)
      .join(", ");
    const lineTwo = [postalCode, city].filter(Boolean).join(" ");
    return [lineOne, lineTwo].filter(Boolean).join("\n");
  }

  function renderCopyButton(value, label) {
    const copyValue = normalizeText(value);
    if (!copyValue) {
      return "";
    }
    return `<button
      type="button"
      class="genform-copy-btn"
      data-copy-value="${escapeHtmlAttribute(copyValue)}"
      data-copy-label="${escapeHtmlAttribute(label)}"
    >Kopiuj</button>`;
  }

  function renderFieldCards(fields, { itemClass = "genform-detail-field" } = {}) {
    return fields
      .map((field) => {
        const value = formatDetailValue(field.value);
        return `<div class="${itemClass}">
          <dt>${escapeHtml(field.label)}</dt>
          <div class="genform-detail-field-row">
            <dd>${escapeHtml(value)}</dd>
            ${renderCopyButton(value === "—" ? "" : value, field.label)}
          </div>
        </div>`;
      })
      .join("");
  }

  function buildSummaryFields(detailData) {
    const item = detailData.item || {};
    return [
      { label: "ID formularza", value: item.id ? String(item.id) : "" },
      { label: "Nazwa własna", value: item.customer_name },
      { label: "E-mail kontaktowy", value: item.customer_email },
      { label: "Telefon kontaktowy", value: item.customer_phone },
      { label: "Utworzone przez", value: item.created_by_name || "—" },
      { label: "Status", value: statusLabel(item.status) },
      { label: "Utworzono", value: formatDate(item.created_at) },
      {
        label: "Wypełniono",
        value: formatDate(detailData.submittedMeta?.submitted_at || item.submitted_at || ""),
      },
    ];
  }

  function buildCompanyFields(payload) {
    return [
      { label: "Nazwa firmy", value: payload.company_name },
      { label: "NIP", value: payload.company_nip },
      { label: "Nr telefonu firmowy", value: payload.company_phone },
      { label: "E-mail firmowy", value: payload.company_email },
      { label: "E-mail do e-faktur", value: payload.billing_email },
      { label: "Adres siedziby", value: buildAddress(payload, "registered") },
      { label: "Adres korespondencyjny", value: buildAddress(payload, "correspondence") },
    ];
  }

  function buildRepresentativeTitle(item, index) {
    const fullName = [normalizeText(item.first_name), normalizeText(item.last_name)]
      .filter(Boolean)
      .join(" ");
    return fullName || `Reprezentant ${index + 1}`;
  }

  function buildRepresentativeFields(item, index) {
    const representativeEmail =
      item.representative_email || item.email || item.contact_email || "";
    const representativePhone =
      item.representative_phone || item.phone || item.contact_phone || item.telephone || "";
    return {
      title: buildRepresentativeTitle(item, index),
      fields: [
        { label: "Osoba", value: buildRepresentativeTitle(item, index) },
        { label: "E-mail reprezentanta", value: representativeEmail },
        { label: "Telefon reprezentanta", value: representativePhone },
        { label: "PESEL", value: item.pesel },
        { label: "Data urodzenia", value: item.birth_date },
        { label: "Dokument", value: item.document_type },
        { label: "Nr dokumentu", value: item.document_number },
        { label: "Data wydania", value: item.document_issue_date },
        { label: "Data ważności", value: item.document_expiry_date },
      ],
    };
  }

  function renderRepresentatives(items) {
    if (!Array.isArray(items) || !items.length) {
      return "<p class=\"genform-detail-empty\">Brak reprezentantów zapisanych w formularzu.</p>";
    }
    return items
      .map((item, index) => {
        const representative = buildRepresentativeFields(item, index);
        return `<article class="genform-detail-representative">
          <header class="genform-detail-representative-header">
            <h5>${escapeHtml(representative.title)}</h5>
          </header>
          <dl class="genform-detail-fields">
            ${renderFieldCards(representative.fields)}
          </dl>
        </article>`;
      })
      .join("");
  }

  function renderDetailSections(detailData) {
    if (!detailSummary || !detailContent || !detailEmpty || !detailCompany || !detailRepresentatives) {
      return;
    }
    detailSummary.innerHTML = renderFieldCards(buildSummaryFields(detailData), {
      itemClass: "genform-detail-summary-card",
    });

    const payload =
      detailData.submittedPayload && typeof detailData.submittedPayload === "object"
        ? detailData.submittedPayload
        : null;
    if (!payload) {
      detailCompany.innerHTML = "";
      detailRepresentatives.innerHTML = "";
      detailContent.hidden = true;
      detailEmpty.hidden = false;
      detailEmpty.textContent =
        "Klient nie zakończył jeszcze wypełniania formularza. Dostępne są wyłącznie informacje operacyjne.";
      return;
    }

    detailCompany.innerHTML = renderFieldCards(buildCompanyFields(payload));
    detailRepresentatives.innerHTML = renderRepresentatives(payload.representatives);
    detailContent.hidden = false;
    detailEmpty.hidden = true;
    detailEmpty.textContent = "";
  }

  function clearPrintMode() {
    document.body.classList.remove("genform-printing");
  }

  function triggerDetailPrint(mode) {
    if (!currentDetailData || !detailModal || detailModal.hidden) {
      showError("Najpierw otwórz szczegóły formularza.");
      return;
    }
    clearMessages();
    document.body.classList.add("genform-printing");
    if (mode === "pdf") {
      showSuccess(
        "Otworzono widok eksportu. W systemowym oknie drukowania wybierz „Zapisz jako PDF”."
      );
    } else {
      showSuccess("Otworzono widok do druku formularza.");
    }
    window.setTimeout(() => {
      window.print();
    }, 60);
  }

  function formatCustomerAddress(payload) {
    if (!payload || typeof payload !== "object") {
      return "";
    }
    const lineOne = [
      normalizeText(payload.registered_street),
      normalizeText(payload.registered_building_no),
      normalizeText(payload.registered_apartment_no)
        ? `lok. ${normalizeText(payload.registered_apartment_no)}`
        : "",
    ]
      .filter(Boolean)
      .join(" ");
    const lineTwo = [
      normalizeText(payload.registered_postal_code),
      normalizeText(payload.registered_city),
    ]
      .filter(Boolean)
      .join(" ");
    return [lineOne, lineTwo].filter(Boolean).join(", ");
  }

  function formatWorkflowDeviceLabel(item) {
    const combinedName = [item?.producer, item?.model].filter(Boolean).join(" ").trim();
    if (combinedName) {
      return combinedName;
    }
    return String(item?.name || item?.description || "Wybrane urządzenie").trim();
  }

  function getSelectedWorkflowDevices() {
    if (!activeWorkflowData?.available_devices) {
      return [];
    }
    return activeWorkflowData.available_devices.filter((item) => item.selected);
  }

  function ensureWorkflowDevicePrices() {
    if (!activeWorkflowData?.available_devices) {
      return;
    }
    activeWorkflowData.available_devices = activeWorkflowData.available_devices.map((item) => {
      const nextItem = { ...item };
      const gross = nextItem.price_gross || nextItem.price || "";
      if (!nextItem.price_gross && gross) {
        nextItem.price_gross = formatPriceValue(parsePriceValue(gross));
      }
      if (!nextItem.price_net && nextItem.price_gross) {
        nextItem.price_net = grossToNet(nextItem.price_gross, Number(nextItem.vat_rate || 23));
      }
      return nextItem;
    });
  }

  function getWorkflowDevicesMissingPrice() {
    return getSelectedWorkflowDevices().filter((item) => {
      const gross = parsePriceValue(item.price_gross || item.price || "");
      const net = parsePriceValue(item.price_net || "");
      return !(gross > 0 || net > 0);
    });
  }

  function formatWorkflowMissingPriceMessage(items) {
    if (!Array.isArray(items) || !items.length) {
      return "";
    }
    const labels = items.map((item) => formatWorkflowDeviceLabel(item));
    const preview = labels.slice(0, 3).join(", ");
    const suffix = labels.length > 3 ? ` i ${labels.length - 3} kolejne` : "";
    return `Uzupełnij cenę netto lub brutto dla: ${preview}${suffix}.`;
  }

  function workflowReservationLabel(item) {
    if (item?.reservation_status) {
      return String(item.reservation_status);
    }
    return "Brak rezerwacji";
  }

  function workflowReservationBadgeClass(item) {
    return item?.reservation_badge_class === "danger" ? "danger" : "soft";
  }

  function getWorkflowPdfUrl(workflow) {
    const proformaId = Number(workflow?.proforma_firebird_id || 0);
    if (!Number.isFinite(proformaId) || proformaId <= 0) {
      return "";
    }
    return `/flow/proforma/${proformaId}/pdf`;
  }

  function formatMultilineText(lines) {
    return lines.filter(Boolean).map((line) => escapeHtml(line)).join("<br>");
  }

  function buildBuyerHtml(payload, forBank) {
    if (forBank) {
      return formatMultilineText([
        BANK_BUYER.name,
        `NIP: ${BANK_BUYER.nip}`,
        `${BANK_BUYER.street}, ${BANK_BUYER.postalCode} ${BANK_BUYER.city}`,
      ]);
    }
    return formatMultilineText([
      payload.company_name || "Brak danych klienta",
      payload.company_nip ? `NIP: ${payload.company_nip}` : "",
      formatCustomerAddress(payload),
    ]);
  }

  function buildCustomerCardHtml(payload) {
    return formatMultilineText([
      payload.company_name || "Brak danych klienta",
      payload.company_nip ? `NIP: ${payload.company_nip}` : "",
      payload.company_email ? `E-mail: ${payload.company_email}` : "",
      payload.company_phone ? `Tel: ${payload.company_phone}` : "",
      formatCustomerAddress(payload),
    ]);
  }

  function buildStatusChecklist(item) {
    const workflow = item.workflow || {};
    const checklist = [];
    checklist.push({
      label: `Etap formularza: ${statusLabel(item.status)}`,
      done: item.status !== "EXPIRED",
    });
    checklist.push({
      label:
        Number(workflow.devices_selected_count || 0) > 0
          ? `Urządzenie: ${workflow.devices_selected_count}`
          : "Urządzenie: brak",
      done: Number(workflow.devices_selected_count || 0) > 0,
    });
    checklist.push({
      label: workflow.proforma_number ? `Proforma: ${workflow.proforma_number}` : "Proforma: brak",
      done: Boolean(workflow.proforma_number),
    });
    checklist.push({
      label: workflow.business_status
        ? `Status GRENKE: ${workflowBusinessStatusLabel(workflow.business_status)}`
        : "Status GRENKE: brak",
      done: Boolean(workflow.business_status),
    });
    return checklist;
  }

  function renderItems(items) {
    if (!Array.isArray(items) || !items.length) {
      tableBody.innerHTML = "<tr><td colspan='6'>Brak wygenerowanych formularzy.</td></tr>";
      return;
    }
    tableBody.innerHTML = items
      .map((item) => {
        const rowId = Number(item.id);
        const payload = item.payload && typeof item.payload === "object" ? item.payload : {};
        const statusChecklist = buildStatusChecklist(item);
        const workflow = item.workflow || {};
        const customerName = payload.company_name || item.customer_name || "—";
        const customerNip = payload.company_nip || item.customer_nip || "—";
        const customerEmail = payload.company_email || item.customer_email || "—";
        const customerPhone = payload.company_phone || item.customer_phone || "—";
        const customerAddress = formatCustomerAddress(payload) || "—";
        const hasWorkflowActions = item.status === "SUBMITTED";
        const msLabel = workflow.firebird_client_id
          ? workflow.firebird_client_status === "created"
            ? `Dodano nowego klienta ID ${workflow.firebird_client_id}`
            : `Powiązano z klientem ID ${workflow.firebird_client_id}`
          : item.status === "SUBMITTED"
            ? "Brak klienta w MS"
            : "Poza etapem Menadżera Serwisu";
        const grenkeLabel = workflow.business_status
          ? workflowBusinessStatusLabel(workflow.business_status)
          : "Brak";

        return `<tr data-form-id="${rowId}" tabindex="0">
          <td>
            <ul class="genform-status-list">
              ${statusChecklist
                .map(
                  (entry) => `<li><span class="genform-status-item"><span class="genform-status-dot ${
                    entry.done ? "success" : "danger"
                  }"></span>${escapeHtml(entry.label)}</span></li>`
                )
                .join("")}
            </ul>
          </td>
          <td><strong>${escapeHtml(rowId)}</strong></td>
          <td>
            <div class="genform-customer-cell">
              <strong>${escapeHtml(customerName)}</strong>
              <span class="genform-subtle">NIP: ${escapeHtml(customerNip)}</span>
              <span class="genform-subtle">E-mail: ${escapeHtml(customerEmail)}</span>
              <span class="genform-subtle">Tel: ${escapeHtml(customerPhone)}</span>
              <span class="genform-subtle">Adres: ${escapeHtml(customerAddress)}</span>
            </div>
          </td>
          <td class="genform-ms-cell">${escapeHtml(msLabel)}</td>
          <td>${escapeHtml(grenkeLabel)}</td>
          <td>
            <div class="genform-row-actions">
              <button type="button" class="genform-row-action" data-action="view" data-form-id="${rowId}">Wyświetl</button>
              ${
                hasWorkflowActions
                  ? `<button type="button" class="genform-row-action" data-action="devices" data-form-id="${rowId}">Dodaj urządzenie</button>
                     <button type="button" class="genform-row-action" data-action="proforma" data-form-id="${rowId}">Stwórz proformę</button>`
                  : ""
              }
              <button type="button" class="genform-row-action danger" data-action="deactivate" data-form-id="${rowId}">Dezaktywuj</button>
            </div>
          </td>
        </tr>`;
      })
      .join("");
  }

  function renderWorkflowSummary(data) {
    if (!workflowSummary) {
      return;
    }
    const form = data.form || {};
    const workflow = data.workflow || {};
    const payload = form.payload && typeof form.payload === "object" ? form.payload : {};
    const companyAddress = formatCustomerAddress(payload) || "—";
    const clientMsValue = workflow.firebird_client_id
      ? workflow.firebird_client_status === "created"
        ? `Dodano nowego klienta ID ${workflow.firebird_client_id}`
        : `Powiązano z klientem ID ${workflow.firebird_client_id}`
      : '<span class="genform-status warning">Brak</span>';
    const devicesValue = Number(workflow.devices_selected_count || 0) > 0
      ? String(workflow.devices_selected_count)
      : '<span class="genform-status warning">Brak</span>';
    const proformaValue = workflow.proforma_number
      ? escapeHtml(workflow.proforma_number)
      : '<span class="genform-status warning">Brak</span>';
    workflowSummary.innerHTML = `
      <article class="genform-detail-summary-card">
        <dt>Dane firmy</dt>
        <dd>
          <strong>${escapeHtml(payload.company_name || form.customer_name || "—")}</strong><br>
          NIP: ${escapeHtml(payload.company_nip || form.customer_nip || "—")}<br>
          E-mail: ${escapeHtml(payload.company_email || form.customer_email || "—")}<br>
          Tel: ${escapeHtml(payload.company_phone || form.customer_phone || "—")}<br>
          Adres: ${escapeHtml(companyAddress)}
        </dd>
      </article>
      <article class="genform-detail-summary-card">
        <dt>Status formularza</dt>
        <dd>${escapeHtml(statusLabel(form.status))}</dd>
      </article>
      <article class="genform-detail-summary-card">
        <dt>Etap workflow</dt>
        <dd>${escapeHtml(workflowStageLabel(workflow.stage))}</dd>
      </article>
      <article class="genform-detail-summary-card">
        <dt>Klient MS</dt>
        <dd>${clientMsValue}</dd>
      </article>
      <article class="genform-detail-summary-card">
        <dt>Urządzenia</dt>
        <dd>${devicesValue}</dd>
      </article>
      <article class="genform-detail-summary-card">
        <dt>Proforma</dt>
        <dd>${proformaValue}</dd>
      </article>
      <article class="genform-detail-summary-card">
        <dt>Status GRENKE</dt>
        <dd>${escapeHtml(workflowBusinessStatusLabel(workflow.business_status))}</dd>
      </article>
    `;
  }

  function updateWorkflowSelectionSummary() {
    if (!workflowSelectionSummary) {
      return;
    }
    const selectedDevices = getSelectedWorkflowDevices();
    if (!selectedDevices.length) {
      workflowSelectionSummary.textContent = "Brak wybranych urządzeń.";
      updateProformaState();
      return;
    }
    const selectedRows = selectedDevices.map((item) => Number(item.row || 0)).filter(Boolean);
    const grossTotal = selectedDevices.reduce((total, item) => {
      const parsed = parsePriceValue(item.price_gross || item.price || "");
      return total + (parsed || 0);
    }, 0);
    const baseSummary =
      selectedRows.length === 1
        ? `Wybrano 1 urządzenie: pozycja ${selectedRows[0]}.`
        : `Wybrano ${selectedRows.length} urządzeń: ${selectedRows.join(", ")}.`;
    workflowSelectionSummary.textContent =
      `${baseSummary} Suma brutto: ${formatPriceValue(grossTotal)} PLN.`;
    updateProformaState();
  }

  function renderWorkflowDevicePicker() {
    if (!workflowDeviceBody) {
      return;
    }
    if (!activeWorkflowData?.available_devices) {
      workflowDeviceBody.innerHTML = "<tr><td colspan='9'>Brak danych urządzeń.</td></tr>";
      updateWorkflowSelectionSummary();
      return;
    }
    const phrase = String(workflowDeviceSearch?.value || "").trim().toLowerCase();
    const statusFilter = String(workflowDeviceStatusFilter?.value || "").trim();
    const reservationFilter = String(workflowDeviceReservationFilter?.value || "").trim();
    const filtered = activeWorkflowData.available_devices.filter((item) => {
      if (statusFilter && String(item.status || "") !== statusFilter) {
        return false;
      }
      if (reservationFilter && String(item.reservation_filter_value || "") !== reservationFilter) {
        return false;
      }
      if (!phrase) {
        return true;
      }
      const haystack = [
        item.producer,
        item.model,
        item.index,
        item.serial,
        item.ewidencja,
        item.status,
        item.reservation_status,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(phrase);
    });
    if (!filtered.length) {
      workflowDeviceBody.innerHTML = "<tr><td colspan='9'>Brak urządzeń dla podanych filtrów.</td></tr>";
      updateWorkflowSelectionSummary();
      return;
    }
    workflowDeviceBody.innerHTML = filtered
      .map((item) => {
        const key = workflowDeviceKey(item);
        const lockedByOther = Boolean(item.locked_by_other);
        const checkboxDisabled = lockedByOther && !item.selected;
        const reservationLabel = workflowReservationLabel(item);
        const reservationMeta = item.reservation_form_id
          ? `<span class="genform-subtle">Formularz ${escapeHtml(item.reservation_form_id)}</span>`
          : "";
        return `
          <tr class="${lockedByOther ? "genform-workflow-row-locked" : ""}">
            <td>
              <input
                type="checkbox"
                data-workflow-device-key="${escapeHtmlAttribute(key)}"
                ${item.selected ? "checked" : ""}
                ${checkboxDisabled ? "disabled" : ""}
              >
            </td>
            <td>${escapeHtml(item.row || "—")}</td>
            <td>${escapeHtml(formatWorkflowDeviceLabel(item) || "—")}</td>
            <td>${escapeHtml(item.index || item.ewidencja || "—")}</td>
            <td>${escapeHtml(item.available_quantity || "—")}</td>
            <td>${escapeHtml(item.status || "—")}</td>
            <td>
              <div class="genform-reservation-stack">
                <span class="genform-status ${workflowReservationBadgeClass(item)}">${escapeHtml(reservationLabel)}</span>
                ${reservationMeta}
              </div>
            </td>
            <td>
              <input
                type="text"
                inputmode="decimal"
                class="genform-price-input"
                data-workflow-device-price-net-key="${escapeHtmlAttribute(key)}"
                value="${escapeHtml(item.price_net || "")}"
                placeholder="0.00"
                ${lockedByOther ? "disabled" : ""}
              >
            </td>
            <td>
              <input
                type="text"
                inputmode="decimal"
                class="genform-price-input"
                data-workflow-device-price-gross-key="${escapeHtmlAttribute(key)}"
                value="${escapeHtml(item.price_gross || item.price || "")}"
                placeholder="0.00"
                ${lockedByOther ? "disabled" : ""}
              >
            </td>
          </tr>
        `;
      })
      .join("");
    updateWorkflowSelectionSummary();
  }

  function syncProformaFormSelection() {
    if (!proformaSheetAssignee || !workflowSheetAssignee) {
      return;
    }
    proformaSheetAssignee.innerHTML =
      workflowSheetAssignee.innerHTML || '<option value="">Brak powiązania</option>';
    proformaSheetAssignee.value = workflowSheetAssignee.value || "";
  }

  function updateWorkflowButtonsState() {
    const workflow = activeWorkflowData?.workflow || {};
    const hasClient = Boolean(workflow.firebird_client_id);
    const selectedDevicesCount = getSelectedWorkflowDevices().length;
    const hasDevices = Number(workflow.devices_selected_count || 0) > 0 || selectedDevicesCount > 0;
    const hasProforma = Boolean(workflow.proforma_number);
    const missingPriceDevices = getWorkflowDevicesMissingPrice();
    const canCreateProforma = hasClient && hasDevices && !hasProforma && missingPriceDevices.length === 0;

    [proformaCreateBtn, proformaCreateBtnTop].forEach((button) => {
      if (!button) {
        return;
      }
      button.disabled = !canCreateProforma && !hasProforma;
      button.textContent = hasProforma
        ? "Proforma już istnieje"
        : !hasClient
          ? "Najpierw zapisz klienta"
          : !hasDevices
            ? "Najpierw wybierz urządzenia"
            : missingPriceDevices.length > 0
              ? "Uzupełnij ceny"
              : "Utwórz PROFORMĘ";
    });
    if (proformaResetBtn) {
      proformaResetBtn.disabled = !hasProforma;
    }
  }

  function renderProformaDeviceTable() {
    if (!proformaDeviceBody) {
      return;
    }
    const selectedDevices = getSelectedWorkflowDevices();
    const hasProforma = Boolean(activeWorkflowData?.workflow?.proforma_number);
    if (!selectedDevices.length) {
      proformaDeviceBody.innerHTML = "<tr><td colspan='5'>Brak wybranych urządzeń.</td></tr>";
      return;
    }
    proformaDeviceBody.innerHTML = selectedDevices
      .map((item, index) => {
        const key = workflowDeviceKey(item);
        const serialLine = String(item.serial || "").trim()
          ? `S/N: ${String(item.serial).trim()}`
          : "Brak numeru seryjnego";
        return `
          <tr>
            <td>${index + 1}</td>
            <td>
              <strong>${escapeHtml(formatWorkflowDeviceLabel(item))}</strong>
              <div class="genform-subtle">${escapeHtml(serialLine)}</div>
            </td>
            <td>${escapeHtml(item.index || item.ewidencja || "—")}</td>
            <td>
              ${
                hasProforma
                  ? `<span class="genform-price-readonly">${escapeHtml(item.price_net || "—")}</span>`
                  : `<input
                      type="text"
                      inputmode="decimal"
                      class="genform-price-input"
                      data-workflow-device-price-net-key="${escapeHtmlAttribute(key)}"
                      value="${escapeHtml(item.price_net || "")}"
                      placeholder="0.00"
                    >`
              }
            </td>
            <td>
              ${
                hasProforma
                  ? `<span class="genform-price-readonly">${escapeHtml(item.price_gross || item.price || "—")}</span>`
                  : `<input
                      type="text"
                      inputmode="decimal"
                      class="genform-price-input"
                      data-workflow-device-price-gross-key="${escapeHtmlAttribute(key)}"
                      value="${escapeHtml(item.price_gross || item.price || "")}"
                      placeholder="0.00"
                    >`
              }
            </td>
          </tr>
        `;
      })
      .join("");
  }

  function renderProformaRecipient() {
    if (!activeWorkflowData) {
      return;
    }
    const payload = activeWorkflowData.form?.payload || {};
    const workflow = activeWorkflowData.workflow || {};
    const forBank = Boolean(proformaBank?.checked);
    const selectedDevices = getSelectedWorkflowDevices();
    if (proformaCustomerCard) {
      proformaCustomerCard.innerHTML = buildCustomerCardHtml(payload);
    }
    if (proformaBuyerCard) {
      proformaBuyerCard.innerHTML = buildBuyerHtml(payload, forBank);
    }
    if (proformaDevicesStatus) {
      proformaDevicesStatus.innerHTML = selectedDevices.length
        ? escapeHtml(String(selectedDevices.length))
        : '<span class="genform-status warning">Brak</span>';
    }
    if (proformaNumberStatus) {
      proformaNumberStatus.innerHTML = workflow.proforma_number
        ? escapeHtml(workflow.proforma_number)
        : '<span class="genform-status warning">Brak</span>';
    }
    if (proformaClientStatus) {
      proformaClientStatus.innerHTML = workflow.firebird_client_id
        ? `Dodano nowego klienta ID ${escapeHtml(workflow.firebird_client_id)}`
        : '<span class="genform-status warning">Brak</span>';
    }
    if (proformaBankNote) {
      proformaBankNote.textContent = forBank
        ? `Bank: ${BANK_BUYER.name}, ${BANK_BUYER.street}, ${BANK_BUYER.postalCode} ${BANK_BUYER.city}, NIP ${BANK_BUYER.nip}.`
        : "Nabywca zostanie pobrany z danych formularza klienta.";
    }
  }

  function updateProformaPdfLinks() {
    const workflow = activeWorkflowData?.workflow || {};
    const pdfUrl = getWorkflowPdfUrl(workflow);
    [proformaPdfLink, proformaPdfLinkTop].forEach((link) => {
      if (!link) {
        return;
      }
      if (pdfUrl) {
        link.hidden = false;
        link.href = pdfUrl;
      } else {
        link.hidden = true;
        link.href = "#";
      }
    });
  }

  function updateProformaState() {
    if (!activeWorkflowData) {
      return;
    }
    const workflow = activeWorkflowData.workflow || {};
    const hasProforma = Boolean(workflow.proforma_number);
    const missingPriceDevices = getWorkflowDevicesMissingPrice();
    renderProformaRecipient();
    updateProformaPdfLinks();
    updateWorkflowButtonsState();
    if (proformaNote) {
      if (hasProforma) {
        proformaNote.textContent = `Zapisana proforma: ${workflow.proforma_number}. Rezerwacja urządzeń pozostaje aktywna.`;
      } else if (activeWorkflowData.workflow_devices_dirty) {
        proformaNote.textContent =
          "Masz niezapisane zmiany w wyborze urządzeń lub cenach. Zostaną zapisane automatycznie podczas tworzenia proformy.";
      } else if (missingPriceDevices.length > 0) {
        proformaNote.textContent = formatWorkflowMissingPriceMessage(missingPriceDevices);
      } else {
        proformaNote.textContent =
          "Po utworzeniu proformy system zapisze plik PDF i zsynchronizuje wpisy w arkuszu Google.";
      }
    }
  }

  function formatSheetCacheNote(cache) {
    if (!cache || !cache.last_sync_at) {
      return "Statusy z arkusza: brak aktualizacji.";
    }
    const dateLabel = formatDate(cache.last_sync_at);
    const rowCount = Number(cache.row_count || 0);
    const worksheet = cache.worksheet_title || "nieznana";
    return `Statusy z arkusza: ${dateLabel} (${rowCount} pozycji, zakladka ${worksheet}).`;
  }

  function setWorkflowButtonsBusy(isBusy, busyLabel = "Trwa...") {
    const buttons = [
      workflowSaveDevicesBtn,
      workflowSaveDevicesBtnBottom,
      workflowRefreshSheetBtn,
      workflowRefreshSheetBtnBottom,
      workflowReleaseSheetBtn,
      workflowReleaseSheetBtnBottom,
    ].filter(Boolean);
    buttons.forEach((button) => {
      button.disabled = isBusy;
      if (isBusy) {
        button.dataset.originalLabel = button.dataset.originalLabel || button.textContent || "";
        button.textContent = busyLabel;
      } else if (button.dataset.originalLabel) {
        button.textContent = button.dataset.originalLabel;
      }
    });
  }

  function renderWorkflowModalData(data) {
    activeWorkflowData = data;
    activeWorkflowData.workflow_devices_dirty = false;
    ensureWorkflowDevicePrices();
    if (workflowSubtitle) {
      workflowSubtitle.textContent = `Formularz ${data.form?.id || "—"} / ${statusLabel(data.form?.status)}`;
    }
    renderWorkflowSummary(data);
    if (workflowDeviceNote) {
      workflowDeviceNote.textContent = data.selection_capabilities?.note || "";
    }
    if (workflowSheetAssignee) {
      const options = Array.isArray(data.sheet_assignee_options) ? data.sheet_assignee_options : [];
      const selectedId = Number(data.sheet_assignee_selected_id || 0);
      workflowSheetAssignee.innerHTML =
        `<option value="">Brak powiązania</option>` +
        options
          .map((option) => {
            const optionId = Number(option.id || 0);
            return `<option value="${escapeHtml(optionId)}" ${
              optionId === selectedId ? "selected" : ""
            }>${escapeHtml(option.label || option.login_user || option.id || "Użytkownik")}</option>`;
          })
          .join("");
    }
    syncProformaFormSelection();
    if (workflowDeviceStatusFilter) {
      workflowDeviceStatusFilter.innerHTML =
        '<option value="">Wszystkie</option>' +
        uniqueValues(data.available_devices || [], "status")
          .map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)
          .join("");
    }
    if (workflowDeviceReservationFilter) {
      workflowDeviceReservationFilter.innerHTML =
        '<option value="">Wszystkie</option>' +
        uniqueValues(data.available_devices || [], "reservation_filter_value")
          .map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)
          .join("");
    }
    if (workflowSheetCacheNote) {
      workflowSheetCacheNote.textContent = formatSheetCacheNote(data.sheet_status_cache || {});
    }
    renderWorkflowDevicePicker();
    if (workflowNote) {
      workflowNote.textContent =
        "Wybór jest zapisywany tylko po stronie CTIP. Na tym etapie nie tworzymy jeszcze wpisów w Menadżerze Serwisu.";
    }
    renderProformaDeviceTable();
    updateProformaState();
  }

  async function openWorkflowModal(formId) {
    if (!token) {
      showLogin("Brak aktywnej sesji.");
      return;
    }
    const numericFormId = Number(formId);
    if (!Number.isFinite(numericFormId) || numericFormId <= 0 || !workflowModal) {
      return;
    }
    clearMessages();
    activeWorkflowFormId = numericFormId;
    setWorkflowButtonsBusy(true, "Ładowanie...");
    try {
      const response = await fetch(`/admin/contracts/forms/${numericFormId}/workflow`, {
        headers: headers(false),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się pobrać danych workflow.");
      }
      renderWorkflowModalData(data);
      workflowModal.hidden = false;
      setWorkflowButtonsBusy(false);
      workflowDeviceSearch?.focus();
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd ładowania workflow.");
      setWorkflowButtonsBusy(false);
    }
  }

  async function persistWorkflowDevices({ showSuccessMessage = true, reopenModal = "workflow" } = {}) {
    if (!activeWorkflowFormId || !activeWorkflowData) {
      return false;
    }
    const selectedAssigneeId = Number(workflowSheetAssignee?.value || 0);
    const selectedDevices = getSelectedWorkflowDevices().map((item) => ({
      row: Number(item.row || 0),
      source_type: workflowDeviceSourceType(item),
      price_net: item.price_net || "",
      price_gross: item.price_gross || item.price || "",
    }));
    setWorkflowButtonsBusy(true, "Zapisywanie...");
    clearMessages();
    try {
      const response = await fetch(`/admin/contracts/forms/${activeWorkflowFormId}/workflow/devices`, {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify({
          devices: selectedDevices,
          sheet_assignee_id: selectedAssigneeId > 0 ? selectedAssigneeId : null,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się zapisać wyboru urządzeń.");
      }
      activeWorkflowData.workflow_devices_dirty = false;
      if (activeWorkflowData.workflow) {
        activeWorkflowData.workflow.devices_selected_count = selectedDevices.length;
      }
      if (showSuccessMessage) {
        showSuccess(data.message || "Wybór urządzeń zapisany.");
      }
      await loadItems(false);
      if (reopenModal === "workflow") {
        await openWorkflowModal(activeWorkflowFormId);
      } else if (reopenModal === "proforma") {
        await openProformaModal(activeWorkflowFormId);
      } else {
        updateWorkflowSelectionSummary();
        updateProformaState();
      }
      return true;
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd zapisu urządzeń.");
      return false;
    } finally {
      setWorkflowButtonsBusy(false);
    }
  }

  async function createWorkflowProforma({ askConfirm = true } = {}) {
    if (!activeWorkflowFormId) {
      return;
    }
    if (activeWorkflowData?.workflow?.proforma_number) {
      showSuccess(`Proforma jest już zapisana: ${activeWorkflowData.workflow.proforma_number}.`);
      return;
    }
    const forBank = Boolean(proformaBank?.checked);
    const selectedAssigneeId = Number(proformaSheetAssignee?.value || 0);
    if (askConfirm) {
      const recipientLabel = forBank
        ? "bank GRENKELEASING Sp. z o.o."
        : "klient z formularza";
      const confirmed = window.confirm(
        `Czy na pewno wystawić proformę?\nOdbiorca: ${recipientLabel}`
      );
      if (!confirmed) {
        return;
      }
    }
    if (activeWorkflowData?.workflow_devices_dirty) {
      const saved = await persistWorkflowDevices({
        showSuccessMessage: false,
        reopenModal: null,
      });
      if (!saved) {
        return;
      }
    }
    [proformaCreateBtn, proformaCreateBtnTop].forEach((button) => {
      if (!button) {
        return;
      }
      button.disabled = true;
      button.dataset.originalLabel = button.dataset.originalLabel || button.textContent || "";
      button.textContent = "Tworzenie...";
    });
    clearMessages();
    try {
      const response = await fetch(
        `/admin/contracts/forms/${activeWorkflowFormId}/workflow/proforma`,
        {
          method: "POST",
          headers: headers(true),
          body: JSON.stringify({
            for_bank: forBank,
            sheet_assignee_id: selectedAssigneeId > 0 ? selectedAssigneeId : null,
          }),
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się wystawić proformy.");
      }
      showSuccess(data.message || "Proforma została zapisana.");
      await loadItems(false);
      await openProformaModal(activeWorkflowFormId);
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd tworzenia proformy.");
    } finally {
      [proformaCreateBtn, proformaCreateBtnTop].forEach((button) => {
        if (!button) {
          return;
        }
        button.disabled = false;
        button.textContent = button.dataset.originalLabel || "Utwórz PROFORMĘ";
      });
      updateWorkflowButtonsState();
    }
  }

  async function resetWorkflowProforma() {
    if (!activeWorkflowFormId || !activeWorkflowData) {
      return;
    }
    const workflow = activeWorkflowData.workflow || {};
    if (!workflow.proforma_number) {
      showError("Brak zapisanej proformy do usunięcia.");
      return;
    }
    const confirmed = window.confirm(
      `Proforma o numerze ${workflow.proforma_number} zostanie usunięta. Rezerwacja urządzeń pozostanie aktywna. Czy kontynuować?`
    );
    if (!confirmed) {
      return;
    }
    clearMessages();
    try {
      const response = await fetch(
        `/admin/contracts/forms/${activeWorkflowFormId}/workflow/proforma-reset`,
        {
          method: "POST",
          headers: headers(false),
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się usunąć proformy.");
      }
      showSuccess(data.message || "Usunięto proformę, a rezerwacja urządzeń pozostała bez zmian.");
      await loadItems(false);
      await openProformaModal(activeWorkflowFormId);
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd usuwania proformy.");
    }
  }

  async function refreshWorkflowSheetStatuses() {
    if (!activeWorkflowFormId) {
      return;
    }
    setWorkflowButtonsBusy(true, "Odswiezanie...");
    clearMessages();
    try {
      const response = await fetch("/admin/contracts/workflow/sheet-status-refresh", {
        method: "POST",
        headers: headers(false),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się odświeżyć statusów arkusza.");
      }
      showSuccess(data.message || "Statusy z arkusza odświeżone.");
      await openWorkflowModal(activeWorkflowFormId);
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd odświeżania statusów arkusza.");
    } finally {
      setWorkflowButtonsBusy(false);
    }
  }

  async function releaseWorkflowSheetReservations() {
    if (!activeWorkflowFormId) {
      return;
    }
    const confirmed = window.confirm(
      "Usunięcie rezerwacji zwolni wpisy w arkuszu Google, usunie przypięte urządzenia i usunie proformę ze sprawy workflow. Czy kontynuować?"
    );
    if (!confirmed) {
      return;
    }
    setWorkflowButtonsBusy(true, "Usuwanie...");
    clearMessages();
    try {
      const clearDevices = await fetch(`/admin/contracts/forms/${activeWorkflowFormId}/workflow/devices`, {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify({ devices: [], sheet_assignee_id: null }),
      });
      const clearData = await clearDevices.json().catch(() => ({}));
      if (!clearDevices.ok) {
        throw new Error(clearData.detail || "Nie udało się usunąć urządzeń ze sprawy workflow.");
      }
      const resetProforma = await fetch(
        `/admin/contracts/forms/${activeWorkflowFormId}/workflow/proforma-reset`,
        {
          method: "POST",
          headers: headers(false),
        }
      );
      if (!resetProforma.ok && resetProforma.status !== 409) {
        const resetData = await resetProforma.json().catch(() => ({}));
        throw new Error(resetData.detail || "Nie udało się usunąć informacji o proformie.");
      }
      const response = await fetch(
        `/admin/contracts/forms/${activeWorkflowFormId}/workflow/sheet-release`,
        {
          method: "POST",
          headers: headers(false),
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się usunąć rezerwacji z arkusza.");
      }
      showSuccess(data.message || "Rezerwacje usunięte.");
      await loadItems(false);
      await openWorkflowModal(activeWorkflowFormId);
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd usuwania rezerwacji.");
    } finally {
      setWorkflowButtonsBusy(false);
    }
  }

  async function openProformaModal(formId) {
    if (!token) {
      showLogin("Brak aktywnej sesji.");
      return;
    }
    const numericFormId = Number(formId);
    if (!Number.isFinite(numericFormId) || numericFormId <= 0 || !proformaModal) {
      return;
    }
    clearMessages();
    activeWorkflowFormId = numericFormId;
    try {
      const response = await fetch(`/admin/contracts/forms/${numericFormId}/workflow`, {
        headers: headers(false),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się pobrać danych proformy.");
      }
      activeWorkflowData = data;
      activeWorkflowData.workflow_devices_dirty = false;
      ensureWorkflowDevicePrices();
      const workflow = data.workflow || {};
      if (proformaSubtitle) {
        proformaSubtitle.textContent = workflow.proforma_number
          ? `Zapisana proforma: ${workflow.proforma_number}`
          : "Kolejny etap po kliencie i wyborze urządzeń. Po zapisaniu proformy możesz od razu zapisać plik PDF.";
      }
      if (proformaSummary) {
        proformaSummary.innerHTML = `
          <article class="genform-detail-summary-card"><dt>ID formularza</dt><dd>${escapeHtml(data.form?.id || "—")}</dd></article>
          <article class="genform-detail-summary-card"><dt>Etap workflow</dt><dd>${escapeHtml(workflowStageLabel(workflow.stage))}</dd></article>
          <article class="genform-detail-summary-card"><dt>Klient MS</dt><dd>${
            workflow.firebird_client_id ? `ID ${escapeHtml(workflow.firebird_client_id)}` : '<span class="genform-status warning">Brak</span>'
          }</dd></article>
          <article class="genform-detail-summary-card"><dt>Urzadzenia</dt><dd>${
            Number(workflow.devices_selected_count || 0) > 0
              ? escapeHtml(String(workflow.devices_selected_count))
              : '<span class="genform-status warning">Brak</span>'
          }</dd></article>
          <article class="genform-detail-summary-card"><dt>Proforma</dt><dd>${
            workflow.proforma_number
              ? escapeHtml(workflow.proforma_number)
              : '<span class="genform-status warning">Brak</span>'
          }</dd></article>
        `;
      }
      if (workflowSheetAssignee) {
        const selectedId = Number(data.sheet_assignee_selected_id || 0);
        workflowSheetAssignee.value = selectedId > 0 ? String(selectedId) : "";
      }
      syncProformaFormSelection();
      if (proformaBank) {
        proformaBank.checked = true;
      }
      renderProformaDeviceTable();
      updateProformaState();
      proformaModal.hidden = false;
      (workflow.proforma_number ? proformaPdfLinkTop : proformaCreateBtnTop)?.focus();
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd ładowania danych proformy.");
    }
  }

  async function saveWorkflowDevices() {
    await persistWorkflowDevices({ showSuccessMessage: true, reopenModal: "workflow" });
  }

  function updateWorkflowDevicePrice(deviceKey, field, rawValue) {
    if (!activeWorkflowData?.available_devices) {
      return;
    }
    activeWorkflowData.available_devices = activeWorkflowData.available_devices.map((item) => {
      if (workflowDeviceKey(item) !== deviceKey) {
        return item;
      }
      const vatRate = Number(item.vat_rate || 23);
      if (field === "net") {
        return {
          ...item,
          price_net: rawValue,
          price_gross: netToGross(rawValue, vatRate),
        };
      }
      return {
        ...item,
        price_gross: rawValue,
        price_net: grossToNet(rawValue, vatRate),
      };
    });
    activeWorkflowData.workflow_devices_dirty = true;
    updateWorkflowSelectionSummary();
    updateProformaState();
  }

  async function fetchMe() {
    if (!token) {
      showLogin();
      return null;
    }
    const response = await fetch("/auth/me", { headers: headers(false) });
    if (!response.ok) {
      clearToken();
      showLogin("Sesja wygasła. Zaloguj się ponownie.");
      return null;
    }
    const user = await response.json();
    const sections = new Set(Array.isArray(user.sections) ? user.sections : []);
    if (!sections.has("generator")) {
      clearToken();
      showLogin("Konto nie ma uprawnień do sekcji generatora formularzy.");
      return null;
    }
    showApp(user);
    return user;
  }

  async function loadItems(showInfo = false) {
    if (!token) {
      showLogin("Brak aktywnej sesji.");
      return;
    }
    clearMessages();
    setBusy(refreshBtn, true, "Odświeżanie…", "Odśwież listę");
    try {
      const response = await fetch(
        "/admin/contracts/dashboard?forms_scope=all&include_devices=0",
        { headers: headers(false) }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się pobrać listy formularzy.");
      }
      latestForms = Array.isArray(data.forms) ? data.forms : [];
      renderItems(latestForms);
      if (showInfo) {
        showSuccess("Lista formularzy została odświeżona.");
      }
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd pobierania listy formularzy.");
    } finally {
      setBusy(refreshBtn, false, "Odświeżanie…", "Odśwież listę");
    }
  }

  async function loadFormDetail(formId) {
    if (!token) {
      showLogin("Brak aktywnej sesji.");
      return;
    }
    clearMessages();
    try {
      const response = await fetch(`/admin/forms/${formId}`, {
        headers: headers(false),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się pobrać szczegółów formularza.");
      }

      const item = data.item || {};
      currentDetailData = {
        item,
        statusMessage: data.status_message || "Brak informacji o statusie.",
        submittedPayload:
          data.submitted_payload && typeof data.submitted_payload === "object"
            ? data.submitted_payload
            : null,
        submittedMeta: data.submitted_meta && typeof data.submitted_meta === "object"
          ? data.submitted_meta
          : {},
      };
      if (detailStatus) {
        detailStatus.textContent = currentDetailData.statusMessage;
      }
      renderDetailSections(currentDetailData);

      openedFormId = formId;
      detailModal.hidden = false;
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd odczytu szczegółów formularza.");
    }
  }

  async function deactivateForm(formId) {
    if (!token) {
      showLogin("Brak aktywnej sesji.");
      return;
    }
    const confirmed = window.confirm(
      "Dezaktywacja formularza usunie nieodwracalnie powiązania z arkuszem i przywróci rezerwacje. " +
      "Klient utworzony w Firebird nie będzie zmieniany. Czy kontynuować?"
    );
    if (!confirmed) {
      return;
    }

    clearMessages();
    try {
      const response = await fetch(`/admin/forms/${formId}`, {
        method: "DELETE",
        headers: headers(false),
      });
      if (response.status !== 204) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "Nie udało się usunąć formularza.");
      }
      if (openedFormId === formId) {
        closeDetailModal();
      }
      if (activeWorkflowFormId === formId) {
        closeWorkflowModal();
      }
      showSuccess("Formularz został dezaktywowany, a rezerwacje przywrócone.");
      await loadItems(false);
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd usuwania formularza.");
    }
  }

  async function handleLogin(event) {
    event.preventDefault();
    const emailInput = document.getElementById("genform-email");
    const rememberInput = document.getElementById("genform-remember");
    const submitButton = loginForm.querySelector("button[type='submit']");
    if (!emailInput || !passwordInput || !rememberInput || !submitButton) {
      return;
    }
    loginError.hidden = true;
    loginError.textContent = "";
    setBusy(submitButton, true, "Logowanie…", "Zaloguj");
    try {
      const response = await fetch("/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: emailInput.value.trim().toLowerCase(),
          password: passwordInput.value,
          remember_me: Boolean(rememberInput.checked),
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nieprawidłowe dane logowania.");
      }
      token = data.token;
      storeToken(token, Boolean(rememberInput.checked));
      const user = await fetchMe();
      if (!user) {
        return;
      }
      await loadItems(false);
      passwordInput.value = "";
      passwordInput.type = "password";
      if (passwordToggleBtn) {
        passwordToggleBtn.textContent = "Pokaż";
      }
    } catch (err) {
      loginError.textContent = err instanceof Error ? err.message : "Nieudane logowanie.";
      loginError.hidden = false;
      clearToken();
    } finally {
      setBusy(submitButton, false, "Logowanie…", "Zaloguj");
    }
  }

  async function handleGenerate(event) {
    event.preventDefault();
    if (!token) {
      showLogin("Brak aktywnej sesji.");
      return;
    }
    clearMessages();
    const submitButton = document.getElementById("genform-generate");
    const nameInput = document.getElementById("gf-customer-name");
    const emailInput = document.getElementById("gf-customer-email");
    const phoneInput = document.getElementById("gf-customer-phone");
    if (!submitButton || !nameInput || !emailInput || !phoneInput || !expiresOnInput) {
      return;
    }
    setBusy(submitButton, true, "Generowanie…", "Generuj formularz");
    try {
      const body = {
        customer_name: nameInput.value.trim(),
        customer_email: emailInput.value.trim(),
        customer_phone: phoneInput.value.trim(),
      };
      const expiresOn = expiresOnInput.value.trim();
      if (expiresOn) {
        body.expires_on = expiresOn;
      }
      const response = await fetch("/admin/forms", {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się wygenerować formularza.");
      }
      if (generatedLink && generatedBox && openLink) {
        generatedLink.textContent = data.form_url || "";
        openLink.href = data.form_url || "#";
        generatedBox.hidden = false;
      }
      const warnings = Array.isArray(data.warnings) ? data.warnings.filter(Boolean) : [];
      if (warnings.length) {
        showSuccess(`Formularz wygenerowany z ostrzeżeniami: ${warnings.join(" ")}`);
      } else {
        showSuccess("Formularz został wygenerowany i wysłany.");
      }
      createForm.reset();
      setDefaultExpiresOn();
      await loadItems(false);
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd generowania formularza.");
    } finally {
      setBusy(submitButton, false, "Generowanie…", "Generuj formularz");
    }
  }

  async function handleLogout() {
    if (!token) {
      showLogin();
      return;
    }
    try {
      await fetch("/auth/logout", {
        method: "POST",
        headers: headers(false),
      });
    } catch (err) {
      console.error("Błąd wylogowania", err);
    } finally {
      clearToken();
      closeDetailModal();
      closeWorkflowModal();
      closeProformaModal();
      showLogin("Wylogowano.");
      if (generatedBox) {
        generatedBox.hidden = true;
      }
    }
  }

  function copyTextWithExecCommand(value) {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "readonly");
    textarea.style.position = "fixed";
    textarea.style.top = "-1000px";
    textarea.style.left = "-1000px";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } catch (err) {
      copied = false;
    } finally {
      document.body.removeChild(textarea);
    }
    return copied;
  }

  async function copyTextToClipboard(value) {
    const text = String(value || "").trim();
    if (!text) {
      return false;
    }

    if (window.isSecureContext && navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch (err) {
        // Fallback dla środowisk, w których Clipboard API jest blokowane.
      }
    }
    return copyTextWithExecCommand(text);
  }

  async function handleCopyLink() {
    if (!generatedLink || !generatedLink.textContent) {
      return;
    }
    const value = generatedLink.textContent.trim();
    if (!value) {
      return;
    }
    try {
      const copied = await copyTextToClipboard(value);
      clearMessages();
      if (copied) {
        showSuccess("Skopiowano link do schowka.");
      } else {
        showError("Nie udało się skopiować automatycznie. Skopiuj link ręcznie z pola.");
      }
    } catch (err) {
      clearMessages();
      showError("Nie udało się skopiować linku.");
    }
  }

  function markCopiedButton(button) {
    const originalLabel = button.dataset.originalLabel || button.textContent || "Kopiuj";
    button.dataset.originalLabel = originalLabel;
    button.textContent = "Skopiowano";
    button.classList.add("is-copied");
    window.setTimeout(() => {
      button.textContent = originalLabel;
      button.classList.remove("is-copied");
    }, 1400);
  }

  async function handleCopyField(button) {
    const value = button.dataset.copyValue || "";
    const label = button.dataset.copyLabel || "Pole";
    try {
      const copied = await copyTextToClipboard(value);
      clearMessages();
      if (copied) {
        markCopiedButton(button);
        showSuccess(`Skopiowano pole: ${label}.`);
      } else {
        showError(`Nie udało się skopiować pola: ${label}.`);
      }
    } catch (err) {
      clearMessages();
      showError(`Nie udało się skopiować pola: ${label}.`);
    }
  }

  function togglePasswordVisibility() {
    if (!passwordInput || !passwordToggleBtn) {
      return;
    }
    const nextType = passwordInput.type === "password" ? "text" : "password";
    passwordInput.type = nextType;
    passwordToggleBtn.textContent = nextType === "password" ? "Pokaż" : "Ukryj";
  }

  loginForm?.addEventListener("submit", handleLogin);
  createForm?.addEventListener("submit", handleGenerate);
  refreshBtn?.addEventListener("click", () => loadItems(true));
  logoutBtn?.addEventListener("click", handleLogout);
  copyLinkBtn?.addEventListener("click", handleCopyLink);
  passwordToggleBtn?.addEventListener("click", togglePasswordVisibility);
  detailCloseBtn?.addEventListener("click", closeDetailModal);
  workflowCloseBtn?.addEventListener("click", closeWorkflowModal);
  proformaCloseBtn?.addEventListener("click", closeProformaModal);
  detailPrintBtn?.addEventListener("click", () => triggerDetailPrint("print"));
  detailPdfBtn?.addEventListener("click", () => triggerDetailPrint("pdf"));
  detailModal?.addEventListener("click", (event) => {
    if (event.target === detailModal) {
      closeDetailModal();
      return;
    }
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const copyButton = target.closest("button[data-copy-value]");
    if (copyButton instanceof HTMLButtonElement) {
      event.preventDefault();
      handleCopyField(copyButton);
    }
  });
  workflowModal?.addEventListener("click", (event) => {
    if (event.target === workflowModal) {
      closeWorkflowModal();
    }
  });
  proformaModal?.addEventListener("click", (event) => {
    if (event.target === proformaModal) {
      closeProformaModal();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && detailModal && !detailModal.hidden) {
      closeDetailModal();
      return;
    }
    if (event.key === "Escape" && workflowModal && !workflowModal.hidden) {
      closeWorkflowModal();
      return;
    }
    if (event.key === "Escape" && proformaModal && !proformaModal.hidden) {
      closeProformaModal();
    }
  });
  window.addEventListener("afterprint", clearPrintMode);
  window.addEventListener("pageshow", () => {
    closeDetailModal();
    closeWorkflowModal();
    closeProformaModal();
  });

  tableBody?.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const actionButton = target.closest("button[data-action]");
    if (actionButton) {
      event.preventDefault();
      event.stopPropagation();
      const formId = Number(actionButton.dataset.formId || actionButton.getAttribute("data-form-id"));
      if (!Number.isInteger(formId) || formId <= 0) {
        return;
      }
      const action = actionButton.dataset.action || actionButton.getAttribute("data-action");
      if (action === "view") {
        loadFormDetail(formId);
      } else if (action === "deactivate") {
        deactivateForm(formId);
      } else if (action === "devices") {
        openWorkflowModal(formId);
      } else if (action === "proforma") {
        openProformaModal(formId);
      }
    }
  });

  workflowDeviceSearch?.addEventListener("input", () => {
    renderWorkflowDevicePicker();
  });
  workflowDeviceStatusFilter?.addEventListener("change", () => {
    renderWorkflowDevicePicker();
  });
  workflowDeviceReservationFilter?.addEventListener("change", () => {
    renderWorkflowDevicePicker();
  });
  workflowSaveDevicesBtn?.addEventListener("click", () => {
    saveWorkflowDevices();
  });
  workflowSaveDevicesBtnBottom?.addEventListener("click", () => {
    saveWorkflowDevices();
  });
  workflowRefreshSheetBtn?.addEventListener("click", () => {
    refreshWorkflowSheetStatuses();
  });
  workflowRefreshSheetBtnBottom?.addEventListener("click", () => {
    refreshWorkflowSheetStatuses();
  });
  workflowReleaseSheetBtn?.addEventListener("click", () => {
    releaseWorkflowSheetReservations();
  });
  workflowReleaseSheetBtnBottom?.addEventListener("click", () => {
    releaseWorkflowSheetReservations();
  });
  proformaCreateBtn?.addEventListener("click", () => {
    createWorkflowProforma();
  });
  proformaCreateBtnTop?.addEventListener("click", () => {
    createWorkflowProforma();
  });
  proformaResetBtn?.addEventListener("click", () => {
    resetWorkflowProforma();
  });
  workflowSheetAssignee?.addEventListener("change", () => {
    syncProformaFormSelection();
    updateProformaState();
  });
  proformaSheetAssignee?.addEventListener("change", () => {
    if (workflowSheetAssignee) {
      workflowSheetAssignee.value = proformaSheetAssignee.value;
    }
    updateProformaState();
  });
  proformaBank?.addEventListener("change", () => {
    updateProformaState();
  });

  workflowDeviceBody?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || !activeWorkflowData?.available_devices) {
      return;
    }
    if (target.dataset.workflowDeviceKey) {
      const key = String(target.dataset.workflowDeviceKey || "");
      activeWorkflowData.available_devices = activeWorkflowData.available_devices.map((item) =>
        workflowDeviceKey(item) === key ? { ...item, selected: target.checked } : item
      );
      activeWorkflowData.workflow_devices_dirty = true;
      updateWorkflowSelectionSummary();
      renderProformaDeviceTable();
      updateProformaState();
    }
  });

  [workflowDeviceBody, proformaDeviceBody].forEach((container) => {
    container?.addEventListener("input", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement)) {
        return;
      }
      if (target.dataset.workflowDevicePriceNetKey) {
        const key = String(target.dataset.workflowDevicePriceNetKey || "");
        updateWorkflowDevicePrice(key, "net", target.value);
        const updatedDevice = activeWorkflowData?.available_devices?.find(
          (item) => workflowDeviceKey(item) === key
        );
        document
          .querySelectorAll(`[data-workflow-device-price-gross-key="${CSS.escape(key)}"]`)
          .forEach((input) => {
            if (input instanceof HTMLInputElement && input !== target) {
              input.value = updatedDevice?.price_gross || "";
            }
          });
        return;
      }
      if (target.dataset.workflowDevicePriceGrossKey) {
        const key = String(target.dataset.workflowDevicePriceGrossKey || "");
        updateWorkflowDevicePrice(key, "gross", target.value);
        const updatedDevice = activeWorkflowData?.available_devices?.find(
          (item) => workflowDeviceKey(item) === key
        );
        document
          .querySelectorAll(`[data-workflow-device-price-net-key="${CSS.escape(key)}"]`)
          .forEach((input) => {
            if (input instanceof HTMLInputElement && input !== target) {
              input.value = updatedDevice?.price_net || "";
            }
          });
      }
    });
    container?.addEventListener("change", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLInputElement)) {
        return;
      }
      if (target.dataset.workflowDevicePriceNetKey || target.dataset.workflowDevicePriceGrossKey) {
        updateProformaState();
      }
    });
  });

  workflowDeviceBody?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || !activeWorkflowData?.available_devices) {
      return;
    }
    if (target.dataset.workflowDevicePriceNetKey || target.dataset.workflowDevicePriceGrossKey) {
      updateProformaState();
    }
  });

  closeDetailModal();
  if (!token) {
    showLogin();
    return;
  }

  fetchMe().then((user) => {
    if (user) {
      loadItems(false);
    }
  });
}

window.addEventListener("DOMContentLoaded", initializeGenForm);
