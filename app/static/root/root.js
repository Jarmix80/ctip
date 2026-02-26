const ROOT_TOKEN_KEY = "admin-session-token";

function readRootToken() {
  return (
    window.localStorage?.getItem(ROOT_TOKEN_KEY) ||
    window.sessionStorage?.getItem(ROOT_TOKEN_KEY) ||
    null
  );
}

function storeRootToken(token, remember) {
  try {
    window.localStorage?.removeItem(ROOT_TOKEN_KEY);
    window.sessionStorage?.removeItem(ROOT_TOKEN_KEY);
    if (!token) {
      return;
    }
    if (remember) {
      window.localStorage?.setItem(ROOT_TOKEN_KEY, token);
    } else {
      window.sessionStorage?.setItem(ROOT_TOKEN_KEY, token);
    }
    window.dispatchEvent(new Event("ctip:session-changed"));
  } catch (err) {
    console.error("Nie udało się zapisać tokenu portalu", err);
  }
}

function initializeRootLoginPage() {
  const loginForm = document.getElementById("root-login-form");
  const loginError = document.getElementById("root-login-error");
  const loginSubmit = document.getElementById("root-login-submit");
  const emailInput = document.getElementById("root-email");
  const passwordInput = document.getElementById("root-password");
  const rememberInput = document.getElementById("root-remember");

  if (!loginForm || !loginError || !loginSubmit || !emailInput || !passwordInput || !rememberInput) {
    return;
  }

  function setBusy(busy) {
    loginSubmit.disabled = busy;
    loginSubmit.textContent = busy ? "Logowanie…" : "Zaloguj";
  }

  loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setBusy(true);
    loginError.hidden = true;
    loginError.textContent = "";
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
        throw new Error(data.detail || "Nie udało się zalogować.");
      }
      storeRootToken(data.token || null, Boolean(rememberInput.checked));
      window.location.assign("/choice");
    } catch (err) {
      storeRootToken(null, false);
      loginError.textContent = err instanceof Error ? err.message : "Błąd logowania.";
      loginError.hidden = false;
    } finally {
      setBusy(false);
    }
  });
}

async function initializeRootChoicePage() {
  const sectionsCard = document.getElementById("root-sections-card");
  const logoutBtn = document.getElementById("root-logout-btn");
  const userLine = document.getElementById("root-user-line");
  const sectionsEmpty = document.getElementById("root-sections-empty");
  const sectionButtons = Array.from(document.querySelectorAll("[data-section]"));
  if (!sectionsCard || !logoutBtn || !userLine || !sectionsEmpty) {
    return;
  }

  let token = readRootToken();
  if (!token) {
    window.location.replace("/");
    return;
  }

  try {
    const response = await fetch("/auth/me", {
      headers: { "X-Admin-Session": token },
    });
    if (!response.ok) {
      token = null;
      storeRootToken(null, false);
      window.location.replace("/");
      return;
    }
    const user = await response.json();
    const fullName = [user.first_name, user.last_name].filter(Boolean).join(" ");
    userLine.textContent = `${fullName || user.email} (${user.role})`;

    const sections = new Set(Array.isArray(user.sections) ? user.sections : []);
    let visibleCount = 0;
    sectionButtons.forEach((button) => {
      const section = button.getAttribute("data-section") || "";
      const visible = sections.has(section);
      button.hidden = !visible;
      if (visible) {
        visibleCount += 1;
      }
    });
    sectionsEmpty.hidden = visibleCount > 0;
  } catch (err) {
    storeRootToken(null, false);
    window.location.replace("/");
    return;
  }

  logoutBtn.addEventListener("click", async () => {
    try {
      if (token) {
        await fetch("/auth/logout", {
          method: "POST",
          headers: { "X-Admin-Session": token },
        });
      }
    } catch (err) {
      console.error("Błąd wylogowania", err);
    } finally {
      storeRootToken(null, false);
      window.location.replace("/");
    }
  });
}

function initializeRootPage() {
  if (document.getElementById("root-login-form")) {
    initializeRootLoginPage();
    return;
  }
  if (document.getElementById("root-sections-card")) {
    initializeRootChoicePage();
  }
}

window.addEventListener("DOMContentLoaded", initializeRootPage);
