"use strict";

const SESSION_TOKEN_KEY = "hub.sessionToken";
const LOCAL_TOKEN_KEY = "hub.savedToken";

const elements = {
  tokenForm: document.querySelector("#token-form"),
  tokenInput: document.querySelector("#token-input"),
  rememberToken: document.querySelector("#remember-token"),
  clearToken: document.querySelector("#clear-token"),
  connectionBadge: document.querySelector("#connection-badge"),
  globalMessage: document.querySelector("#global-message"),
  dashboard: document.querySelector("#dashboard"),
  refreshStatus: document.querySelector("#refresh-status"),
  statusContent: document.querySelector("#status-content"),
  statusMessage: document.querySelector("#status-message"),
  taskList: document.querySelector("#task-list"),
  tasksMessage: document.querySelector("#tasks-message"),
  taskResultWrap: document.querySelector("#task-result-wrap"),
  taskResultTitle: document.querySelector("#task-result-title"),
  taskResultBadge: document.querySelector("#task-result-badge"),
  taskResultMessage: document.querySelector("#task-result-message"),
  taskResult: document.querySelector("#task-result"),
  loadLogs: document.querySelector("#load-logs"),
  logsMessage: document.querySelector("#logs-message"),
  logsOutput: document.querySelector("#logs-output"),
};

let activeToken = "";
let taskRunning = false;
let accessVersion = 0;

function setMessage(target, message, kind = "") {
  target.textContent = message;
  target.className = "message";
  if (kind) {
    target.classList.add(`message-${kind}`);
  }
}

function setConnection(label, kind = "muted") {
  elements.connectionBadge.textContent = label;
  elements.connectionBadge.className = `badge badge-${kind}`;
}

function clearProtectedView() {
  elements.dashboard.hidden = true;
  elements.statusContent.replaceChildren();
  elements.taskList.replaceChildren();
  elements.taskResultWrap.hidden = true;
  elements.logsOutput.hidden = true;
  elements.logsOutput.textContent = "";
}

function storeToken(token, remember) {
  sessionStorage.removeItem(SESSION_TOKEN_KEY);
  localStorage.removeItem(LOCAL_TOKEN_KEY);
  if (remember) {
    localStorage.setItem(LOCAL_TOKEN_KEY, token);
  } else {
    sessionStorage.setItem(SESSION_TOKEN_KEY, token);
  }
}

function removeStoredToken() {
  sessionStorage.removeItem(SESSION_TOKEN_KEY);
  localStorage.removeItem(LOCAL_TOKEN_KEY);
}

function errorDetails(payload, fallback) {
  return {
    code: payload?.error?.code || "request_failed",
    message: payload?.error?.message || fallback,
  };
}

async function apiFetch(path, options = {}) {
  if (!activeToken) {
    throw { code: "authentication_required", message: "请先输入 Hub Token。" };
  }

  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        ...options.headers,
        Authorization: `Bearer ${activeToken}`,
      },
    });
  } catch {
    throw { code: "network_error", message: "无法连接 Hub，请检查服务和网络。" };
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw { code: "invalid_response", message: "Hub 返回了无法识别的响应。" };
  }

  if (!response.ok || payload.success !== true) {
    const detail = errorDetails(payload, `请求失败（HTTP ${response.status}）`);
    if (response.status === 401) {
      detail.code = "invalid_credentials";
    }
    throw detail;
  }
  return payload.data;
}

function handleAccessError(error) {
  if (error.code === "invalid_credentials" || error.code === "authentication_required") {
    removeStoredToken();
    setConnection("认证失败", "failed");
    setMessage(elements.globalMessage, "Token 无效或已变更，请重新输入。", "error");
    clearProtectedView();
    return true;
  }
  if (error.code === "security_not_configured") {
    setConnection("未配置认证", "timeout");
    setMessage(elements.globalMessage, "Hub 尚未配置 HUB_TOKEN，请在服务端配置后重启。", "error");
    clearProtectedView();
    return true;
  }
  if (error.code === "network_error") {
    setConnection("连接失败", "failed");
    setMessage(elements.globalMessage, error.message, "error");
  }
  return false;
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "—";
  }
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function formatUptime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) {
    return "—";
  }
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  return [days ? `${days}天` : "", hours ? `${hours}小时` : "", `${minutes}分钟`]
    .filter(Boolean)
    .join(" ");
}

function addMetric(label, value) {
  const item = document.createElement("div");
  const labelNode = document.createElement("span");
  const valueNode = document.createElement("span");
  item.className = "metric";
  labelNode.className = "metric-label";
  valueNode.className = "metric-value";
  labelNode.textContent = label;
  valueNode.textContent = value;
  item.append(labelNode, valueNode);
  elements.statusContent.append(item);
}

function renderStatus(data) {
  elements.statusContent.replaceChildren();
  addMetric("节点", data.node.name);
  addMetric("平台", data.node.detected_platform);
  addMetric("主机", data.system.hostname || "—");
  addMetric("Hub 版本", data.hub.version);
  addMetric("CPU", `${data.system.cpu_percent.toFixed(1)}%`);
  addMetric(
    "内存",
    `${data.system.memory_percent.toFixed(1)}% · ${formatBytes(data.system.memory_used_bytes)}`,
  );
  addMetric(
    "磁盘",
    `${data.system.disk_percent.toFixed(1)}% · ${formatBytes(data.system.disk_used_bytes)}`,
  );
  addMetric("运行时长", formatUptime(data.system.uptime_seconds));
}

async function loadStatus() {
  const requestVersion = accessVersion;
  setMessage(elements.statusMessage, "正在读取节点状态…");
  try {
    const data = await apiFetch("/api/status");
    if (requestVersion !== accessVersion) {
      return false;
    }
    renderStatus(data);
    setMessage(elements.statusMessage, "状态已更新。", "success");
    return true;
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return false;
    }
    handleAccessError(error);
    setMessage(elements.statusMessage, error.message || "状态读取失败。", "error");
    return false;
  }
}

