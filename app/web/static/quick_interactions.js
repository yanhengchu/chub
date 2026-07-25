"use strict";

const sessionId = document.body.dataset.sessionId;
const token = sessionStorage.getItem("hub.sessionToken")
  || localStorage.getItem("hub.savedToken")
  || "";
const message = document.querySelector("#quick-interaction-history-message");
const history = document.querySelector("#quick-interaction-history");
let pollTimer = null;

function showMessage(text, kind = "") {
  message.textContent = text;
  message.className = "message";
  if (kind) {
    message.classList.add(`message-${kind}`);
  }
}

function formatTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("zh-CN", { hour12: false });
}

function statusText(status) {
  return {
    requested: "等待执行",
    running: "执行中",
    succeeded: "已完成",
    failed: "执行失败",
    timed_out: "执行超时",
    needs_terminal: "需要实时终端",
  }[status] || status;
}

async function request(path) {
  if (!token) {
    throw new Error("请先返回首页连接节点。");
  }
  const response = await fetch(path, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${token}` },
  });
  const payload = await response.json();
  if (!response.ok || payload.success !== true) {
    throw new Error(payload?.error?.message || "读取失败。");
  }
  return payload.data;
}

function renderTasks(tasks) {
  history.replaceChildren();
  if (!tasks.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "暂无快速交互记录。";
    history.append(empty);
    return false;
  }
  let active = false;
  tasks.forEach((task) => {
    const item = document.createElement("article");
    const heading = document.createElement("div");
    const status = document.createElement("strong");
    const time = document.createElement("span");
    const promptLabel = document.createElement("span");
    const prompt = document.createElement("pre");
    item.className = `quick-interaction-history-item quick-interaction-${task.status}`;
    heading.className = "quick-interaction-history-heading";
    status.textContent = statusText(task.status);
    time.textContent = formatTime(task.updated_at);
    promptLabel.className = "quick-interaction-history-label";
    promptLabel.textContent = "提交内容";
    prompt.className = "quick-interaction-history-content";
    prompt.textContent = task.prompt || "历史任务未保存提交内容。";
    heading.append(status, time);
    item.append(heading, promptLabel, prompt);
    if (task.result || task.error) {
      const resultLabel = document.createElement("span");
      const result = document.createElement("pre");
      resultLabel.className = "quick-interaction-history-label";
      resultLabel.textContent = task.result ? "执行结果" : "失败原因";
      result.className = "quick-interaction-history-content";
      result.textContent = task.result || task.error;
      item.append(resultLabel, result);
    }
    active ||= ["requested", "running"].includes(task.status);
    history.append(item);
  });
  return active;
}

async function load() {
  window.clearTimeout(pollTimer);
  try {
    const tasks = await request(
      `/api/codex/sessions/${encodeURIComponent(sessionId)}/quick-interactions`,
    );
    const active = renderTasks(tasks.tasks);
    showMessage(active ? "任务仍在后台执行，可以离开此页面。" : "");
    if (active && document.visibilityState !== "hidden") {
      pollTimer = window.setTimeout(load, 1500);
    }
  } catch (error) {
    showMessage(error.message || "快速交互记录读取失败。", "error");
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    load();
  } else {
    window.clearTimeout(pollTimer);
  }
});
load();
