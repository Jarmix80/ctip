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
  const detailModal = document.getElementById("genform-detail-modal");
  const detailCloseBtn = document.getElementById("genform-detail-close");
  const detailStatus = document.getElementById("genform-detail-status");
  const detailName = document.getElementById("genform-detail-name");
  const detailEmail = document.getElementById("genform-detail-email");
  const detailPhone = document.getElementById("genform-detail-phone");
  const detailStage = document.getElementById("genform-detail-stage");
  const detailCreated = document.getElementById("genform-detail-created");
  const detailSubmittedAt = document.getElementById("genform-detail-submitted-at");
  const detailPayloadBox = document.getElementById("genform-detail-payload-box");
  const detailPayload = document.getElementById("genform-detail-payload");

  let token = readToken();
  let openedFormId = null;

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

  function setNodeText(node, value) {
    if (!node) {
      return;
    }
    node.textContent = value || "—";
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
      setNodeText(detailName, item.customer_name);
      setNodeText(detailEmail, item.customer_email);
      setNodeText(detailPhone, item.customer_phone);
      setNodeText(detailStage, statusLabel(item.status));
      setNodeText(detailCreated, formatDate(item.created_at));
      setNodeText(
        detailSubmittedAt,
        formatDate(data.submitted_meta?.submitted_at || item.submitted_at || null)
      );
      setNodeText(detailStatus, data.status_message || "Brak informacji o statusie.");

      const payload = data.submitted_payload;
      if (payload && typeof payload === "object") {
        detailPayload.textContent = JSON.stringify(payload, null, 2);
        detailPayloadBox.hidden = false;
      } else {
        detailPayload.textContent = "";
        detailPayloadBox.hidden = true;
      }

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
    if (!submitButton || !nameInput || !emailInput || !phoneInput) {
      return;
    }
    setBusy(submitButton, true, "Generowanie…", "Generuj formularz");
    try {
      const response = await fetch("/admin/forms", {
        method: "POST",
        headers: headers(true),
        body: JSON.stringify({
          customer_name: nameInput.value.trim(),
          customer_email: emailInput.value.trim(),
          customer_phone: phoneInput.value.trim(),
        }),
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

  async function handleCopyLink() {
    if (!generatedLink || !generatedLink.textContent) {
      return;
    }
    try {
      await navigator.clipboard.writeText(generatedLink.textContent);
      clearMessages();
      showSuccess("Skopiowano link do schowka.");
    } catch (err) {
      clearMessages();
      showError("Nie udało się skopiować linku.");
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
  detailModal?.addEventListener("click", (event) => {
    if (event.target === detailModal) {
      closeDetailModal();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && detailModal && !detailModal.hidden) {
      closeDetailModal();
    }
  });
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
