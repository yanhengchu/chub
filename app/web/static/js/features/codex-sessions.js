"use strict";

const CODEX_CARD_CACHE_KEY = "hub.codexCardCache";
const CODEX_MODEL_PREFERENCE_CACHE_KEY = "hub.codexModelPreferenceCache";
const CODEX_DEFAULT_PERMISSION_KEY = "hub.codexDefaultPermission.v1";
const CODEX_DEFAULT_MODEL_KEY = "hub.codexDefaultModel.v1";
const CODEX_DEFAULT_REASONING_EFFORT_KEY = "hub.codexDefaultReasoningEffort.v1";
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
let codexMutationCount = 0;
const codexDocument = typeof document === "undefined" ? null : document;
const codexRenameDialog = codexDocument?.querySelector("#codex-rename-dialog");
const codexRenameForm = codexDocument?.querySelector("#codex-rename-form");
const codexRenameInput = codexDocument?.querySelector("#codex-rename-input");
const codexRenameMessage = codexDocument?.querySelector("#codex-rename-message");
const codexRenameClose = codexDocument?.querySelector("#codex-rename-close");
const codexRenameCancel = codexDocument?.querySelector("#codex-rename-cancel");
const codexRenameConfirm = codexDocument?.querySelector("#codex-rename-confirm");
let codexRenameTarget = null;
let codexRenameButton = null;
let codexRenamePending = false;

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
  const sessionsTitle = document.createElement("h3");
  const refreshButton = document.createElement("button");
  const createActions = document.createElement("div");
  const createButton = document.createElement("button");
  const workspaceDialog = document.createElement("dialog");
  const workspaceDialogSurface = document.createElement("div");
  const workspaceDialogHeader = document.createElement("div");
  const workspaceDialogTitle = document.createElement("h3");
  const workspaceDialogClose = document.createElement("button");
  const sessionModeField = document.createElement("fieldset");
  const sessionModeLegend = document.createElement("legend");
  const sessionModeControl = document.createElement("div");
  const sessionModeQuickLabel = document.createElement("label");
  const sessionModeTerminalLabel = document.createElement("label");
  const sessionModeQuick = document.createElement("input");
  const sessionModeTerminal = document.createElement("input");
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
  kicker.textContent = "AI";
  title.id = "codex-title";
  title.textContent = "会话工作台";
  description.className = "section-description";
  description.textContent = "统一管理实时终端和快速交互会话。";
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
  sessionsTitle.className = "card-group-title codex-sessions-title";
  sessionsTitle.textContent = "正在读取会话";
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
  sessionModeField.className = "codex-session-mode-field";
  sessionModeLegend.textContent = "会话类型";
  sessionModeControl.className = "codex-session-mode-control";
  sessionModeQuick.type = "radio";
  sessionModeQuick.name = "codex-session-mode";
  sessionModeQuick.value = "quick";
  sessionModeQuick.checked = true;
  sessionModeTerminal.type = "radio";
  sessionModeTerminal.name = "codex-session-mode";
  sessionModeTerminal.value = "terminal";
  sessionModeQuickLabel.className = "codex-session-mode-option";
  const sessionModeQuickText = document.createElement("span");
  sessionModeQuickText.textContent = "快速交互";
  sessionModeQuickLabel.append(sessionModeQuick, sessionModeQuickText);
  sessionModeTerminalLabel.className = "codex-session-mode-option";
  const sessionModeTerminalText = document.createElement("span");
  sessionModeTerminalText.textContent = "实时终端";
  sessionModeTerminalLabel.append(sessionModeTerminal, sessionModeTerminalText);
  sessionModeControl.append(sessionModeQuickLabel, sessionModeTerminalLabel);
  sessionModeField.append(sessionModeLegend, sessionModeControl);
  workspaceDialogDescription.className = "section-description";
  workspaceDialogDescription.textContent = "首页列出三个常用目录；其他目录启动的 Codex 会话会自动出现在列表中。";
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
  createActions.append(createButton);
  workspaceDialogHeader.append(workspaceDialogTitle, workspaceDialogClose);
  workspaceDialogSurface.append(
    workspaceDialogHeader,
    sessionModeField,
    workspaceDialogDescription,
    workspaceList,
  );
  workspaceDialog.append(workspaceDialogSurface);
  panel.append(
    currentHint,
    modelPreference,
    quota,
    sessionsTitle,
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
  elements.codexSessionCount = sessionsTitle;
  elements.refreshCodex = refreshButton;
  elements.createCodex = createButton;
  elements.codexWorkspaceDialog = workspaceDialog;
  elements.codexSessionModeControl = sessionModeControl;

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
      () => createCodexSession(workspace.id, button, readCodexSessionMode()),
    );
    elements.codexWorkspaces.append(button);
  });
  if (elements.createCodex) {
    elements.createCodex.disabled = !available || !hasAvailableWorkspace;
  }
}

