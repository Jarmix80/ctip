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
    DRAFT: "Wypełniony formularz klienta",
    PENDING_APPROVAL: "Umowa GRENKE czeka na podpis",
    WAITING_SIGNATURE: "Umowa GRENKE czeka na podpis",
    APPROVED: "Zgoda na realizację zamówienia",
    APPROVED_ORDER: "Zgoda na realizację zamówienia",
    ZEROWKA: "Zerówka",
    REJECTED: "Odmowa GRENKE",
    REJECTED_GRENKE: "Odmowa GRENKE",
  };
  return mapped[status] || status || "Brak";
}

function mailboxSyncResultLabel(result) {
  const mapped = {
    ok: "OK",
    error: "błąd",
    timeout: "timeout",
    exception: "wyjątek",
    skipped: "pominięto",
    unknown: "nieznany",
  };
  return mapped[String(result || "").trim().toLowerCase()] || "nieznany";
}

function formatDateInputValue(value) {
  if (!value) {
    return "";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "";
  }
  const yyyy = parsed.getFullYear();
  const mm = String(parsed.getMonth() + 1).padStart(2, "0");
  const dd = String(parsed.getDate()).padStart(2, "0");
  const hh = String(parsed.getHours()).padStart(2, "0");
  const min = String(parsed.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}T${hh}:${min}`;
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
  const mailboxSyncNote = document.getElementById("genform-mailbox-sync-note");
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
  const detailDataEnteredBtn = document.getElementById("genform-detail-data-entered");
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
  const statusModal = document.getElementById("genform-status-modal");
  const statusCloseBtn = document.getElementById("genform-status-close");
  const statusSubtitle = document.getElementById("genform-status-subtitle");
  const statusSaveBtn = document.getElementById("genform-status-save");
  const statusSelect = document.getElementById("genform-status-select");
  const statusSignatureDeadline = document.getElementById("genform-status-signature-deadline");
  const statusNote = document.getElementById("genform-status-note");
  const summaryModal = document.getElementById("genform-summary-modal");
  const summaryCloseBtn = document.getElementById("genform-summary-close");
  const summarySubtitle = document.getElementById("genform-summary-subtitle");
  const summaryContent = document.getElementById("genform-summary-content");
  const archiveMenuItems = document.querySelectorAll("[data-archive-scope]");
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
  let detailDataEnteredBusy = false;
  let latestForms = [];
  let activeWorkflowFormId = null;
  let activeWorkflowData = null;
  let activeArchiveScope = "active";
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
    detailDataEnteredBusy = false;
    updateDataEnteredButtonState();
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
    if (workflowDeviceSearch) {
      workflowDeviceSearch.value = "";
    }
    if (workflowDeviceStatusFilter) {
      workflowDeviceStatusFilter.innerHTML = "";
      workflowDeviceStatusFilter.value = "";
    }
    if (workflowDeviceReservationFilter) {
      workflowDeviceReservationFilter.innerHTML = "";
      workflowDeviceReservationFilter.value = "";
    }
  }

  function closeStatusModal() {
    if (!statusModal) {
      return;
    }
    statusModal.hidden = true;
    if (statusSelect) {
      statusSelect.innerHTML = "";
    }
    if (statusSignatureDeadline) {
      statusSignatureDeadline.value = "";
    }
    if (statusNote) {
      statusNote.textContent =
        "Termin podpisu jest używany dla statusu „Umowa GRENKE czeka na podpis”.";
    }
  }

  function closeSummaryModal() {
    if (!summaryModal) {
      return;
    }
    summaryModal.hidden = true;
    if (summaryContent) {
      summaryContent.innerHTML = "";
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
    closeStatusModal();
    closeSummaryModal();
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
    closeStatusModal();
    closeSummaryModal();
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

  function buildWorkflowPdfDownloadName(workflow) {
    const rawNumber = String(workflow?.proforma_number || "").trim();
    if (rawNumber) {
      const sanitized = rawNumber
        .replace(/[\\/:*?"<>|]+/g, "_")
        .replace(/\s+/g, "_")
        .replace(/_+/g, "_")
        .replace(/^[._\s]+|[._\s]+$/g, "");
      if (sanitized) {
        return `${sanitized}.pdf`;
      }
    }
    const proformaId = Number(workflow?.proforma_firebird_id || 0);
    if (Number.isFinite(proformaId) && proformaId > 0) {
      return `proforma_${proformaId}.pdf`;
    }
    return "proforma.pdf";
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
    const flowStatus = item.flow_status || {};
    const checklist = [];
    checklist.push({
      label: `Etap: ${flowStatus.label || statusLabel(item.status)}`,
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

  function archiveInfoLabel(item) {
    const archive = item.archive_state || {};
    if (archive.bucket) {
      return `Archiwum: ${archive.bucket}`;
    }
    if (archive.days_to_archive !== null && archive.days_to_archive !== undefined) {
      return `Archiwizacja za ${archive.days_to_archive} dni`;
    }
    return "";
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
        const actions = item.available_actions || {};
        const hasWorkflowActions = Boolean(actions.workflow);
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
        const archiveLabel = archiveInfoLabel(item);
        const resourceReleaseLabel =
          item.days_to_resource_release !== null && item.days_to_resource_release !== undefined
            ? `Zwolnienie zasobów za ${item.days_to_resource_release} dni`
            : "";

        return `<tr data-form-id="${rowId}" class="genform-row-tone-${escapeHtmlAttribute(item.row_tone || "active")}" tabindex="0">
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
              ${actions.status_change ? `<button type="button" class="genform-row-action" data-action="status" data-form-id="${rowId}">Zmiana statusu</button>` : ""}
              ${actions.summary ? `<button type="button" class="genform-row-action" data-action="summary" data-form-id="${rowId}">Podsumowanie</button>` : ""}
              ${actions.release_resources ? `<button type="button" class="genform-row-action danger" data-action="release-resources" data-form-id="${rowId}">Zwolnij zasoby</button>` : ""}
              ${actions.archive ? `<button type="button" class="genform-row-action" data-action="archive" data-form-id="${rowId}">Przenieś do archiwum</button>` : ""}
              ${actions.extend_archive ? `<button type="button" class="genform-row-action" data-action="extend-archive" data-form-id="${rowId}">Przedłuż o 7 dni</button>` : ""}
              <button type="button" class="genform-row-action danger" data-action="deactivate" data-form-id="${rowId}">Dezaktywuj</button>
            </div>
            ${archiveLabel ? `<div class="genform-subtle">${escapeHtml(archiveLabel)}</div>` : ""}
            ${resourceReleaseLabel ? `<div class="genform-subtle">${escapeHtml(resourceReleaseLabel)}</div>` : ""}
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
    const workflowValue = workflowSheetAssignee.value || "";
    const hasMatchingValue = Array.from(proformaSheetAssignee.options).some(
      (option) => option.value === workflowValue
    );
    proformaSheetAssignee.value = hasMatchingValue ? workflowValue : "";
  }

  function renderSheetAssigneeOptions(selectElement, options, selectedId) {
    if (!selectElement) {
      return;
    }
    const normalizedOptions = Array.isArray(options)
      ? options
          .map((option) => {
            const optionId = Number(option?.id || 0);
            if (!Number.isFinite(optionId) || optionId <= 0) {
              return null;
            }
            return {
              id: String(optionId),
              label: String(option?.label || option?.login_user || optionId),
            };
          })
          .filter(Boolean)
      : [];
    const expectedValue = Number.isFinite(selectedId) && selectedId > 0 ? String(selectedId) : "";
    selectElement.innerHTML =
      '<option value="">Brak powiązania</option>' +
      normalizedOptions
        .map(
          (option) =>
            `<option value="${escapeHtmlAttribute(option.id)}">${
              escapeHtml(option.label || "Użytkownik")
            }</option>`
        )
        .join("");
    const hasExpectedValue = normalizedOptions.some((option) => option.id === expectedValue);
    selectElement.value = hasExpectedValue ? expectedValue : "";
  }

  function canSaveWorkflowStatus() {
    if (!statusSelect) {
      return false;
    }
    return !statusSelect.disabled && statusSelect.options.length > 0
      && Boolean(statusSelect.value);
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
      button.disabled = hasProforma || !canCreateProforma;
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
    const downloadName = buildWorkflowPdfDownloadName(workflow);
    [proformaPdfLink, proformaPdfLinkTop].forEach((link) => {
      if (!link) {
        return;
      }
      if (pdfUrl) {
        link.hidden = false;
        link.href = pdfUrl;
        link.setAttribute("download", downloadName);
      } else {
        link.hidden = true;
        link.href = "#";
        link.removeAttribute("download");
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
      statusSaveBtn,
    ].filter(Boolean);
    buttons.forEach((button) => {
      if (isBusy) {
        button.disabled = true;
      } else if (button === statusSaveBtn) {
        button.disabled = !canSaveWorkflowStatus();
      } else {
        button.disabled = false;
      }
      if (isBusy) {
        button.dataset.originalLabel = button.dataset.originalLabel || button.textContent || "";
        button.textContent = busyLabel;
      } else if (button.dataset.originalLabel) {
        button.textContent = button.dataset.originalLabel;
      }
    });
    if (statusSelect) {
      statusSelect.disabled = isBusy || statusSelect.options.length === 0;
    }
  }

  function renderWorkflowModalData(data) {
    activeWorkflowData = data;
    activeWorkflowData.workflow_devices_dirty = false;
    ensureWorkflowDevicePrices();
    const workflow = data.workflow || {};
    if (workflowSubtitle) {
      workflowSubtitle.textContent = `Formularz ${data.form?.id || "—"} / ${statusLabel(data.form?.status)}`;
    }
    renderWorkflowSummary(data);
    if (workflowDeviceNote) {
      workflowDeviceNote.textContent = data.selection_capabilities?.note || "";
    }
    const assigneeOptions = Array.isArray(data.sheet_assignee_options) ? data.sheet_assignee_options : [];
    const selectedAssigneeId = Number(data.sheet_assignee_selected_id || 0);
    renderSheetAssigneeOptions(workflowSheetAssignee, assigneeOptions, selectedAssigneeId);
    syncProformaFormSelection();
    if (workflowDeviceSearch) {
      workflowDeviceSearch.value = "";
    }
    if (workflowDeviceStatusFilter) {
      workflowDeviceStatusFilter.innerHTML =
        '<option value="">Wszystkie</option>' +
        uniqueValues(data.available_devices || [], "status")
          .map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)
          .join("");
      workflowDeviceStatusFilter.value = "";
    }
    if (workflowDeviceReservationFilter) {
      workflowDeviceReservationFilter.innerHTML =
        '<option value="">Wszystkie</option>' +
        uniqueValues(data.available_devices || [], "reservation_filter_value")
          .map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)
          .join("");
      workflowDeviceReservationFilter.value = "";
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
      "Zwolnienie zasobów usunie aktywną proformę i rezerwacje, ale zostawi historię formularza. Czy kontynuować?"
    );
    if (!confirmed) {
      return;
    }
    setWorkflowButtonsBusy(true, "Usuwanie...");
    clearMessages();
    try {
      const response = await fetch(
        `/admin/contracts/forms/${activeWorkflowFormId}/workflow/release-resources`,
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
      if (workflowModal && !workflowModal.hidden) {
        await openWorkflowModal(activeWorkflowFormId);
      }
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd usuwania rezerwacji.");
    } finally {
      setWorkflowButtonsBusy(false);
    }
  }

  async function saveWorkflowStatus() {
    if (!activeWorkflowFormId) {
      return;
    }
    if (!statusSelect) {
      return;
    }
    setBusy(statusSaveBtn, true, "Zapisywanie...", "Zapisz status");
    clearMessages();
    try {
      const deadlineValue = statusSignatureDeadline?.value || "";
      const response = await fetch(
        `/admin/contracts/forms/${activeWorkflowFormId}/workflow/status`,
        {
          method: "POST",
          headers: headers(true),
          body: JSON.stringify({
            business_status: statusSelect.value || "WAITING_SIGNATURE",
            signature_deadline_at: deadlineValue ? new Date(deadlineValue).toISOString() : null,
          }),
        }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się zapisać statusu.");
      }
      showSuccess(data.message || "Status GRENKE zapisany.");
      await loadItems(false);
      closeStatusModal();
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd zapisu statusu.");
    } finally {
      setBusy(statusSaveBtn, false, "Zapisywanie...", "Zapisz status");
    }
  }

  async function openStatusModal(formId) {
    if (!token) {
      showLogin("Brak aktywnej sesji.");
      return;
    }
    const numericFormId = Number(formId);
    if (!Number.isFinite(numericFormId) || numericFormId <= 0 || !statusModal) {
      return;
    }
    clearMessages();
    activeWorkflowFormId = numericFormId;
    setBusy(statusSaveBtn, true, "Ładowanie...", "Zapisz status");
    try {
      const response = await fetch(`/admin/contracts/forms/${numericFormId}/workflow`, {
        headers: headers(false),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się pobrać statusu workflow.");
      }
      activeWorkflowData = data;
      const workflow = data.workflow || {};
      const statusAction = data.workflow_status_action || {};
      const statusOptions = Array.isArray(statusAction.options) ? statusAction.options : [];
      const currentStatus = String(workflow.business_status || statusAction.current || "");
      const optionValues = statusOptions.map((item) => String(item?.value || "").trim()).filter(Boolean);
      const selectedStatus = optionValues.includes(currentStatus) ? currentStatus : optionValues[0] || "";
      if (statusSelect) {
        statusSelect.innerHTML = statusOptions.length
          ? statusOptions
              .map(
                (item) => `<option value="${escapeHtmlAttribute(item.value)}" ${
                  String(item.value || "") === selectedStatus ? "selected" : ""
                }>${escapeHtml(item.label || item.value || "Status")}</option>`
              )
              .join("")
          : '<option value="">Brak statusów do wyboru</option>';
        statusSelect.value = selectedStatus;
      }
      if (statusSignatureDeadline) {
        statusSignatureDeadline.value = formatDateInputValue(workflow.signature_deadline_at);
        statusSignatureDeadline.disabled = selectedStatus !== "WAITING_SIGNATURE";
      }
      if (statusSubtitle) {
        statusSubtitle.textContent = `Formularz ${data.form?.id || "—"} / ${workflowBusinessStatusLabel(currentStatus)}`;
      }
      statusModal.hidden = false;
      statusSelect?.focus();
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd ładowania statusu.");
    } finally {
      setBusy(statusSaveBtn, false, "Ładowanie...", "Zapisz status");
      if (statusSaveBtn) {
        statusSaveBtn.disabled = !canSaveWorkflowStatus();
      }
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
      const assigneeOptions = Array.isArray(data.sheet_assignee_options) ? data.sheet_assignee_options : [];
      const selectedAssigneeId = Number(data.sheet_assignee_selected_id || 0);
      renderSheetAssigneeOptions(workflowSheetAssignee, assigneeOptions, selectedAssigneeId);
      renderSheetAssigneeOptions(proformaSheetAssignee, assigneeOptions, selectedAssigneeId);
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

  function renderSummarySection(title, rows) {
    const visibleRows = (rows || []).filter((row) => normalizeText(row.value));
    if (!visibleRows.length) {
      return "";
    }
    return `<article class="genform-summary-section">
      <h3>${escapeHtml(title)}</h3>
      <dl class="genform-detail-fields">${renderFieldCards(visibleRows)}</dl>
    </article>`;
  }

  async function openSummaryModal(formId) {
    if (!token) {
      showLogin("Brak aktywnej sesji.");
      return;
    }
    const numericFormId = Number(formId);
    if (!Number.isFinite(numericFormId) || numericFormId <= 0 || !summaryModal || !summaryContent) {
      return;
    }
    clearMessages();
    try {
      const response = await fetch(`/admin/contracts/forms/${numericFormId}/workflow`, {
        headers: headers(false),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się pobrać podsumowania.");
      }
      const form = data.form || {};
      const workflow = data.workflow || {};
      const payload = form.payload || {};
      const salesPacket = data.sales_packet || {};
      const mailboxMeta = workflow.status_history || [];
      if (summarySubtitle) {
        summarySubtitle.textContent = `Formularz ${form.id || "—"} / ${workflowBusinessStatusLabel(workflow.business_status)}`;
      }
      summaryContent.innerHTML = [
        renderSummarySection("Klient", [
          { label: "Firma", value: payload.company_name || form.customer_name },
          { label: "NIP", value: payload.company_nip || form.customer_nip },
          { label: "E-mail", value: payload.company_email || form.customer_email },
          { label: "Telefon", value: payload.company_phone || form.customer_phone },
          { label: "Adres", value: formatCustomerAddress(payload) },
        ]),
        renderSummarySection("Workflow", [
          { label: "Status GRENKE", value: workflowBusinessStatusLabel(workflow.business_status) },
          { label: "Termin podpisu", value: formatDate(workflow.signature_deadline_at) },
          { label: "Proforma", value: workflow.proforma_number },
          { label: "Klient MS", value: workflow.firebird_client_id ? `ID ${workflow.firebird_client_id}` : "" },
          { label: "Urządzenia", value: String(workflow.devices_selected_count || "") },
        ]),
        renderSummarySection("Dostawa", [
          { label: "Termin", value: workflow.delivery_label },
          { label: "Kontakt", value: workflow.delivery_contact_name },
          { label: "Telefon", value: workflow.delivery_contact_phone },
          { label: "Uwagi", value: workflow.delivery_notes },
        ]),
        `<article class="genform-summary-section"><h3>Urządzenia</h3>${
          Array.isArray(salesPacket.devices) && salesPacket.devices.length
            ? `<ul class="genform-summary-list">${salesPacket.devices
                .map((device) => `<li>${escapeHtml([device.producer, device.model, device.serial, device.price_gross].filter(Boolean).join(" / "))}</li>`)
                .join("")}</ul>`
            : '<p class="genform-detail-empty">Brak urządzeń.</p>'
        }</article>`,
        `<article class="genform-summary-section"><h3>Historia statusów</h3>${
          Array.isArray(mailboxMeta) && mailboxMeta.length
            ? `<ul class="genform-summary-list">${mailboxMeta
                .map((event) => `<li>${escapeHtml(formatDate(event.changed_at))}: ${escapeHtml(event.label || event.status || "")} (${escapeHtml(event.source || "")})</li>`)
                .join("")}</ul>`
            : '<p class="genform-detail-empty">Brak historii statusów.</p>'
        }</article>`,
      ].join("");
      summaryModal.hidden = false;
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd ładowania podsumowania.");
    }
  }

  async function archiveForm(formId) {
    clearMessages();
    try {
      const response = await fetch(`/admin/contracts/forms/${formId}/archive`, {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify({}),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się przenieść formularza do archiwum.");
      }
      showSuccess(data.message || "Formularz przeniesiono do archiwum.");
      await loadItems(false);
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd archiwizacji formularza.");
    }
  }

  async function extendArchive(formId) {
    clearMessages();
    try {
      const response = await fetch(`/admin/contracts/forms/${formId}/archive/extend`, {
        method: "POST",
        headers: headers(false),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się przedłużyć terminu archiwizacji.");
      }
      showSuccess(data.message || "Termin archiwizacji przedłużony.");
      await loadItems(false);
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd przedłużenia archiwizacji.");
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
        `/admin/contracts/dashboard?forms_scope=all&include_devices=0&archive_scope=${encodeURIComponent(activeArchiveScope)}`,
        { headers: headers(false) }
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się pobrać listy formularzy.");
      }
      latestForms = Array.isArray(data.forms) ? data.forms : [];
      renderArchiveMenu(data.archive_totals || {});
      renderMailboxSyncNote(data.mailbox_sync || null);
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

  function renderArchiveMenu(totals) {
    archiveMenuItems.forEach((button) => {
      const scope = button.getAttribute("data-archive-scope") || "active";
      button.classList.toggle("is-active", scope === activeArchiveScope);
    });
    const mapping = {
      active: document.getElementById("genform-count-active"),
      accepted: document.getElementById("genform-count-accepted"),
      rejected: document.getElementById("genform-count-rejected"),
      unfilled: document.getElementById("genform-count-unfilled"),
    };
    Object.entries(mapping).forEach(([scope, element]) => {
      if (element) {
        element.textContent = String(Number(totals?.[scope] || 0));
      }
    });
  }

  function renderMailboxSyncNote(mailboxSync) {
    if (!mailboxSyncNote) {
      return;
    }
    if (!mailboxSync?.available || !mailboxSync?.last_run_at) {
      mailboxSyncNote.textContent = "Synchronizacja e-mail GRENKE: brak danych o ostatnim przebiegu.";
      return;
    }

    const resultLabel = mailboxSyncResultLabel(mailboxSync.result);
    const sourceLabel = mailboxSync.source === "scheduler" ? "automat" : "ręcznie";
    const summary = mailboxSync.summary && typeof mailboxSync.summary === "object"
      ? mailboxSync.summary
      : null;
    const updatedCount = Number(summary?.updated || 0);
    const warningsCount = Number(summary?.warnings || 0);
    const countersLabel = summary
      ? `, zaktualizowane: ${updatedCount}, ostrzeżenia: ${warningsCount}`
      : "";

    mailboxSyncNote.textContent =
      `Synchronizacja e-mail GRENKE: ${resultLabel}, ${formatDate(mailboxSync.last_run_at)} `
      + `(${sourceLabel}${countersLabel}).`;
  }

  function normalizeDataEnteredStatus(rawStatus) {
    if (!rawStatus || typeof rawStatus !== "object") {
      return { sent: false, sentAt: null, recipientEmail: null };
    }
    return {
      sent: Boolean(rawStatus.sent),
      sentAt: rawStatus.sent_at || null,
      recipientEmail: rawStatus.recipient_email || null,
    };
  }

  function updateDataEnteredButtonState() {
    if (!detailDataEnteredBtn) {
      return;
    }
    const isSubmitted = currentDetailData?.item?.status === "SUBMITTED";
    const status = currentDetailData?.dataEnteredEmail || {
      sent: false,
      sentAt: null,
      recipientEmail: null,
    };
    if (!isSubmitted) {
      detailDataEnteredBtn.hidden = true;
      detailDataEnteredBtn.disabled = true;
      detailDataEnteredBtn.classList.remove("is-complete");
      detailDataEnteredBtn.textContent = "Dane zostały wpisane";
      detailDataEnteredBtn.title = "Przycisk dostępny po wypełnieniu formularza przez klienta.";
      return;
    }
    detailDataEnteredBtn.hidden = false;
    if (detailDataEnteredBusy) {
      detailDataEnteredBtn.disabled = true;
      detailDataEnteredBtn.classList.remove("is-complete");
      detailDataEnteredBtn.textContent = "Wysyłanie…";
      detailDataEnteredBtn.title = "Trwa wysyłka powiadomienia e-mail.";
      return;
    }
    if (status.sent) {
      detailDataEnteredBtn.disabled = true;
      detailDataEnteredBtn.classList.add("is-complete");
      detailDataEnteredBtn.textContent = "E-mail wysłany";
      detailDataEnteredBtn.title = status.sentAt
        ? `Wysłano: ${formatDate(status.sentAt)}`
        : "Wiadomość została już wysłana.";
      return;
    }
    detailDataEnteredBtn.disabled = false;
    detailDataEnteredBtn.classList.remove("is-complete");
    detailDataEnteredBtn.textContent = "Dane zostały wpisane";
    detailDataEnteredBtn.title = "Wyślij klientowi informację o kolejnych krokach umowy.";
  }

  async function sendDataEnteredNotification() {
    if (!token) {
      showLogin("Brak aktywnej sesji.");
      return;
    }
    if (!openedFormId || !currentDetailData || currentDetailData.item?.status !== "SUBMITTED") {
      showError("Powiadomienie można wysłać tylko dla formularza w statusie „Wypełniony”.");
      return;
    }
    if (currentDetailData?.dataEnteredEmail?.sent) {
      showSuccess("Wiadomość była już wysłana wcześniej.");
      return;
    }

    clearMessages();
    detailDataEnteredBusy = true;
    updateDataEnteredButtonState();
    try {
      const response = await fetch(`/admin/forms/${openedFormId}/notify-data-entered`, {
        method: "POST",
        headers: headers(false),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się wysłać wiadomości e-mail.");
      }

      currentDetailData.dataEnteredEmail = {
        sent: true,
        sentAt: data.sent_at || new Date().toISOString(),
        recipientEmail: data.recipient_email || null,
      };
      updateDataEnteredButtonState();
      showSuccess(
        data.message ||
          "Wiadomość została wysłana do klienta z informacją o dalszych krokach procesu."
      );
    } catch (err) {
      showError(err instanceof Error ? err.message : "Błąd wysyłki wiadomości e-mail.");
    } finally {
      detailDataEnteredBusy = false;
      updateDataEnteredButtonState();
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
        dataEnteredEmail: normalizeDataEnteredStatus(data.data_entered_email),
      };
      if (detailStatus) {
        detailStatus.textContent = currentDetailData.statusMessage;
      }
      renderDetailSections(currentDetailData);

      openedFormId = formId;
      detailDataEnteredBusy = false;
      updateDataEnteredButtonState();
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
      closeStatusModal();
      closeSummaryModal();
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
  statusCloseBtn?.addEventListener("click", closeStatusModal);
  summaryCloseBtn?.addEventListener("click", closeSummaryModal);
  proformaCloseBtn?.addEventListener("click", closeProformaModal);
  detailDataEnteredBtn?.addEventListener("click", sendDataEnteredNotification);
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
  statusModal?.addEventListener("click", (event) => {
    if (event.target === statusModal) {
      closeStatusModal();
    }
  });
  summaryModal?.addEventListener("click", (event) => {
    if (event.target === summaryModal) {
      closeSummaryModal();
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
      return;
    }
    if (event.key === "Escape" && statusModal && !statusModal.hidden) {
      closeStatusModal();
      return;
    }
    if (event.key === "Escape" && summaryModal && !summaryModal.hidden) {
      closeSummaryModal();
    }
  });
  window.addEventListener("afterprint", clearPrintMode);
  window.addEventListener("pageshow", () => {
    closeDetailModal();
    closeWorkflowModal();
    closeStatusModal();
    closeSummaryModal();
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
      } else if (action === "status") {
        openStatusModal(formId);
      } else if (action === "summary") {
        openSummaryModal(formId);
      } else if (action === "release-resources") {
        activeWorkflowFormId = formId;
        releaseWorkflowSheetReservations();
      } else if (action === "archive") {
        archiveForm(formId);
      } else if (action === "extend-archive") {
        extendArchive(formId);
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
  statusSaveBtn?.addEventListener("click", () => {
    saveWorkflowStatus();
  });
  statusSelect?.addEventListener("change", () => {
    if (statusSaveBtn) {
      statusSaveBtn.disabled = !canSaveWorkflowStatus();
    }
    if (statusSignatureDeadline) {
      statusSignatureDeadline.disabled = statusSelect.value !== "WAITING_SIGNATURE";
    }
  });
  archiveMenuItems.forEach((button) => {
    button.addEventListener("click", () => {
      activeArchiveScope = button.getAttribute("data-archive-scope") || "active";
      loadItems(false);
    });
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
