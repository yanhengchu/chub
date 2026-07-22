"use strict";

const token = sessionStorage.getItem("hub.sessionToken") || localStorage.getItem("hub.savedToken") || "";
const list = document.querySelector("#detail-automation-list");
const message = document.querySelector("#detail-automation-message");
const badge = document.querySelector("#detail-browser-badge");
const count = document.querySelector("#detail-automation-count");
const refresh = document.querySelector("#refresh-automations");
let pollTimer = null;

function showMessage(text, kind = "") {
  message.textContent = text;
  message.className = "message";
  if (kind) {
    message.classList.add(`message-${kind}`);
  }
}

async function request(path, options = {}) {
  if (!token) {
    throw new Error("请先返回首页连接节点。");
  }
  const response = await fetch(path, {
    ...options,
    headers: { ...options.headers, Authorization: `Bearer ${token}` },
  });
  const payload = await response.json();
  if (!response.ok || payload.success !== true) {
    throw new Error(payload?.error?.message || "请求失败。");
  }
  return payload.data;
}

function stateText(value) {
  return { idle: "尚未执行", queued: "等待执行", running: "执行中", success: "成功", failed: "失败" }[value] || value;
}

function stateTime(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("zh-CN", { hour12: false });
}

async function run(task, button) {
  button.disabled = true;
  button.textContent = "受理中…";
  try {
    await request(`/api/automations/${encodeURIComponent(task.id)}/run`, { method: "POST" });
    showMessage("");
    await load();
  } catch (error) {
    showMessage(error.message, "error");
    button.disabled = false;
    button.textContent = "运行";
  }
}

function render(data) {
  list.replaceChildren();
  const running = data.browser_state === "running";
  const environmentChecking = data.feishu_environment.state === "checking";
  badge.textContent = `${data.browser_message}${data.browser_mode ? ` · ${data.browser_mode}` : ""}`;
  badge.className = `badge badge-${running ? "success" : data.browser_state === "stopped" ? "timeout" : "failed"}`;
  count.textContent = `共 ${data.tasks.length} 个任务 · 已启用 ${data.enabled_count} 个`;
  let active = false;
  data.tasks.forEach((task) => {
    const item = document.createElement("article");
    const copy = document.createElement("div");
    const title = document.createElement("strong");
    const status = document.createElement("span");
    const reason = document.createElement("span");
    const button = document.createElement("button");
    const busy = ["queued", "running"].includes(task.state.status);
    active = active || busy;
    item.className = "automation-item";
    copy.className = "automation-item-copy";
    title.textContent = task.name;
    const time = stateTime(task.state.finished_at || task.state.started_at);
    status.className = "automation-item-status";
    status.textContent = `${stateText(task.state.status)}${time ? ` · ${time}` : ""}`;
    reason.className = "automation-item-reason";
    reason.textContent = task.state.message || "暂无状态说明";
    button.type = "button";
    button.className = "button-secondary automation-run";
    button.textContent = busy ? "执行中…" : "运行";
    button.disabled = !running || !task.enabled || busy || environmentChecking;
    button.addEventListener("click", () => run(task, button));
    copy.append(title, status, reason);
    item.append(copy, button);
    list.append(item);
  });
  if (!data.tasks.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "暂无自动化任务。";
    list.append(empty);
  }
  return active;
}

async function load() {
  refresh.disabled = true;
  try {
    const data = await request("/api/automations?all_tasks=true");
    showMessage("");
    const active = render(data);
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
    if (active) {
      pollTimer = window.setTimeout(load, 1000);
    }
  } catch (error) {
    badge.textContent = "检查失败";
    badge.className = "badge badge-failed";
    showMessage(error.message, "error");
  } finally {
    refresh.disabled = false;
  }
}

refresh.addEventListener("click", load);
load();
