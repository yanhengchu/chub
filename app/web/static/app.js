"use strict";

const SESSION_TOKEN_KEY = "hub.sessionToken";
const LOCAL_TOKEN_KEY = "hub.savedToken";

const elements = {
  accessCard: document.querySelector("#access-card"),
  accessTitle: document.querySelector("#access-title"),
  accessBadge: document.querySelector("#access-badge"),
  tokenForm: document.querySelector("#token-form"),
  tokenInput: document.querySelector("#token-input"),
  rememberToken: document.querySelector("#remember-token"),
  connectSubmit: document.querySelector("#connect-submit"),
  connectedBar: document.querySelector("#connected-bar"),
  connectedNode: document.querySelector("#connected-node"),
  connectedMeta: document.querySelector("#connected-meta"),
  connectedSummary: document.querySelector("#connected-summary"),
  connectionBadge: document.querySelector("#connection-badge"),
  clearToken: document.querySelector("#clear-token"),
  globalMessage: document.querySelector("#global-message"),
  dashboard: document.querySelector("#dashboard"),
  refreshStatus: document.querySelector("#refresh-status"),
  taskList: document.querySelector("#task-list"),
  tasksMessage: document.querySelector("#tasks-message"),
  codexPanel: document.querySelector("#codex-panel"),
  codexWorkspaces: document.querySelector("#codex-workspaces"),
  codexMessage: document.querySelector("#codex-message"),
  codexSessions: document.querySelector("#codex-sessions"),
  refreshCodex: document.querySelector("#refresh-codex"),
  taskResultWrap: document.querySelector("#task-result-wrap"),
  taskResultTitle: document.querySelector("#task-result-title"),
  taskResultBadge: document.querySelector("#task-result-badge"),
  taskResultMessage: document.querySelector("#task-result-message"),
  taskResultFields: document.querySelector("#task-result-fields"),
  taskResultRawWrap: document.querySelector("#task-result-raw-wrap"),
  taskResult: document.querySelector("#task-result"),
  loadLogs: document.querySelector("#load-logs"),
  logsMessage: document.querySelector("#logs-message"),
  logsOutput: document.querySelector("#logs-output"),
};

let activeToken = "";
let taskRunning = false;
let accessVersion = 0;
let connectionAttempt = 0;

const TASK_TEXT = {
  show_version: ["版本信息", "查看 Hub、Python、节点和平台版本。"],
  check_system: ["系统检查", "检查 CPU、内存、磁盘和系统运行时长。"],
  check_codex: ["Codex 检查", "检查 Codex 是否可用并显示版本。"],
  check_docker: ["Docker 检查", "检查 Docker、Compose 和容器状态。"],
};

const STATUS_TEXT = {
  success: "成功",
  failed: "失败",
  timeout: "超时",
};

const MESSAGE_TEXT = {
  "Version information collected": "版本信息已获取",
  "System status collected": "系统状态已获取",
  "Codex is available": "Codex 可用",
  "Codex is not installed or not available on PATH": "未安装 Codex，或 Codex 不在 PATH 中",
  "Codex is not available": "Codex 不可用",
  "Codex cannot be executed": "Codex 无法执行",
  "Unable to read Codex version": "无法读取 Codex 版本",
  "Docker is available": "Docker 可用",
  "Docker CLI is not installed or not available on PATH": "未安装 Docker CLI，或 Docker 不在 PATH 中",
  "Docker CLI is not available": "Docker CLI 不可用",
  "Docker CLI cannot be executed": "Docker CLI 无法执行",
  "Unable to read Docker client version": "无法读取 Docker 客户端版本",
  "Docker service is not available": "Docker 服务不可用",
  "Docker returned an unexpected status": "Docker 返回了无法识别的状态",
  "Task execution timed out": "任务执行超时",
  "Task execution failed": "任务执行失败",
};

const RESULT_LABELS = {
  available: "可用",
  boot_time: "系统启动时间",
  cli_available: "CLI 可用",
  client_version: "客户端版本",
  compose_available: "Compose 可用",
  compose_version: "Compose 版本",
  containers: "容器",
  cpu_percent: "CPU 使用率",
  disk_percent: "磁盘使用率",
  disk_total_bytes: "磁盘总量",
  disk_used_bytes: "磁盘已用",
  hostname: "主机名",
  hub_version: "Hub 版本",
  memory_percent: "内存使用率",
  memory_total_bytes: "内存总量",
  memory_used_bytes: "内存已用",
  node_id: "节点 ID",
  operating_system: "操作系统",
  operating_system_version: "系统版本",
  path: "程序路径",
  platform: "平台",
  python_version: "Python 版本",
  server_available: "服务端可用",
  server_version: "服务端版本",
  uptime_seconds: "运行时长",
  version: "版本",
};

