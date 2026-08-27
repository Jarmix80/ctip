const ADMIN_TOKEN_KEY = "admin-session-token";

function readAdminToken() {
  return (
    window.localStorage?.getItem(ADMIN_TOKEN_KEY) ||
    window.sessionStorage?.getItem(ADMIN_TOKEN_KEY) ||
    null
  );
}

function storeAdminToken(token, remember) {
  try {
    window.localStorage?.removeItem(ADMIN_TOKEN_KEY);
    window.sessionStorage?.removeItem(ADMIN_TOKEN_KEY);
    if (!token) {
      return;
    }
    if (remember) {
      window.localStorage?.setItem(ADMIN_TOKEN_KEY, token);
    } else {
      window.sessionStorage?.setItem(ADMIN_TOKEN_KEY, token);
    }
    window.dispatchEvent(new Event("ctip:session-changed"));
  } catch (err) {
    console.error("Nie udało się zapisać tokenu", err);
  }
}

function initHtmxHeaders(token) {
  if (window.htmx) {
    if (!htmx.config.headers) {
      htmx.config.headers = {};
    }
    if (token) {
      htmx.config.headers["X-Admin-Session"] = token;
    } else {
      delete htmx.config.headers["X-Admin-Session"];
    }
  }
}

document.addEventListener("htmx:configRequest", (event) => {
  const token = readAdminToken();
  if (token) {
    event.detail.headers["X-Admin-Session"] = token;
  }
});

function showToast(message, variant = "info") {
  const containerId = "toast-container";
  let container = document.getElementById(containerId);
  if (!container) {
    container = document.createElement("div");
    container.id = containerId;
    container.className = "toast-container";
    document.body.appendChild(container);
  }
  const toast = document.createElement("div");
  toast.className = "toast";
  toast.textContent = message;
  if (variant === "success") {
    toast.style.borderLeftColor = "#2e7d32";
  } else if (variant === "warning") {
    toast.style.borderLeftColor = "#c47f1d";
  } else if (variant === "error") {
    toast.style.borderLeftColor = "#c62828";
  }
  container.appendChild(toast);
  requestAnimationFrame(() => {
    toast.classList.add("visible");
  });
  setTimeout(() => {
    toast.classList.remove("visible");
    toast.addEventListener(
      "transitionend",
      () => toast.remove(),
      { once: true },
    );
  }, 3500);
}

