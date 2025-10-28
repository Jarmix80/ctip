(() => {
  const TOKEN_KEY = "admin-session-token";

  const state = {
    token: null,
    profile: null,
    templates: [],
    editingTemplateId: null,
  };

  const elements = {};

  const readToken = () =>
    window.localStorage?.getItem(TOKEN_KEY) ||
    window.sessionStorage?.getItem(TOKEN_KEY) ||
    null;

  const clearToken = () => {
    try {
      window.localStorage?.removeItem(TOKEN_KEY);
      window.sessionStorage?.removeItem(TOKEN_KEY);
    } catch (err) {
      console.warn("Nie udało się wyczyścić tokenu", err);
    }
    state.token = null;
  };

  const redirectToPanel = () => {
    window.location.href = "/operator";
  };

  const showToast = (message, variant = "info") => {
    const containerId = "toast-container";
    let container = document.getElementById(containerId);
    if (!container) {
      container = document.createElement("div");
      container.id = containerId;
      container.className = "toast-container";
      document.body.appendChild(container);
    }
    const toast = document.createElement("div");
    toast.className = `toast ${variant}`;
    toast.textContent = message;
    container.appendChild(toast);
    requestAnimationFrame(() => toast.classList.add("visible"));
    setTimeout(() => {
      toast.classList.remove("visible");
      toast.addEventListener(
        "transitionend",
        () => toast.remove(),
        { once: true },
      );
    }, 3200);
  };

  const handleAuthError = (message = "Sesja wygasła. Zaloguj się ponownie.") => {
    clearToken();
    showToast(message, "warning");
    setTimeout(redirectToPanel, 800);
  };

  const fetchWithAuth = async (url, options = {}) => {
    const token = state.token || readToken();
    if (!token) {
      handleAuthError();
      throw new Error("Brak tokenu sesji.");
    }
    state.token = token;
    const headers = options.headers ? { ...options.headers } : {};
    headers["X-Admin-Session"] = token;
    if (options.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401 || response.status === 403) {
      handleAuthError();
      throw new Error("Sesja wygasła. Zaloguj się ponownie.");
    }
    if (!response.ok) {
      let message = `Błąd HTTP ${response.status}`;
      try {
        const payload = await response.json();
        if (payload?.detail) {
          message = payload.detail;
        }
      } catch (err) {
        // brak ciała odpowiedzi
      }
      throw new Error(message);
    }
    return response;
  };

  const fetchJson = async (url, options = {}) => {
    const response = await fetchWithAuth(url, options);
    if (response.status === 204) {
      return null;
    }
    return response.json();
  };

  const initElements = () => {
    elements.profileForm = document.getElementById("profile-form");
    elements.profileEmail = document.getElementById("profile-email");
    elements.profileFirstName = document.getElementById("profile-first-name");
    elements.profileLastName = document.getElementById("profile-last-name");
    elements.profileInternalExt = document.getElementById("profile-internal-ext");
    elements.profileMobilePhone = document.getElementById("profile-mobile-phone");
    elements.profileFeedback = document.getElementById("profile-feedback");
    elements.profileSubmit = document.getElementById("profile-submit");

    elements.passwordForm = document.getElementById("password-form");
    elements.passwordCurrent = document.getElementById("password-current");
    elements.passwordNew = document.getElementById("password-new");
    elements.passwordConfirm = document.getElementById("password-confirm");
    elements.passwordFeedback = document.getElementById("password-feedback");
    elements.passwordSubmit = document.getElementById("password-submit");

    elements.templateForm = document.getElementById("template-form");
    elements.templateId = document.getElementById("template-id");
    elements.templateName = document.getElementById("template-name");
    elements.templateBody = document.getElementById("template-body");
    elements.templateActive = document.getElementById("template-active");
    elements.templateSubmit = document.getElementById("template-submit");
    elements.templateCancel = document.getElementById("template-cancel");
    elements.templateFeedback = document.getElementById("template-feedback");
    elements.templatesBody = document.getElementById("templates-body");
  };

  const setFeedback = (element, message, variant = "info") => {
    if (!element) {
      return;
    }
    element.textContent = message || "";
    element.className = `op-form-hint${message ? ` ${variant}` : ""}`;
  };

  const fillProfileForm = (profile) => {
    elements.profileEmail.value = profile.email || "";
    elements.profileFirstName.value = profile.first_name || "";
    elements.profileLastName.value = profile.last_name || "";
    elements.profileInternalExt.value = profile.internal_ext || "";
    elements.profileMobilePhone.value = profile.mobile_phone || "";
  };

  const loadProfile = async () => {
    const profile = await fetchJson("/operator/api/profile");
    if (!profile) {
      return;
    }
    state.profile = profile;
    fillProfileForm(profile);
  };

  const escapeHtml = (value) => {
    if (value == null) {
      return "";
    }
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  };

  const formatScope = (scope) => {
    if (scope === "global") {
      return "Globalny";
    }
    return "Mój szablon";
  };

  const formatStatusTag = (template) => {
    const variant = template.is_active ? "success" : "neutral";
    const label = template.is_active ? "Aktywny" : "Wyłączony";
    return `<span class="op-tag ${variant}">${label}</span>`;
  };

  const renderTemplates = () => {
    if (!elements.templatesBody) {
      return;
    }
    if (!state.templates.length) {
      elements.templatesBody.innerHTML = `
        <tr>
          <td colspan="5" class="op-empty">Brak zdefiniowanych szablonów.</td>
        </tr>`;
      return;
    }
    elements.templatesBody.innerHTML = "";
    state.templates.forEach((template) => {
      const tr = document.createElement("tr");
      tr.dataset.templateId = String(template.id);
      const preview = escapeHtml(template.body || "").slice(0, 120);
      const actions = template.editable
        ? `<div class="op-template-actions">
             <button type="button" class="op-ghost small" data-action="edit" data-id="${template.id}">Edytuj</button>
             <button type="button" class="op-ghost small danger" data-action="delete" data-id="${template.id}">Usuń</button>
           </div>`
        : `<span class="op-tag neutral light">Tylko odczyt</span>`;
      tr.innerHTML = `
        <td>
          <strong>${escapeHtml(template.name)}</strong>
        </td>
        <td>${formatStatusTag(template)}</td>
        <td>${formatScope(template.scope)}</td>
        <td><code class="op-template-preview">${preview}${template.body && template.body.length > 120 ? "…" : ""}</code></td>
        <td>${actions}</td>
      `;
      elements.templatesBody.appendChild(tr);
    });
  };

  const loadTemplates = async () => {
    const templates = await fetchJson("/operator/api/sms/templates");
    state.templates = Array.isArray(templates) ? templates : [];
    renderTemplates();
  };

  const toProfilePayload = () => ({
    email: elements.profileEmail.value.trim(),
    first_name: elements.profileFirstName.value.trim() || null,
    last_name: elements.profileLastName.value.trim() || null,
    internal_ext: elements.profileInternalExt.value.trim() || null,
    mobile_phone: elements.profileMobilePhone.value.trim() || null,
  });

  const handleProfileSubmit = async (event) => {
    event.preventDefault();
    setFeedback(elements.profileFeedback, "");
    const payload = toProfilePayload();
    try {
      const updated = await fetchJson("/operator/api/profile", {
        method: "PUT",
        body: JSON.stringify(payload),
      });
      if (updated) {
        state.profile = updated;
        fillProfileForm(updated);
      }
      setFeedback(elements.profileFeedback, "Zapisano dane operatora.", "success");
      showToast("Dane operatora zostały zaktualizowane.", "success");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Nie udało się zapisać danych.";
      setFeedback(elements.profileFeedback, message, "error");
      showToast(message, "error");
    }
  };

  const handlePasswordSubmit = async (event) => {
    event.preventDefault();
    setFeedback(elements.passwordFeedback, "");
    const currentPassword = elements.passwordCurrent.value;
    const newPassword = elements.passwordNew.value;
    const confirmPassword = elements.passwordConfirm.value;
    if (!currentPassword || !newPassword) {
      setFeedback(elements.passwordFeedback, "Uzupełnij wszystkie pola hasła.", "error");
      return;
    }
    if (newPassword !== confirmPassword) {
      setFeedback(elements.passwordFeedback, "Nowe hasło i potwierdzenie muszą być identyczne.", "error");
      return;
    }
    try {
      await fetchWithAuth("/operator/api/profile/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
      });
      elements.passwordCurrent.value = "";
      elements.passwordNew.value = "";
      elements.passwordConfirm.value = "";
      setFeedback(elements.passwordFeedback, "Hasło zostało zmienione. Zaloguj się ponownie na innych urządzeniach.", "success");
      showToast("Hasło zmienione poprawnie.", "success");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Nie udało się zmienić hasła.";
      setFeedback(elements.passwordFeedback, message, "error");
      showToast(message, "error");
    }
  };

  const resetTemplateForm = () => {
    state.editingTemplateId = null;
    if (elements.templateId) {
      elements.templateId.value = "";
    }
    elements.templateName.value = "";
    elements.templateBody.value = "";
    elements.templateActive.checked = true;
    elements.templateSubmit.textContent = "Dodaj szablon";
    elements.templateCancel.hidden = true;
    setFeedback(elements.templateFeedback, "");
  };

  const fillTemplateForm = (template) => {
    state.editingTemplateId = template.id;
    elements.templateId.value = String(template.id);
    elements.templateName.value = template.name || "";
    elements.templateBody.value = template.body || "";
    elements.templateActive.checked = Boolean(template.is_active);
    elements.templateSubmit.textContent = "Zapisz zmiany";
    elements.templateCancel.hidden = false;
    setFeedback(elements.templateFeedback, "");
  };

  const templatePayloadFromForm = () => ({
    name: elements.templateName.value.trim(),
    body: elements.templateBody.value.trim(),
    is_active: Boolean(elements.templateActive.checked),
  });

  const handleTemplateSubmit = async (event) => {
    event.preventDefault();
    setFeedback(elements.templateFeedback, "");
    const payload = templatePayloadFromForm();
    if (!payload.name || !payload.body) {
      setFeedback(elements.templateFeedback, "Uzupełnij nazwę i treść szablonu.", "error");
      return;
    }
    try {
      if (state.editingTemplateId) {
        await fetchJson(`/operator/api/sms/templates/${state.editingTemplateId}`, {
          method: "PUT",
          body: JSON.stringify(payload),
        });
        showToast("Zapisano zmiany szablonu.", "success");
      } else {
        await fetchJson("/operator/api/sms/templates", {
          method: "POST",
          body: JSON.stringify(payload),
        });
        showToast("Dodano nowy szablon.", "success");
      }
      resetTemplateForm();
      await loadTemplates();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Operacja na szablonie nie powiodła się.";
      setFeedback(elements.templateFeedback, message, "error");
      showToast(message, "error");
    }
  };

  const handleTemplateCancel = (event) => {
    event.preventDefault();
    resetTemplateForm();
  };

  const handleTemplateAction = async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }
    const templateId = Number(button.dataset.id || 0);
    if (!templateId) {
      return;
    }
    const template = state.templates.find((item) => item.id === templateId);
    if (!template) {
      return;
    }
    if (button.dataset.action === "edit") {
      fillTemplateForm(template);
      return;
    }
    if (button.dataset.action === "delete") {
      const confirmed = window.confirm(`Czy na pewno usunąć szablon "${template.name}"?`);
      if (!confirmed) {
        return;
      }
      try {
        await fetchWithAuth(`/operator/api/sms/templates/${templateId}`, { method: "DELETE" });
        showToast("Szablon został usunięty.", "success");
        resetTemplateForm();
        await loadTemplates();
      } catch (err) {
        const message = err instanceof Error ? err.message : "Nie udało się usunąć szablonu.";
        showToast(message, "error");
      }
    }
  };

  const bindEvents = () => {
    if (elements.profileForm) {
      elements.profileForm.addEventListener("submit", handleProfileSubmit);
    }
    if (elements.passwordForm) {
      elements.passwordForm.addEventListener("submit", handlePasswordSubmit);
    }
    if (elements.templateForm) {
      elements.templateForm.addEventListener("submit", handleTemplateSubmit);
    }
    if (elements.templateCancel) {
      elements.templateCancel.addEventListener("click", handleTemplateCancel);
    }
    if (elements.templatesBody) {
      elements.templatesBody.addEventListener("click", handleTemplateAction);
    }
  };

  const loadInitialData = async () => {
    try {
      await Promise.all([loadProfile(), loadTemplates()]);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Nie udało się wczytać ustawień.";
      showToast(message, "error");
    }
  };

  const init = () => {
    initElements();
    bindEvents();
    const token = readToken();
    if (!token) {
      handleAuthError();
      return;
    }
    state.token = token;
    loadInitialData();
  };

  document.addEventListener("DOMContentLoaded", init);
})();
