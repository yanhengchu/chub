"use strict";

const SESSION_TOKEN_KEY = "hub.sessionToken";
const LOCAL_TOKEN_KEY = "hub.savedToken";
const CODEX_CARD_CACHE_KEY = "hub.codexCardCache";
const CARD_COLLAPSED_STATE_KEY = "hub.cardCollapsedState.v1";
const CODEX_PERMISSION_OPTIONS = [
  ["ask", "Ask for approval", "在当前工作区操作，越界时由你确认。"],
  ["auto-review", "Approve for me", "保持工作区边界，由 Codex 自动审核越界请求。"],
  ["full-access", "Full access", "不受工作区沙箱限制，且不会请求操作审批。"],
  ["read-only", "Read Only", "只能查看和分析，不能修改文件。"],
];
const CARD_RETURN_REFRESHERS = {
  codex: () => loadCodexSessions(),
  "project-docs": () => loadProjectDocuments(),
};

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
let activeLogSource = "operations";
let automationPollTimer = null;
let automationBrowserState = "unavailable";
let automationBrowserProfiles = [];
let feishuQrObjectUrl = "";
let feishuQrLoading = false;
let feishuQrVersion = 0;
let codexPollTimer = null;
let codexPollUnchangedSince = 0;
let codexShouldPoll = false;
let codexSessionSignature = "";
let codexLoadPromise = null;
let codexMutationCount = 0;
let cardsRefreshAt = 0;
let cardCollapsedState = {};

const CODEX_POLL_FAST_MS = 2000;
const CODEX_POLL_SLOW_MS = 8000;
const CODEX_POLL_SLOW_AFTER_MS = 2 * 60 * 1000;

function platformText(platform) {
  return {
    macos: "macOS",
    ubuntu: "Ubuntu",
    windows: "Windows",
    unknown: "未知平台",
  }[platform] || platform;
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

function loadCardCollapsedState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(CARD_COLLAPSED_STATE_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).filter(([, value]) => typeof value === "boolean"),
    );
  } catch (_error) {
    return {};
  }
}

function saveCardCollapsedState() {
  try {
    localStorage.setItem(
      CARD_COLLAPSED_STATE_KEY,
      JSON.stringify(cardCollapsedState),
    );
  } catch (_error) {
    // Storage can be unavailable in private browsing; keep the in-memory state.
  }
}

function setupCollapsibleCard(card) {
  if (!card || card.dataset.collapsibleReady === "1") {
    return;
  }
  const heading = card.querySelector(".section-heading");
  const headingCopy = card.querySelector("[data-card-heading]");
  const content = card.querySelector("[data-card-content]");
  if (!heading || !headingCopy || !content) {
    return;
  }
  card.dataset.collapsibleReady = "1";
  card.dataset.collapsibleCard = "";
  const cardKey = card.dataset.cardKey;
  if (!cardKey) {
    return;
  }
  const initiallyCollapsed = typeof cardCollapsedState[cardKey] === "boolean"
    ? cardCollapsedState[cardKey]
    : card.dataset.collapsed === "true";
  headingCopy.setAttribute("role", "button");
  headingCopy.tabIndex = 0;
  headingCopy.setAttribute("aria-expanded", String(!initiallyCollapsed));
  card.classList.toggle("is-collapsed", initiallyCollapsed);
  content.hidden = initiallyCollapsed;

  const setCollapsed = (collapsed) => {
    card.classList.toggle("is-collapsed", collapsed);
    headingCopy.setAttribute("aria-expanded", String(!collapsed));
    content.hidden = collapsed;
    cardCollapsedState[cardKey] = collapsed;
    saveCardCollapsedState();
  };
  const isInteractiveTarget = (target) => Boolean(
    target.closest("button, a, input, select, textarea, summary"),
  );

  heading.addEventListener("click", (event) => {
    if (isInteractiveTarget(event.target)) {
      event.stopPropagation();
      return;
    }
    event.stopPropagation();
    setCollapsed(!card.classList.contains("is-collapsed"));
  });
  heading.addEventListener("pointerdown", (event) => {
    if (isInteractiveTarget(event.target)) {
      event.stopPropagation();
    }
  });
  headingCopy.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault();
    setCollapsed(!card.classList.contains("is-collapsed"));
  });
  card.addEventListener("click", (event) => {
    if (!card.classList.contains("is-collapsed") || isInteractiveTarget(event.target)) {
      return;
    }
    setCollapsed(false);
  });
}

