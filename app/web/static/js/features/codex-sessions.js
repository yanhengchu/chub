"use strict";

const CODEX_CARD_CACHE_KEY = "hub.codexCardCache";
const CODEX_MODEL_PREFERENCE_CACHE_KEY = "hub.codexModelPreferenceCache";
const AI_USAGE_CACHE_KEY = "hub.aiUsageCache";
const CODEX_ENTRY_MODE_KEY = "hub.codexEntryMode.v1";
const CODEX_DEFAULT_PERMISSION_KEY = "hub.codexDefaultPermission.v1";
const CODEX_DEFAULT_MODEL_KEY = "hub.codexDefaultModel.v1";
const CODEX_DEFAULT_REASONING_EFFORT_KEY = "hub.codexDefaultReasoningEffort.v1";
const CODEX_SHOW_TRANSLATION_SESSION_KEY = "hub.codexShowTranslationSession.v1";
const CODEX_QUOTA_REFRESH_MS = 5 * 60 * 1000;
const CODEX_REASONING_LABELS = {
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra High",
  max: "Max",
  ultra: "Ultra",
};
const CODEX_PERMISSION_OPTIONS = [
  ["ask", "Ask for approval", "在当前工作区操作，越界时由你确认。"],
  ["auto-review", "Approve for me", "保持工作区边界，由 Codex 自动审核越界请求。"],
  ["full-access", "Full access", "不受工作区沙箱限制，且不会请求操作审批。"],
  ["read-only", "Read Only", "只能查看和分析，不能修改文件。"],
];
const CODEX_POLL_FAST_MS = 2000;
const CODEX_POLL_SLOW_MS = 8000;
const CODEX_POLL_SLOW_AFTER_MS = 2 * 60 * 1000;
let codexPollTimer = null;
let codexPollUnchangedSince = 0;
let codexShouldPoll = false;
let codexSessionSignature = "";
let codexLoadPromise = null;
let codexQuotaLoadPromise = null;
let codexQuotaLoadedAt = 0;
let codexMutationCount = 0;

