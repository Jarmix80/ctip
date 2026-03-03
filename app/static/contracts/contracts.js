const CONTRACTS_TOKEN_KEY = "admin-session-token";

function readContractsToken() {
  return (
    window.localStorage?.getItem(CONTRACTS_TOKEN_KEY) ||
    window.sessionStorage?.getItem(CONTRACTS_TOKEN_KEY) ||
    null
  );
}

function clearContractsToken() {
  window.localStorage?.removeItem(CONTRACTS_TOKEN_KEY);
  window.sessionStorage?.removeItem(CONTRACTS_TOKEN_KEY);
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

async function initializeContractsPage() {
  const token = readContractsToken();
  if (!token) {
    window.location.replace("/");
    return;
  }

  const formsTotal = document.getElementById("contracts-forms-total");
  const devicesTotal = document.getElementById("contracts-devices-total");
  const devicesMatched = document.getElementById("contracts-devices-matched");
  const formsBody = document.getElementById("contracts-forms-body");
  const devicesBody = document.getElementById("contracts-devices-body");
  const refreshBtn = document.getElementById("contracts-refresh");
  const logoutBtn = document.getElementById("contracts-logout");
  const formsEmpty = document.getElementById("contracts-forms-empty");
  const errorBox = document.getElementById("contracts-error");
  const infoBox = document.getElementById("contracts-info");

  if (
    !formsTotal ||
    !devicesTotal ||
    !devicesMatched ||
    !formsBody ||
    !devicesBody ||
    !refreshBtn ||
    !logoutBtn ||
    !formsEmpty ||
    !errorBox ||
    !infoBox
  ) {
    return;
  }

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

  const headers = () => ({
    "X-Admin-Session": token,
  });

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
        const statusClass = firebird.found ? "ok" : "warn";
        const statusLabel = firebird.found ? "Znaleziony" : "Brak klienta";
        const actionLabel = item.contract_action === "utworz_klienta" ? "Utworz klienta" : "Podlacz klienta";
        const actionCode = item.contract_action === "utworz_klienta" ? "utworz_klienta" : "podlacz_klienta";
        return `
          <tr>
            <td>${escapeHtml(item.id)}</td>
            <td>${escapeHtml(item.customer_name)}</td>
            <td>${escapeHtml(item.customer_nip)}</td>
            <td>${escapeHtml(item.customer_email)}</td>
            <td>${escapeHtml(item.customer_phone)}</td>
            <td>
              <span class="contracts-badge ${statusClass}">${statusLabel}</span>
              <div>ID: ${escapeHtml(firebird.id_klient || "—")}</div>
            </td>
            <td>
              <button
                type="button"
                class="contracts-action-btn"
                data-entity="form"
                data-action="${escapeHtml(actionCode)}"
                data-target-id="${escapeHtml(item.id)}"
              >
                ${escapeHtml(actionLabel)}
              </button>
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
        const statusLabel = item.found_in_firebird ? "Potwierdzone" : "Brak w Firebird";
        return `
          <tr>
            <td>${escapeHtml(item.row)}</td>
            <td>${escapeHtml(item.serial)}</td>
            <td>${escapeHtml(item.ewidencja)}</td>
            <td>${escapeHtml(item.model)}</td>
            <td><span class="contracts-badge ${statusClass}">${statusLabel}</span></td>
            <td>${escapeHtml(item.id_maszyna || "—")}</td>
            <td>${escapeHtml(item.id_klient || "—")}</td>
            <td>
              <button
                type="button"
                class="contracts-action-btn"
                data-entity="device"
                data-action="${escapeHtml(item.sync_action || "do_weryfikacji")}"
                data-row="${escapeHtml(item.row || 0)}"
              >
                ${escapeHtml(item.sync_action || "do_weryfikacji")}
              </button>
            </td>
          </tr>
        `;
      })
      .join("");
  };

  const runAction = async (button) => {
    if (!button) {
      return;
    }
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
    } catch (err) {
      setError(err instanceof Error ? err.message : "Błąd akcji.");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  };

  const loadData = async () => {
    setError("");
    setInfo("");
    setBusy(true);
    try {
      const meResponse = await fetch("/auth/me", { headers: headers() });
      if (!meResponse.ok) {
        throw new Error("Sesja wygasla.");
      }
      const me = await meResponse.json();
      const sections = new Set(Array.isArray(me.sections) ? me.sections : []);
      if (!sections.has("generator")) {
        throw new Error("Brak uprawnien do sekcji Obsluga umow.");
      }

      const response = await fetch("/admin/contracts/dashboard", { headers: headers() });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "Nie udalo sie pobrac danych dashboardu.");
      }

      formsTotal.textContent = String(data.forms_total || 0);
      devicesTotal.textContent = String(data.devices_total || 0);
      devicesMatched.textContent = String(data.devices_matched || 0);
      renderForms(data.forms || []);
      renderDevices(data.devices || []);
      setInfo(`Dane odswiezone: ${formatDate(new Date().toISOString())}`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Blad ladowania danych.";
      setError(message);
      if (message.includes("Sesja")) {
        clearContractsToken();
        window.location.replace("/");
      }
    } finally {
      setBusy(false);
    }
  };

  refreshBtn.addEventListener("click", () => {
    loadData();
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) {
      return;
    }
    const actionButton = target.closest(".contracts-action-btn");
    if (!(actionButton instanceof HTMLButtonElement)) {
      return;
    }
    runAction(actionButton);
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
      clearContractsToken();
      window.location.replace("/");
    }
  });

  loadData();
}

document.addEventListener("DOMContentLoaded", () => {
  initializeContractsPage();
});