const BYTE_FIELDS = new Set([
  "memory_total_bytes",
  "memory_used_bytes",
  "disk_total_bytes",
  "disk_used_bytes",
]);

function taskText(task) {
  const text = TASK_TEXT[task.name];
  return text
    ? { title: text[0], description: text[1] }
    : { title: task.title, description: task.description };
}

function platformText(platform) {
  return {
    macos: "macOS",
    ubuntu: "Ubuntu",
    windows: "Windows",
    unknown: "未知平台",
  }[platform] || platform;
}

function messageText(message) {
  return MESSAGE_TEXT[message] || message;
}

function setMessage(target, message, kind = "") {
  target.textContent = message;
  target.className = "message";
  if (kind) {
    target.classList.add(`message-${kind}`);
  }
}

function setBadge(target, label, kind = "muted") {
  target.textContent = label;
  target.className = `badge badge-${kind}`;
}

function clearProtectedView() {
  elements.dashboard.hidden = true;
  elements.connectedBar.hidden = true;
  elements.taskList.replaceChildren();
  elements.taskResultWrap.hidden = true;
  elements.codexPanel.hidden = true;
  elements.codexWorkspaces.replaceChildren();
  elements.codexSessions.replaceChildren();
  elements.taskResultFields.replaceChildren();
  elements.taskResultRawWrap.hidden = true;
  elements.logsOutput.hidden = true;
  elements.logsOutput.textContent = "";
}

function showDisconnectedView(message = "输入启动 Hub 时配置的 Token。", kind = "") {
  elements.accessCard.hidden = false;
  elements.accessTitle.textContent = "连接此节点";
  elements.connectSubmit.textContent = "连接节点";
  elements.connectSubmit.disabled = false;
  setBadge(elements.accessBadge, "未连接");
  setMessage(elements.globalMessage, message, kind);
  elements.tokenInput.focus();
}

