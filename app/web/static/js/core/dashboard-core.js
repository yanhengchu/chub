"use strict";

const elements = {
  connectedBar: document.querySelector("#connected-bar"),
  connectedNode: document.querySelector("#connected-node"),
  connectedMeta: document.querySelector("#connected-meta"),
  connectedSummary: document.querySelector("#connected-summary"),
  connectionBadge: document.querySelector("#connection-badge"),
  globalMessage: document.querySelector("#global-message"),
  dashboard: document.querySelector("#dashboard"),
  siteSettings: document.querySelector("#site-settings"),
  refreshCoreCapabilities: document.querySelector("#refresh-core-capabilities"),
  refreshThirdPartyServices: document.querySelector("#refresh-third-party-services"),
  restartHub: document.querySelector("#restart-hub"),
  chubServiceDetail: document.querySelector("#chub-service-detail"),
  chubServiceMessage: document.querySelector("#chub-service-message"),
  quickWorkerDetail: document.querySelector("#quick-worker-detail"),
  quickWorkerRestart: document.querySelector("#quick-worker-restart"),
  quickWorkerMessage: document.querySelector("#quick-worker-message"),
  aiRuntimeDetail: document.querySelector("#ai-runtime-detail"),
  systemUpgradeDetail: document.querySelector("#system-upgrade-detail"),
  systemUpgradeStart: document.querySelector("#system-upgrade-start"),
  codexCardHost: document.querySelector("#codex-card-host"),
  clawbotDetail: document.querySelector("#clawbot-detail"),
  openclawMessage: document.querySelector("#openclaw-message"),
  openclawStart: document.querySelector("#openclaw-start"),
  openclawRestart: document.querySelector("#openclaw-restart"),
  openclawStop: document.querySelector("#openclaw-stop"),
  openclawDialog: document.querySelector("#openclaw-dialog"),
  openclawDialogTitle: document.querySelector("#openclaw-dialog-title"),
  openclawDialogMessage: document.querySelector("#openclaw-dialog-message"),
  openclawDialogClose: document.querySelector("#openclaw-dialog-close"),
  openclawDialogCancel: document.querySelector("#openclaw-dialog-cancel"),
  openclawDialogConfirm: document.querySelector("#openclaw-dialog-confirm"),
  automationBrowserDetail: document.querySelector("#automation-browser-detail"),
  automationBrowserMessage: document.querySelector("#automation-browser-message"),
  automationBrowserControl: document.querySelector("#automation-browser-control"),
  automationBrowserRestart: document.querySelector("#automation-browser-restart"),
  automationBrowserDialog: document.querySelector("#automation-browser-dialog"),
  automationBrowserForm: document.querySelector("#automation-browser-form"),
  automationBrowserDialogClose: document.querySelector("#automation-browser-dialog-close"),
  automationBrowserDialogCancel: document.querySelector("#automation-browser-dialog-cancel"),
  automationBrowserDialogConfirm: document.querySelector("#automation-browser-dialog-confirm"),
  automationBrowserNotice: document.querySelector("#automation-browser-notice"),
  automationBrowserProfile: document.querySelector("#automation-browser-profile"),
  automationBrowserModeInputs: document.querySelectorAll('input[name="automation-browser-mode"]'),
  automationFeishuBadge: document.querySelector("#automation-feishu-badge"),
  automationFeishuDetail: document.querySelector("#automation-feishu-detail"),
  automationFeishuCheck: document.querySelector("#automation-feishu-check"),
  automationFeishuLogin: document.querySelector("#automation-feishu-login"),
  automationFeishuQr: document.querySelector("#automation-feishu-qr"),
  automationWeeklyReportTitle: document.querySelector("#automation-weekly-report-title"),
  automationWeeklyDownloadTitle: document.querySelector("#automation-weekly-download-title"),
  automationWeeklyDownloadStatus: document.querySelector("#automation-weekly-download-status"),
  automationWeeklyDownloadAction: document.querySelector("#automation-weekly-download-action"),
  automationWeeklyDownload: document.querySelector("#automation-weekly-download"),
  automationWeeklyDocumentsTitle: document.querySelector("#automation-weekly-documents-title"),
  automationWeeklyReportList: document.querySelector("#automation-weekly-report-list"),
  automationWeeklyReportMessage: document.querySelector("#automation-weekly-report-message"),
  automationCount: document.querySelector("#automation-count"),
  automationList: document.querySelector("#automation-list"),
  automationMessage: document.querySelector("#automation-message"),
  refreshAutomations: document.querySelector("#refresh-automations"),
  automationEnvironmentMessage: document.querySelector("#automation-environment-message"),
  projectDocsList: document.querySelector("#design-document-list"),
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
};

let connectionMethod = "";
let accessVersion = 0;
let connectionAttempt = 0;
let cardsRefreshAt = 0;
let hubRestartInProgress = false;
let maintenanceReloadTimer = 0;
const dashboardNavigationEntry = performance.getEntriesByType("navigation")[0];
const dashboardIsHistoryReturn = dashboardNavigationEntry?.type === "back_forward";

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

