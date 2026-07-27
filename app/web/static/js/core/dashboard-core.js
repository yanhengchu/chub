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
  restartHub: document.querySelector("#restart-hub"),
  restartDialog: document.querySelector("#restart-dialog"),
  restartDialogClose: document.querySelector("#restart-dialog-close"),
  restartDialogCancel: document.querySelector("#restart-dialog-cancel"),
  restartDialogConfirm: document.querySelector("#restart-dialog-confirm"),
  codexCardHost: document.querySelector("#codex-card-host"),
  openclawBadge: document.querySelector("#openclaw-badge"),
  openclawVersion: document.querySelector("#openclaw-version"),
  openclawService: document.querySelector("#openclaw-service"),
  openclawBind: document.querySelector("#openclaw-bind"),
  openclawCheckedAt: document.querySelector("#openclaw-checked-at"),
  openclawAccessUrl: document.querySelector("#openclaw-access-url"),
  openclawAccessOpen: document.querySelector("#openclaw-access-open"),
  openclawAccessUnavailable: document.querySelector("#openclaw-access-unavailable"),
  openclawMessage: document.querySelector("#openclaw-message"),
  refreshOpenclaw: document.querySelector("#refresh-openclaw"),
  openclawStart: document.querySelector("#openclaw-start"),
  openclawRestart: document.querySelector("#openclaw-restart"),
  openclawStop: document.querySelector("#openclaw-stop"),
  openclawDialog: document.querySelector("#openclaw-dialog"),
  openclawDialogTitle: document.querySelector("#openclaw-dialog-title"),
  openclawDialogMessage: document.querySelector("#openclaw-dialog-message"),
  openclawDialogClose: document.querySelector("#openclaw-dialog-close"),
  openclawDialogCancel: document.querySelector("#openclaw-dialog-cancel"),
  openclawDialogConfirm: document.querySelector("#openclaw-dialog-confirm"),
  automationBrowserBadge: document.querySelector("#automation-browser-badge"),
  automationBrowserControl: document.querySelector("#automation-browser-control"),
  automationBrowserProfile: document.querySelector("#automation-browser-profile"),
  automationBrowserMode: document.querySelector("#automation-browser-mode"),
  automationFeishuBadge: document.querySelector("#automation-feishu-badge"),
  automationFeishuCheck: document.querySelector("#automation-feishu-check"),
  automationFeishuLogin: document.querySelector("#automation-feishu-login"),
  automationFeishuQr: document.querySelector("#automation-feishu-qr"),
  automationCount: document.querySelector("#automation-count"),
  automationList: document.querySelector("#automation-list"),
  automationMessage: document.querySelector("#automation-message"),
  refreshAutomations: document.querySelector("#refresh-automations"),
  projectDocsList: document.querySelector(".design-document-list-compact"),
  projectDocsCount: document.querySelector("#project-docs-count"),
  projectDocsMessage: document.querySelector("#project-docs-message"),
  refreshProjectDocs: document.querySelector("#refresh-project-docs"),
  codexPanel: null,
  codexWorkspaces: null,
  codexMessage: null,
  codexSessions: null,
  codexSessionCount: null,
  refreshCodex: null,
  createCodex: null,
  codexWorkspaceDialog: null,
  codexPermissionDialog: null,
  codexPermissionForm: null,
  codexPermissionCurrent: null,
  codexPermissionNotice: null,
  loadLogs: document.querySelector("#load-logs"),
  logLines: document.querySelector("#log-lines"),
  logTabs: document.querySelectorAll("[data-log-source]"),
  logsMessage: document.querySelector("#logs-message"),
  logsOutput: document.querySelector("#logs-output"),
};

let activeToken = "";
let accessVersion = 0;
let connectionAttempt = 0;
let cardsRefreshAt = 0;

function platformText(platform) {
  return {
    macos: "macOS",
    ubuntu: "Ubuntu",
    windows: "Windows",
    unknown: "未知平台",
  }[platform] || platform;
}

function sleep(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

async function hubInstanceId() {
  const response = await fetch("/api/health", { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok || payload.success !== true) {
    throw new Error("无法读取当前 Hub 实例状态。");
  }
  return payload.data.instance_id;
}

async function waitForHubRestart(previousInstanceId) {
  await sleep(1000);
  for (let attempt = 0; attempt < 30; attempt += 1) {
    try {
      const response = await fetch("/api/health", { cache: "no-store" });
      const payload = await response.json();
      if (
        response.ok
        && payload.success === true
        && payload.data.instance_id !== previousInstanceId
      ) {
        return;
      }
    } catch {
      // A temporary connection failure is expected while the service restarts.
    }
    await sleep(500);
  }
  throw new Error("重启后未能连接 Hub，请稍后刷新页面检查服务状态。");
}

async function refreshCardsAfterRestart() {
  await Promise.all([
    loadStatus(),
    loadCodexSessions(),
    loadOpenClaw(),
    loadAutomations(),
    loadLogs(),
  ]);
}

function clearProtectedView() {
  stopCodexPolling({ reset: true });
  codexLoadPromise = null;
  codexMutationCount = 0;
  elements.dashboard.hidden = true;
  elements.connectedBar.hidden = true;
  elements.automationList.replaceChildren();
  elements.codexCardHost.replaceChildren();
  clearOpenClawCache();
  resetOpenClawView();
  if (automationPollTimer) {
    window.clearTimeout(automationPollTimer);
    automationPollTimer = null;
  }
  elements.codexPanel = null;
  elements.codexWorkspaces = null;
  elements.codexSessions = null;
  elements.codexMessage = null;
  elements.codexSessionCount = null;
  elements.refreshCodex = null;
  elements.createCodex = null;
  elements.codexWorkspaceDialog = null;
  elements.logsOutput.hidden = true;
  elements.logsOutput.textContent = "";
  releaseFeishuQr();
  sessionStorage.removeItem(CODEX_CARD_CACHE_KEY);
}

function showDisconnectedView(message = "输入启动 Hub 时配置的 Token。", kind = "") {
  elements.accessCard.hidden = false;
  elements.restartHub.disabled = true;
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
  elements.restartHub.disabled = false;
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
    ensureCodexCard();
    renderStatus(status);
    restoreOpenClawCache(status.node.id);
    showConnectedView(status);
    await Promise.all([loadCodexSessions(), loadOpenClaw(), loadAutomations()]);
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