function createCodexCard() {
  const card = document.createElement("article");
  const header = document.createElement("div");
  const kicker = document.createElement("p");
  const title = document.createElement("h2");
  const description = document.createElement("p");
  const cardContent = document.createElement("div");
  const cardContentInner = document.createElement("div");
  const panel = document.createElement("div");
  const currentHint = document.createElement("p");
  const modelPreference = document.createElement("p");
  const quota = document.createElement("p");
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
  cardContentInner.className = "card-content-inner";
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
  modelPreference.className = "codex-model-preference";
  modelPreference.id = "codex-model-preference";
  modelPreference.setAttribute("aria-live", "polite");
  modelPreference.textContent = "新建默认：正在读取…";
  quota.className = "codex-quota";
  quota.id = "codex-quota";
  quota.setAttribute("aria-live", "polite");
  quota.textContent = "额度：正在读取…";
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
  panel.append(
    currentHint,
    modelPreference,
    quota,
    sessionsDivider,
    sessionList,
    createActions,
  );
  cardContentInner.append(panel, workspaceDialog);
  cardContent.append(cardContentInner);
  card.append(header, cardContent);
  setupCollapsibleCard(card);

  elements.codexPanel = panel;
  elements.codexWorkspaces = workspaceList;
  elements.codexMessage = currentHint;
  elements.codexModelPreference = modelPreference;
  elements.codexQuota = quota;
  elements.codexSessions = sessionList;
  elements.codexSessionCount = sessionsDividerLabel;
  elements.refreshCodex = refreshButton;
  elements.createCodex = createButton;
  elements.codexWorkspaceDialog = workspaceDialog;

  refreshButton.addEventListener("click", () =>
    loadCodexSessions({ refreshModelPreference: true, refreshQuota: true }),
  );
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

function codexEntryMode(session) {
  if (session.terminal_access_allowed === false) {
    return "quick";
  }
  try {
    const stored = JSON.parse(localStorage.getItem(CODEX_ENTRY_MODE_KEY) || "{}");
    return stored?.[session.id] === "terminal" ? "terminal" : "quick";
  } catch (_error) {
    return "quick";
  }
}

function readCodexDefaultPermission() {
  try {
    const stored = localStorage.getItem(CODEX_DEFAULT_PERMISSION_KEY);
    return CODEX_PERMISSION_OPTIONS.some(([value]) => value === stored)
      ? stored
      : "full-access";
  } catch (_error) {
    return "full-access";
  }
}

function readCodexDefaultModel() {
  try {
    return localStorage.getItem(CODEX_DEFAULT_MODEL_KEY) || null;
  } catch (_error) {
    return null;
  }
}

function readCodexDefaultReasoningEffort() {
  try {
    return localStorage.getItem(CODEX_DEFAULT_REASONING_EFFORT_KEY) || null;
  } catch (_error) {
    return null;
  }
}

function clearCodexModelPreferences() {
  try {
    localStorage.removeItem(CODEX_DEFAULT_MODEL_KEY);
    localStorage.removeItem(CODEX_DEFAULT_REASONING_EFFORT_KEY);
  } catch (_error) {
    // Session creation can still retry with defaults for this request.
  }
}

function saveCodexEntryMode(sessionId, mode) {
  try {
    const stored = JSON.parse(localStorage.getItem(CODEX_ENTRY_MODE_KEY) || "{}");
    const entries = stored && typeof stored === "object" ? stored : {};
    entries[sessionId] = mode === "quick" ? "quick" : "terminal";
    localStorage.setItem(CODEX_ENTRY_MODE_KEY, JSON.stringify(entries));
  } catch (_error) {
    // Storage failure only affects persistence; the current page still updates.
  }
}

function codexEntryLabel(mode) {
  return mode === "quick" ? "快速交互" : "实时终端";
}

function quickInteractionUrl(sessionId) {
  return `/codex/${encodeURIComponent(sessionId)}/quick-interactions/conversation`;
}

function updateCodexEntryButton(session, trigger, mode) {
  if (session.terminal_access_allowed === false) {
    trigger.textContent = codexEntryLabel("quick");
    trigger.dataset.entryMode = "quick";
    trigger.disabled = true;
    trigger.title = "文本优化与翻译 Session 仅支持快速交互";
    trigger.setAttribute("aria-label", trigger.title);
    const main = trigger.closest(".session-item")?.querySelector(".session-enter");
    if (main) {
      main.disabled = false;
      main.title = "";
    }
    return;
  }
  const nextMode = mode === "quick" ? "terminal" : "quick";
  trigger.textContent = codexEntryLabel(mode);
  trigger.dataset.entryMode = mode;
  trigger.title = `点击切换为${codexEntryLabel(nextMode)}`;
  trigger.setAttribute(
    "aria-label",
    `当前交互入口为${codexEntryLabel(mode)}，点击切换为${codexEntryLabel(nextMode)}`,
  );
  const main = trigger.closest(".session-item")?.querySelector(".session-enter");
  if (main) {
    main.disabled = session.quick_interaction_running === true
      && mode === "terminal";
    main.title = main.disabled ? "快速交互正在执行" : "";
  }
}

function toggleCodexEntryMode(session, trigger) {
  if (session.terminal_access_allowed === false) {
    return;
  }
  const currentMode = trigger.dataset.entryMode === "quick"
    ? "quick"
    : "terminal";
  const nextMode = currentMode === "quick" ? "terminal" : "quick";
  saveCodexEntryMode(session.id, nextMode);
  updateCodexEntryButton(session, trigger, nextMode);
}

function codexSessionDisplayTitle(session) {
  const slot = session.weixin_session_slot;
  const slotLabel = Number.isInteger(slot) && slot >= 1 && slot <= 9
    ? `S${slot}`
    : "S";
  return `${slotLabel} · ${session.title || "未命名 Session"}`;
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
    const quickInteractionRunning = session.quick_interaction_running === true;
    const sessionRunning = session.status === "running"
      || quickInteractionRunning
      || session.activity === "working";
    const activitySource = session.activity_source || "none";
    const entryMode = codexEntryMode(session);
    const item = document.createElement("article");
    const main = document.createElement("button");
    const text = document.createElement("span");
    const title = document.createElement("strong");
    const meta = document.createElement("span");
    const path = document.createElement("span");
    const entry = document.createElement("button");
    const actions = document.createElement("div");
    const stop = document.createElement("button");
    const archive = document.createElement("button");
    item.className = "session-item";
    main.className = "session-enter";
    main.type = "button";
    title.textContent = codexSessionDisplayTitle(session);
    title.title = title.textContent;
    const state = quickInteractionRunning
      ? "快速交互 · 执行中"
      : session.activity === "working"
        ? activitySource === "quick"
          ? "快速交互 · 执行中"
          : activitySource === "terminal"
            ? "实时终端 · 执行中"
            : "会话 · 状态未知"
      : session.error
        ? "终端访问异常 · 可重试"
        : session.status === "error"
          ? "会话异常 · 可重试"
          : session.status === "new"
            ? "尚未启动 · 可进入"
            : session.activity === "idle"
              ? "会话 · 等待输入"
              : "会话 · 状态未知";
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
    main.disabled = quickInteractionRunning && entryMode === "terminal";
    if (quickInteractionRunning && entryMode === "terminal") {
      main.title = "快速交互正在执行";
    }
    main.addEventListener("click", () => {
      const selectedMode = entry.dataset.entryMode || entryMode;
      if (selectedMode === "quick") {
        window.location.href = quickInteractionUrl(session.id);
        return;
      }
      enterCodexSession(session.id, main);
    });
    entry.type = "button";
    entry.className = "session-permission session-entry-mode";
    entry.dataset.entryMode = entryMode;
    updateCodexEntryButton(session, entry, entryMode);
    entry.addEventListener("click", () => toggleCodexEntryMode(session, entry));
    stop.type = "button";
    stop.className = "button-secondary session-action";
    stop.textContent = "停止";
    stop.disabled = !sessionRunning;
    stop.title = quickInteractionRunning
      ? "停止当前快速交互和会话"
      : "";
    stop.addEventListener("click", () => stopCodexSession(session.id, stop));
    archive.type = "button";
    archive.className = "button-secondary session-action";
    archive.textContent = "归档";
    archive.setAttribute("aria-haspopup", "dialog");
    archive.setAttribute("aria-controls", "confirmation-dialog");
    archive.disabled = !session.codex_session_id
      || quickInteractionRunning;
    if (quickInteractionRunning) {
      archive.title = "快速交互正在执行";
    }
    archive.addEventListener("click", () =>
      archiveCodexSession(session, archive),
    );
    actions.className = "session-actions";
    actions.append(entry, stop, archive);
    item.append(main, actions);
    elements.codexSessions.append(item);
  });
}

