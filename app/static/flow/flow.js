const FLOW_TOKEN_KEY = "admin-session-token";

function readFlowToken() {
  return (
    window.localStorage?.getItem(FLOW_TOKEN_KEY) ||
    window.sessionStorage?.getItem(FLOW_TOKEN_KEY) ||
    null
  );
}

function clearFlowToken() {
  window.localStorage?.removeItem(FLOW_TOKEN_KEY);
  window.sessionStorage?.removeItem(FLOW_TOKEN_KEY);
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
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
    return date.toLocaleString("pl-PL");
  } catch (err) {
    return value;
  }
}

function formatDateOnly(value) {
  if (!value) {
    return "—";
  }
  try {
    const date = new Date(`${value}T00:00:00`);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return date.toLocaleDateString("pl-PL", {
      weekday: "short",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  } catch (err) {
    return value;
  }
}

function formStatusLabel(status) {
  const mapped = {
    GENERATED: "Wygenerowany",
    DISPATCHED: "Wyslany",
    SUBMITTED: "Wypelniony",
    EXPIRED: "Wygasl",
  };
  return mapped[status] || status || "Nieznany";
}

function formStatusClass(status) {
  if (status === "SUBMITTED") {
    return "ok";
  }
  if (status === "EXPIRED") {
    return "warn";
  }
  return "soft";
}

function deviceActionLabel(action) {
  const mapped = {
    synchronizuj: "Synchronizuj",
    podlacz: "Synchronizuj",
    do_weryfikacji: "Do weryfikacji",
  };
  return mapped[action] || action || "Akcja";
}

function workflowStageLabel(stage) {
  const mapped = {
    FORM_SUBMITTED: "Formularz wypelniony",
    CLIENT_READY: "Klient gotowy",
    DEVICES_SELECTED: "Urzadzenia wybrane",
    PROFORMA_CREATED: "Proforma gotowa",
  };
  return mapped[stage] || stage || "Nieznany etap";
}

function workflowBusinessStatusLabel(status) {
  const mapped = {
    DRAFT: "Robocza",
    PENDING_APPROVAL: "Oczekuje na akceptacje",
    APPROVED: "Zaakceptowano",
    ZEROWKA: "Zerowka",
    REJECTED: "Odrzucono",
  };
  return mapped[status] || status || "Robocza";
}

function humanizeValue(value) {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "Tak" : "Nie";
  }
  return String(value);
}

function detailLabel(path) {
  const mapped = {
    company_name: "Nazwa firmy",
    company_nip: "NIP",
    company_phone: "Telefon firmy",
    company_email: "E-mail firmy",
    billing_email: "E-mail do faktur",
    registered_street: "Ulica rejestrowa",
    registered_building_no: "Nr budynku rejestrowy",
    registered_apartment_no: "Nr lokalu rejestrowy",
    registered_postal_code: "Kod pocztowy rejestrowy",
    registered_city: "Miasto rejestrowe",
    correspondence_same_as_registered: "Adres korespondencyjny jak rejestrowy",
    correspondence_street: "Ulica korespondencyjna",
    correspondence_building_no: "Nr budynku korespondencyjny",
    correspondence_apartment_no: "Nr lokalu korespondencyjny",
    correspondence_postal_code: "Kod pocztowy korespondencyjny",
    correspondence_city: "Miasto korespondencyjne",
    consent: "Zgoda",
    submitted_at: "Data wypelnienia",
    client_ip: "Adres IP",
    user_agent: "User-Agent",
  };
  if (mapped[path]) {
    return mapped[path];
  }
  const representativeMatch = path.match(/^representatives\[(\d+)\]\.(.+)$/);
  if (representativeMatch) {
    const index = Number(representativeMatch[1]) + 1;
    const suffix = detailLabel(representativeMatch[2]);
    return `Reprezentant ${index} / ${suffix}`;
  }
  return path.replaceAll("_", " ");
}

function flattenDetails(value, prefix = "") {
  if (Array.isArray(value)) {
    return value.flatMap((entry, index) => flattenDetails(entry, `${prefix}[${index}]`));
  }
  if (value && typeof value === "object") {
    return Object.entries(value).flatMap(([key, nested]) => {
      const path = prefix ? `${prefix}.${key}` : key;
      return flattenDetails(nested, path);
    });
  }
  return [{ key: prefix, value: humanizeValue(value) }];
}

async function copyText(value) {
  const text = String(value ?? "");
  if (!text) {
    return;
  }
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "true");
  textarea.style.position = "absolute";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  document.body.removeChild(textarea);
}

