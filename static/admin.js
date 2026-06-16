const TOKEN_KEY = "ai-chat-token";

const accessPanel = document.querySelector("#login-panel");
const dashboard = document.querySelector("#dashboard");
const accessTitle = document.querySelector("#access-title");
const accessMessage = document.querySelector("#login-message");
const loginForm = document.querySelector("#login-form");
const adminUser = document.querySelector("#admin-user");
const refreshButton = document.querySelector("#refresh-button");
const logoutButton = document.querySelector("#logout-button");

const userCount = document.querySelector("#user-count");
const sessionCount = document.querySelector("#session-count");
const messageCount = document.querySelector("#message-count");
const todayUsed = document.querySelector("#today-used");
const limitLabel = document.querySelector("#limit-label");
const userTable = document.querySelector("#user-table");
const detailPanel = document.querySelector("#detail-panel");
const detailTitle = document.querySelector("#detail-title");
const detailContent = document.querySelector("#detail-content");
const closeDetailButton = document.querySelector("#close-detail-button");
const limitModal = document.querySelector("#limit-modal");
const limitForm = document.querySelector("#limit-form");
const limitModalUser = document.querySelector("#limit-modal-user");
const limitInput = document.querySelector("#limit-input");
const limitCancelButton = document.querySelector("#limit-cancel-button");

let token = localStorage.getItem(TOKEN_KEY);
let currentUser = null;
let editingLimitUserId = null;

function showAccess(message = "请先在聊天页登录管理员账号。") {
  accessPanel.classList.remove("is-hidden");
  dashboard.classList.add("is-hidden");
  accessMessage.textContent = message;
}

function showDashboard() {
  accessPanel.classList.add("is-hidden");
  dashboard.classList.remove("is-hidden");
  adminUser.textContent = currentUser?.username || "";
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
    if (response.status === 401 || response.status === 403) {
      showAccess(data.detail || "当前账号没有管理员权限。");
    }
    throw new Error(data.detail || "请求失败。");
  }
  return data;
}

function formatTime(timestamp) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(timestamp * 1000));
}