function codexSessionsNewestFirst(sessions) {
  return [...sessions].sort((left, right) => {
    const createdDifference = Date.parse(right.created_at) - Date.parse(left.created_at);
    if (Number.isFinite(createdDifference) && createdDifference !== 0) {
      return createdDifference;
    }
    const leftId = String(left.id);
    const rightId = String(right.id);
    return leftId < rightId ? 1 : leftId > rightId ? -1 : 0;
  });
}

function visibleCodexSessions(sessions) {
  let visible = sessions;
  try {
    if (localStorage.getItem(CODEX_SHOW_TRANSLATION_SESSION_KEY) === "true") {
      return codexSessionsNewestFirst(visible);
    }
  } catch (_error) {
    // A blocked browser preference falls back to hiding the internal Session.
  }
  visible = visible.filter(
    (session) => session.workspace_id !== "weixin-translation",
  );
  return codexSessionsNewestFirst(visible);
}

function codexSessionListUrl() {
  try {
    if (localStorage.getItem(CODEX_SHOW_TRANSLATION_SESSION_KEY) === "true") {
      return "/api/codex/sessions?include_translation=true";
    }
  } catch (_error) {
    // A blocked browser preference uses the API's hidden-by-default list.
  }
  return "/api/codex/sessions";
}

function codexSessionsSignature(sessions) {
  return JSON.stringify(
    sessions.map((session) => [
      session.id,
      session.status,
      session.activity,
      session.activity_source,
      session.quick_interaction_running,
      session.quick_interaction_updated_at,
      session.updated_at,
      session.title,
      session.weixin_session_slot,
      session.cwd,
      session.codex_session_id,
      session.permission_mode,
      session.active_permission_mode,
      session.permission_pending,
    ]),
  );
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
  const visibleSessions = visibleCodexSessions(data.sessions);
  renderCodexSessions(visibleSessions);
  codexSessionSignature = codexSessionsSignature(data.sessions);
  elements.codexSessionCount.textContent = `共 ${visibleSessions.length} 个会话`;
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

function renderCodexQuota(data) {
  if (!elements.codexQuota) {
    return false;
  }
  if (!data || typeof data !== "object") {
    return false;
  }
  const longText = data?.display?.long;
  if (data.status === "available" && typeof longText === "string" && longText) {
    const staleMessage = data.stale && typeof data.message === "string" && data.message
      ? `；${data.message}`
      : "";
    elements.codexQuota.textContent = `${longText}${staleMessage}`;
    return true;
  }
  if (typeof data.message === "string" && data.message) {
    elements.codexQuota.textContent = `额度：${data.message}`;
    return true;
  }
  elements.codexQuota.textContent = "额度：暂不可用。";
  return true;
}

function restoreCodexQuotaCache() {
  try {
    const cached = JSON.parse(sessionStorage.getItem(AI_USAGE_CACHE_KEY) || "null");
    if (renderCodexQuota(cached)) {
      const checkedAt = new Date(cached?.checked_at).getTime();
      codexQuotaLoadedAt = Number.isNaN(checkedAt) || checkedAt > Date.now()
        ? 0
        : checkedAt;
    } else {
      sessionStorage.removeItem(AI_USAGE_CACHE_KEY);
    }
  } catch {
    sessionStorage.removeItem(AI_USAGE_CACHE_KEY);
  }
}

function storeCodexQuotaCache(data) {
  try {
    sessionStorage.setItem(AI_USAGE_CACHE_KEY, JSON.stringify(data));
  } catch {
    // A storage quota failure must not break the live Codex card.
  }
}

function storeCodexCardCache(data) {
  try {
    sessionStorage.setItem(CODEX_CARD_CACHE_KEY, JSON.stringify(data));
  } catch {
    // A storage quota failure must not break the live Codex card.
  }
}

function readCodexPreference(key) {
  try {
    return localStorage.getItem(key) || "";
  } catch (_error) {
    return "";
  }
}

function codexReasoningLabel(effort) {
  return CODEX_REASONING_LABELS[effort] || effort || "跟随模型默认";
}

function renderCodexModelPreference(data) {
  if (!elements.codexModelPreference) {
    return false;
  }
  const models = Array.isArray(data?.models) ? data.models : [];
  const preferredModel = readCodexPreference(CODEX_DEFAULT_MODEL_KEY);
  const preferredEffort = readCodexPreference(CODEX_DEFAULT_REASONING_EFFORT_KEY);
  const selectedModelId = preferredModel || data?.default_model;
  const model = models.find((item) => item?.id === selectedModelId);
  if (!model || typeof model.name !== "string") {
    elements.codexModelPreference.textContent = "新建默认：暂时无法确认模型与等级";
    return false;
  }
  const levels = Array.isArray(model.levels) ? model.levels : [];
  const selectedEffort = preferredEffort
    || data?.default_reasoning_effort
    || model.default_level;
  const effort = levels.some((level) => level?.id === selectedEffort)
    ? selectedEffort
    : model.default_level;
  const modelAndEffort = `${model.name} · ${codexReasoningLabel(effort)}`;
  elements.codexModelPreference.textContent = `新建默认：${modelAndEffort}`;
  elements.codexModelPreference.dataset.hasValue = "true";
  return true;
}

function restoreCodexModelPreferenceCache() {
  try {
    const cached = JSON.parse(
      sessionStorage.getItem(CODEX_MODEL_PREFERENCE_CACHE_KEY) || "null",
    );
    if (!renderCodexModelPreference(cached)) {
      sessionStorage.removeItem(CODEX_MODEL_PREFERENCE_CACHE_KEY);
    }
  } catch {
    sessionStorage.removeItem(CODEX_MODEL_PREFERENCE_CACHE_KEY);
  }
}

function storeCodexModelPreferenceCache(data) {
  try {
    sessionStorage.setItem(CODEX_MODEL_PREFERENCE_CACHE_KEY, JSON.stringify(data));
  } catch {
    // A storage quota failure must not break the live Codex card.
  }
}

async function loadCodexModelPreference() {
  if (!elements.codexModelPreference || !hasProtectedAccess()) {
    return;
  }
  const requestVersion = accessVersion;
  try {
    const data = await apiFetch("/api/codex/models");
    if (requestVersion === accessVersion) {
      if (renderCodexModelPreference(data)) {
        storeCodexModelPreferenceCache(data);
      }
    }
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return;
    }
    if (elements.codexModelPreference?.dataset.hasValue !== "true") {
      elements.codexModelPreference.textContent = "新建默认：暂时无法确认模型与等级";
    }
    handleAccessError(error);
  }
}