function showConnectedView(status) {
  elements.accessCard.hidden = true;
  elements.connectedBar.hidden = false;
  elements.dashboard.hidden = false;
  elements.connectedNode.textContent = status.node.name;
  elements.connectedMeta.textContent =
    `${platformText(status.node.detected_platform)} · ${status.system.hostname || "未知主机"}`;
  setBadge(elements.connectionBadge, "已连接", "success");
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

async function apiFetch(path, options = {}, token = activeToken) {
  if (!token) {
    throw { code: "authentication_required", message: "请先输入 Hub Token。" };
  }

  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        ...options.headers,
        Authorization: `Bearer ${token}`,
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
    activeToken = "";
    accessVersion += 1;
    clearProtectedView();
    showDisconnectedView("Token 无效或已变更，请重新输入。", "error");
    return true;
  }
  if (error.code === "security_not_configured") {
    removeStoredToken();
    activeToken = "";
    accessVersion += 1;
    clearProtectedView();
    showDisconnectedView(
      "Hub 尚未配置 HUB_TOKEN，请在服务端配置后重启。",
      "error",
    );
    setBadge(elements.accessBadge, "未配置认证", "timeout");
    return true;
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

function renderStatus(data) {
  elements.connectedSummary.textContent =
    `CPU ${data.system.cpu_percent.toFixed(1)}% · ` +
    `内存 ${data.system.memory_percent.toFixed(1)}% · ` +
    `磁盘 ${data.system.disk_percent.toFixed(1)}%`;
}

async function connectWithToken(token, remember, savedCredential = false) {
  const attempt = ++connectionAttempt;
  elements.connectSubmit.disabled = true;
  setBadge(elements.accessBadge, "验证中");
  setMessage(
    elements.globalMessage,
    savedCredential ? "正在验证已保存凭证…" : "正在验证 Token…",
  );

  try {
    const status = await apiFetch("/api/status", {}, token);
    if (attempt !== connectionAttempt) {
      return;
    }
    activeToken = token;
    accessVersion += 1;
    storeToken(token, remember);
    renderStatus(status);
    showConnectedView(status);
    await loadTasks();
    if (new URLSearchParams(window.location.search).get("view") === "codex") {
      showCodexPanel();
    }
  } catch (error) {
    if (attempt !== connectionAttempt) {
      return;
    }
    handleAccessError(error);
    if (error.code === "network_error") {
      showDisconnectedView(error.message, "error");
      setBadge(elements.accessBadge, "连接失败", "failed");
    }
  } finally {
    if (attempt === connectionAttempt) {
      elements.connectSubmit.disabled = false;
    }
  }
}

async function loadStatus() {
  const requestVersion = accessVersion;
  elements.refreshStatus.disabled = true;
  try {
    const data = await apiFetch("/api/status");
    if (requestVersion !== accessVersion) {
      return;
    }
    renderStatus(data);
    showConnectedView(data);
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return;
    }
    if (!handleAccessError(error)) {
      setBadge(elements.connectionBadge, "刷新失败", "failed");
    }
  } finally {
    elements.refreshStatus.disabled = false;
  }
}

function setTaskButtonsDisabled(disabled, activeButton = null) {
  elements.taskList.querySelectorAll("button").forEach((button) => {
    button.disabled = disabled;
    button.classList.toggle(
      "task-button-running",
      disabled && button === activeButton,
    );
    button.classList.toggle(
      "task-button-paused",
      disabled && button !== activeButton,
    );
  });
  if (activeButton) {
    activeButton.textContent = disabled ? "执行中…" : "执行";
  }
}

function renderTaskResult(task, data) {
  const display = taskText(task);
  elements.taskResultWrap.hidden = false;
  elements.taskResultTitle.textContent = display.title;
  elements.taskResultBadge.textContent = STATUS_TEXT[data.status] || data.status;
  elements.taskResultBadge.className = `badge badge-${data.status}`;
  elements.taskResultMessage.textContent =
    `${messageText(data.message)} · ${data.duration_ms} 毫秒`;
  renderResultFields(data.result);
  elements.taskResult.textContent = data.result
    ? JSON.stringify(data.result, null, 2)
    : "无附加结果";
  elements.taskResultRawWrap.hidden = !data.result;
  elements.taskResultRawWrap.open = false;
}

function resultValue(key, value) {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "boolean") {
    return value ? "是" : "否";
  }
  if (BYTE_FIELDS.has(key) && Number.isFinite(value)) {
    return formatBytes(value);
  }
  if (key === "uptime_seconds" && Number.isFinite(value)) {
    return formatUptime(value);
  }
  if (key.endsWith("_percent") && Number.isFinite(value)) {
    return `${value.toFixed(1)}%`;
  }
  if (key === "platform") {
    return platformText(value);
  }
  if (key === "containers" && typeof value === "object") {
    return [
      `总数：${value.total ?? "—"}`,
      `运行中：${value.running ?? "—"}`,
      `已暂停：${value.paused ?? "—"}`,
      `已停止：${value.stopped ?? "—"}`,
    ].join("\n");
  }
  if (typeof value === "object") {
    return JSON.stringify(value, null, 2);
  }
  return String(value);
}

function renderResultFields(result) {
  elements.taskResultFields.replaceChildren();
  if (!result) {
    elements.taskResultFields.hidden = true;
    return;
  }
  elements.taskResultFields.hidden = false;
  Object.entries(result).forEach(([key, value]) => {
    const row = document.createElement("div");
    const label = document.createElement("dt");
    const content = document.createElement("dd");
    row.className = "result-field";
    label.textContent = RESULT_LABELS[key] || key;
    content.textContent = resultValue(key, value);
    row.append(label, content);
    elements.taskResultFields.append(row);
  });
}

async function runTask(task, triggerButton) {
  if (taskRunning) {
    return;
  }
  taskRunning = true;
  const requestVersion = accessVersion;
  const display = taskText(task);
  elements.codexPanel.hidden = true;
  setTaskButtonsDisabled(true, triggerButton);
  elements.taskResultWrap.hidden = false;
  elements.taskResultTitle.textContent = display.title;
  elements.taskResultBadge.textContent = "执行中";
  elements.taskResultBadge.className = "badge badge-muted";
  elements.taskResultMessage.textContent = `${display.title}正在执行，请稍候…`;
  elements.taskResultFields.hidden = true;
  elements.taskResultRawWrap.hidden = true;
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
    if (!handleAccessError(error)) {
      elements.taskResultBadge.textContent = "请求失败";
      elements.taskResultBadge.className = "badge badge-failed";
      elements.taskResultMessage.textContent = error.message || "任务请求失败。";
      elements.taskResult.textContent = "";
    }
  } finally {
    taskRunning = false;
    setTaskButtonsDisabled(false, triggerButton);
  }
}

