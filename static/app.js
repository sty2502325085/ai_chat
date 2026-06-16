const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const messagesEl = document.querySelector("#messages");
const sendButton = document.querySelector("#send-button");
const statusEl = document.querySelector("#status");

const messages = [];

function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
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

async function sendMessage(content) {
  sendButton.disabled = true;
  input.disabled = true;
  setStatus("AI 思考中...");

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ messages }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "请求失败，请稍后重试。");
    }

    messages.push({ role: "assistant", content: data.reply });
    addMessage("assistant", data.reply);
    setStatus("本地运行中");
  } catch (error) {
    addMessage("assistant", `出错了：${error.message}`);
    setStatus("请求失败", true);
  } finally {
    sendButton.disabled = false;
    input.disabled = false;
    input.focus();
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const content = input.value.trim();
  if (!content) return;

  messages.push({ role: "user", content });
  addMessage("user", content);
  input.value = "";
  sendMessage(content);
});

input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    form.requestSubmit();
  }
});
