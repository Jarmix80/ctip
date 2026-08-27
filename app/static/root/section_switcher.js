const SECTION_SWITCHER_TOKEN_KEY = "admin-session-token";

function readSectionSwitcherToken() {
  return (
    window.localStorage?.getItem(SECTION_SWITCHER_TOKEN_KEY) ||
    window.sessionStorage?.getItem(SECTION_SWITCHER_TOKEN_KEY) ||
    null
  );
}

function sectionLabel(section) {
  const labels = {
    admin: "Panel administracyjny",
    operator: "Panel operatora",
    generator: "Generator formularzy",
    delivery: "Obsługa dostaw",
    shipping: "Wysyłki części",
  };
  return labels[section] || section;
}

function sectionPath(section) {
  const paths = {
    admin: "/admin",
    operator: "/operator",
    generator: "/genform",
    delivery: "/delivery",
    shipping: "/shipping",
  };
  return paths[section] || "/choice";
}

async function hydrateSectionSwitchers() {
  const wrappers = Array.from(document.querySelectorAll("[data-section-switcher]"));
  if (!wrappers.length) {
    return;
  }

  wrappers.forEach((wrapper) => {
    wrapper.hidden = true;
  });

  const token = readSectionSwitcherToken();
  if (!token) {
    return;
  }

  let user = null;
  try {
    const response = await fetch("/auth/me", {
      headers: { "X-Admin-Session": token },
    });
    if (!response.ok) {
      return;
    }
    user = await response.json();
  } catch (err) {
    return;
  }

  const sections = Array.isArray(user?.sections) ? user.sections : [];
  wrappers.forEach((wrapper) => {
    const select = wrapper.querySelector("[data-role='section-select']");
    if (!(select instanceof HTMLSelectElement)) {
      return;
    }
    const options = [
      { value: "", label: "Przełącz sekcję…" },
      { value: "/choice", label: "Wybór sekcji" },
      ...sections.map((section) => ({
        value: sectionPath(section),
        label: sectionLabel(section),
      })),
    ];
    select.innerHTML = options
      .map((item) => `<option value="${item.value}">${item.label}</option>`)
      .join("");
    wrapper.hidden = false;
    select.value = "";
    select.onchange = () => {
      const next = select.value;
      if (!next) {
        return;
      }
      window.location.assign(next);
    };
  });
}

window.addEventListener("DOMContentLoaded", hydrateSectionSwitchers);
window.addEventListener("ctip:session-changed", () => {
  hydrateSectionSwitchers();
});
