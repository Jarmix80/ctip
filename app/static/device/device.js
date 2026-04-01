const DEVICE_TOKEN_KEY = "admin-session-token";

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
  } catch (_err) {
    return value;
  }
}

function formatValue(value) {
  const normalized = String(value ?? "").trim();
  return normalized || "—";
}

function issueText(issue) {
  const severityMap = {
    critical: "Krytyczne",
    warn: "Do poprawy",
    info: "Uwaga",
  };
  const prefix = severityMap[issue?.severity] || "Info";
  return `${prefix}: ${issue?.message || ""}`;
}

function renderProcessStatus(status) {
  const severity = status?.severity || "info";
  const label = escapeHtml(status?.label || "Weryfikacja");
  const detail = escapeHtml(status?.detail || "");
  return `
    <div class="device-status-wrap">
      <span class="device-status-badge ${escapeHtml(severity)}">${label}</span>
      ${detail ? `<div class="device-status-detail">${detail}</div>` : ""}
    </div>
  `;
}

function renderCellStack(rows) {
  return `
    <div class="device-cell-stack">
      ${rows
        .map((row) => {
          const label = escapeHtml(row.label || "");
          const value = escapeHtml(formatValue(row.value));
          const extraClass = row.strong ? " device-cell-strong" : "";
          return `
            <div>
              <div class="device-cell-label">${label}</div>
              <div class="${`device-cell-value${extraClass}`}">${value}</div>
            </div>
          `;
        })
        .join("")}
    </div>
  `;
}

function renderIssueSummary(item) {
  const issues = Array.isArray(item?.issues) ? item.issues : [];
  if (issues.length === 0) {
    return '<span class="flow-badge ok">Gotowe</span>';
  }
  return `
    <div class="device-issues">
      ${issues
        .map(
          (issue) => `
            <span class="device-issue ${escapeHtml(issue.severity || "info")}" title="${escapeHtml(
              issueText(issue)
            )}">
              ${escapeHtml(issue.message || issue.code || "Problem")}
            </span>
          `
        )
      .join("")}
    </div>
  `;
}

function renderActions(item) {
  const actions = Array.isArray(item?.next_actions) ? item.next_actions : [];
  const recommended = item?.internal_number?.recommended || "";
  const source = item?.internal_number?.source || "";
  const inconsistent = item?.internal_number?.consistent === false;

  return `
    <div class="device-actions">
      ${
        recommended
          ? `
            <div class="device-action-lead${inconsistent ? " mismatch" : ""}">
              Nr wew docelowy: <strong>${escapeHtml(recommended)}</strong>
              ${source ? ` z ${escapeHtml(source)}` : ""}
            </div>
          `
          : ""
      }
      ${
        actions.length
          ? `
            <ul class="device-action-list">
              ${actions.map((action) => `<li>${escapeHtml(action)}</li>`).join("")}
            </ul>
          `
          : ""
      }
    </div>
  `;
}