function readCodexSessionMode() {
  const selected = document.querySelector(
    'input[name="codex-session-mode"]:checked',
  );
  return selected?.value === "terminal" ? "terminal" : "quick";
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

function quickInteractionUrl(sessionId) {
  return `/codex/${encodeURIComponent(sessionId)}/quick-interactions/conversation`;
}

function codexSessionDisplayTitle(session) {
  const slot = session.weixin_session_slot;
  const slotLabel = Number.isInteger(slot) && slot >= 1 && slot <= 9
    ? `S${slot}`
    : "";
  const modeLabel = session.session_mode === "terminal" ? "终端" : "快速";
  const slotPrefix = slotLabel ? `${slotLabel} · ` : "";
  return `${modeLabel} · ${slotPrefix}${session.title || "未命名 Session"}`;
}

function sessionUsagePresentation(session) {
  const usage = session.usage || {};
  const owner = usage.owner || "none";
  const phase = usage.phase || "idle";
  if (owner === "external") {
    return {
      label: "其他应用 · 正在使用",
      blocked: true,
      title: "This is open in another app, close it there to continue here.",
    };
  }
  if (owner === "unknown") {
    return {
      label: "占用状态未知 · 请刷新",
      blocked: true,
      title: "无法确认 Session 占用状态，请刷新后重试。",
    };
  }
  if (owner === "quick_worker") {
    return {
      label: "快速交互 · 执行中",
      blocked: false,
      title: "",
    };
  }
  if (owner === "terminal") {
    return {
      label: phase === "running"
        ? "实时终端 · 执行中"
        : phase === "idle"
          ? "实时终端 · 等待输入"
          : "活动状态未知 · 请刷新",
      blocked: false,
      title: "",
    };
  }
  const activitySource = session.activity_source || "none";
  const label = session.quick_interaction_running === true
    ? "快速交互 · 执行中"
    : session.activity === "working"
      ? activitySource === "quick"
        ? "快速交互 · 执行中"
        : activitySource === "terminal"
          ? "实时终端 · 执行中"
          : "活动状态未知 · 请刷新"
    : session.error
      ? "终端连接异常 · 可重试"
      : session.status === "error"
        ? "会话异常 · 可重试"
        : session.status === "new"
          ? "尚未启动 · 可进入"
          : session.activity === "idle"
            ? sessionModeIdleLabel(session)
            : "活动状态未知 · 请刷新";
  return { label, blocked: false, title: "" };
}

function sessionModeIdleLabel(session) {
  return session.session_mode === "quick"
    ? "快速交互 · 待输入"
    : "实时终端 · 等待输入";
}

function sessionEntryBlocked(session, usagePresentation) {
  return session.session_mode === "terminal" && usagePresentation.blocked;
}

function sessionStopReady(session) {
  const owner = session.usage?.owner || "none";
  const phase = session.usage?.phase || "unknown";
  return (owner === "terminal" && phase === "running")
    || (
      owner === "quick_worker"
      && ["running", "waiting_result"].includes(phase)
    );
}

function sessionArchiveBlockReason(session) {
  const owner = session.usage?.owner || "none";
  const phase = session.usage?.phase || "unknown";
  if (owner === "external") {
    return "This is open in another app, close it there to continue here.";
  }
  if (
    (owner === "terminal" && phase === "running")
    || (
      owner === "quick_worker"
      && ["running", "waiting_result"].includes(phase)
    )
  ) {
    return "Session 当前正在执行，请等待任务结束后再归档。";
  }
  return "";
}

function setCodexRenamePending(pending) {
  codexRenamePending = pending;
  codexRenameForm?.toggleAttribute("aria-busy", pending);
  if (codexRenameInput) {
    codexRenameInput.disabled = pending;
  }
  if (codexRenameClose) {
    codexRenameClose.disabled = pending;
  }
  if (codexRenameCancel) {
    codexRenameCancel.disabled = pending;
  }
  if (codexRenameConfirm) {
    codexRenameConfirm.disabled = pending;
  }
  if (codexRenameButton?.isConnected) {
    codexRenameButton.disabled = pending;
    codexRenameButton.toggleAttribute("aria-busy", pending);
  }
}

function closeCodexRenameDialog(force = false) {
  if ((!codexRenamePending || force) && codexRenameDialog?.open) {
    codexRenameDialog.close();
    codexRenameTarget = null;
    codexRenameButton = null;
  }
}

function openCodexRenameDialog(session, button) {
  if (codexRenamePending || !codexRenameDialog || !codexRenameInput) {
    return;
  }
  codexRenameTarget = session;
  codexRenameButton = button;
  codexRenameInput.value = session.title?.trim() || "";
  setMessage(codexRenameMessage, "");
  codexRenameDialog.showModal();
  window.requestAnimationFrame(() => {
    codexRenameInput.focus();
    codexRenameInput.select();
  });
}

async function renameCodexSession(event) {
  event.preventDefault();
  if (codexRenamePending || !codexRenameTarget || !codexRenameInput) {
    return;
  }
  const title = codexRenameInput.value.trim();
  if (!title) {
    setMessage(codexRenameMessage, "请输入 Session 标题。", "error");
    codexRenameInput.focus();
    return;
  }
  setCodexRenamePending(true);
  beginCodexMutation();
  try {
    await apiFetch(
      `/api/codex/sessions/${encodeURIComponent(codexRenameTarget.id)}/title`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      },
    );
    await loadCodexSessions({ force: true });
    closeCodexRenameDialog(true);
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(
        codexRenameMessage,
        formatApiErrorMessage(error, "Session 重命名失败。"),
        "error",
      );
    }
  } finally {
    setCodexRenamePending(false);
    if (codexRenameButton?.isConnected) {
      codexRenameButton.disabled = false;
      codexRenameButton.removeAttribute("aria-busy");
    }
    endCodexMutation();
  }
}