async function loadCodexQuota({ force = false } = {}) {
  if (!elements.codexQuota || !hasProtectedAccess()) {
    return;
  }
  if (!force && Date.now() - codexQuotaLoadedAt < CODEX_QUOTA_REFRESH_MS) {
    return;
  }
  if (codexQuotaLoadPromise) {
    return codexQuotaLoadPromise;
  }
  const requestVersion = accessVersion;
  const loadPromise = (async () => {
    try {
      const data = await window.ChubTheme.loadAiUsage({ force });
      if (requestVersion !== accessVersion || !elements.codexQuota) {
        return;
      }
      if (renderCodexQuota(data)) {
        storeCodexQuotaCache(data);
        codexQuotaLoadedAt = Date.now();
      }
    } catch (error) {
      if (requestVersion === accessVersion) {
        handleAccessError(error);
      }
    }
  })();
  codexQuotaLoadPromise = loadPromise;
  try {
    return await loadPromise;
  } finally {
    if (codexQuotaLoadPromise === loadPromise) {
      codexQuotaLoadPromise = null;
    }
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
  let usedCodexDefaults = false;
  try {
    const createRequest = (model, reasoningEffort) => apiFetch(
      "/api/codex/sessions",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workspace_id: workspaceId,
          permission_mode: readCodexDefaultPermission(),
          model,
          reasoning_effort: reasoningEffort,
        }),
      },
    );
    const preferredModel = readCodexDefaultModel();
    const preferredEffort = readCodexDefaultReasoningEffort();
    try {
      await createRequest(preferredModel, preferredEffort);
    } catch (error) {
      const preferenceErrors = new Set([
        "codex_model_catalog_unavailable",
        "codex_model_unavailable",
        "codex_reasoning_effort_requires_model",
        "codex_reasoning_effort_unsupported",
      ]);
      if (
        (!preferredModel && !preferredEffort)
        || !preferenceErrors.has(error.code)
      ) {
        throw error;
      }
      clearCodexModelPreferences();
      await createRequest(null, null);
      usedCodexDefaults = true;
    }
    await loadCodexSessions({ force: true });
    elements.codexWorkspaceDialog?.close();
    if (usedCodexDefaults) {
      setMessage(
        elements.codexMessage,
        "原模型或等级当前不可用，已按 Codex 默认创建会话。",
        "success",
      );
    }
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