async function initializeDevicePage() {
  const token = readDeviceToken();
  if (!token) {
    window.location.replace("/");
    return;
  }

  const refreshBtn = document.getElementById("device-refresh");
  const logoutBtn = document.getElementById("device-logout");
  const userChip = document.getElementById("device-user-chip");
  const errorBox = document.getElementById("device-error");
  const infoBox = document.getElementById("device-info");
  const summaryRows = document.getElementById("device-summary-rows");
  const summarySerial = document.getElementById("device-summary-serial");
  const summaryMachines = document.getElementById("device-summary-machines");
  const summaryCritical = document.getElementById("device-summary-critical");
  const summaryReady = document.getElementById("device-summary-ready");
  const intakesBody = document.getElementById("device-intakes-body");
  const modelDuplicates = document.getElementById("device-model-duplicates");
  const modelMissingKind = document.getElementById("device-model-missing-kind");
  const modelMissingColor = document.getElementById("device-model-missing-color");
  const modelMissingImage = document.getElementById("device-model-missing-image");
  const modelDuplicatesBody = document.getElementById("device-model-duplicates-body");
  const operationalNotes = document.getElementById("device-operational-notes");
  const processRules = document.getElementById("device-process-rules");
  const catalogForm = document.getElementById("device-catalog-form");
  const catalogModelSelect = document.getElementById("device-catalog-model-select");
  const catalogOnlyMissingInput = document.getElementById("device-catalog-only-missing");
  const catalogSyncBtn = document.getElementById("device-catalog-sync-btn");
  const catalogSyncAllBtn = document.getElementById("device-catalog-sync-all-btn");
  const modelCreateMarkaInput = document.getElementById("device-model-create-marka");
  const modelCreateModelInput = document.getElementById("device-model-create-model");
  const modelCreateGrupaInput = document.getElementById("device-model-create-grupa");
  const modelCreateRodzajInput = document.getElementById("device-model-create-rodzaj");
  const modelCreatePlikInput = document.getElementById("device-model-create-plik");
  const modelCreateKolorInput = document.getElementById("device-model-create-kolor");
  const modelCreateSyncCatalogInput = document.getElementById("device-model-create-sync-catalog");
  const modelCreateBtn = document.getElementById("device-model-create-btn");
  const intakeForm = document.getElementById("device-intake-form");
  const intakeEwidPrefixInput = document.getElementById("device-intake-ewid-prefix");
  const intakeEwidNextNumberInput = document.getElementById("device-intake-ewid-next-number");
  const supplierInput = document.getElementById("device-supplier-input");
  const supplierOptions = document.getElementById("device-supplier-options");
  const supplierCreateNameInput = document.getElementById("device-supplier-create-name");
  const supplierCreateNipInput = document.getElementById("device-supplier-create-nip");
  const supplierCreateAddressInput = document.getElementById("device-supplier-create-address");
  const supplierCreatePostalInput = document.getElementById("device-supplier-create-postal");
  const supplierCreateCityInput = document.getElementById("device-supplier-create-city");
  const supplierCreatePhoneInput = document.getElementById("device-supplier-create-phone");
  const supplierCreateEmailInput = document.getElementById("device-supplier-create-email");
  const supplierCreateBtn = document.getElementById("device-supplier-create-btn");
  const supplierStatusBox = document.getElementById("device-supplier-status");
  const modelInput = document.getElementById("device-model-input");
  const modelOptions = document.getElementById("device-model-options");
  const intakeDocExternalInput = document.getElementById("device-intake-doc-external");
  const intakeIssuedByInput = document.getElementById("device-intake-issued-by");
  const intakeAddQuantityInput = document.getElementById("device-intake-add-quantity");
  const intakeAddPriceNettoInput = document.getElementById("device-intake-add-price-netto");
  const intakeAddItemsBtn = document.getElementById("device-intake-add-items-btn");
  const intakeClearBtn = document.getElementById("device-intake-clear-btn");
  const intakeItemsBody = document.getElementById("device-intake-items-body");
  const intakeForceInput = document.getElementById("device-intake-force");
  const intakeCreateBtn = document.getElementById("device-intake-create-btn");
  const intakeSubmitStatusBox = document.getElementById("device-intake-submit-status");
  const intakeResultBox = document.getElementById("device-intake-result");
  const intakeResultHead = document.getElementById("device-intake-result-head");
  const intakeResultList = document.getElementById("device-intake-result-list");

  const requiredElements = [
    ["device-refresh", refreshBtn],
    ["device-logout", logoutBtn],
    ["device-user-chip", userChip],
    ["device-error", errorBox],
    ["device-info", infoBox],
    ["device-summary-rows", summaryRows],
    ["device-summary-serial", summarySerial],
    ["device-summary-machines", summaryMachines],
    ["device-summary-critical", summaryCritical],
    ["device-summary-ready", summaryReady],
    ["device-intakes-body", intakesBody],
    ["device-model-duplicates", modelDuplicates],
    ["device-model-missing-kind", modelMissingKind],
    ["device-model-missing-color", modelMissingColor],
    ["device-model-missing-image", modelMissingImage],
    ["device-model-duplicates-body", modelDuplicatesBody],
    ["device-operational-notes", operationalNotes],
    ["device-process-rules", processRules],
    ["device-catalog-form", catalogForm],
    ["device-catalog-model-select", catalogModelSelect],
    ["device-catalog-only-missing", catalogOnlyMissingInput],
    ["device-catalog-sync-btn", catalogSyncBtn],
    ["device-catalog-sync-all-btn", catalogSyncAllBtn],
    ["device-model-create-marka", modelCreateMarkaInput],
    ["device-model-create-model", modelCreateModelInput],
    ["device-model-create-grupa", modelCreateGrupaInput],
    ["device-model-create-rodzaj", modelCreateRodzajInput],
    ["device-model-create-plik", modelCreatePlikInput],
    ["device-model-create-kolor", modelCreateKolorInput],
    ["device-model-create-sync-catalog", modelCreateSyncCatalogInput],
    ["device-model-create-btn", modelCreateBtn],
    ["device-intake-form", intakeForm],
    ["device-intake-ewid-prefix", intakeEwidPrefixInput],
    ["device-intake-ewid-next-number", intakeEwidNextNumberInput],
    ["device-supplier-input", supplierInput],
    ["device-supplier-options", supplierOptions],
    ["device-supplier-create-name", supplierCreateNameInput],
    ["device-supplier-create-nip", supplierCreateNipInput],
    ["device-supplier-create-address", supplierCreateAddressInput],
    ["device-supplier-create-postal", supplierCreatePostalInput],
    ["device-supplier-create-city", supplierCreateCityInput],
    ["device-supplier-create-phone", supplierCreatePhoneInput],
    ["device-supplier-create-email", supplierCreateEmailInput],
    ["device-supplier-create-btn", supplierCreateBtn],
    ["device-supplier-status", supplierStatusBox],
    ["device-model-input", modelInput],
    ["device-model-options", modelOptions],
    ["device-intake-doc-external", intakeDocExternalInput],
    ["device-intake-issued-by", intakeIssuedByInput],
    ["device-intake-add-quantity", intakeAddQuantityInput],
    ["device-intake-add-price-netto", intakeAddPriceNettoInput],
    ["device-intake-add-items-btn", intakeAddItemsBtn],
    ["device-intake-clear-btn", intakeClearBtn],
    ["device-intake-items-body", intakeItemsBody],
    ["device-intake-force", intakeForceInput],
    ["device-intake-create-btn", intakeCreateBtn],
    ["device-intake-submit-status", intakeSubmitStatusBox],
    ["device-intake-result", intakeResultBox],
    ["device-intake-result-head", intakeResultHead],
    ["device-intake-result-list", intakeResultList],
  ];
  const missingElements = requiredElements.filter(([, element]) => !element).map(([id]) => id);
  if (missingElements.length) {
    console.error("Brak wymaganych elementow UI DEVICE:", missingElements);
    if (errorBox) {
      errorBox.textContent = `Blad UI DEVICE. Brak elementow: ${missingElements.join(", ")}`;
      errorBox.hidden = false;
    }
    return;
  }

  const headers = () => ({
    "X-Admin-Session": token,
  });

  const DEFAULT_TIMEOUT_MS = 20000;
  const LONG_TIMEOUT_MS = 120000;
  const DEVICE_EWIDENCJA_BASE_PREFIX = "KP/";

  const buildTimeoutMessage = (timeoutMs) => {
    const seconds = Math.max(1, Math.round(Number(timeoutMs) / 1000));
    return `Przekroczono limit czasu odpowiedzi (${seconds}s). Sprobuj ponownie.`;
  };

  const fetchJson = async (
    url,
    {
      method = "GET",
      extraHeaders = null,
      body = null,
      timeoutMs = DEFAULT_TIMEOUT_MS,
      defaultError = "Nie udalo sie wykonac operacji.",
    } = {}
  ) => {
    const abortController = new AbortController();
    const timeoutHandle = window.setTimeout(() => abortController.abort(), timeoutMs);
    try {
      const response = await fetch(url, {
        method,
        headers: {
          ...headers(),
          ...(extraHeaders || {}),
        },
        body,
        signal: abortController.signal,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(payload.detail || defaultError);
      }
      return payload;
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") {
        throw new Error(buildTimeoutMessage(timeoutMs));
      }
      if (err instanceof Error) {
        throw err;
      }
      throw new Error(defaultError);
    } finally {
      window.clearTimeout(timeoutHandle);
    }
  };

  const setError = (message = "") => {
    errorBox.textContent = message;
    errorBox.hidden = !message;
  };

  const setInfo = (message = "") => {
    infoBox.textContent = message;
    infoBox.hidden = !message;
  };

  const setBusy = (busy) => {
    refreshBtn.disabled = busy;
    refreshBtn.textContent = busy ? "Odswiezanie..." : "Odswiez dane";
  };

  const setActionBusy = (button, busy, busyLabel, defaultLabel) => {
    button.disabled = busy;
    button.textContent = busy ? busyLabel : defaultLabel;
  };

  const setInlineStatus = (element, message = "", kind = "info") => {
    const kinds = new Set(["info", "success", "warn", "error"]);
    element.textContent = message;
    element.hidden = !message;
    element.classList.remove("info", "success", "warn", "error");
    if (message) {
      element.classList.add(kinds.has(kind) ? kind : "info");
    }
  };

  const setSupplierStatus = (message = "", kind = "info") =>
    setInlineStatus(supplierStatusBox, message, kind);

  const setIntakeSubmitStatus = (message = "", kind = "info") =>
    setInlineStatus(intakeSubmitStatusBox, message, kind);

  const clearIntakeResult = () => {
    intakeResultHead.textContent = "";
    intakeResultList.innerHTML = "";
    intakeResultBox.hidden = true;
  };

  const renderIntakeResult = ({ batch, modelLabels }) => {
    const pzNumber = String(batch?.pz_number || "—");
    const pzId = Number(batch?.pz_id || 0);
    const rows = Array.isArray(batch?.items) ? batch.items : [];
    const createdAt = formatDate(new Date().toISOString());

    intakeResultHead.textContent = `Dokument ${pzNumber} (ID ${pzId}) zapisany. Pozycji: ${rows.length}. Czas: ${createdAt}.`;
    intakeResultList.innerHTML = "";

    const previewLimit = 6;
    rows.slice(0, previewLimit).forEach((item, index) => {
      const modelId = Number(item?.model_id || 0);
      const modelLabel = modelLabels.get(modelId) || `ID ${modelId}`;
      const serial = String(item?.serial || "").trim() || "—";
      const ewidencja = String(item?.ewidencja || "").trim() || "—";
      const li = document.createElement("li");
      li.textContent = `${index + 1}. ${modelLabel} | S/N: ${serial} | KP: ${ewidencja}`;
      intakeResultList.appendChild(li);
    });

    if (rows.length > previewLimit) {
      const li = document.createElement("li");
      li.textContent = `... oraz ${rows.length - previewLimit} kolejnych pozycji.`;
      intakeResultList.appendChild(li);
    }

    intakeResultBox.hidden = false;
  };

  const updateSummary = (summary) => {
    summaryRows.textContent = String(Number(summary?.device_rows || 0));
    summarySerial.textContent = String(Number(summary?.serial_linked_rows || 0));
    summaryMachines.textContent = String(Number(summary?.machine_linked_rows || 0));
    summaryCritical.textContent = String(Number(summary?.critical_rows || 0));
    summaryReady.textContent = String(Number(summary?.ready_rows || 0));
  };

  const renderIntakes = (items) => {
    if (!Array.isArray(items) || items.length === 0) {
      intakesBody.innerHTML = "<tr><td colspan='8'>Brak danych przyjec urzadzen.</td></tr>";
      return;
    }

    intakesBody.innerHTML = items
      .map((item) => {
        const supplierName = item?.supplier?.nazwa || item?.supplier?.id_klient || "—";
        const supplierNip = item?.supplier?.nip || "";
        return `
          <tr>
            <td>
              ${renderCellStack([
                { label: "Numer", value: item.pz_number, strong: true },
                { label: "Data", value: formatDate(item.pz_date) },
                { label: "Dok. zew.", value: item.external_document_number || "—" },
              ])}
            </td>
            <td>
              ${renderCellStack([
                { label: "Dostawca", value: supplierName, strong: true },
                { label: "NIP", value: supplierNip || "—" },
                { label: "Przyjal", value: item.issued_by || "—" },
              ])}
            </td>
            <td>
              ${renderCellStack([
                { label: "Nazwa", value: item.purchase?.name, strong: true },
                { label: "Serial z PZ", value: item.purchase?.serial || "—" },
                { label: "Nr wew z PZ", value: item.purchase?.ewidencja || "—" },
              ])}
            </td>
            <td>
              ${renderCellStack([
                { label: "ID", value: item.warehouse?.id_magazyn_table || "—", strong: true },
                { label: "INDEKS", value: item.warehouse?.index || "—" },
                { label: "ID_MODEL", value: item.warehouse?.id_model || "—" },
              ])}
            </td>
            <td>
              ${renderCellStack([
                { label: "ID_SERIAL", value: item.serial?.id_serial || "—", strong: true },
                { label: "Serial", value: item.serial?.serial || "—" },
                { label: "Nr wew", value: item.serial?.ewidencja || "—" },
              ])}
            </td>
            <td>
              ${renderCellStack([
                { label: "ID_MASZYNA", value: item.machine?.id_maszyna || "—", strong: true },
                { label: "EWIDENCJA", value: item.machine?.ewidencja || "—" },
                { label: "SYNWP", value: item.machine?.synwp ?? "—" },
              ])}
            </td>
            <td>
              ${renderCellStack([
                { label: "Model", value: item.model?.model || item.warehouse?.model || "—", strong: true },
                { label: "RODZAJ", value: item.model?.rodzaj || "—" },
                { label: "PLIK", value: item.model?.plik || "—" },
              ])}
            </td>
            <td>
              <div class="device-process-cell">
                ${renderProcessStatus(item.process_status)}
                ${renderActions(item)}
                ${renderIssueSummary(item)}
              </div>
            </td>
          </tr>
        `;
      })
      .join("");
  };

  const renderModelQuality = (quality) => {
    modelDuplicates.textContent = String(Number(quality?.duplicate_signatures_count || 0));
    modelMissingKind.textContent = String(Number(quality?.missing_rodzaj_count || 0));
    modelMissingColor.textContent = String(Number(quality?.missing_kolor_count || 0));
    modelMissingImage.textContent = String(Number(quality?.missing_plik_count || 0));

    const duplicates = Array.isArray(quality?.top_duplicate_signatures)
      ? quality.top_duplicate_signatures
      : [];
    if (duplicates.length === 0) {
      modelDuplicatesBody.innerHTML = "<tr><td colspan='4'>Brak duplikatow modelu.</td></tr>";
      return;
    }

    modelDuplicatesBody.innerHTML = duplicates
      .map(
        (item) => `
          <tr>
            <td>${escapeHtml(item.marka || "—")}</td>
            <td>${escapeHtml(item.model || "—")}</td>
            <td>${escapeHtml(item.count || 0)}</td>
            <td>${escapeHtml((item.id_models || []).join(", ") || "—")}</td>
          </tr>
        `
      )
      .join("");
  };

  const renderOperationalNotes = (items) => {
    if (!Array.isArray(items) || items.length === 0) {
      operationalNotes.innerHTML = "<li>Brak notatek operacyjnych.</li>";
      return;
    }
    operationalNotes.innerHTML = items
      .map((item) => `<li>${escapeHtml(item)}</li>`)
      .join("");
  };

  const renderProcessRules = (items) => {
    if (!Array.isArray(items) || items.length === 0) {
      processRules.innerHTML = "<li>Brak zdefiniowanych regul procesu.</li>";
      return;
    }
    processRules.innerHTML = items.map((item) => `<li>${escapeHtml(item)}</li>`).join("");
  };

  const intakeItems = [];
  let availableModels = [];
  let catalogModels = [];
  let availableSuppliers = [];

  const normalizeDeviceKey = (value) =>
    String(value || "")
      .toUpperCase()
      .replace(/[^A-Z0-9]/g, "");

  const normalizeEwidPrefix = (value) => {
    const trimmed = String(value || "")
      .trim()
      .toUpperCase();
    if (!trimmed) {
      return "KP/";
    }
    return trimmed.endsWith("/") ? trimmed : `${trimmed}/`;
  };

  const parsePositiveInt = (value, errorMessage) => {
    const number = Number(String(value || "").trim());
    if (!Number.isInteger(number) || number <= 0) {
      throw new Error(errorMessage);
    }
    return number;
  };

  const formatEwidencjaBase = (prefix, number, width = 4) =>
    `${normalizeEwidPrefix(prefix)}${String(number).padStart(Math.max(1, width), "0")}/`;

  const splitEwidencjaBase = (value) => {
    const match = String(value || "")
      .trim()
      .toUpperCase()
      .match(/^(.*?)(\d+)\/?$/);
    if (!match) {
      return null;
    }
    const prefixRaw = String(match[1] || "");
    const parsedNumber = Number(match[2]);
    if (!Number.isInteger(parsedNumber) || parsedNumber <= 0) {
      return null;
    }
    return {
      prefix: normalizeEwidPrefix(prefixRaw),
      number: parsedNumber,
      width: String(match[2]).length,
    };
  };

  const buildFullEwidencja = (baseValue, suffixValue) => {
    const parsed = splitEwidencjaBase(baseValue);
    if (!parsed) {
      return "";
    }
    const normalizedBase = formatEwidencjaBase(parsed.prefix, parsed.number, parsed.width);
    const suffix = String(suffixValue || "")
      .trim()
      .replace(/^\/+/, "");
    if (!suffix) {
      return normalizedBase;
    }
    return `${normalizedBase}${suffix}`;
  };

  const isDeviceBasePrefix = (value) =>
    normalizeEwidPrefix(value) === normalizeEwidPrefix(DEVICE_EWIDENCJA_BASE_PREFIX);

  const parseNonNegativeDecimal = (value, errorMessage) => {
    const normalized = String(value || "").trim().replace(",", ".");
    if (!normalized) {
      return null;
    }
    const number = Number(normalized);
    if (!Number.isFinite(number) || number < 0) {
      throw new Error(errorMessage);
    }
    return Number(number.toFixed(4));
  };

  const normalizeNipValue = (value) =>
    String(value || "")
      .trim()
      .replace(/[^0-9]/g, "");

  const normalizeNameForCompare = (value) =>
    String(value || "")
      .trim()
      .replace(/\s+/g, " ")
      .toUpperCase();

  const normalizeLookupQuery = (value) => {
    const normalized = String(value || "").trim();
    if (!normalized) {
      return "";
    }
    const selectedFromList = normalized.match(/^(\d+)\s*\|/);
    if (selectedFromList) {
      return selectedFromList[1];
    }
    return normalized;
  };

  const normalizeSupplierLookupQuery = (value) => {
    const normalized = String(value || "").trim();
    if (!normalized) {
      return "";
    }
    const selectedMatch = normalized.match(/^(\d+)\s*\|\s*(.*?)\s*\|\s*NIP:\s*(.*)$/i);
    if (!selectedMatch) {
      return normalizeLookupQuery(normalized);
    }
    const selectedName = String(selectedMatch[2] || "").trim();
    const selectedNip = normalizeNipValue(selectedMatch[3]);
    if (selectedNip) {
      return selectedNip;
    }
    if (selectedName) {
      return selectedName;
    }
    return String(selectedMatch[1] || "").trim();
  };

  const formatSupplierLabel = (item) =>
    `${item.id_klient} | ${item.nazwa || "Brak nazwy"} | NIP: ${item.nip || "—"}`;

  const findSupplierByFuzzyValue = (normalized) => {
    const normalizedName = normalizeNameForCompare(normalized);
    const normalizedDigits = normalizeNipValue(normalized);
    const fuzzyMatches = availableSuppliers.filter((item) => {
      const itemName = normalizeNameForCompare(item.nazwa);
      const itemNip = normalizeNipValue(item.nip);
      return (
        (normalizedName.length >= 3 && itemName.includes(normalizedName)) ||
        (normalizedDigits.length >= 3 && itemNip.includes(normalizedDigits))
      );
    });
    if (fuzzyMatches.length === 1) {
      return fuzzyMatches[0];
    }
    return null;
  };

  const findSupplierByInputValue = (value) => {
    const normalized = String(value || "").trim();
    if (!normalized) {
      return null;
    }
    const exact = availableSuppliers.find((item) => formatSupplierLabel(item) === normalized);
    if (exact) {
      return exact;
    }
    const match = normalized.match(/^(\d+)\b/);
    if (!match) {
      return findSupplierByFuzzyValue(normalized);
    }
    const id = Number(match[1]);
    if (!Number.isInteger(id) || id <= 0) {
      return findSupplierByFuzzyValue(normalized);
    }
    return availableSuppliers.find((item) => Number(item.id_klient) === id) || null;
  };

  const formatModelLabel = (item) => {
    const modelLabel = [item.marka || "", item.model || ""].join(" ").trim() || "Brak nazwy";
    return `${item.id_model} | ${modelLabel}`;
  };

  const formatCatalogModelLabel = (item) =>
    [item.marka || "", item.model || ""].join(" ").trim() || "Brak nazwy";

  const findModelByFuzzyValue = (normalized) => {
    const normalizedText = normalizeNameForCompare(normalized);
    const fuzzyMatches = availableModels.filter((item) =>
      normalizeNameForCompare([item.marka || "", item.model || ""].join(" ")).includes(
        normalizedText
      )
    );
    if (fuzzyMatches.length === 1) {
      return fuzzyMatches[0];
    }
    return null;
  };

  const findModelByInputValue = (value) => {
    const normalized = String(value || "").trim();
    if (!normalized) {
      return null;
    }
    const exact = availableModels.find((item) => formatModelLabel(item) === normalized);
    if (exact) {
      return exact;
    }
    const match = normalized.match(/^(\d+)\b/);
    if (!match) {
      return findModelByFuzzyValue(normalized);
    }
    const id = Number(match[1]);
    if (!Number.isInteger(id) || id <= 0) {
      return findModelByFuzzyValue(normalized);
    }
    return availableModels.find((item) => Number(item.id_model) === id) || null;
  };

  const renderSupplierOptions = () => {
    if (!availableSuppliers.length) {
      supplierOptions.innerHTML = "";
      return;
    }
    supplierOptions.innerHTML = availableSuppliers
      .map((item) => {
        const label = formatSupplierLabel(item);
        return `<option value="${escapeHtml(label)}"></option>`;
      })
      .join("");
  };

  const renderModelOptions = () => {
    if (!availableModels.length) {
      modelOptions.innerHTML = "";
      return;
    }
    modelOptions.innerHTML = availableModels
      .map((item) => {
        const label = formatModelLabel(item);
        return `<option value="${escapeHtml(label)}"></option>`;
      })
      .join("");
  };

  const renderCatalogModelOptions = () => {
    const currentValue = String(catalogModelSelect.value || "");
    const options = ["<option value=''>-- wszystkie modele --</option>"];
    const ordered = [...catalogModels].sort((a, b) =>
      formatCatalogModelLabel(a).localeCompare(formatCatalogModelLabel(b), "pl")
    );
    ordered.forEach((item) => {
      const modelId = Number(item.id_model);
      if (!Number.isInteger(modelId) || modelId <= 0) {
        return;
      }
      options.push(
        `<option value="${escapeHtml(modelId)}">${escapeHtml(formatCatalogModelLabel(item))}</option>`
      );
    });
    catalogModelSelect.innerHTML = options.join("");
    if (currentValue && options.some((option) => option.includes(`value="${currentValue}"`))) {
      catalogModelSelect.value = currentValue;
    }
  };

  const applySupplierToCreateForm = (supplier) => {
    if (!supplier) {
      return;
    }
    supplierCreateNameInput.value = String(supplier.nazwa || "");
    supplierCreateNipInput.value = String(supplier.nip || "");
    supplierCreateAddressInput.value = String(supplier.adres || "");
    supplierCreatePostalInput.value = String(supplier.kod || "");
    supplierCreateCityInput.value = String(supplier.poczta || "");
    supplierCreatePhoneInput.value = String(supplier.telefon || "");
    supplierCreateEmailInput.value = String(supplier.email || "");
  };

  const findExistingSupplierBeforeCreate = async ({ name, nip }) => {
    const normalizedName = normalizeNameForCompare(name);
    const normalizedNip = normalizeNipValue(nip);
    const queries = [normalizedNip, normalizeLookupQuery(name)]
      .map((value) => String(value || "").trim())
      .map((value) => value.slice(0, 100))
      .filter(Boolean);
    const checked = new Set();

    for (const query of queries) {
      if (checked.has(query)) {
        continue;
      }
      checked.add(query);
      const body = await fetchJson(
        `/admin/device/suppliers?query=${encodeURIComponent(query)}&limit=200`,
        {
          timeoutMs: DEFAULT_TIMEOUT_MS,
          defaultError: "Nie udalo sie zweryfikowac istnienia dostawcy.",
        }
      );
      const rows = Array.isArray(body.rows) ? body.rows : [];
      const existing = rows.find((item) => {
        const sameNip = normalizedNip && normalizeNipValue(item.nip) === normalizedNip;
        const sameName =
          normalizedName && normalizeNameForCompare(item.nazwa) === normalizedName;
        return Boolean(sameNip || sameName);
      });
      if (existing) {
        return existing;
      }
    }
    return null;
  };

  const loadSuppliers = async (query = "") => {
    const queryValue = normalizeSupplierLookupQuery(query);
    const body = await fetchJson(
      `/admin/device/suppliers?query=${encodeURIComponent(queryValue)}&limit=200`,
      {
        timeoutMs: DEFAULT_TIMEOUT_MS,
        defaultError: "Nie udalo sie pobrac listy dostawcow.",
      }
    );
    availableSuppliers = Array.isArray(body.rows) ? body.rows : [];
    renderSupplierOptions();
  };

  const loadModels = async (query = "") => {
    const queryValue = normalizeLookupQuery(query);
    const body = await fetchJson(
      `/admin/device/models?query=${encodeURIComponent(queryValue)}&limit=200`,
      {
        timeoutMs: DEFAULT_TIMEOUT_MS,
        defaultError: "Nie udalo sie pobrac listy modeli.",
      }
    );
    availableModels = Array.isArray(body.rows) ? body.rows : [];
    renderModelOptions();
  };

  const loadCatalogModels = async () => {
    const body = await fetchJson("/admin/device/models?query=&limit=500", {
      timeoutMs: DEFAULT_TIMEOUT_MS,
      defaultError: "Nie udalo sie pobrac listy modeli do synchronizacji AUTO.",
    });
    catalogModels = Array.isArray(body.rows) ? body.rows : [];
    renderCatalogModelOptions();
  };

  const renderModelCreateOptions = (options) => {
    const brands = Array.isArray(options?.brands) ? options.brands : [];
    const groups = Array.isArray(options?.groups) ? options.groups : [];
    const kinds = Array.isArray(options?.kinds) ? options.kinds : [];
    const defaultBrand = String(options?.default_brand || "Ricoh");

    modelCreateMarkaInput.innerHTML = brands
      .map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`)
      .join("");
    if (brands.includes(defaultBrand)) {
      modelCreateMarkaInput.value = defaultBrand;
    }

    modelCreateGrupaInput.innerHTML = [
      "<option value=''>-- wybierz --</option>",
      ...groups.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`),
    ].join("");
    const defaultGroup = groups.find((item) => normalizeNameForCompare(item) === "DRUK");
    modelCreateGrupaInput.value = defaultGroup || "";

    modelCreateRodzajInput.innerHTML = [
      "<option value=''>-- wybierz --</option>",
      ...kinds.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`),
    ].join("");
  };

  const loadModelFormOptions = async () => {
    const body = await fetchJson("/admin/device/model-form-options", {
      timeoutMs: DEFAULT_TIMEOUT_MS,
      defaultError: "Nie udalo sie pobrac slownikow modelu.",
    });
    renderModelCreateOptions(body.options || {});
  };

  const renderIntakeItems = () => {
    if (!intakeItems.length) {
      intakeItemsBody.innerHTML = "<tr><td colspan='6'>Brak pozycji. Dodaj model i ilosc.</td></tr>";
      return;
    }
    intakeItemsBody.innerHTML = intakeItems
      .map(
        (item, index) => `
          <tr data-row-index="${index}">
            <td>${index + 1}</td>
            <td>
              <div class="device-cell-stack">
                <div>
                  <div class="device-cell-value device-cell-strong">${escapeHtml(
                    item.model_label || `ID ${item.model_id}`
                  )}</div>
                  <div class="device-cell-label">ID_MODEL: ${escapeHtml(item.model_id)}</div>
                </div>
              </div>
            </td>
            <td>
              <input
                type="text"
                maxlength="100"
                class="device-editor-input device-editor-serial"
                value="${escapeHtml(item.serial)}"
              >
            </td>
            <td>
              <div class="device-ewidencja-stack">
                <input
                  type="text"
                  maxlength="100"
                  class="device-editor-input device-editor-ewidencja-base"
                  value="${escapeHtml(item.ewidencja_base)}"
                  placeholder="KP/0001/"
                >
                <input
                  type="text"
                  maxlength="100"
                  class="device-editor-input device-editor-ewidencja-suffix"
                  value="${escapeHtml(item.ewidencja_suffix || "")}"
                  placeholder="Dalsza czesc (opcjonalnie), np. 22/333"
                >
              </div>
            </td>
            <td>
              <input
                type="number"
                min="0"
                step="0.01"
                class="device-editor-input device-editor-price"
                value="${escapeHtml(item.purchase_price_netto ?? "")}"
              >
            </td>
            <td>
              <button type="button" class="flow-secondary device-editor-remove">Usun</button>
            </td>
          </tr>
        `
      )
      .join("");
  };

  const appendIntakeItems = () => {
    const selectedModel = findModelByInputValue(modelInput.value);
    if (!selectedModel) {
      throw new Error("Wybierz model z listy (ID / marka / model).");
    }
    const modelId = parsePositiveInt(selectedModel.id_model, "Wybierz poprawny model z listy.");
    const modelLabel = [selectedModel.marka || "", selectedModel.model || ""].join(" ").trim() || `ID ${modelId}`;
    const quantity = parsePositiveInt(
      intakeAddQuantityInput.value,
      "Ilosc egzemplarzy musi byc dodatnia liczba calkowita."
    );
    const priceNetto = parseNonNegativeDecimal(
      intakeAddPriceNettoInput.value,
      "Cena zakupu netto musi byc liczba >= 0."
    );
    const prefix = normalizeEwidPrefix(intakeEwidPrefixInput.value);
    const startNumber = parsePositiveInt(
      intakeEwidNextNumberInput.value,
      "Podaj poprawny numer startowy ewidencji."
    );
    let nextNumber = startNumber;
    if (intakeItems.length) {
      const extracted = intakeItems
        .map((item) => splitEwidencjaBase(item.ewidencja_base))
        .filter(Boolean)
        .map((item) => item.number);
      if (extracted.length) {
        nextNumber = Math.max(...extracted) + 1;
      }
    }
    const width = Math.max(4, String(nextNumber + quantity - 1).length);
    for (let offset = 0; offset < quantity; offset += 1) {
      const ewidencjaBase = formatEwidencjaBase(prefix, nextNumber + offset, width);
      intakeItems.push({
        model_id: modelId,
        model_label: modelLabel,
        serial: "",
        ewidencja_base: ewidencjaBase,
        ewidencja_suffix: "",
        system_ewidencja_base: ewidencjaBase,
        purchase_price_netto: priceNetto,
      });
    }
    intakeEwidPrefixInput.value = prefix;
    renderIntakeItems();
    setInfo(`Dodano ${quantity} pozycje/pozycji dla modelu ${modelLabel} (ID=${modelId}).`);
    setIntakeSubmitStatus("", "info");
  };

  const validateIntakeItems = () => {
    if (!intakeItems.length) {
      throw new Error("Dodaj przynajmniej jedna pozycje do dokumentu PZ.");
    }
    const seenSerial = new Set();
    const seenEwidencja = new Set();
    return intakeItems.map((item, index) => {
      const rowNo = index + 1;
      const modelId = Number(item.model_id);
      const serial = String(item.serial || "").trim();
      const ewidencjaBase = String(item.ewidencja_base || "").trim();
      const ewidencjaSuffix = String(item.ewidencja_suffix || "").trim();
      const priceNetto = parseNonNegativeDecimal(
        item.purchase_price_netto,
        `Wiersz ${rowNo}: cena zakupu netto musi byc liczba >= 0.`
      );
      if (!Number.isInteger(modelId) || modelId <= 0) {
        throw new Error(`Wiersz ${rowNo}: niepoprawne ID_MODEL.`);
      }
      if (!serial) {
        throw new Error(`Wiersz ${rowNo}: pole S/N jest wymagane.`);
      }
      const parsedBase = splitEwidencjaBase(ewidencjaBase);
      if (!parsedBase) {
        throw new Error(
          `Wiersz ${rowNo}: baza ewidencji musi miec format typu KP/0001/.`
        );
      }
      if (!isDeviceBasePrefix(parsedBase.prefix)) {
        throw new Error(
          `Wiersz ${rowNo}: baza ewidencji musi zaczynac sie od ${DEVICE_EWIDENCJA_BASE_PREFIX}.`
        );
      }
      const ewidencja = buildFullEwidencja(ewidencjaBase, ewidencjaSuffix);
      const serialKey = normalizeDeviceKey(serial);
      const ewidencjaKey = normalizeDeviceKey(ewidencja);
      if (seenSerial.has(serialKey)) {
        throw new Error(`Wiersz ${rowNo}: duplikat numeru seryjnego w formularzu.`);
      }
      if (seenEwidencja.has(ewidencjaKey)) {
        throw new Error(`Wiersz ${rowNo}: duplikat numeru ewidencyjnego w formularzu.`);
      }
      seenSerial.add(serialKey);
      seenEwidencja.add(ewidencjaKey);
      return {
        model_id: modelId,
        serial,
        ewidencja,
        purchase_price_netto: priceNetto,
      };
    });
  };

  const loadIntakeDefaults = async (prefixOverride = null) => {
    const prefix = normalizeEwidPrefix(prefixOverride || DEVICE_EWIDENCJA_BASE_PREFIX);
    try {
      const body = await fetchJson(
        `/admin/device/intake/defaults?ewidencja_prefix=${encodeURIComponent(prefix)}`,
        {
          timeoutMs: DEFAULT_TIMEOUT_MS,
          defaultError: "Nie udalo sie pobrac numeracji ewidencyjnej.",
        }
      );
      const defaults = body.defaults || {};
      intakeEwidPrefixInput.value = normalizeEwidPrefix(DEVICE_EWIDENCJA_BASE_PREFIX);
      intakeEwidNextNumberInput.value = String(Number(defaults.next_number || 1));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Blad odczytu domyslnej numeracji ewidencyjnej."
      );
    }
  };

  const loadData = async () => {
    setError("");
    setBusy(true);
    try {
      const me = await fetchJson("/auth/me", {
        timeoutMs: DEFAULT_TIMEOUT_MS,
        defaultError: "Sesja wygasla.",
      });
      const sections = new Set(Array.isArray(me.sections) ? me.sections : []);
      if (!sections.has("generator")) {
        throw new Error("Brak uprawnien do sekcji DEVICE.");
      }

      const displayName = [me.first_name, me.last_name].filter(Boolean).join(" ").trim();
      userChip.textContent = displayName || me.email || "Uzytkownik";

      const data = await fetchJson("/admin/device/dashboard", {
        timeoutMs: DEFAULT_TIMEOUT_MS,
        defaultError: "Nie udalo sie pobrac dashboardu urzadzen.",
      });

      updateSummary(data.summary || {});
      renderIntakes(data.recent_intakes || []);
      renderModelQuality(data.model_quality || {});
      renderProcessRules(data.process_rules || []);
      renderOperationalNotes(data.operational_notes || []);
      if (!intakeItems.length) {
        await loadIntakeDefaults();
      }
      await loadSuppliers(supplierInput.value);
      await loadModels(modelInput.value);
      await loadCatalogModels();
      await loadModelFormOptions();
      setInfo(`Dane urzadzen odswiezone: ${formatDate(new Date().toISOString())}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Blad ladowania danych.";
      setError(message);
      if (message.includes("Sesja")) {
        clearDeviceToken();
        window.location.replace("/");
      }
    } finally {
      setBusy(false);
    }
  };

  refreshBtn.addEventListener("click", () => {
    loadData();
  });

  catalogForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setError("");
    setInfo("");
    try {
      const selectedModelId = Number(String(catalogModelSelect.value || "").trim());
      const modelIds =
        Number.isInteger(selectedModelId) && selectedModelId > 0 ? [selectedModelId] : null;
      const payload = {
        model_ids: modelIds,
        only_missing: Boolean(catalogOnlyMissingInput.checked),
      };
      setActionBusy(
        catalogSyncBtn,
        true,
        "Synchronizacja...",
        "Synchronizuj kartoteke AUTO"
      );
      const body = await fetchJson("/admin/device/catalog/sync", {
        method: "POST",
        extraHeaders: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
        timeoutMs: LONG_TIMEOUT_MS,
        defaultError: "Nie udalo sie zsynchronizowac kartoteki AUTO.",
      });
      setInfo(body.message || "Synchronizacja kartoteki AUTO zakonczona.");
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad synchronizacji kartoteki AUTO.");
    } finally {
      setActionBusy(
        catalogSyncBtn,
        false,
        "Synchronizacja...",
        "Synchronizuj kartoteke AUTO"
      );
    }
  });

  catalogSyncAllBtn.addEventListener("click", async () => {
    setError("");
    setInfo("");
    try {
      setActionBusy(
        catalogSyncAllBtn,
        true,
        "Synchronizacja...",
        "Pelna synchronizacja wszystkich modeli"
      );
      const body = await fetchJson("/admin/device/catalog/sync", {
        method: "POST",
        extraHeaders: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ model_ids: null, only_missing: false }),
        timeoutMs: LONG_TIMEOUT_MS,
        defaultError: "Nie udalo sie uruchomic pelnej synchronizacji.",
      });
      setInfo(body.message || "Pelna synchronizacja kartoteki AUTO zakonczona.");
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad pelnej synchronizacji AUTO.");
    } finally {
      setActionBusy(
        catalogSyncAllBtn,
        false,
        "Synchronizacja...",
        "Pelna synchronizacja wszystkich modeli"
      );
    }
  });

  modelCreateBtn.addEventListener("click", async () => {
    setError("");
    setInfo("");
    try {
      const marka = String(modelCreateMarkaInput.value || "").trim();
      const model = String(modelCreateModelInput.value || "").trim();
      if (!marka || !model) {
        throw new Error("Podaj marke i model dla nowego rekordu.");
      }
      const payload = {
        marka,
        model,
        grupa: String(modelCreateGrupaInput.value || "").trim() || null,
        rodzaj: String(modelCreateRodzajInput.value || "").trim() || null,
        kolor: Boolean(modelCreateKolorInput.checked),
        plik: String(modelCreatePlikInput.value || "").trim() || null,
        sync_catalog: Boolean(modelCreateSyncCatalogInput.checked),
      };
      setActionBusy(modelCreateBtn, true, "Dodawanie...", "Dodaj model");
      const body = await fetchJson("/admin/device/models", {
        method: "POST",
        extraHeaders: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
        timeoutMs: DEFAULT_TIMEOUT_MS,
        defaultError: "Nie udalo sie dodac modelu.",
      });
      const result = body.model || {};
      setInfo(
        `Model zapisany: ID_MODEL=${result.id_model}, marka=${result.marka}, model=${result.model}.`
      );
      modelCreateModelInput.value = "";
      const defaultGroupOption = Array.from(modelCreateGrupaInput.options).find(
        (option) => normalizeNameForCompare(option.value) === "DRUK"
      );
      modelCreateGrupaInput.value = defaultGroupOption ? defaultGroupOption.value : "";
      modelCreateRodzajInput.value = "";
      modelCreatePlikInput.value = "";
      modelCreateKolorInput.checked = false;
      modelCreateSyncCatalogInput.checked = true;
      await loadModels(modelInput.value);
      await loadData();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Blad tworzenia modelu.");
    } finally {
      setActionBusy(modelCreateBtn, false, "Dodawanie...", "Dodaj model");
    }
  });

  intakeForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setError("");
    setInfo("");
    clearIntakeResult();
    setIntakeSubmitStatus("", "info");
    try {
      const modelLabels = new Map();
      intakeItems.forEach((item) => {
        const modelId = Number(item.model_id);
        if (!modelLabels.has(modelId)) {
          modelLabels.set(modelId, String(item.model_label || `ID ${modelId}`));
        }
      });
      const items = validateIntakeItems();
      const supplierValue = String(supplierInput.value || "").trim();
      let supplierId = null;
      if (supplierValue) {
        let supplier = findSupplierByInputValue(supplierValue);
        if (!supplier) {
          await loadSuppliers(supplierValue);
          supplier = findSupplierByInputValue(supplierValue);
        }
        if (!supplier) {
          throw new Error("Wybierz dostawce z listy filtrowanej (NIP / nazwa / ID).");
        }
        supplierId = Number(supplier.id_klient);
        supplierInput.value = formatSupplierLabel(supplier);
        applySupplierToCreateForm(supplier);
        setSupplierStatus(
          `Wybrano dostawce: ID ${supplier.id_klient}, ${supplier.nazwa || "Brak nazwy"}.`,
          "success"
        );
      }
      const payload = {
        items,
        supplier_id: supplierId,
        external_document: String(intakeDocExternalInput.value || "").trim() || null,
        issued_by: String(intakeIssuedByInput.value || "").trim() || null,
        force: Boolean(intakeForceInput.checked),
        ewidencja_prefix: normalizeEwidPrefix(intakeEwidPrefixInput.value),
      };
      setActionBusy(
        intakeCreateBtn,
        true,
        "Tworzenie PZ...",
        "Utworz przyjecie PZ (batch)"
      );
      setIntakeSubmitStatus("Wysylanie dokumentu PZ do systemu...", "info");
      const body = await fetchJson("/admin/device/intake/batch", {
        method: "POST",
        extraHeaders: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
        timeoutMs: LONG_TIMEOUT_MS,
        defaultError: "Nie udalo sie utworzyc przyjecia PZ.",
      });
      setInfo(body.message || "Przyjecie PZ utworzone.");
      setIntakeSubmitStatus("Przyjecie PZ zapisane poprawnie.", "success");
      renderIntakeResult({
        batch: body.batch || {},
        modelLabels,
      });
      intakeItems.length = 0;
      renderIntakeItems();
      intakeDocExternalInput.value = "";
      loadIntakeDefaults().catch(() => null);
      loadData().catch(() => null);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Blad tworzenia przyjecia PZ.";
      setError(message);
      setIntakeSubmitStatus(message, "error");
      intakeSubmitStatusBox.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } finally {
      setActionBusy(
        intakeCreateBtn,
        false,
        "Tworzenie PZ...",
        "Utworz przyjecie PZ (batch)"
      );
    }
  });

  intakeAddItemsBtn.addEventListener("click", () => {
    setError("");
    try {
      appendIntakeItems();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Nie udalo sie dodac pozycji.");
    }
  });

  intakeClearBtn.addEventListener("click", async () => {
    intakeItems.length = 0;
    renderIntakeItems();
    setInfo("Wyczyszczono wszystkie pozycje przyjecia.");
    await loadIntakeDefaults(intakeEwidPrefixInput.value);
  });

  let supplierSearchTimer = null;
  supplierInput.addEventListener("input", () => {
    if (supplierSearchTimer) {
      window.clearTimeout(supplierSearchTimer);
    }
    supplierSearchTimer = window.setTimeout(async () => {
      try {
        await loadSuppliers(String(supplierInput.value || "").trim());
      } catch (err) {
        setError(err instanceof Error ? err.message : "Blad odczytu dostawcow.");
      }
    }, 250);
  });
  supplierInput.addEventListener("change", () => {
    const supplier = findSupplierByInputValue(supplierInput.value);
    if (supplier) {
      supplierInput.value = formatSupplierLabel(supplier);
      applySupplierToCreateForm(supplier);
      setSupplierStatus(
        `Wybrano istniejacego dostawce: ID ${supplier.id_klient}, ${supplier.nazwa || "Brak nazwy"}.`,
        "success"
      );
      return;
    }
    const rawValue = String(supplierInput.value || "").trim();
    if (rawValue) {
      setSupplierStatus(
        "Nie znaleziono jednoznacznego dostawcy. Wybierz pozycje z listy lub dodaj nowego dostawce.",
        "warn"
      );
      return;
    }
    setSupplierStatus("", "info");
  });

  let modelSearchTimer = null;
  modelInput.addEventListener("input", () => {
    if (modelSearchTimer) {
      window.clearTimeout(modelSearchTimer);
    }
    modelSearchTimer = window.setTimeout(async () => {
      try {
        await loadModels(String(modelInput.value || "").trim());
      } catch (err) {
        setError(err instanceof Error ? err.message : "Blad odczytu modeli.");
      }
    }, 250);
  });
  modelInput.addEventListener("change", () => {
    const model = findModelByInputValue(modelInput.value);
    if (model) {
      modelInput.value = formatModelLabel(model);
    }
  });

  supplierCreateBtn.addEventListener("click", async () => {
    setError("");
    setInfo("");
    try {
      const name = String(supplierCreateNameInput.value || "").trim();
      if (!name) {
        throw new Error("Podaj nazwe nowego dostawcy.");
      }
      const payload = {
        name,
        nip: String(supplierCreateNipInput.value || "").trim() || null,
        address: String(supplierCreateAddressInput.value || "").trim() || null,
        postal_code: String(supplierCreatePostalInput.value || "").trim() || null,
        city: String(supplierCreateCityInput.value || "").trim() || null,
        phone: String(supplierCreatePhoneInput.value || "").trim() || null,
        email: String(supplierCreateEmailInput.value || "").trim() || null,
      };
      const existing = await findExistingSupplierBeforeCreate({
        name: payload.name,
        nip: payload.nip,
      });
      if (existing) {
        supplierInput.value = formatSupplierLabel(existing);
        applySupplierToCreateForm(existing);
        setSupplierStatus(
          `Dostawca juz istnieje: ID ${existing.id_klient}. Uzyj istniejacego rekordu.`,
          "warn"
        );
        setInfo(
          `Dostawca juz istnieje: ID_KLIENT=${existing.id_klient}. Nie dodano nowego rekordu.`
        );
        return;
      }

      setActionBusy(
        supplierCreateBtn,
        true,
        "Zapisywanie...",
        "Dodaj nowego dostawce"
      );
      const body = await fetchJson("/admin/device/suppliers", {
        method: "POST",
        extraHeaders: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
        timeoutMs: DEFAULT_TIMEOUT_MS,
        defaultError: "Nie udalo sie utworzyc dostawcy.",
      });
      const supplier = body.supplier || {};
      supplierInput.value = String(supplier.nazwa || "");
      await loadSuppliers(supplierInput.value);
      if (supplier.id_klient && supplier.nazwa) {
        supplierInput.value = formatSupplierLabel(supplier);
      }
      applySupplierToCreateForm(supplier);
      setSupplierStatus(`Dodano nowego dostawce: ID ${supplier.id_klient}.`, "success");
      setInfo(`Dostawca zapisany: ID_KLIENT=${supplier.id_klient}.`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Blad tworzenia dostawcy.";
      setError(message);
      setSupplierStatus(message, "error");
    } finally {
      setActionBusy(supplierCreateBtn, false, "Zapisywanie...", "Dodaj nowego dostawce");
    }
  });

  intakeEwidPrefixInput.addEventListener("change", async () => {
    if (intakeItems.length) {
      return;
    }
    await loadIntakeDefaults(intakeEwidPrefixInput.value);
  });

  intakeItemsBody.addEventListener("click", async (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    if (!target.classList.contains("device-editor-remove")) {
      return;
    }
    const row = target.closest("tr[data-row-index]");
    if (!row) {
      return;
    }
    const index = Number(row.getAttribute("data-row-index"));
    if (!Number.isInteger(index) || index < 0 || index >= intakeItems.length) {
      return;
    }
    intakeItems.splice(index, 1);
    renderIntakeItems();
    if (!intakeItems.length) {
      await loadIntakeDefaults(intakeEwidPrefixInput.value);
    }
  });

  intakeItemsBody.addEventListener("input", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) {
      return;
    }
    const row = target.closest("tr[data-row-index]");
    if (!row) {
      return;
    }
    const index = Number(row.getAttribute("data-row-index"));
    if (!Number.isInteger(index) || index < 0 || index >= intakeItems.length) {
      return;
    }
    if (target.classList.contains("device-editor-serial")) {
      intakeItems[index].serial = target.value;
      return;
    }
    if (target.classList.contains("device-editor-ewidencja-base")) {
      intakeItems[index].ewidencja_base = target.value;
      return;
    }
    if (target.classList.contains("device-editor-ewidencja-suffix")) {
      intakeItems[index].ewidencja_suffix = target.value;
      return;
    }
    if (target.classList.contains("device-editor-price")) {
      intakeItems[index].purchase_price_netto = target.value;
    }
  });

  intakeItemsBody.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement)) {
      return;
    }
    if (!target.classList.contains("device-editor-ewidencja-base")) {
      return;
    }
    const row = target.closest("tr[data-row-index]");
    if (!row) {
      return;
    }
    const index = Number(row.getAttribute("data-row-index"));
    if (!Number.isInteger(index) || index < 0 || index >= intakeItems.length) {
      return;
    }
    const item = intakeItems[index];
    const changedValue = String(target.value || "").trim().toUpperCase();
    item.ewidencja_base = changedValue;
    if (!changedValue || changedValue === item.system_ewidencja_base) {
      return;
    }

    const continueFromManual = window.confirm(
      "Zmieniles kolejnosc numeracji ewidencyjnej. Czy kolejne pozycje maja byc numerowane od podanego numeru?"
    );
    if (!continueFromManual) {
      for (let cursor = index + 1; cursor < intakeItems.length; cursor += 1) {
        if (intakeItems[cursor].system_ewidencja_base) {
          intakeItems[cursor].ewidencja_base = intakeItems[cursor].system_ewidencja_base;
        }
      }
      renderIntakeItems();
      setInfo("Zachowano systemowa numeracje dla kolejnych pozycji.");
      return;
    }

    const parsed = splitEwidencjaBase(changedValue);
    if (!parsed) {
      setError(
        "Nie mozna kontynuowac numeracji od zmienionego numeru. Podaj baze w formacie typu KP/0001/."
      );
      return;
    }
    if (!isDeviceBasePrefix(parsed.prefix)) {
      setError(`Baza ewidencji musi zaczynac sie od ${DEVICE_EWIDENCJA_BASE_PREFIX}.`);
      return;
    }

    intakeEwidPrefixInput.value = parsed.prefix;
    let nextNumber = parsed.number + 1;
    for (let cursor = index + 1; cursor < intakeItems.length; cursor += 1) {
      const nextValue = formatEwidencjaBase(parsed.prefix, nextNumber, parsed.width);
      intakeItems[cursor].ewidencja_base = nextValue;
      intakeItems[cursor].system_ewidencja_base = nextValue;
      nextNumber += 1;
    }
    if (index === 0) {
      intakeEwidNextNumberInput.value = String(parsed.number);
    }
    const normalizedChanged = formatEwidencjaBase(parsed.prefix, parsed.number, parsed.width);
    item.ewidencja_base = normalizedChanged;
    item.system_ewidencja_base = normalizedChanged;
    renderIntakeItems();
    setInfo("Zmieniono kolejnosc: kolejne numery ewidencyjne kontynuuja od recznej wartosci.");
  });

  logoutBtn.addEventListener("click", async () => {
    try {
      await fetchJson("/auth/logout", {
        method: "POST",
        timeoutMs: DEFAULT_TIMEOUT_MS,
        defaultError: "Nie udalo sie zakonczyc sesji.",
      });
    } catch (_err) {
      // Token i tak jest usuwany lokalnie.
    } finally {
      clearDeviceToken();
      window.location.replace("/");
    }
  });

  await loadData();
}

document.addEventListener("DOMContentLoaded", () => {
  initializeDevicePage();
});
