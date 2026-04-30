const TOKEN_KEY = "admin-session-token";

function readToken() {
  return (
    window.localStorage?.getItem(TOKEN_KEY) ||
    window.sessionStorage?.getItem(TOKEN_KEY) ||
    null
  );
}

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("pl-PL", { hour12: false });
}

async function requestJson(url, options = {}, token) {
  const headers = {
    "Content-Type": "application/json",
    "X-Admin-Session": token,
    ...(options.headers || {}),
  };
  const response = await fetch(url, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data?.detail || "Błąd API.");
  }
  return data;
}

function renderSources(sourcesList, sources = []) {
  sourcesList.innerHTML = "";
  if (!Array.isArray(sources) || sources.length === 0) {
    const li = document.createElement("li");
    li.textContent = "Brak odczytanych źródeł w tej odpowiedzi.";
    sourcesList.appendChild(li);
    return;
  }
  for (const source of sources) {
    const li = document.createElement("li");
    const rowCount = source?.row_count ?? "n/d";
    const duration = source?.duration_ms ?? "n/d";
    li.textContent = `${source.tool}: rekordy=${rowCount}, czas=${duration} ms`;
    sourcesList.appendChild(li);
  }
}

function renderChangeRequestInfo(element, payload) {
  if (!payload?.blocked_as_change_request) {
    element.textContent = "Brak nowych wniosków.";
    return;
  }
  if (payload?.change_request_id) {
    element.textContent = `Utworzono wniosek o zmianę #${payload.change_request_id}.`;
    return;
  }
  element.textContent = "Wykryto próbę modyfikacji danych. Wymagany ręczny wniosek o zmianę.";
}

function appendMessage(messagesEl, role, content, createdAt) {
  const wrapper = document.createElement("article");
  wrapper.className = `assistant-message ${role}`;
  wrapper.innerHTML = `
    <div class="assistant-message-content">${escapeHtml(content)}</div>
    <div class="assistant-message-meta">${role === "user" ? "Użytkownik" : "Asystent"} · ${escapeHtml(formatDate(createdAt))}</div>
  `;
  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return wrapper;
}

function setBusy(sendBtn, inputEl, busy) {
  sendBtn.disabled = busy;
  inputEl.disabled = busy;
  sendBtn.textContent = busy ? "Wysyłanie..." : "Wyślij";
}

