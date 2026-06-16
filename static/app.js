const authScreen = document.querySelector("#auth-screen");
const appShell = document.querySelector("#app-shell");
const authForm = document.querySelector("#auth-form");
const authUsername = document.querySelector("#auth-username");
const authPassword = document.querySelector("#auth-password");
const authSubmit = document.querySelector("#auth-submit");
const authMessage = document.querySelector("#auth-message");
const loginTab = document.querySelector("#login-tab");
const registerTab = document.querySelector("#register-tab");

const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const messagesEl = document.querySelector("#messages");
const sendButton = document.querySelector("#send-button");
const statusEl = document.querySelector("#status");
const sessionListEl = document.querySelector("#session-list");
const newChatButton = document.querySelector("#new-chat-button");
const clearChatButton = document.querySelector("#clear-chat-button");
const logoutButton = document.querySelector("#logout-button");
const userPill = document.querySelector("#user-pill");

const TOKEN_KEY = "ai-chat-token";
const USER_KEY = "ai-chat-user";
const WELCOME_MESSAGE = {
  role: "assistant",
  content: "你好，我在这里。登录后你的聊天记录会保存到数据库里，换浏览器也不会混到别人那里。",
};

let authMode = "login";
let token = localStorage.getItem(TOKEN_KEY);
let currentUser = loadSavedUser();
let sessions = [];
let activeSessionId = null;

function loadSavedUser() {
  try {
    return JSON.parse(localStorage.getItem(USER_KEY) || "null");
  } catch {
    return null;
  }
}

function saveAuth(auth) {
  token = auth.token;
  currentUser = auth.user;
  localStorage.setItem(TOKEN_KEY, auth.token);
  localStorage.setItem(USER_KEY, JSON.stringify(auth.user));
}

function clearAuth() {
  token = null;
  currentUser = null;
  sessions = [];
  activeSessionId = null;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

function setAuthMode(mode) {
  authMode = mode;
  loginTab.classList.toggle("active", mode === "login");
  registerTab.classList.toggle("active", mode === "register");
  authSubmit.textContent = mode === "login" ? "登录" : "注册";
  authPassword.autocomplete = mode === "login" ? "current-password" : "new-password";
  authMessage.textContent = "";
}

function showAuth(message = "") {
  authScreen.classList.remove("is-hidden");
  appShell.classList.add("is-hidden");
  authMessage.textContent = message;
  authUsername.focus();
}

function showApp() {
  authScreen.classList.add("is-hidden");
  appShell.classList.remove("is-hidden");
  userPill.textContent = currentUser ? currentUser.username : "";
  input.focus();
}

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(path, { ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) {
      clearAuth();
      showAuth("登录已失效，请重新登录。");
    }
    throw new Error(data.detail || "请求失败，请稍后重试。");
  }
  return data;
}

function formatSessionTime(timestamp) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp * 1000));
}

function getActiveSession() {
  return sessions.find((session) => session.id === activeSessionId) || null;
}

function upsertSession(session) {
  const index = sessions.findIndex((item) => item.id === session.id);
  if (index >= 0) {
    sessions[index] = session;
  } else {
    sessions.unshift(session);
  }
  sessions.sort((a, b) => b.updated_at - a.updated_at || b.id - a.id);
  activeSessionId = session.id;
}