async function archiveCodexSession(session, button) {
  if (!elements.codexMessage) {
    return;
  }
  const title = session.title?.trim() || "未命名 Session";
  await showConfirmationDialog({
    title: "归档 Session",
    description: `归档“${title}”后，该 Session 将从活动列表移除，正在运行的实时终端会停止；如已分配微信槽位，槽位也会释放。Chub 页面暂不提供恢复入口。`,
    confirmLabel: "确认归档",
    pendingLabel: "归档中…",
    errorMessage: "Session 归档失败。",
    onConfirm: async () => {
      setMessage(elements.codexMessage, "");
      setCodexButtonBusy(button, true);
      beginCodexMutation();
      try {
        await apiFetch(`/api/codex/sessions/${session.id}/archive`, {
          method: "POST",
        });
        await loadCodexSessions({ force: true });
      } catch (error) {
        if (handleAccessError(error)) {
          return;
        }
        throw error;
      } finally {
        setCodexButtonBusy(button, false);
        endCodexMutation();
      }
    },
  });
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
    || !hasProtectedAccess()
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
    authenticated: hasProtectedAccess(),
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
  const refreshModelPreference = options?.refreshModelPreference === true;
  const refreshQuota = options?.refreshQuota === true;
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
    if (!hasProtectedAccess() || !elements.codexPanel) {
      return;
    }
  }
  const requestVersion = accessVersion;
  if (!background && elements.refreshCodex) {
    elements.refreshCodex.disabled = true;
  }
  const loadPromise = (async () => {
    try {
      if (!background) {
        if (refreshModelPreference || !elements.codexModelPreference.dataset.loaded) {
          void loadCodexModelPreference().finally(() => {
            if (elements.codexModelPreference) {
              elements.codexModelPreference.dataset.loaded = "true";
            }
          });
        }
        void loadCodexQuota({ force: refreshQuota });
      }
      const data = await apiFetch(codexSessionListUrl());
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
