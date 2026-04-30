const MM_TOKEN_KEY = "admin-session-token";

function readMmToken() {
  return window.localStorage?.getItem(MM_TOKEN_KEY) || window.sessionStorage?.getItem(MM_TOKEN_KEY) || null;
}

function clearMmToken() {
  window.localStorage?.removeItem(MM_TOKEN_KEY);
  window.sessionStorage?.removeItem(MM_TOKEN_KEY);
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
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return value;
    }
    return parsed.toLocaleDateString("pl-PL");
  } catch (_err) {
    return value;
  }
}

function csvEscape(value) {
  const text = String(value ?? "");
  if (text.includes(";") || text.includes("\"") || text.includes("\n")) {
    return `"${text.replaceAll("\"", "\"\"")}"`;
  }
  return text;
}

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

async function initializeMmPage() {
  let token = readMmToken();
  const form = document.getElementById("mm-filter-form");
  const dateFromInput = document.getElementById("mm-date-from");
  const dateToInput = document.getElementById("mm-date-to");
  const destinationInput = document.getElementById("mm-destination");
  const modelInput = document.getElementById("mm-model");
  const searchInput = document.getElementById("mm-search");
  const refreshBtn = document.getElementById("mm-refresh");
  const resetBtn = document.getElementById("mm-reset");
  const exportBtn = document.getElementById("mm-export");
  const logoutBtn = document.getElementById("mm-logout");
  const docsCount = document.getElementById("mm-docs-count");
  const itemsCount = document.getElementById("mm-items-count");
  const quantitySum = document.getElementById("mm-quantity-sum");
  const rangeLabel = document.getElementById("mm-range-label");
  const destinationSummary = document.getElementById("mm-destination-summary");
  const itemsBody = document.getElementById("mm-items-body");
  const userChip = document.getElementById("mm-user-chip");
  const errorBox = document.getElementById("mm-error");
  const infoBox = document.getElementById("mm-info");
  const truncatedBox = document.getElementById("mm-truncated");
  const periodButtons = Array.from(document.querySelectorAll(".mm-period-btn"));

  if (
    !form ||
    !dateFromInput ||
    !dateToInput ||
    !destinationInput ||
    !modelInput ||
    !searchInput ||
    !refreshBtn ||
    !resetBtn ||
    !exportBtn ||
    !logoutBtn ||
    !docsCount ||
    !itemsCount ||
    !quantitySum ||
    !rangeLabel ||
    !destinationSummary ||
    !itemsBody ||
    !userChip ||
    !errorBox ||
    !infoBox ||
    !truncatedBox
  ) {
    return;
  }

  if (!dateToInput.value) {
    dateToInput.value = todayIso();
  }

  const headers = () => {
    if (!token) {
      return {};
    }
    return { "X-Admin-Session": token };
  };

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

  const writeQueryToLocation = () => {
    const params = new URLSearchParams();
    params.set("date_from", dateFromInput.value || "2023-06-01");
    params.set("date_to", dateToInput.value || todayIso());
    if (destinationInput.value && destinationInput.value !== "all") {
      params.set("destination", destinationInput.value);
    }
    if (modelInput.value.trim()) {
      params.set("model", modelInput.value.trim());
    }
    if (searchInput.value.trim()) {
      params.set("search", searchInput.value.trim());
    }
    const query = params.toString();
    const path = query ? `/mm?${query}` : "/mm";
    window.history.replaceState(null, "", path);
  };

  const readQueryFromLocation = () => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("date_from")) {
      dateFromInput.value = params.get("date_from");
    }
    if (params.get("date_to")) {
      dateToInput.value = params.get("date_to");
    }
    if (params.get("destination")) {
      destinationInput.value = params.get("destination");
    }
    if (params.get("model")) {
      modelInput.value = params.get("model");
    }
    if (params.get("search")) {
      searchInput.value = params.get("search");
    }
  };

  const ensureCurrentUser = async () => {
    const response = await fetch("/auth/me", { headers: headers() });
    if (response.status === 401) {
      throw new Error("Sesja wygasla.");
    }
    if (!response.ok) {
      let detail = "Nie udalo sie pobrac danych sesji.";
      try {
        const payload = await response.json();
        detail = payload?.detail || detail;
      } catch (_err) {
        // ignore
      }
      throw new Error(detail);
    }
    const user = await response.json();
    const label = [user?.name, user?.email].filter(Boolean).join(" • ");
    userChip.textContent = label || "Uzytkownik";
  };

  const renderDestinationSummary = (summary) => {
    const docsByDest = summary?.documents_by_destination || {};
    const itemsByDest = summary?.items_by_destination || {};
    const qtyByDest = summary?.quantity_by_destination || {};
    const keys = Array.from(
      new Set([...Object.keys(docsByDest), ...Object.keys(itemsByDest), ...Object.keys(qtyByDest)]),
    );
    if (keys.length === 0) {
      destinationSummary.innerHTML = "<p>Brak danych dla wybranych filtrow.</p>";
      return;
    }
    destinationSummary.innerHTML = keys
      .sort((a, b) => a.localeCompare(b, "pl"))
      .map((key) => {
        const docs = docsByDest[key] ?? 0;
        const items = itemsByDest[key] ?? 0;
        const qty = qtyByDest[key] ?? 0;
        return `
          <div class="mm-destination-row">
            <strong>${escapeHtml(key)}</strong>
            <span>Dok: ${escapeHtml(docs)} | Poz: ${escapeHtml(items)} | Ilosc: ${escapeHtml(qty)}</span>
          </div>
        `;
      })
      .join("");
  };

  const renderItems = (items) => {
    if (!Array.isArray(items) || items.length === 0) {
      itemsBody.innerHTML = "<tr><td colspan='12'>Brak pozycji dla wybranych filtrow.</td></tr>";
      return;
    }
    itemsBody.innerHTML = items
      .map((item) => {
        const kindClass =
          item.destination_kind === "zlom"
            ? "mm-row-kind-zlom"
            : item.destination_kind === "wynajem"
              ? "mm-row-kind-wynajem"
              : "";
        return `
          <tr class="${escapeHtml(kindClass)}">
            <td>${escapeHtml(formatDate(item.data_wyst))}</td>
            <td>${escapeHtml(item.numer_mm)}</td>
            <td>${escapeHtml(item.magazyn_przyjmujacy)}</td>
            <td>${escapeHtml(item.magazyn_wydajacy)}</td>
            <td>${escapeHtml(item.model_label)}</td>
            <td>${escapeHtml(item.indeks)}</td>
            <td>${escapeHtml(item.nazwa_pozycji)}</td>
            <td>${escapeHtml(item.ilosc)}</td>
            <td>${escapeHtml(item.jm)}</td>
            <td>${escapeHtml(item.cena_zakupu_netto ?? 0)}</td>
            <td>${escapeHtml(item.serial || "—")}</td>
            <td>${escapeHtml(item.ewidencja || "—")}</td>
          </tr>
        `;
      })
      .join("");
  };

  let lastItems = [];
  let lastSummary = null;

  const exportCurrentCsv = () => {
    if (!Array.isArray(lastItems) || lastItems.length === 0) {
      setInfo("Brak danych do eksportu CSV.");
      return;
    }
    const columns = [
      "data_wyst",
      "numer_mm",
      "magazyn_przyjmujacy",
      "magazyn_wydajacy",
      "model_label",
      "indeks",
      "nazwa_pozycji",
      "ilosc",
      "jm",
      "cena_zakupu_netto",
      "serial",
      "ewidencja",
      "id_zakupy_table",
      "id_zakpozycja_table",
    ];
    const headerLine = columns.join(";");
    const lines = lastItems.map((item) => columns.map((key) => csvEscape(item[key])).join(";"));
    const csvText = [headerLine, ...lines].join("\n");
    const blob = new Blob([csvText], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `mm_${dateFromInput.value || "from"}_${dateToInput.value || "to"}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    const docs = lastSummary?.documents_count ?? "0";
    setInfo(`Wyeksportowano CSV (${lastItems.length} pozycji, dokumentow: ${docs}).`);
  };

  const loadDashboard = async () => {
    setError("");
    setInfo("");
    setBusy(true);
    try {
      const params = new URLSearchParams();
      params.set("date_from", dateFromInput.value || "2023-06-01");
      params.set("date_to", dateToInput.value || todayIso());
      params.set("destination", destinationInput.value || "all");
      if (modelInput.value.trim()) {
        params.set("model", modelInput.value.trim());
      }
      if (searchInput.value.trim()) {
        params.set("search", searchInput.value.trim());
      }
      params.set("limit", "10000");

      const response = await fetch(`/admin/mm/dashboard?${params.toString()}`, {
        headers: headers(),
      });
      if (response.status === 401) {
        throw new Error("Sesja wygasla.");
      }
      if (!response.ok) {
        let detail = "Nie udalo sie pobrac raportu MM.";
        try {
          const payload = await response.json();
          detail = payload?.detail || detail;
        } catch (_err) {
          // ignore
        }
        throw new Error(detail);
      }
      const data = await response.json();
      const summary = data?.summary || {};
      docsCount.textContent = String(summary.documents_count ?? 0);
      itemsCount.textContent = String(summary.items_count ?? 0);
      quantitySum.textContent = String(summary.quantity_sum ?? 0);
      rangeLabel.textContent = `${escapeHtml(data.date_from || "")} - ${escapeHtml(data.date_to || "")}`;
      truncatedBox.hidden = !summary.truncated;

      renderDestinationSummary(summary);
      renderItems(data.items || []);
      lastItems = Array.isArray(data.items) ? data.items : [];
      lastSummary = summary;
      writeQueryToLocation();
      setInfo(`Wczytano raport MM: dokumenty ${summary.documents_count ?? 0}, pozycje ${summary.items_count ?? 0}.`);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Nieznany blad.";
      setError(message);
      if (message.toLowerCase().includes("sesja")) {
        clearMmToken();
      }
    } finally {
      setBusy(false);
    }
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    loadDashboard();
  });

  refreshBtn.addEventListener("click", () => {
    loadDashboard();
  });

  resetBtn.addEventListener("click", () => {
    dateFromInput.value = "2023-06-01";
    dateToInput.value = todayIso();
    destinationInput.value = "all";
    modelInput.value = "";
    searchInput.value = "";
    loadDashboard();
  });

  exportBtn.addEventListener("click", () => {
    exportCurrentCsv();
  });

  for (const button of periodButtons) {
    button.addEventListener("click", () => {
      dateFromInput.value = button.dataset.from || dateFromInput.value;
      dateToInput.value = button.dataset.to || dateToInput.value;
      loadDashboard();
    });
  }

  logoutBtn.addEventListener("click", async () => {
    try {
      await fetch("/auth/logout", { method: "POST", headers: headers() });
    } catch (_err) {
      // ignore
    } finally {
      clearMmToken();
      window.location.href = "/";
    }
  });

  readQueryFromLocation();
  try {
    await ensureCurrentUser();
    await loadDashboard();
  } catch (err) {
    const message = err instanceof Error ? err.message : "Nieznany blad autoryzacji.";
    setError(message);
    clearMmToken();
  }
}

window.addEventListener("DOMContentLoaded", () => {
  initializeMmPage();
});