function addMessage(role, content) {
  const item = document.createElement("article");
  item.className = `message ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = content;

  item.appendChild(bubble);
  messagesEl.appendChild(item);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function addTypingMessage() {
  const item = document.createElement("article");
  item.className = "message assistant typing";
  item.innerHTML = `
    <div class="bubble">
      AI 正在思考
      <span class="typing-dots" aria-hidden="true">
        <span></span><span></span><span></span>
      </span>
    </div>
  `;
  messagesEl.appendChild(item);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return item;
}

function renderMessages() {
  const session = getActiveSession();
  messagesEl.innerHTML = "";
  [WELCOME_MESSAGE, ...(session?.messages || [])].forEach((message) => {
    addMessage(message.role, message.content);
  });
}

function renderSessions() {
  sessionListEl.innerHTML = "";
  sessions.forEach((session) => {
    const item = document.createElement("div");
    item.className = `session-item${session.id === activeSessionId ? " active" : ""}`;
    item.dataset.sessionId = session.id;

    const openButton = document.createElement("button");
    openButton.type = "button";
    openButton.className = "session-open";

    const title = document.createElement("span");
    title.className = "session-title";
    title.textContent = session.title;

    const meta = document.createElement("span");
    meta.className = "session-meta";
    meta.textContent = `${session.messages.length} 条消息 · ${formatSessionTime(session.updated_at)}`;

    const actions = document.createElement("div");
    actions.className = "session-actions";

    const renameButton = document.createElement("button");
    renameButton.type = "button";
    renameButton.className = "session-action";
    renameButton.dataset.action = "rename";
    renameButton.textContent = "改名";

    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "session-action danger";
    deleteButton.dataset.action = "delete";
    deleteButton.textContent = "删除";

    openButton.append(title, meta);
    actions.append(renameButton, deleteButton);
    item.append(openButton, actions);
    sessionListEl.appendChild(item);
  });
}

function render() {
  renderSessions();
  renderMessages();
}

async function loadSessions() {
  sessions = await api("/api/sessions");
  activeSessionId = sessions[0]?.id || null;
  render();
}

async function sendMessage(content) {
  sendButton.disabled = true;
  input.disabled = true;
  setStatus("AI 思考中...");
  addMessage("user", content);
  const typingMessage = addTypingMessage();

  try {
    const data = await api("/api/chat", {
      method: "POST",
      body: JSON.stringify({ content, session_id: activeSessionId }),
    });
    typingMessage.remove();
    upsertSession(data.session);
    render();
    setStatus("本地运行中");
  } catch (error) {
    typingMessage.remove();
    addMessage("assistant", `出错了：${error.message}`);
    setStatus("请求失败", true);
  } finally {
    sendButton.disabled = false;
    input.disabled = false;
    input.focus();
  }
}

authForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  authSubmit.disabled = true;
  authMessage.textContent = authMode === "login" ? "正在登录..." : "正在注册...";

  try {
    const username = authUsername.value.trim();
    const data = await api(`/api/auth/${authMode}`, {
      method: "POST",
      body: JSON.stringify({
        username,
        password: authPassword.value,
      }),
    });

    if (authMode === "register") {
      clearAuth();
      setAuthMode("login");
      authUsername.value = username;
      authPassword.value = "";
      authMessage.textContent = data.message || "注册成功，请登录。";
      authPassword.focus();
      return;
    }

    saveAuth(data);
    authPassword.value = "";
    showApp();
    await loadSessions();
    setStatus("本地运行中");
  } catch (error) {
    authMessage.textContent = error.message;
  } finally {
    authSubmit.disabled = false;
  }
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const content = input.value.trim();
  if (!content) return;
  input.value = "";
  sendMessage(content);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});

sessionListEl.addEventListener("click", (event) => {
  const item = event.target.closest(".session-item");
  if (!item) return;
  const sessionId = Number(item.dataset.sessionId);
  const action = event.target.dataset.action;

  if (action === "rename") {
    renameSession(sessionId);
    return;
  }

  if (action === "delete") {
    deleteSession(sessionId);
    return;
  }

  activeSessionId = Number(item.dataset.sessionId);
  render();
  input.focus();
});

async function renameSession(sessionId) {
  const session = sessions.find((item) => item.id === sessionId);
  if (!session) return;
  const title = window.prompt("输入新的聊天标题", session.title);
  if (title === null) return;
  const nextTitle = title.trim();
  if (!nextTitle) {
    setStatus("标题不能为空", true);
    return;
  }

  try {
    const updated = await api(`/api/sessions/${sessionId}`, {
      method: "PATCH",
      body: JSON.stringify({ title: nextTitle }),
    });
    upsertSession(updated);
    render();
    setStatus("标题已更新");
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function deleteSession(sessionId) {
  const session = sessions.find((item) => item.id === sessionId);
  if (!session) return;
  const confirmed = window.confirm(`确定删除「${session.title}」吗？`);
  if (!confirmed) return;

  try {
    await api(`/api/sessions/${sessionId}`, { method: "DELETE" });
    sessions = sessions.filter((item) => item.id !== sessionId);
    if (activeSessionId === sessionId) {
      activeSessionId = sessions[0]?.id || null;
    }
    render();
    setStatus("聊天记录已删除");
  } catch (error) {
    setStatus(error.message, true);
  }
}

newChatButton.addEventListener("click", async () => {
  try {
    const session = await api("/api/sessions", { method: "POST", body: "{}" });
    upsertSession(session);
    render();
    setStatus("本地运行中");
    input.focus();
  } catch (error) {
    setStatus(error.message, true);
  }
});

clearChatButton.addEventListener("click", async () => {
  if (!activeSessionId) return;
  try {
    const session = await api(`/api/sessions/${activeSessionId}/clear`, { method: "POST", body: "{}" });
    upsertSession(session);
    render();
    setStatus("已清空当前对话");
    input.focus();
  } catch (error) {
    setStatus(error.message, true);
  }
});

logoutButton.addEventListener("click", () => {
  clearAuth();
  render();
  showAuth("已退出登录。");
});

loginTab.addEventListener("click", () => setAuthMode("login"));
registerTab.addEventListener("click", () => setAuthMode("register"));

async function boot() {
  setAuthMode("login");
  if (!token) {
    showAuth();
    return;
  }

  try {
    currentUser = await api("/api/auth/me");
    localStorage.setItem(USER_KEY, JSON.stringify(currentUser));
    showApp();
    await loadSessions();
  } catch {
    clearAuth();
    showAuth("请先登录。");
  }
}

boot();
