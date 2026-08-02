"use strict";

const CODEX_CARD_CACHE_KEY = "hub.codexCardCache";
const CODEX_ENTRY_MODE_KEY = "hub.codexEntryMode.v1";
const QUICK_INTERACTION_VIEW_KEY = "hub.quickInteractionView.v1";
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
  cardContentInner.append(panel, workspaceDialog, permissionDialog);
  cardContent.append(cardContentInner);
  card.append(header, cardContent);
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

function codexEntryMode(session) {
  if (!session.codex_session_id) {
    return "terminal";
  }
  try {
    const stored = JSON.parse(localStorage.getItem(CODEX_ENTRY_MODE_KEY) || "{}");
    return stored?.[session.id] === "quick" ? "quick" : "terminal";
  } catch (_error) {
    return "terminal";
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

function quickInteractionView() {
  try {
    return localStorage.getItem(QUICK_INTERACTION_VIEW_KEY) === "conversation"
      ? "conversation"
      : "task";
  } catch (_error) {
    return "task";
  }
}

function quickInteractionUrl(sessionId) {
  const base = `/codex/${encodeURIComponent(sessionId)}/quick-interactions`;
  return quickInteractionView() === "conversation"
    ? `${base}/conversation`
    : base;
}

function updateCodexEntryButton(session, trigger, mode) {
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
  if (!session.codex_session_id) {
    return;
  }
  const currentMode = trigger.dataset.entryMode === "quick"
    ? "quick"
    : "terminal";
  const nextMode = currentMode === "quick" ? "terminal" : "quick";
  saveCodexEntryMode(session.id, nextMode);
  updateCodexEntryButton(session, trigger, nextMode);
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
    const llmInteractionRunning = session.llm_interaction_running === true;
    const activitySource = session.activity_source || "none";
    const entryMode = codexEntryMode(session);
    const item = document.createElement("article");
    const main = document.createElement("button");
    const text = document.createElement("span");
    const title = document.createElement("strong");
    const meta = document.createElement("span");
    const path = document.createElement("span");
    const permissionPanel = document.createElement("div");
    const permission = document.createElement("button");
    const entry = document.createElement("button");
    const permissionPending = document.createElement("span");
    const actions = document.createElement("div");
    const stop = document.createElement("button");
    const archive = document.createElement("button");
    item.className = "session-item";
    main.className = "session-enter";
    main.type = "button";
    title.textContent = session.title || session.workspace_name;
    title.title = title.textContent;
    const state = quickInteractionRunning
      ? "快速交互 · 执行中"
      : llmInteractionRunning
        ? "Amazon Bedrock API · 回答中"
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
          : llmInteractionRunning
            ? session.llm_interaction_updated_at
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
    permission.disabled = quickInteractionRunning;
    if (quickInteractionRunning) {
      permission.title = "快速交互正在执行";
    }
    permission.addEventListener("click", () => openCodexPermissionDialog(session));
    entry.type = "button";
    entry.className = "session-permission session-entry-mode";
    entry.dataset.entryMode = entryMode;
    entry.disabled = !session.codex_session_id;
    if (!session.codex_session_id) {
      entry.textContent = "实时终端";
      entry.setAttribute("aria-label", "当前交互入口为实时终端");
      entry.title = "会话启动后可以选择快速交互";
    } else {
      updateCodexEntryButton(session, entry, entryMode);
    }
    entry.addEventListener("click", () => toggleCodexEntryMode(session, entry));
    permissionPending.className = "session-permission-pending";
    permissionPending.textContent = session.permission_pending
      ? `待切换至${codexPermissionLabel(configuredPermission)}`
      : "";
    permissionPending.hidden = !session.permission_pending;
    permissionPanel.append(permission, entry, permissionPending);
    stop.type = "button";
    stop.className = "button-secondary session-action";
    stop.textContent = "停止";
    stop.disabled = session.status !== "running" || quickInteractionRunning;
    if (quickInteractionRunning) {
      stop.title = "快速交互正在执行";
    }
    stop.addEventListener("click", () => stopCodexSession(session.id, stop));
    archive.type = "button";
    archive.className = "button-secondary session-action";
    archive.textContent = "归档";
    archive.disabled = !session.codex_session_id
      || quickInteractionRunning
      || llmInteractionRunning;
    if (quickInteractionRunning || llmInteractionRunning) {
      archive.title = llmInteractionRunning
        ? "Amazon Bedrock API 正在回答"
        : "快速交互正在执行";
    }
    archive.addEventListener("click", () =>
      archiveCodexSession(session.id, archive),
    );
    actions.className = "session-actions";
    actions.append(stop, archive);
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
      session.activity_source,
      session.quick_interaction_running,
      session.llm_interaction_running,
      session.quick_interaction_updated_at,
      session.llm_interaction_updated_at,
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