if (codexRenameForm) {
  codexRenameForm.addEventListener("submit", renameCodexSession);
}
codexRenameClose?.addEventListener("click", closeCodexRenameDialog);
codexRenameCancel?.addEventListener("click", closeCodexRenameDialog);
codexRenameDialog?.addEventListener("click", (event) => {
  if (event.target === codexRenameDialog) {
    closeCodexRenameDialog();
  }
});
codexRenameDialog?.addEventListener("cancel", (event) => {
  if (codexRenamePending) {
    event.preventDefault();
  }
});

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
    const usagePresentation = sessionUsagePresentation(session);
    const usageBlocked = usagePresentation.blocked;
    const deleteBlockReason = session.usage?.owner === "external"
      ? usagePresentation.title
      : "";
    const archiveBlockReason = sessionArchiveBlockReason(session);
    const entryBlocked = sessionEntryBlocked(session, usagePresentation);
    const stopReady = sessionStopReady(session);
    const item = document.createElement("article");
    const main = document.createElement("button");
    const text = document.createElement("span");
    const title = document.createElement("strong");
    const meta = document.createElement("span");
    const path = document.createElement("span");
    const actions = document.createElement("div");
    const rename = document.createElement("button");
    const stop = document.createElement("button");
    const archive = document.createElement("button");
    const remove = document.createElement("button");
    item.className = "session-item";
    main.className = "session-enter";
    main.type = "button";
    title.textContent = codexSessionDisplayTitle(session);
    title.title = title.textContent;
    const state = usagePresentation.label;
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
    main.addEventListener("click", () => {
      if (session.session_mode === "quick") {
        window.location.href = quickInteractionUrl(session.id);
        return;
      }
      enterCodexSession(session.id, main);
    });
    main.disabled = entryBlocked;
    main.title = entryBlocked ? usagePresentation.title : "";
    stop.type = "button";
    stop.className = "button-secondary session-action";
    stop.textContent = "停止";
    stop.disabled = !stopReady || usageBlocked;
    stop.title = quickInteractionRunning
      ? "停止当前快速交互和会话"
      : stopReady
        ? "停止当前执行"
        : "当前没有正在执行的任务";
    if (usageBlocked) {
      stop.title = usagePresentation.title;
    }
    stop.addEventListener("click", () => stopCodexSession(session.id, stop));
    rename.type = "button";
    rename.className = "button-secondary session-action";
    rename.textContent = "重命名";
    rename.setAttribute("aria-haspopup", "dialog");
    rename.setAttribute("aria-controls", "codex-rename-dialog");
    rename.title = "重命名 Session";
    rename.setAttribute("aria-label", "重命名 Session");
    rename.disabled = session.workspace_id === "weixin-translation";
    rename.addEventListener("click", () => openCodexRenameDialog(session, rename));
    archive.type = "button";
    archive.className = "button-secondary session-action";
    archive.textContent = "归档";
    archive.setAttribute("aria-haspopup", "dialog");
    archive.setAttribute("aria-controls", "confirmation-dialog");
    archive.disabled = !session.can_archive || Boolean(archiveBlockReason);
    archive.addEventListener("click", () =>
      archiveCodexSession(session, archive),
    );
    remove.type = "button";
    remove.className = "button-danger session-action";
    remove.textContent = "删除";
    remove.setAttribute("aria-haspopup", "dialog");
    remove.setAttribute("aria-controls", "confirmation-dialog");
    remove.title = "删除 Session";
    remove.setAttribute("aria-label", "删除 Session");
    remove.disabled = Boolean(deleteBlockReason);
    if (archiveBlockReason) {
      archive.title = archiveBlockReason;
    }
    if (deleteBlockReason) {
      remove.title = deleteBlockReason;
    }
    remove.addEventListener("click", () =>
      deleteCodexSession(session, remove),
    );
    actions.className = "session-actions";
    actions.append(rename, stop, archive, remove);
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
  const visible = sessions.filter(
    (session) => session.workspace_id !== "weixin-translation",
  );
  return codexSessionsNewestFirst(visible);
}