function setTaskButtonsDisabled(disabled) {
  elements.taskList.querySelectorAll("button").forEach((button) => {
    button.disabled = disabled;
  });
}

function renderTaskResult(task, data) {
  elements.taskResultWrap.hidden = false;
  elements.taskResultTitle.textContent = task.title;
  elements.taskResultBadge.textContent = data.status;
  elements.taskResultBadge.className = `badge badge-${data.status}`;
  elements.taskResultMessage.textContent = `${data.message} · ${data.duration_ms} ms`;
  elements.taskResult.textContent = data.result
    ? JSON.stringify(data.result, null, 2)
    : "无附加结果";
}

async function runTask(task) {
  if (taskRunning) {
    return;
  }
  taskRunning = true;
  const requestVersion = accessVersion;
  setTaskButtonsDisabled(true);
  elements.taskResultWrap.hidden = false;
  elements.taskResultTitle.textContent = task.title;
  elements.taskResultBadge.textContent = "执行中";
  elements.taskResultBadge.className = "badge badge-muted";
  elements.taskResultMessage.textContent = "任务正在执行，请稍候…";
  elements.taskResult.textContent = "";

  try {
    const data = await apiFetch("/api/tasks/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task: task.name, params: {} }),
    });
    if (requestVersion !== accessVersion) {
      return;
    }
    renderTaskResult(task, data);
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return;
    }
    handleAccessError(error);
    elements.taskResultBadge.textContent = "请求失败";
    elements.taskResultBadge.className = "badge badge-failed";
    elements.taskResultMessage.textContent = error.message || "任务请求失败。";
    elements.taskResult.textContent = "";
  } finally {
    taskRunning = false;
    setTaskButtonsDisabled(false);
  }
}

function renderTasks(tasks) {
  elements.taskList.replaceChildren();
  if (!tasks.length) {
    setMessage(elements.tasksMessage, "当前平台没有可用任务。");
    return;
  }

  tasks.forEach((task) => {
    const item = document.createElement("article");
    const content = document.createElement("div");
    const title = document.createElement("h3");
    const description = document.createElement("p");
    const button = document.createElement("button");
    item.className = "task-item";
    title.textContent = task.title;
    description.textContent = task.description;
    button.type = "button";
    button.textContent = "执行";
    button.addEventListener("click", () => runTask(task));
    content.append(title, description);
    item.append(content, button);
    elements.taskList.append(item);
  });
  setMessage(elements.tasksMessage, `共 ${tasks.length} 个可用任务。`, "success");
}

async function loadTasks() {
  const requestVersion = accessVersion;
  setMessage(elements.tasksMessage, "正在读取任务列表…");
  try {
    const data = await apiFetch("/api/tasks");
    if (requestVersion !== accessVersion) {
      return false;
    }
    renderTasks(data.tasks);
    return true;
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return false;
    }
    handleAccessError(error);
    setMessage(elements.tasksMessage, error.message || "任务列表读取失败。", "error");
    return false;
  }
}

async function loadLogs() {
  const requestVersion = accessVersion;
  elements.loadLogs.disabled = true;
  setMessage(elements.logsMessage, "正在读取日志…");
  try {
    const data = await apiFetch("/api/logs?lines=50");
    if (requestVersion !== accessVersion) {
      return;
    }
    elements.logsOutput.textContent = data.lines.length
      ? data.lines.join("\n")
      : "当前日志为空。";
    elements.logsOutput.hidden = false;
    setMessage(elements.logsMessage, `已读取 ${data.count} 行日志。`, "success");
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return;
    }
    handleAccessError(error);
    elements.logsOutput.hidden = true;
    setMessage(elements.logsMessage, error.message || "日志读取失败。", "error");
  } finally {
    elements.loadLogs.disabled = false;
  }
}

async function loadDashboard(rememberAfterValidation = null) {
  const requestVersion = accessVersion;
  elements.dashboard.hidden = false;
  setConnection("连接中", "muted");
  const results = await Promise.all([loadStatus(), loadTasks()]);
  if (requestVersion === accessVersion && results.some(Boolean)) {
    if (rememberAfterValidation !== null) {
      storeToken(activeToken, rememberAfterValidation);
    }
    setConnection("已连接", "success");
    setMessage(elements.globalMessage, "已连接到 Hub。", "success");
  }
}

elements.tokenForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const token = elements.tokenInput.value.trim();
  if (!token) {
    setMessage(elements.globalMessage, "请输入 Hub Token。", "error");
    return;
  }
  activeToken = token;
  accessVersion += 1;
  removeStoredToken();
  elements.tokenInput.value = "";
  loadDashboard(elements.rememberToken.checked);
});

elements.clearToken.addEventListener("click", () => {
  activeToken = "";
  accessVersion += 1;
  removeStoredToken();
  elements.tokenInput.value = "";
  elements.rememberToken.checked = false;
  clearProtectedView();
  setConnection("未连接", "muted");
  setMessage(elements.globalMessage, "凭证已从此浏览器清除。", "success");
});

elements.refreshStatus.addEventListener("click", loadStatus);
elements.loadLogs.addEventListener("click", loadLogs);

const savedToken = sessionStorage.getItem(SESSION_TOKEN_KEY)
  || localStorage.getItem(LOCAL_TOKEN_KEY)
  || "";
if (savedToken) {
  activeToken = savedToken;
  accessVersion += 1;
  elements.rememberToken.checked = Boolean(localStorage.getItem(LOCAL_TOKEN_KEY));
  loadDashboard();
}