function reloadDashboardAfterMaintenance() {
  if (maintenanceReloadTimer) {
    return;
  }
  maintenanceReloadTimer = window.setTimeout(() => {
    window.location.reload();
  }, 2000);
}

function showMaintenanceCompletion(element, message) {
  element.textContent = `${message} 浏览器将在稍后自动刷新页面。`;
}

function clearProtectedView() {
  if (elements.automationBrowserDialog.open) {
    elements.automationBrowserDialog.close();
  }
  stopCodexPolling({ reset: true });
  codexLoadPromise = null;
  codexMutationCount = 0;
  window.ChubAiUsage?.clear();
  elements.dashboard.hidden = true;
  elements.connectedBar.hidden = true;
  elements.siteSettings.hidden = true;
  elements.automationList.replaceChildren();
  elements.codexCardHost.replaceChildren();
  clearOpenClawCache();
  resetOpenClawView();
  resetQuickWorkerView();
  if (automationPollTimer) {
    window.clearTimeout(automationPollTimer);
    automationPollTimer = null;
  }
  if (automationEnvironmentPollTimer) {
    window.clearTimeout(automationEnvironmentPollTimer);
    automationEnvironmentPollTimer = null;
  }
  elements.codexPanel = null;
  elements.codexWorkspaces = null;
  elements.codexSessions = null;
  elements.codexMessage = null;
  elements.codexQuota = null;
  elements.codexSessionCount = null;
  elements.refreshCodex = null;
  elements.createCodex = null;
  elements.codexWorkspaceDialog = null;
  releaseFeishuQr();
  sessionStorage.removeItem(CODEX_CARD_CACHE_KEY);
}

function showDisconnectedView(message, kind = "") {
  syncCoreMaintenanceControls();
  setMessage(elements.globalMessage, message, kind);
}

function showConnectedView(status) {
  setMessage(elements.globalMessage, "");
  elements.connectedBar.hidden = false;
  elements.dashboard.hidden = false;
  elements.siteSettings.hidden = false;
  syncCoreMaintenanceControls();
  elements.connectedNode.textContent = status.node.name;
  elements.connectedMeta.textContent =
    `${platformText(status.node.detected_platform)} · ${status.system.hostname || "未知主机"}`;
  setBadge(
    elements.connectionBadge,
    status.authentication_method === "tailscale" ? "Tailnet 已连接" : "本机已连接",
    "success",
  );
}

function hasProtectedAccess() {
  return Boolean(connectionMethod);
}

function authorizationHeaders() {
  return {};
}

function errorDetails(payload, fallback) {
  return {
    code: payload?.error?.code || "request_failed",
    message: payload?.error?.message || fallback,
    source: payload?.error?.source || null,
  };
}

function formatApiErrorMessage(error, fallback) {
  const message = error?.message || fallback;
  const label = error?.source === "runtime"
    ? "Codex CLI（上游 Runtime）"
    : error?.source === "chub"
      ? "Chub"
      : "";
  return label ? `${label}：${message}` : message;
}

async function apiFetch(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        ...options.headers,
        ...authorizationHeaders(),
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
  if (error.code === "trusted_network_required") {
    connectionMethod = "";
    accessVersion += 1;
    clearProtectedView();
    showDisconnectedView("当前来源不在本机或 Tailnet 内。", "error");
    return true;
  }
  return false;
}

async function connectToHub() {
  const attempt = ++connectionAttempt;
  setMessage(elements.globalMessage, "");

  try {
    const status = await apiFetch("/api/status", {}, "");
    if (attempt !== connectionAttempt) {
      return;
    }
    connectionMethod = status.authentication_method || "loopback";
    accessVersion += 1;
    ensureCodexCard();
    renderStatus(status);
    const openclawCacheRestored = restoreOpenClawCache(status.node.id);
    showConnectedView(status);
    const cardLoads = [
      loadCodexSessions(),
      loadAutomations(),
      loadAutomationEnvironment(),
      loadWeeklyReports(),
      loadQuickWorkerStatus(),
      loadSystemUpgradeStatus(),
    ];
    if (dashboardIsHistoryReturn && openclawCacheRestored) {
      cardLoads.push(loadOpenClawWeixinStatus());
    } else {
      cardLoads.push(loadOpenClaw());
    }
    await Promise.all(cardLoads);
  } catch (error) {
    if (attempt !== connectionAttempt) {
      return;
    }
    connectionMethod = "";
    if (error.code === "network_error") {
      showDisconnectedView(error.message, "error");
    } else if (!handleAccessError(error)) {
      showDisconnectedView(
        formatApiErrorMessage(error, "暂时无法读取节点状态，请稍后重试。"),
        "error",
      );
    }
  } finally {
    // No credential form is needed for trusted local or Tailnet access.
  }
}