function codexSessionListUrl() {
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
      session.session_mode,
      session.weixin_session_slot,
      session.cwd,
      session.can_archive,
      session.usage?.native_session_present,
      session.usage?.owner,
      session.usage?.phase,
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
        missing || data.unavailable_reason || "会话工作台不可用。",
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
  elements.codexQuota.classList.remove(
    "codex-quota-compact",
    "codex-quota-today-complete",
  );
  const longText = data?.display?.long;
  if (data.status === "available" && typeof longText === "string" && longText) {
    const staleMessage = data.stale && typeof data.message === "string" && data.message
      ? `；${data.message}`
      : "";
    const separatorText = " · ";
    const todayIndex = longText.indexOf(`${separatorText}Today `);
    const resetIndex = longText.indexOf(`${separatorText}Resets `);
    if (todayIndex < 0 && resetIndex < 0) {
      elements.codexQuota.textContent = `${longText}${staleMessage}`;
      return true;
    }

    const hasLimit = (data.weekly?.limit_usd ?? null) !== null;
    const hasUsed = (data.today?.used_usd ?? null) !== null;
    const hasTokens = (data.today?.tokens ?? null) !== null;
    elements.codexQuota.classList.toggle(
      "codex-quota-compact",
      !hasLimit && !hasUsed,
    );
    elements.codexQuota.classList.toggle(
      "codex-quota-today-complete",
      hasUsed && hasTokens,
    );

    const firstSeparatorIndex = todayIndex >= 0 ? todayIndex : resetIndex;
    const weekly = document.createElement("span");
    const content = [weekly];
    weekly.className = "codex-quota-part codex-quota-weekly-part";
    weekly.textContent = longText.slice(0, firstSeparatorIndex);

    const appendPart = (kind, text) => {
      const group = document.createElement("span");
      const separator = document.createElement("span");
      const lineBreak = document.createElement("br");
      const part = document.createElement("span");
      group.className = `codex-quota-group codex-quota-${kind}-group`;
      separator.className = `codex-quota-separator codex-quota-${kind}-separator`;
      separator.textContent = separatorText;
      lineBreak.className = `codex-quota-break codex-quota-${kind}-break`;
      part.className = `codex-quota-part codex-quota-${kind}-part`;
      part.textContent = text;
      group.append(separator, part);
      content.push(lineBreak, group);
    };

    if (todayIndex >= 0) {
      const todayEnd = resetIndex > todayIndex ? resetIndex : longText.length;
      appendPart(
        "today",
        longText.slice(todayIndex + separatorText.length, todayEnd),
      );
    }
    if (resetIndex >= 0) {
      appendPart(
        "reset",
        `${longText.slice(resetIndex + separatorText.length)}${staleMessage}`,
      );
    } else if (staleMessage) {
      content.push(document.createTextNode(staleMessage));
    }
    elements.codexQuota.replaceChildren(...content);
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
  renderCodexQuota(window.ChubAiUsage?.current());
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
  const requestVersion = accessVersion;
  try {
    const data = await window.ChubAiUsage.load({ force });
    if (requestVersion !== accessVersion || !elements.codexQuota) {
      return;
    }
    renderCodexQuota(data);
  } catch (error) {
    if (requestVersion === accessVersion) {
      handleAccessError(error);
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

async function createCodexSession(workspaceId, button, sessionMode = "quick") {
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
          session_mode: sessionMode === "terminal" ? "terminal" : "quick",
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
      setMessage(
        elements.codexMessage,
        formatApiErrorMessage(error, "会话创建失败。"),
        "error",
      );
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
      setMessage(
        elements.codexMessage,
        formatApiErrorMessage(error, "打开失败。"),
        "error",
      );
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
      setMessage(
        elements.codexMessage,
        formatApiErrorMessage(error, "停止失败。"),
        "error",
      );
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
    description: `归档“${title}”后，该 Session 将从活动列表移除；如已分配微信槽位，槽位也会释放。执行中的 Session 需要先等待任务结束。Chub 页面暂不提供恢复入口。`,
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

async function deleteCodexSession(session, button) {
  if (!elements.codexMessage) {
    return;
  }
  const title = session.title?.trim() || "未命名 Session";
  await showConfirmationDialog({
    title: "删除 Session",
    description: `删除“${title}”后，该 Session 将永久删除，无法恢复；执行中的 Quick Worker 任务会在删除过程中先停止，实时终端也会关闭；如已分配微信槽位，槽位也会释放。`,
    confirmLabel: "确认删除",
    pendingLabel: "删除中…",
    errorMessage: "Session 删除失败。",
    onConfirm: async () => {
      setMessage(elements.codexMessage, "");
      setCodexButtonBusy(button, true);
      beginCodexMutation();
      try {
        await apiFetch(`/api/codex/sessions/${session.id}`, {
          method: "DELETE",
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
        setMessage(
          elements.codexMessage,
          formatApiErrorMessage(error, "会话读取失败。"),
          "error",
        );
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