function setText(element, value) {
  element.textContent = String(value);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderStats(stats) {
  setText(userCount, stats.user_count);
  setText(sessionCount, stats.session_count);
  setText(messageCount, stats.message_count);
  setText(todayUsed, stats.today_used);
  limitLabel.textContent = `普通用户每日上限 ${stats.daily_limit} 次`;

  userTable.innerHTML = "";
  stats.users.forEach((user) => {
    const row = document.createElement("tr");
    const remaining = user.unlimited ? "不限" : `${user.remaining}/${user.daily_limit}`;
    const badges = [
      user.is_admin ? '<span class="badge admin">管理员</span>' : "",
      user.unlimited ? '<span class="badge unlimited">不限次数</span>' : "",
    ]
      .filter(Boolean)
      .join(" ");

    row.innerHTML = `
      <td>${escapeHtml(user.username)}</td>
      <td>${formatTime(user.created_at)}</td>
      <td>${user.session_count}</td>
      <td>${user.message_count}</td>
      <td>${user.today_used}</td>
      <td>${remaining}</td>
      <td>${badges || '<span class="badge">普通用户</span>'}</td>
      <td>
        <div class="row-actions">
          <button type="button" data-action="toggle" data-user-id="${user.id}">
            ${user.is_disabled ? "启用" : "禁用"}
          </button>
          <button type="button" data-action="limit" data-user-id="${user.id}">额度</button>
          <button type="button" data-action="clear-limit" data-user-id="${user.id}">默认</button>
          <button type="button" data-action="detail" data-user-id="${user.id}">记录</button>
        </div>
      </td>
    `;
    if (user.is_disabled) {
      row.classList.add("disabled-row");
    }
    userTable.appendChild(row);
  });
}

async function loadDashboard() {
  const stats = await api("/api/admin/stats");
  renderStats(stats);
}

async function updateUser(userId, payload) {
  await api(`/api/admin/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
  await loadDashboard();
}

async function clearLimit(userId) {
  await api(`/api/admin/users/${userId}/limit`, { method: "DELETE" });
  await loadDashboard();
}

async function showUserDetail(userId) {
  const detail = await api(`/api/admin/users/${userId}`);
  detailTitle.textContent = `${detail.user.username} 的最近聊天记录`;
  detailContent.innerHTML = "";

  if (!detail.sessions.length) {
    detailContent.innerHTML = '<p class="empty-state">暂无聊天记录。</p>';
  }

  detail.sessions.forEach((session) => {
    const block = document.createElement("article");
    block.className = "session-detail";

    const heading = document.createElement("h3");
    heading.textContent = session.title;
    block.appendChild(heading);

    session.messages.slice(-8).forEach((message) => {
      const item = document.createElement("div");
      item.className = `detail-message ${message.role}`;

      const role = document.createElement("strong");
      role.textContent = message.role === "user" ? "用户" : "AI";

      const content = document.createElement("span");
      content.textContent = message.content;

      item.append(role, content);
      block.appendChild(item);
    });

    detailContent.appendChild(block);
  });

  detailPanel.classList.remove("is-hidden");
  detailPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

function openLimitModal(userId) {
  const row = [...userTable.querySelectorAll("tr")].find((item) =>
    item.querySelector(`[data-user-id="${userId}"]`),
  );
  const username = row?.querySelector("td")?.textContent || "用户";
  editingLimitUserId = userId;
  limitModalUser.textContent = `正在修改：${username}`;
  limitInput.value = "";
  limitModal.classList.remove("is-hidden");
  limitInput.focus();
}

function closeLimitModal() {
  editingLimitUserId = null;
  limitModal.classList.add("is-hidden");
  limitInput.value = "";
}

userTable.addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const userId = Number(button.dataset.userId);
  const action = button.dataset.action;

  try {
    if (action === "toggle") {
      const shouldDisable = button.textContent.trim() === "禁用";
      const confirmed = window.confirm(shouldDisable ? "确定禁用这个用户吗？" : "确定启用这个用户吗？");
      if (!confirmed) return;
      await updateUser(userId, { is_disabled: shouldDisable });
    }

    if (action === "limit") {
      openLimitModal(userId);
    }

    if (action === "clear-limit") {
      await clearLimit(userId);
    }

    if (action === "detail") {
      await showUserDetail(userId);
    }
  } catch (error) {
    window.alert(error.message);
  }
});

limitForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!editingLimitUserId) return;
  const parsed = Number(limitInput.value);
  if (!Number.isInteger(parsed) || parsed < 0) {
    limitInput.setCustomValidity("请输入大于等于 0 的整数。");
    limitInput.reportValidity();
    return;
  }
  limitInput.setCustomValidity("");
  try {
    await updateUser(editingLimitUserId, { daily_limit_override: parsed });
    closeLimitModal();
  } catch (error) {
    window.alert(error.message);
  }
});

limitCancelButton.addEventListener("click", closeLimitModal);

limitModal.addEventListener("click", (event) => {
  if (event.target === limitModal) {
    closeLimitModal();
  }
});

closeDetailButton.addEventListener("click", () => {
  detailPanel.classList.add("is-hidden");
});

refreshButton.addEventListener("click", () => {
  loadDashboard().catch((error) => {
    accessMessage.textContent = error.message;
  });
});

logoutButton.addEventListener("click", () => {
  localStorage.removeItem(TOKEN_KEY);
  token = null;
  currentUser = null;
  window.location.href = "/";
});

loginForm.addEventListener("submit", (event) => {
  event.preventDefault();
  window.location.href = "/";
});

async function boot() {
  accessTitle.textContent = "需要管理员权限";
  if (!token) {
    showAccess("请先回到聊天页，用管理员账号登录。");
    return;
  }
  try {
    currentUser = await api("/api/auth/me");
    if (!currentUser.is_admin) {
      showAccess("当前账号不是管理员，请切换管理员账号。");
      return;
    }
    showDashboard();
    await loadDashboard();
  } catch {
    showAccess("请先回到聊天页，用管理员账号登录。");
  }
}

boot();
