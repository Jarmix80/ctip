(() => {
  const state = {
    calls: [],
    selectedCallId: null,
    user: null,
    filters: {
      search: "",
      direction: "",
    },
    token: null,
    rememberMe: true,
    stats: {
      smsToday: 0,
      smsMonth: 0,
    },
    currentCall: null,
    contactForm: {
      visible: false,
      contactId: null,
    },
    templates: [],
    quickSms: {
      selectedTemplateId: "__custom__",
      confirmBeforeSend: true,
      saveAsTemplate: false,
    },
  };

  const elements = {};

  const TOKEN_KEY = "admin-session-token";
  const SMS_CONFIRM_KEY = "operator-sms-confirm";
  const DEFAULT_TEMPLATES = [
    {
      id: "default-app",
      name: "Aplikacja",
      body:
        "Instrukcja instalacji aplikacji Ksero Partner znajdziesz na stronie https://www.ksero-partner.com.pl/appkp/.",
    },
    {
      id: "default-counter",
      name: "Liczniki",
      body:
        "Dzień dobry! Ksero-Partner przypomina o konieczności podania liczników urządzeń. Prosimy o przesłanie odczytów na adres biuro@ksero-partner.pl.",
    },
  ];

  const readToken = () =>
    window.localStorage?.getItem(TOKEN_KEY) ||
    window.sessionStorage?.getItem(TOKEN_KEY) ||
    null;

  const storeToken = (token, remember) => {
    try {
      window.localStorage?.removeItem(TOKEN_KEY);
      window.sessionStorage?.removeItem(TOKEN_KEY);
      if (!token) {
        return;
      }
      if (remember) {
        window.localStorage?.setItem(TOKEN_KEY, token);
      } else {
        window.sessionStorage?.setItem(TOKEN_KEY, token);
      }
    } catch (err) {
      console.error("Nie udało się zapisać tokenu operatora", err);
    }
  };

  const clearToken = () => {
    storeToken(null, false);
    state.token = null;
  };

  const showLogin = (message = "") => {
    if (elements.loginWrapper) {
      elements.loginWrapper.hidden = false;
    }
    if (elements.shell) {
      elements.shell.hidden = true;
    }
    if (message && elements.loginError) {
      elements.loginError.textContent = message;
    } else if (elements.loginError) {
      elements.loginError.textContent = "";
    }
    if (elements.loginRemember) {
      elements.loginRemember.checked = state.rememberMe;
    }
  };

  const showApp = () => {
    if (elements.loginWrapper) {
      elements.loginWrapper.hidden = true;
    }
    if (elements.shell) {
      elements.shell.hidden = false;
    }
    if (elements.loginError) {
      elements.loginError.textContent = "";
    }
    renderSmsStats();
  };

  const loadQuickSmsPreferences = () => {
    try {
      const storedConfirm = window.localStorage?.getItem(SMS_CONFIRM_KEY);
      if (storedConfirm !== null) {
        state.quickSms.confirmBeforeSend = storedConfirm === "true";
      }
    } catch (err) {
      console.warn("Nie udało się odczytać preferencji SMS", err);
    }
  };

  const persistConfirmPreference = (value) => {
    try {
      window.localStorage?.setItem(SMS_CONFIRM_KEY, value ? "true" : "false");
    } catch (err) {
      console.warn("Nie udało się zapisać preferencji SMS", err);
    }
  };

  const fetchJson = async (url, options = {}) => {
    const token = state.token || readToken();
    if (!token) {
      clearToken();
      showLogin();
      throw new Error("Sesja wygasła. Zaloguj się ponownie.");
    }
    state.token = token;
    const headers = options.headers ? { ...options.headers } : {};
    headers["X-Admin-Session"] = token;
    if (options.body && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(url, { ...options, headers });
    if (response.status === 401 || response.status === 403) {
      clearToken();
      showLogin("Sesja wygasła. Zaloguj się ponownie.");
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
        // noop
      }
      throw new Error(message);
    }
    return response.json();
  };

  const formatDateTime = (value) => {
    if (!value) {
      return "—";
    }
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
  };

  const formatDuration = (seconds) => {
    if (seconds == null) {
      return "—";
    }
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  };

  const digitsOnly = (value) => (value || "").replace(/\D+/g, "");

  const formatPhoneDisplay = (value) => {
    const digits = digitsOnly(value);
    if (!digits) {
      return value || "—";
    }
    if (digits.length <= 3) {
      return digits;
    }
    return digits.slice(-9);
  };

  const normalizeSmsDestination = (value) => {
    if (!value) {
      return "";
    }
    const trimmed = value.trim();
    if (trimmed.startsWith("+") && !trimmed.startsWith("+0")) {
      return trimmed;
    }
    let digits = digitsOnly(trimmed);
    if (digits.startsWith("000")) {
      digits = digits.slice(1);
    }
    if (digits.startsWith("0") && !digits.startsWith("00") && digits.length >= 10) {
      digits = digits.slice(1);
    }
    if (digits.startsWith("00")) {
      digits = digits.slice(2);
    }
    if (digits.length === 9) {
      digits = `48${digits}`;
    }
    if (digits.length >= 9) {
      return `+${digits}`;
    }
    return trimmed;
  };

  const normalizeContactNumber = (value) => {
    const digits = digitsOnly(value);
    if (!digits) {
      return "";
    }
    if (digits.length > 9) {
      return digits.slice(-9);
    }
    return digits;
  };

  const DISPOSITION_LABELS = {
    ANSWERED: "Odebrane",
    NO_ANSWER: "Nieodebrane",
    BUSY: "Zajęte",
    FAILED: "Błąd połączenia",
    UNKNOWN: "Nieznany",
  };

  const DIRECTION_LABELS = {
    IN: "Przychodzące",
    OUT: "Wychodzące",
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
    }, 3000);
  };

  const renderUser = () => {
    if (!state.user) {
      return;
    }
    elements.userName.textContent = [state.user.first_name, state.user.last_name].filter(Boolean).join(" ") || "Operator";
    elements.userEmail.textContent = state.user.email || "—";
  };

  const renderSmsStats = () => {
    if (!elements.smsStatsToday || !elements.smsStatsMonth) {
      return;
    }
    elements.smsStatsToday.textContent = String(state.stats.smsToday ?? 0);
    elements.smsStatsMonth.textContent = String(state.stats.smsMonth ?? 0);
  };

  const setContactFeedback = (message, variant = "info") => {
    if (!elements.contactFeedback) {
      return;
    }
    elements.contactFeedback.textContent = message || "";
    elements.contactFeedback.className = `op-form-hint${message ? ` ${variant}` : ""}`;
  };

  const fillContactForm = (contact) => {
    if (!elements.contactForm) {
      return;
    }
    const callNumber = state.currentCall?.number;
    const preferred = contact?.number || callNumber || "";
    elements.contactFormNumber.value = normalizeContactNumber(preferred);
    elements.contactFormExt.value = contact?.ext || state.currentCall?.ext || "";
    elements.contactFormFirstName.value = contact?.first_name || "";
    elements.contactFormLastName.value = contact?.last_name || "";
    elements.contactFormCompany.value = contact?.company || "";
    elements.contactFormEmail.value = contact?.email || "";
    elements.contactFormFirebird.value = contact?.firebird_id || "";
    elements.contactFormNip.value = contact?.nip || "";
    elements.contactFormNotes.value = contact?.notes || "";
  };

  const toggleContactForm = (visible) => {
    if (visible && elements.contactEditToggle?.disabled) {
      return;
    }
    state.contactForm.visible = visible;
    if (!elements.contactForm) {
      return;
    }
    elements.contactForm.hidden = !visible;
    if (elements.contactEditToggle) {
      elements.contactEditToggle.textContent = visible ? "Zamknij edycję" : "Dodaj/edytuj kontakt";
    }
    if (visible) {
      fillContactForm(state.currentCall?.contact);
      setContactFeedback("");
    }
  };

  const renderCalls = () => {
    const tbody = elements.callListBody;
    if (!tbody) {
      return;
    }
    if (!state.calls.length) {
      tbody.innerHTML = '<tr><td colspan="6" class="op-empty">Brak danych – spróbuj zmienić filtr.</td></tr>';
      return;
    }
    tbody.innerHTML = "";
    state.calls.forEach((call) => {
      const tr = document.createElement("tr");
      tr.dataset.callId = String(call.id);
      if (call.id === state.selectedCallId) {
        tr.classList.add("op-selected");
      }
      const displayNumber = formatPhoneDisplay(call.number);
      const dispositionLabel = DISPOSITION_LABELS[call.disposition] || call.disposition || "—";
      const directionLabel = DIRECTION_LABELS[call.direction] || call.direction || "—";
      tr.innerHTML = `
        <td>${formatDateTime(call.started_at)}</td>
        <td>${directionLabel}</td>
        <td>${displayNumber}</td>
        <td>${call.ext}</td>
        <td>${dispositionLabel}</td>
        <td>${formatDuration(call.duration_s)}</td>
      `;
      tr.addEventListener("click", () => {
        if (state.selectedCallId !== call.id) {
          state.selectedCallId = call.id;
          state.currentCall = call;
          renderCalls();
          loadCallDetail(call.id).catch((err) => showToast(err.message, "error"));
        }
      });
      tbody.appendChild(tr);
    });
  };

  const renderContactCard = (contact) => {
    if (!contact) {
      elements.contactStatus.textContent = "Brak danych";
      elements.contactStatus.className = "op-tag neutral";
      elements.contactName.textContent = "—";
      elements.contactCompany.textContent = "—";
      if (elements.contactNumber) {
        elements.contactNumber.textContent = "—";
      }
      elements.contactEmail.textContent = "—";
      elements.contactFirebird.textContent = "—";
      if (elements.contactNip) {
        elements.contactNip.textContent = "—";
      }
      elements.contactNotes.textContent = "—";
      state.contactForm.contactId = null;
      if (state.contactForm.visible) {
        fillContactForm(null);
      }
      return;
    }
    const fullName = [contact.first_name, contact.last_name].filter(Boolean).join(" ") || "—";
    elements.contactStatus.textContent = "Kontakt";
    elements.contactStatus.className = "op-tag success";
    elements.contactName.textContent = fullName;
    elements.contactCompany.textContent = contact.company || "—";
    if (elements.contactNumber) {
      elements.contactNumber.textContent = formatPhoneDisplay(contact.number) || "—";
    }
    elements.contactEmail.textContent = contact.email || "—";
    elements.contactFirebird.textContent = contact.firebird_id || "—";
    if (elements.contactNip) {
      elements.contactNip.textContent = contact.nip || "—";
    }
    elements.contactNotes.textContent = contact.notes || "—";
    state.contactForm.contactId = contact.id || null;
    if (state.contactForm.visible) {
      fillContactForm(contact);
    }
  };

  const renderTimeline = (events) => {
    const list = elements.timelineList;
    if (!list) {
      return;
    }
    if (!events.length) {
      list.innerHTML = '<li class="op-empty">Brak zdarzeń dla wybranego połączenia.</li>';
      return;
    }
    list.innerHTML = "";
    events.forEach((event) => {
      const li = document.createElement("li");
      li.innerHTML = `
        <strong>${formatDateTime(event.ts)} • ${event.typ}</strong>
        <span>${event.ext ? `Wewnętrzny: ${event.ext}` : ""} ${event.number ? `Numer: ${event.number}` : ""}</span>
        ${event.payload ? `<code>${event.payload}</code>` : ""}
      `;
      list.appendChild(li);
    });
  };

  const renderSmsHistory = (entries) => {
    const tbody = elements.smsHistoryBody;
    if (!tbody) {
      return;
    }
    if (!entries.length) {
      tbody.innerHTML = '<tr><td colspan="3" class="op-empty">Brak wysłanych SMS dla tego numeru.</td></tr>';
      return;
    }
    tbody.innerHTML = "";
    entries.forEach((sms) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${formatDateTime(sms.created_at)}</td>
        <td>${sms.status}</td>
        <td>${sms.text}</td>
      `;
    tbody.appendChild(tr);
    });
  };

  const getQuickTemplateButtons = () => {
    const activeTemplates = (state.templates || []).filter((tpl) => tpl.is_active);
    const userTemplates = activeTemplates.map((tpl) => ({
      id: `tpl-${tpl.id}`,
      templateId: tpl.id,
      name: tpl.name,
      body: tpl.body,
    }));
    return [
      { id: "__custom__", name: "Własna wiadomość", body: "", isCustom: true },
      ...DEFAULT_TEMPLATES,
      ...userTemplates,
    ];
  };

  const renderSmsTemplates = () => {
    if (!elements.smsTemplateList) {
      return;
    }
    const buttons = getQuickTemplateButtons();
    elements.smsTemplateList.innerHTML = "";
    const group = document.createElement("div");
    group.className = "op-template-button-group";
    buttons.forEach((button) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "op-template-button";
      if (state.quickSms.selectedTemplateId === button.id) {
        btn.classList.add("selected");
      }
      btn.dataset.templateId = button.id;
      if (button.templateId != null) {
        btn.dataset.apiId = String(button.templateId);
      }
      btn.textContent = button.name;
      btn.title = button.body ? button.body : "Własna wiadomość";
      group.appendChild(btn);
    });
    elements.smsTemplateList.appendChild(group);
    const hasUserTemplates = (state.templates || []).some((tpl) => tpl.is_active);
    if (!hasUserTemplates) {
      const info = document.createElement("p");
      info.className = "op-sms-template-info";
      info.textContent = "Brak aktywnych szablonów operatora – dodaj je w ustawieniach.";
      elements.smsTemplateList.appendChild(info);
    }
  };

  const applyTemplateButton = (button) => {
    state.quickSms.selectedTemplateId = button.id;
    if (button.isCustom) {
      if (elements.smsSaveTemplate) {
        elements.smsSaveTemplate.checked = false;
      }
      state.quickSms.saveAsTemplate = false;
      if (elements.smsFeedback) {
        elements.smsFeedback.textContent = "Wpisz treść własnej wiadomości.";
        elements.smsFeedback.className = "op-form-hint";
      }
      if (elements.smsText) {
        elements.smsText.focus();
      }
    } else {
      if (elements.smsText) {
        elements.smsText.value = button.body || "";
        elements.smsText.focus();
      }
      if (elements.smsFeedback) {
        elements.smsFeedback.textContent = `Załadowano szablon „${button.name}”.`;
        elements.smsFeedback.className = "op-form-hint info";
      }
      if (elements.smsSaveTemplate) {
        elements.smsSaveTemplate.checked = false;
      }
      state.quickSms.saveAsTemplate = false;
    }
    renderSmsTemplates();
  };

  const handleTemplateListClick = (event) => {
    const button = event.target.closest("button[data-template-id]");
    if (!button) {
      return;
    }
    const templateId = button.dataset.templateId;
    const templates = getQuickTemplateButtons();
    const entry = templates.find((tpl) => tpl.id === templateId);
    if (!entry) {
      return;
    }
    applyTemplateButton(entry);
  };

  const loadSmsTemplates = async () => {
    try {
      const data = await fetchJson("/operator/api/sms/templates");
      state.templates = Array.isArray(data) ? data : [];
    } catch (err) {
      console.warn("Nie udało się pobrać szablonów SMS", err);
      state.templates = [];
    }
    renderSmsTemplates();
  };

  const saveCustomTemplate = async (text) => {
    const trimmed = text.trim();
    if (!trimmed) {
      return;
    }
    const exists = (state.templates || []).some(
      (tpl) => tpl.is_active && tpl.body.trim() === trimmed
    );
    if (exists) {
      showToast("Taki szablon już istnieje – pomijam zapis.", "info");
      return;
    }
    let name = window.prompt("Podaj nazwę szablonu SMS", "Nowy szablon");
    if (!name) {
      return;
    }
    name = name.trim();
    if (!name) {
      return;
    }
    try {
      await fetchJson("/operator/api/sms/templates", {
        method: "POST",
        body: JSON.stringify({ name, body: trimmed, is_active: true }),
      });
      showToast("Szablon został zapisany.", "success");
      await loadSmsTemplates();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Nie udało się zapisać szablonu.";
      showToast(message, "error");
    }
  };

  const loadCalls = async () => {
    const params = new URLSearchParams();
    params.set("limit", "50");
    if (state.filters.search) {
      params.set("search", state.filters.search);
    }
    if (state.filters.direction) {
      params.set("direction", state.filters.direction);
    }
    const data = await fetchJson(`/operator/api/calls?${params.toString()}`);
    state.calls = data || [];
    renderCalls();
    if (state.calls.length && !state.selectedCallId) {
      state.selectedCallId = state.calls[0].id;
      renderCalls();
      await loadCallDetail(state.selectedCallId);
    } else if (!state.calls.length) {
      state.selectedCallId = null;
      renderContactCard(null);
      renderTimeline([]);
      renderSmsHistory([]);
      elements.smsDest.value = "";
      elements.smsFeedback.textContent = "";
      if (elements.contactEditToggle) {
        elements.contactEditToggle.disabled = true;
      }
    }
  };

  const loadCallDetail = async (id) => {
    const detail = await fetchJson(`/operator/api/calls/${id}`);
    if (!detail) {
      return;
    }
    state.currentCall = detail.call;
    state.currentCall.contact = detail.contact || null;
    const hasExternalNumber = digitsOnly(detail.call.number || "").length >= 5;
    if (elements.contactEditToggle) {
      elements.contactEditToggle.disabled = !hasExternalNumber;
    }
    renderContactCard(detail.contact);
    renderTimeline(detail.events);
    renderSmsHistory(detail.sms_history);
    elements.smsDest.value = hasExternalNumber ? normalizeSmsDestination(detail.call.number || "") : "";
    elements.smsFeedback.textContent = "";
    elements.smsFeedback.className = "op-form-hint";
    state.contactForm.contactId = detail.contact?.id || null;
    if (state.contactForm.visible) {
      fillContactForm(detail.contact);
    }
  };

  const handleSmsSubmit = async (event) => {
    event.preventDefault();
    const dest = normalizeSmsDestination(elements.smsDest.value.trim());
    const text = elements.smsText.value.trim();
    if (!dest || !text) {
      elements.smsFeedback.textContent = "Uzupełnij numer i treść wiadomości.";
      elements.smsFeedback.className = "op-form-hint error";
      return;
    }
    if (elements.smsConfirmToggle) {
      state.quickSms.confirmBeforeSend = Boolean(elements.smsConfirmToggle.checked);
    }
    if (elements.smsSaveTemplate) {
      state.quickSms.saveAsTemplate = Boolean(elements.smsSaveTemplate.checked);
    }
    if (state.quickSms.confirmBeforeSend) {
      const preview = text.length > 280 ? `${text.slice(0, 280)}…` : text;
      const confirmed = window.confirm(
        `Czy na pewno wysłać SMS na numer ${dest}?\n\n${preview}`
      );
      if (!confirmed) {
        elements.smsFeedback.textContent = "Wysyłka anulowana.";
        elements.smsFeedback.className = "op-form-hint";
        return;
      }
    }
    try {
      await fetchJson("/operator/api/sms/send", {
        method: "POST",
        body: JSON.stringify({
          dest,
          text,
          call_id: state.selectedCallId,
          origin: "operator",
        }),
      });
      if (state.quickSms.saveAsTemplate) {
        await saveCustomTemplate(text);
        state.quickSms.saveAsTemplate = false;
        if (elements.smsSaveTemplate) {
          elements.smsSaveTemplate.checked = false;
        }
      }
      elements.smsFeedback.textContent = "Wiadomość dodana do kolejki.";
      elements.smsFeedback.className = "op-form-hint success";
      if (state.quickSms.selectedTemplateId === "__custom__") {
        elements.smsText.value = "";
      }
      await updateSmsStats();
      if (state.selectedCallId) {
        await loadCallDetail(state.selectedCallId);
      } else {
        const history = await fetchJson(`/operator/api/sms/history?number=${encodeURIComponent(dest)}&limit=20`);
        renderSmsHistory(history || []);
      }
      renderSmsTemplates();
    } catch (err) {
      elements.smsFeedback.textContent = err.message;
      elements.smsFeedback.className = "op-form-hint error";
      showToast(err.message, "error");
    }
  };

  const gatherContactPayload = () => ({
    number: normalizeContactNumber(elements.contactFormNumber?.value?.trim() || ""),
    ext: elements.contactFormExt?.value?.trim() || null,
    first_name: elements.contactFormFirstName?.value?.trim() || null,
    last_name: elements.contactFormLastName?.value?.trim() || null,
    company: elements.contactFormCompany?.value?.trim() || null,
    email: elements.contactFormEmail?.value?.trim() || null,
    firebird_id: elements.contactFormFirebird?.value?.trim() || null,
    nip: elements.contactFormNip?.value?.trim() || null,
    notes: elements.contactFormNotes?.value?.trim() || null,
  });

  const handleContactSubmit = async () => {
    if (!state.currentCall?.number) {
      throw new Error("Brak numeru zewnętrznego – nie można zapisać kontaktu.");
    }
    const payload = gatherContactPayload();
    payload.number = normalizeContactNumber(state.currentCall.number || payload.number);
    if (!payload.number) {
      throw new Error("Nie można zapisać kontaktu bez numeru.");
    }
    if (payload.number.length < 5) {
      throw new Error("Numer kontaktu musi mieć co najmniej 5 cyfr.");
    }

    const endpoint =
      state.contactForm.contactId != null
        ? `/operator/api/contacts/${state.contactForm.contactId}`
        : "/operator/api/contacts";
    const method = state.contactForm.contactId != null ? "PUT" : "POST";

    const saved = await fetchJson(endpoint, {
      method,
      body: JSON.stringify(payload),
    });

    showToast("Kontakt zapisany.", "success");
    setContactFeedback("Kontakt zapisany.", "success");
    renderContactCard(saved);
    state.contactForm.contactId = saved.id || null;
    if (state.currentCall) {
      state.currentCall.contact = saved;
    }
    toggleContactForm(false);
    await loadCalls();
  };

  const updateSmsStats = async () => {
    try {
      const stats = await fetchJson("/operator/api/stats");
      state.stats.smsToday = stats?.sms_today ?? 0;
      state.stats.smsMonth = stats?.sms_month ?? 0;
      renderSmsStats();
    } catch (err) {
      console.warn("Nie udało się pobrać statystyk SMS", err);
    }
  };

  const handleLogout = async () => {
    const token = state.token || readToken();
    if (token) {
      await fetch("/operator/auth/logout", {
        method: "POST",
        headers: { "X-Admin-Session": token },
      }).catch(() => {});
    }
    clearToken();
    state.user = null;
    state.calls = [];
    renderCalls();
    renderContactCard(null);
    renderTimeline([]);
    renderSmsHistory([]);
    showLogin();
  };

  const initElements = () => {
    elements.loginWrapper = document.getElementById("op-login-wrapper");
    elements.loginForm = document.getElementById("op-login-form");
    elements.loginEmail = document.getElementById("operator-email");
    elements.loginPassword = document.getElementById("operator-password");
    elements.loginRemember = document.getElementById("operator-remember");
    elements.loginError = document.getElementById("operator-login-error");
    elements.shell = document.getElementById("op-shell");
    elements.userName = document.getElementById("op-user-name");
    elements.userEmail = document.getElementById("op-user-email");
    elements.callListBody = document.getElementById("call-list-body");
    elements.timelineList = document.getElementById("timeline-list");
    elements.smsHistoryBody = document.getElementById("sms-history-body");
    elements.contactStatus = document.getElementById("contact-status-tag");
    elements.contactName = document.getElementById("contact-name");
    elements.contactCompany = document.getElementById("contact-company");
    elements.contactEmail = document.getElementById("contact-email");
    elements.contactFirebird = document.getElementById("contact-firebird");
    elements.contactNip = document.getElementById("contact-nip");
    elements.contactNotes = document.getElementById("contact-notes");
    elements.contactEditToggle = document.getElementById("contact-edit-toggle");
    elements.contactForm = document.getElementById("contact-form");
    elements.contactFormNumber = document.getElementById("contact-form-number");
    elements.contactFormExt = document.getElementById("contact-form-ext");
    elements.contactFormFirstName = document.getElementById("contact-form-first-name");
    elements.contactFormLastName = document.getElementById("contact-form-last-name");
    elements.contactFormCompany = document.getElementById("contact-form-company");
    elements.contactFormEmail = document.getElementById("contact-form-email");
    elements.contactFormFirebird = document.getElementById("contact-form-firebird");
    elements.contactFormNip = document.getElementById("contact-form-nip");
    elements.contactFormNotes = document.getElementById("contact-form-notes");
    elements.contactFeedback = document.getElementById("contact-feedback");
    if (elements.contactForm) {
      elements.contactForm.hidden = true;
    }
    if (elements.contactEditToggle) {
      elements.contactEditToggle.disabled = true;
    }
    elements.smsForm = document.getElementById("quick-sms-form");
    elements.smsDest = document.getElementById("sms-dest");
    elements.smsText = document.getElementById("sms-text");
    elements.smsFeedback = document.getElementById("sms-feedback");
    elements.smsTemplateList = document.getElementById("sms-template-list");
    elements.smsConfirmToggle = document.getElementById("sms-confirm-toggle");
    elements.smsSaveTemplate = document.getElementById("sms-save-template");
    elements.filterForm = document.getElementById("op-filter-form");
    elements.filterSearch = document.getElementById("filter-search");
    elements.filterDirection = document.getElementById("filter-direction");
    elements.filterClear = document.getElementById("filter-clear");
    elements.openSettings = document.getElementById("op-open-settings");
    elements.logout = document.getElementById("op-logout");
    elements.smsStatsToday = document.getElementById("sms-stats-today");
    elements.smsStatsMonth = document.getElementById("sms-stats-month");
    if (elements.smsConfirmToggle) {
      elements.smsConfirmToggle.checked = state.quickSms.confirmBeforeSend;
    }
    if (elements.smsSaveTemplate) {
      elements.smsSaveTemplate.checked = state.quickSms.saveAsTemplate;
    }
  };

  const bindEvents = () => {
    if (elements.loginForm) {
      elements.loginForm.addEventListener("submit", (event) => {
        event.preventDefault();
        handleLoginSubmit().catch((err) => {
          showToast(err.message, "error");
        });
      });
    }
    if (elements.smsForm) {
      elements.smsForm.addEventListener("submit", (event) => {
        handleSmsSubmit(event).catch((err) => showToast(err.message, "error"));
      });
    }
    if (elements.smsTemplateList) {
      elements.smsTemplateList.addEventListener("click", handleTemplateListClick);
    }
    if (elements.smsConfirmToggle) {
      elements.smsConfirmToggle.addEventListener("change", (event) => {
        state.quickSms.confirmBeforeSend = Boolean(event.target.checked);
        persistConfirmPreference(state.quickSms.confirmBeforeSend);
      });
    }
    if (elements.smsSaveTemplate) {
      elements.smsSaveTemplate.addEventListener("change", (event) => {
        state.quickSms.saveAsTemplate = Boolean(event.target.checked);
      });
    }
    if (elements.smsText) {
      elements.smsText.addEventListener("input", () => {
        state.quickSms.selectedTemplateId = "__custom__";
        renderSmsTemplates();
      });
    }
    if (elements.filterForm) {
      elements.filterForm.addEventListener("submit", (event) => {
        event.preventDefault();
        state.filters.search = elements.filterSearch.value.trim();
        state.filters.direction = elements.filterDirection.value;
        loadCalls().catch((err) => showToast(err.message, "error"));
      });
    }
    if (elements.filterClear) {
      elements.filterClear.addEventListener("click", () => {
        elements.filterSearch.value = "";
        elements.filterDirection.value = "";
        state.filters.search = "";
        state.filters.direction = "";
        loadCalls().catch((err) => showToast(err.message, "error"));
      });
    }
    if (elements.logout) {
      elements.logout.addEventListener("click", handleLogout);
    }
    if (elements.openSettings) {
      elements.openSettings.addEventListener("click", () => {
        window.location.href = "/operator/settings";
      });
    }
    if (elements.contactEditToggle) {
      elements.contactEditToggle.addEventListener("click", () => {
        if (!state.currentCall?.number) {
          showToast("Wybierz połączenie z numerem zewnętrznym, aby edytować kontakt.", "info");
          return;
        }
        toggleContactForm(!state.contactForm.visible);
      });
    }
    if (elements.contactForm) {
      elements.contactForm.addEventListener("submit", (event) => {
        event.preventDefault();
        handleContactSubmit().catch((err) => {
          const message = err instanceof Error ? err.message : String(err);
          setContactFeedback(message, "error");
          showToast(message, "error");
        });
      });
    }
  };

  const loadInitialData = async () => {
    try {
      state.user = await fetchJson("/operator/api/me");
      renderUser();
      await Promise.all([loadSmsTemplates(), updateSmsStats()]);
      await loadCalls();
      showApp();
    } catch (err) {
      clearToken();
      showLogin(err.message);
      showToast(err.message, "error");
    }
  };

  const handleLoginSubmit = async () => {
    const email = elements.loginEmail.value.trim();
    const password = elements.loginPassword.value;
    state.rememberMe = Boolean(elements.loginRemember?.checked);
    if (!email || !password) {
      showLogin("Podaj login i hasło.");
      return;
    }
    try {
      const response = await fetch("/operator/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password, remember_me: state.rememberMe }),
      });
      if (!response.ok) {
        const detail = await response.json().catch(() => ({}));
        throw new Error(detail?.detail || "Nieudane logowanie");
      }
      const data = await response.json();
      state.token = data.token;
      storeToken(state.token, state.rememberMe);
      showApp();
      await loadInitialData();
    } catch (err) {
      const message = err instanceof Error ? err.message : "Błąd logowania";
      showLogin(message);
      throw err;
    }
  };

  const init = async () => {
    loadQuickSmsPreferences();
    initElements();
    renderSmsTemplates();
    bindEvents();
    const token = readToken();
    if (token) {
      state.token = token;
      state.rememberMe = Boolean(window.localStorage?.getItem(TOKEN_KEY));
      await loadInitialData();
    } else {
      showLogin();
    }
  };

  document.addEventListener("DOMContentLoaded", init);
})();
