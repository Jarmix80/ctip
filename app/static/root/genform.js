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

  let token = readToken();
  let openedFormId = null;
  let currentDetailData = null;
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

  function showLogin(message = "") {
    loginSection.hidden = false;
    appSection.hidden = true;
    setAuthLayout(true);
    closeDetailModal();
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

  function renderItems(items) {
    if (!Array.isArray(items) || !items.length) {
      tableBody.innerHTML = "<tr><td colspan='8'>Brak wygenerowanych formularzy.</td></tr>";
      return;
    }
    tableBody.innerHTML = items
      .map((item) => {
        const rowId = Number(item.id);
        return `<tr data-form-id="${rowId}" tabindex="0">
          <td>${escapeHtml(item.customer_name || "—")}</td>
          <td>${escapeHtml(item.customer_email || "—")}</td>
          <td>${escapeHtml(item.customer_phone || "—")}</td>
          <td>${escapeHtml(item.created_by_name || "—")}</td>
          <td><span class="genform-status ${statusClass(item.status)}">${escapeHtml(statusLabel(item.status))}</span></td>
          <td>${escapeHtml(formatDate(item.token_expires_at))}</td>
          <td>${escapeHtml(formatDate(item.created_at))}</td>
          <td>
            <div class="genform-row-actions">
              <button type="button" class="genform-row-action" data-action="view" data-form-id="${rowId}">Wyświetl</button>
              <button type="button" class="genform-row-action danger" data-action="delete" data-form-id="${rowId}">Usuń</button>
            </div>
          </td>
        </tr>`;
      })
      .join("");
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
      const response = await fetch("/admin/forms", { headers: headers(false) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udało się pobrać listy formularzy.");
      }
      renderItems(data.items || []);
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

  async function deleteForm(formId) {
    if (!token) {
      showLogin("Brak aktywnej sesji.");
      return;
    }
    const confirmed = window.confirm(
      "Czy na pewno chcesz usunąć ten formularz? Operacja usunie wpis z listy generatora."
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
      showSuccess("Formularz został usunięty.");
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
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && detailModal && !detailModal.hidden) {
      closeDetailModal();
    }
  });
  window.addEventListener("afterprint", clearPrintMode);
  window.addEventListener("pageshow", () => {
    closeDetailModal();
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
      } else if (action === "delete") {
        deleteForm(formId);
      }
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
