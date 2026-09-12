(() => {
  "use strict";

  const api = "/admin/shipping/orbit";
  const tabs = ["overview", "timeline", "toners", "billing", "service", "contracts", "contractlife", "story"];
  const statuses = { confirmed: "Potwierdzone", estimated: "Szacowane", partial: "Częściowe", missing: "Brak danych", unavailable: "Niedostępne", not_acquired: "Niepozyskane", not_applicable: "Nie dotyczy", conflict: "Konflikt", stale: "Nieaktualne", error: "Błąd źródła", active: "Aktywne", inactive: "Nieaktywne", open: "Otwarty okres", closed: "Zamknięte", pending: "Oczekuje", unknown: "Nieustalone" };
  Object.assign(statuses, { suspended: "Zawieszone", scrapped: "Wycofane", review: "Do weryfikacji", known: "Znane ze źródła", measured: "Zmierzony poziom", reset: "Reset licznika", excluded: "Wyłączone z obliczenia", running: "W trakcie", success: "Odczyt zakończony", failed: "Błąd odczytu", unverified: "Niepotwierdzone" });
  const labels = {
    total: "Ogółem", bw: "Czarno-białe", black: "Czarno-białe", color: "Kolor", scans: "Skany", scan: "Skany", counters: "Liczniki", summary: "Podsumowanie", current: "Ostatni odczyt", start: "Początek", end: "Koniec", delta: "Przyrost", print_delta: "Przyrost wydruków", printed_pages: "Wydrukowane strony", billed_pages: "Strony rozliczone", entry: "Odczyt na wejściu", exit: "Odczyt na wyjściu",
    id: "Identyfikator", number: "Numer", type: "Typ", kind: "Rodzaj", name: "Nazwa", title: "Opis", description: "Opis", text: "Treść", customer: "Klient", serial: "Numer seryjny", model: "Model", brand: "Producent", location: "Lokalizacja", ip: "Adres IP", inventory_number: "Numer ewidencyjny", device_id: "Urządzenie", contract_id: "Umowa", contract: "Umowa", contracts: "Umowy", contract_life: "Przebieg umowy", activity: "Eksploatacja", months: "Miesiące", items: "Pozycje",
    date_from: "Od", date_to: "Do", start_date: "Początek umowy", end_date: "Koniec umowy", assigned_from: "Urządzenie w umowie od", assigned_to: "Urządzenie w umowie do", period: "Okres", interval: "Zakres", month: "Miesiąc", year: "Rok", days_remaining: "Dni do końca", progress_percent: "Postęp czasu [%]", amendments: "Aneksy", extensions: "Przedłużenia", termination: "Wypowiedzenie",
    source: "Źródło", source_ref: "Odwołanie do źródła", observed_at: "Data obserwacji", updated_at: "Aktualizacja", imported_at: "Data importu", time_precision: "Precyzja czasu", timezone: "Strefa źródła", precision: "Precyzja", status: "Stan danych", coverage: "Pokrycie danych", method: "Metoda", basis: "Podstawa", note: "Uwagi", reason: "Przyczyna", missing: "Braki", gaps: "Luki", last_seen_at: "Ostatni odczyt", first_seen_at: "Pierwszy odczyt", resolution: "Rozdzielczość", sources: "Źródła", metrics: "Wskaźniki",
    toners: "Tonery", parts: "Części", service: "Serwis", services: "Serwis", ordered: "Zamówione", issued: "Wydane", shipped: "Wysłane", delivered: "Dostarczone", replaced: "Potwierdzone wymiany", installed: "Zamontowane", requests: "Zgłoszenia", visits: "Wizyty", work_hours: "Czas pracy [h]", downtime: "Przestój", quantity: "Ilość", unit: "Jednostka", sku: "SKU", level_percent: "Pozostały poziom [%]", remaining_percent: "Pozostały poziom [%]", estimated_percent: "Szacowana pozostała wydajność [%]", yield_pages: "Wydajność nominalna [stron / wkład]", history: "Historia", cycles: "Cykle", readings: "Odczyty", levels: "Poziomy", replacement_at: "Data wymiany", shipped_at: "Data wysyłki", delivered_at: "Data dostawy", calculation_version: "Wersja obliczenia", counter_basis: "Baza licznika", assumptions: "Założenia",
    finance: "Finanse", revenue: "Przychód", invoiced: "Zafakturowano", paid: "Zapłacono", invoice: "Faktura", invoices: "Faktury", corrections: "Korekty", known_costs: "Koszty znane", costs: "Koszty znane według kategorii", margin: "Marża", currency: "Waluta", amount: "Kwota", net: "Netto", gross: "Brutto", tax_basis: "Netto / brutto", fixed_fees: "Opłaty stałe", transport: "Transport", labor: "Praca", travel: "Dojazdy", financing: "Finansowanie", subcontractors: "Podwykonawcy", allocation_method: "Metoda przypisania kwoty", value: "Wartość", data: "Dane źródłowe",
  };
  Object.assign(labels, {
    mono: "Czarno-białe", mono_a3: "Czarno-białe A3", color_a3: "Kolor A3", scan_input: "Skanowanie wejściowe", metric: "Rodzaj licznika", points: "Punkty pomiarowe", epoch: "Epoka licznika", events: "Zdarzenia [szt.]", count: "Liczba rekordów", contracts: "Umowy [szt.]", contract_number: "Numer umowy", customer_id: "Identyfikator klienta", first_at: "Pierwsze dane", last_at: "Ostatnie dane", synced_at: "Synchronizacja", finished_at: "Koniec odczytu źródła", started_at: "Początek odczytu źródła",
    contract_source: "Źródło umowy", starts_at: "Początek umowy", ends_at: "Koniec umowy", net_value: "Kwota netto", documented_issues: "Potwierdzone wartości wydań — przed uzgodnieniem zwrotów", issue_purchase_cost: "Wartość wydania według ceny zakupu — przed uzgodnieniem zwrotów", unit_price: "Cena zakupu za jednostkę", currency_status: "Potwierdzenie waluty", currency_scope: "Zakres identyfikacji waluty", returns_status: "Uzgodnienie zwrotów", scope: "Zakres wartości",
    purchase_costs: "Znane koszty zakupu", invoice_revenue: "Przychód z faktur netto", missing_costs: "Pozycje bez znanego kosztu [szt.]", margin_available: "Dostępność marży", cost: "Koszt znany", net_amount: "Kwota netto", allocation_status: "Potwierdzenie przypisania kwoty", allocation_basis: "Podstawa przypisania kwoty", basis_kind: "Typ podstawy", remaining_pages: "Pozostała wydajność [stron]", consumed_pages: "Przyrost od podstawy [stron]", yield_status: "Stan wydajności nominalnej", yield_source: "Źródło wydajności nominalnej", baseline_at: "Data podstawy cyklu", stock_quantity: "Potencjalny zapas wysyłek [szt.]", period_start: "Początek okresu", period_end: "Koniec okresu", document_number: "Numer dokumentu", document_kind: "Rodzaj dokumentu", document_id: "Identyfikator dokumentu", order_id: "Numer zlecenia", order_year: "Rok zlecenia", tracking_number: "Numer przesyłki", issued_quantity: "Wydano [szt.]", returned_quantity: "Zwrócono [szt.]", item_name: "Nazwa materiału", item_id: "Kartoteka materiału", is_contract: "Zdarzenie umowne", assignment_basis: "Podstawa powiązania", counter_reset: "Reset licznika", replacement_confirmed: "Potwierdzona wymiana", billing: "Rozliczenie stron", invoice_linked: "Powiązana faktura", ms_machine_id: "Identyfikator maszyny MS", toner_levels: "Poziomy tonerów", black: "Czarny", cyan: "Cyjan", magenta: "Magenta", yellow: "Żółty",
  });
  const financialKey = /(^|_)(finance|financial|billing|costs?|prices?|amounts?|revenue|margin|profit|invoices?|payments?|paid|invoiced|net|gross|currency|rates?|fees?|vat|tax|money|documented_issues)(_|$)/i;
  const colors = { K: "Czarny", C: "Cyjan", M: "Magenta", Y: "Żółty" };
  const colorCodes = { black: "K", cyan: "C", magenta: "M", yellow: "Y", k: "K", c: "C", m: "M", y: "Y" };
  const timelineTabs = ["timeline", "service", "toners", "billing", "contractlife"];
  const state = { enabled: false, capabilityFinance: false, listFinance: null, data: null, detailRevision: 0, device: "", tab: "overview", contract: "", from: "", to: "", bucket: "month", counterSeries: "", contractOptions: new Map(), listPage: 1, timelinePage: 1, list: null, timeline: null, active: false, initialized: null, controllers: {}, order: null, evidenceTrigger: null };
  const element = (id) => document.getElementById(`orbit-${id}`);
  const escaped = (value) => escapeShippingHtml(value == null ? "" : String(value));
  const object = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const rows = (value) => Array.isArray(value) ? value : Array.isArray(value?.items) ? value.items : [];
  const present = (value) => value !== null && value !== undefined && value !== "";
  const canFinance = () => state.capabilityFinance && state.listFinance !== false && state.data?.can_view_finance === true;
  const numeric = (value) => present(value) && ["number", "string"].includes(typeof value) && String(value).trim() !== "" && Number.isFinite(Number(value)) ? Number(value) : null;
  const number = (value) => numeric(value) === null ? "Brak danych" : Number(value).toLocaleString("pl-PL", { maximumFractionDigits: 4 });
  const label = (key) => labels[key] || key;
  const empty = (message = "Brak danych w wybranym zakresie.") => `<p class="orbit-empty">${escaped(message)}</p>`;
  const badge = (status) => `<span class="orbit-badge" data-status="${escaped(status || "missing")}">${escaped(statuses[status] || status || "Brak danych")}</span>`;

  function validDate(value) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return "";
    const parsed = new Date(`${value}T00:00:00Z`);
    return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value ? value : "";
  }

  function date(value, precision) {
    if (!present(value)) return "Brak daty";
    const text = String(value);
    if (precision === "month" || /^\d{4}-\d{2}$/.test(text)) return text.slice(0, 7);
    if (["period", "interval", "week", "year"].includes(precision)) return `${text} (okres źródłowy)`;
    if (["day", "date"].includes(precision) || /^\d{4}-\d{2}-\d{2}$/.test(text)) return text.slice(0, 10);
    if (!/(Z|[+-]\d{2}:\d{2})$/.test(text)) return `${text} (strefa nieustalona)`;
    const parsed = new Date(text);
    return Number.isFinite(parsed.getTime()) ? `${parsed.toLocaleString("pl-PL", { timeZone: "Europe/Warsaw" })} (Europe/Warsaw; źródło: ${text})` : text;
  }

  function source(value) {
    if (!present(value)) return "Źródło niepodane";
    if (Array.isArray(value)) return value.map(source).join(" · ") || "Źródło niepodane";
    if (object(value)) return [value.label || value.name || value.system || value.kind, value.ref || value.id].filter(present).join(" · ") || "Źródło niepodane";
    return String(value);
  }

  function provenance(value) {
    const data = object(value) ? value : {};
    const observed = data.time_precision === "month" ? eventDate({ ...data, data: { period: data.period || data.data?.period } }) : date(data.observed_at || data.updated_at, data.time_precision || data.precision);
    return `<small class="orbit-meta">${escaped(source(data.source || data.source_ref))} · ${escaped(observed)}${data.time_precision ? ` · precyzja: ${escaped(({ instant: "chwila", datetime: "chwila", timestamp: "chwila", day: "dzień", date: "dzień", week: "tydzień", month: "miesiąc", period: "okres", unknown: "nieustalona" })[data.time_precision] || data.time_precision)}` : ""}${data.imported_at ? `<br>Import: ${escaped(date(data.imported_at))}` : ""}</small>`;
  }

  function visibleData(value) {
    if (Array.isArray(value)) return value.map(visibleData).filter((item) => item !== undefined);
    if (!object(value)) return value;
    if (!canFinance() && value.requires_finance === true) return undefined;
    return Object.fromEntries(Object.entries(value).filter(([key]) => !["raw", "payload", "raw_payload"].includes(key) && (canFinance() || key === "billing" || !financialKey.test(key))).map(([key, item]) => [key, visibleData(item)]).filter(([, item]) => item !== undefined));
  }

  function evidenceLinks(value) {
    if (!object(value)) return "";
    const ids = [...new Set([value.event_id, ...(Array.isArray(value.evidence_ids) ? value.evidence_ids : [])].filter((item) => ["string", "number"].includes(typeof item) && present(item)))];
    return ids.map((id, index) => `<button type="button" class="orbit-source-button" data-orbit-evidence="${escaped(id)}">${ids.length > 1 ? `Źródło ${index + 1}` : "Pokaż źródło"}</button>`).join("");
  }

  function scalar(value) {
    if (!present(value)) return "Brak danych";
    if (typeof value === "boolean") return value ? "Tak" : "Nie";
    return typeof value === "number" ? number(value) : String(value);
  }

  function metric(value) {
    const data = object(value) ? value : { value };
    const shown = `${data.status === "estimated" ? "≈ " : ""}${scalar(data.value)}${present(data.value) && data.unit ? ` ${data.unit}` : ""}${present(data.value) && data.currency ? ` ${data.currency}` : ""}${data.tax_basis ? ` · ${data.tax_basis}` : ""}`;
    return `<strong>${escaped(shown)}</strong>${data.status ? badge(data.status) : ""}${provenance(data)}${data.method ? `<small class="orbit-meta">Metoda: ${escaped(data.method)}</small>` : ""}${data.basis ? `<small class="orbit-meta">Podstawa: ${escaped(scalar(data.basis))}</small>` : ""}${evidenceLinks(data)}`;
  }

  function renderValue(value, depth = 0) {
    if (!present(value)) return empty();
    if (!object(value) && !Array.isArray(value)) return escaped(scalar(value));
    if (depth > 6) return empty("Szczegóły dostępne w zdarzeniu źródłowym.");
    if (object(value) && Object.hasOwn(value, "value")) return metric(value);
    if (Array.isArray(value)) {
      if (!value.length) return empty();
      if (value.every(object)) {
        const columns = [...new Set(value.flatMap((item) => Object.keys(item)))].filter((key) => !["evidence_ids", "event_id", "requires_finance"].includes(key));
        return `<div class="orbit-table-scroll" tabindex="0" aria-label="Dane szczegółowe"><table><thead><tr>${columns.map((key) => `<th scope="col">${escaped(label(key))}</th>`).join("")}<th scope="col">Dowody</th></tr></thead><tbody>${value.map((item) => `<tr>${columns.map((key) => `<td>${key === "observed_at" && item.time_precision === "month" ? escaped(eventDate({ ...item, data: { period: item.period } })) : fieldValue(key, item[key], depth + 1)}</td>`).join("")}<td>${evidenceLinks(item) || "Brak powiązanych dowodów"}</td></tr>`).join("")}</tbody></table></div>`;
      }
      return `<ul>${value.map((item) => `<li>${renderValue(item, depth + 1)}</li>`).join("")}</ul>`;
    }
    const entries = Object.entries(value).filter(([key]) => !["event_id", "evidence_ids", "requires_finance", "can_view_finance"].includes(key));
    return (entries.length ? `<dl class="orbit-fields">${entries.map(([key, item]) => `<div><dt>${escaped(label(key))}</dt><dd>${fieldValue(key, item, depth + 1)}</dd></div>`).join("")}</dl>` : empty()) + evidenceLinks(value);
  }

  function fieldValue(key, value, depth) {
    if (key === "status") return badge(value);
    if (key === "currency") return escaped(present(value) ? value : "waluta niepotwierdzona");
    if (["currency_status", "returns_status"].includes(key)) return badge(value || "unverified");
    if (key === "scope" && value === "before_returns") return "Przed uzgodnieniem zwrotów";
    if (["source", "source_ref"].includes(key)) return escaped(source(value));
    if (key === "basis") return escaped(({ measured: "Pomiar poziomu", confirmedreplacement: "Potwierdzona wymiana", shipmentbaseline: "Pierwsza wysyłka — wymiana niepotwierdzona" })[value] || scalar(value));
    if (key === "metric") return escaped(label(value));
    if ((key.endsWith("_at") || key.endsWith("_date")) && !object(value)) return escaped(date(value));
    return renderValue(value, depth);
  }

  function section(title, value, note = "") {
    return `<section class="orbit-card"><h3>${escaped(title)}</h3>${note ? `<p class="orbit-muted">${escaped(note)}</p>` : ""}${renderValue(visibleData(value))}</section>`;
  }

  function reportParameters() {
    const params = new URLSearchParams();
    if (state.contract) params.set("contract_id", state.contract);
    if (state.from) params.set("date_from", state.from);
    if (state.to) params.set("date_to", state.to);
    return params;
  }

  function writeUrl(replace = false) {
    const params = new URLSearchParams({ view: "orbit" });
    for (const key of ["device", "tab", "contract", "from", "to"]) if (state[key]) params.set(key, state[key]);
    const target = `${window.location.pathname}?${params}${window.location.hash}`;
    if (`${window.location.pathname}${window.location.search}${window.location.hash}` !== target) window.history[replace ? "replaceState" : "pushState"](null, "", target);
  }

  function readUrl() {
    const params = new URLSearchParams(window.location.search);
    state.device = params.get("device") || "";
    const aliases = { toner: "toners", finance: "billing", narrative: "story", "contract-life": "contractlife" };
    const tab = aliases[params.get("tab")] || params.get("tab");
    state.tab = tabs.includes(tab) ? tab : "overview";
    state.contract = params.get("contract") || "";
    state.from = validDate(params.get("from"));
    state.to = validDate(params.get("to"));
    state.timelinePage = 1;
    if ((params.get("from") && !state.from) || (params.get("to") && !state.to)) feedback("Niepoprawny format daty w odnośniku. Użyj RRRR-MM-DD.");
  }

  function feedback(message) {
    element("feedback").textContent = message;
    element("feedback").hidden = !message;
  }

  function cancel(channel) {
    state.controllers[channel]?.abort();
  }

  async function request(channel, url) {
    cancel(channel);
    const controller = new AbortController();
    state.controllers[channel] = controller;
    const timer = window.setTimeout(() => controller.abort("timeout"), 20000);
    try {
      const response = await fetch(url, { method: "GET", headers: shippingHeaders(), signal: controller.signal });
      if (!response.ok) throw new Error(response.status === 403 ? "Brak uprawnień do tych danych." : response.status === 404 ? "Raport lub dane źródłowe nie są dostępne." : response.status === 401 ? "Sesja wygasła. Zaloguj się ponownie." : `Nie udało się pobrać danych (HTTP ${response.status}).`);
      const data = await response.json();
      if (controller.signal.aborted) throw new DOMException("Anulowano odczyt", "AbortError");
      return data;
    } catch (error) {
      if (controller.signal.reason === "timeout") throw new Error("Przekroczono czas odczytu. Ponów pobranie danych.");
      if (controller.signal.aborted) throw new DOMException("Anulowano odczyt", "AbortError");
      if (error instanceof TypeError) throw new Error("Nie można połączyć się z raportem. Sprawdź połączenie i ponów odczyt.");
      if (error instanceof SyntaxError) throw new Error("Raport zwrócił niepoprawną odpowiedź. Ponów odczyt.");
      throw error;
    } finally {
      window.clearTimeout(timer);
    }
  }

  function updateOrderLink(order = state.order) {
    state.order = order;
    const link = document.getElementById("shipping-orbit-order-link");
    if (!link) return;
    const machineId = order?.machine_id;
    const valid = ["number", "string"].includes(typeof machineId) && /^[1-9]\d*$/.test(String(machineId));
    const ambiguous = order?.machine_count > 1 || order?.machine_ids?.length > 1 || order?.device_match_status === "ambiguous";
    link.hidden = !state.enabled || !valid || ambiguous;
    if (link.hidden) link.removeAttribute("href");
    else link.setAttribute("href", `${window.location.pathname}?${new URLSearchParams({ view: "orbit", device: `ms:${machineId}` })}`);
  }

  function disable(message = "KP ORBIT jest obecnie niedostępny.") {
    state.enabled = false;
    state.capabilityFinance = false;
    state.listFinance = false;
    state.data = null;
    state.list = null;
    state.timeline = null;
    for (const channel of ["list", "detail", "contracts", "timeline", "evidence"]) cancel(channel);
    document.querySelectorAll('[data-shipping-view="orbit"]').forEach((button) => { button.hidden = true; });
    element("detail").hidden = true;
    element("pane").replaceChildren();
    element("devices").replaceChildren();
    closeEvidence();
    updateOrderLink();
    if (state.active) {
      switchShippingView("dispatch", false);
      shippingAlert(message, true);
    }
  }

  async function capabilities() {
    try {
      const data = await request("capabilities", `${api}/capabilities`);
      if (data.enabled !== true) { disable("KP ORBIT jest wyłączony."); return false; }
      state.enabled = true;
      state.capabilityFinance = data.can_view_finance === true;
      state.capabilityPolicy = data.can_manage_policy === true;
      document.querySelectorAll('[data-shipping-view="orbit"]').forEach((button) => { button.hidden = false; });
      updateOrderLink();
      return true;
    } catch (error) {
      if (error.name !== "AbortError") disable(error.message);
      return false;
    }
  }

  function initialize() {
    if (!state.initialized) state.initialized = capabilities();
    return state.initialized;
  }

  function pagination(prefix, data, page) {
    const total = numeric(data?.total);
    const size = numeric(data?.page_size);
    const pages = total !== null && size > 0 ? Math.max(1, Math.ceil(total / size)) : null;
    element(`${prefix}-page`).textContent = pages === null ? `Strona ${page} · liczba wyników niepodana` : `Strona ${page} z ${pages} · ${number(total)} wyników`;
    element(`${prefix}-prev`).disabled = page <= 1;
    element(`${prefix}-next`).disabled = pages === null || page >= pages;
  }

  function renderDevices() {
    element("devices").innerHTML = rows(state.list).map((item) => `<li><button type="button" data-orbit-device="${escaped(item.id)}" aria-current="${String(item.id) === state.device}"><strong>${escaped(scalar(item.model))}</strong><span>S/N: ${escaped(scalar(item.serial))}</span><small>${escaped(object(item.customer) ? item.customer.name || "Klient niepodany" : scalar(item.customer))}</small>${badge(item.status)}<small>Aktualizacja: ${escaped(date(item.updated_at))}</small></button></li>`).join("");
    pagination("devices", state.list, state.listPage);
  }

  async function loadDevices(reset = false) {
    if (!state.enabled) return;
    if (reset) state.listPage = 1;
    element("search-feedback").textContent = "Wczytywanie urządzeń…";
    element("devices").replaceChildren();
    element("devices").setAttribute("aria-busy", "true");
    element("devices-prev").disabled = true;
    element("devices-next").disabled = true;
    try {
      const params = new URLSearchParams({ query: element("query").value.trim(), scope: element("scope").value, page: state.listPage, page_size: 20 });
      const data = await request("list", `${api}?${params}`);
      if (data.enabled !== true) { disable(); return; }
      state.listFinance = data.can_view_finance === true;
      state.list = data;
      const last = Math.max(1, Math.ceil((numeric(data.total) || 0) / (numeric(data.page_size) || 20)));
      if (state.listPage > last) { state.listPage = last; await loadDevices(); return; }
      state.listPage = numeric(data.page) || state.listPage;
      renderDevices();
      element("search-feedback").textContent = rows(data).length ? "Wybierz urządzenie, aby otworzyć raport." : "Brak pasujących urządzeń.";
      if (state.data) renderReport();
    } catch (error) {
      if (error.name !== "AbortError") {
        state.listFinance = false;
        element("search-feedback").textContent = error.message;
        if (state.data) renderReport();
      }
    } finally {
      if (!state.controllers.list?.signal.aborted) element("devices").removeAttribute("aria-busy");
    }
  }

  function contracts() {
    return (state.contractOptions.get(state.device) || rows(state.data?.contracts)).filter((item) => present(item.id));
  }

  function contractName(item) {
    return item.contract_number || item.number || item.id;
  }

  function contractDates(item) {
    return { start: item?.start || item?.starts_at || item?.start_date, end: item?.end || item?.ends_at || item?.end_date };
  }

  async function loadContractOptions(deviceId) {
    try {
      const data = await request("contracts", `${api}/${encodeURIComponent(deviceId)}`);
      if (state.device !== deviceId) return;
      state.contractOptions.set(deviceId, rows(data.contracts));
      if (state.data) renderReport();
    } catch (error) {
      if (error.name !== "AbortError") feedback("Nie udało się pobrać pełnej listy umów. Widoczne są metadane bieżącego zakresu.");
    }
  }

  function identityText(value) {
    return object(value) ? scalar(value.name || value.label || value.model) : scalar(value);
  }

  function renderIdentity() {
    const device = state.data.device || {};
    element("identity").innerHTML = `<div class="orbit-photo">Brak zdjęcia</div><div><p class="orbit-eyebrow">Producent: ${escaped(scalar(device.brand))}</p><h3>${escaped(identityText(device.model))}</h3><p>S/N: <strong>${escaped(scalar(device.serial))}</strong> · Ewidencja: ${escaped(scalar(device.inventory_number))}</p><p>Klient: ${escaped(identityText(device.customer))} · Lokalizacja: ${escaped(identityText(device.location))}</p><p>IP: ${escaped(scalar(device.ip))}</p>${badge(device.status)}<small class="orbit-meta">Aktualizacja raportu: ${escaped(date(device.updated_at))}<br>Synchronizacja: ${escaped(date(device.synced_at))}<br>Daty i źródła pomiarów podano przy poszczególnych wartościach.</small></div>`;
    if (typeof device.image_url === "string" && /^\/static\/[a-zA-Z0-9_./%-]+$/.test(device.image_url)) {
      const image = document.createElement("img");
      image.alt = identityText(device.model);
      image.loading = "lazy";
      image.addEventListener("error", () => { element("identity").querySelector(".orbit-photo").textContent = "Brak zdjęcia"; });
      image.src = device.image_url;
      element("identity").querySelector(".orbit-photo").replaceChildren(image);
    }
  }

  function renderFilters() {
    const options = contracts();
    const select = element("contract");
    select.replaceChildren(new Option("Cała historia urządzenia", ""), ...options.map((item) => new Option(`${contractName(item)} · ${item.type || "typ niepodany"}`, String(item.id))));
    if (state.contract && !options.some((item) => String(item.id) === state.contract)) select.add(new Option("Wybrana umowa — brak metadanych", state.contract));
    select.value = state.contract;
    element("contract-field").hidden = options.length <= 1;
    element("contract-label").innerHTML = options.length === 1 ? `Umowa: ${escaped(contractName(options[0]))} <button type="button" data-orbit-contract="${escaped(state.contract ? "" : options[0].id)}">${state.contract ? "Cała historia urządzenia" : "Wybierz umowę"}</button>` : !options.length ? `Brak metadanych umowy${state.contract ? ' <button type="button" data-orbit-contract="">Cała historia urządzenia</button>' : ""}` : "";
    element("from").value = state.from;
    element("to").value = state.to;
    element("bucket").value = state.bucket;
    element("range-label").textContent = `${state.contract ? `Wybrana umowa: ${state.contract}` : "Cała historia urządzenia"} · ${state.from || "od początku dostępnych danych"} — ${state.to || "do ostatnich dostępnych danych"}`;
    document.querySelectorAll("[data-orbit-shift], [data-orbit-zoom]").forEach((button) => { button.disabled = !state.from || !state.to; });
  }

  function renderCounters() {
    const counters = state.data.counters || {};
    if (Array.isArray(counters) || Array.isArray(counters.items)) {
      const series = rows(counters);
      const cards = series.map((entry) => {
        const points = rows(entry.points);
        const latest = points.at(-1);
        return `<article class="orbit-metric"><span>${escaped(label(entry.metric))} · ${escaped(source(entry.source))}</span>${metric(latest ? { ...latest, source: entry.source, unit: "stron" } : null)}<p class="orbit-muted">Stan serii: ${badge(entry.status)}</p>${latest?.reason ? `<p class="orbit-muted">${escaped(latest.reason)}</p>` : ""}<details><summary>Ostatnie ${Math.min(50, points.length)} z ${points.length} odczytów, przyrosty i epoki</summary>${renderValue(points.slice(-50))}</details></article>`;
      }).join("");
      return `<section class="orbit-card"><h3>Ostatnie dostępne stany liczników</h3><p class="orbit-muted">Serie i źródła są rozdzielone. Stany liczników, przyrosty i rozliczenia CPC nie są sumowane ze sobą.</p>${cards ? `<div class="orbit-grid">${cards}</div>` : empty("Brak odczytów liczników w wybranym zakresie.")}</section>`;
    }
    const latest = counters.current || counters.latest || counters;
    const fields = [["total", "Wydruki ogółem"], ["bw", "Czarno-białe"], ["color", "Kolor"], ["scans", "Skany"]];
    return `<section class="orbit-card"><h3>Ostatnie dostępne stany liczników</h3><p class="orbit-muted">Wartości kumulacyjne; nie są przyrostem w wybranym okresie.</p><div class="orbit-grid">${fields.map(([key, title]) => `<article class="orbit-metric"><span>${title}</span>${metric(latest[key] ?? (key === "scans" ? latest.scan : undefined))}</article>`).join("")}</div><details><summary>Zakres pomiarów, przyrosty i źródła</summary>${renderValue(visibleData(counters))}</details></section>`;
  }

  function renderToners() {
    const data = state.data.toners;
    const levels = Array.isArray(data) ? data : rows(data?.levels || data);
    const mono = state.data.device?.is_color === false || state.data.device?.color_mode === "mono";
    const rings = levels.map((item) => {
      const code = colorCodes[String(item.color || item.channel || "").toLowerCase()];
      if (!code || (mono && code !== "K")) return "";
      const estimated = item.status === "estimated" || (!present(item.level_percent) && present(item.estimated_percent));
      const raw = item.level_percent ?? item.remaining_percent ?? item.estimated_percent;
      const level = numeric(object(raw) ? raw.value : raw);
      const usable = level !== null && level >= 0 && level <= 100 && ["measured", "estimated", "confirmed", "known", "stale"].includes(item.status);
      return `<article class="orbit-metric"><span>${code} · ${colors[code]}</span><div class="orbit-ring" data-color="${code}"${usable ? ` style="--orbit-level: ${level}%"` : ""}><strong>${usable ? `${estimated ? "≈ " : ""}${number(level)}%` : "—"}</strong></div>${badge(estimated ? "estimated" : item.status)}${usable ? "" : '<p class="orbit-muted">Brak miarodajnego poziomu</p>'}${provenance(item)}${evidenceLinks(item)}<details><summary>Podstawa i ograniczenia</summary>${renderValue(visibleData(item))}</details></article>`;
    }).join("");
    return `<section class="orbit-card"><h3>Życie tonerów</h3><p class="orbit-muted">${escaped(state.data.toner_scope || "Odczyt lub jawny szacunek ze źródła. Wysyłka, dostawa i potwierdzona wymiana to osobne etapy.")}</p>${rings ? `<div class="orbit-toners">${rings}</div>` : empty("Brak danych o poziomie i obsługiwanych kolorach.")}</section>`;
  }

  function renderContracts() {
    const items = contracts();
    return `<section class="orbit-card"><h3>Umowy i terminy</h3><p class="orbit-muted">Okres obsługi CPC i finansowanie mają odrębne daty i źródła.</p>${items.length ? items.map((item) => `<article class="orbit-card"><h3>${escaped(contractName(item))} · ${escaped(item.type || "Typ niepodany")}</h3><p>Początek: ${escaped(date(contractDates(item).start, "day"))}<br>Koniec: ${present(contractDates(item).end) ? escaped(date(contractDates(item).end, "day")) : "Brak terminu"}</p>${contractProgress(item)}${provenance({ ...item, source: item.source || item.contract_source })}${renderValue(visibleData(item))}<button type="button" data-orbit-contract="${escaped(item.id)}" data-orbit-contract-life>Przebieg tej umowy</button></article>`).join("") : empty("Brak metadanych umów urządzenia.")}</section>`;
  }

  function contractProgress(item) {
    const start = validDate(String(contractDates(item).start || "").slice(0, 10));
    const end = validDate(String(contractDates(item).end || "").slice(0, 10));
    if (!end) return '<p class="orbit-muted">Brak terminu — postęp czasu niedostępny.</p>';
    const today = new Date().toLocaleDateString("en-CA", { timeZone: "Europe/Warsaw" });
    const remaining = Math.round((Date.parse(end) - Date.parse(today)) / 86400000);
    const description = remaining >= 0 ? `Do wskazanego końca: ${number(remaining)} dni` : `Wskazany termin upłynął ${number(-remaining)} dni temu`;
    const percentage = start && start < end ? Math.max(0, Math.min(100, (Date.parse(today) - Date.parse(start)) / (Date.parse(end) - Date.parse(start)) * 100)) : null;
    return `<p class="orbit-muted">${description} · stan na ${escaped(today)}</p>${percentage === null ? '<p class="orbit-muted">Brak poprawnego początku — postęp czasu niedostępny.</p>' : `<progress class="orbit-progress" value="${percentage}" max="100" aria-label="Postęp czasu wybranej umowy">${number(percentage)}%</progress>`}`;
  }

  function renderContractLife() {
    if (!state.contract) return section("Przebieg umowy", null, "Wybierz umowę, aby zobaczyć bilans tego urządzenia w jej okresie.") + renderContracts();
    const selected = contracts().find((item) => String(item.id) === state.contract);
    const summary = state.data.summary?.contract_life || state.data.summary;
    return section("Urządzenie w wybranej umowie", selected, "Bilans zdarzeń i rozliczeń dotyczy wybranego urządzenia, umowy i zakresu dat. Miesięczne CPC zachowuje pełny okres źródłowy.") + contractProgress(selected) + section("Dostępny bilans zakresu", summary, "Liczba zdarzeń nie jest liczbą dostarczonych tonerów ani wykonanych wizyt. Szczegóły miesięcy i materiałów znajdują się na osi poniżej.") + renderCounters() + (canFinance() ? renderFinance("Finanse wybranego urządzenia i umowy") : "") + renderToners();
  }

  function renderFinance(title = "Rozliczenia") {
    if (!canFinance()) return "";
    const data = state.data.finance;
    if (!data) return section(title, null);
    const amountGroups = [["purchase_costs", "Znane koszty zakupu materiałów i części", "Podstawa netto/brutto: niepodana w podsumowaniu; sprawdź dokument źródłowy."], ["invoice_revenue", "Zafakturowany przychód netto", "Kwota netto przypisana przez źródło; nie potwierdza zapłaty."]];
    return `<section class="orbit-card"><h3>${escaped(title)}</h3>${badge(data.status)}<p class="orbit-muted">${escaped(data.note || "Kompletność kosztów niepotwierdzona.")}</p><div class="orbit-grid">${amountGroups.map(([key, heading, note]) => `<article class="orbit-metric"><span>${heading}</span>${object(data[key]) && Object.keys(data[key]).length ? Object.entries(data[key]).map(([currency, amount]) => `<strong>${escaped(scalar(amount))} ${escaped(currency)}</strong>`).join("") : empty("Brak udokumentowanych kwot.")}<small class="orbit-meta">${note}</small></article>`).join("")}<article class="orbit-metric"><span>Pozycje bez znanego kosztu</span><strong>${escaped(number(data.missing_costs))} szt.</strong><small class="orbit-meta">Nie obejmuje wszystkich braków kosztów pracy, dojazdów i innych kategorii.</small></article><article class="orbit-metric"><span>Marża</span><strong>Niedostępna</strong><small class="orbit-meta">Brak potwierdzonego pełnego kosztu i definicji marży.</small></article></div><p class="orbit-muted">Waluty pozostają oddzielne. Źródła i daty kwot są dostępne w zdarzeniach rozliczeniowych poniżej.</p></section>${renderDocumentedIssues(data.documented_issues)}`;
  }

  function renderDocumentedIssues(value) {
    if (!canFinance() || !Array.isArray(value)) return "";
    const items = value.filter(object);
    return `<section class="orbit-card" data-orbit-documented-issues><h3>Potwierdzone wartości wydań — przed uzgodnieniem zwrotów</h3><p class="orbit-muted">Wartości dokumentów według cen zakupu. Nie stanowią kosztu netto po zwrotach i nie są doliczane do podsumowania kosztów.</p>${items.length ? `<div class="orbit-table-scroll" tabindex="0" aria-label="Potwierdzone wartości wydań"><table><thead><tr><th scope="col">Dokument / źródło / data</th><th scope="col">Ilość</th><th scope="col">Cena zakupu za jednostkę</th><th scope="col">Wartość wydania</th><th scope="col">Waluta</th><th scope="col">Zakres / zwroty / podstawa</th><th scope="col">Dowód</th></tr></thead><tbody>${items.map((item) => `<tr><td>${escaped(scalar(item.document_id))}${provenance(item)}${badge(item.status)}</td><td>${escaped(scalar(item.quantity))}</td><td>${escaped(scalar(item.unit_price))}</td><td>${escaped(scalar(item.amount))}</td><td>${fieldValue("currency", item.currency)}<small>${badge(item.currency_status || "unverified")}</small><small>${escaped(item.currency_scope || "Zakres waluty niepodany")}</small></td><td>${item.scope === "before_returns" ? "Przed uzgodnieniem zwrotów" : escaped(item.scope || "Zakres niepodany")}<small>Zwroty: ${badge(item.returns_status || "unverified")}</small><small>Podstawa: ${escaped(scalar(item.basis))}</small></td><td>${evidenceLinks(item) || "Brak powiązanego dowodu"}</td></tr>`).join("")}</tbody></table></div>` : empty("Brak potwierdzonych wartości wydań w tym zakresie.")}</section>`;
  }

  function renderAdvice() {
    const forecast = rows(state.data.forecast);
    const projection = `<section class="orbit-card"><h3>Zużycie rozliczone i prognoza CPC</h3><p>Prognoza stron, nie bieżący licznik ani procent tonera. Podstawa: 3–6 kolejnych miesięcy jednej umowy.</p><div class="orbit-table-scroll"><table><thead><tr><th>Metryka</th><th>Okresy</th><th>Stron / dzień ≈</th><th>Od końca okresu ≈</th><th>Następne 30 dni ≈</th><th>Podstawa</th></tr></thead><tbody>${forecast.map((item) => `<tr><td>${escaped(label(item.metric))}</td><td>${number(item.periods)}</td><td>${number(item.daily_pages)}</td><td>${number(item.since_period_pages)}</td><td>${number(item.next_30_days_pages)}</td><td>${escaped(item.period_end || "Brak okresu")}<small>${escaped(item.reason)}</small></td></tr>`).join("")}</tbody></table></div></section>`;
    const advice = `<section class="orbit-card"><h3>Ocena kolejnej dostawy po jednej sztuce</h3><p>Wskazówki bez blokowania. Potencjalny zapas nie jest potwierdzonym stanem magazynu klienta.</p>${rows(state.data.advice?.items).map((item) => `<p><strong>${escaped(colors[colorCodes[item.color]] || item.color)}</strong> · ${escaped(({warning: "Ostrzeżenie", ok: "Brak ostrzeżeń", insufficient_data: "Niewystarczające dane"})[item.status] || item.status)}<br>${escaped((item.reasons || []).join(" "))}<small>Dopuszczony zapas: ${number(item.policy?.spare_toners)} · Próg: ${number(item.policy?.low_percent)}% · Dane: ${escaped(item.observed_at || "brak")}</small></p>`).join("")}</section>`;
    if (!state.capabilityPolicy) return projection + advice;
    const scope = state.policyScope || "device";
    const color = state.policyColor || "all";
    const scopeId = scope === "global" ? "0" : scope === "customer" ? String(state.data.device.customer_id || "") : state.device;
    const saved = rows(state.data.policy?.overrides).find((item) => item.scope === scope && item.scope_id === scopeId && item.color === color)?.values || {};
    const fields = [["spare_toners", "Zapasowe tonery", 0, 20], ["low_percent", "Próg niskiego poziomu [%]", 0, 100], ["lead_days", "Wyprzedzenie [dni]", 1, 90], ["cpc_stale_days", "Ważność CPC [dni]", 1, 365], ["measurement_stale_days", "Ważność pomiaru [dni]", 1, 90]];
    return projection + advice + `<details class="orbit-card"><summary>Ustawienia ostrzeżeń — administrator</summary><p>Kolejność: globalne → klient → urządzenie. Puste pola dziedziczą; wyczyszczenie wszystkich usuwa działanie wyjątku. Każdy zapis jest audytowany.</p><form id="orbit-policy-form"><label>Zakres<select name="scope" data-orbit-policy-scope>${[["device", "Urządzenie"], ["customer", "Klient"], ["global", "Wszystkie urządzenia"]].filter(([key]) => key !== "customer" || state.data.device.customer_id).map(([key, title]) => `<option value="${key}" ${scope === key ? "selected" : ""}>${title}</option>`).join("")}</select></label><label>Kolor<select name="color" data-orbit-policy-color>${[["all", "Wszystkie"], ["black", "Czarny"], ["cyan", "Cyjan"], ["magenta", "Magenta"], ["yellow", "Żółty"]].map(([key, title]) => `<option value="${key}" ${color === key ? "selected" : ""}>${title}</option>`).join("")}</select></label>${fields.map(([key, title, minimum, maximum]) => `<label>${title}<input name="${key}" type="number" min="${minimum}" max="${maximum}" step="1" value="${escaped(saved[key] ?? "")}" placeholder="Dziedzicz"></label>`).join("")}<button type="submit">Zapisz zasady</button></form></details>`;
  }

  async function savePolicy(event) {
    if (event.target.id !== "orbit-policy-form") return;
    event.preventDefault();
    const form = new FormData(event.target);
    const scope = form.get("scope");
    const scopeId = scope === "global" ? "0" : scope === "customer" ? String(state.data.device.customer_id) : state.device;
    const values = {};
    for (const key of ["spare_toners", "low_percent", "lead_days", "cpc_stale_days", "measurement_stale_days"]) {
      if (form.get(key) !== "") values[key] = Number(form.get(key));
    }
    try {
      await shippingJson(`${api}/policies`, {method: "PUT", body: JSON.stringify({scope, scope_id: scopeId, color: form.get("color"), values})});
      await loadDetail();
      feedback("Zasady zapisane. Zmiana znajduje się w audycie administratora.");
    } catch (error) { feedback(error.message); }
  }

  function renderReport() {
    if (!state.data || !state.enabled) return;
    if (!canFinance()) {
      closeEvidence();
      if (state.tab === "billing") { state.tab = "overview"; writeUrl(true); }
    }
    element("tab-billing").hidden = !canFinance();
    renderIdentity();
    renderFilters();
    element("coverage").innerHTML = renderValue(visibleData(state.data.coverage));
    element("coverage").innerHTML += `<h4>Odczyty źródeł</h4><p class="orbit-muted">Stan integracji; nie potwierdza pokrycia wszystkich pól urządzenia.</p>${renderValue(visibleData(state.data.sources))}`;
    document.querySelectorAll("[data-orbit-tab]").forEach((button) => {
      const active = button.dataset.orbitTab === state.tab;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    element("pane").setAttribute("aria-labelledby", `orbit-tab-${state.tab}`);
    const views = {
      overview: () => renderCounters() + section("Przegląd urządzenia", state.data.summary) + renderToners(),
      timeline: () => "",
      toners: renderToners,
      billing: () => renderFinance(),
      service: () => section("Serwis i obsługa", state.data.summary?.service || state.data.summary?.services, "Zgłoszenie i wykonana wizyta to osobne zdarzenia. Historia poniżej pokazuje bieżącą stronę osi.") ,
      contracts: renderContracts,
      contractlife: renderContractLife,
      story: () => section("Opowieść urządzenia", state.data.narrative, "Obserwacje wynikające z zapisanych danych i zdarzeń.") + '<button type="button" data-orbit-goto="timeline">Sprawdź zdarzenia i źródła na osi życia</button>',
    };
    element("pane").innerHTML = views[state.tab]();
    if (["overview", "toners", "billing", "contractlife"].includes(state.tab)) {
      element("pane").innerHTML += renderAdvice();
    }
    element("timeline").hidden = !timelineTabs.includes(state.tab) || (state.tab === "contractlife" && !state.contract);
    if (state.timeline && !element("timeline").hidden) renderTimeline();
  }

  async function loadDetail() {
    state.policyScope = state.policyScope || "device";
    const revision = ++state.detailRevision;
    cancel("detail");
    cancel("contracts");
    cancel("timeline");
    closeEvidence();
    state.data = null;
    state.timeline = null;
    element("detail").hidden = true;
    element("pane").replaceChildren();
    element("chart").replaceChildren();
    element("events").replaceChildren();
    element("buckets").replaceChildren();
    element("empty").hidden = false;
    if (!state.enabled || !state.device) { element("empty").textContent = "Wybierz urządzenie, aby otworzyć raport."; return; }
    if (state.from && state.to && state.from > state.to) {
      element("empty").textContent = "Niepoprawny zakres: data od jest późniejsza niż data do. Popraw daty w odnośniku lub wybierz urządzenie ponownie.";
      return;
    }
    element("empty").textContent = "Wczytywanie raportu urządzenia…";
    try {
      const data = await request("detail", `${api}/${encodeURIComponent(state.device)}?${reportParameters()}`);
      if (!object(data.device)) throw new Error("Odpowiedź nie zawiera danych urządzenia.");
      state.data = data;
      if (!state.contract && !state.from && !state.to) state.contractOptions.set(state.device, rows(data.contracts));
      else if (!state.contractOptions.has(state.device)) await loadContractOptions(state.device);
      if (!state.data || revision !== state.detailRevision || state.controllers.detail?.signal.aborted) return;
      if (state.tab === "contractlife" && !state.contract && contracts().length === 1) {
        state.contract = String(contracts()[0].id);
        writeUrl(true);
        await loadDetail();
        return;
      }
      element("empty").hidden = true;
      element("detail").hidden = false;
      renderReport();
      if (timelineTabs.includes(state.tab) && !element("timeline").hidden) await loadTimeline();
    } catch (error) {
      if (error.name !== "AbortError" && revision === state.detailRevision) element("empty").textContent = `${error.message} Użyj „Odśwież raport”, aby ponowić odczyt.`;
    }
  }

  function eventLane(item) {
    if (/replace|replacement|install|exchange/.test(item.kind)) return 2;
    if (/ship|delivery|dispatch|toner_issue/.test(item.kind)) return 1;
    if (/service|jam|repair|maintenance|fault/.test(item.kind)) return 3;
    return 4;
  }

  function eventPages(item) {
    if (/cpc|billing|invoice/.test(item.kind)) return null;
    const series = rows(state.data?.counters).find((entry) => `${entry.metric}:${entry.source}` === state.counterSeries);
    const point = rows(series?.points).find((entry) => String(entry.event_id) === String(item.id));
    if (!point || !["known", "confirmed"].includes(point.status)) return null;
    const value = point.delta;
    const pages = numeric(object(value) ? value.value : value);
    return pages !== null && pages >= 0 && !["conflict", "missing", "error"].includes(item.status) ? pages : null;
  }

  function renderChart(items) {
    const points = items.filter((item) => !["month", "unknown", "period"].includes(item.time_precision)).map((item) => {
      const raw = String(item.observed_at || "");
      const knownInstant = /(Z|[+-]\d{2}:\d{2})$/.test(raw);
      const day = validDate(raw.slice(0, 10));
      return { item, timestamp: knownInstant ? Date.parse(raw) : day ? Date.parse(`${day}T00:00:00Z`) : NaN };
    }).filter((point) => Number.isFinite(point.timestamp));
    if (!points.length) { element("chart").innerHTML = empty("Brak zdarzeń z datą umożliwiającą umieszczenie na osi."); return; }
    const minimum = state.from ? Date.parse(`${state.from}T00:00:00Z`) : Math.min(...points.map((point) => point.timestamp));
    const maximum = state.to ? Date.parse(`${state.to}T23:59:59Z`) : Math.max(...points.map((point) => point.timestamp));
    const span = Math.max(86400000, maximum - minimum);
    const maximumPages = Math.max(1, ...points.map((point) => eventPages(point.item) || 0));
    const laneNames = ["Przyrost stron", "Wysyłki / dostawy", "Wymiany", "Serwis / komunikaty", "Inne zdarzenia"];
    const marks = points.filter((point) => point.timestamp >= minimum && point.timestamp <= maximum).map(({ item, timestamp }) => {
      const position = 155 + (timestamp - minimum) / span * 610;
      const pages = eventPages(item);
      const attributes = `role="button" tabindex="0" data-orbit-evidence="${escaped(item.id)}" aria-label="${escaped(`${item.title || "Zdarzenie"} · ${date(item.observed_at, item.time_precision)}${pages !== null ? ` · ${number(pages)} stron` : ""}`)}"`;
      return pages !== null ? `<g ${attributes}><title>${escaped(item.title)} · ${number(pages)} stron</title><rect x="${position - 14}" y="25" width="28" height="72" fill="transparent"/><rect x="${position - 4}" y="${95 - pages / maximumPages * 65}" width="8" height="${Math.max(2, pages / maximumPages * 65)}"/></g>` : `<g ${attributes}><title>${escaped(item.title)}</title><circle cx="${position}" cy="${100 + eventLane(item) * 37}" r="15" fill="transparent"/><circle cx="${position}" cy="${100 + eventLane(item) * 37}" r="7"/></g>`;
    }).join("");
    element("chart").innerHTML = `<svg viewBox="0 0 800 310" role="group" aria-label="Oś dat: przyrosty wydruków i osobne warstwy zdarzeń. Szczegóły dostępne także w tabeli.">${laneNames.map((name, index) => `<text x="0" y="${index ? 104 + index * 37 : 45}">${name}</text><line x1="150" x2="780" y1="${index ? 100 + index * 37 : 95}" y2="${index ? 100 + index * 37 : 95}"/>`).join("")}${marks}<text x="150" y="298">${escaped(new Date(minimum).toISOString().slice(0, 10))}</text><text x="780" y="298" text-anchor="end">${escaped(new Date(maximum).toISOString().slice(0, 10))}</text></svg><p class="orbit-muted">${points.some((point) => eventPages(point.item) !== null) ? `Skala słupków na tej stronie: do ${number(maximumPages)} stron.` : "Brak porównywalnych przyrostów na tej stronie."} Słupki pokazują przyrost między odczytami jednej serii, nie dzienne zużycie. Okresy miesięczne i daty nieznane są osobno w zestawieniu okresów.</p>`;
  }

  function eventDate(item) {
    if (item.time_precision === "month") {
      const period = item.data?.period || item.data?.billing || {};
      return `${period.start || item.data?.period_start || "Początek niepodany"} — ${period.end || item.data?.period_end || "Koniec niepodany"} (okres miesięczny)`;
    }
    return date(item.observed_at, item.time_precision);
  }

  function renderBuckets() {
    const buckets = rows(state.timeline?.buckets);
    element("buckets").innerHTML = buckets.length ? `<p class="orbit-muted">Liczby zdarzeń z całego zakresu; nie są liczbą wydrukowanych stron. Agregaty nie są sumowane z odczytami.</p><table><thead><tr><th scope="col">Okres / dokładność</th><th scope="col">Zdarzenia [szt.]</th></tr></thead><tbody>${buckets.map((item) => `<tr><td>${escaped(item.period === "unknown" ? "Data nieznana" : String(item.period).startsWith("month:") ? `${String(item.period).slice(6)} · precyzja miesięczna źródła` : item.period)}</td><td>${escaped(number(item.events))}</td></tr>`).join("")}</tbody></table>` : empty("Brak agregatów okresów.");
  }

  function renderTimeline() {
    const all = rows(visibleData(state.timeline));
    const filters = { service: (item) => eventLane(item) === 3 || item.kind === "request", toners: (item) => /material_issue|shipment|supply_event|replace|toner/.test(item.kind), billing: (item) => /billing_period|invoice|correction/.test(item.kind) };
    const items = filters[state.tab] ? all.filter(filters[state.tab]) : all;
    element("timeline-scope").textContent = filters[state.tab] ? "Zdarzenia tematyczne z bieżącej strony całej osi. Kolejne strony mogą zawierać następne pozycje. Agregaty okresów dotyczą wszystkich zdarzeń." : "Zdarzenia z bieżącej strony. Daty zachowują precyzję źródła.";
    const select = element("counter-series");
    const series = rows(state.data?.counters).filter((entry) => ["total", "mono", "color"].includes(entry.metric));
    select.replaceChildren(...(series.length ? series.map((entry) => new Option(`${label(entry.metric)} · ${source(entry.source)}`, `${entry.metric}:${entry.source}`)) : [new Option("Brak porównywalnych odczytów", "")]));
    if (!series.some((entry) => `${entry.metric}:${entry.source}` === state.counterSeries)) state.counterSeries = select.value;
    select.value = state.counterSeries;
    renderChart(items);
    renderBuckets();
    element("events").innerHTML = items.length ? `<table><thead><tr><th scope="col">Data / precyzja</th><th scope="col">Zdarzenie</th><th scope="col">Źródło / status</th><th scope="col">Szczegóły</th></tr></thead><tbody>${items.map((item) => `<tr><td>${escaped(eventDate(item))}<small>${escaped(({ day: "dzień", date: "dzień", month: "miesiąc", week: "tydzień", period: "okres", instant: "chwila", datetime: "chwila", unknown: "nieznana" })[item.time_precision] || item.time_precision || "Precyzja niepodana")}</small></td><td>${escaped(item.title || "Zdarzenie bez opisu")}<small>${escaped(item.kind || "Typ niepodany")}</small>${["contractlife", "billing", "toners", "service"].includes(state.tab) ? `<details><summary>Pozycja źródłowa</summary>${renderValue(item.data)}${canFinance() && item.finance ? section("Kwoty tej pozycji", item.finance) : ""}</details>` : ""}</td><td>${escaped(source(item.source))}<br>${badge(item.status)}</td><td><button type="button" data-orbit-evidence="${escaped(item.id)}">Pokaż źródło</button></td></tr>`).join("")}</tbody></table>` : empty("Brak dostępnych zdarzeń na tej stronie dla wybranego widoku.");
    pagination("timeline", state.timeline, state.timelinePage);
  }

  async function loadTimeline() {
    if (!state.enabled || !state.device || !state.data) return;
    state.timeline = null;
    element("chart").replaceChildren();
    element("events").replaceChildren();
    element("buckets").replaceChildren();
    element("timeline-feedback").textContent = "Wczytywanie osi życia…";
    element("events").setAttribute("aria-busy", "true");
    element("timeline-prev").disabled = true;
    element("timeline-next").disabled = true;
    try {
      const params = reportParameters();
      params.set("bucket", state.bucket);
      params.set("page", state.timelinePage);
      params.set("page_size", "50");
      const data = await request("timeline", `${api}/${encodeURIComponent(state.device)}/timeline?${params}`);
      state.timeline = data;
      const last = Math.max(1, Math.ceil((numeric(data.total) || 0) / (numeric(data.page_size) || 50)));
      if (state.timelinePage > last) { state.timelinePage = last; await loadTimeline(); return; }
      state.timelinePage = numeric(data.page) || state.timelinePage;
      renderTimeline();
      element("timeline-feedback").textContent = "";
    } catch (error) {
      if (error.name !== "AbortError") element("timeline-feedback").textContent = error.message;
    } finally {
      if (!state.controllers.timeline?.signal.aborted) element("events").removeAttribute("aria-busy");
    }
  }

  function closeEvidence() {
    cancel("evidence");
    if (element("evidence")?.open) element("evidence").close();
    element("evidence-body")?.replaceChildren();
  }

  async function openEvidence(id, trigger) {
    if (!state.enabled || !state.device) return;
    state.evidenceTrigger = trigger;
    const dialog = element("evidence");
    element("evidence-body").textContent = "Wczytywanie danych źródłowych…";
    if (!dialog.open) dialog.showModal();
    try {
      const data = await request("evidence", `${api}/${encodeURIComponent(state.device)}/evidence/${encodeURIComponent(id)}`);
      const visible = visibleData(data);
      element("evidence-body").innerHTML = visible ? `<h4>${escaped(visible.title || "Zdarzenie bez opisu")}</h4>${badge(visible.status)}${provenance(visible)}<p>${escaped(eventDate(visible))}</p>${renderValue(visible.data)}${canFinance() && visible.finance ? section("Kwoty tej pozycji", visible.finance) : ""}` : empty("Brak uprawnień do tych danych.");
    } catch (error) {
      if (error.name !== "AbortError") element("evidence-body").textContent = error.message;
    }
  }

  function selectTab(tab) {
    if (!tabs.includes(tab) || (tab === "billing" && !canFinance())) return;
    state.tab = tab;
    writeUrl();
    if (tab === "contractlife" && !state.contract && contracts().length === 1) {
      state.contract = String(contracts()[0].id);
      writeUrl(true);
      loadDetail();
      return;
    }
    renderReport();
    if (timelineTabs.includes(tab) && !element("timeline").hidden && !state.timeline) loadTimeline();
  }

  async function open(updateUrl = true) {
    if (!state.enabled) return;
    state.active = true;
    feedback("");
    if (new URLSearchParams(window.location.search).get("view") === "orbit") readUrl();
    if (updateUrl) writeUrl();
    await Promise.allSettled([loadDevices(), loadDetail()]);
  }

  function leave() {
    state.active = false;
    for (const channel of ["list", "detail", "contracts", "timeline"]) cancel(channel);
    closeEvidence();
  }

  async function refresh() {
    state.contractOptions.delete(state.device);
    if (!await capabilities()) return;
    feedback("");
    await Promise.allSettled([loadDevices(), loadDetail()]);
  }

  function applyFilters() {
    const from = element("from").value;
    const to = element("to").value;
    if ((from && !validDate(from)) || (to && !validDate(to)) || (from && to && from > to)) { feedback("Podaj poprawny zakres: data od nie może być późniejsza niż data do."); return; }
    feedback("");
    state.from = from;
    state.to = to;
    state.contract = element("contract").value;
    state.timelinePage = 1;
    writeUrl();
    loadDetail();
  }

  function moveRange(shift, zoom) {
    if (!state.from || !state.to) return;
    const start = Date.parse(`${state.from}T00:00:00Z`);
    const end = Date.parse(`${state.to}T00:00:00Z`);
    const day = 86400000;
    const days = Math.round((end - start) / day) + 1;
    const nextDays = Math.max(1, Math.round(days * zoom));
    const nextStart = start + shift * days * day - Math.floor((nextDays - days) / 2) * day;
    element("from").value = new Date(nextStart).toISOString().slice(0, 10);
    element("to").value = new Date(nextStart + (nextDays - 1) * day).toISOString().slice(0, 10);
    applyFilters();
  }

  document.addEventListener("DOMContentLoaded", () => {
    element("pane")?.addEventListener("submit", savePolicy);
    element("pane")?.addEventListener("change", (event) => {
      if (event.target.hasAttribute("data-orbit-policy-scope")) state.policyScope = event.target.value;
      else if (event.target.hasAttribute("data-orbit-policy-color")) state.policyColor = event.target.value;
      else return;
      renderReport();
      element("pane").querySelector("details:has(#orbit-policy-form)")?.setAttribute("open", "");
    });
    const panel = document.getElementById("shipping-orbit-view");
    if (!panel) return;
    element("search").addEventListener("submit", (event) => { event.preventDefault(); loadDevices(true); });
    element("query").addEventListener("input", shippingDebounce(() => loadDevices(true), 300));
    element("scope").addEventListener("change", () => loadDevices(true));
    element("filters").addEventListener("submit", (event) => { event.preventDefault(); applyFilters(); });
    element("contract").addEventListener("change", applyFilters);
    element("refresh").addEventListener("click", refresh);
    element("bucket").addEventListener("change", () => { state.bucket = element("bucket").value; state.timelinePage = 1; loadTimeline(); });
    element("counter-series").addEventListener("change", () => { state.counterSeries = element("counter-series").value; if (state.timeline) renderTimeline(); });
    element("timeline-retry").addEventListener("click", () => loadTimeline());
    for (const prefix of ["devices", "timeline"]) {
      for (const [direction, delta] of [["prev", -1], ["next", 1]]) element(`${prefix}-${direction}`).addEventListener("click", () => {
        if (prefix === "devices") { state.listPage += delta; loadDevices(); }
        else { state.timelinePage += delta; loadTimeline(); }
      });
    }
    element("evidence-close").addEventListener("click", closeEvidence);
    element("evidence").addEventListener("close", () => { cancel("evidence"); element("evidence-body").replaceChildren(); if (state.evidenceTrigger?.isConnected) state.evidenceTrigger.focus(); });
    panel.addEventListener("click", (event) => {
      const target = event.target.closest("button, [role='button']");
      if (!target || target.disabled) return;
      const data = target.dataset;
      if (data.orbitTab) selectTab(data.orbitTab);
      if (data.orbitGoto) selectTab(data.orbitGoto);
      if (data.orbitDevice) {
        state.device = data.orbitDevice;
        state.contract = "";
        state.timelinePage = 1;
        feedback("");
        if (state.from && state.to && state.from > state.to) { state.from = ""; state.to = ""; }
        writeUrl();
        renderDevices();
        loadDetail();
      }
      if (Object.hasOwn(data, "orbitContract")) {
        state.contract = data.orbitContract;
        if (Object.hasOwn(data, "orbitContractLife")) state.tab = "contractlife";
        state.timelinePage = 1;
        writeUrl();
        loadDetail();
      }
      if (data.orbitEvidence) openEvidence(data.orbitEvidence, target);
      if (data.orbitRange) {
        const today = new Date().toLocaleDateString("en-CA", { timeZone: "Europe/Warsaw" });
        element("from").value = data.orbitRange === "year" ? `${today.slice(0, 4)}-01-01` : "";
        element("to").value = data.orbitRange === "year" ? today : "";
        applyFilters();
      }
      if (data.orbitShift) moveRange(Number(data.orbitShift), 1);
      if (data.orbitZoom) moveRange(0, Number(data.orbitZoom));
    });
    element("tabs").addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      const buttons = [...element("tabs").querySelectorAll("[data-orbit-tab]")].filter((button) => !button.hidden);
      const index = buttons.indexOf(event.target);
      if (index < 0) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next].focus();
      selectTab(buttons[next].dataset.orbitTab);
    });
    element("chart").addEventListener("keydown", (event) => {
      const target = event.target.closest("[data-orbit-evidence]");
      if (target && ["Enter", " "].includes(event.key)) { event.preventDefault(); openEvidence(target.dataset.orbitEvidence, target); }
    });
    document.getElementById("shipping-orbit-order-link")?.addEventListener("click", (event) => {
      if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      window.history.pushState(null, "", event.currentTarget.getAttribute("href"));
      switchShippingView("orbit", false);
    });
    window.addEventListener("popstate", () => {
      const view = new URLSearchParams(window.location.search).get("view");
      if (view === "orbit" && state.enabled) switchShippingView("orbit", false);
      else if (state.active) switchShippingView(["dispatch", "catalog", "toners", "tracking", "archive"].includes(view) ? view : "dispatch", false);
    });
  });

  window.shippingOrbitDashboard = { initialize, open, leave, refresh, updateOrderLink, get enabled() { return state.enabled; } };
})();