function renderTasks(tasks) {
  elements.taskList.replaceChildren();
  if (!tasks.length) {
    setMessage(elements.tasksMessage, "当前平台没有可用任务。");
    return;
  }

  tasks
    .filter((task) => !["show_version", "check_system"].includes(task.name))
    .forEach((task) => {
    const display = taskText(task);
    const item = document.createElement("article");
    const content = document.createElement("div");
    const title = document.createElement("h3");
    const description = document.createElement("p");
    const button = document.createElement("button");
    item.className = "task-item";
    title.textContent = display.title;
    description.textContent = display.description;
    button.type = "button";
    button.textContent = "执行";
    button.addEventListener("click", () => runTask(task, button));
    content.append(title, description);
    item.append(content, button);
    elements.taskList.append(item);
  });
  const codexItem = document.createElement("article");
  const codexContent = document.createElement("div");
  const codexTitle = document.createElement("h3");
  const codexDescription = document.createElement("p");
  const codexButton = document.createElement("button");
  codexItem.className = "task-item";
  codexTitle.textContent = "Codex PTY";
  codexDescription.textContent = "通过手机进入本机 Codex CLI 会话。";
  codexButton.type = "button";
  codexButton.textContent = "打开";
  codexButton.addEventListener("click", showCodexPanel);
  codexContent.append(codexTitle, codexDescription);
  codexItem.append(codexContent, codexButton);
  elements.taskList.append(codexItem);
  setMessage(elements.tasksMessage, "节点功能已加载。", "success");
}

function formatSessionTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间未知"
    : date.toLocaleString("zh-CN", { hour12: false });
}

function dependencyMessage(dependencies) {
  const missing = Object.entries(dependencies)
    .filter(([, available]) => !available)
    .map(([name]) => name);
  return missing.length ? `缺少依赖：${missing.join("、")}` : "";
}

async function enterCodexSession(sessionId, button) {
  button.disabled = true;
  setMessage(elements.codexMessage, "正在准备终端…");
  try {
    const data = await apiFetch(`/api/codex/sessions/${sessionId}/access`, {
      method: "POST",
    });
    window.location.assign(data.terminal_url);
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.codexMessage, error.message || "终端启动失败。", "error");
    }
    button.disabled = false;
  }
}

async function stopCodexSession(sessionId, button) {
  button.disabled = true;
  try {
    await apiFetch(`/api/codex/sessions/${sessionId}/stop`, { method: "POST" });
    await loadCodexSessions();
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.codexMessage, error.message || "停止会话失败。", "error");
    }
    button.disabled = false;
  }
}

async function archiveCodexSession(sessionId) {
  if (!window.confirm("归档此 Codex Session？归档后将从当前列表隐藏，但可以在 Codex 中取消归档。")) {
    return;
  }
  try {
    await apiFetch(`/api/codex/sessions/${sessionId}/archive`, { method: "POST" });
    await loadCodexSessions();
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.codexMessage, error.message || "归档会话失败。", "error");
    }
  }
}

async function removeCodexSession(sessionId) {
  if (!window.confirm("永久删除此 Codex Session 及其本地历史？此操作无法撤销。")) {
    return;
  }
  try {
    await apiFetch(`/api/codex/sessions/${sessionId}`, { method: "DELETE" });
    await loadCodexSessions();
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.codexMessage, error.message || "删除会话失败。", "error");
    }
  }
}

function renderCodexSessions(sessions) {
  elements.codexSessions.replaceChildren();
  if (!sessions.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "还没有 Session，请从上方选择目录新建。";
    elements.codexSessions.append(empty);
    return;
  }
  sessions.forEach((session) => {
    const item = document.createElement("article");
    const main = document.createElement("button");
    const text = document.createElement("span");
    const title = document.createElement("strong");
    const meta = document.createElement("span");
    const actions = document.createElement("div");
    const stop = document.createElement("button");
    const archive = document.createElement("button");
    const remove = document.createElement("button");
    item.className = "session-item";
    main.className = "session-enter";
    main.type = "button";
    title.textContent = session.title || session.workspace_name;
    title.title = title.textContent;
    const state =
      session.status !== "running"
        ? "可恢复"
        : session.activity === "working"
          ? "执行中"
          : session.activity === "idle"
            ? "等待输入"
            : "运行中";
    meta.textContent =
      `${state} · ` +
      `${formatSessionTime(session.updated_at)} · ${session.cwd}`;
    text.append(title, meta);
    main.append(text);
    main.addEventListener("click", () => enterCodexSession(session.id, main));
    stop.type = "button";
    stop.className = "button-secondary session-action";
    stop.textContent = "停止";
    stop.disabled = session.status !== "running";
    stop.addEventListener("click", () => stopCodexSession(session.id, stop));
    archive.type = "button";
    archive.className = "button-secondary session-action";
    archive.textContent = "归档";
    archive.disabled = !session.codex_session_id;
    archive.addEventListener("click", () => archiveCodexSession(session.id));
    remove.type = "button";
    remove.className = "button-link session-action";
    remove.textContent = "删除";
    remove.addEventListener("click", () => removeCodexSession(session.id));
    actions.className = "session-actions";
    actions.append(stop, archive, remove);
    item.append(main, actions);
    elements.codexSessions.append(item);
  });
}

