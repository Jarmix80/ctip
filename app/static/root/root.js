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
  const profileToggle = document.getElementById("root-profile-toggle");
  const profileForm = document.getElementById("root-profile-form");
  const profileEmail = document.getElementById("root-profile-email");
  const profileFirstName = document.getElementById("root-profile-first-name");
  const profileLastName = document.getElementById("root-profile-last-name");
  const profileInternalExt = document.getElementById("root-profile-internal-ext");
  const profileMobilePhone = document.getElementById("root-profile-mobile-phone");
  const profileSubmit = document.getElementById("root-profile-submit");
  const profileCancel = document.getElementById("root-profile-cancel");
  const profileFeedback = document.getElementById("root-profile-feedback");
  const passwordToggle = document.getElementById("root-password-toggle");
  const passwordForm = document.getElementById("root-password-form");
  const passwordCurrent = document.getElementById("root-password-current");
  const passwordNew = document.getElementById("root-password-new");
  const passwordConfirm = document.getElementById("root-password-confirm");
  const passwordSubmit = document.getElementById("root-password-submit");
  const passwordCancel = document.getElementById("root-password-cancel");
  const passwordFeedback = document.getElementById("root-password-feedback");
  const sectionButtons = Array.from(document.querySelectorAll("[data-section]"));
  if (
    !sectionsCard ||
    !logoutBtn ||
    !userLine ||
    !sectionsEmpty ||
    !profileToggle ||
    !profileForm ||
    !profileEmail ||
    !profileFirstName ||
    !profileLastName ||
    !profileInternalExt ||
    !profileMobilePhone ||
    !profileSubmit ||
    !profileCancel ||
    !profileFeedback ||
    !passwordToggle ||
    !passwordForm ||
    !passwordCurrent ||
    !passwordNew ||
    !passwordConfirm ||
    !passwordSubmit ||
    !passwordCancel ||
    !passwordFeedback
  ) {
    return;
  }

  let token = readRootToken();
  if (!token) {
    window.location.replace("/");
    return;
  }

  const updateUserLine = (profile) => {
    const fullName = [profile.first_name, profile.last_name].filter(Boolean).join(" ");
    userLine.textContent = `${fullName || profile.email} (${profile.role})`;
  };

  const setProfileFeedback = (message, variant = "") => {
    profileFeedback.textContent = message || "";
    profileFeedback.hidden = !message;
    profileFeedback.className = "root-profile-feedback";
    if (variant) {
      profileFeedback.classList.add(variant);
    }
  };

  const setPasswordFeedback = (message, variant = "") => {
    passwordFeedback.textContent = message || "";
    passwordFeedback.hidden = !message;
    passwordFeedback.className = "root-profile-feedback";
    if (variant) {
      passwordFeedback.classList.add(variant);
    }
  };

  const fillProfileForm = (profile) => {
    profileEmail.value = profile.email || "";
    profileFirstName.value = profile.first_name || "";
    profileLastName.value = profile.last_name || "";
    profileInternalExt.value = profile.internal_ext || "";
    profileMobilePhone.value = profile.mobile_phone || "";
  };

  const readProfilePayload = () => ({
    email: profileEmail.value.trim().toLowerCase(),
    first_name: profileFirstName.value.trim() || null,
    last_name: profileLastName.value.trim() || null,
    internal_ext: profileInternalExt.value.trim() || null,
    mobile_phone: profileMobilePhone.value.trim() || null,
  });

  const parseErrorMessage = async (response, fallback) => {
    const payload = await response.json().catch(() => ({}));
    if (Array.isArray(payload?.detail)) {
      const messages = payload.detail
        .map((item) => (item && typeof item.msg === "string" ? item.msg : ""))
        .filter(Boolean);
      if (messages.length) {
        return messages.join(" ");
      }
    }
    if (typeof payload?.detail === "string" && payload.detail) {
      return payload.detail;
    }
    return fallback;
  };

  const fetchProfile = async () => {
    const response = await fetch("/auth/profile", {
      headers: { "X-Admin-Session": token },
    });
    if (!response.ok) {
      throw new Error(await parseErrorMessage(response, "Nie udało się pobrać profilu."));
    }
    return response.json();
  };

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
    updateUserLine(user);

    const sections = new Set(Array.isArray(user.sections) ? user.sections : []);
    let visibleCount = 0;
    sectionButtons.forEach((button) => {
      const section = button.getAttribute("data-section") || "";
      const visible = section === "assistant" ? true : sections.has(section);
      button.hidden = !visible;
      if (visible) {
        visibleCount += 1;
      }
    });
    sectionsEmpty.hidden = visibleCount > 0;

    const profile = await fetchProfile();
    fillProfileForm(profile);
  } catch (err) {
    storeRootToken(null, false);
    window.location.replace("/");
    return;
  }

  profileToggle.addEventListener("click", () => {
    const nextVisible = profileForm.hidden;
    profileForm.hidden = !nextVisible;
    profileToggle.textContent = nextVisible ? "Ukryj profil" : "Edytuj profil";
    if (!nextVisible) {
      setProfileFeedback("");
    }
  });

  profileCancel.addEventListener("click", () => {
    profileForm.hidden = true;
    profileToggle.textContent = "Edytuj profil";
    setProfileFeedback("");
  });

  passwordToggle.addEventListener("click", () => {
    const nextVisible = passwordForm.hidden;
    passwordForm.hidden = !nextVisible;
    passwordToggle.textContent = nextVisible ? "Ukryj zmianę hasła" : "Zmień hasło";
    if (!nextVisible) {
      setPasswordFeedback("");
    }
  });

  passwordCancel.addEventListener("click", () => {
    passwordForm.hidden = true;
    passwordToggle.textContent = "Zmień hasło";
    passwordCurrent.value = "";
    passwordNew.value = "";
    passwordConfirm.value = "";
    setPasswordFeedback("");
  });

  profileForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setProfileFeedback("");
    profileSubmit.disabled = true;
    profileSubmit.textContent = "Zapisywanie…";
    try {
      const response = await fetch("/auth/profile", {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Session": token,
        },
        body: JSON.stringify(readProfilePayload()),
      });
      if (!response.ok) {
        throw new Error(await parseErrorMessage(response, "Nie udało się zapisać profilu."));
      }
      const updated = await response.json();
      fillProfileForm(updated);
      updateUserLine(updated);
      setProfileFeedback("Dane profilu zostały zapisane.", "success");
    } catch (err) {
      setProfileFeedback(err instanceof Error ? err.message : "Błąd zapisu profilu.", "error");
    } finally {
      profileSubmit.disabled = false;
      profileSubmit.textContent = "Zapisz profil";
    }
  });

  passwordForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    setPasswordFeedback("");

    const currentPassword = passwordCurrent.value;
    const newPassword = passwordNew.value;
    const confirmPassword = passwordConfirm.value;
    if (!currentPassword || !newPassword || !confirmPassword) {
      setPasswordFeedback("Uzupełnij wszystkie pola hasła.", "error");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordFeedback("Nowe hasło i potwierdzenie muszą być identyczne.", "error");
      return;
    }

    passwordSubmit.disabled = true;
    passwordSubmit.textContent = "Zmiana…";
    try {
      const response = await fetch("/auth/profile/change-password", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Session": token,
        },
        body: JSON.stringify({
          current_password: currentPassword,
          new_password: newPassword,
        }),
      });
      if (!response.ok) {
        throw new Error(await parseErrorMessage(response, "Nie udało się zmienić hasła."));
      }
      passwordCurrent.value = "";
      passwordNew.value = "";
      passwordConfirm.value = "";
      setPasswordFeedback(
        "Hasło zostało zmienione. Pozostałe sesje użytkownika zostały unieważnione.",
        "success",
      );
    } catch (err) {
      setPasswordFeedback(err instanceof Error ? err.message : "Błąd zmiany hasła.", "error");
    } finally {
      passwordSubmit.disabled = false;
      passwordSubmit.textContent = "Zmień hasło";
    }
  });

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