async function bootstrapAssistantPage() {
  const token = readToken();
  if (!token) {
    window.location.replace("/");
    return;
  }

  const newChatBtn = document.getElementById("assistant-new-chat-btn");
  const chatList = document.getElementById("assistant-chat-list");
  const chatTitle = document.getElementById("assistant-chat-title");
  const messagesEl = document.getElementById("assistant-messages");
  const promptForm = document.getElementById("assistant-prompt-form");
  const promptInput = document.getElementById("assistant-prompt-input");
  const sendBtn = document.getElementById("assistant-send-btn");
  const sourcesList = document.getElementById("assistant-sources-list");
  const changeRequestInfo = document.getElementById("assistant-change-request-info");
  if (
    !newChatBtn ||
    !chatList ||
    !chatTitle ||
    !messagesEl ||
    !promptForm ||
    !promptInput ||
    !sendBtn ||
    !sourcesList ||
    !changeRequestInfo
  ) {
    return;
  }

  const state = {
    threads: [],
    activeThreadId: null,
  };

  const refreshThreads = async () => {
    state.threads = await requestJson("/assistant/chats", { method: "GET" }, token);
    if (!Array.isArray(state.threads)) {
      state.threads = [];
    }
    if (!state.activeThreadId && state.threads.length > 0) {
      state.activeThreadId = state.threads[0].id;
    }
    chatList.innerHTML = "";
    for (const thread of state.threads) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      if (thread.id === state.activeThreadId) {
        button.classList.add("active");
      }
      button.innerHTML = `<strong>${escapeHtml(thread.title)}</strong><br><small>${escapeHtml(formatDate(thread.last_activity_at))}</small>`;
      button.addEventListener("click", async () => {
        state.activeThreadId = thread.id;
        await refreshThreads();
        await loadThread(thread.id);
      });
      li.appendChild(button);
      chatList.appendChild(li);
    }
  };

  const loadThread = async (threadId) => {
    if (!threadId) {
      messagesEl.innerHTML = "";
      chatTitle.textContent = "Wybierz rozmowę";
      return;
    }
    const data = await requestJson(`/assistant/chats/${threadId}`, { method: "GET" }, token);
    chatTitle.textContent = data?.thread?.title || "Rozmowa";
    messagesEl.innerHTML = "";
    const messages = Array.isArray(data?.messages) ? data.messages : [];
    for (const item of messages) {
      appendMessage(messagesEl, item.role, item.content, item.created_at);
    }
    renderSources(sourcesList, []);
    renderChangeRequestInfo(changeRequestInfo, null);
  };

  const createNewThread = async () => {
    const created = await requestJson(
      "/assistant/chats",
      {
        method: "POST",
        body: JSON.stringify({ title: null }),
      },
      token,
    );
    state.activeThreadId = created.id;
    await refreshThreads();
    await loadThread(created.id);
  };

  async function handleSseResponse(response, assistantNode) {
    if (!response.body) {
      throw new Error("Brak strumienia odpowiedzi SSE.");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    const donePayload = { sources: [], blocked_as_change_request: false };
    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      let splitIndex = buffer.indexOf("\n\n");
      while (splitIndex !== -1) {
        const eventBlock = buffer.slice(0, splitIndex);
        buffer = buffer.slice(splitIndex + 2);
        splitIndex = buffer.indexOf("\n\n");
        const lines = eventBlock.split("\n");
        let eventName = "message";
        let eventData = "";
        for (const line of lines) {
          if (line.startsWith("event:")) {
            eventName = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            eventData += line.slice(5).trim();
          }
        }
        if (!eventData) {
          continue;
        }
        let parsed;
        try {
          parsed = JSON.parse(eventData);
        } catch (_err) {
          continue;
        }
        if (eventName === "chunk") {
          const contentNode = assistantNode.querySelector(".assistant-message-content");
          if (contentNode) {
            contentNode.textContent = `${contentNode.textContent || ""}${parsed.text || ""}`;
          }
        } else if (eventName === "done") {
          Object.assign(donePayload, parsed || {});
        }
      }
    }
    return donePayload;
  }

  promptForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const prompt = promptInput.value.trim();
    if (!prompt) {
      return;
    }
    if (!state.activeThreadId) {
      await createNewThread();
    }
    if (!state.activeThreadId) {
      throw new Error("Nie udało się utworzyć rozmowy.");
    }

    setBusy(sendBtn, promptInput, true);
    try {
      appendMessage(messagesEl, "user", prompt, new Date().toISOString());
      promptInput.value = "";
      const assistantNode = appendMessage(messagesEl, "assistant", "", new Date().toISOString());
      const response = await fetch(`/assistant/chats/${state.activeThreadId}/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Admin-Session": token,
        },
        body: JSON.stringify({ prompt, stream: true }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload?.detail || "Nie udało się pobrać odpowiedzi asystenta.");
      }
      const donePayload = await handleSseResponse(response, assistantNode);
      await refreshThreads();
      if (state.activeThreadId) {
        await loadThread(state.activeThreadId);
      }
      renderSources(sourcesList, donePayload?.sources || []);
      renderChangeRequestInfo(changeRequestInfo, donePayload || {});
    } catch (err) {
      appendMessage(
        messagesEl,
        "assistant",
        err instanceof Error ? err.message : "Błąd komunikacji z asystentem.",
        new Date().toISOString(),
      );
    } finally {
      setBusy(sendBtn, promptInput, false);
      promptInput.focus();
    }
  });

  newChatBtn.addEventListener("click", async () => {
    await createNewThread();
  });

  try {
    await refreshThreads();
    if (state.activeThreadId) {
      await loadThread(state.activeThreadId);
    } else {
      await createNewThread();
    }
  } catch (err) {
    messagesEl.innerHTML = `<article class="assistant-message assistant">${escapeHtml(err instanceof Error ? err.message : "Błąd inicjalizacji modułu asystenta.")}</article>`;
  }
}

bootstrapAssistantPage();
