(() => {
  "use strict";
  const api = "/admin/shipping/toner-yields";
  const colors = { black: "Czarny", cyan: "Cyjan", magenta: "Magenta", yellow: "Żółty", unknown: "Nieustalony" };
  const statuses = { confirmed: "Potwierdzona", estimated: "Szacunkowa", missing: "Brak danych" };
  const scopes = { active: "Aktywne umowy", inactive: "Poza aktywnymi umowami", review: "Wymaga sprawdzenia" };
  const filterIds = ["scope", "color", "brand", "supplier", "kind", "status"];
  const state = { page: 1, total: 0, canEdit: false, detail: null, dirty: false, saving: false, request: 0, detailRequest: 0 };
  const element = (id) => document.getElementById(`toner-${id}`);
  const escaped = (value) => escapeShippingHtml(value);
  const number = (value) => value == null ? "—" : Number(value).toLocaleString("pl-PL");
  const date = (value) => value ? new Date(value).toLocaleString("pl-PL") : "brak odczytu";
  const models = (item) => item.models.map((model) => `${model.brand} ${model.model}`).join(" · ");

  function feedback(message, error = false, edit = false) {
    const target = element(edit ? "edit-feedback" : "feedback");
    target.textContent = message;
    target.hidden = !message;
    target.classList.toggle("error", error);
  }

  function fillFacet(field, values, emptyLabel) {
    const select = element(field);
    const previous = select.value;
    select.replaceChildren(new Option(emptyLabel, ""), ...values.map((value) => new Option(value, value)));
    select.value = previous;
  }

  function parameters() {
    const params = new URLSearchParams({ query: element("query").value.trim(), page: state.page, page_size: 50, only_available: element("available").checked });
    for (const field of filterIds) params.set(field, element(field).value);
    return params;
  }

  async function load(reset = false) {
    if (!element("rows")) return;
    if (reset) state.page = 1;
    const request = ++state.request;
    element("rows").setAttribute("aria-busy", "true");
    try {
      const data = await shippingJson(`${api}?${parameters()}`);
      if (request !== state.request) return;
      const lastPage = Math.max(1, Math.ceil(data.total / data.page_size));
      if (state.page > lastPage) { state.page = lastPage; await load(); return; }
      state.total = data.total;
      state.canEdit = data.can_edit;
      element("refresh-ms").disabled = !state.canEdit;
      fillFacet("brand", data.facets.brand, "Wszystkie marki");
      fillFacet("supplier", data.facets.supplier, "Wszyscy dostawcy");
      for (const field of ["total", "confirmed", "estimated", "missing"]) element(field).textContent = number(data.summary[field]);
      element("coverage").textContent = `${number(data.summary.filled_percent)}% uzupełnionych · ${number(data.summary.confirmed_percent)}% potwierdzonych`;
      element("review-count").textContent = `Konflikty zgodności w wyniku: ${number(data.summary.review)}`;
      element("sync-time").textContent = `Ostatni odczyt MS: ${date(data.sync?.synced_at)}`;
      const rows = data.items.map((item) => `<tr class="${state.detail?.item_id === item.item_id ? "toner-selected" : ""}">
        <td><button class="toner-open" type="button" data-toner-id="${item.item_id}">${escaped(item.name)}</button><small>#${item.item_id} · ${escaped(item.item_index)}${item.sku ? ` · ${escaped(item.sku)}` : ""}</small><small>${escaped(models(item) || "Model do ustalenia")}</small></td>
        <td><span class="toner-color-dot ${escaped(item.color)}"></span>${escaped(colors[item.color] || item.color)}</td>
        <td class="toner-numeric">${number(item.stock)}</td><td class="toner-numeric"><strong>${number(item.pages)}</strong></td>
        <td><span class="toner-badge ${escaped(item.status)}">${escaped(statuses[item.status])}</span><small>${escaped(item.source || "Nie wskazano źródła")}</small>${item.scope === "review" ? "<small>⚠ Sprawdź zgodność modelu</small>" : ""}</td></tr>`);
      element("rows").innerHTML = rows.join("") || '<tr><td colspan="5">Brak pasujących tonerów. Zmień filtry lub odśwież katalog.</td></tr>';
      const pages = Math.max(1, Math.ceil(data.total / data.page_size));
      element("page").textContent = `Strona ${state.page} z ${pages} · ${number(data.total)} kartotek`;
      element("prev").disabled = state.page <= 1;
      element("next").disabled = state.page >= pages;
    } catch (error) {
      if (request === state.request) feedback(error.message, true);
    } finally {
      if (request === state.request) element("rows").removeAttribute("aria-busy");
    }
  }

  function validateStatus() {
    const status = element("edit-status").value;
    element("edit-pages").required = status !== "missing";
    element("edit-pages").disabled = status === "missing";
    element("edit-source").required = status !== "missing";
    element("edit-basis").required = status === "estimated";
  }

  function confirmDiscard() {
    return !state.saving && (!state.dirty || window.confirm("Porzucić niezapisane zmiany wydajności?"));
  }

  async function open(itemId, force = false) {
    if (!force && !confirmDiscard()) return;
    const request = ++state.detailRequest;
    try {
      const data = await shippingJson(`${api}/${itemId}`);
      if (request !== state.detailRequest) return;
      state.detail = data;
      state.dirty = false;
      element("detail").hidden = false;
      element("detail-name").textContent = data.name;
      element("detail-meta").textContent = `Kartoteka #${data.item_id} · ${data.item_index} · ${colors[data.color]} · stan ${number(data.stock)} · wersja ${data.revision}`;
      element("detail-models").textContent = models(data) || "Brak potwierdzonych modeli.";
      element("detail-scope").textContent = `${scopes[data.scope]}: ${data.scope_note}`;
      element("edit-pages").value = data.pages ?? "";
      element("edit-status").value = data.status;
      element("edit-source").value = data.source;
      element("edit-basis").value = data.basis;
      element("edit-reason").value = "";
      element("edit-fields").disabled = !data.can_edit;
      element("save").hidden = !data.can_edit;
      element("readonly").hidden = data.can_edit;
      element("reload-detail").hidden = true;
      feedback("", false, true);
      validateStatus();
      element("evidence").replaceChildren();
      for (const evidence of data.evidence) {
        const card = document.createElement("article");
        card.className = "toner-history-entry";
        const title = document.createElement("strong");
        title.textContent = evidence.source || "Szacunek na podstawie zgodnych wariantów";
        card.append(title);
        const description = document.createElement("p");
        description.textContent = [
          evidence.yield_pages || evidence.pages ? `${number(evidence.yield_pages || evidence.pages)} stron / wkład` : "",
          evidence.sku || evidence.code || "",
          evidence.ean ? `EAN: ${evidence.ean}` : "",
          evidence.invoice ? `Faktura: ${evidence.invoice}` : "",
          evidence.page ? `Strona dokumentu: ${evidence.page}` : "",
        ].filter(Boolean).join(" · ");
        card.append(description);
        if (evidence.source_url) {
          try {
            const url = new URL(evidence.source_url);
            if (["https:", "http:"].includes(url.protocol)) {
              const link = document.createElement("a");
              link.href = url.href;
              link.target = "_blank";
              link.rel = "noopener noreferrer";
              link.textContent = "Otwórz dokument źródłowy ↗";
              card.append(link);
            }
          } catch {}
        }
        const details = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "Pełne dane źródłowe";
        const text = document.createElement("pre");
        text.textContent = JSON.stringify(evidence, null, 2);
        details.append(summary, text);
        card.append(details);
        element("evidence").append(card);
      }
      if (!data.evidence.length) element("evidence").textContent = "Brak zaimportowanych dowodów. Ręcznie wskazane źródła znajdują się w historii zmian.";
      element("history").innerHTML = data.history.map((entry) => `<article class="toner-history-entry"><strong>${escaped(date(entry.created_at))}</strong><p>${escaped(entry.actor)} · wersja ${entry.revision}</p><p>${number(entry.before.pages)} → ${number(entry.after.pages)} stron · ${escaped(statuses[entry.after.status])}</p><p>${escaped(entry.after.reason)}</p><small>${escaped(entry.after.source)}${entry.after.basis ? ` · ${escaped(entry.after.basis)}` : ""}</small></article>`).join("") || "Brak korekt.";
      element("detail").scrollIntoView({ behavior: "smooth", block: "nearest" });
    } catch (error) {
      if (request === state.detailRequest) feedback(error.message, true);
    }
  }

  async function save(event) {
    event.preventDefault();
    if (!state.detail?.can_edit || state.saving) return;
    const payload = { revision: state.detail.revision, status: element("edit-status").value, source: element("edit-source").value.trim(), basis: element("edit-basis").value.trim(), reason: element("edit-reason").value.trim() };
    payload.pages = payload.status === "missing" ? null : Number(element("edit-pages").value);
    if (payload.reason.length < 3 || (payload.status !== "missing" && (!Number.isInteger(payload.pages) || payload.pages <= 0 || !payload.source)) || (payload.status === "estimated" && !payload.basis)) {
      feedback("Podaj poprawną liczbę stron, źródło i uzasadnienie. Szacunek wymaga podstawy oszacowania.", true, true);
      return;
    }
    state.saving = true;
    element("save").disabled = true;
    element("edit-fields").disabled = true;
    const itemId = state.detail.item_id;
    try {
      await shippingJson(`${api}/${itemId}`, { method: "PATCH", body: JSON.stringify(payload) });
      state.dirty = false;
      await open(itemId, true);
      feedback("Zapisano wydajność i historię korekty.", false, true);
      await load();
    } catch (error) {
      feedback(error.message, true, true);
      element("reload-detail").hidden = false;
    } finally {
      state.saving = false;
      element("save").disabled = false;
      element("edit-fields").disabled = !state.detail?.can_edit;
    }
  }

  async function refresh() {
    element("refresh-ms").disabled = true;
    feedback("Pobieranie kartotek i aktywnych umów MS… Wydajności ręczne pozostają bez zmian.");
    try {
      await shippingJson(`${api}/refresh`, { method: "POST" });
      await load(true);
      feedback("Odświeżono dane MS. Zachowano wydajności i historię korekt.");
    } catch (error) {
      feedback(error.message, true);
    } finally {
      element("refresh-ms").disabled = !state.canEdit;
    }
  }

  window.tonerYieldDashboard = { load };
  document.addEventListener("DOMContentLoaded", () => {
    if (!element("rows")) return;
    let timer;
    element("query").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => load(true), 300); });
    element("filters").addEventListener("submit", (event) => { event.preventDefault(); clearTimeout(timer); load(true); });
    for (const field of [...filterIds, "available"]) element(field).addEventListener("change", () => load(true));
    element("reset").addEventListener("click", () => { element("filters").reset(); load(true); });
    element("prev").addEventListener("click", () => { if (state.page > 1) { state.page--; load(); } });
    element("next").addEventListener("click", () => { if (state.page * 50 < state.total) { state.page++; load(); } });
    element("rows").addEventListener("click", (event) => { const button = event.target.closest("[data-toner-id]"); if (button) open(Number(button.dataset.tonerId)); });
    element("detail-close").addEventListener("click", () => { if (confirmDiscard()) { ++state.detailRequest; element("detail").hidden = true; state.detail = null; state.dirty = false; } });
    element("edit-form").addEventListener("input", () => { state.dirty = true; });
    element("edit-status").addEventListener("change", () => { state.dirty = true; validateStatus(); });
    element("edit-form").addEventListener("submit", save);
    element("reload-detail").addEventListener("click", () => open(state.detail.item_id));
    element("refresh-ms").addEventListener("click", refresh);
    window.addEventListener("beforeunload", (event) => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
  });
})();
