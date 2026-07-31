(function () {
  "use strict";

  var formDefinitions = {
    contact: {
      channel: "form",
      queue: "other",
      category: "contact",
      subject: "Kontakt ze strony WWW",
      detail: "Formularz kontaktowy – następca Bitrix 81 / CF7",
    },
    product: {
      channel: "configurator",
      queue: "sales",
      category: "sales",
      subject: "Zapytanie o produkt lub wynajem",
      detail: "Formularz produktowy – następca Bitrix 79 / CF7",
    },
    service: {
      channel: "form",
      queue: "service_it",
      category: "service",
      subject: "Zgłoszenie serwisowe lub IT",
      detail: "Nowy formularz Centrum Obsługi",
    },
    contracts: {
      channel: "form",
      queue: "contracts",
      category: "contracts",
      subject: "Umowa lub przekazanie liczników",
      detail: "Nowy formularz Centrum Obsługi",
    },
    app: {
      channel: "form",
      queue: "service_it",
      category: "app",
      subject: "Pomoc z aplikacją Ksero Partner",
      detail: "Formularz aplikacji – następca Bitrix 93",
    },
  };

  var scenarios = {
    sales_chat: {
      channel: "chat",
      queue: "sales",
      category: "sales",
      priority: "normal",
      subject: "Wynajem trzech urządzeń do dwóch oddziałów",
      message: "Klient prosi o ofertę najmu, porównanie wariantów i termin wdrożenia.",
      company_name: "Fikcyjna Spółka Handlowa",
      contact_name: "Anna Przykładowa",
      phone: "600000101",
      email: "anna@example.com",
      identity_status: "exact",
    },
    sales_configurator: {
      channel: "configurator",
      queue: "sales",
      category: "sales",
      priority: "normal",
      subject: "Porównanie dwóch kolorowych urządzeń",
      message: "Konfigurator wskazał dwa modele. Klient oczekuje rekomendacji i ceny zakupu.",
      company_name: "Fikcyjna Pracownia Projektowa",
      contact_name: "Kamil Testowy",
      phone: "600000102",
      email: "kamil@example.com",
    },
    service_voice: {
      channel: "voice",
      queue: "service_it",
      category: "service",
      priority: "high",
      subject: "Pilna awaria urządzenia – SC542",
      message: "Urządzenie nie drukuje, kod SC542. Przestój blokuje pracę sekretariatu.",
      company_name: "Fikcyjne Centrum Medyczne",
      contact_name: "Ewa Przykładowa",
      phone: "600000103",
      email: "ewa@example.com",
      identity_status: "exact",
      device_label: "Ricoh IM C3000 · sekretariat",
      device_serial_last4: "T001",
    },
    it_form: {
      channel: "form",
      queue: "service_it",
      category: "it",
      priority: "normal",
      subject: "Konfiguracja skanowania SMB",
      message: "Po zmianie serwera skany nie trafiają do katalogu sieciowego.",
      company_name: "Fikcyjna Hurtownia",
      contact_name: "Piotr Testowy",
      phone: "600000104",
      email: "piotr@example.com",
      device_label: "Ricoh M C250FWB",
      device_serial_last4: "T002",
    },
    accounting_email: {
      channel: "email",
      queue: "other",
      category: "accounting",
      priority: "normal",
      subject: "Prośba o korektę danych na fakturze",
      message: "Klient wskazuje błędny adres na dokumencie testowym FV/LAB/27/07.",
      company_name: "Fikcyjne Biuro Rachunkowe",
      contact_name: "Monika Przykładowa",
      phone: "600000105",
      email: "monika@example.com",
    },
    contract_phone: {
      channel: "phone",
      queue: "contracts",
      category: "contracts",
      priority: "normal",
      subject: "Kończąca się umowa GRENKE",
      message: "Klient pyta o wykup urządzenia oraz wariant przedłużenia współpracy.",
      company_name: "Fikcyjna Kancelaria",
      contact_name: "Tomasz Testowy",
      phone: "600000106",
      email: "tomasz@example.com",
    },
    meters_form: {
      channel: "form",
      queue: "contracts",
      category: "meters",
      priority: "normal",
      subject: "Liczniki B/W, kolor i skan",
      message: "B/W: 125400; kolor: 48320; skan: 91200.",
      company_name: "Fikcyjna Firma Produkcyjna",
      contact_name: "Joanna Przykładowa",
      phone: "600000107",
      email: "joanna@example.com",
      identity_status: "exact",
      device_label: "Ricoh IM C4500 · biuro",
      device_serial_last4: "T003",
    },
    ambiguous_chat: {
      channel: "chat",
      queue: "other",
      category: "identity",
      priority: "high",
      subject: "Niejednoznaczne rozpoznanie klienta",
      message: "Numer telefonu pasuje do kilku kont. Nie ujawniono firmy ani urządzeń.",
      company_name: "Firma do potwierdzenia",
      contact_name: "Osoba do potwierdzenia",
      phone: "600000108",
      email: "identity@example.com",
      identity_status: "ambiguous",
    },
  };

  function uniqueReference(prefix) {
    var suffix = window.crypto && window.crypto.randomUUID
      ? window.crypto.randomUUID()
      : Date.now() + "-" + Math.random().toString(16).slice(2);
    return prefix + ":" + suffix;
  }

  function requestCase(payload) {
    var idempotencyKey = uniqueReference("lab-request");
    return fetch("/api/crm/v1/intake", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "Idempotency-Key": idempotencyKey,
      },
      body: JSON.stringify(payload),
    }).then(function (response) {
      return response.json().then(function (body) {
        if (!response.ok) {
          var detail = body.detail;
          if (Array.isArray(detail)) {
            detail = detail.map(function (item) {
              return item.msg;
            }).join(", ");
          }
          throw new Error(detail || "Nie udało się utworzyć sprawy LAB.");
        }
        return body;
      });
    });
  }

  function showResult(element, message, isError) {
    element.hidden = false;
    element.classList.toggle("is-error", Boolean(isError));
    element.textContent = message;
  }

  function formValue(data, name) {
    return String(data.get(name) || "").trim();
  }

  function structuredMessage(data, type) {
    var parts = [];
    var names = {
      product_name: "Produkt/model",
      product_intent: "Cel",
      quantity: "Liczba urządzeń",
      serial: "Numer seryjny",
      location: "Lokalizacja",
      service_area: "Obszar pomocy",
      error_code: "Kod błędu",
      meter_bw: "Licznik B/W",
      meter_color: "Licznik kolor",
      meter_scan: "Licznik skan",
      contract_case_type: "Typ sprawy",
      contract_number: "Numer umowy",
      contract_area: "Sprawa umowna",
      app_area: "Sprawa aplikacji",
    };
    var relevant = {
      product: ["product_name", "product_intent", "quantity"],
      service: ["serial", "location", "service_area", "error_code"],
      contracts: [
        "contract_case_type",
        "contract_number",
        "contract_area",
        "serial",
        "location",
        "meter_bw",
        "meter_color",
        "meter_scan",
      ],
      app: ["app_area"],
      contact: [],
    };
    (relevant[type] || []).forEach(function (name) {
      var value = formValue(data, name);
      if (value) {
        parts.push(names[name] + ": " + value);
      }
    });
    var message = formValue(data, "message");
    if (message) {
      parts.push("Opis: " + message);
    }
    return parts.length ? parts.join("\n") : "Prośba o kontakt z klientem.";
  }

  function setVisibleSections(form) {
    var type = form.querySelector("[data-form-type]").value;
    form.querySelectorAll("[data-show-for]").forEach(function (section) {
      var allowed = section.getAttribute("data-show-for").split(",");
      section.hidden = allowed.indexOf(type) === -1;
    });
  }

  function validateContact(form) {
    var phone = form.elements.phone;
    var email = form.elements.email;
    phone.classList.remove("lab-invalid");
    email.classList.remove("lab-invalid");
    if (!phone.value.trim() && !email.value.trim()) {
      phone.classList.add("lab-invalid");
      email.classList.add("lab-invalid");
      phone.setCustomValidity("Podaj telefon lub adres e-mail.");
      return false;
    }
    phone.setCustomValidity("");
    return true;
  }

  function initializeForm() {
    var form = document.querySelector("[data-lab-form]");
    if (!form) {
      return;
    }
    var result = form.querySelector("[data-lab-result]");
    var typeSelect = form.querySelector("[data-form-type]");
    typeSelect.addEventListener("change", function () {
      setVisibleSections(form);
    });
    form.addEventListener("reset", function () {
      window.setTimeout(function () {
        setVisibleSections(form);
        result.hidden = true;
      }, 0);
    });
    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var contactValid = validateContact(form);
      if (!form.checkValidity() || !contactValid) {
        form.reportValidity();
        showResult(result, "Uzupełnij oznaczone pola formularza.", true);
        return;
      }
      var submit = form.querySelector('button[type="submit"]');
      var data = new FormData(form);
      var type = formValue(data, "form_type");
      var definition = formDefinitions[type];
      var externalRef = uniqueReference("www-lab-" + type);
      var serial = formValue(data, "serial");
      var category = type === "contracts"
        ? formValue(data, "contract_case_type") || "contracts"
        : definition.category;
      var payload = {
        external_ref: externalRef,
        channel: definition.channel,
        queue: definition.queue,
        category: category,
        priority: type === "service" ? "high" : "normal",
        subject: category === "meters" ? "Przekazanie liczników" : definition.subject,
        message: structuredMessage(data, type),
        company_name: formValue(data, "company_name"),
        contact_name: formValue(data, "contact_name"),
        phone: formValue(data, "phone") || null,
        email: formValue(data, "email") || null,
        source_detail: definition.detail,
        source_url: "wordpress:centrum-obslugi-lab",
        device_label: serial ? "Urządzenie z formularza" : null,
        device_serial_last4: serial ? serial.slice(-4) : null,
        is_lab: true,
        metadata: {
          form_type: type,
          notifications_suppressed: true,
          firebird_write: false,
        },
      };
      submit.disabled = true;
      showResult(result, "Tworzenie sprawy testowej…", false);
      requestCase(payload)
        .then(function (body) {
          showResult(
            result,
            "Utworzono sprawę " + body.case.ref + " w kolejce " + body.case.queue + ".",
            false
          );
          form.reset();
        })
        .catch(function (error) {
          showResult(result, error.message, true);
        })
        .finally(function () {
          submit.disabled = false;
        });
    });
    setVisibleSections(form);
  }

  function initializeScenarios() {
    var grid = document.querySelector("[data-scenario-grid]");
    var result = document.querySelector("[data-scenario-result]");
    if (!grid || !result) {
      return;
    }
    grid.addEventListener("click", function (event) {
      var button = event.target.closest("[data-scenario]");
      if (!button) {
        return;
      }
      var scenarioName = button.getAttribute("data-scenario");
      var definition = scenarios[scenarioName];
      if (!definition) {
        return;
      }
      var payload = Object.assign({}, definition, {
        external_ref: uniqueReference("scenario-" + scenarioName),
        source_detail: "Generator scenariuszy CTIP LAB",
        source_url: "wordpress:centrum-obslugi-lab",
        is_lab: true,
        metadata: {
          scenario: scenarioName,
          notifications_suppressed: true,
          firebird_write: false,
        },
      });
      button.disabled = true;
      showResult(result, "Dodawanie scenariusza…", false);
      requestCase(payload)
        .then(function (body) {
          showResult(
            result,
            "Dodano " + body.case.ref + ": " + body.case.subject + ".",
            false
          );
        })
        .catch(function (error) {
          showResult(result, error.message, true);
        })
        .finally(function () {
          button.disabled = false;
        });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initializeForm();
    initializeScenarios();
  });
}());