document.addEventListener("alpine:init", () => {
  const adminApp = () => ({
    token: null,
    loggedIn: false,
    section: localStorage.getItem("admin-section") || "dashboard",
    user: null,
    email: "",
    password: "",
    rememberMe: true,
    loading: false,
    error: null,
    pendingAction: null,
    pendingScrollTarget: null,
    ctipStream: {
      socket: null,
      reconnectTimer: null,
      filter: { ext: null, limit: 50 },
    },
    diagnosticsOpen: false,
    diagnosticsLoading: false,
    diagnosticsTitle: "",
    diagnosticsError: null,
    diagnosticsPayload: null,

    init() {
      const storedToken = readAdminToken();
      const persisted = Boolean(window.localStorage?.getItem(ADMIN_TOKEN_KEY));
      window.AdminPanel = {
        handleCardAction: (action) => this.handleCardAction(action),
        reloadSection: (name) => this.loadSection(name, this.resolveSectionUrl(name)),
        loadSection: (name, url) => this.loadSection(name, url || this.resolveSectionUrl(name)),
        openDiagnostics: (endpoint, title) => this.openDiagnostics(endpoint, title),
        applyCtipFilter: (event) => this.applyCtipFilter(event),
        openCtipContactEditor: (payload) => this.openCtipContactEditor(payload),
        handleCtipContactButton: (button) => this.handleCtipContactButton(button),
      };
      if (storedToken) {
        this.applyToken(storedToken, persisted);
        this.fetchCurrentUser();
      }
    },

    applyToken(token, remember = true) {
      this.token = token;
      storeAdminToken(token, remember);
      initHtmxHeaders(token);
    },

    clearState() {
      this.stopCtipStream();
      this.loggedIn = false;
      this.user = null;
      this.section = "dashboard";
      this.email = "";
      this.password = "";
      this.error = null;
      this.pendingAction = null;
      this.closeDiagnostics();
      this.applyToken(null);
      localStorage.removeItem("admin-section");
    },

    get isAdmin() {
      return this.user?.role === "admin";
    },

    canAccessSection(name) {
      if (this.isAdmin) {
        return true;
      }
      const allowed = new Set(["dashboard", "ctip", "contacts"]);
      return allowed.has(name);
    },

    async fetchCurrentUser() {
      if (!this.token) {
        this.clearState();
        return;
      }
      try {
        const response = await fetch("/admin/auth/me", {
          headers: {
            "X-Admin-Session": this.token,
          },
        });
        if (!response.ok) {
          throw new Error("Sesja wygasła");
        }
        this.user = await response.json();
        this.loggedIn = true;
        this.error = null;
        if (!this.canAccessSection(this.section)) {
          this.section = "dashboard";
          localStorage.setItem("admin-section", "dashboard");
        }
        this.loadSection(this.section, this.resolveSectionUrl(this.section));
      } catch (err) {
        console.error(err);
        this.clearState();
      }
    },

    resolveSectionUrl(name) {
      const buildUrl = (title, description = null) => {
        const params = new URLSearchParams();
        if (title) {
          params.set("title", title);
        }
        if (description) {
          params.set("description", description);
        }
        const qs = params.toString();
        return qs ? `/admin/partials/coming-soon?${qs}` : "/admin/partials/coming-soon";
      };

      switch (name) {
        case "dashboard":
          return "/admin/partials/dashboard";
        case "database":
          return "/admin/partials/config/database";
        case "kp-repair":
          return "/admin/partials/kp-repair";
        case "firebird":
          return "/admin/partials/config/firebird";
        case "backups":
          return "/admin/partials/backups";
        case "ctip":
        case "ctip-live":
          return "/admin/partials/ctip/live";
        case "ctip-config":
          return "/admin/partials/config/ctip";
        case "email":
          return "/admin/partials/config/email";
        case "form-handling":
          return "/admin/partials/config/form-handling";
        case "sms":
          return "/admin/partials/config/sms";
        case "call-sms":
          return "/admin/partials/call-sms";
        case "sms-history":
          return "/admin/partials/sms/history";
        case "contacts":
          return "/admin/partials/contacts";
        case "bot-identities":
          return "/admin/partials/bot-identities";
        case "sql":
          return buildUrl(
            "Konsola SQL",
            "Sandbox SELECT zostanie dodany w kolejnym etapie.",
          );
        case "users":
          return "/admin/partials/users";
        default:
          return "/admin/partials/coming-soon";
      }
    },

    async loadSection(name, url) {
      if (!this.loggedIn) {
        return;
      }
      const previousSection = this.section;
      if (!this.canAccessSection(name)) {
        showToast("Brak uprawnień do tej sekcji", "warning");
        return;
      }
      this.section = name;
      localStorage.setItem("admin-section", name);
      const target = document.querySelector("#admin-content");
      if (!target) {
        return;
      }
      if (previousSection === "ctip" && name !== "ctip") {
        this.stopCtipStream();
      }
      target.innerHTML = '<div class="placeholder"><strong>Wczytywanie…</strong></div>';
      try {
        const headers = new Headers();
        headers.set("HX-Request", "true");
        headers.set("HX-Target", "admin-content");
        if (this.token) {
          headers.set("X-Admin-Session", this.token);
        }
        const response = await fetch(url, { headers });
        if (response.status === 401 || response.status === 403) {
          this.clearState();
          showToast("Sesja administratora wygasła", "warning");
          throw new Error("Sesja administratora wygasła");
        }
        if (!response.ok) {
          let message = `Błąd HTTP ${response.status}`;
          const contentType = response.headers.get("content-type") || "";
          try {
            if (contentType.includes("application/json")) {
              const data = await response.json();
              if (data?.detail) {
                message = data.detail;
              }
            } else {
              const text = await response.text();
              if (text) {
                message = text.slice(0, 240);
              }
            }
          } catch (parseErr) {
            console.error("Nie udało się odczytać treści błędu", parseErr);
          }
          throw new Error(message);
        }
        const html = await response.text();
        const executeEmbeddedScripts = (container) => {
          const scripts = container.querySelectorAll("script");
          scripts.forEach((oldScript) => {
            const newScript = document.createElement("script");
            for (const attr of oldScript.attributes) {
              newScript.setAttribute(attr.name, attr.value);
            }
            if (oldScript.textContent) {
              newScript.textContent = oldScript.textContent;
            }
            oldScript.replaceWith(newScript);
          });
        };
        const processTree = () => {
          const template = document.createElement("template");
          template.innerHTML = html;
          target.replaceChildren(template.content.cloneNode(true));
          executeEmbeddedScripts(target);
          if (window.Alpine) {
            if (typeof Alpine.flushAndStopDeferringMutations === "function") {
              Alpine.flushAndStopDeferringMutations();
            }
            Alpine.initTree(target);
            if (typeof Alpine.restartObservingMutations === "function") {
              Alpine.restartObservingMutations();
            }
          }
          if (window.htmx) {
            htmx.process(target);
          }
        };

        if (window.Alpine && typeof Alpine.mutateDom === "function") {
          Alpine.mutateDom(processTree);
        } else {
          processTree();
        }
        document.dispatchEvent(
          new CustomEvent("admin:section-loaded", { detail: { name } }),
        );
        this.onSectionLoaded(name);
        if (this.pendingAction === "test-database" && name === "database") {
          document.dispatchEvent(new CustomEvent("admin:database-test"));
          this.pendingAction = null;
        }
      } catch (err) {
        console.error(err);
        target.innerHTML = '<div class="placeholder"><strong>Błąd ładowania sekcji</strong><p>Sprawdź połączenie i spróbuj ponownie.</p></div>';
        const message = err instanceof Error ? err.message : "Błąd ładowania sekcji";
        showToast(message, "error");
      }
    },

    async login(event) {
      event.preventDefault();
      if (this.loading) {
        return;
      }
      this.loading = true;
      this.error = null;
      try {
        const response = await fetch("/admin/auth/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email: this.email,
            password: this.password,
            remember_me: this.rememberMe,
          }),
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail?.detail || "Nieudane logowanie");
        }
        const data = await response.json();
        this.applyToken(data.token, this.rememberMe);
        await this.fetchCurrentUser();
        showToast("Zalogowano pomyślnie", "success");
      } catch (err) {
        this.error = err instanceof Error ? err.message : "Błąd logowania";
        this.applyToken(null);
      } finally {
        this.loading = false;
      }
    },

    async logout() {
      try {
        if (this.token) {
          await fetch("/admin/auth/logout", {
            method: "POST",
            headers: {
              "X-Admin-Session": this.token,
            },
          });
        }
      } catch (err) {
        console.error(err);
      } finally {
        showToast("Wylogowano", "info");
        this.clearState();
        storeAdminToken(null, false);
      }
    },

    openSection(name) {
      if (!this.canAccessSection(name)) {
        showToast("Brak uprawnień do tej sekcji", "warning");
        return;
      }
      const url = this.resolveSectionUrl(name);
      this.loadSection(name, url);
    },

    handleCardAction(action) {
      if (!action) {
        return;
      }
      if (action.startsWith("open-section:")) {
        const sectionName = action.split(":")[1];
        if (sectionName === "ctip-config") {
          this.pendingScrollTarget = "#ctip-config-card";
          this.openSection("ctip");
          return;
        }
        if (sectionName === "email") {
          this.pendingScrollTarget = "#email-config-card";
          this.openSection("email");
          return;
        }
        if (sectionName === "sms-history") {
          this.pendingScrollTarget = "#sms-history-card";
          this.openSection("sms");
          return;
        }
        this.openSection(sectionName);
        return;
      }
      if (action === "test-database") {
        if (!this.canAccessSection("database")) {
          showToast("Brak uprawnień do tej sekcji", "warning");
          return;
        }
        if (this.section !== "database") {
          this.pendingAction = "test-database";
          this.openSection("database");
        } else {
          document.dispatchEvent(new CustomEvent("admin:database-test"));
        }
        return;
      }
      if (action === "test-email") {
        if (!this.canAccessSection("email")) {
          showToast("Brak uprawnień do tej sekcji", "warning");
          return;
        }
        if (this.section !== "email") {
          this.pendingAction = "test-email";
          this.openSection("email");
        } else {
          document.dispatchEvent(new CustomEvent("admin:email-test"));
        }
        return;
      }
      if (action === "refresh-dashboard") {
        this.loadSection("dashboard", this.resolveSectionUrl("dashboard"));
      }
    },

    onSectionLoaded(name) {
      if (name === "ctip") {
        this.startCtipStream();
      } else {
        this.stopCtipStream();
      }
      if (this.pendingScrollTarget) {
        const selector = this.pendingScrollTarget;
        this.pendingScrollTarget = null;
        setTimeout(() => {
          const target = document.querySelector(selector);
          if (!target) {
            return;
          }
          target.scrollIntoView({ behavior: "smooth", block: "start" });
          target.classList.add("scroll-highlight");
          setTimeout(() => target.classList.remove("scroll-highlight"), 1500);
        }, 600);
      }
      if (this.pendingAction === "test-database" && name === "database") {
        document.dispatchEvent(new CustomEvent("admin:database-test"));
        this.pendingAction = null;
      }
      if (this.pendingAction === "test-email" && name === "email") {
        document.dispatchEvent(new CustomEvent("admin:email-test"));
        this.pendingAction = null;
      }
    },

    get diagnosticsPretty() {
      if (!this.diagnosticsPayload) {
        return "";
      }
      const copy = { ...this.diagnosticsPayload };
      delete copy.card;
      return JSON.stringify(copy, null, 2);
    },

    async openDiagnostics(endpoint, title) {
      if (!this.token) {
        showToast("Zaloguj się, aby uruchomić diagnostykę", "warning");
        return;
      }
      this.diagnosticsTitle = title;
      this.diagnosticsOpen = true;
      this.diagnosticsLoading = true;
      this.diagnosticsError = null;
      this.diagnosticsPayload = null;
      try {
        const response = await fetch(endpoint, {
          headers: {
            "X-Admin-Session": this.token,
          },
        });
        if (response.status === 401 || response.status === 403) {
          this.clearState();
          showToast("Sesja administratora wygasła", "warning");
          throw new Error("Sesja administratora wygasła");
        }
        if (!response.ok) {
          throw new Error(`Błąd HTTP ${response.status}`);
        }
        this.diagnosticsPayload = await response.json();
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd diagnostyki";
        this.diagnosticsError = message;
        showToast(message, "error");
      } finally {
        this.diagnosticsLoading = false;
      }
    },

    closeDiagnostics() {
      this.diagnosticsOpen = false;
      this.diagnosticsLoading = false;
      this.diagnosticsError = null;
      this.diagnosticsPayload = null;
      this.diagnosticsTitle = "";
    },

    applyCtipFilter(event) {
      if (event) {
        event.preventDefault();
      }
      const form = event?.target;
      if (!form) {
        return false;
      }
      const extValue = form.ext?.value?.trim() || null;
      const limitValue = Number(form.limit?.value || 50);
      const card = document.querySelector("#ctip-events-card");
      if (card) {
        card.dataset.ext = extValue || "";
        card.dataset.limit = String(limitValue);
      }
      this.ctipStream.filter = { ext: extValue, limit: Math.min(Math.max(limitValue, 5), 200) };
      if (this.ctipStream.socket && this.ctipStream.socket.readyState === WebSocket.OPEN) {
        this.ctipStream.socket.send(
          JSON.stringify({
            type: "filter",
            ext: this.ctipStream.filter.ext,
            limit: this.ctipStream.filter.limit,
          }),
        );
      } else {
        this.startCtipStream();
      }
      return false;
    },

    openCtipContactEditor(payload) {
      if (!payload || !payload.number) {
        showToast("Zdarzenie nie zawiera numeru telefonu", "warning");
        return;
      }
      if (!this.canAccessSection("contacts")) {
        showToast("Brak uprawnień do edycji książki adresowej", "warning");
        return;
      }
      document.dispatchEvent(
        new CustomEvent("admin:ctip-contact-open", {
          detail: {
            number: String(payload.number),
            ext: payload.ext ? String(payload.ext) : "",
          },
        }),
      );
    },

    handleCtipContactButton(button) {
      if (!button) {
        return;
      }
      const number = button.dataset.number || "";
      const ext = button.dataset.ext || "";
      if (!number) {
        showToast("To zdarzenie nie posiada numeru do zapisania", "info");
        return;
      }
      this.openCtipContactEditor({ number, ext });
    },

    startCtipStream() {
      if (!this.token) {
        return;
      }
      const card = document.querySelector("#ctip-events-card");
      if (!card) {
        return;
      }
      const limit = Number(card.dataset.limit || 50);
      const ext = card.dataset.ext || null;
      this.ctipStream.filter = {
        ext: ext && ext.length ? ext : null,
        limit: Math.min(Math.max(limit, 5), 200),
      };
      if (this.ctipStream.socket) {
        this.ctipStream.socket.close();
        this.ctipStream.socket = null;
      }
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const params = new URLSearchParams({
        token: this.token,
        limit: String(this.ctipStream.filter.limit),
      });
      if (this.ctipStream.filter.ext) {
        params.set("ext", this.ctipStream.filter.ext);
      }
      const socketUrl = `${protocol}://${window.location.host}/admin/ctip/ws?${params.toString()}`;
      const socket = new WebSocket(socketUrl);
      this.ctipStream.socket = socket;

      socket.addEventListener("message", (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload?.type === "events") {
            this.renderCtipEvents(payload);
          }
        } catch (err) {
          console.error("Błąd dekodowania strumienia CTIP", err);
        }
      });

      socket.addEventListener("close", () => {
        if (this.ctipStream.socket === socket) {
          this.ctipStream.socket = null;
          if (this.section === "ctip") {
            this.scheduleCtipReconnect();
          }
        }
      });

      socket.addEventListener("error", () => {
        console.error("Błąd WebSocket CTIP");
        socket.close();
      });
    },

    stopCtipStream() {
      if (this.ctipStream.reconnectTimer) {
        clearTimeout(this.ctipStream.reconnectTimer);
        this.ctipStream.reconnectTimer = null;
      }
      if (this.ctipStream.socket) {
        try {
          this.ctipStream.socket.close();
        } catch (err) {
          console.error("Błąd zamykania strumienia CTIP", err);
        }
      }
      this.ctipStream.socket = null;
    },

    scheduleCtipReconnect() {
      if (this.ctipStream.reconnectTimer || this.ctipStream.socket) {
        return;
      }
      this.ctipStream.reconnectTimer = setTimeout(() => {
        this.ctipStream.reconnectTimer = null;
        if (this.section === "ctip") {
          this.startCtipStream();
        }
      }, 4000);
    },

    renderCtipEvents(payload) {
      const card = document.querySelector("#ctip-events-card");
      if (!card) {
        return;
      }
      if (typeof payload.limit === "number") {
        card.dataset.limit = String(payload.limit);
      }
      card.dataset.ext = payload.ext || "";
      if (payload.generated_at) {
        card.dataset.generated = payload.generated_at;
        const info = card.querySelector("[data-role='generated-info']");
        if (info) {
          const extLabel = payload.ext ? `Wewnętrzny ${payload.ext} • ` : "";
          info.textContent = `Limit: ${payload.limit || this.ctipStream.filter.limit} • ${extLabel}Odświeżono: ${this.formatDateTime(payload.generated_at)}`;
        }
      }
      const tbody = card.querySelector("#ctip-events-body");
      if (!tbody) {
        return;
      }
      if (!Array.isArray(payload.items) || !payload.items.length) {
        tbody.innerHTML = `<tr><td colspan="5"><span class="sms-history-empty">Brak zdarzeń dla wybranego filtra.</span></td></tr>`;
        return;
      }
      const rows = payload.items
        .map((item) => {
          const time = this.formatDateTime(item.ts);
          const typ = item.typ || "—";
          const ext = item.ext || "—";
          const number = item.number || "—";
          const payloadPreview = item.payload ? this.truncatePayload(item.payload) : "—";
          return `<tr>
            <td>${time}</td>
            <td><span class="status-chip info">${typ}</span></td>
            <td>${ext}</td>
            <td>${number}</td>
            <td>${payloadPreview}</td>
          </tr>`;
        })
        .join("");
      tbody.innerHTML = rows;
    },

    formatDateTime(value) {
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
          second: "2-digit",
        });
      } catch (err) {
        return value;
      }
    },

    truncatePayload(text) {
      if (!text) {
        return "—";
      }
      if (text.length <= 120) {
        return `<code class="ctip-payload">${text}</code>`;
      }
      return `<code class="ctip-payload">${text.slice(0, 120)}…</code>`;
    },
  });

  const adminContacts = () => ({
    contacts: [],
    canManage: false,
    loading: false,
    saving: false,
    actionContactId: null,
    search: "",
    error: null,
    success: null,
    form: {
      number: "",
      ext: "",
      firebirdId: "",
      firstName: "",
      lastName: "",
      company: "",
      email: "",
      nip: "",
      notes: "",
      source: "manual",
    },
    modalOpen: false,
    modalLoading: false,
    modalSaving: false,
    modalError: null,
    modalSuccess: null,
    modalDetail: null,
    modalEdit: {
      number: "",
      ext: "",
      firebirdId: "",
      firstName: "",
      lastName: "",
      company: "",
      email: "",
      nip: "",
      notes: "",
      source: "manual",
    },

    init() {
      this.contacts = this._readContacts(this.$el.dataset.contacts);
      this.canManage = this._readFlag(this.$el.dataset.canManage);
      this.canDelete = this._readFlag(this.$el.dataset.canDelete);
      const searchValue = this.$el.dataset.search || "";
      this.search = searchValue;
      this.refreshListener = () => {
        if (!this.$el.isConnected) {
          document.removeEventListener("admin:contacts-refresh", this.refreshListener);
          return;
        }
        if (!this.loading) {
          this.fetchContacts(false);
        }
      };
      document.addEventListener("admin:contacts-refresh", this.refreshListener);
    },

    _readContacts(payload) {
      if (!payload) {
        return [];
      }
      try {
        const parsed = JSON.parse(payload);
        if (Array.isArray(parsed)) {
          return parsed;
        }
        if (Array.isArray(parsed.items)) {
          return parsed.items;
        }
      } catch (err) {
        console.error("Nie można zdekodować listy kontaktów", err);
      }
      return [];
    },

    _readFlag(value) {
      if (!value) {
        return false;
      }
      try {
        return Boolean(JSON.parse(value));
      } catch (err) {
        return value === "true";
      }
    },

    _headers(includeJson = true) {
      const token = localStorage.getItem("admin-session-token");
      if (!token) {
        showToast("Sesja administratora nieaktywna", "warning");
        return null;
      }
      const headers = { "X-Admin-Session": token };
      if (includeJson) {
        headers["Content-Type"] = "application/json";
      }
      return headers;
    },

    _normalize(value) {
      if (value === undefined || value === null) {
        return null;
      }
      if (typeof value === "string") {
        const trimmed = value.trim();
        return trimmed.length ? trimmed : null;
      }
      return value;
    },

    _payloadFromForm(form) {
      return {
        number: (form.number || "").trim(),
        ext: this._normalize(form.ext),
        firebird_id: this._normalize(form.firebirdId),
        first_name: this._normalize(form.firstName),
        last_name: this._normalize(form.lastName),
        company: this._normalize(form.company),
        email: this._normalize(form.email),
        nip: this._normalize(form.nip),
        notes: this._normalize(form.notes),
        source: this._normalize(form.source) || "manual",
      };
    },

    _upsertContact(contact) {
      const index = this.contacts.findIndex((item) => item.id === contact.id);
      if (index >= 0) {
        this.contacts.splice(index, 1, contact);
        this.contacts = [...this.contacts];
      } else {
        this.contacts = [contact, ...this.contacts];
      }
    },

    _removeContact(contactId) {
      this.contacts = this.contacts.filter((item) => item.id !== contactId);
    },

    resetMessages() {
      this.error = null;
      this.success = null;
    },

    resetForm() {
      this.form.number = "";
      this.form.ext = "";
      this.form.firebirdId = "";
      this.form.firstName = "";
      this.form.lastName = "";
      this.form.company = "";
      this.form.email = "";
      this.form.nip = "";
      this.form.notes = "";
      this.form.source = "manual";
    },

    formatDate(value) {
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
    },

    formatName(contact) {
      if (!contact) {
        return "—";
      }
      const parts = [contact.first_name, contact.last_name].filter(Boolean);
      return parts.length ? parts.join(" ") : "—";
    },

    formatPhone(value) {
      if (!value) {
        return "—";
      }
      return value;
    },

    formatFirebird(value) {
      return value || "—";
    },

    truncateNotes(value) {
      if (!value) {
        return "—";
      }
      if (value.length <= 80) {
        return value;
      }
      return `${value.slice(0, 77)}…`;
    },

    async fetchContacts(showMessage = false) {
      const headers = this._headers(false);
      if (!headers) {
        return;
      }
      this.loading = true;
      this.error = null;
      try {
        const query = this.search?.trim();
        let url = "/admin/contacts";
        if (query) {
          url += `?search=${encodeURIComponent(query)}`;
        }
        const response = await fetch(url, { headers });
        if (response.status === 401 || response.status === 403) {
          showToast("Sesja administratora wygasła", "warning");
          throw new Error("Sesja administratora wygasła");
        }
        if (!response.ok) {
          throw new Error(`Błąd HTTP ${response.status}`);
        }
        const data = await response.json();
        if (Array.isArray(data.items)) {
          this.contacts = data.items;
        } else {
          this.contacts = [];
        }
        if (showMessage) {
          showToast("Lista kontaktów odświeżona", "success");
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd pobierania kontaktów";
        this.error = message;
        showToast(message, "error");
      } finally {
        this.loading = false;
      }
    },

    async searchContacts(event) {
      if (event) {
        event.preventDefault();
      }
      await this.fetchContacts(false);
    },

    async clearSearch() {
      if (!this.search) {
        return;
      }
      this.search = "";
      await this.fetchContacts(false);
    },

    async createContact() {
      if (!this.canManage) {
        return;
      }
      this.resetMessages();
      const headers = this._headers();
      if (!headers) {
        return;
      }
      if (!this.form.number.trim()) {
        this.error = "Podaj numer telefonu.";
        return;
      }
      this.saving = true;
      try {
        const response = await fetch("/admin/contacts", {
          method: "POST",
          headers,
          body: JSON.stringify(this._payloadFromForm(this.form)),
        });
        if (response.status === 401 || response.status === 403) {
          showToast("Sesja administratora wygasła", "warning");
          throw new Error("Sesja administratora wygasła");
        }
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          const message = detail?.detail || `Błąd HTTP ${response.status}`;
          throw new Error(message);
        }
        const data = await response.json();
        this._upsertContact(data);
        this.resetForm();
        this.success = "Kontakt został dodany.";
        showToast("Kontakt zapisany", "success");
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd dodawania kontaktu";
        this.error = message;
        showToast(message, "error");
      } finally {
        this.saving = false;
      }
    },

    openModal(contactId) {
      this.modalOpen = true;
      this.modalLoading = true;
      this.modalError = null;
      this.modalSuccess = null;
      this.modalDetail = null;
      this.modalEdit = {
        number: "",
        ext: "",
        firebirdId: "",
        firstName: "",
        lastName: "",
        company: "",
        email: "",
        nip: "",
        notes: "",
        source: "manual",
      };
      this.loadContact(contactId);
    },

    async loadContact(contactId) {
      const headers = this._headers(false);
      if (!headers) {
        this.modalLoading = false;
        return;
      }
      try {
        const response = await fetch(`/admin/contacts/${contactId}`, { headers });
        if (response.status === 401 || response.status === 403) {
          this.modalError = "Sesja wygasła. Zaloguj się ponownie.";
          showToast("Sesja administratora wygasła", "warning");
          throw new Error("Sesja administratora wygasła");
        }
        if (response.status === 404) {
          this.modalError = "Nie znaleziono kontaktu.";
          this._removeContact(contactId);
          return;
        }
        if (!response.ok) {
          throw new Error(`Błąd HTTP ${response.status}`);
        }
        const data = await response.json();
        this.modalDetail = data;
        this.modalEdit = this._mapDetailToEdit(data);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd pobierania kontaktu";
        this.modalError = message;
      } finally {
        this.modalLoading = false;
      }
    },

    _mapDetailToEdit(detail) {
      return {
        number: detail.number || "",
        ext: detail.ext || "",
        firebirdId: detail.firebird_id || "",
        firstName: detail.first_name || "",
        lastName: detail.last_name || "",
        company: detail.company || "",
        email: detail.email || "",
        nip: detail.nip || "",
        notes: detail.notes || "",
        source: detail.source || "manual",
      };
    },

    async updateContact() {
      if (!this.modalDetail || !this.canManage) {
        return;
      }
      const headers = this._headers();
      if (!headers) {
        return;
      }
      this.modalSaving = true;
      this.modalError = null;
      this.modalSuccess = null;
      try {
        const response = await fetch(`/admin/contacts/${this.modalDetail.id}`, {
          method: "PUT",
          headers,
          body: JSON.stringify(this._payloadFromForm(this.modalEdit)),
        });
        if (response.status === 401 || response.status === 403) {
          this.modalError = "Sesja wygasła. Zaloguj się ponownie.";
          showToast("Sesja administratora wygasła", "warning");
          throw new Error("Sesja administratora wygasła");
        }
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          const message = detail?.detail || `Błąd HTTP ${response.status}`;
          throw new Error(message);
        }
        const data = await response.json();
        this.modalDetail = data;
        this.modalEdit = this._mapDetailToEdit(data);
        this._upsertContact(data);
        this.modalSuccess = "Zmiany zostały zapisane.";
        showToast("Kontakt zaktualizowany", "success");
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd aktualizacji kontaktu";
        this.modalError = message;
        showToast(message, "error");
      } finally {
        this.modalSaving = false;
      }
    },

    async deleteContact(contact) {
      if (!this.canDelete || !contact) {
        return;
      }
      if (!window.confirm(`Usunąć kontakt ${contact.number}?`)) {
        return;
      }
      const headers = this._headers(false);
      if (!headers) {
        return;
      }
      this.actionContactId = contact.id;
      try {
        const response = await fetch(`/admin/contacts/${contact.id}`, {
          method: "DELETE",
          headers,
        });
        if (response.status === 401 || response.status === 403) {
          showToast("Sesja administratora wygasła", "warning");
          throw new Error("Sesja administratora wygasła");
        }
        if (response.status === 404) {
          this._removeContact(contact.id);
          this.closeModal();
          return;
        }
        if (!response.ok) {
          throw new Error(`Błąd HTTP ${response.status}`);
        }
        this._removeContact(contact.id);
        if (this.modalDetail?.id === contact.id) {
          this.closeModal();
        }
        showToast("Kontakt został usunięty", "success");
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd usuwania kontaktu";
        showToast(message, "error");
      } finally {
        this.actionContactId = null;
      }
    },

    closeModal() {
      this.modalOpen = false;
      this.modalLoading = false;
      this.modalSaving = false;
      this.modalError = null;
      this.modalSuccess = null;
      this.modalDetail = null;
      this.modalEdit = {
        number: "",
        ext: "",
        firebirdId: "",
        firstName: "",
        lastName: "",
        company: "",
        email: "",
        nip: "",
        notes: "",
        source: "manual",
      };
    },
  });

  Alpine.data("adminContacts", adminContacts);
  window.adminContacts = adminContacts;

  const ctipLivePanel = () => ({
    editor: {
      open: false,
      loading: false,
      saving: false,
      contactId: null,
      exists: false,
      number: "",
      ext: "",
      error: null,
      success: null,
      form: {
        firstName: "",
        lastName: "",
        company: "",
        email: "",
        nip: "",
        firebirdId: "",
        notes: "",
        source: "ctip",
      },
    },

    init() {
      this._openListener = (event) => {
        if (!this.$el.isConnected) {
          document.removeEventListener("admin:ctip-contact-open", this._openListener);
          return;
        }
        const detail = event.detail || {};
        this.openEditor(detail.number || "", detail.ext || "");
      };
      document.addEventListener("admin:ctip-contact-open", this._openListener);
    },

    _headers(includeJson = true) {
      const token = localStorage.getItem("admin-session-token");
      if (!token) {
        showToast("Sesja administratora nieaktywna", "warning");
        return null;
      }
      const headers = { "X-Admin-Session": token };
      if (includeJson) {
        headers["Content-Type"] = "application/json";
      }
      return headers;
    },

    resetForm(number, ext) {
      this.editor.form.firstName = "";
      this.editor.form.lastName = "";
      this.editor.form.company = "";
      this.editor.form.email = "";
      this.editor.form.nip = "";
      this.editor.form.firebirdId = "";
      this.editor.form.notes = "";
      this.editor.form.source = "ctip";
      this.editor.number = number;
      this.editor.ext = ext;
    },

    setFormFromContact(contact) {
      this.editor.contactId = contact.id;
      this.editor.exists = true;
      this.editor.form.firstName = contact.first_name || "";
      this.editor.form.lastName = contact.last_name || "";
      this.editor.form.company = contact.company || "";
      this.editor.form.email = contact.email || "";
      this.editor.form.nip = contact.nip || "";
      this.editor.form.firebirdId = contact.firebird_id || "";
      this.editor.form.notes = contact.notes || "";
      this.editor.form.source = contact.source || "manual";
      this.editor.number = contact.number;
      this.editor.ext = contact.ext || "";
    },

    async openEditor(number, ext) {
      if (!number) {
        showToast("Zdarzenie nie zawiera numeru telefonu", "warning");
        return;
      }
      this.editor.open = true;
      this.editor.loading = true;
      this.editor.contactId = null;
      this.editor.exists = false;
      this.editor.error = null;
      this.editor.success = null;
      this.resetForm(number, ext);
      const headers = this._headers(false);
      if (!headers) {
        this.editor.loading = false;
        return;
      }
      try {
        const response = await fetch(`/admin/contacts/by-number/${encodeURIComponent(number)}`, {
          headers,
        });
        if (response.status === 401 || response.status === 403) {
          showToast("Sesja administratora wygasła", "warning");
          throw new Error("Sesja administratora wygasła");
        }
        if (response.status === 404) {
          this.editor.exists = false;
          this.editor.loading = false;
          return;
        }
        if (!response.ok) {
          throw new Error(`Błąd HTTP ${response.status}`);
        }
        const data = await response.json();
        this.setFormFromContact(data);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd pobierania kontaktu";
        this.editor.error = message;
      } finally {
        this.editor.loading = false;
      }
    },

    async saveContact() {
      if (this.editor.saving || !this.editor.number) {
        return;
      }
      const headers = this._headers();
      if (!headers) {
        return;
      }
      this.editor.saving = true;
      this.editor.error = null;
      this.editor.success = null;
      const payload = {
        number: this.editor.number.trim(),
        ext: this.editor.ext ? this.editor.ext.trim() : null,
        first_name: this.editor.form.firstName.trim() || null,
        last_name: this.editor.form.lastName.trim() || null,
        company: this.editor.form.company.trim() || null,
        email: this.editor.form.email.trim() || null,
        firebird_id: this.editor.form.firebirdId.trim() || null,
        nip: this.editor.form.nip.trim() || null,
        notes: this.editor.form.notes.trim() || null,
        source: this.editor.form.source.trim() || "ctip",
      };
      try {
        let response;
        if (this.editor.contactId) {
          response = await fetch(`/admin/contacts/${this.editor.contactId}`, {
            method: "PUT",
            headers,
            body: JSON.stringify(payload),
          });
        } else {
          response = await fetch("/admin/contacts", {
            method: "POST",
            headers,
            body: JSON.stringify(payload),
          });
        }
        if (response.status === 401 || response.status === 403) {
          showToast("Sesja administratora wygasła", "warning");
          throw new Error("Sesja administratora wygasła");
        }
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          const message = detail?.detail || `Błąd HTTP ${response.status}`;
          throw new Error(message);
        }
        const data = await response.json();
        this.setFormFromContact(data);
        this.editor.success = "Kontakt został zapisany.";
        showToast("Kontakt zapisany", "success");
        document.dispatchEvent(new CustomEvent("admin:contacts-refresh"));
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd zapisu kontaktu";
        this.editor.error = message;
        showToast(message, "error");
      } finally {
        this.editor.saving = false;
      }
    },

    closeEditor() {
      this.editor.open = false;
      this.editor.error = null;
      this.editor.success = null;
    },
  });

  Alpine.data("ctipLivePanel", ctipLivePanel);

  const adminUsers = () => ({
    users: [],
    canManage: false,
    loading: false,
    saving: false,
    actionUserId: null,
    currentUserId: null,
    error: null,
    success: null,
    formPassword: null,
    msUsers: [],
    msUsersLoading: false,
    msUsersError: null,
    form: {
      email: "",
      firstName: "",
      lastName: "",
      internalExt: "",
      role: "operator",
      isSalesperson: false,
      crmSalesSmsEnabled: false,
      crmSalesEmailEnabled: false,
      crmOperationsSmsEnabled: false,
      crmOperationsEmailEnabled: false,
      mobilePhone: "",
      firebirdAppUserId: "",
      firebirdAppUserLogin: "",
      imapEnabled: false,
      imapEmail: "",
      imapHost: "",
      imapPort: "993",
      imapUsername: "",
      imapUseSsl: true,
      imapFolder: "INBOX",
      imapPassword: "",
      imapClearPassword: false,
      sections: ["operator", "generator"],
    },
    modalOpen: false,
    modalLoading: false,
    modalSaving: false,
    modalError: null,
    modalSuccess: null,
    modalPassword: null,
    modalDetail: null,
    modalEdit: {
      email: "",
      firstName: "",
      lastName: "",
      internalExt: "",
      role: "operator",
      isSalesperson: false,
      crmSalesSmsEnabled: false,
      crmSalesEmailEnabled: false,
      crmOperationsSmsEnabled: false,
      crmOperationsEmailEnabled: false,
      mobilePhone: "",
      firebirdAppUserId: "",
      firebirdAppUserLogin: "",
      imapEnabled: false,
      imapEmail: "",
      imapHost: "",
      imapPort: "993",
      imapUsername: "",
      imapUseSsl: true,
      imapFolder: "INBOX",
      imapPassword: "",
      imapClearPassword: false,
      sections: ["operator", "generator"],
    },

    init() {
      this.users = this._readUsers(this.$el.dataset.users);
      this.canManage = this._readFlag(this.$el.dataset.canManage);
      this.currentUserId = Number(this.$el.dataset.currentId || 0) || null;
      if (this.canManage) {
        this.fetchMsUsers();
      }
    },

    _readUsers(payload) {
      if (!payload) {
        return [];
      }
      try {
        const parsed = JSON.parse(payload);
        if (Array.isArray(parsed)) {
          return parsed;
        }
        if (Array.isArray(parsed.items)) {
          return parsed.items;
        }
      } catch (err) {
        console.error("Nie można zdekodować listy użytkowników", err);
      }
      return [];
    },

    _readFlag(value) {
      if (!value) {
        return false;
      }
      try {
        return Boolean(JSON.parse(value));
      } catch (err) {
        return value === "true";
      }
    },

    _getToken() {
      return (
        window.localStorage?.getItem("admin-session-token") ||
        window.sessionStorage?.getItem("admin-session-token") ||
        null
      );
    },

    _headers(includeJson = true) {
      const token = this._getToken();
      if (!token) {
        return null;
      }
      const headers = {};
      if (includeJson) {
        headers["Content-Type"] = "application/json";
      }
      headers["X-Admin-Session"] = token;
      return headers;
    },

    formatDate(value) {
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
    },

    formatName(user) {
      if (!user) {
        return "—";
      }
      const parts = [user.first_name, user.last_name].filter(Boolean);
      return parts.length ? parts.join(" ") : "—";
    },

    formatPhone(value) {
      if (!value) {
        return "—";
      }
      return value;
    },

    formatMsUser(user) {
      const login = String(user?.firebird_app_user_login || "").trim();
      if (login) {
        return login;
      }
      const id = this.normalizeMsUserId(user?.firebird_app_user_id);
      if (id) {
        return `ID ${id}`;
      }
      return "—";
    },

    formatImapStatus(imap) {
      if (!imap) {
        return "Wyłączony";
      }
      if (!imap.enabled) {
        return "Wyłączony";
      }
      const email = String(imap.email || "").trim();
      return email ? `Włączony (${email})` : "Włączony";
    },

    formatCrmNotifications(user) {
      const groups = [];
      const sales = [];
      const operations = [];
      if (user?.crm_sales_sms_enabled) {
        sales.push("SMS");
      }
      if (user?.crm_sales_email_enabled) {
        sales.push("e-mail");
      }
      if (user?.crm_operations_sms_enabled) {
        operations.push("SMS");
      }
      if (user?.crm_operations_email_enabled) {
        operations.push("e-mail");
      }
      if (sales.length) {
        groups.push(`Handel: ${sales.join(", ")}`);
      }
      if (operations.length) {
        groups.push(`Pozostałe: ${operations.join(", ")}`);
      }
      return groups.length ? groups.join("; ") : "Wyłączone";
    },

    defaultSectionsForRole(role) {
      if (role === "admin") {
        return ["admin", "operator", "generator", "delivery", "shipping"];
      }
      if (role === "serwisant") {
        return ["delivery"];
      }
      return ["operator", "generator"];
    },

    normalizeSectionsForRole(sections, role) {
      const defaults = this.defaultSectionsForRole(role);
      const input = Array.isArray(sections) ? sections : defaults;
      const seen = new Set();
      const normalized = [];
      input.forEach((item) => {
        const value = String(item || "").trim().toLowerCase();
        if (!value || !["admin", "operator", "generator", "delivery", "shipping"].includes(value)) {
          return;
        }
        if (value === "admin" && role !== "admin") {
          return;
        }
        if (seen.has(value)) {
          return;
        }
        seen.add(value);
        normalized.push(value);
      });
      if (!normalized.length) {
        return defaults;
      }
      return normalized;
    },

    formatSections(sections, role = "operator") {
      const normalized = this.normalizeSectionsForRole(sections, role);
      const labels = {
        admin: "Admin",
        operator: "Operator",
        generator: "Generator",
        delivery: "Obsługa dostaw",
        shipping: "Wysyłki części",
      };
      return normalized.map((item) => labels[item] || item).join(", ");
    },

    formatRole(role) {
      const labels = {
        admin: "admin",
        operator: "operator",
        serwisant: "serwisant",
      };
      return labels[role] || role || "—";
    },

    roleBadgeClass(role) {
      if (role === "admin") {
        return "badge-admin";
      }
      if (role === "serwisant") {
        return "badge-info";
      }
      return "badge-operator";
    },

    resetMessages() {
      this.error = null;
      this.success = null;
    },

    buildCredentialsMessage(action, data) {
      const base =
        action === "reset"
          ? "Hasło użytkownika zostało zresetowane."
          : "Użytkownik został dodany.";
      if (data?.sms_queued) {
        const recipient = String(data.sms_recipient || "").trim();
        if (recipient) {
          return `${base} SMS z danymi logowania dodano do kolejki dla ${recipient}.`;
        }
        return `${base} SMS z danymi logowania dodano do kolejki.`;
      }
      return `${base} SMS z danymi logowania nie został dodany do kolejki. Sprawdź numer telefonu użytkownika.`;
    },

    resetForm() {
      this.form.email = "";
      this.form.firstName = "";
      this.form.lastName = "";
      this.form.internalExt = "";
      this.form.role = "operator";
      this.form.isSalesperson = false;
      this.form.crmSalesSmsEnabled = false;
      this.form.crmSalesEmailEnabled = false;
      this.form.crmOperationsSmsEnabled = false;
      this.form.crmOperationsEmailEnabled = false;
      this.form.mobilePhone = "";
      this.form.firebirdAppUserId = "";
      this.form.firebirdAppUserLogin = "";
      this.form.imapEnabled = false;
      this.form.imapEmail = "";
      this.form.imapHost = "";
      this.form.imapPort = "993";
      this.form.imapUsername = "";
      this.form.imapUseSsl = true;
      this.form.imapFolder = "INBOX";
      this.form.imapPassword = "";
      this.form.imapClearPassword = false;
      this.form.sections = this.defaultSectionsForRole("operator");
    },

    normalizeMsUserId(value) {
      if (value === null || value === undefined || value === "") {
        return null;
      }
      const parsed = Number(value);
      if (!Number.isInteger(parsed) || parsed <= 0) {
        return null;
      }
      return parsed;
    },

    availableMsUsers(selectedId, selectedLogin) {
      const items = Array.isArray(this.msUsers) ? [...this.msUsers] : [];
      const normalizedId = this.normalizeMsUserId(selectedId);
      if (!normalizedId) {
        return items;
      }
      const exists = items.some((item) => this.normalizeMsUserId(item?.id) === normalizedId);
      if (exists) {
        return items;
      }
      const login = String(selectedLogin || "").trim();
      items.unshift({
        id: normalizedId,
        label: login ? `${login} (zapisane powiązanie)` : `ID ${normalizedId} (zapisane powiązanie)`,
      });
      return items;
    },

    onCreateRoleChange() {
      this.form.sections = this.defaultSectionsForRole(this.form.role);
    },

    onModalRoleChange() {
      this.modalEdit.sections = this.defaultSectionsForRole(this.modalEdit.role);
    },

    async reload() {
      await this.fetchMsUsers(false);
      await this.fetchUsers(true);
    },

    async fetchMsUsers(showMessage = false) {
      const headers = this._headers(false);
      if (!headers) {
        this.msUsersError = "Brak aktywnej sesji administratora.";
        return;
      }
      this.msUsersLoading = true;
      this.msUsersError = null;
      try {
        const response = await fetch("/admin/users/firebird-ms-users", { headers });
        const data = await response.json().catch(() => ({}));
        if (response.status === 401 || response.status === 403) {
          this.msUsersError = "Sesja administratora wygasła. Zaloguj się ponownie.";
          return;
        }
        if (!response.ok) {
          throw new Error(
            data?.detail || "Nie udało się pobrać listy użytkowników Menadżera Serwisu.",
          );
        }
        this.msUsers = Array.isArray(data.items) ? data.items : [];
        if (showMessage) {
          showToast("Lista użytkowników Menadżera Serwisu została odświeżona.", "success");
        }
      } catch (err) {
        this.msUsers = [];
        this.msUsersError =
          err instanceof Error ? err.message : "Błąd odczytu użytkowników Menadżera Serwisu.";
        showToast(this.msUsersError, "warning");
      } finally {
        this.msUsersLoading = false;
      }
    },

    async fetchUsers(showMessage = true) {
      const headers = this._headers(false);
      if (!headers) {
        this.error = "Brak aktywnej sesji administratora.";
        return;
      }
      this.loading = true;
      this.error = null;
      try {
        const response = await fetch("/admin/users", { headers });
        if (response.status === 401 || response.status === 403) {
          this.error = "Sesja administratora wygasła. Zaloguj się ponownie.";
          showToast(this.error, "warning");
          return;
        }
        if (!response.ok) {
          throw new Error(`Błąd HTTP ${response.status}`);
        }
        const data = await response.json();
        this.users = Array.isArray(data.items) ? data.items : [];
        if (showMessage) {
          this.success = "Lista użytkowników została odświeżona.";
          showToast(this.success, "success");
        }
      } catch (err) {
        console.error(err);
        this.error = err instanceof Error ? err.message : "Nie udało się pobrać listy użytkowników.";
        showToast(this.error, "error");
      } finally {
        this.loading = false;
      }
    },

    _buildPayload(source) {
      const normalize = (value) => {
        if (value === null || value === undefined) {
          return "";
        }
        return String(value).trim();
      };
      const email = normalize(source.email).toLowerCase();
      const mobile = normalize(source.mobilePhone);
      const imapEmail = normalize(source.imapEmail).toLowerCase();
      const imapHost = normalize(source.imapHost);
      const imapUsername = normalize(source.imapUsername);
      const imapFolder = normalize(source.imapFolder) || "INBOX";
      const imapPassword = normalize(source.imapPassword);
      const imapPortRaw = normalize(source.imapPort);
      const imapPortParsed = imapPortRaw ? Number(imapPortRaw) : null;
      const imapPort =
        Number.isInteger(imapPortParsed) && imapPortParsed > 0 && imapPortParsed <= 65535
          ? imapPortParsed
          : null;
      const hasImapPayload =
        Boolean(source.imapEnabled)
        || Boolean(imapEmail)
        || Boolean(imapHost)
        || Boolean(imapUsername)
        || Boolean(imapPassword)
        || Boolean(source.imapClearPassword)
        || (imapPort !== null && String(imapPort) !== "993")
        || (imapFolder && imapFolder.toUpperCase() !== "INBOX")
        || source.imapUseSsl === false;
      return {
        email,
        first_name: normalize(source.firstName) || null,
        last_name: normalize(source.lastName) || null,
        internal_ext: normalize(source.internalExt) || null,
        role: source.role || "operator",
        is_salesperson: Boolean(source.isSalesperson),
        crm_sales_sms_enabled: Boolean(source.crmSalesSmsEnabled),
        crm_sales_email_enabled: Boolean(source.crmSalesEmailEnabled),
        crm_operations_sms_enabled: Boolean(source.crmOperationsSmsEnabled),
        crm_operations_email_enabled: Boolean(source.crmOperationsEmailEnabled),
        mobile_phone: mobile || null,
        firebird_app_user_id: this.normalizeMsUserId(source.firebirdAppUserId),
        sections: this.normalizeSectionsForRole(source.sections, source.role || "operator"),
        imap: hasImapPayload
          ? {
              enabled: Boolean(source.imapEnabled),
              email: imapEmail || null,
              host: imapHost || null,
              port: imapPort,
              username: imapUsername || null,
              use_ssl: source.imapUseSsl !== false,
              folder: imapFolder || null,
              password: imapPassword || null,
              clear_password: Boolean(source.imapClearPassword),
            }
          : null,
      };
    },

    _upsertUser(summary) {
      if (!summary || typeof summary.id !== "number") {
        return;
      }
      const index = this.users.findIndex((item) => item.id === summary.id);
      if (index >= 0) {
        this.users.splice(index, 1, summary);
      } else {
        this.users.unshift(summary);
      }
    },

    async createUser() {
      if (this.saving || !this.canManage) {
        return;
      }
      const headers = this._headers(true);
      if (!headers) {
        this.error = "Brak aktywnej sesji administratora.";
        return;
      }
      const email = (this.form.email || "").trim();
      const mobile = (this.form.mobilePhone || "").trim();
      if (!this.form.email) {
        this.error = "Adres e-mail jest wymagany.";
        return;
      }
      if (!mobile) {
        this.error = "Telefon komórkowy jest wymagany.";
        return;
      }
      if (!/^[0-9+\s-]+$/.test(mobile)) {
        this.error = "Numer telefonu zawiera niedozwolone znaki.";
        return;
      }
      if (mobile.replace(/[^0-9]/g, "").length < 6) {
        this.error = "Numer telefonu jest zbyt krótki.";
        return;
      }
      this.saving = true;
      this.resetMessages();
      try {
        const payload = this._buildPayload(this.form);
        if (this.form.role === "admin") {
          payload.role = "admin";
        }
        if (this.formPassword) {
          this.formPassword = null;
        }
        const response = await fetch("/admin/users", {
          method: "POST",
          headers,
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się utworzyć użytkownika.");
        }
        await this.fetchUsers(false);
        this.formPassword = data.password || null;
        this.resetForm();
        this.success = this.buildCredentialsMessage("create", data);
        showToast(this.success, data?.sms_queued === false ? "warning" : "success");
      } catch (err) {
        this.error = err instanceof Error ? err.message : "Błąd tworzenia użytkownika.";
        showToast(this.error, "error");
      } finally {
        this.saving = false;
      }
    },

    async resetPassword(user) {
      if (!this.canManage || !user || typeof user.id !== "number") {
        return;
      }
      const headers = this._headers(true);
      if (!headers) {
        showToast("Brak aktywnej sesji administratora.", "warning");
        return;
      }
      if (!window.confirm(`Wygenerować nowe hasło dla ${user.email}?`)) {
        return;
      }
      this.actionUserId = user.id;
      this.error = null;
      try {
        const response = await fetch(`/admin/users/${user.id}/reset-password`, {
          method: "POST",
          headers,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się zresetować hasła.");
        }
        const newPassword = data.password || null;
        if (this.modalOpen && this.modalDetail && this.modalDetail.id === user.id) {
          this.modalPassword = newPassword;
          this.modalSuccess = "Nowe hasło zostało wygenerowane.";
        }
        this.success = this.buildCredentialsMessage("reset", data);
        showToast(this.success, data?.sms_queued === false ? "warning" : "success");
        this.formPassword = newPassword;
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd resetu hasła.";
        this.error = message;
        if (this.modalOpen) {
          this.modalError = message;
        }
        showToast(message, "error");
      } finally {
        this.actionUserId = null;
      }
    },

    async toggleActive(user) {
      if (!this.canManage || !user || typeof user.id !== "number") {
        return;
      }
      const headers = this._headers(true);
      if (!headers) {
        showToast("Brak aktywnej sesji administratora.", "warning");
        return;
      }
      this.actionUserId = user.id;
      this.error = null;
      try {
        const response = await fetch(`/admin/users/${user.id}/status`, {
          method: "PATCH",
          headers,
          body: JSON.stringify({ is_active: !user.is_active }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się zmienić statusu.");
        }
        this._upsertUser(data);
        if (this.modalOpen && this.modalDetail && this.modalDetail.id === user.id) {
          this.modalDetail.is_active = data.is_active;
          this.modalEdit.role = data.role;
          this.modalEdit.isSalesperson = Boolean(data.is_salesperson);
          this.modalEdit.crmSalesSmsEnabled = Boolean(data.crm_sales_sms_enabled);
          this.modalEdit.crmSalesEmailEnabled = Boolean(data.crm_sales_email_enabled);
          this.modalEdit.crmOperationsSmsEnabled = Boolean(data.crm_operations_sms_enabled);
          this.modalEdit.crmOperationsEmailEnabled = Boolean(
            data.crm_operations_email_enabled,
          );
          this.modalEdit.sections = this.normalizeSectionsForRole(
            data.sections,
            data.role || this.modalEdit.role,
          );
        }
        const statusText = data.is_active ? "aktywowano" : "zablokowano";
        this.success = `Konto użytkownika ${statusText}.`;
        showToast(this.success, "success");
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd aktualizacji statusu.";
        this.error = message;
        showToast(message, "error");
      } finally {
        this.actionUserId = null;
      }
    },

    async deleteUser(user) {
      if (!this.canManage || !user || typeof user.id !== "number") {
        return;
      }
      if (user.id === this.currentUserId) {
        showToast("Nie możesz usunąć własnego konta administratora.", "warning");
        return;
      }
      if (!window.confirm(`Na pewno chcesz usunąć konto ${user.email}?`)) {
        return;
      }
      const headers = this._headers(false);
      if (!headers) {
        showToast("Brak aktywnej sesji administratora.", "warning");
        return;
      }
      this.actionUserId = user.id;
      this.error = null;
      try {
        const response = await fetch(`/admin/users/${user.id}`, {
          method: "DELETE",
          headers,
        });
        if (response.status === 204) {
          this.users = this.users.filter((item) => item.id !== user.id);
          if (this.modalOpen && this.modalDetail && this.modalDetail.id === user.id) {
            this.closeModal();
          }
          this.success = "Użytkownik został usunięty.";
          showToast(this.success, "success");
          return;
        }
        const detail = await response.json().catch(() => ({}));
        const message = detail?.detail || "Nie udało się usunąć użytkownika.";
        throw new Error(message);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd usuwania użytkownika.";
        this.error = message;
        showToast(message, "error");
      } finally {
        this.actionUserId = null;
      }
    },

    openModal(userId) {
      if (typeof userId !== "number") {
        return;
      }
      this.modalOpen = true;
      this.modalLoading = true;
      this.modalError = null;
      this.modalSuccess = null;
      this.modalPassword = null;
      this.modalDetail = null;
      this.modalEdit.email = "";
      this.modalEdit.firstName = "";
      this.modalEdit.lastName = "";
      this.modalEdit.internalExt = "";
      this.modalEdit.role = "operator";
      this.modalEdit.isSalesperson = false;
      this.modalEdit.crmSalesSmsEnabled = false;
      this.modalEdit.crmSalesEmailEnabled = false;
      this.modalEdit.crmOperationsSmsEnabled = false;
      this.modalEdit.crmOperationsEmailEnabled = false;
      this.modalEdit.mobilePhone = "";
      this.modalEdit.firebirdAppUserId = "";
      this.modalEdit.firebirdAppUserLogin = "";
      this.modalEdit.imapEnabled = false;
      this.modalEdit.imapEmail = "";
      this.modalEdit.imapHost = "";
      this.modalEdit.imapPort = "993";
      this.modalEdit.imapUsername = "";
      this.modalEdit.imapUseSsl = true;
      this.modalEdit.imapFolder = "INBOX";
      this.modalEdit.imapPassword = "";
      this.modalEdit.imapClearPassword = false;
      this.modalEdit.sections = this.defaultSectionsForRole("operator");
      this._loadModal(userId);
    },

    async _loadModal(userId) {
      const headers = this._headers(false);
      if (!headers) {
        this.modalLoading = false;
        this.modalError = "Brak aktywnej sesji administratora.";
        return;
      }
      try {
        const response = await fetch(`/admin/users/${userId}`, {
          headers,
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail?.detail || "Nie udało się wczytać szczegółów użytkownika.");
        }
        const data = await response.json();
        this.modalDetail = data;
        this.modalEdit.email = data.email || "";
        this.modalEdit.firstName = data.first_name || "";
        this.modalEdit.lastName = data.last_name || "";
        this.modalEdit.internalExt = data.internal_ext || "";
        this.modalEdit.role = data.role || "operator";
        this.modalEdit.isSalesperson = Boolean(data.is_salesperson);
        this.modalEdit.crmSalesSmsEnabled = Boolean(data.crm_sales_sms_enabled);
        this.modalEdit.crmSalesEmailEnabled = Boolean(data.crm_sales_email_enabled);
        this.modalEdit.crmOperationsSmsEnabled = Boolean(data.crm_operations_sms_enabled);
        this.modalEdit.crmOperationsEmailEnabled = Boolean(
          data.crm_operations_email_enabled,
        );
        this.modalEdit.mobilePhone = data.mobile_phone || "";
        this.modalEdit.firebirdAppUserId = data.firebird_app_user_id
          ? String(data.firebird_app_user_id)
          : "";
        this.modalEdit.firebirdAppUserLogin = data.firebird_app_user_login || "";
        this.modalEdit.imapEnabled = Boolean(data.imap?.enabled);
        this.modalEdit.imapEmail = data.imap?.email || "";
        this.modalEdit.imapHost = data.imap?.host || "";
        this.modalEdit.imapPort = data.imap?.port ? String(data.imap.port) : "993";
        this.modalEdit.imapUsername = data.imap?.username || "";
        this.modalEdit.imapUseSsl = data.imap?.use_ssl !== false;
        this.modalEdit.imapFolder = data.imap?.folder || "INBOX";
        this.modalEdit.imapPassword = "";
        this.modalEdit.imapClearPassword = false;
        this.modalEdit.sections = this.normalizeSectionsForRole(
          data.sections,
          this.modalEdit.role,
        );
      } catch (err) {
        this.modalError = err instanceof Error ? err.message : "Błąd wczytywania szczegółów.";
      } finally {
        this.modalLoading = false;
      }
    },

    closeModal() {
      this.modalOpen = false;
      this.modalLoading = false;
      this.modalSaving = false;
      this.modalError = null;
      this.modalSuccess = null;
      this.modalPassword = null;
      this.modalDetail = null;
    },

    async saveModal() {
      if (!this.modalDetail || !this.canManage || this.modalSaving) {
        return;
      }
      const headers = this._headers(true);
      if (!headers) {
        this.modalError = "Brak aktywnej sesji administratora.";
        return;
      }
      this.modalSaving = true;
      this.modalError = null;
      this.modalSuccess = null;
      try {
        const payload = this._buildPayload(this.modalEdit);
        const response = await fetch(`/admin/users/${this.modalDetail.id}`, {
          method: "PUT",
          headers,
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail?.detail || "Nie udało się zapisać zmian.");
        }
        const detail = await response.json();
        this.modalDetail = detail;
        this.modalEdit.imapPassword = "";
        this.modalEdit.imapClearPassword = false;
        this.modalSuccess = "Zmiany zostały zapisane.";
        showToast(this.modalSuccess, "success");
        await this.fetchUsers(false);
      } catch (err) {
        this.modalError = err instanceof Error ? err.message : "Błąd zapisu zmian.";
        showToast(this.modalError, "error");
      } finally {
        this.modalSaving = false;
      }
    },
  });

  const databaseConfig = () => ({
    host: "",
    port: "",
    database: "",
    user: "",
    sslmode: "disable",
    password: "",
    passwordSet: false,
    source: "env",
    editable: false,
    lockReason: "",
    saving: false,
    testing: false,
    error: null,
    success: null,
    testStatus: "neutral",
    testMessage: "",

    init() {
      const initial = this._readInitial();
      this.host = initial.host || "";
      this.port = String(initial.port || "");
      this.database = initial.database || "";
      this.user = initial.user || "";
      this.sslmode = initial.sslmode || "disable";
      this.password = "";
      this.passwordSet = Boolean(initial.password_set);
      this.source = initial.source || "env";
      this.editable = Boolean(initial.editable);
      this.lockReason = initial.lock_reason || "";
    },

    _readInitial() {
      try {
        return JSON.parse(this.$el.dataset.initial || "{}");
      } catch (err) {
        console.error("Nie można zdekodować konfiguracji bazy", err);
        return {};
      }
    },

    get headers() {
      const token = localStorage.getItem("admin-session-token");
      const headers = { "Content-Type": "application/json" };
      if (token) {
        headers["X-Admin-Session"] = token;
      }
      return headers;
    },

    resetMessages() {
      this.error = null;
      this.success = null;
    },

    get sourceLabel() {
      return this.source === "env" ? ".env" : this.source;
    },

    async save() {
      if (!this.editable) {
        this.error = this.lockReason || "Ta sekcja jest tylko do odczytu.";
        showToast(this.error, "warning");
        return;
      }
      if (this.saving) {
        return;
      }
      this.resetMessages();
      this.saving = true;
      try {
        const payload = {
          host: this.host,
          port: Number(this.port),
          database: this.database,
          user: this.user,
          sslmode: this.sslmode,
        };
        if (this.password) {
          payload.password = this.password;
        }
        const response = await fetch("/admin/config/database", {
          method: "PUT",
          headers: this.headers,
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail?.detail || "Nie udało się zapisać konfiguracji");
        }
        const data = await response.json();
        this.host = data.host;
        this.port = String(data.port);
        this.database = data.database;
        this.user = data.user;
        this.sslmode = data.sslmode;
        this.password = "";
        this.passwordSet = true;
        this.source = data.source || "env";
        this.editable = Boolean(data.editable);
        this.lockReason = data.lock_reason || "";
        this.success = "Konfiguracja została zapisana.";
        showToast("Zapisano konfigurację bazy", "success");
        this.$el.dataset.initial = JSON.stringify(data);
      } catch (err) {
        this.error = err instanceof Error ? err.message : "Błąd zapisu";
        showToast(this.error, "error");
      } finally {
        this.saving = false;
      }
    },

    async testConnection() {
      if (this.testing) {
        return;
      }
      this.testing = true;
      this.testStatus = null;
      this.testMessage = "Testowanie połączenia…";
      try {
        const token = localStorage.getItem("admin-session-token");
        const headers = token ? { "X-Admin-Session": token } : {};
        const response = await fetch("/admin/status/database", {
          headers,
        });
        if (!response.ok) {
          throw new Error("Błąd testu połączenia");
        }
        const data = await response.json();
        this.testStatus = data.state === "error" ? "warning" : "success";
        this.testMessage = data.details || data.status;
        if (data.state === "error") {
          showToast("Test połączenia nieudany", "warning");
        } else {
          showToast("Test połączenia zakończony sukcesem", "success");
        }
      } catch (err) {
        console.error(err);
        this.testStatus = "warning";
        this.testMessage = err instanceof Error ? err.message : "Błąd testu";
        showToast(this.testMessage, "error");
      } finally {
        this.testing = false;
      }
    },
  });

  const firebirdMsConfig = () => ({
    mode: "network",
    host: "",
    networkHostCache: "",
    port: "",
    database: "",
    user: "",
    password: "",
    charset: "WIN1250",
    role: "",
    localCopyPath: "",
    allowWrites: false,
    passwordSet: false,
    source: "env",
    editable: false,
    lockReason: "",
    saving: false,
    testing: false,
    error: null,
    success: null,
    testStatus: "neutral",
    testMessage: "",

    init() {
      const initial = this._readInitial();
      this.mode = initial.mode || "network";
      this.host = initial.host || "";
      this.networkHostCache = initial.host || "";
      this.port = String(initial.port || "");
      this.database = initial.database || "";
      this.user = initial.user || "";
      this.charset = initial.charset || "WIN1250";
      this.role = initial.role || "";
      this.localCopyPath = initial.local_copy_path || "";
      this.allowWrites = Boolean(initial.allow_writes);
      this.password = "";
      this.passwordSet = Boolean(initial.password_set);
      this.source = initial.source || "env";
      this.editable = Boolean(initial.editable);
      this.lockReason = initial.lock_reason || "";

      this.$watch("mode", (mode, prevMode) => {
        if (mode === prevMode) {
          return;
        }
        if (mode === "local") {
          if (this.host) {
            this.networkHostCache = this.host;
          }
          this.host = "";
        } else if (!this.host) {
          this.host = this.networkHostCache || "";
        }
      });

      if (this.mode === "local") {
        this.host = "";
      }
    },

    get configLabel() {
      return this.$el.dataset.configLabel || "Menadżer Serwisu";
    },

    get saveEndpoint() {
      return this.$el.dataset.saveEndpoint || "/admin/config/firebird";
    },

    get testEndpoint() {
      return this.$el.dataset.testEndpoint || "/admin/firebird/test";
    },

    _readInitial() {
      try {
        return JSON.parse(this.$el.dataset.initial || "{}");
      } catch (err) {
        console.error("Nie można zdekodować konfiguracji Firebird", err);
        return {};
      }
    },

    get headers() {
      const token = localStorage.getItem("admin-session-token");
      const headers = { "Content-Type": "application/json" };
      if (token) {
        headers["X-Admin-Session"] = token;
      }
      return headers;
    },

    resetMessages() {
      this.error = null;
      this.success = null;
    },

    get sourceLabel() {
      return this.source === "env" ? ".env" : this.source;
    },

    async save() {
      if (!this.editable) {
        this.error = this.lockReason || "Ta sekcja jest tylko do odczytu.";
        showToast(this.error, "warning");
        return;
      }
      if (this.saving) {
        return;
      }
      this.resetMessages();
      this.saving = true;
      try {
        const payload = {
          mode: this.mode,
          host: this.mode === "local" ? "" : this.host,
          port: Number(this.port),
          database: this.database,
          user: this.user,
          charset: this.charset,
          role: this.role || null,
          local_copy_path: this.localCopyPath,
          allow_writes: Boolean(this.allowWrites),
        };
        if (this.password) {
          payload.password = this.password;
        }
        const response = await fetch(this.saveEndpoint, {
          method: "PUT",
          headers: this.headers,
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się zapisać konfiguracji Firebird");
        }
        this.host = data.host || "";
        this.mode = data.mode || "network";
        if (this.mode === "network") {
          this.networkHostCache = this.host;
        } else {
          this.host = "";
        }
        this.port = String(data.port || "");
        this.database = data.database || "";
        this.user = data.user || "";
        this.charset = data.charset || "WIN1250";
        this.role = data.role || "";
        this.localCopyPath = data.local_copy_path || "";
        this.allowWrites = Boolean(data.allow_writes);
        this.password = "";
        this.passwordSet = Boolean(data.password_set);
        this.source = data.source || "env";
        this.editable = Boolean(data.editable);
        this.lockReason = data.lock_reason || "";
        this.success = `Konfiguracja Firebird (${this.configLabel}) została zapisana.`;
        showToast(this.success, "success");
        this.$el.dataset.initial = JSON.stringify(data);
      } catch (err) {
        this.error = err instanceof Error ? err.message : "Błąd zapisu";
        showToast(this.error, "error");
      } finally {
        this.saving = false;
      }
    },

    async testConnection() {
      if (this.testing) {
        return;
      }
      this.testing = true;
      this.testStatus = "info";
      this.testMessage = "Testowanie połączenia…";
      try {
        const token = localStorage.getItem("admin-session-token");
        if (!token) {
          throw new Error("Brak aktywnej sesji administratora.");
        }
        const payload = {
          mode: this.mode || "network",
          host: this.mode === "local" ? null : this.host || null,
          port: Number(this.port || 3050),
          database: this.database || null,
          local_copy_path: this.localCopyPath || null,
          user: this.user || null,
          password: this.password || null,
          charset: this.charset || null,
          role: this.role || null,
        };
        const response = await fetch(this.testEndpoint, {
          method: "POST",
          headers: { "X-Admin-Session": token, "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Błąd testu Firebird");
        }
        this.testStatus = data.success ? "success" : "warning";
        const resultMessage =
          data.message
          || (data.success ? "Połączenie zakończone sukcesem." : "Serwer zwrócił błąd.");
        this.testMessage = `${this.configLabel}: ${resultMessage}`;
        showToast(this.testMessage, data.success ? "success" : "warning");
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd testu Firebird";
        this.testStatus = "error";
        this.testMessage = `${this.configLabel}: ${message}`;
        showToast(message, "error");
      } finally {
        this.testing = false;
      }
    },
  });

  const firebirdConfig = firebirdMsConfig;

  const googleSheetsConfig = () => ({
    enabled: true,
    credentialsPath: "",
    spreadsheetId: "",
    workflowDevicesWorksheet: "Urzadzenia_magazyn",
    source: "env",
    editable: false,
    lockReason: "",
    saving: false,
    testing: false,
    bootstrapping: false,
    error: null,
    success: null,
    testStatus: "neutral",
    testMessage: "",
    serviceAccountEmail: "",
    spreadsheetTitle: "",
    worksheetTitle: "",
    missingHeaders: [],
    addedHeaders: [],
    existingHeaders: [],

    init() {
      const initial = this._readInitial();
      this.enabled = initial.enabled !== false;
      this.credentialsPath = initial.credentials_path || "";
      this.spreadsheetId = initial.spreadsheet_id || "";
      this.workflowDevicesWorksheet = initial.workflow_devices_worksheet || "Urzadzenia_magazyn";
      this.source = initial.source || "env";
      this.editable = Boolean(initial.editable);
      this.lockReason = initial.lock_reason || "";
      this.resetResultDetails();
    },

    get saveEndpoint() {
      return this.$el.dataset.saveEndpoint || "/admin/config/google-sheets";
    },

    get testEndpoint() {
      return this.$el.dataset.testEndpoint || "/admin/google-sheets/test";
    },

    get bootstrapEndpoint() {
      return this.$el.dataset.bootstrapEndpoint || "/admin/google-sheets/bootstrap-headers";
    },

    get sourceLabel() {
      return this.source === "env" ? ".env" : this.source;
    },

    _readInitial() {
      try {
        return JSON.parse(this.$el.dataset.initial || "{}");
      } catch (err) {
        console.error("Nie można zdekodować konfiguracji Google Sheets", err);
        return {};
      }
    },

    get headers() {
      const token = localStorage.getItem("admin-session-token");
      const headers = { "Content-Type": "application/json" };
      if (token) {
        headers["X-Admin-Session"] = token;
      }
      return headers;
    },

    resetMessages() {
      this.error = null;
      this.success = null;
    },

    resetResultDetails() {
      this.serviceAccountEmail = "";
      this.spreadsheetTitle = "";
      this.worksheetTitle = "";
      this.missingHeaders = [];
      this.addedHeaders = [];
      this.existingHeaders = [];
    },

    applyResponse(data) {
      this.enabled = data.enabled !== false;
      this.credentialsPath = data.credentials_path || "";
      this.spreadsheetId = data.spreadsheet_id || "";
      this.workflowDevicesWorksheet = data.workflow_devices_worksheet || "Urzadzenia_magazyn";
      this.source = data.source || "env";
      this.editable = Boolean(data.editable);
      this.lockReason = data.lock_reason || "";
      this.$el.dataset.initial = JSON.stringify(data);
    },

    async save() {
      if (!this.editable) {
        this.error = this.lockReason || "Ta sekcja jest tylko do odczytu.";
        showToast(this.error, "warning");
        return;
      }
      if (this.saving) {
        return;
      }
      this.resetMessages();
      this.saving = true;
      try {
        const payload = {
          enabled: Boolean(this.enabled),
          credentials_path: this.credentialsPath,
          spreadsheet_id: this.spreadsheetId,
          workflow_devices_worksheet: this.workflowDevicesWorksheet,
        };
        const response = await fetch(this.saveEndpoint, {
          method: "PUT",
          headers: this.headers,
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się zapisać konfiguracji Google Sheets");
        }
        this.applyResponse(data);
        this.success = "Konfiguracja Google Sheets dla FLOW została zapisana.";
        showToast(this.success, "success");
      } catch (err) {
        this.error = err instanceof Error ? err.message : "Błąd zapisu";
        showToast(this.error, "error");
      } finally {
        this.saving = false;
      }
    },

    async testConnection() {
      if (this.testing) {
        return;
      }
      this.testing = true;
      this.resetMessages();
      this.resetResultDetails();
      this.testStatus = "info";
      this.testMessage = "Testowanie połączenia…";
      try {
        const token = localStorage.getItem("admin-session-token");
        if (!token) {
          throw new Error("Brak aktywnej sesji administratora.");
        }
        const payload = {
          enabled: Boolean(this.enabled),
          credentials_path: this.credentialsPath || null,
          spreadsheet_id: this.spreadsheetId || null,
          workflow_devices_worksheet: this.workflowDevicesWorksheet || null,
        };
        const response = await fetch(this.testEndpoint, {
          method: "POST",
          headers: { "X-Admin-Session": token, "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Błąd testu Google Sheets");
        }
        this.testStatus = data.success ? "success" : "warning";
        this.testMessage = data.message || "";
        this.serviceAccountEmail = data.service_account_email || "";
        this.spreadsheetTitle = data.spreadsheet_title || "";
        this.worksheetTitle = data.worksheet_title || "";
        this.missingHeaders = Array.isArray(data.missing_headers) ? data.missing_headers : [];
        showToast(this.testMessage, data.success ? "success" : "warning");
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd testu Google Sheets";
        this.testStatus = "error";
        this.testMessage = message;
        showToast(message, "error");
      } finally {
        this.testing = false;
      }
    },

    async bootstrapHeaders() {
      if (this.bootstrapping) {
        return;
      }
      this.bootstrapping = true;
      this.resetMessages();
      this.resetResultDetails();
      this.testStatus = "info";
      this.testMessage = "Przygotowywanie nagłówków FLOW…";
      try {
        const token = localStorage.getItem("admin-session-token");
        if (!token) {
          throw new Error("Brak aktywnej sesji administratora.");
        }
        const payload = {
          enabled: Boolean(this.enabled),
          credentials_path: this.credentialsPath || null,
          spreadsheet_id: this.spreadsheetId || null,
          workflow_devices_worksheet: this.workflowDevicesWorksheet || null,
        };
        const response = await fetch(this.bootstrapEndpoint, {
          method: "POST",
          headers: { "X-Admin-Session": token, "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Błąd przygotowania nagłówków FLOW");
        }
        this.testStatus = data.success ? "success" : "warning";
        this.testMessage = data.message || "";
        this.serviceAccountEmail = data.service_account_email || "";
        this.spreadsheetTitle = data.spreadsheet_title || "";
        this.worksheetTitle = data.worksheet_title || "";
        this.addedHeaders = Array.isArray(data.added_headers) ? data.added_headers : [];
        this.existingHeaders = Array.isArray(data.existing_headers) ? data.existing_headers : [];
        showToast(this.testMessage, data.success ? "success" : "warning");
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd przygotowania nagłówków FLOW";
        this.testStatus = "error";
        this.testMessage = message;
        showToast(message, "error");
      } finally {
        this.bootstrapping = false;
      }
    },
  });

  const firebirdVMaintenanceConfig = () => ({
    host: "",
    port: "",
    database: "",
    user: "",
    password: "",
    charset: "WIN1250",
    role: "",
    passwordSet: false,
    source: "env",
    editable: false,
    lockReason: "",
    saving: false,
    testing: false,
    error: null,
    success: null,
    testStatus: "neutral",
    testMessage: "",

    init() {
      const initial = this._readInitial();
      this.host = initial.host || "";
      this.port = String(initial.port || "");
      this.database = initial.database || "";
      this.user = initial.user || "";
      this.charset = initial.charset || "WIN1250";
      this.role = initial.role || "";
      this.password = "";
      this.passwordSet = Boolean(initial.password_set);
      this.source = initial.source || "env";
      this.editable = Boolean(initial.editable);
      this.lockReason = initial.lock_reason || "";
    },

    get configLabel() {
      return this.$el.dataset.configLabel || "v-maintenance";
    },

    get saveEndpoint() {
      return this.$el.dataset.saveEndpoint || "/admin/config/firebird-vmaintenance";
    },

    get testEndpoint() {
      return this.$el.dataset.testEndpoint || "/admin/firebird/test-vmaintenance";
    },

    _readInitial() {
      try {
        return JSON.parse(this.$el.dataset.initial || "{}");
      } catch (err) {
        console.error("Nie można zdekodować konfiguracji Firebird v-maintenance", err);
        return {};
      }
    },

    get headers() {
      const token = localStorage.getItem("admin-session-token");
      const headers = { "Content-Type": "application/json" };
      if (token) {
        headers["X-Admin-Session"] = token;
      }
      return headers;
    },

    resetMessages() {
      this.error = null;
      this.success = null;
    },

    get sourceLabel() {
      return this.source === "env" ? ".env" : this.source;
    },

    async save() {
      if (!this.editable) {
        this.error = this.lockReason || "Ta sekcja jest tylko do odczytu.";
        showToast(this.error, "warning");
        return;
      }
      if (this.saving) {
        return;
      }
      this.resetMessages();
      this.saving = true;
      try {
        const payload = {
          host: this.host,
          port: Number(this.port),
          database: this.database,
          user: this.user,
          charset: this.charset,
          role: this.role || null,
        };
        if (this.password) {
          payload.password = this.password;
        }
        const response = await fetch(this.saveEndpoint, {
          method: "PUT",
          headers: this.headers,
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się zapisać konfiguracji Firebird v-maintenance");
        }
        this.host = data.host || "";
        this.port = String(data.port || "");
        this.database = data.database || "";
        this.user = data.user || "";
        this.charset = data.charset || "WIN1250";
        this.role = data.role || "";
        this.password = "";
        this.passwordSet = Boolean(data.password_set);
        this.source = data.source || "env";
        this.editable = Boolean(data.editable);
        this.lockReason = data.lock_reason || "";
        this.success = `Konfiguracja Firebird (${this.configLabel}) została zapisana.`;
        this.$el.dataset.initial = JSON.stringify(data);
        showToast(this.success, "success");
      } catch (err) {
        this.error = err instanceof Error ? err.message : "Błąd zapisu";
        showToast(this.error, "error");
      } finally {
        this.saving = false;
      }
    },

    async testConnection() {
      if (this.testing) {
        return;
      }
      this.testing = true;
      this.testStatus = "info";
      this.testMessage = "Testowanie połączenia…";
      try {
        const token = localStorage.getItem("admin-session-token");
        if (!token) {
          throw new Error("Brak aktywnej sesji administratora.");
        }
        const payload = {
          host: this.host || null,
          port: Number(this.port || 3050),
          database: this.database || null,
          user: this.user || null,
          password: this.password || null,
          charset: this.charset || null,
          role: this.role || null,
        };
        const response = await fetch(this.testEndpoint, {
          method: "POST",
          headers: { "X-Admin-Session": token, "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Błąd testu Firebird v-maintenance");
        }
        this.testStatus = data.success ? "success" : "warning";
        const resultMessage =
          data.message
          || (data.success ? "Połączenie zakończone sukcesem." : "Serwer zwrócił błąd.");
        this.testMessage = `${this.configLabel}: ${resultMessage}`;
        showToast(this.testMessage, data.success ? "success" : "warning");
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd testu Firebird v-maintenance";
        this.testStatus = "error";
        this.testMessage = `${this.configLabel}: ${message}`;
        showToast(message, "error");
      } finally {
        this.testing = false;
      }
    },
  });

  const kpRepairSourceConfig = () => ({
    csvDirectory: "",
    csvPattern: "DPLAC*.csv",
    emailLookbackMonths: 5,
    csvDirectorySource: "env",
    csvPatternSource: "env",
    csvEditable: false,
    emailLookbackSource: "env",
    emailLookbackEditable: true,
    lockReason: "",
    latestFile: "",
    saving: false,
    testing: false,
    error: null,
    success: null,
    testStatus: "neutral",
    testMessage: "",

    init() {
      const initial = this._readInitial();
      this.csvDirectory = initial.csv_directory || "";
      this.csvPattern = initial.csv_pattern || "DPLAC*.csv";
      this.emailLookbackMonths = Number(initial.email_lookback_months || 5);
      this.csvDirectorySource = initial.csv_directory_source || "env";
      this.csvPatternSource = initial.csv_pattern_source || "env";
      this.csvEditable = Boolean(initial.csv_editable);
      this.emailLookbackSource = initial.email_lookback_source || "env";
      this.emailLookbackEditable = initial.email_lookback_editable !== false;
      this.lockReason = initial.lock_reason || "";
      this.latestFile = "";
    },

    _readInitial() {
      try {
        return JSON.parse(this.$el.dataset.initial || "{}");
      } catch (err) {
        console.error("Nie można zdekodować konfiguracji źródła KP", err);
        return {};
      }
    },

    get headers() {
      const token = localStorage.getItem("admin-session-token");
      const headers = { "Content-Type": "application/json" };
      if (token) {
        headers["X-Admin-Session"] = token;
      }
      return headers;
    },

    resetMessages() {
      this.error = null;
      this.success = null;
    },

    get csvSourceLabel() {
      return this.csvDirectorySource === "env" ? ".env" : this.csvDirectorySource;
    },

    get emailLookbackSourceLabel() {
      return this.emailLookbackSource === "env" ? ".env" : "panel admin";
    },

    async save() {
      if (!this.emailLookbackEditable) {
        this.error = "Ta sekcja jest tylko do odczytu.";
        showToast(this.error, "warning");
        return;
      }
      if (this.saving) {
        return;
      }
      this.resetMessages();
      this.saving = true;
      try {
        const payload = {
          csv_directory: this.csvDirectory,
          csv_pattern: this.csvPattern,
          email_lookback_months: Number(this.emailLookbackMonths || 0),
        };
        const response = await fetch("/admin/config/kp-repair-source", {
          method: "PUT",
          headers: this.headers,
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się zapisać konfiguracji źródła CSV");
        }
        this.csvDirectory = data.csv_directory || "";
        this.csvPattern = data.csv_pattern || "DPLAC*.csv";
        this.emailLookbackMonths = Number(data.email_lookback_months || 0);
        this.csvDirectorySource = data.csv_directory_source || "env";
        this.csvPatternSource = data.csv_pattern_source || "env";
        this.csvEditable = Boolean(data.csv_editable);
        this.emailLookbackSource = data.email_lookback_source || "env";
        this.emailLookbackEditable = data.email_lookback_editable !== false;
        this.lockReason = data.lock_reason || "";
        this.$el.dataset.initial = JSON.stringify(data);
        this.success = "Konfiguracja źródeł KP została zapisana.";
        showToast(this.success, "success");
      } catch (err) {
        this.error = err instanceof Error ? err.message : "Błąd zapisu";
        showToast(this.error, "error");
      } finally {
        this.saving = false;
      }
    },

    async testSource() {
      if (this.testing) {
        return;
      }
      this.testing = true;
      this.testStatus = "info";
      this.testMessage = "Testowanie źródła CSV…";
      this.latestFile = "";
      try {
        const token = localStorage.getItem("admin-session-token");
        if (!token) {
          throw new Error("Brak aktywnej sesji administratora.");
        }
        const response = await fetch("/admin/kp-repair/csv-source/test", {
          method: "POST",
          headers: { "X-Admin-Session": token },
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Błąd testu katalogu CSV");
        }
        this.testStatus = data.success ? "success" : "warning";
        this.testMessage = data.message || "";
        this.latestFile = data.latest_file || "";
        showToast(this.testMessage, data.success ? "success" : "warning");
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd testu katalogu CSV";
        this.testStatus = "error";
        this.testMessage = message;
        showToast(message, "error");
      } finally {
        this.testing = false;
      }
    },
  });

  const kpRepairTool = () => ({
    working: false,
    error: null,
    success: null,
    result: null,
    statusText: "",
    statusVariant: "neutral",
    commitMode: "true",
    emailLookbackMonths: "",
    sourceLookbackMonths: 5,
    summaryLines: [],
    requestTimeoutMs: 600000,
    progressVisible: false,
    progressPercent: 0,
    progressTitle: "",
    progressInfo: "",
    progressStartedAtMs: 0,
    progressTicker: null,

    init() {
      const source = this._readSource();
      this.sourceLookbackMonths = Number(source.email_lookback_months || 5);
      this.emailLookbackMonths = "";
    },

    _readSource() {
      try {
        return JSON.parse(this.$el.dataset.source || "{}");
      } catch (err) {
        console.error("Nie można zdekodować konfiguracji źródeł KP", err);
        return {};
      }
    },

    _headers(includeJson = false) {
      const token = localStorage.getItem("admin-session-token");
      if (!token) {
        throw new Error("Brak aktywnej sesji administratora.");
      }
      const headers = { "X-Admin-Session": token };
      if (includeJson) {
        headers["Content-Type"] = "application/json";
      }
      return headers;
    },

    _requestPayload() {
      const payload = { commit: this.commitMode === "true" };
      const lookbackRaw = String(this.emailLookbackMonths || "").trim();
      if (lookbackRaw.length) {
        payload.email_lookback_months = Number(lookbackRaw);
      }
      return payload;
    },

    _resetMessages() {
      this.error = null;
      this.success = null;
    },

    _formatDuration(seconds) {
      const sec = Math.max(0, Number(seconds || 0));
      if (sec < 60) {
        return `${sec}s`;
      }
      const min = Math.floor(sec / 60);
      const rest = sec % 60;
      return `${min}m ${rest}s`;
    },

    _startProgress(title) {
      this.progressTitle = title;
      this.progressPercent = 4;
      this.progressVisible = true;
      this.progressStartedAtMs = Date.now();
      const timeoutSeconds = Math.round(this.requestTimeoutMs / 1000);
      this.progressInfo = `Czas: 0s / limit ${this._formatDuration(timeoutSeconds)}`;
      if (this.progressTicker) {
        window.clearInterval(this.progressTicker);
      }
      this.progressTicker = window.setInterval(() => {
        const elapsedSeconds = Math.floor((Date.now() - this.progressStartedAtMs) / 1000);
        const suggestedProgress = Math.min(94, 4 + Math.floor(elapsedSeconds * 1.6));
        this.progressPercent = Math.max(this.progressPercent, suggestedProgress);
        this.progressInfo = `Czas: ${this._formatDuration(elapsedSeconds)} / limit ${this._formatDuration(timeoutSeconds)}`;
      }, 1000);
    },

    _stopProgress(markAsDone = false) {
      if (this.progressTicker) {
        window.clearInterval(this.progressTicker);
        this.progressTicker = null;
      }
      if (markAsDone) {
        this.progressPercent = 100;
      }
      if (!this.working) {
        window.setTimeout(() => {
          this.progressVisible = false;
          this.progressInfo = "";
        }, 800);
      }
    },

    async _fetchWithTimeout(url, options = {}) {
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), this.requestTimeoutMs);
      try {
        return await fetch(url, {
          ...options,
          signal: controller.signal,
        });
      } catch (err) {
        if (err && err.name === "AbortError") {
          const timeoutSeconds = Math.round(this.requestTimeoutMs / 1000);
          throw new Error(`Przekroczono limit czasu żądania (${this._formatDuration(timeoutSeconds)}).`);
        }
        throw err;
      } finally {
        window.clearTimeout(timer);
      }
    },

    _setResult(data) {
      this.result = data;
      this.summaryLines = [];
      if (!data || typeof data !== "object") {
        return;
      }
      if (data.marker_counts) {
        this.summaryLines.push(
          `Markery aktualne V/E/R: ${data.marker_counts.V || 0} / ${data.marker_counts.E || 0} / ${data.marker_counts.R || 0}`,
        );
      }
      if (data.marker_counts_before) {
        this.summaryLines.push(
          `Przed V/E/R: ${data.marker_counts_before.V || 0} / ${data.marker_counts_before.E || 0} / ${data.marker_counts_before.R || 0}`,
        );
      }
      if (data.marker_counts_after) {
        this.summaryLines.push(
          `Po V/E/R: ${data.marker_counts_after.V || 0} / ${data.marker_counts_after.E || 0} / ${data.marker_counts_after.R || 0}`,
        );
      }
      if (data.matched_counts) {
        this.summaryLines.push(
          `Kandydaci V/E/R: ${data.matched_counts.V || 0} / ${data.matched_counts.E || 0} / ${data.matched_counts.R || 0}`,
        );
      }
      if (data.candidates !== undefined) {
        this.summaryLines.push(`Kandydaci zmian: ${data.candidates}`);
      }
      if (data.updated !== undefined || data.skipped !== undefined) {
        this.summaryLines.push(`Wynik zmian: updated=${data.updated || 0}, skipped=${data.skipped || 0}, errors=${data.errors || 0}`);
      }
      if (data.source_counts?.EMAIL_LOOKBACK_MONTHS !== undefined) {
        this.summaryLines.push(`EMAIL lookback: ${data.source_counts.EMAIL_LOOKBACK_MONTHS} mies.`);
      }
      if (data.latest_csv_file) {
        this.summaryLines.push(`Najnowszy CSV: ${data.latest_csv_file}`);
      }
    },

    async runSummary() {
      if (this.working) {
        return;
      }
      this._resetMessages();
      this.working = true;
      this.statusVariant = "info";
      this.statusText = "Generowanie raportu…";
      this._startProgress("Generowanie raportu KP…");
      try {
        const response = await this._fetchWithTimeout("/admin/kp-repair/summary", {
          headers: this._headers(false),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się wygenerować raportu.");
        }
        this.success = "Raport został wygenerowany.";
        this.statusVariant = "success";
        this.statusText = data.report_file ? `Raport: ${data.report_file}` : this.success;
        this._setResult(data);
        showToast(this.success, "success");
        this._stopProgress(true);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd raportu KP";
        this.error = message;
        this.statusVariant = "error";
        this.statusText = message;
        showToast(message, "error");
      } finally {
        this.working = false;
        this._stopProgress(false);
      }
    },

    async runClear() {
      if (this.working) {
        return;
      }
      this._resetMessages();
      this.working = true;
      this.statusVariant = "info";
      this.statusText = "Czyszczenie markerów…";
      this._startProgress("Czyszczenie markerów KP…");
      try {
        const response = await this._fetchWithTimeout("/admin/kp-repair/clear", {
          method: "POST",
          headers: this._headers(true),
          body: JSON.stringify(this._requestPayload()),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się wyczyścić markerów.");
        }
        this.success = data.message || "Czyszczenie zakończone.";
        this.statusVariant = "success";
        this.statusText = this.success;
        this._setResult(data);
        showToast(this.success, "success");
        this._stopProgress(true);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd czyszczenia KP";
        this.error = message;
        this.statusVariant = "error";
        this.statusText = message;
        showToast(message, "error");
      } finally {
        this.working = false;
        this._stopProgress(false);
      }
    },

    async runRebuild() {
      if (this.working) {
        return;
      }
      this._resetMessages();
      this.working = true;
      this.statusVariant = "info";
      this.statusText = "Retagowanie markerów…";
      this._startProgress("Retagowanie markerów KP…");
      try {
        const response = await this._fetchWithTimeout("/admin/kp-repair/rebuild", {
          method: "POST",
          headers: this._headers(true),
          body: JSON.stringify(this._requestPayload()),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się retagować markerów.");
        }
        this.success = data.message || "Retagowanie zakończone.";
        this.statusVariant = "success";
        this.statusText = this.success;
        this._setResult(data);
        showToast(this.success, "success");
        this._stopProgress(true);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd retagowania KP";
        this.error = message;
        this.statusVariant = "error";
        this.statusText = message;
        showToast(message, "error");
      } finally {
        this.working = false;
        this._stopProgress(false);
      }
    },
  });

  const ctipConfig = () => ({
    host: "",
    port: "",
    pin: "",
    pinSet: false,
    source: "env",
    editable: false,
    lockReason: "",
    saving: false,
    error: null,
    success: null,

    init() {
      const initial = this._readInitial();
      this.host = initial.host || "";
      this.port = String(initial.port || "");
      this.pin = "";
      this.pinSet = Boolean(initial.pin_set);
      this.source = initial.source || "env";
      this.editable = Boolean(initial.editable);
      this.lockReason = initial.lock_reason || "";
    },

    _readInitial() {
      try {
        return JSON.parse(this.$el.dataset.initial || "{}");
      } catch (err) {
        console.error("Nie można zdekodować konfiguracji CTIP", err);
        return {};
      }
    },

    get pinState() {
      if (this.pinSet || this.pin) {
        return "success";
      }
      return "warning";
    },

    get pinMessage() {
      if (this.pin) {
        return "PIN zostanie zaktualizowany";
      }
      return this.pinSet ? "PIN jest zapisany w konfiguracji" : "Brak PIN-u w konfiguracji";
    },

    get headers() {
      const token = localStorage.getItem("admin-session-token");
      const headers = { "Content-Type": "application/json" };
      if (token) {
        headers["X-Admin-Session"] = token;
      }
      return headers;
    },

    resetMessages() {
      this.error = null;
      this.success = null;
    },

    get sourceLabel() {
      return this.source === "env" ? ".env" : this.source;
    },

    async save() {
      if (!this.editable) {
        this.error = this.lockReason || "Ta sekcja jest tylko do odczytu.";
        showToast(this.error, "warning");
        return;
      }
      if (this.saving) {
        return;
      }
      this.resetMessages();
      this.saving = true;
      try {
        const payload = {
          host: this.host,
          port: Number(this.port),
        };
        if (this.pin) {
          payload.pin = this.pin;
        }

        const response = await fetch("/admin/config/ctip", {
          method: "PUT",
          headers: this.headers,
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail?.detail || "Nie udało się zapisać konfiguracji CTIP");
        }
        const data = await response.json();
        this.host = data.host;
        this.port = String(data.port);
        this.pinSet = Boolean(data.pin_set);
        this.pin = "";
        this.source = data.source || "env";
        this.editable = Boolean(data.editable);
        this.lockReason = data.lock_reason || "";
        this.success = "Konfiguracja została zapisana.";
        showToast("Zapisano konfigurację CTIP", "success");
        this.$el.dataset.initial = JSON.stringify(data);
      } catch (err) {
        this.error = err instanceof Error ? err.message : "Błąd zapisu";
        showToast(this.error, "error");
      } finally {
        this.saving = false;
      }
    },
  });

  const ctipIvrMap = () => ({
    entries: [],
    editingExt: null,
    loading: false,
    saving: false,
    error: null,
    success: null,
    form: {
      ext: "",
      digit: 9,
      smsText: "",
      enabled: true,
    },
    token: "",

    init() {
      this.token = this.$el.dataset.token || "";
      this.entries = this._readItems();
      if (this.entries.length) {
        this.edit(this.entries[0]);
      }
    },

    _readItems() {
      try {
        const payload = JSON.parse(this.$el.dataset.items || "[]");
        return Array.isArray(payload) ? payload : [];
      } catch (err) {
        console.error("Nie można zdekodować mapowania IVR", err);
        return [];
      }
    },

    _headers() {
      const headers = { "Content-Type": "application/json" };
      const token = this.token || localStorage.getItem("admin-session-token");
      if (token) {
        headers["X-Admin-Session"] = token;
      }
      return headers;
    },

    _normalizeExt(value) {
      return (value || "").trim();
    },

    resetMessages() {
      this.error = null;
      this.success = null;
    },

    resetForm() {
      this.editingExt = null;
      this.form = {
        ext: "",
        digit: 9,
        smsText: "",
        enabled: true,
      };
      this.resetMessages();
    },

    edit(entry) {
      if (!entry) {
        this.resetForm();
        return;
      }
      this.editingExt = entry.ext;
      this.form.ext = entry.ext;
      this.form.digit = entry.digit;
      this.form.smsText = entry.sms_text || entry.smsText || "";
      this.form.enabled = Boolean(entry.enabled);
      this.resetMessages();
    },

    async refresh() {
      this.loading = true;
      try {
        const response = await fetch("/admin/ctip/ivr-map", {
          headers: this._headers(),
        });
        if (!response.ok) {
          throw new Error("Nie udało się odczytać konfiguracji IVR");
        }
        const data = await response.json();
        this.entries = Array.isArray(data.items) ? data.items : [];
        if (this.entries.length && (!this.editingExt || !this.entries.find((item) => item.ext === this.editingExt))) {
          this.edit(this.entries[0]);
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd odczytu";
        this.error = message;
        showToast(message, "error");
      } finally {
        this.loading = false;
      }
    },

    async save() {
      if (this.saving) {
        return;
      }
      this.resetMessages();
      const payload = {
        digit: Number(this.form.digit),
        sms_text: this.form.smsText.trim(),
        enabled: Boolean(this.form.enabled),
      };
      const ext = this._normalizeExt(this.form.ext);
      if (!ext) {
        this.error = "Podaj numer wewnętrzny.";
        return;
      }
      if (!payload.sms_text.length) {
        this.error = "Treść wiadomości nie może być pusta.";
        return;
      }
      this.saving = true;
      try {
        const isUpdate = Boolean(this.editingExt && this.editingExt === ext);
        const url = isUpdate ? `/admin/ctip/ivr-map/${encodeURIComponent(ext)}` : "/admin/ctip/ivr-map";
        const method = isUpdate ? "PUT" : "POST";
        const body = isUpdate ? JSON.stringify(payload) : JSON.stringify({ ...payload, ext });
        const response = await fetch(url, {
          method,
          headers: this._headers(),
          body,
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail?.detail || "Nie udało się zapisać konfiguracji.");
        }
        const saved = await response.json();
        const existingIndex = this.entries.findIndex((item) => item.ext === saved.ext);
        if (existingIndex >= 0) {
          this.entries.splice(existingIndex, 1, saved);
        } else {
          this.entries = [saved, ...this.entries];
        }
        this.success = "Zapisano konfigurację.";
        showToast("Zapisano mapowanie IVR.", "success");
        this.edit(saved);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd zapisu";
        this.error = message;
        showToast(message, "error");
      } finally {
        this.saving = false;
      }
    },

    async remove(entry) {
      if (!entry || this.saving) {
        return;
      }
      if (!window.confirm(`Usunąć mapowanie dla numeru ${entry.ext}?`)) {
        return;
      }
      this.resetMessages();
      try {
        const response = await fetch(`/admin/ctip/ivr-map/${encodeURIComponent(entry.ext)}`, {
          method: "DELETE",
          headers: this._headers(),
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail?.detail || "Nie udało się usunąć mapowania.");
        }
        this.entries = this.entries.filter((item) => item.ext !== entry.ext);
        showToast("Usunięto mapowanie IVR.", "success");
        if (this.editingExt === entry.ext) {
          this.resetForm();
          if (this.entries.length) {
            this.edit(this.entries[0]);
          }
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd usuwania";
        this.error = message;
        showToast(message, "error");
      }
    },
  });

  const smsConfig = () => ({
    apiUrl: "",
    defaultSender: "CTIP-Test",
    smsType: "eco+",
    apiUsername: "",
    apiPassword: "",
    apiToken: "",
    tokenSet: false,
    passwordSet: false,
    testMode: true,
    source: "env",
    editable: false,
    lockReason: "",
    saving: false,
    error: null,
    success: null,
    testing: false,
    testDest: "",
    testText: "Test wysyłki z CTIP",
    testError: null,
    testSuccess: null,

    init() {
      const initial = this._readInitial();
      this.apiUrl = initial.api_url || "";
      this.defaultSender = initial.default_sender || "CTIP-Test";
      this.smsType = initial.sms_type || "eco+";
      this.apiUsername = initial.api_username || "";
      this.apiPassword = "";
      this.apiToken = "";
      this.tokenSet = Boolean(initial.api_token_set);
      this.passwordSet = Boolean(initial.api_password_set);
      this.source = initial.source || "env";
      this.editable = Boolean(initial.editable);
      this.lockReason = initial.lock_reason || "";
      const rawTestMode = initial.test_mode;
      if (typeof rawTestMode === "string") {
        this.testMode = ["1", "true", "t", "yes", "on"].includes(rawTestMode.toLowerCase());
      } else if (rawTestMode === undefined || rawTestMode === null) {
        this.testMode = true;
      } else {
        this.testMode = Boolean(rawTestMode);
      }
    },

    _readInitial() {
      try {
        return JSON.parse(this.$el.dataset.initial || "{}");
      } catch (err) {
        console.error("Nie można zdekodować konfiguracji SMS", err);
        return {};
      }
    },

    get headers() {
      const token = localStorage.getItem("admin-session-token");
      const headers = { "Content-Type": "application/json" };
      if (token) {
        headers["X-Admin-Session"] = token;
      }
      return headers;
    },

    resetMessages() {
      this.error = null;
      this.success = null;
    },

    get sourceLabel() {
      return this.source === "env" ? ".env" : this.source;
    },

    async save() {
      if (!this.editable) {
        this.error = this.lockReason || "Ta sekcja jest tylko do odczytu.";
        showToast(this.error, "warning");
        return;
      }
      if (this.saving) {
        return;
      }
      this.resetMessages();
      this.saving = true;
      try {
        const payload = {
          api_url: this.apiUrl,
          default_sender: this.defaultSender,
          sms_type: this.smsType,
          api_username: this.apiUsername || null,
          test_mode: Boolean(this.testMode),
        };
        if (this.apiPassword) {
          payload.api_password = this.apiPassword;
        }
        if (this.apiToken) {
          payload.api_token = this.apiToken;
        }

        const response = await fetch("/admin/config/sms", {
          method: "PUT",
          headers: this.headers,
          body: JSON.stringify(payload),
        });
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}));
          throw new Error(detail?.detail || "Nie udało się zapisać konfiguracji SMS");
        }
        const data = await response.json();
        this.apiUrl = data.api_url;
        this.defaultSender = data.default_sender;
        this.smsType = data.sms_type;
        this.apiUsername = data.api_username || "";
        this.testMode = Boolean(data.test_mode);
        this.tokenSet = Boolean(data.api_token_set);
        this.passwordSet = Boolean(data.api_password_set);
        this.apiPassword = "";
        this.apiToken = "";
        this.source = data.source || "env";
        this.editable = Boolean(data.editable);
        this.lockReason = data.lock_reason || "";
        this.success = "Konfiguracja zapisana.";
        showToast("Zapisano konfigurację SerwerSMS", "success");
        this.$el.dataset.initial = JSON.stringify(data);
      } catch (err) {
        this.error = err instanceof Error ? err.message : "Błąd zapisu";
        showToast(this.error, "error");
      } finally {
        this.saving = false;
      }
    },

    async sendTest() {
      if (this.testing) {
        return;
      }
      this.testError = null;
      this.testSuccess = null;
      this.testing = true;
      try {
        const response = await fetch("/admin/sms/test", {
          method: "POST",
          headers: this.headers,
          body: JSON.stringify({ dest: this.testDest, text: this.testText }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się wysłać testu");
        }
        if (data.success) {
          this.testSuccess = data.message || "Wiadomość testowa wysłana.";
          this.testError = null;
          showToast(this.testSuccess, "success");
        } else {
          this.testError = data.message || "Serwer zwrócił błąd";
          this.testSuccess = null;
          showToast(this.testError, "warning");
        }
      } catch (err) {
        this.testError = err instanceof Error ? err.message : "Błąd wysyłki";
        showToast(this.testError, "error");
      } finally {
        this.testing = false;
      }
    },
  });

  const callSmsConfig = () => ({
    enabled: false,
    inboundEnabled: true,
    outboundEnabled: false,
    inboundAnsweredEnabled: false,
    inboundAnsweredText: "",
    inboundMissedEnabled: false,
    inboundMissedText: "",
    inboundRepeatAnsweredEnabled: false,
    inboundRepeatAnsweredText: "",
    inboundRepeatMissedEnabled: false,
    inboundRepeatMissedText: "",
    outboundAnsweredEnabled: false,
    outboundAnsweredText: "",
    outboundMissedEnabled: false,
    outboundMissedText: "",
    outboundRepeatAnsweredEnabled: false,
    outboundRepeatAnsweredText: "",
    outboundRepeatMissedEnabled: false,
    outboundRepeatMissedText: "",
    afterHoursEnabled: false,
    afterHoursText: "",
    afterHoursExts: "",
    cooldownMode: "after_days",
    cooldownDays: 30,
    optOutNumbers: "",
    saving: false,
    error: null,
    success: null,
    bulkText: "",
    bulkDays: "",
    bulkDirection: "IN",
    bulkSending: false,
    bulkError: null,
    bulkSuccess: null,

    init() {
      const initial = this._readInitial();
      this.enabled = Boolean(initial.enabled);
      this.inboundEnabled = Boolean(initial.inbound_enabled);
      this.outboundEnabled = Boolean(initial.outbound_enabled);
      this.inboundAnsweredEnabled = Boolean(initial.inbound_answered_enabled);
      this.inboundAnsweredText = initial.inbound_answered_text || "";
      this.inboundMissedEnabled = Boolean(initial.inbound_missed_enabled);
      this.inboundMissedText = initial.inbound_missed_text || "";
      this.inboundRepeatAnsweredEnabled = Boolean(initial.inbound_repeat_answered_enabled);
      this.inboundRepeatAnsweredText = initial.inbound_repeat_answered_text || "";
      this.inboundRepeatMissedEnabled = Boolean(initial.inbound_repeat_missed_enabled);
      this.inboundRepeatMissedText = initial.inbound_repeat_missed_text || "";
      this.outboundAnsweredEnabled = Boolean(initial.outbound_answered_enabled);
      this.outboundAnsweredText = initial.outbound_answered_text || "";
      this.outboundMissedEnabled = Boolean(initial.outbound_missed_enabled);
      this.outboundMissedText = initial.outbound_missed_text || "";
      this.outboundRepeatAnsweredEnabled = Boolean(initial.outbound_repeat_answered_enabled);
      this.outboundRepeatAnsweredText = initial.outbound_repeat_answered_text || "";
      this.outboundRepeatMissedEnabled = Boolean(initial.outbound_repeat_missed_enabled);
      this.outboundRepeatMissedText = initial.outbound_repeat_missed_text || "";
      this.afterHoursEnabled = Boolean(initial.after_hours_enabled);
      this.afterHoursText = initial.after_hours_text || "";
      this.afterHoursExts = initial.after_hours_exts || "";
      this.cooldownMode = initial.cooldown_mode || "after_days";
      this.cooldownDays = Number(initial.cooldown_days || 30);
      this.optOutNumbers = initial.opt_out_numbers || "";
    },

    _readInitial() {
      try {
        return JSON.parse(this.$el.dataset.initial || "{}");
      } catch (err) {
        console.error("Nie można zdekodować konfiguracji SMS połączeń", err);
        return {};
      }
    },

    get headers() {
      const token = localStorage.getItem("admin-session-token");
      const headers = { "Content-Type": "application/json" };
      if (token) {
        headers["X-Admin-Session"] = token;
      }
      return headers;
    },

    resetMessages() {
      this.error = null;
      this.success = null;
    },

    validate() {
      if (!this.enabled) {
        return null;
      }
      const ensureText = (isEnabled, text, label) => {
        if (isEnabled && !text.trim()) {
          return `Uzupełnij treść dla scenariusza: ${label}.`;
        }
        return null;
      };
      if (this.inboundEnabled) {
        const inboundError =
          ensureText(this.inboundAnsweredEnabled, this.inboundAnsweredText, "przychodzące odebrane")
          || ensureText(this.inboundMissedEnabled, this.inboundMissedText, "przychodzące nieodebrane")
          || ensureText(this.inboundRepeatAnsweredEnabled, this.inboundRepeatAnsweredText, "przychodzące ponowne (odebrane)")
          || ensureText(this.inboundRepeatMissedEnabled, this.inboundRepeatMissedText, "przychodzące ponowne (nieodebrane)");
        if (inboundError) {
          return inboundError;
        }
      }
      if (this.outboundEnabled) {
        const outboundError =
          ensureText(this.outboundAnsweredEnabled, this.outboundAnsweredText, "wychodzące odebrane")
          || ensureText(this.outboundMissedEnabled, this.outboundMissedText, "wychodzące nieodebrane")
          || ensureText(this.outboundRepeatAnsweredEnabled, this.outboundRepeatAnsweredText, "wychodzące ponowne (odebrane)")
          || ensureText(this.outboundRepeatMissedEnabled, this.outboundRepeatMissedText, "wychodzące ponowne (nieodebrane)");
        if (outboundError) {
          return outboundError;
        }
      }
      if (this.afterHoursEnabled) {
        const afterHoursError = ensureText(true, this.afterHoursText, "po godzinach pracy");
        if (afterHoursError) {
          return afterHoursError;
        }
        if (!String(this.afterHoursExts || "").trim()) {
          return "Podaj numery wewnetrzne dla scenariusza po godzinach pracy.";
        }
      }
      return null;
    },

    async save() {
      if (this.saving) {
        return;
      }
      this.resetMessages();
      const validationError = this.validate();
      if (validationError) {
        this.error = validationError;
        showToast(validationError, "warning");
        return;
      }
      this.saving = true;
      try {
        const payload = {
          enabled: Boolean(this.enabled),
          inbound_enabled: Boolean(this.inboundEnabled),
          outbound_enabled: Boolean(this.outboundEnabled),
          inbound_answered_enabled: Boolean(this.inboundAnsweredEnabled),
          inbound_answered_text: this.inboundAnsweredText || "",
          inbound_missed_enabled: Boolean(this.inboundMissedEnabled),
          inbound_missed_text: this.inboundMissedText || "",
          inbound_repeat_answered_enabled: Boolean(this.inboundRepeatAnsweredEnabled),
          inbound_repeat_answered_text: this.inboundRepeatAnsweredText || "",
          inbound_repeat_missed_enabled: Boolean(this.inboundRepeatMissedEnabled),
          inbound_repeat_missed_text: this.inboundRepeatMissedText || "",
          outbound_answered_enabled: Boolean(this.outboundAnsweredEnabled),
          outbound_answered_text: this.outboundAnsweredText || "",
          outbound_missed_enabled: Boolean(this.outboundMissedEnabled),
          outbound_missed_text: this.outboundMissedText || "",
          outbound_repeat_answered_enabled: Boolean(this.outboundRepeatAnsweredEnabled),
          outbound_repeat_answered_text: this.outboundRepeatAnsweredText || "",
          outbound_repeat_missed_enabled: Boolean(this.outboundRepeatMissedEnabled),
          outbound_repeat_missed_text: this.outboundRepeatMissedText || "",
          after_hours_enabled: Boolean(this.afterHoursEnabled),
          after_hours_text: this.afterHoursText || "",
          after_hours_exts: this.afterHoursExts || "",
          cooldown_mode: this.cooldownMode || "after_days",
          cooldown_days: Number(this.cooldownDays || 30),
          opt_out_numbers: this.optOutNumbers || "",
        };
        const response = await fetch("/admin/call-sms/config", {
          method: "PUT",
          headers: this.headers,
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się zapisać konfiguracji.");
        }
        this.enabled = Boolean(data.enabled);
        this.inboundEnabled = Boolean(data.inbound_enabled);
        this.outboundEnabled = Boolean(data.outbound_enabled);
        this.inboundAnsweredEnabled = Boolean(data.inbound_answered_enabled);
        this.inboundAnsweredText = data.inbound_answered_text || "";
        this.inboundMissedEnabled = Boolean(data.inbound_missed_enabled);
        this.inboundMissedText = data.inbound_missed_text || "";
        this.inboundRepeatAnsweredEnabled = Boolean(data.inbound_repeat_answered_enabled);
        this.inboundRepeatAnsweredText = data.inbound_repeat_answered_text || "";
        this.inboundRepeatMissedEnabled = Boolean(data.inbound_repeat_missed_enabled);
        this.inboundRepeatMissedText = data.inbound_repeat_missed_text || "";
        this.outboundAnsweredEnabled = Boolean(data.outbound_answered_enabled);
        this.outboundAnsweredText = data.outbound_answered_text || "";
        this.outboundMissedEnabled = Boolean(data.outbound_missed_enabled);
        this.outboundMissedText = data.outbound_missed_text || "";
        this.outboundRepeatAnsweredEnabled = Boolean(data.outbound_repeat_answered_enabled);
        this.outboundRepeatAnsweredText = data.outbound_repeat_answered_text || "";
        this.outboundRepeatMissedEnabled = Boolean(data.outbound_repeat_missed_enabled);
        this.outboundRepeatMissedText = data.outbound_repeat_missed_text || "";
        this.afterHoursEnabled = Boolean(data.after_hours_enabled);
        this.afterHoursText = data.after_hours_text || "";
        this.afterHoursExts = data.after_hours_exts || "";
        this.cooldownMode = data.cooldown_mode || "after_days";
        this.cooldownDays = Number(data.cooldown_days || 30);
        this.optOutNumbers = data.opt_out_numbers || "";
        this.success = "Konfiguracja została zapisana.";
        showToast(this.success, "success");
        this.$el.dataset.initial = JSON.stringify(data);
      } catch (err) {
        this.error = err instanceof Error ? err.message : "Błąd zapisu";
        showToast(this.error, "error");
      } finally {
        this.saving = false;
      }
    },

    async sendBulk() {
      if (this.bulkSending) {
        return;
      }
      this.bulkError = null;
      this.bulkSuccess = null;
      const message = (this.bulkText || "").trim();
      if (!message) {
        this.bulkError = "Podaj treść wiadomości do wysyłki masowej.";
        showToast(this.bulkError, "warning");
        return;
      }
      this.bulkSending = true;
      try {
        const payload = {
          text: message,
          direction: this.bulkDirection || "IN",
        };
        const days = Number(this.bulkDays || 0);
        if (days > 0) {
          payload.days_back = days;
        }
        const response = await fetch("/admin/call-sms/bulk", {
          method: "POST",
          headers: this.headers,
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się uruchomić wysyłki masowej.");
        }
        this.bulkSuccess = `Dodano do kolejki: ${data.created} (unikalne: ${data.total_unique}).`;
        showToast(this.bulkSuccess, "success");
      } catch (err) {
        this.bulkError = err instanceof Error ? err.message : "Błąd wysyłki";
        showToast(this.bulkError, "error");
      } finally {
        this.bulkSending = false;
      }
    },
  });

  const formHandlingConfig = () => ({
    publicBaseUrl: "",
    inviteSmsTemplate: "",
    inviteEmailSubject: "",
    inviteEmailBody: "",
    submissionEmailSubject: "",
    submissionEmailBody: "",
    ownerSmsTemplate: "",
    saving: false,
    error: null,
    success: null,

    init() {
      const initial = this._readInitial();
      this.publicBaseUrl = initial.public_base_url || "";
      this.inviteSmsTemplate = initial.invite_sms_template || "";
      this.inviteEmailSubject = initial.invite_email_subject || "";
      this.inviteEmailBody = initial.invite_email_body || "";
      this.submissionEmailSubject = initial.submission_email_subject || "";
      this.submissionEmailBody = initial.submission_email_body || "";
      this.ownerSmsTemplate = initial.owner_sms_template || "";
    },

    _readInitial() {
      try {
        return JSON.parse(this.$el.dataset.initial || "{}");
      } catch (err) {
        console.error("Nie można zdekodować konfiguracji obsługi formularza", err);
        return {};
      }
    },

    get headers() {
      const token = localStorage.getItem("admin-session-token");
      const headers = { "Content-Type": "application/json" };
      if (token) {
        headers["X-Admin-Session"] = token;
      }
      return headers;
    },

    get statusState() {
      if (!this.publicBaseUrl) {
        return "warning";
      }
      return this.publicBaseUrl.startsWith("https://") ? "success" : "warning";
    },

    get statusMessage() {
      if (!this.publicBaseUrl) {
        return "Brak adresu publicznego";
      }
      if (this.publicBaseUrl.startsWith("https://")) {
        return this.publicBaseUrl;
      }
      return `${this.publicBaseUrl} (zalecane HTTPS)`;
    },

    get previewValues() {
      const baseUrl = (this.publicBaseUrl || "https://form.twoja-domena.pl")
        .trim()
        .replace(/\/+$/, "");
      return {
        form_url: `${baseUrl}/formularz/przykladowy-token-2026`,
        expires_at: "2026-04-30 23:59:59 CEST",
        customer_name: "Jan Kowalski",
        company_name: "Ksero-Partner Sp. z o.o.",
        sender_name: "Biuro Ksero-Partner",
      };
    },

    renderPreview(template) {
      let rendered = String(template || "");
      const values = this.previewValues;
      Object.entries(values).forEach(([key, value]) => {
        rendered = rendered.replaceAll(`{${key}}`, value);
      });
      return rendered;
    },

    get inviteSmsPreview() {
      return this.renderPreview(this.inviteSmsTemplate);
    },

    get inviteEmailSubjectPreview() {
      return this.renderPreview(this.inviteEmailSubject);
    },

    get inviteEmailBodyPreview() {
      return this.renderPreview(this.inviteEmailBody);
    },

    get submissionEmailSubjectPreview() {
      return this.renderPreview(this.submissionEmailSubject);
    },

    get submissionEmailBodyPreview() {
      return this.renderPreview(this.submissionEmailBody);
    },

    get ownerSmsPreview() {
      return this.renderPreview(this.ownerSmsTemplate);
    },

    resetMessages() {
      this.error = null;
      this.success = null;
    },

    async save() {
      if (this.saving) {
        return;
      }
      this.resetMessages();
      this.saving = true;
      try {
        const payload = {
          public_base_url: this.publicBaseUrl || "",
          invite_sms_template: this.inviteSmsTemplate || "",
          invite_email_subject: this.inviteEmailSubject || "",
          invite_email_body: this.inviteEmailBody || "",
          submission_email_subject: this.submissionEmailSubject || "",
          submission_email_body: this.submissionEmailBody || "",
          owner_sms_template: this.ownerSmsTemplate || "",
        };
        const response = await fetch("/admin/config/form-handling", {
          method: "PUT",
          headers: this.headers,
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się zapisać konfiguracji formularza.");
        }
        this.publicBaseUrl = data.public_base_url || "";
        this.inviteSmsTemplate = data.invite_sms_template || "";
        this.inviteEmailSubject = data.invite_email_subject || "";
        this.inviteEmailBody = data.invite_email_body || "";
        this.submissionEmailSubject = data.submission_email_subject || "";
        this.submissionEmailBody = data.submission_email_body || "";
        this.ownerSmsTemplate = data.owner_sms_template || "";
        this.success = "Konfiguracja obsługi formularza została zapisana.";
        this.$el.dataset.initial = JSON.stringify(data);
        showToast(this.success, "success");
      } catch (err) {
        this.error =
          err instanceof Error ? err.message : "Błąd zapisu konfiguracji formularza.";
        showToast(this.error, "error");
      } finally {
        this.saving = false;
      }
    },
  });

  const emailConfig = () => ({
    host: "",
    port: "587",
    username: "",
    password: "",
    passwordSet: false,
    senderName: "",
    senderAddress: "",
    useTls: true,
    useSsl: false,
    source: "env",
    editable: false,
    lockReason: "",
    saving: false,
    testing: false,
    error: null,
    success: null,
    testStatus: "info",
    testMessage: "",
    testDest: "",
    testSubject: "Test konfiguracji SMTP",
    testBody: "To jest wiadomość testowa wysłana z panelu CTIP.",
    sendingTest: false,
    mailError: null,
    mailSuccess: null,

    init() {
      const initial = this._readInitial();
      this.host = initial.host || "";
      this.port = String(initial.port ?? 587);
      this.username = initial.username || "";
      this.passwordSet = Boolean(initial.password_set);
      this.senderName = initial.sender_name || "";
      this.senderAddress = initial.sender_address || "";
      this.useTls = Boolean(initial.use_tls);
      this.useSsl = Boolean(initial.use_ssl);
      this.source = initial.source || "env";
      this.editable = Boolean(initial.editable);
      this.lockReason = initial.lock_reason || "";
      if (this.useTls && this.useSsl) {
        this.useSsl = false;
      }
    },

    _readInitial() {
      try {
        return JSON.parse(this.$el.dataset.initial || "{}");
      } catch (err) {
        console.error("Nie można zdekodować konfiguracji SMTP", err);
        return {};
      }
    },

    get headers() {
      const token = localStorage.getItem("admin-session-token");
      const headers = { "Content-Type": "application/json" };
      if (token) {
        headers["X-Admin-Session"] = token;
      }
      return headers;
    },

    get encryptionMessage() {
      if (this.useTls && this.useSsl) {
        return "Wybierz tylko jeden tryb szyfrowania.";
      }
      if (!this.useTls && !this.useSsl) {
        return "Połączenie bez szyfrowania.";
      }
      return this.useTls ? "Połączenie STARTTLS." : "Połączenie SSL/TLS.";
    },

    get statusState() {
      if (this.host && this.senderAddress) {
        return "success";
      }
      return "warning";
    },

    get statusMessage() {
      if (this.host && this.senderAddress) {
        return `${this.host}:${this.port}`;
      }
      return "Konfiguracja niepełna";
    },

    get sourceLabel() {
      return this.source === "env" ? ".env" : this.source;
    },

    handleToggle(mode) {
      if (mode === "tls" && this.useTls) {
        this.useSsl = false;
      }
      if (mode === "ssl" && this.useSsl) {
        this.useTls = false;
      }
    },

    resetMessages() {
      this.error = null;
      this.success = null;
      this.testMessage = "";
      this.testStatus = "info";
      this.mailError = null;
      this.mailSuccess = null;
    },

    async save() {
      if (!this.editable) {
        this.error = this.lockReason || "Ta sekcja jest tylko do odczytu.";
        showToast(this.error, "warning");
        return;
      }
      if (this.saving) {
        return;
      }
      this.resetMessages();
      this.saving = true;
      try {
        const payload = {
          host: this.host,
          port: Number(this.port || 587),
          username: this.username || null,
          password: this.password || null,
          sender_name: this.senderName || null,
          sender_address: this.senderAddress || null,
          use_tls: Boolean(this.useTls),
          use_ssl: Boolean(this.useSsl),
        };
        const response = await fetch("/admin/config/email", {
          method: "PUT",
          headers: this.headers,
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Nie udało się zapisać konfiguracji SMTP");
        }
        this.host = data.host || "";
        this.port = String(data.port ?? 587);
        this.username = data.username || "";
        this.senderName = data.sender_name || "";
        this.senderAddress = data.sender_address || "";
        this.useTls = Boolean(data.use_tls);
        this.useSsl = Boolean(data.use_ssl);
        if (this.useTls && this.useSsl) {
          this.useSsl = false;
        }
        this.password = "";
        this.passwordSet = Boolean(data.password_set);
        this.source = data.source || "env";
        this.editable = Boolean(data.editable);
        this.lockReason = data.lock_reason || "";
        this.success = "Konfiguracja SMTP została zapisana.";
        showToast(this.success, "success");
        this.$el.dataset.initial = JSON.stringify(data);
      } catch (err) {
        this.error = err instanceof Error ? err.message : "Błąd zapisu";
        showToast(this.error, "error");
      } finally {
        this.saving = false;
      }
    },

    async testConnection() {
      if (this.testing) {
        return;
      }
      this.testMessage = "Testowanie połączenia…";
      this.testStatus = "info";
      this.testing = true;
      try {
        const token = localStorage.getItem("admin-session-token");
        if (!token) {
          throw new Error("Brak aktywnej sesji administratora.");
        }
        const payload = {
          host: this.host || null,
          port: Number(this.port || 587),
          username: this.username || null,
          password: this.password || null,
          sender_name: this.senderName || null,
          sender_address: this.senderAddress || null,
          use_tls: Boolean(this.useTls),
          use_ssl: Boolean(this.useSsl),
        };
        const response = await fetch("/admin/email/test", {
          method: "POST",
          headers: { "X-Admin-Session": token, "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Błąd testu SMTP");
        }
        this.testStatus = data.success ? "success" : "warning";
        this.testMessage = data.message || (data.success ? "Połączenie zakończone sukcesem." : "Serwer zwrócił błąd.");
        showToast(this.testMessage, data.success ? "success" : "warning");
      } catch (err) {
        const message = err instanceof Error ? err.message : "Błąd testu SMTP";
        this.testStatus = "error";
        this.testMessage = message;
        showToast(message, "error");
      } finally {
        this.testing = false;
      }
    },

    async sendTestEmail() {
      if (this.sendingTest) {
        return;
      }
      this.mailError = null;
      this.mailSuccess = null;
      const token = localStorage.getItem("admin-session-token");
      if (!token) {
        this.mailError = "Brak aktywnej sesji administratora.";
        showToast(this.mailError, "warning");
        return;
      }
      const dest = (this.testDest || "").trim();
      if (!dest) {
        this.mailError = "Podaj adres docelowy.";
        showToast(this.mailError, "warning");
        return;
      }
      this.sendingTest = true;
      try {
        const payload = {
          host: this.host || null,
          port: Number(this.port || 587),
          username: this.username || null,
          password: this.password || null,
          sender_name: this.senderName || null,
          sender_address: this.senderAddress || null,
          use_tls: Boolean(this.useTls),
          use_ssl: Boolean(this.useSsl),
          test_recipient: dest,
          test_subject: this.testSubject || "Test konfiguracji SMTP",
          test_body: this.testBody || "To jest wiadomość testowa wysłana z panelu CTIP.",
        };
        const response = await fetch("/admin/email/test", {
          method: "POST",
          headers: { "X-Admin-Session": token, "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(data?.detail || "Wysyłka testowa zakończona błędem.");
        }
        if (data.success) {
          this.mailSuccess = data.message || "Wiadomość testowa została wysłana.";
          showToast(this.mailSuccess, "success");
        } else {
          this.mailError = data.message || "Serwer SMTP zwrócił błąd podczas wysyłki.";
          showToast(this.mailError, "warning");
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "Nie udało się wysłać wiadomości testowej.";
        this.mailError = message;
        showToast(message, "error");
      } finally {
        this.sendingTest = false;
      }
    },
  });

Alpine.data("adminApp", adminApp);
Alpine.data("adminUsers", adminUsers);
Alpine.data("databaseConfig", databaseConfig);
Alpine.data("firebirdMsConfig", firebirdMsConfig);
Alpine.data("firebirdConfig", firebirdConfig);
Alpine.data("googleSheetsConfig", googleSheetsConfig);
Alpine.data("firebirdVMaintenanceConfig", firebirdVMaintenanceConfig);
Alpine.data("kpRepairSourceConfig", kpRepairSourceConfig);
Alpine.data("kpRepairTool", kpRepairTool);
Alpine.data("ctipConfig", ctipConfig);
Alpine.data("ctipIvrMap", ctipIvrMap);
Alpine.data("smsConfig", smsConfig);
Alpine.data("callSmsConfig", callSmsConfig);
Alpine.data("formHandlingConfig", formHandlingConfig);
Alpine.data("emailConfig", emailConfig);

  window.adminApp = adminApp;
  window.adminUsers = adminUsers;
  window.databaseConfig = databaseConfig;
  window.firebirdMsConfig = firebirdMsConfig;
  window.firebirdConfig = firebirdConfig;
window.googleSheetsConfig = googleSheetsConfig;
window.firebirdVMaintenanceConfig = firebirdVMaintenanceConfig;
window.kpRepairSourceConfig = kpRepairSourceConfig;
window.kpRepairTool = kpRepairTool;
window.ctipConfig = ctipConfig;
window.ctipIvrMap = ctipIvrMap;
window.smsConfig = smsConfig;
window.callSmsConfig = callSmsConfig;
window.formHandlingConfig = formHandlingConfig;
window.emailConfig = emailConfig;
});

window.AdminUI = { showToast };

document.body.addEventListener("htmx:responseError", (event) => {
  const detail = event.detail;
  const message = detail.xhr?.response || "Błąd odpowiedzi serwera";
  showToast(typeof message === "string" ? message : "Błąd odpowiedzi serwera", "error");
});

document.body.addEventListener("htmx:sendError", () => {
  showToast("Nie udało się wysłać żądania do serwera", "error");
});

window.addEventListener("unhandledrejection", (event) => {
  if (!event?.reason) {
    return;
  }
  const message = event.reason instanceof Error ? event.reason.message : String(event.reason);
  showToast(message || "Nierozpoznany błąd", "error");
});

window.addEventListener("error", (event) => {
  const message = event?.message?.trim();
  if (!message) {
    return;
  }
  // Przeglądarka zwraca ogólny "Script error." dla błędów bez stacktrace (np. cross-origin).
  // Nie pokazujemy takiego komunikatu jako toast, bo zaciemnia diagnostykę.
  if (message === "Script error." || message === "Script error") {
    return;
  }
  showToast(message, "error");
});