function workflowSummaryLines(workflow) {
  if (!workflow || !workflow.exists) {
    return ["Brak zapisanej sprawy CTIP."];
  }
  const lines = [workflowStageLabel(workflow.stage)];
  if (workflow.business_status) {
    lines.push(`Status: ${workflowBusinessStatusLabel(workflow.business_status)}`);
  }
  if (workflow.firebird_client_id) {
    lines.push(`Klient MS: ID ${workflow.firebird_client_id}`);
  }
  if (workflow.devices_selected_count) {
    lines.push(`Urzadzenia CTIP: ${workflow.devices_selected_count}`);
  }
  if (workflow.proforma_number) {
    lines.push(`Proforma: ${workflow.proforma_number}`);
  }
  if (workflow.delivery_label) {
    lines.push(`Dowoz: ${workflow.delivery_label}`);
  }
  return lines;
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

function toIsoDate(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const year = String(date.getFullYear());
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function shiftIsoDate(isoDate, diffDays) {
  const date = new Date(`${isoDate}T00:00:00`);
  if (Number.isNaN(date.getTime())) {
    return isoDate;
  }
  date.setDate(date.getDate() + diffDays);
  return toIsoDate(date);
}

function resolveInitialSection() {
  const fromHash = window.location.hash.replace("#", "").trim();
  if (fromHash === "devices") {
    return "devices";
  }
  if (fromHash === "schedule") {
    return "schedule";
  }
  return "contracts";
}

async function initializeFlowPage() {
  let token = readFlowToken();

  const refreshBtn = document.getElementById("flow-refresh");
  const logoutBtn = document.getElementById("flow-logout");
  const userChip = document.getElementById("flow-user-chip");
  const errorBox = document.getElementById("flow-error");
  const infoBox = document.getElementById("flow-info");
  const formsBody = document.getElementById("flow-forms-body");
  const formsEmpty = document.getElementById("flow-forms-empty");
  const devicesBody = document.getElementById("flow-devices-body");
  const detailModal = document.getElementById("flow-form-detail-modal");
  const detailCloseBtn = document.getElementById("flow-form-detail-close");
  const detailBody = document.getElementById("flow-form-detail-body");
  const detailId = document.getElementById("flow-detail-id");
  const detailStatus = document.getElementById("flow-detail-status");
  const detailMs = document.getElementById("flow-detail-ms");
  const workflowModal = document.getElementById("flow-workflow-modal");
  const workflowCloseBtn = document.getElementById("flow-workflow-close");
  const workflowFormId = document.getElementById("flow-workflow-form-id");
  const workflowStage = document.getElementById("flow-workflow-stage");
  const workflowClientId = document.getElementById("flow-workflow-client-id");
  const workflowDevicesCount = document.getElementById("flow-workflow-devices-count");
  const workflowBusinessStatus = document.getElementById("flow-workflow-business-status");
  const workflowDeliverySummary = document.getElementById("flow-workflow-delivery-summary");
  const workflowClientPreview = document.getElementById("flow-workflow-client-preview");
  const workflowClientActionLabel = document.getElementById("flow-workflow-client-action-label");
  const workflowClientSaveBtn = document.getElementById("flow-workflow-client-save");
  const workflowOpenDevicesBtn = document.getElementById("flow-workflow-open-devices");
  const workflowClientNote = document.getElementById("flow-workflow-client-note");
  const workflowDeviceSearch = document.getElementById("flow-workflow-device-search");
  const workflowDeviceStatusFilter = document.getElementById("flow-workflow-device-status-filter");
  const workflowDeviceReservationFilter = document.getElementById(
    "flow-workflow-device-reservation-filter",
  );
  const workflowDeviceNote = document.getElementById("flow-workflow-device-note");
  const workflowSelectionSummary = document.getElementById("flow-workflow-selection-summary");
  const workflowDevicePickerBody = document.getElementById("flow-workflow-device-picker-body");
  const workflowDevicesSaveBtn = document.getElementById("flow-workflow-devices-save");
  const workflowProformaCreateBtn = document.getElementById("flow-workflow-proforma-create");
  const workflowProformaBank = document.getElementById("flow-workflow-proforma-bank");
  const workflowProformaPreviewLink = document.getElementById("flow-workflow-proforma-preview");
  const workflowProformaNote = document.getElementById("flow-workflow-proforma-note");
  const workflowDeliverySection = document.getElementById("flow-workflow-delivery-section");
  const workflowDeliveryDate = document.getElementById("flow-workflow-delivery-date");
  const workflowDeliveryTimeWindow = document.getElementById("flow-workflow-delivery-time-window");
  const workflowDeliveryContactName = document.getElementById("flow-workflow-delivery-contact-name");
  const workflowDeliveryContactPhone = document.getElementById("flow-workflow-delivery-contact-phone");
  const workflowDeliveryNotes = document.getElementById("flow-workflow-delivery-notes");
  const workflowDeliverySaveBtn = document.getElementById("flow-workflow-delivery-save");
  const workflowDeliveryClearBtn = document.getElementById("flow-workflow-delivery-clear");
  const workflowDeliveryNote = document.getElementById("flow-workflow-delivery-note");
  const workflowStatusSelect = document.getElementById("flow-workflow-status-select");
  const workflowStatusSaveBtn = document.getElementById("flow-workflow-status-save");
  const workflowStatusNote = document.getElementById("flow-workflow-status-note");
  const salesClient = document.getElementById("flow-sales-client");
  const salesRepresentatives = document.getElementById("flow-sales-representatives");
  const salesDevicesBody = document.getElementById("flow-sales-devices-body");
  const salesProforma = document.getElementById("flow-sales-proforma");
  const scheduleFromInput = document.getElementById("flow-schedule-from");
  const scheduleToInput = document.getElementById("flow-schedule-to");
  const scheduleLoadBtn = document.getElementById("flow-schedule-load");
  const scheduleEmpty = document.getElementById("flow-schedule-empty");
  const scheduleBoard = document.getElementById("flow-schedule-board");
  const navButtons = Array.from(document.querySelectorAll(".flow-nav-btn"));
  const panels = Array.from(document.querySelectorAll("[data-section-panel]"));

  const statNodes = {
    formsTotal: document.getElementById("flow-forms-total"),
    formsGenerated: document.getElementById("flow-forms-generated"),
    formsDispatched: document.getElementById("flow-forms-dispatched"),
    formsSubmitted: document.getElementById("flow-forms-submitted"),
    formsExpired: document.getElementById("flow-forms-expired"),
    devicesTotal: document.getElementById("flow-devices-total"),
    devicesMatched: document.getElementById("flow-devices-matched"),
    devicesPending: document.getElementById("flow-devices-pending"),
  };

  if (
    !refreshBtn ||
    !logoutBtn ||
    !userChip ||
    !errorBox ||
    !infoBox ||
    !formsBody ||
    !formsEmpty ||
    !devicesBody ||
    !detailModal ||
    !detailCloseBtn ||
    !detailBody ||
    !detailId ||
    !detailStatus ||
    !detailMs ||
    !workflowModal ||
    !workflowCloseBtn ||
    !workflowFormId ||
    !workflowStage ||
    !workflowClientId ||
    !workflowDevicesCount ||
    !workflowBusinessStatus ||
    !workflowDeliverySummary ||
    !workflowClientPreview ||
    !workflowClientActionLabel ||
    !workflowClientSaveBtn ||
    !workflowOpenDevicesBtn ||
    !workflowClientNote ||
    !workflowDeviceSearch ||
    !workflowDeviceStatusFilter ||
    !workflowDeviceReservationFilter ||
    !workflowDeviceNote ||
    !workflowSelectionSummary ||
    !workflowDevicePickerBody ||
    !workflowDevicesSaveBtn ||
    !workflowProformaCreateBtn ||
    !workflowProformaBank ||
    !workflowProformaPreviewLink ||
    !workflowProformaNote ||
    !workflowDeliverySection ||
    !workflowDeliveryDate ||
    !workflowDeliveryTimeWindow ||
    !workflowDeliveryContactName ||
    !workflowDeliveryContactPhone ||
    !workflowDeliveryNotes ||
    !workflowDeliverySaveBtn ||
    !workflowDeliveryClearBtn ||
    !workflowDeliveryNote ||
    !workflowStatusSelect ||
    !workflowStatusSaveBtn ||
    !workflowStatusNote ||
    !salesClient ||
    !salesRepresentatives ||
    !salesDevicesBody ||
    !salesProforma ||
    !scheduleFromInput ||
    !scheduleToInput ||
    !scheduleLoadBtn ||
    !scheduleEmpty ||
    !scheduleBoard ||
    !statNodes.formsTotal ||
    !statNodes.formsGenerated ||
    !statNodes.formsDispatched ||
    !statNodes.formsSubmitted ||
    !statNodes.formsExpired ||
    !statNodes.devicesTotal ||
    !statNodes.devicesMatched ||
    !statNodes.devicesPending
  ) {
    return;
  }

  const headers = () => {
    if (!token) {
      return {};
    }
    return { "X-Admin-Session": token };
  };

  let latestForms = [];
  let latestScheduleItems = [];
  let activeWorkflowFormId = null;
  let activeWorkflowData = null;
  let currentUser = null;

  const setError = (message) => {
    errorBox.textContent = message || "";
    errorBox.hidden = !message;
  };

  const setInfo = (message) => {
    infoBox.textContent = message || "";
    infoBox.hidden = !message;
  };

  const setBusy = (busy) => {
    refreshBtn.disabled = busy;
    refreshBtn.textContent = busy ? "Odswiezanie..." : "Odswiez";
  };

  const ensureCurrentUser = async ({ forceRefresh = false } = {}) => {
    if (currentUser && !forceRefresh) {
      return currentUser;
    }
    const response = await fetch("/auth/me", { headers: headers() });
    if (response.status === 401) {
      throw new Error("Sesja wygasla.");
    }
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.detail || "Nie udalo sie pobrac danych sesji.");
    }
    currentUser = await response.json();
    return currentUser;
  };

  const closeDetailModal = () => {
    detailModal.hidden = true;
  };

  const closeWorkflowModal = () => {
    workflowModal.hidden = true;
    activeWorkflowFormId = null;
    activeWorkflowData = null;
  };

  const openDetailModal = (formId) => {
    const item = latestForms.find((entry) => Number(entry.id) === Number(formId));
    if (!item) {
      setError(`Nie znaleziono danych formularza ID ${formId}.`);
      return;
    }

    const detailRows = [
      { key: "customer_name", value: item.customer_name || "—" },
      { key: "customer_email", value: item.customer_email || "—" },
      { key: "customer_phone", value: item.customer_phone || "—" },
      { key: "status_message", value: item.status_message || "—", label: "Opis statusu" },
      { key: "token_expires_at", value: formatDate(item.token_expires_at), label: "Waznosc linku" },
      ...flattenDetails(item.payload || {}),
      ...flattenDetails(item.meta || {}),
    ];

    detailId.textContent = String(item.id);
    detailStatus.textContent = formStatusLabel(item.status);
    detailMs.textContent =
      item.status === "SUBMITTED"
        ? item.firebird?.found
          ? `ID ${item.firebird.id_klient}`
          : "Brak klienta"
        : "Poza etapem Menadzera Serwisu";

    detailBody.innerHTML = detailRows
      .filter((row) => row.key || row.label)
      .map((row) => {
        const label = row.label || detailLabel(row.key);
        const value = humanizeValue(row.value);
        return `
          <tr>
            <td class="flow-detail-key">${escapeHtml(label)}</td>
            <td class="flow-detail-value">${escapeHtml(value)}</td>
            <td>
              <button
                type="button"
                class="flow-copy-btn"
                data-copy-value="${escapeHtml(value)}"
              >
                Kopiuj
              </button>
            </td>
          </tr>
        `;
      })
      .join("");

    detailModal.hidden = false;
  };

  const getSelectedWorkflowRows = () => {
    if (!activeWorkflowData?.available_devices) {
      return [];
    }
    return activeWorkflowData.available_devices
      .filter((item) => item.selected)
      .map((item) => Number(item.row))
      .filter((value) => Number.isFinite(value) && value > 0)
      .sort((left, right) => left - right);
  };

  const getSelectedWorkflowDevices = () => {
    if (!activeWorkflowData?.available_devices) {
      return [];
    }
    return activeWorkflowData.available_devices
      .filter((item) => item.selected)
      .sort((left, right) => Number(left.row || 0) - Number(right.row || 0));
  };

  const ensureWorkflowDevicePrices = () => {
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
  };

  const updateWorkflowSelectionSummary = () => {
    const selectedDevices = getSelectedWorkflowDevices();
    if (selectedDevices.length === 0) {
      workflowSelectionSummary.textContent = "Brak wybranych urzadzen.";
      return;
    }
    const selectedRows = selectedDevices.map((item) => Number(item.row));
    const grossTotal = selectedDevices.reduce((total, item) => {
      const parsed = parsePriceValue(item.price_gross);
      return total + (parsed || 0);
    }, 0);
    const baseSummary =
      selectedRows.length === 1
        ? `Wybrano 1 urzadzenie: wiersz ${selectedRows[0]}.`
        : `Wybrano ${selectedRows.length} urzadzenia: ${selectedRows.join(", ")}.`;
    workflowSelectionSummary.textContent = `${baseSummary} Suma brutto: ${formatPriceValue(grossTotal)} PLN.`;
  };

  const renderSalesPacket = () => {
    const packet = activeWorkflowData?.sales_packet || {};
    const clientFields = Array.isArray(packet.client_fields) ? packet.client_fields : [];
    const representatives = Array.isArray(packet.representatives) ? packet.representatives : [];
    const devices = Array.isArray(packet.devices) ? packet.devices : [];

    salesClient.innerHTML = clientFields.length
      ? clientFields
          .map(
            (item) => `
              <article class="flow-preview-item">
                <span>${escapeHtml(item.label || "Pole")}</span>
                <strong>${escapeHtml(item.value || "—")}</strong>
              </article>
            `,
          )
          .join("")
      : "<p class='flow-note'>Brak zapisanych danych klienta.</p>";

    salesRepresentatives.innerHTML = representatives.length
      ? representatives
          .map(
            (item) => `
              <article class="flow-preview-item">
                <span>${escapeHtml(item.label || "Reprezentant")}</span>
                <strong>${escapeHtml(item.value || "—")}</strong>
              </article>
            `,
          )
          .join("")
      : "<p class='flow-note'>Brak reprezentantow w formularzu.</p>";

    salesDevicesBody.innerHTML = devices.length
      ? devices
          .map(
            (item) => `
              <tr>
                <td>${escapeHtml(item.row || "—")}</td>
                <td>${escapeHtml([item.producer, item.model].filter(Boolean).join(" ") || "—")}</td>
                <td>${escapeHtml(item.serial || "—")}</td>
                <td>${escapeHtml(item.ewidencja || "—")}</td>
                <td>${escapeHtml(item.price_net || "—")}</td>
                <td>${escapeHtml(item.price_gross || "—")}</td>
              </tr>
            `,
          )
          .join("")
      : "<tr><td colspan='6'>Brak wybranych urzadzen.</td></tr>";

    const proformaItems = [];
    if (packet.proforma_number) {
      proformaItems.push({
        label: "Numer proformy",
        value: packet.proforma_number,
      });
    }
    if (packet.proforma_preview_url) {
      proformaItems.push({
        label: "Podglad A4",
        value: packet.proforma_preview_url,
      });
    }
    salesProforma.innerHTML = proformaItems.length
      ? proformaItems
          .map(
            (item) => `
              <article class="flow-preview-item">
                <span>${escapeHtml(item.label || "Pole")}</span>
                <strong>${escapeHtml(item.value || "—")}</strong>
              </article>
            `,
          )
          .join("")
      : "<p class='flow-note'>Proforma nie zostala jeszcze zapisana.</p>";
  };

  const syncSalesPacketDevicesFromSelection = () => {
    if (!activeWorkflowData) {
      return;
    }
    const selectedDevices = getSelectedWorkflowDevices().map((item) => ({
      row: item.row,
      producer: item.producer || "",
      model: item.model || "",
      serial: item.serial || "",
      ewidencja: item.ewidencja || "",
      price_net: item.price_net || "",
      price_gross: item.price_gross || "",
    }));
    activeWorkflowData.sales_packet = {
      ...(activeWorkflowData.sales_packet || {}),
      devices: selectedDevices,
    };
    renderSalesPacket();
  };

  const renderWorkflowDevicePicker = () => {
    if (!activeWorkflowData?.available_devices) {
      workflowDevicePickerBody.innerHTML = "<tr><td colspan='10'>Brak danych urzadzen.</td></tr>";
      updateWorkflowSelectionSummary();
      return;
    }

    const phrase = workflowDeviceSearch.value.trim().toLowerCase();
    const statusFilter = workflowDeviceStatusFilter.value.trim();
    const reservationFilter = workflowDeviceReservationFilter.value.trim();
    const filtered = activeWorkflowData.available_devices.filter((item) => {
      if (statusFilter && item.status !== statusFilter) {
        return false;
      }
      if (reservationFilter && item.reservation_status !== reservationFilter) {
        return false;
      }
      if (!phrase) {
        return true;
      }
      const haystack = [
        item.producer,
        item.model,
        item.serial,
        item.ewidencja,
        item.status,
        item.reservation_status,
      ]
        .join(" ")
        .toLowerCase();
      return haystack.includes(phrase);
    });

    if (filtered.length === 0) {
      workflowDevicePickerBody.innerHTML =
        "<tr><td colspan='10'>Brak urzadzen dla podanych filtrow.</td></tr>";
      updateWorkflowSelectionSummary();
      return;
    }

    workflowDevicePickerBody.innerHTML = filtered
      .map(
        (item) => `
          <tr>
            <td>
              <input
                type="checkbox"
                class="flow-device-picker-select"
                data-workflow-device-row="${escapeHtml(item.row)}"
                ${item.selected ? "checked" : ""}
              >
            </td>
            <td>${escapeHtml(item.row)}</td>
            <td>${escapeHtml(item.producer || "—")}</td>
            <td>${escapeHtml(item.model || "—")}</td>
            <td>${escapeHtml(item.serial || "—")}</td>
            <td>${escapeHtml(item.ewidencja || "—")}</td>
            <td>${escapeHtml(item.status || "—")}</td>
            <td>${escapeHtml(item.reservation_status || "—")}</td>
            <td>
              <input
                type="text"
                inputmode="decimal"
                class="flow-price-input"
                data-workflow-device-price-net="${escapeHtml(item.row)}"
                value="${escapeHtml(item.price_net || "")}"
                placeholder="0.00"
              >
            </td>
            <td>
              <input
                type="text"
                inputmode="decimal"
                class="flow-price-input"
                data-workflow-device-price-gross="${escapeHtml(item.row)}"
                value="${escapeHtml(item.price_gross || item.price || "")}"
                placeholder="0.00"
              >
            </td>
          </tr>
        `,
      )
      .join("");

    updateWorkflowSelectionSummary();
  };

  const renderWorkflowModal = (data) => {
    activeWorkflowData = data;
    ensureWorkflowDevicePrices();
    const workflow = data.workflow || {};
    const clientAction = data.client_action || {};
    const previewItems = Array.isArray(data.client_preview) ? data.client_preview : [];
    const statusAction = data.workflow_status_action || {};

    workflowFormId.textContent = String(data.form?.id || "—");
    workflowStage.textContent = workflowStageLabel(workflow.stage);
    workflowClientId.textContent = workflow.firebird_client_id
      ? `ID ${workflow.firebird_client_id}`
      : "Brak";
    workflowDevicesCount.textContent = String(workflow.devices_selected_count || 0);
    workflowBusinessStatus.textContent = workflowBusinessStatusLabel(workflow.business_status);
    workflowDeliverySummary.textContent = workflow.delivery_label || "Brak";
    workflowDeliveryDate.value = workflow.delivery_date || "";
    workflowDeliveryTimeWindow.value = workflow.delivery_time_window || "";
    workflowDeliveryContactName.value = workflow.delivery_contact_name || "";
    workflowDeliveryContactPhone.value = workflow.delivery_contact_phone || "";
    workflowDeliveryNotes.value = workflow.delivery_notes || "";
    workflowDeliveryClearBtn.disabled = !workflow.delivery_planned;
    workflowDeliveryNote.textContent = workflow.delivery_label
      ? `Zaplanowany dowoz: ${workflow.delivery_label}.`
      : "Brak zapisanego terminu dowozu.";
    workflowClientActionLabel.textContent = clientAction.label || "Tryb podstawowy";
    workflowClientSaveBtn.textContent = clientAction.button_label || "Zapisz klienta";
    workflowClientSaveBtn.disabled = Boolean(workflow.firebird_client_id);
    workflowClientNote.textContent = workflow.firebird_client_id
      ? `Klient jest juz zapisany po stronie CTIP jako Menadzer Serwisu ID ${workflow.firebird_client_id}.`
      : "Najpierw potwierdz dane klienta do podstawowego tworzenia na potrzeby proformy.";
    const hasProforma = Boolean(workflow.proforma_number);
    const canCreateProforma =
      Boolean(workflow.firebird_client_id) &&
      Number(workflow.devices_selected_count || 0) > 0 &&
      !hasProforma;
    workflowProformaCreateBtn.disabled = !canCreateProforma;
    workflowProformaCreateBtn.textContent = hasProforma
      ? "Proforma zapisana"
      : canCreateProforma
        ? "Utworz proforme"
        : "Najpierw klient i urzadzenia";
    workflowProformaBank.checked = true;
    workflowProformaBank.disabled = hasProforma;
    if (workflow.proforma_preview_url) {
      workflowProformaPreviewLink.hidden = false;
      workflowProformaPreviewLink.href = workflow.proforma_preview_url;
    } else {
      workflowProformaPreviewLink.hidden = true;
      workflowProformaPreviewLink.href = "#";
    }
    updateWorkflowProformaNote();
    workflowStatusSelect.innerHTML = Array.isArray(statusAction.options)
      ? statusAction.options
          .map(
            (item) => `
              <option
                value="${escapeHtml(item.value)}"
                ${item.value === (workflow.business_status || statusAction.current) ? "selected" : ""}
              >
                ${escapeHtml(item.label)}
              </option>
            `,
          )
          .join("")
      : "";
    workflowStatusNote.textContent = workflow.proforma_number
      ? "Po akceptacji mozesz recznie ustawic status Zaakceptowano, a nastepnie Zerowka."
      : "Status biznesowy mozna ustawic juz teraz, ale praktyczny etap akceptacji zaczyna sie po proformie.";

    workflowClientPreview.innerHTML = previewItems.length
      ? previewItems
          .map(
            (item) => `
              <article class="flow-preview-item">
                <span>${escapeHtml(item.label || "Pole")}</span>
                <strong>${escapeHtml(item.value || "—")}</strong>
              </article>
            `,
          )
          .join("")
      : "<p class='flow-note'>Brak danych klienta do podgladu.</p>";

    const availableDevices = Array.isArray(data.available_devices) ? data.available_devices : [];
    workflowDeviceNote.textContent = data.selection_capabilities?.note || "";
    workflowDeviceStatusFilter.innerHTML =
      '<option value="">Wszystkie</option>' +
      uniqueValues(availableDevices, "status")
        .map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)
        .join("");
    workflowDeviceReservationFilter.innerHTML =
      '<option value="">Wszystkie</option>' +
      uniqueValues(availableDevices, "reservation_status")
        .map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`)
        .join("");
    renderWorkflowDevicePicker();
    syncSalesPacketDevicesFromSelection();
    workflowModal.hidden = false;
  };

  const openWorkflowModal = async (formId, focusSection = "client") => {
    const numericFormId = Number(formId);
    if (!numericFormId) {
      setError("Nieprawidlowe ID formularza.");
      return;
    }

    activeWorkflowFormId = numericFormId;
    workflowClientSaveBtn.disabled = true;
    workflowDevicesSaveBtn.disabled = true;
    workflowClientSaveBtn.textContent = "Ladowanie...";
    setError("");
    setInfo("");
    try {
      const response = await fetch(`/admin/contracts/forms/${numericFormId}/workflow`, {
        headers: headers(),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udalo sie pobrac danych sprawy.");
      }
      renderWorkflowModal(data);
      workflowClientSaveBtn.disabled = Boolean(data.workflow?.firebird_client_id);
      workflowDevicesSaveBtn.disabled = false;
      workflowClientSaveBtn.textContent =
        data.client_action?.button_label || "Zapisz klienta";
      if (focusSection === "devices") {
        workflowDeviceSearch.focus();
      }
      if (focusSection === "delivery") {
        workflowDeliverySection.scrollIntoView({ block: "start", behavior: "smooth" });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad ladowania sprawy.");
      workflowClientSaveBtn.disabled = false;
      workflowDevicesSaveBtn.disabled = false;
      workflowProformaCreateBtn.disabled = false;
      workflowClientSaveBtn.textContent = "Zapisz klienta";
      workflowDevicesSaveBtn.textContent = "Zapisz wybor urzadzen";
    }
  };

  const saveWorkflowClient = async () => {
    if (!activeWorkflowFormId) {
      setError("Nie wybrano formularza.");
      return;
    }

    workflowClientSaveBtn.disabled = true;
    workflowClientSaveBtn.textContent = "Zapisywanie...";
    setError("");
    setInfo("");
    try {
      const response = await fetch(`/admin/contracts/forms/${activeWorkflowFormId}/workflow/client`, {
        method: "POST",
        headers: {
          ...headers(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ mode: "basic_proforma" }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udalo sie zapisac klienta.");
      }
      setInfo(data.message || "Klient zapisany.");
      await loadData();
      await openWorkflowModal(activeWorkflowFormId, "devices");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad zapisu klienta.");
    } finally {
      if (workflowModal.hidden) {
        workflowClientSaveBtn.disabled = false;
        workflowClientSaveBtn.textContent = "Zapisz klienta";
      }
    }
  };

  const saveWorkflowDevices = async () => {
    if (!activeWorkflowFormId) {
      setError("Nie wybrano formularza.");
      return;
    }

    const selectedDevices = getSelectedWorkflowDevices().map((item) => ({
      row: Number(item.row),
      price_net: item.price_net || "",
      price_gross: item.price_gross || "",
    }));
    workflowDevicesSaveBtn.disabled = true;
    workflowDevicesSaveBtn.textContent = "Zapisywanie...";
    setError("");
    setInfo("");
    try {
      const response = await fetch(
        `/admin/contracts/forms/${activeWorkflowFormId}/workflow/devices`,
        {
          method: "POST",
          headers: {
            ...headers(),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ devices: selectedDevices }),
        },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udalo sie zapisac urzadzen.");
      }
      setInfo(data.message || "Urzadzenia zapisane.");
      await loadData();
      await openWorkflowModal(activeWorkflowFormId, "devices");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad zapisu urzadzen.");
    } finally {
      if (workflowModal.hidden) {
        workflowDevicesSaveBtn.disabled = false;
        workflowDevicesSaveBtn.textContent = "Zapisz wybor urzadzen";
      }
    }
  };

  const saveWorkflowStatus = async () => {
    if (!activeWorkflowFormId) {
      setError("Nie wybrano formularza.");
      return;
    }

    workflowStatusSaveBtn.disabled = true;
    workflowStatusSaveBtn.textContent = "Zapisywanie...";
    setError("");
    setInfo("");
    try {
      const response = await fetch(
        `/admin/contracts/forms/${activeWorkflowFormId}/workflow/status`,
        {
          method: "POST",
          headers: {
            ...headers(),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ business_status: workflowStatusSelect.value || "DRAFT" }),
        },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udalo sie zapisac statusu.");
      }
      setInfo(data.message || "Status zapisany.");
      await loadData();
      await openWorkflowModal(activeWorkflowFormId, "devices");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad zapisu statusu.");
    } finally {
      if (!workflowModal.hidden) {
        workflowStatusSaveBtn.disabled = false;
        workflowStatusSaveBtn.textContent = "Zapisz status";
      }
    }
  };

  const saveWorkflowProforma = async () => {
    if (!activeWorkflowFormId) {
      setError("Nie wybrano formularza.");
      return;
    }

    workflowProformaCreateBtn.disabled = true;
    workflowProformaCreateBtn.textContent = "Tworzenie...";
    setError("");
    setInfo("");
    try {
      const response = await fetch(
        `/admin/contracts/forms/${activeWorkflowFormId}/workflow/proforma`,
        {
          method: "POST",
          headers: {
            ...headers(),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            for_bank: Boolean(workflowProformaBank.checked),
          }),
        },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udalo sie utworzyc proformy.");
      }
      setInfo(data.message || "Proforma zapisana.");
      await loadData();
      await openWorkflowModal(activeWorkflowFormId, "devices");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad tworzenia proformy.");
      workflowProformaCreateBtn.disabled = false;
      workflowProformaCreateBtn.textContent = "Utworz proforme";
    }
  };

  const saveWorkflowDelivery = async () => {
    if (!activeWorkflowFormId) {
      setError("Nie wybrano formularza.");
      return;
    }
    if (!workflowDeliveryDate.value) {
      setError("Wybierz date dowozu.");
      return;
    }

    workflowDeliverySaveBtn.disabled = true;
    workflowDeliverySaveBtn.textContent = "Zapisywanie...";
    setError("");
    setInfo("");
    try {
      const response = await fetch(
        `/admin/contracts/forms/${activeWorkflowFormId}/workflow/delivery`,
        {
          method: "POST",
          headers: {
            ...headers(),
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            delivery_date: workflowDeliveryDate.value,
            delivery_time_window: workflowDeliveryTimeWindow.value || "",
            delivery_contact_name: workflowDeliveryContactName.value || "",
            delivery_contact_phone: workflowDeliveryContactPhone.value || "",
            delivery_notes: workflowDeliveryNotes.value || "",
          }),
        },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udalo sie zapisac terminu dowozu.");
      }
      setInfo(data.message || "Dane dowozu zapisane.");
      await loadData();
      await loadSchedule();
      await openWorkflowModal(activeWorkflowFormId, "delivery");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad zapisu terminu dowozu.");
    } finally {
      if (!workflowModal.hidden) {
        workflowDeliverySaveBtn.disabled = false;
        workflowDeliverySaveBtn.textContent = "Zapisz dowoz";
      }
    }
  };

  const clearWorkflowDelivery = async () => {
    if (!activeWorkflowFormId) {
      setError("Nie wybrano formularza.");
      return;
    }
    const confirmed = window.confirm(
      "Czy na pewno usunac wpis harmonogramu dowozu dla tego formularza?",
    );
    if (!confirmed) {
      return;
    }

    workflowDeliveryClearBtn.disabled = true;
    setError("");
    setInfo("");
    try {
      const response = await fetch(
        `/admin/contracts/forms/${activeWorkflowFormId}/workflow/delivery`,
        {
          method: "DELETE",
          headers: headers(),
        },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udalo sie usunac wpisu dowozu.");
      }
      setInfo(data.message || "Usunieto dane dowozu.");
      await loadData();
      await loadSchedule();
      await openWorkflowModal(activeWorkflowFormId, "delivery");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad usuwania wpisu dowozu.");
    } finally {
      if (!workflowModal.hidden) {
        workflowDeliveryClearBtn.disabled = false;
      }
    }
  };

  const updateWorkflowProformaNote = () => {
    if (!activeWorkflowData) {
      return;
    }
    const workflow = activeWorkflowData.workflow || {};
    const hasProforma = Boolean(workflow.proforma_number);
    const canCreateProforma =
      Boolean(workflow.firebird_client_id) &&
      Number(workflow.devices_selected_count || 0) > 0 &&
      !hasProforma;
    const recipientLabel = workflowProformaBank.checked
      ? "bank GRENKELEASING Sp. z o.o."
      : "klienta z formularza";

    workflowProformaNote.textContent = workflow.proforma_number
      ? `Zapisana proforma: ${workflow.proforma_number}.`
      : canCreateProforma
        ? `Mozesz teraz utworzyc proforme. Odbiorca: ${recipientLabel}.`
        : "Dla tego formularza nie ma jeszcze zapisanej proformy.";
  };

  const activateSection = (sectionName) => {
    navButtons.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.sectionTarget === sectionName);
    });
    panels.forEach((panel) => {
      panel.hidden = panel.dataset.sectionPanel !== sectionName;
    });
    window.history.replaceState(null, "", `#${sectionName}`);
  };

  const shouldIncludeDevices = () => {
    const section = window.location.hash.replace("#", "").trim();
    return section === "devices";
  };

  const currentSection = () => {
    const section = window.location.hash.replace("#", "").trim();
    if (section === "devices" || section === "schedule") {
      return section;
    }
    return "contracts";
  };

  const renderForms = (items) => {
    if (!Array.isArray(items) || items.length === 0) {
      formsBody.innerHTML = "";
      formsEmpty.hidden = false;
      return;
    }

    formsEmpty.hidden = true;
    formsBody.innerHTML = items
      .map((item) => {
        const firebird = item.firebird || {};
        const workflow = item.workflow || {};
        const hasWorkflowActions = item.status === "SUBMITTED";
        const clientActionLabel =
          item.contract_action === "utworz_klienta"
            ? "Utworz klienta"
            : item.contract_action === "podlacz_klienta"
              ? "Podlacz klienta"
              : "Prowadz sprawe";
        const firebirdHtml = item.status === "SUBMITTED"
          ? `
              <span class="flow-badge ${firebird.found ? "ok" : "warn"}">
                ${firebird.found ? "Znaleziony" : "Brak klienta"}
              </span>
              <div class="flow-subtle">ID: ${escapeHtml(firebird.id_klient || "—")}</div>
              ${workflow.exists ? `<div class="flow-subtle">CTIP: ${escapeHtml(workflowStageLabel(workflow.stage))}</div>` : ""}
            `
          : `
              <span class="flow-badge soft">Poza etapem MS</span>
              <div class="flow-subtle">${escapeHtml(item.status_message || "Brak danych do mapowania.")}</div>
            `;

        return `
          <tr>
            <td>${escapeHtml(item.id)}</td>
            <td>
              <span class="flow-badge ${formStatusClass(item.status)}">${escapeHtml(formStatusLabel(item.status))}</span>
              <div class="flow-subtle">${escapeHtml(item.status_message || "")}</div>
              <div class="flow-subtle">Utworzono: ${escapeHtml(formatDate(item.created_at))}</div>
              <div class="flow-subtle">Wypelniono: ${escapeHtml(formatDate(item.submitted_at))}</div>
            </td>
            <td>${escapeHtml(item.customer_name)}</td>
            <td>${escapeHtml(item.customer_nip || "—")}</td>
            <td>${escapeHtml(item.customer_email)}</td>
            <td>${escapeHtml(item.customer_phone)}</td>
            <td>${firebirdHtml}</td>
            <td>
              <div class="flow-action-stack">
                <button
                  type="button"
                  class="flow-action-btn flow-action-btn-secondary"
                  data-detail-form-id="${escapeHtml(item.id)}"
                >
                  Dane
                </button>
                <button
                  type="button"
                  class="flow-action-btn flow-action-btn-danger"
                  data-delete-form-id="${escapeHtml(item.id)}"
                >
                  Usun
                </button>
                ${
                  hasWorkflowActions
                    ? `
                    <button
                      type="button"
                      class="flow-action-btn flow-open-workflow-btn"
                      data-workflow-form-id="${escapeHtml(item.id)}"
                      data-workflow-focus="client"
                    >
                      ${escapeHtml(clientActionLabel)}
                    </button>
                    <button
                      type="button"
                      class="flow-action-btn flow-action-btn-secondary flow-open-workflow-btn"
                      data-workflow-form-id="${escapeHtml(item.id)}"
                      data-workflow-focus="devices"
                    >
                      Dodaj urzadzenie
                    </button>
                  `
                    : '<span class="flow-subtle">Brak akcji na tym etapie</span>'
                }
                ${workflowSummaryLines(workflow)
                  .map((line) => `<span class="flow-subtle">${escapeHtml(line)}</span>`)
                  .join("")}
              </div>
            </td>
          </tr>
        `;
      })
      .join("");
  };

  const renderDevices = (items) => {
    if (!Array.isArray(items) || items.length === 0) {
      devicesBody.innerHTML = "<tr><td colspan='8'>Brak danych urzadzen.</td></tr>";
      return;
    }

    devicesBody.innerHTML = items
      .map((item) => {
        const statusClass = item.found_in_firebird ? "ok" : "warn";
        const statusLabel = item.found_in_firebird ? "Potwierdzone" : "Brak w Menadzerze Serwisu";
        return `
          <tr>
            <td>${escapeHtml(item.row)}</td>
            <td>${escapeHtml(item.serial)}</td>
            <td>${escapeHtml(item.ewidencja)}</td>
            <td>${escapeHtml(item.model)}</td>
            <td><span class="flow-badge ${statusClass}">${statusLabel}</span></td>
            <td>${escapeHtml(item.id_maszyna || "—")}</td>
            <td>${escapeHtml(item.id_klient || "—")}</td>
            <td>
              <button
                type="button"
                class="flow-action-btn"
                data-entity="device"
                data-action="${escapeHtml(item.sync_action || "do_weryfikacji")}"
                data-row="${escapeHtml(item.row || 0)}"
              >
                ${escapeHtml(deviceActionLabel(item.sync_action || "do_weryfikacji"))}
              </button>
            </td>
          </tr>
        `;
      })
      .join("");
  };

  const updateStats = (data) => {
    const totals = data.forms_status_totals || {};
    statNodes.formsTotal.textContent = String(data.forms_total || 0);
    statNodes.formsGenerated.textContent = String(totals.GENERATED || 0);
    statNodes.formsDispatched.textContent = String(totals.DISPATCHED || 0);
    statNodes.formsSubmitted.textContent = String(totals.SUBMITTED || 0);
    statNodes.formsExpired.textContent = String(totals.EXPIRED || 0);
    statNodes.devicesTotal.textContent = String(data.devices_total || 0);
    statNodes.devicesMatched.textContent = String(data.devices_matched || 0);
    statNodes.devicesPending.textContent = String(
      Math.max(0, Number(data.devices_total || 0) - Number(data.devices_matched || 0)),
    );
  };

  const runAction = async (button) => {
    const entity = button.dataset.entity || "";
    const action = button.dataset.action || "";
    const targetIdRaw = button.dataset.targetId || "";
    const rowRaw = button.dataset.row || "";
    if (!entity || !action) {
      setError("Brak danych akcji.");
      return;
    }

    const body = {
      entity,
      action,
      target_id: targetIdRaw ? Number(targetIdRaw) : null,
      row: rowRaw ? Number(rowRaw) : null,
    };

    button.disabled = true;
    const originalText = button.textContent || "Akcja";
    button.textContent = "Trwa...";
    setError("");
    setInfo("");
    try {
      const response = await fetch("/admin/contracts/action", {
        method: "POST",
        headers: {
          ...headers(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się wykonać akcji.");
      }
      setInfo(data.message || "Akcja wykonana.");
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad akcji.");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  };

  const deleteFormRequest = async (button) => {
    const formIdRaw = button.dataset.deleteFormId || "";
    const formId = Number(formIdRaw);
    if (!Number.isFinite(formId) || formId <= 0) {
      setError("Nieprawidlowy identyfikator formularza.");
      return;
    }

    const item = latestForms.find((entry) => Number(entry.id) === formId);
    const customerLabel = item?.customer_name ? `${item.customer_name} (ID ${formId})` : `ID ${formId}`;
    const confirmed = window.confirm(
      `Czy na pewno chcesz usunac formularz ${customerLabel}? Operacja jest nieodwracalna.`,
    );
    if (!confirmed) {
      return;
    }

    button.disabled = true;
    const originalText = button.textContent || "Usun";
    button.textContent = "Usuwanie...";
    setError("");
    setInfo("");
    try {
      const response = await fetch(`/admin/forms/${formId}`, {
        method: "DELETE",
        headers: headers(),
      });
      if (response.status !== 204) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || "Nie udalo sie usunac formularza.");
      }
      if (activeWorkflowFormId && Number(activeWorkflowFormId) === formId) {
        closeWorkflowModal();
      }
      if (!detailModal.hidden && Number(detailId.textContent || 0) === formId) {
        closeDetailModal();
      }
      await loadData();
      setInfo(`Usunieto formularz ID ${formId}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad usuwania formularza.");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  };

  const renderSchedule = (items, dayFrom, dayTo) => {
    latestScheduleItems = Array.isArray(items) ? items : [];
    if (dayFrom) {
      scheduleFromInput.value = dayFrom;
    }
    if (dayTo) {
      scheduleToInput.value = dayTo;
    }

    const fromValue = scheduleFromInput.value;
    const toValue = scheduleToInput.value;
    if (!fromValue || !toValue) {
      scheduleBoard.innerHTML = "";
      scheduleEmpty.hidden = false;
      return;
    }

    const fromDate = new Date(`${fromValue}T00:00:00`);
    const toDate = new Date(`${toValue}T00:00:00`);
    if (Number.isNaN(fromDate.getTime()) || Number.isNaN(toDate.getTime()) || toDate < fromDate) {
      scheduleBoard.innerHTML = "";
      scheduleEmpty.hidden = false;
      return;
    }

    const grouped = new Map();
    latestScheduleItems.forEach((item) => {
      const key = String(item.delivery_date || "");
      if (!key) {
        return;
      }
      if (!grouped.has(key)) {
        grouped.set(key, []);
      }
      grouped.get(key).push(item);
    });

    const days = [];
    const cursor = new Date(fromDate.getTime());
    while (cursor <= toDate && days.length < 31) {
      days.push(toIsoDate(cursor));
      cursor.setDate(cursor.getDate() + 1);
    }

    scheduleBoard.innerHTML = days
      .map((day) => {
        const entries = grouped.get(day) || [];
        const cards = entries.length
          ? entries
              .map(
                (entry) => `
                  <article class="flow-schedule-card">
                    <strong>${escapeHtml(entry.customer_name || "Klient")}</strong>
                    <div class="flow-schedule-meta">
                      <span class="flow-subtle">Formularz: ${escapeHtml(entry.form_request_id || "—")}</span>
                      <span class="flow-subtle">Dowoz: ${escapeHtml(entry.delivery_label || day)}</span>
                      <span class="flow-subtle">Kontakt: ${escapeHtml(entry.delivery_contact_name || "—")} / ${escapeHtml(entry.delivery_contact_phone || "—")}</span>
                      <span class="flow-subtle">Notatka: ${escapeHtml(entry.delivery_notes || "—")}</span>
                    </div>
                    <div class="flow-schedule-actions">
                      <button
                        type="button"
                        class="flow-action-btn flow-action-btn-secondary"
                        data-schedule-action="open-workflow"
                        data-schedule-form-id="${escapeHtml(entry.form_request_id || 0)}"
                      >
                        Edytuj
                      </button>
                      <button
                        type="button"
                        class="flow-action-btn flow-action-btn-secondary"
                        data-schedule-action="move-pick"
                        data-schedule-case-id="${escapeHtml(entry.workflow_case_id || 0)}"
                        data-schedule-date="${escapeHtml(entry.delivery_date || day)}"
                      >
                        Przenies...
                      </button>
                      <button
                        type="button"
                        class="flow-action-btn flow-action-btn-secondary"
                        data-schedule-action="move-prev"
                        data-schedule-case-id="${escapeHtml(entry.workflow_case_id || 0)}"
                        data-schedule-date="${escapeHtml(entry.delivery_date || day)}"
                      >
                        -1 dzien
                      </button>
                      <button
                        type="button"
                        class="flow-action-btn flow-action-btn-secondary"
                        data-schedule-action="move-next"
                        data-schedule-case-id="${escapeHtml(entry.workflow_case_id || 0)}"
                        data-schedule-date="${escapeHtml(entry.delivery_date || day)}"
                      >
                        +1 dzien
                      </button>
                      <button
                        type="button"
                        class="flow-action-btn flow-action-btn-danger"
                        data-schedule-action="delete"
                        data-schedule-case-id="${escapeHtml(entry.workflow_case_id || 0)}"
                      >
                        Usun
                      </button>
                    </div>
                  </article>
                `,
              )
              .join("")
          : "<p class='flow-note'>Brak wpisow.</p>";
        return `
          <section class="flow-schedule-day">
            <h4>${escapeHtml(formatDateOnly(day))}</h4>
            <div class="flow-schedule-items">${cards}</div>
          </section>
        `;
      })
      .join("");

    scheduleEmpty.hidden = latestScheduleItems.length > 0;
  };

  const loadSchedule = async () => {
    if (!scheduleFromInput.value || !scheduleToInput.value) {
      const today = toIsoDate(new Date());
      scheduleFromInput.value = today;
      scheduleToInput.value = shiftIsoDate(today, 6);
    }

    scheduleLoadBtn.disabled = true;
    scheduleLoadBtn.textContent = "Ladowanie...";
    setError("");
    try {
      const params = new URLSearchParams({
        day_from: scheduleFromInput.value,
        day_to: scheduleToInput.value,
      });
      const response = await fetch(`/admin/contracts/delivery/schedule?${params.toString()}`, {
        headers: headers(),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udalo sie pobrac harmonogramu dowozow.");
      }
      renderSchedule(data.items || [], data.day_from, data.day_to);
    } catch (err) {
      scheduleBoard.innerHTML = "";
      scheduleEmpty.hidden = false;
      setError(err instanceof Error ? err.message : "Blad ladowania harmonogramu.");
    } finally {
      scheduleLoadBtn.disabled = false;
      scheduleLoadBtn.textContent = "Odswiez harmonogram";
    }
  };

  const moveScheduleEntry = async (workflowCaseId, currentDate, diffDays) => {
    const caseId = Number(workflowCaseId);
    if (!caseId || !currentDate) {
      setError("Brak danych wpisu harmonogramu.");
      return;
    }
    const targetDate = shiftIsoDate(currentDate, diffDays);
    setError("");
    setInfo("");
    try {
      const response = await fetch(`/admin/contracts/delivery/${caseId}/move`, {
        method: "POST",
        headers: {
          ...headers(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ delivery_date: targetDate }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udalo sie przeniesc wpisu.");
      }
      setInfo(data.message || "Przeniesiono wpis harmonogramu.");
      await loadSchedule();
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad przenoszenia wpisu.");
    }
  };

  const moveScheduleEntryToDate = async (workflowCaseId, currentDate) => {
    const caseId = Number(workflowCaseId);
    if (!caseId) {
      setError("Brak identyfikatora wpisu harmonogramu.");
      return;
    }
    const defaultDate = currentDate || toIsoDate(new Date());
    const nextDateRaw = window.prompt(
      "Podaj nowa date dowozu (RRRR-MM-DD):",
      defaultDate,
    );
    if (nextDateRaw === null) {
      return;
    }
    const nextDate = String(nextDateRaw || "").trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(nextDate)) {
      setError("Nieprawidlowy format daty. Uzyj RRRR-MM-DD.");
      return;
    }

    setError("");
    setInfo("");
    try {
      const response = await fetch(`/admin/contracts/delivery/${caseId}/move`, {
        method: "POST",
        headers: {
          ...headers(),
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ delivery_date: nextDate }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udalo sie przeniesc wpisu.");
      }
      setInfo(data.message || "Przeniesiono wpis harmonogramu.");
      await loadSchedule();
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad przenoszenia wpisu.");
    }
  };

  const deleteScheduleEntry = async (workflowCaseId) => {
    const caseId = Number(workflowCaseId);
    if (!caseId) {
      setError("Brak identyfikatora wpisu harmonogramu.");
      return;
    }
    const confirmed = window.confirm("Czy na pewno usunac wpis harmonogramu dowozu?");
    if (!confirmed) {
      return;
    }
    setError("");
    setInfo("");
    try {
      const response = await fetch(`/admin/contracts/delivery/${caseId}`, {
        method: "DELETE",
        headers: headers(),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udalo sie usunac wpisu harmonogramu.");
      }
      setInfo(data.message || "Usunieto wpis harmonogramu.");
      await loadSchedule();
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad usuwania wpisu harmonogramu.");
    }
  };

  const loadData = async ({ includeDevices } = {}) => {
    setError("");
    setBusy(true);
    try {
      const includeDevicesFlag = (
        typeof includeDevices === "boolean" ? includeDevices : shouldIncludeDevices()
      );
      const me = await ensureCurrentUser();
      const sections = new Set(Array.isArray(me.sections) ? me.sections : []);
      if (!sections.has("generator")) {
        throw new Error("Brak uprawnien do sekcji FLOW.");
      }
      const displayName = [me.first_name, me.last_name].filter(Boolean).join(" ").trim();
      userChip.textContent = displayName || me.email || "Uzytkownik";

      const response = await fetch(
        `/admin/contracts/dashboard?forms_scope=all&include_devices=${includeDevicesFlag ? "1" : "0"}`,
        {
          headers: headers(),
        },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        if (response.status === 401) {
          throw new Error("Sesja wygasla.");
        }
        throw new Error(data.detail || "Nie udalo sie pobrac danych FLOW.");
      }

      latestForms = Array.isArray(data.forms) ? data.forms : [];
      updateStats(data);
      renderForms(latestForms);
      renderDevices(includeDevicesFlag ? data.devices || [] : []);
      setInfo(`Dane FLOW odswiezone: ${formatDate(new Date().toISOString())}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Blad ladowania danych.";
      setError(message);
      if (message.includes("Sesja")) {
        token = null;
        currentUser = null;
        clearFlowToken();
        window.location.replace("/");
      }
    } finally {
      setBusy(false);
    }
  };

  refreshBtn.addEventListener("click", () => {
    const sectionName = currentSection();
    loadData({ includeDevices: sectionName === "devices" });
    if (sectionName === "schedule") {
      loadSchedule();
    }
  });

  navButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const sectionName = button.dataset.sectionTarget || "contracts";
      activateSection(sectionName);
      loadData({ includeDevices: sectionName === "devices" });
      if (sectionName === "schedule") {
        loadSchedule();
      }
    });
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const workflowButton = target.closest("[data-workflow-form-id]");
    if (workflowButton instanceof HTMLButtonElement) {
      openWorkflowModal(
        workflowButton.dataset.workflowFormId || "",
        workflowButton.dataset.workflowFocus || "client",
      );
      return;
    }
    const detailButton = target.closest("[data-detail-form-id]");
    if (detailButton instanceof HTMLButtonElement) {
      openDetailModal(detailButton.dataset.detailFormId || "");
      return;
    }
    const deleteButton = target.closest("[data-delete-form-id]");
    if (deleteButton instanceof HTMLButtonElement) {
      deleteFormRequest(deleteButton);
      return;
    }
    const scheduleActionButton = target.closest("[data-schedule-action]");
    if (scheduleActionButton instanceof HTMLButtonElement) {
      const actionName = scheduleActionButton.dataset.scheduleAction || "";
      if (actionName === "open-workflow") {
        openWorkflowModal(scheduleActionButton.dataset.scheduleFormId || "", "delivery");
        return;
      }
      if (actionName === "move-prev") {
        moveScheduleEntry(
          scheduleActionButton.dataset.scheduleCaseId || "",
          scheduleActionButton.dataset.scheduleDate || "",
          -1,
        );
        return;
      }
      if (actionName === "move-pick") {
        moveScheduleEntryToDate(
          scheduleActionButton.dataset.scheduleCaseId || "",
          scheduleActionButton.dataset.scheduleDate || "",
        );
        return;
      }
      if (actionName === "move-next") {
        moveScheduleEntry(
          scheduleActionButton.dataset.scheduleCaseId || "",
          scheduleActionButton.dataset.scheduleDate || "",
          1,
        );
        return;
      }
      if (actionName === "delete") {
        deleteScheduleEntry(scheduleActionButton.dataset.scheduleCaseId || "");
      }
      return;
    }
    const copyButton = target.closest(".flow-copy-btn");
    if (copyButton instanceof HTMLButtonElement) {
      copyText(copyButton.dataset.copyValue || "")
        .then(() => {
          setInfo("Skopiowano wartosc pola.");
        })
        .catch((err) => {
          setError(err instanceof Error ? err.message : "Nie udalo sie skopiowac wartosci.");
        });
      return;
    }
    if (target.matches("[data-flow-modal-close]")) {
      closeDetailModal();
      return;
    }
    if (target.matches("[data-flow-workflow-close]")) {
      closeWorkflowModal();
      return;
    }
    const actionButton = target.closest(".flow-action-btn");
    if (!(actionButton instanceof HTMLButtonElement)) {
      return;
    }
    if (actionButton.classList.contains("flow-open-workflow-btn")) {
      return;
    }
    if (!actionButton.dataset.entity && !actionButton.dataset.action) {
      return;
    }
    runAction(actionButton);
  });

  document.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (target instanceof HTMLInputElement && target.matches("[data-workflow-device-row]")) {
      if (!activeWorkflowData?.available_devices) {
        return;
      }
      const row = Number(target.dataset.workflowDeviceRow || 0);
      activeWorkflowData.available_devices = activeWorkflowData.available_devices.map((item) =>
        Number(item.row) === row ? { ...item, selected: target.checked } : item,
      );
      updateWorkflowSelectionSummary();
      syncSalesPacketDevicesFromSelection();
      return;
    }
    if (
      target === workflowDeviceStatusFilter ||
      target === workflowDeviceReservationFilter
    ) {
      renderWorkflowDevicePicker();
    }
  });

  detailCloseBtn.addEventListener("click", () => {
    closeDetailModal();
  });

  workflowCloseBtn.addEventListener("click", () => {
    closeWorkflowModal();
  });

  workflowClientSaveBtn.addEventListener("click", () => {
    saveWorkflowClient();
  });

  workflowOpenDevicesBtn.addEventListener("click", () => {
    workflowDeviceSearch.focus();
    workflowDeviceSearch.scrollIntoView({ block: "center", behavior: "smooth" });
  });

  workflowDevicesSaveBtn.addEventListener("click", () => {
    saveWorkflowDevices();
  });

  workflowProformaCreateBtn.addEventListener("click", () => {
    saveWorkflowProforma();
  });

  workflowDeliverySaveBtn.addEventListener("click", () => {
    saveWorkflowDelivery();
  });

  workflowDeliveryClearBtn.addEventListener("click", () => {
    clearWorkflowDelivery();
  });

  workflowProformaBank.addEventListener("change", () => {
    updateWorkflowProformaNote();
  });

  workflowStatusSaveBtn.addEventListener("click", () => {
    saveWorkflowStatus();
  });

  workflowDeviceSearch.addEventListener("input", () => {
    renderWorkflowDevicePicker();
  });

  scheduleLoadBtn.addEventListener("click", () => {
    loadSchedule();
  });

  document.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || !activeWorkflowData?.available_devices) {
      return;
    }

    if (target.dataset.workflowDevicePriceNet) {
      const row = Number(target.dataset.workflowDevicePriceNet || 0);
      activeWorkflowData.available_devices = activeWorkflowData.available_devices.map((item) => {
        if (Number(item.row) !== row) {
          return item;
        }
        const vatRate = Number(item.vat_rate || 23);
        return {
          ...item,
          price_net: target.value,
          price_gross: netToGross(target.value, vatRate),
        };
      });
      renderWorkflowDevicePicker();
      updateWorkflowSelectionSummary();
      syncSalesPacketDevicesFromSelection();
      return;
    }

    if (target.dataset.workflowDevicePriceGross) {
      const row = Number(target.dataset.workflowDevicePriceGross || 0);
      activeWorkflowData.available_devices = activeWorkflowData.available_devices.map((item) => {
        if (Number(item.row) !== row) {
          return item;
        }
        const vatRate = Number(item.vat_rate || 23);
        return {
          ...item,
          price_gross: target.value,
          price_net: grossToNet(target.value, vatRate),
        };
      });
      renderWorkflowDevicePicker();
      updateWorkflowSelectionSummary();
      syncSalesPacketDevicesFromSelection();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !detailModal.hidden) {
      closeDetailModal();
      return;
    }
    if (event.key === "Escape" && !workflowModal.hidden) {
      closeWorkflowModal();
    }
  });

  logoutBtn.addEventListener("click", async () => {
    try {
      await fetch("/auth/logout", {
        method: "POST",
        headers: headers(),
      });
    } catch (err) {
      console.error(err);
    } finally {
      token = null;
      currentUser = null;
      clearFlowToken();
      window.location.replace("/");
    }
  });

  const initialSection = resolveInitialSection();
  const today = toIsoDate(new Date());
  scheduleFromInput.value = today;
  scheduleToInput.value = shiftIsoDate(today, 6);
  activateSection(initialSection);
  loadData({ includeDevices: initialSection === "devices" });
  if (initialSection === "schedule") {
    loadSchedule();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  initializeFlowPage();
});