function setupCollapsibleCards() {
  document.querySelectorAll("[data-collapsible-card]")
    .forEach(setupCollapsibleCard);
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
    ensureCodexCard();
    renderStatus(status);
    showConnectedView(status);
    await Promise.all([loadCodexSessions(), loadAutomations()]);
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

function createCodexCard() {
  const card = document.createElement("article");
  const header = document.createElement("div");
  const kicker = document.createElement("p");
  const title = document.createElement("h2");
  const description = document.createElement("p");
  const cardContent = document.createElement("div");
  const panel = document.createElement("div");
  const currentHint = document.createElement("p");
  const sessionsDivider = document.createElement("div");
  const sessionsDividerLabel = document.createElement("span");
  const refreshButton = document.createElement("button");
  const createActions = document.createElement("div");
  const createButton = document.createElement("button");
  const workspaceDialog = document.createElement("dialog");
  const workspaceDialogSurface = document.createElement("div");
  const workspaceDialogHeader = document.createElement("div");
  const workspaceDialogTitle = document.createElement("h3");
  const workspaceDialogClose = document.createElement("button");
  const workspaceDialogDescription = document.createElement("p");
  const workspaceList = document.createElement("div");
  const sessionList = document.createElement("div");
  const permissionDialog = document.createElement("dialog");
  const permissionSurface = document.createElement("div");
  const permissionHeader = document.createElement("div");
  const permissionTitle = document.createElement("h3");
  const permissionClose = document.createElement("button");
  const permissionCurrent = document.createElement("p");
  const permissionForm = document.createElement("form");
  const permissionOptions = document.createElement("div");
  const permissionNotice = document.createElement("p");
  const permissionFooter = document.createElement("div");
  const permissionCancel = document.createElement("button");
  const permissionSave = document.createElement("button");

  card.className = "card codex-card";
  card.dataset.cardKey = "codex";
  card.dataset.cardReturnRefresh = "true";
  card.dataset.collapsibleCard = "";
  card.setAttribute("aria-labelledby", "codex-title");
  header.className = "section-heading codex-card-heading";
  panel.className = "codex-panel";
  panel.id = "codex-panel";
  panel.hidden = false;
  cardContent.className = "card-content";
  cardContent.dataset.cardContent = "";
  kicker.className = "section-kicker";
  kicker.textContent = "远程开发";
  title.id = "codex-title";
  title.textContent = "Codex PTY";
  description.className = "section-description";
  description.textContent = "远程管理本机 Codex 会话。";
  refreshButton.type = "button";
  refreshButton.id = "refresh-codex";
  refreshButton.className = "button-secondary";
  refreshButton.textContent = "刷新";
  currentHint.textContent = "";
  currentHint.className = "message";
  currentHint.id = "codex-message";
  currentHint.setAttribute("aria-live", "polite");
  sessionsDivider.className = "codex-divider codex-sessions-divider";
  sessionsDividerLabel.textContent = "正在读取会话";
  createActions.className = "codex-create-actions";
  createButton.type = "button";
  createButton.id = "create-codex";
  createButton.className = "button-secondary";
  createButton.textContent = "新建会话";
  createButton.disabled = true;
  workspaceDialog.className = "codex-workspace-dialog";
  workspaceDialog.setAttribute("aria-labelledby", "codex-workspace-dialog-title");
  workspaceDialogSurface.className = "codex-workspace-dialog-surface";
  workspaceDialogHeader.className = "codex-workspace-dialog-header";
  workspaceDialogTitle.id = "codex-workspace-dialog-title";
  workspaceDialogTitle.textContent = "选择工作目录";
  workspaceDialogClose.type = "button";
  workspaceDialogClose.className = "button-link codex-workspace-dialog-close";
  workspaceDialogClose.setAttribute("aria-label", "关闭目录选择");
  workspaceDialogClose.textContent = "关闭";
  workspaceDialogDescription.className = "section-description";
  workspaceDialogDescription.textContent = "选择一个固定目录新建 Codex 会话。";
  workspaceList.className = "workspace-list";
  workspaceList.id = "codex-workspaces";
  sessionList.className = "session-list";
  sessionList.id = "codex-sessions";
  permissionDialog.className = "codex-workspace-dialog codex-permission-dialog";
  permissionDialog.setAttribute("aria-labelledby", "codex-permission-dialog-title");
  permissionSurface.className = "codex-workspace-dialog-surface";
  permissionHeader.className = "codex-workspace-dialog-header";
  permissionTitle.id = "codex-permission-dialog-title";
  permissionTitle.textContent = "会话权限";
  permissionClose.type = "button";
  permissionClose.className = "button-link codex-workspace-dialog-close";
  permissionClose.setAttribute("aria-label", "关闭权限设置");
  permissionClose.textContent = "关闭";
  permissionCurrent.className = "codex-permission-current";
  permissionForm.className = "codex-permission-form";
  permissionOptions.className = "codex-permission-options";
  CODEX_PERMISSION_OPTIONS.forEach(([value, label, description]) => {
    const option = document.createElement("label");
    const radio = document.createElement("input");
    const copy = document.createElement("span");
    const name = document.createElement("strong");
    const detail = document.createElement("span");
    option.className = "codex-permission-option";
    radio.type = "radio";
    radio.name = "permission_mode";
    radio.value = value;
    name.textContent = label;
    detail.textContent = description;
    copy.append(name, detail);
    option.append(radio, copy);
    permissionOptions.append(option);
  });
  permissionNotice.className = "codex-permission-notice";
  permissionFooter.className = "codex-permission-footer";
  permissionCancel.type = "button";
  permissionCancel.className = "button-link";
  permissionCancel.textContent = "取消";
  permissionSave.type = "submit";
  permissionSave.className = "button-secondary";
  permissionSave.textContent = "保存";

  header.append(
    (() => {
      const copy = document.createElement("div");
      copy.className = "card-heading-copy";
      copy.dataset.cardHeading = "";
      copy.append(kicker, title, description);
      return copy;
    })(),
    refreshButton,
  );
  sessionsDivider.append(sessionsDividerLabel);
  createActions.append(createButton);
  workspaceDialogHeader.append(workspaceDialogTitle, workspaceDialogClose);
  workspaceDialogSurface.append(
    workspaceDialogHeader,
    workspaceDialogDescription,
    workspaceList,
  );
  workspaceDialog.append(workspaceDialogSurface);
  permissionHeader.append(permissionTitle, permissionClose);
  permissionFooter.append(permissionCancel, permissionSave);
  permissionForm.append(permissionOptions, permissionNotice, permissionFooter);
  permissionSurface.append(permissionHeader, permissionCurrent, permissionForm);
  permissionDialog.append(permissionSurface);
  panel.append(
    currentHint,
    sessionsDivider,
    sessionList,
    createActions,
  );
  cardContent.append(panel);
  card.append(header, cardContent, workspaceDialog, permissionDialog);
  setupCollapsibleCard(card);

  elements.codexPanel = panel;
  elements.codexWorkspaces = workspaceList;
  elements.codexMessage = currentHint;
  elements.codexSessions = sessionList;
  elements.codexSessionCount = sessionsDividerLabel;
  elements.refreshCodex = refreshButton;
  elements.createCodex = createButton;
  elements.codexWorkspaceDialog = workspaceDialog;
  elements.codexPermissionDialog = permissionDialog;
  elements.codexPermissionForm = permissionForm;
  elements.codexPermissionCurrent = permissionCurrent;
  elements.codexPermissionNotice = permissionNotice;

  refreshButton.addEventListener("click", loadCodexSessions);
  createButton.addEventListener("click", () => {
    if (!workspaceDialog.open) {
      workspaceDialog.showModal();
    }
  });
  workspaceDialogClose.addEventListener("click", () => workspaceDialog.close());
  workspaceDialog.addEventListener("click", (event) => {
    if (event.target === workspaceDialog) {
      workspaceDialog.close();
    }
  });
  permissionClose.addEventListener("click", () => permissionDialog.close());
  permissionCancel.addEventListener("click", () => permissionDialog.close());
  permissionDialog.addEventListener("click", (event) => {
    if (event.target === permissionDialog) {
      permissionDialog.close();
    }
  });
  permissionForm.addEventListener("submit", saveCodexSessionPermission);
  return card;
}

function ensureCodexCard() {
  if (elements.codexPanel) {
    return;
  }
  elements.codexCardHost.replaceChildren(createCodexCard());
}

function renderCodexWorkspaces(workspaces, available) {
  if (!elements.codexWorkspaces) {
    return;
  }

  elements.codexWorkspaces.replaceChildren();
  let hasAvailableWorkspace = false;
  workspaces.forEach((workspace) => {
    const button = document.createElement("button");
    const name = document.createElement("strong");
    const path = document.createElement("span");
    button.type = "button";
    button.className = "workspace-button";
    button.disabled = !available || !workspace.available;
    hasAvailableWorkspace ||= !button.disabled;
    name.textContent = workspace.name;
    path.textContent = workspace.path;
    button.append(name, path);
    button.addEventListener(
      "click",
      () => createCodexSession(workspace.id, button),
    );
    elements.codexWorkspaces.append(button);
  });
  if (elements.createCodex) {
    elements.createCodex.disabled = !available || !hasAvailableWorkspace;
  }
}

async function openQuickInteractionDialog(session) {
  const dialog = document.createElement("dialog");
  const surface = document.createElement("div");
  const heading = document.createElement("div");
  const title = document.createElement("h3");
  const form = document.createElement("form");
  const prompt = document.createElement("textarea");
  const unknownNotice = document.createElement("p");
  const message = document.createElement("p");
  const submit = document.createElement("button");
  dialog.className = "codex-workspace-dialog quick-interaction-dialog";
  surface.className = "codex-workspace-dialog-surface";
  heading.className = "codex-workspace-dialog-header";
  title.textContent = "快速交互";
  form.id = `quick-interaction-form-${session.id}`;
  prompt.rows = 6;
  prompt.required = true;
  prompt.maxLength = 8000;
  prompt.placeholder = "输入要交给 Codex 执行的需求…";
  prompt.setAttribute("aria-label", "快速交互需求");
  let confirmStopUnknownTerminal =
    session.status === "running" && session.activity === "unknown";
  unknownNotice.className = "quick-interaction-warning";
  unknownNotice.textContent =
    "点击执行会先停止当前实时会话，可能中断正在执行的任务，然后启动新任务。";
  unknownNotice.hidden = !confirmStopUnknownTerminal;
  message.className = "message";
  message.setAttribute("aria-live", "polite");
  submit.type = "submit";
  submit.className = "button-secondary quick-interaction-submit";
  submit.textContent = "执行";
  submit.setAttribute("form", form.id);
  heading.append(title, submit);
  form.append(prompt, unknownNotice, message);
  surface.append(heading, form);
  dialog.append(surface);
  document.body.append(dialog);
  const card = elements.codexCardHost.querySelector(".codex-card");
  const scrollCardToTop = () => {
    const start = window.scrollY;
    const target = Math.max(0, start + card.getBoundingClientRect().top);
    const distance = target - start;
    const duration = 220;
    if (
      Math.abs(distance) < 1
      || window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      window.scrollTo(0, target);
      return;
    }
    const startedAt = performance.now();
    const step = (now) => {
      const progress = Math.min((now - startedAt) / duration, 1);
      const eased = 1 - ((1 - progress) ** 3);
      window.scrollTo(0, start + (distance * eased));
      if (progress < 1) {
        window.requestAnimationFrame(step);
      }
    };
    window.requestAnimationFrame(step);
  };
  const restoreCardPosition = () => {
    if (!card || !window.matchMedia("(pointer: coarse)").matches) {
      return;
    }
    const viewport = window.visualViewport;
    let restoreTimer = null;
    const finishRestore = () => {
      if (viewport) {
        viewport.removeEventListener("resize", scheduleRestore);
      }
      scrollCardToTop();
    };
    const scheduleRestore = () => {
      window.clearTimeout(restoreTimer);
      restoreTimer = window.setTimeout(finishRestore, 180);
    };
    if (viewport) {
      viewport.addEventListener("resize", scheduleRestore);
    }
    restoreTimer = window.setTimeout(finishRestore, 450);
  };
  const closeDialog = () => {
    prompt.blur();
    if (dialog.open) {
      dialog.close();
    }
  };
  dialog.addEventListener(
    "close",
    () => {
      dialog.remove();
      restoreCardPosition();
    },
    { once: true },
  );
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      closeDialog();
    }
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    submit.disabled = true;
    prompt.disabled = true;
    setMessage(message, "正在提交快速交互…");
    try {
      await apiFetch(`/api/codex/sessions/${encodeURIComponent(session.id)}/quick-interactions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: prompt.value.trim(),
          confirm_stop_unknown_terminal: confirmStopUnknownTerminal,
        }),
      });
      closeDialog();
      setMessage(elements.codexMessage, "");
      loadCodexSessions({ force: true });
    } catch (error) {
      if (!handleAccessError(error)) {
        if (
          error.code
          === "quick_interaction_terminal_confirmation_required"
        ) {
          confirmStopUnknownTerminal = true;
          unknownNotice.hidden = false;
          setMessage(message, "请确认影响后再次点击执行。", "error");
        } else {
          setMessage(message, error.message || "快速交互提交失败。", "error");
        }
      }
      submit.disabled = false;
      prompt.disabled = false;
    }
  });
  dialog.showModal();
  prompt.focus();
}

function renderCodexSessions(sessions) {
  if (!elements.codexSessions) {
    return;
  }

  elements.codexSessions.replaceChildren();
  if (!sessions.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "暂无会话，先新建。";
    elements.codexSessions.append(empty);
    return;
  }

  sessions.forEach((session) => {
    const configuredPermission = normalizeCodexPermission(session.permission_mode);
    const quickInteractionRunning = session.quick_interaction_running === true;
    const item = document.createElement("article");
    const main = document.createElement("button");
    const text = document.createElement("span");
    const title = document.createElement("strong");
    const meta = document.createElement("span");
    const path = document.createElement("span");
    const permissionPanel = document.createElement("div");
    const permission = document.createElement("button");
    const permissionPending = document.createElement("span");
    const actions = document.createElement("div");
    const quickInteraction = document.createElement("button");
    const interactionHistory = document.createElement("button");
    const stop = document.createElement("button");
    const archive = document.createElement("button");
    item.className = "session-item";
    main.className = "session-enter";
    main.type = "button";
    title.textContent = session.title || session.workspace_name;
    title.title = title.textContent;
    const state = quickInteractionRunning
      ? "快速交互 · 执行中"
      : session.error
        ? "终端访问异常 · 可重试"
        : session.status === "error"
          ? "会话异常 · 可重试"
          : session.status === "new"
            ? "尚未启动 · 可进入"
            : session.status !== "running"
              ? "会话已停止 · 可恢复"
              : session.activity === "working"
                ? "会话运行中 · 执行中"
                : session.activity === "idle"
                  ? "会话运行中 · 等待输入"
                  : "会话运行中 · 状态未知";
    meta.textContent =
      `${state} · ` +
      `${formatSessionTime(
        quickInteractionRunning
          ? session.quick_interaction_updated_at
          : session.updated_at,
      )}`;
    meta.className = "session-meta";
    path.className = "session-path";
    path.textContent = session.cwd;
    path.title = session.cwd;
    text.append(title, meta, path);
    main.append(text);
    main.disabled = quickInteractionRunning;
    if (quickInteractionRunning) {
      main.title = "快速交互正在执行";
    }
    main.addEventListener("click", () => enterCodexSession(session.id, main));
    const displayedPermission = session.status === "running"
      ? normalizeCodexPermission(session.active_permission_mode)
      : configuredPermission;
    permissionPanel.className = "session-permission-panel";
    permission.type = "button";
    permission.className =
      `session-permission session-permission-${displayedPermission}`;
    permission.textContent = `${codexPermissionLabel(displayedPermission)} ▾`;
    permission.setAttribute(
      "aria-label",
      `设置 ${title.textContent} 的会话权限`,
    );
    permission.addEventListener("click", () => openCodexPermissionDialog(session));
    permissionPending.className = "session-permission-pending";
    permissionPending.textContent = session.permission_pending
      ? `待切换至${codexPermissionLabel(configuredPermission)}`
      : "";
    permissionPending.hidden = !session.permission_pending;
    permissionPanel.append(permission, permissionPending);
    stop.type = "button";
    stop.className = "button-secondary session-action";
    stop.textContent = "停止";
    stop.disabled = session.status !== "running";
    stop.addEventListener("click", () => stopCodexSession(session.id, stop));
    archive.type = "button";
    archive.className = "button-secondary session-action";
    archive.textContent = "归档";
    archive.disabled = !session.codex_session_id || quickInteractionRunning;
    if (quickInteractionRunning) {
      archive.title = "快速交互正在执行";
    }
    archive.addEventListener("click", () =>
      archiveCodexSession(session.id, archive),
    );
    actions.className = "session-actions";
    quickInteraction.type = "button";
    quickInteraction.className = "button-secondary session-action session-quick-interaction";
    quickInteraction.textContent = "快速交互";
    quickInteraction.disabled = !session.codex_session_id
      || (
        session.status === "running"
        && session.activity === "working"
      )
      || quickInteractionRunning
      || configuredPermission === "ask";
    if (quickInteractionRunning) {
      quickInteraction.title = "快速交互正在执行";
    } else if (configuredPermission === "ask") {
      quickInteraction.title = "Ask for approval 需要进入实时终端完成审批";
    } else if (
      session.status === "running"
      && session.activity === "working"
    ) {
      quickInteraction.title = "实时会话正在执行";
    } else if (
      session.status === "running"
      && session.activity === "unknown"
    ) {
      quickInteraction.title = "执行前需要确认停止状态未知的实时会话";
    } else if (session.status === "running") {
      quickInteraction.title = "执行时将自动停止空闲的实时会话";
    } else if (!session.codex_session_id) {
      quickInteraction.title = "会话尚未启动";
    }
    quickInteraction.addEventListener("click", () => openQuickInteractionDialog(session));
    interactionHistory.type = "button";
    interactionHistory.className =
      "button-secondary session-action session-interaction-history";
    interactionHistory.textContent = "交互记录";
    interactionHistory.addEventListener("click", () => {
      window.location.href =
        `/codex/${encodeURIComponent(session.id)}/quick-interactions`;
    });
    actions.append(quickInteraction, interactionHistory, stop, archive);
    item.append(main, permissionPanel, actions);
    elements.codexSessions.append(item);
  });
}

function codexSessionsSignature(sessions) {
  return JSON.stringify(
    sessions.map((session) => [
      session.id,
      session.status,
      session.activity,
      session.quick_interaction_running,
      session.quick_interaction_updated_at,
      session.updated_at,
      session.title,
      session.cwd,
      session.codex_session_id,
      session.permission_mode,
      session.active_permission_mode,
      session.permission_pending,
    ]),
  );
}

function codexPermissionLabel(mode) {
  return CODEX_PERMISSION_OPTIONS.find(([value]) => value === mode)?.[1]
    || "Ask for approval";
}

function normalizeCodexPermission(mode) {
  return ["ask", "auto-review", "read-only", "full-access"].includes(mode)
    ? mode
    : "ask";
}

function openCodexPermissionDialog(session) {
  const dialog = elements.codexPermissionDialog;
  const form = elements.codexPermissionForm;
  if (!dialog || !form) {
    return;
  }
  form.dataset.sessionId = session.id;
  const configuredPermission = normalizeCodexPermission(session.permission_mode);
  form.dataset.permissionMode = configuredPermission;
  const activeMode = session.status === "running"
    ? normalizeCodexPermission(session.active_permission_mode)
    : configuredPermission;
  elements.codexPermissionCurrent.textContent =
    `当前：${codexPermissionLabel(activeMode)}`;
  elements.codexPermissionNotice.textContent = session.status === "running"
    ? "切换权限会立即停止当前会话；再次进入时按新权限恢复。"
    : "权限将在下次进入会话时生效。";
  const selected = form.elements.namedItem("permission_mode");
  Array.from(selected || []).forEach((radio) => {
    radio.checked = radio.value === configuredPermission;
  });
  if (!dialog.open) {
    dialog.showModal();
  }
}

async function saveCodexSessionPermission(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const sessionId = form.dataset.sessionId;
  const data = new FormData(form);
  const permissionMode = data.get("permission_mode");
  const submit = form.querySelector('button[type="submit"]');
  if (!sessionId || typeof permissionMode !== "string" || !submit) {
    return;
  }
  if (
    permissionMode === "full-access"
    && form.dataset.permissionMode !== "full-access"
    && !window.confirm(
      "完全访问将取消工作区沙箱限制，并且不会请求操作审批。确定保存吗？",
    )
  ) {
    return;
  }
  setCodexButtonBusy(submit, true);
  beginCodexMutation();
  try {
    await apiFetch(`/api/codex/sessions/${sessionId}/permission`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ permission_mode: permissionMode }),
    });
    elements.codexPermissionDialog?.close();
    await loadCodexSessions({ force: true });
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.codexMessage, error.message || "权限保存失败。", "error");
    }
  } finally {
    if (submit.isConnected) {
      setCodexButtonBusy(submit, false);
    }
    endCodexMutation();
  }
}

function renderCodexData(data, { sessionsOnly = false } = {}) {
  if (
    !Array.isArray(data?.workspaces)
    || !Array.isArray(data?.sessions)
    || typeof data?.available !== "boolean"
    || !data?.dependencies
    || typeof data.dependencies !== "object"
  ) {
    return false;
  }
  if (!sessionsOnly) {
    renderCodexWorkspaces(data.workspaces, data.available);
  }
  renderCodexSessions(data.sessions);
  codexSessionSignature = codexSessionsSignature(data.sessions);
  elements.codexSessionCount.textContent = `共 ${data.sessions.length} 个会话`;
  if (!sessionsOnly) {
    const missing = dependencyMessage(data.dependencies);
    if (data.available) {
      setMessage(elements.codexMessage, "");
    } else {
      setMessage(
        elements.codexMessage,
        missing || data.unavailable_reason || "Codex PTY 不可用。",
        "error",
      );
    }
  }
  return true;
}

function restoreCodexCardCache() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(CODEX_CARD_CACHE_KEY) || "null");
    if (!renderCodexData(cached)) {
      sessionStorage.removeItem(CODEX_CARD_CACHE_KEY);
    }
  } catch {
    sessionStorage.removeItem(CODEX_CARD_CACHE_KEY);
  }
}

function storeCodexCardCache(data) {
  try {
    sessionStorage.setItem(CODEX_CARD_CACHE_KEY, JSON.stringify(data));
  } catch {
    // A storage quota failure must not break the live Codex card.
  }
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

function setCodexButtonBusy(button, busy) {
  if (busy) {
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    return;
  }
  button.disabled = false;
  button.removeAttribute("aria-busy");
}

async function createCodexSession(workspaceId, button) {
  if (!elements.codexMessage) {
    return;
  }

  const workspaceButtons = Array.from(
    elements.codexWorkspaces?.querySelectorAll("button") || [],
  );
  const disabledStates = workspaceButtons.map((item) => item.disabled);
  setMessage(elements.codexMessage, "正在创建会话…");
  workspaceButtons.forEach((item) => {
    item.disabled = true;
  });
  setCodexButtonBusy(button, true);
  beginCodexMutation();
  try {
    await apiFetch("/api/codex/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace_id: workspaceId }),
    });
    await loadCodexSessions({ force: true });
    elements.codexWorkspaceDialog?.close();
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.codexMessage, error.message || "会话创建失败。", "error");
    }
  } finally {
    workspaceButtons.forEach((item, index) => {
      if (item.isConnected) {
        item.disabled = disabledStates[index];
      }
    });
    if (button.isConnected) {
      setCodexButtonBusy(button, false);
    }
    endCodexMutation();
  }
}

async function enterCodexSession(sessionId, button) {
  if (!elements.codexMessage) {
    return;
  }

  setMessage(elements.codexMessage, "");
  button.disabled = true;
  beginCodexMutation();
  try {
    const data = await apiFetch(`/api/codex/sessions/${sessionId}/access`, {
      method: "POST",
    });
    window.location.assign(data.terminal_url);
  } catch (error) {
    if (!handleAccessError(error)) {
      await loadCodexSessions({ force: true });
      setMessage(elements.codexMessage, error.message || "打开失败。", "error");
    }
  } finally {
    button.disabled = false;
    endCodexMutation();
  }
}

async function stopCodexSession(sessionId, button) {
  if (!elements.codexMessage) {
    return;
  }

  setMessage(elements.codexMessage, "");
  setCodexButtonBusy(button, true);
  beginCodexMutation();
  try {
    await apiFetch(`/api/codex/sessions/${sessionId}/stop`, {
      method: "POST",
    });
    await loadCodexSessions({ force: true });
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.codexMessage, error.message || "停止失败。", "error");
    }
  } finally {
    setCodexButtonBusy(button, false);
    endCodexMutation();
  }
}

async function archiveCodexSession(sessionId, button) {
  if (!elements.codexMessage) {
    return;
  }

  setMessage(elements.codexMessage, "");
  setCodexButtonBusy(button, true);
  beginCodexMutation();
  try {
    await apiFetch(`/api/codex/sessions/${sessionId}/archive`, {
      method: "POST",
    });
    await loadCodexSessions({ force: true });
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.codexMessage, error.message || "归档失败。", "error");
    }
  } finally {
    setCodexButtonBusy(button, false);
    endCodexMutation();
  }
}

function clearCodexPollTimer() {
  if (codexPollTimer) {
    window.clearTimeout(codexPollTimer);
    codexPollTimer = null;
  }
}

function stopCodexPolling({ reset = false } = {}) {
  clearCodexPollTimer();
  if (reset) {
    codexShouldPoll = false;
    codexPollUnchangedSince = 0;
    codexSessionSignature = "";
  }
}

function scheduleCodexPoll(delay) {
  clearCodexPollTimer();
  if (
    !codexShouldPoll
    || codexMutationCount > 0
    || document.visibilityState !== "visible"
    || !activeToken
  ) {
    return;
  }
  codexPollTimer = window.setTimeout(() => {
    codexPollTimer = null;
    loadCodexSessions({ background: true });
  }, delay);
}

function updateCodexPolling(data, stateChanged) {
  const plan = window.codexPollPlan({
    sessions: data.sessions,
    stateChanged,
    unchangedSince: codexPollUnchangedSince,
    now: Date.now(),
    visible: document.visibilityState === "visible",
    authenticated: Boolean(activeToken),
    mutating: codexMutationCount > 0,
    fastDelay: CODEX_POLL_FAST_MS,
    slowDelay: CODEX_POLL_SLOW_MS,
    slowAfter: CODEX_POLL_SLOW_AFTER_MS,
  });
  codexShouldPoll = plan.shouldPoll;
  codexPollUnchangedSince = plan.unchangedSince;
  if (!plan.shouldPoll) {
    stopCodexPolling();
    return;
  }
  if (plan.delay !== null) {
    scheduleCodexPoll(plan.delay);
  }
}

function beginCodexMutation() {
  codexMutationCount += 1;
  clearCodexPollTimer();
  if (elements.refreshCodex) {
    elements.refreshCodex.disabled = true;
  }
}

function endCodexMutation() {
  codexMutationCount = Math.max(0, codexMutationCount - 1);
  if (codexMutationCount === 0 && elements.refreshCodex) {
    elements.refreshCodex.disabled = false;
  }
  if (codexMutationCount === 0 && codexShouldPoll) {
    scheduleCodexPoll(CODEX_POLL_FAST_MS);
  }
}

async function loadCodexSessions(options = {}) {
  const background = options?.background === true;
  const force = options?.force === true;
  if (
    !elements.codexPanel ||
    !elements.codexWorkspaces ||
    !elements.codexMessage ||
    !elements.codexSessions ||
    !elements.codexSessionCount
  ) {
    return;
  }

  if (codexLoadPromise) {
    await codexLoadPromise;
    if (!force) {
      return;
    }
    if (!activeToken || !elements.codexPanel) {
      return;
    }
  }
  const requestVersion = accessVersion;
  if (!background && elements.refreshCodex) {
    elements.refreshCodex.disabled = true;
  }
  const loadPromise = (async () => {
    try {
      const data = await apiFetch("/api/codex/sessions");
      if (requestVersion !== accessVersion) {
        return;
      }
      if (background && codexMutationCount > 0) {
        return;
      }
      const previousSignature = codexSessionSignature;
      const nextSignature = Array.isArray(data?.sessions)
        ? codexSessionsSignature(data.sessions)
        : "";
      const stateChanged = nextSignature !== previousSignature;
      if (
        (!background || stateChanged)
        && renderCodexData(data, { sessionsOnly: background })
      ) {
        storeCodexCardCache(data);
      } else if (background && !stateChanged) {
        storeCodexCardCache(data);
      }
      if (Array.isArray(data?.sessions)) {
        updateCodexPolling(data, stateChanged);
      }
    } catch (error) {
      if (requestVersion !== accessVersion) {
        return;
      }
      if (handleAccessError(error)) {
        return;
      }
      if (background) {
        scheduleCodexPoll(CODEX_POLL_SLOW_MS);
      } else {
        setMessage(elements.codexMessage, error.message || "会话读取失败。", "error");
      }
    } finally {
      if (!background && codexMutationCount === 0 && elements.refreshCodex) {
        elements.refreshCodex.disabled = false;
      }
    }
  })();
  codexLoadPromise = loadPromise;
  try {
    return await loadPromise;
  } finally {
    if (codexLoadPromise === loadPromise) {
      codexLoadPromise = null;
    }
  }
}

function automationStatusText(state) {
  return {
    idle: "尚未执行",
    queued: "等待执行",
    running: "执行中",
    success: "成功",
    failed: "失败",
  }[state] || state;
}

function automationTime(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("zh-CN", { hour12: false });
}

async function runAutomation(task, button) {
  button.disabled = true;
  button.textContent = "受理中…";
  try {
    await apiFetch(`/api/automations/${encodeURIComponent(task.id)}/run`, {
      method: "POST",
    });
    setMessage(elements.automationMessage, "");
    await loadAutomations();
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(
        elements.automationMessage,
        error.message || "自动化任务启动失败。",
        "error",
      );
    }
  } finally {
    button.textContent = "运行";
  }
}

async function controlAutomationBrowser() {
  const action = automationBrowserState === "running" ? "stop" : "start";
  if (
    action === "stop"
    && !window.confirm("确定停止 Debug Chrome 吗？已打开的调试浏览器页面会关闭。")
  ) {
    return;
  }
  elements.automationBrowserControl.disabled = true;
  elements.automationBrowserProfile.disabled = true;
  elements.automationBrowserMode.disabled = true;
  const selectedProfile = automationBrowserProfiles.find(
    (profile) => profile.id === elements.automationBrowserProfile.value,
  );
  if (action === "start" && !selectedProfile) {
    setMessage(elements.automationMessage, "请选择浏览器用户。", "error");
    await loadAutomations();
    return;
  }
  const requiresInitialization = action === "start" && !selectedProfile.initialized;
  if (
    requiresInitialization
    && !window.confirm(
      "首次使用需要复制该浏览器用户。请先完全退出默认 Chrome；复制完成后将自动启动 Debug Chrome。确定继续吗？",
    )
  ) {
    await loadAutomations();
    return;
  }
  elements.automationBrowserControl.textContent = requiresInitialization
    ? "初始化中…"
    : action === "start" ? "启动中…" : "停止中…";
  try {
    const options = { method: "POST" };
    if (action === "start") {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify({
        mode: elements.automationBrowserMode.value,
        profile_id: selectedProfile.id,
      });
    }
    const endpoint = requiresInitialization
      ? "/api/automations/browser/initialize"
      : `/api/automations/browser/${action}`;
    await apiFetch(endpoint, options);
    setMessage(elements.automationMessage, "");
    await loadAutomations();
  } catch (error) {
    if (!handleAccessError(error)) {
      await loadAutomations();
      setMessage(
        elements.automationMessage,
        error.message || "Debug Chrome 操作失败。",
        "error",
      );
    }
  }
}

async function checkFeishuEnvironment() {
  releaseFeishuQr();
  elements.automationFeishuCheck.disabled = true;
  elements.automationFeishuCheck.textContent = "检查中…";
  setBadge(elements.automationFeishuBadge, "检查中", "muted");
  try {
    await apiFetch("/api/automations/environment/feishu/check", { method: "POST" });
    setMessage(elements.automationMessage, "");
    await loadAutomations();
  } catch (error) {
    if (!handleAccessError(error)) {
      await loadAutomations();
      setMessage(
        elements.automationMessage,
        error.message || "飞书环境检查失败。",
        "error",
      );
    }
  }
}

function releaseFeishuQr() {
  feishuQrVersion += 1;
  if (feishuQrObjectUrl) {
    URL.revokeObjectURL(feishuQrObjectUrl);
    feishuQrObjectUrl = "";
  }
  feishuQrLoading = false;
  elements.automationFeishuQr.removeAttribute("src");
  elements.automationFeishuLogin.hidden = true;
}

async function loadFeishuQr() {
  if (feishuQrLoading || feishuQrObjectUrl || !activeToken) {
    return;
  }
  const requestVersion = accessVersion;
  const qrVersion = feishuQrVersion;
  feishuQrLoading = true;
  try {
    const response = await fetch("/api/automations/environment/feishu/qr", {
      headers: { Authorization: `Bearer ${activeToken}` },
      cache: "no-store",
    });
    if (response.status === 401) {
      throw { code: "invalid_credentials", message: "Token 无效或已变更。" };
    }
    if (!response.ok) {
      throw { code: "feishu_qr_unavailable", message: "飞书登录二维码读取失败。" };
    }
    const blob = await response.blob();
    if (requestVersion !== accessVersion || qrVersion !== feishuQrVersion) {
      return;
    }
    feishuQrObjectUrl = URL.createObjectURL(blob);
    elements.automationFeishuQr.src = feishuQrObjectUrl;
  } catch (error) {
    if (requestVersion !== accessVersion || handleAccessError(error)) {
      return;
    }
    setMessage(
      elements.automationMessage,
      error.message || "飞书登录二维码读取失败。",
      "error",
    );
  } finally {
    feishuQrLoading = false;
  }
}

function renderAutomations(data) {
  elements.automationList.replaceChildren();
  const browserRunning = data.browser_state === "running";
  automationBrowserProfiles = data.browser_profiles || [];
  const previousProfile = elements.automationBrowserProfile.value;
  elements.automationBrowserProfile.replaceChildren();
  automationBrowserProfiles.forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.id;
    const availability = profile.initialized
      ? profile.source_available ? "" : " · 源用户已不存在"
      : " · 未初始化";
    option.textContent = `${profile.name}${availability}`;
    elements.automationBrowserProfile.append(option);
  });
  const preferredProfile = automationBrowserProfiles.find(
    (profile) => profile.id === previousProfile,
  ) || automationBrowserProfiles.find(
    (profile) => profile.id === data.browser_profile_id,
  ) || automationBrowserProfiles.find(
    (profile) => profile.active && profile.initialized,
  ) || automationBrowserProfiles.find(
    (profile) => profile.initialized,
  ) || automationBrowserProfiles[0];
  if (preferredProfile) {
    elements.automationBrowserProfile.value = preferredProfile.id;
  }
  const selectedProfile = automationBrowserProfiles.find(
    (profile) => profile.id === elements.automationBrowserProfile.value,
  );
  const initializing = automationBrowserProfiles.some(
    (profile) => profile.initialization_state === "running",
  );
  const feishuChecking = data.feishu_environment.state === "checking";
  const automationBusy = initializing || feishuChecking || data.tasks.some((task) => ["queued", "running"].includes(task.state.status));
  automationBrowserState = data.browser_state;
  setBadge(
    elements.automationBrowserBadge,
    `${data.browser_message}${data.browser_profile_name ? ` · ${data.browser_profile_name}` : ""}${data.browser_mode ? ` · ${data.browser_mode}` : ""}`,
    browserRunning ? "success" : data.browser_state === "stopped" ? "timeout" : "failed",
  );
  elements.automationBrowserControl.disabled = (
    !["running", "stopped"].includes(data.browser_state)
    || (browserRunning && automationBusy)
    || (!browserRunning && (!selectedProfile || !selectedProfile.source_available && !selectedProfile.initialized))
    || initializing
  );
  elements.automationBrowserProfile.hidden = data.browser_state !== "stopped";
  elements.automationBrowserProfile.disabled = data.browser_state !== "stopped" || initializing;
  elements.automationBrowserMode.hidden = data.browser_state !== "stopped";
  elements.automationBrowserMode.disabled = data.browser_state !== "stopped" || initializing;
  elements.automationBrowserControl.textContent = browserRunning
    ? "停止"
    : initializing
      ? "初始化中…"
      : selectedProfile && !selectedProfile.initialized
        ? "初始化并启动"
        : "启动";
  const failedInitialization = selectedProfile?.initialization_state === "failed"
    ? selectedProfile
    : null;
  if (failedInitialization?.initialization_message) {
    setMessage(elements.automationMessage, failedInitialization.initialization_message, "error");
  } else if (data.browser_profiles_error && !automationBrowserProfiles.length) {
    setMessage(elements.automationMessage, data.browser_profiles_error, "error");
  }
  const feishuTime = automationTime(data.feishu_environment.checked_at);
  const feishuBadgeKind = {
    available: "success",
    login_required: "timeout",
    failed: "failed",
    browser_stopped: "muted",
    checking: "muted",
    unchecked: "muted",
  }[data.feishu_environment.state] || "muted";
  setBadge(
    elements.automationFeishuBadge,
    `${data.feishu_environment.message}${feishuTime ? ` · ${feishuTime}` : ""}`,
    feishuBadgeKind,
  );
  elements.automationFeishuCheck.disabled = !browserRunning || automationBusy;
  elements.automationFeishuCheck.textContent = feishuChecking
    ? "检查中…"
    : data.feishu_environment.state === "unchecked"
      || data.feishu_environment.state === "browser_stopped"
      ? "检查"
      : "重新检查";
  if (
    data.feishu_environment.state === "login_required"
    && data.feishu_environment.qr_available
  ) {
    elements.automationFeishuLogin.hidden = false;
    loadFeishuQr();
  } else {
    releaseFeishuQr();
  }
  elements.automationCount.textContent = `已启用 ${data.enabled_count} 个任务`;

  if (!data.enabled) {
    setMessage(elements.automationMessage, "自动化任务未启用。", "error");
    return false;
  }
  if (!data.tasks.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "暂无自动化任务，请先配置 automations.yaml 或 automations.local.yaml。";
    elements.automationList.append(empty);
    return false;
  }

  let active = false;
  data.tasks.forEach((task) => {
    const item = document.createElement("article");
    const copy = document.createElement("div");
    const name = document.createElement("strong");
    const status = document.createElement("span");
    const reason = document.createElement("span");
    const button = document.createElement("button");
    const busy = ["queued", "running"].includes(task.state.status);
    active = active || busy;
    item.className = "automation-item";
    copy.className = "automation-item-copy";
    name.textContent = task.name;
    const time = automationTime(task.state.finished_at || task.state.started_at);
    status.className = "automation-item-status";
    status.textContent = `${automationStatusText(task.state.status)}${time ? ` · ${time}` : ""}`;
    reason.className = "automation-item-reason";
    reason.textContent = task.state.message || "暂无状态说明";
    button.type = "button";
    button.className = "button-secondary automation-run";
    button.textContent = busy ? "执行中…" : "运行";
    button.disabled = !browserRunning || !task.enabled || busy || feishuChecking;
    button.addEventListener("click", () => runAutomation(task, button));
    copy.append(name, status, reason);
    if (task.state.linked_documents?.length) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `关联文档明细（${task.state.linked_documents.length}）`;
      details.className = "automation-linked-details";
      details.append(summary);
      task.state.linked_documents.forEach((linkedDocument) => {
        const row = document.createElement("span");
        row.textContent = `${linkedDocument.status === "success" ? "成功" : "失败"} · ${linkedDocument.name} · ${linkedDocument.message}`;
        details.append(row);
      });
      copy.append(details);
    }
    item.append(copy, button);
    elements.automationList.append(item);
  });
  return active || initializing;
}

async function loadAutomations() {
  const requestVersion = accessVersion;
  elements.refreshAutomations.disabled = true;
  try {
    const data = await apiFetch("/api/automations");
    if (requestVersion !== accessVersion) {
      return;
    }
    setMessage(elements.automationMessage, "");
    const active = renderAutomations(data);
    if (automationPollTimer) {
      window.clearTimeout(automationPollTimer);
      automationPollTimer = null;
    }
    if (active) {
      automationPollTimer = window.setTimeout(loadAutomations, 1000);
    }
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return;
    }
    if (!handleAccessError(error)) {
      automationBrowserState = "unknown";
      setBadge(elements.automationBrowserBadge, "检查失败", "failed");
      elements.automationBrowserControl.disabled = true;
      elements.automationBrowserProfile.hidden = true;
      elements.automationBrowserProfile.disabled = true;
      elements.automationBrowserMode.hidden = true;
      elements.automationBrowserMode.disabled = true;
      setBadge(elements.automationFeishuBadge, "检查失败", "failed");
      elements.automationFeishuCheck.disabled = true;
      setMessage(elements.automationMessage, error.message || "自动化任务读取失败。", "error");
    }
  } finally {
    elements.refreshAutomations.disabled = false;
  }
}

function projectDocumentDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function renderProjectDocuments(data) {
  elements.projectDocsList.replaceChildren();
  data.documents.forEach((item) => {
    const card = document.createElement("article");
    const link = document.createElement("a");
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    const summary = document.createElement("span");
    const meta = document.createElement("span");
    const badge = document.createElement("span");
    const time = document.createElement("time");
    const footer = document.createElement("div");
    const archive = document.createElement("button");
    card.className = "design-document-item";
    link.className = "design-document-main";
    link.href = `/project-docs/${encodeURIComponent(item.id)}`;
    copy.className = "design-document-copy";
    title.textContent = item.title;
    summary.textContent = item.summary;
    meta.className = "design-document-meta";
    badge.className = "badge badge-success";
    badge.textContent = item.status;
    time.dateTime = item.updated_at;
    time.textContent = projectDocumentDate(item.updated_at);
    archive.className = "button-secondary document-archive-action";
    archive.type = "button";
    archive.dataset.documentId = item.id;
    archive.textContent = "归档";
    copy.append(title, summary);
    meta.append(badge, time);
    link.append(copy);
    footer.className = "design-document-footer";
    footer.append(meta, archive);
    card.append(link, footer);
    elements.projectDocsList.append(card);
  });
  if (!data.documents.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "暂无设计文档。";
    elements.projectDocsList.append(empty);
  }
  elements.projectDocsCount.textContent = `${data.count} 份文档`;
}

async function archiveProjectDocument(button) {
  const documentId = button.dataset.documentId;
  if (!documentId || !window.confirm("归档后，该文档将从首页移除。确定继续吗？")) {
    return;
  }
  button.disabled = true;
  try {
    await apiFetch(`/api/project-docs/${encodeURIComponent(documentId)}/archive`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived: true }),
    });
    setMessage(elements.projectDocsMessage, "文档已归档。", "success");
    await loadProjectDocuments({ clearMessage: false });
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.projectDocsMessage, error.message || "文档归档失败。", "error");
    }
    button.disabled = false;
  }
}

async function loadProjectDocuments({ clearMessage = true } = {}) {
  const requestVersion = accessVersion;
  elements.refreshProjectDocs.disabled = true;
  if (clearMessage) {
    setMessage(elements.projectDocsMessage, "");
  }
  try {
    const data = await apiFetch("/api/project-docs");
    if (requestVersion !== accessVersion) {
      return;
    }
    renderProjectDocuments(data);
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return;
    }
    if (!handleAccessError(error)) {
      setMessage(elements.projectDocsMessage, error.message || "文档列表读取失败。", "error");
    }
  } finally {
    elements.refreshProjectDocs.disabled = false;
  }
}

elements.projectDocsList.addEventListener("click", (event) => {
  const button = event.target.closest(".document-archive-action");
  if (button) {
    archiveProjectDocument(button);
  }
});

async function loadLogs() {
  const requestVersion = accessVersion;
  elements.loadLogs.disabled = true;
  setMessage(elements.logsMessage, "正在读取日志…");
  try {
    const lines = elements.logLines.value;
    const data = await apiFetch(`/api/logs/page?source=${activeLogSource}&lines=${lines}`);
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

elements.logTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    activeLogSource = tab.dataset.logSource;
    elements.logTabs.forEach((item) => {
      const selected = item === tab;
      item.classList.toggle("is-active", selected);
      item.setAttribute("aria-selected", String(selected));
    });
    loadLogs();
  });
});

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

elements.refreshStatus.addEventListener("click", loadStatus);
elements.refreshAutomations.addEventListener("click", () => loadAutomations());
elements.refreshProjectDocs.addEventListener("click", loadProjectDocuments);
elements.loadLogs.addEventListener("click", loadLogs);
elements.automationBrowserControl.addEventListener("click", controlAutomationBrowser);
elements.automationBrowserProfile.addEventListener("change", () => {
  if (automationBrowserState !== "stopped") {
    return;
  }
  const selected = automationBrowserProfiles.find(
    (profile) => profile.id === elements.automationBrowserProfile.value,
  );
  elements.automationBrowserControl.textContent = selected && !selected.initialized
    ? "初始化并启动"
    : "启动";
  elements.automationBrowserControl.disabled = (
    !selected || (!selected.initialized && !selected.source_available)
  );
});
elements.automationFeishuCheck.addEventListener("click", checkFeishuEnvironment);

function refreshCardsOnReturn() {
  if (!activeToken) {
    return;
  }
  const now = Date.now();
  if (now - cardsRefreshAt < 500) {
    return;
  }
  cardsRefreshAt = now;
  document.querySelectorAll('[data-card-return-refresh="true"]').forEach((card) => {
    const refresh = CARD_RETURN_REFRESHERS[card.dataset.cardKey];
    if (refresh) {
      refresh();
    }
  });
}

window.addEventListener("pageshow", () => {
  refreshCardsOnReturn();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    refreshCardsOnReturn();
    if (codexShouldPoll) {
      loadCodexSessions({ background: true });
    }
  } else {
    clearCodexPollTimer();
  }
});

async function restartHub() {
  elements.restartDialogConfirm.disabled = true;
  setMessage(elements.globalMessage, "正在下发重启命令…");
  try {
    const previousInstanceId = await hubInstanceId();
    await apiFetch("/api/maintenance/restart", { method: "POST" });
    elements.restartDialog.close();
    setMessage(elements.globalMessage, "重启命令已下发，正在等待 Hub 恢复…");
    await waitForHubRestart(previousInstanceId);
    setMessage(elements.globalMessage, "Chub 已恢复，正在同步卡片状态…");
    await refreshCardsAfterRestart();
    setMessage(elements.globalMessage, "Chub 已重启并恢复连接。", "success");
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.globalMessage, error.message || "重启失败。", "error");
    }
  } finally {
    elements.restartDialogConfirm.disabled = false;
  }
}

elements.restartHub.addEventListener("click", () => {
  if (!elements.restartDialog.open) {
    elements.restartDialog.showModal();
  }
});
elements.restartDialogClose.addEventListener("click", () => elements.restartDialog.close());
elements.restartDialogCancel.addEventListener("click", () => elements.restartDialog.close());
elements.restartDialogConfirm.addEventListener("click", restartHub);
elements.restartDialog.addEventListener("click", (event) => {
  if (event.target === elements.restartDialog) {
    elements.restartDialog.close();
  }
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

cardCollapsedState = loadCardCollapsedState();
const savedSessionToken = sessionStorage.getItem(SESSION_TOKEN_KEY);
const savedLocalToken = localStorage.getItem(LOCAL_TOKEN_KEY);
const savedToken = savedSessionToken || savedLocalToken || "";
ensureCodexCard();
setupCollapsibleCards();
if (savedToken) {
  restoreCodexCardCache();
  elements.rememberToken.checked = Boolean(savedLocalToken);
  setBadge(elements.accessBadge, "自动连接");
  connectWithToken(savedToken, Boolean(savedLocalToken), true);
} else {
  showDisconnectedView();
}