async function createCodexSession(workspaceId, button) {
  button.disabled = true;
  setMessage(elements.codexMessage, "正在创建 Session…");
  try {
    const session = await apiFetch("/api/codex/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace_id: workspaceId }),
    });
    await enterCodexSession(session.id, button);
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.codexMessage, error.message || "创建 Session 失败。", "error");
    }
    button.disabled = false;
  }
}

function renderCodexWorkspaces(workspaces, available) {
  elements.codexWorkspaces.replaceChildren();
  workspaces.forEach((workspace) => {
    const button = document.createElement("button");
    const name = document.createElement("strong");
    const path = document.createElement("span");
    button.type = "button";
    button.className = "workspace-button";
    button.disabled = !available || !workspace.available;
    name.textContent = workspace.name;
    path.textContent = workspace.path;
    button.append(name, path);
    button.addEventListener(
      "click",
      () => createCodexSession(workspace.id, button),
    );
    elements.codexWorkspaces.append(button);
  });
}

async function loadCodexSessions() {
  setMessage(elements.codexMessage, "正在读取 Session…");
  try {
    const data = await apiFetch("/api/codex/sessions");
    renderCodexWorkspaces(data.workspaces, data.available);
    renderCodexSessions(data.sessions);
    const missing = dependencyMessage(data.dependencies);
    if (data.available) {
      setMessage(
        elements.codexMessage,
        `共 ${data.sessions.length} 个 Session。`,
        "success",
      );
    } else {
      setMessage(
        elements.codexMessage,
        missing || data.unavailable_reason || "Codex PTY 当前不可用。",
        "error",
      );
    }
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.codexMessage, error.message || "Session 读取失败。", "error");
    }
  }
}

function showCodexPanel() {
  elements.taskResultWrap.hidden = true;
  elements.codexPanel.hidden = false;
  window.history.replaceState(null, "", "/?view=codex");
  loadCodexSessions();
  elements.codexPanel.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadTasks() {
  const requestVersion = accessVersion;
  setMessage(elements.tasksMessage, "正在读取任务列表…");
  try {
    const data = await apiFetch("/api/tasks");
    if (requestVersion !== accessVersion) {
      return;
    }
    renderTasks(data.tasks);
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return;
    }
    if (!handleAccessError(error)) {
      setMessage(elements.tasksMessage, error.message || "任务列表读取失败。", "error");
    }
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
    if (!handleAccessError(error)) {
      elements.logsOutput.hidden = true;
      setMessage(elements.logsMessage, error.message || "日志读取失败。", "error");
    }
  } finally {
    elements.loadLogs.disabled = false;
  }
}

elements.tokenForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const token = elements.tokenInput.value.trim();
  if (!token) {
    setMessage(elements.globalMessage, "请输入 Hub Token。", "error");
    elements.tokenInput.focus();
    return;
  }
  elements.tokenInput.value = "";
  connectWithToken(token, elements.rememberToken.checked);
});

elements.clearToken.addEventListener("click", () => {
  if (!window.confirm("确定退出当前节点吗？此设备保存的 Hub Token 将被清除。")) {
    return;
  }
  connectionAttempt += 1;
  activeToken = "";
  accessVersion += 1;
  removeStoredToken();
  elements.tokenInput.value = "";
  elements.rememberToken.checked = false;
  clearProtectedView();
  showDisconnectedView("已退出，凭证已从此浏览器清除。", "success");
});

elements.refreshStatus.addEventListener("click", loadStatus);
elements.loadLogs.addEventListener("click", loadLogs);
elements.refreshCodex.addEventListener("click", loadCodexSessions);

const savedSessionToken = sessionStorage.getItem(SESSION_TOKEN_KEY);
const savedLocalToken = localStorage.getItem(LOCAL_TOKEN_KEY);
const savedToken = savedSessionToken || savedLocalToken || "";
if (savedToken) {
  elements.rememberToken.checked = Boolean(savedLocalToken);
  setBadge(elements.accessBadge, "自动连接");
  connectWithToken(savedToken, Boolean(savedLocalToken), true);
} else {
  showDisconnectedView();
}
