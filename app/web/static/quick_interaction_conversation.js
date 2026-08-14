"use strict";

let conversationSessionId = document.body.dataset.sessionId;
const {
  canSubmit: canSubmitConversation,
  clearSessionModelPreferences: clearConversationSessionModelPreferences,
  createClient: createConversationClient,
  firstSessionAfterArchive: firstConversationSessionAfterArchive,
  formatTime: formatConversationTime,
  isRetryableRequestError: isRetryableConversationError,
  pollDelay: conversationPollDelay,
  readPageSize: readConversationPageSize,
  readSessionCreationPreferences: readConversationSessionCreationPreferences,
  readToken: readConversationToken,
  sessionNavigationMode: conversationSessionNavigationMode,
  sessionSwitcherEntries: conversationSessionEntries,
  sessionSwitcherLabels: conversationSessionLabels,
  sessionSwitcherStatus: conversationSessionStatus,
  shouldPoll: shouldPollConversation,
  shouldRetrySessionCreationWithDefaults: shouldRetryConversationCreationWithDefaults,
  statusText: conversationStatusText,
  submissionBlockReason: conversationSubmissionBlockReason,
} = window.QuickInteractionCore;
const conversationToken = readConversationToken();
let conversationClient = createConversationClient({
  token: conversationToken,
  sessionId: conversationSessionId,
});
const conversationForm = document.querySelector("#conversation-form");
const conversationPrompt = document.querySelector("#conversation-prompt");
const conversationSubmit = document.querySelector("#conversation-submit");
const conversationSubmitMessage = document.querySelector("#conversation-submit-message");
const conversationHistoryMessage = document.querySelector("#conversation-history-message");
const conversationScroll = document.querySelector("#conversation-scroll");
const conversationFeed = document.querySelector("#conversation-feed");
const conversationLoadEarlier = document.querySelector("#conversation-load-earlier");
const conversationJumpLatest = document.querySelector("#conversation-jump-latest");
const conversationSessionNavigation = document.querySelector("#conversation-session-navigation");
const conversationSessionCreate = document.querySelector("#conversation-session-create");
const conversationSessionSwitcher = document.querySelector("#conversation-session-switcher");
const conversationSessionTitleRow = document.querySelector("#conversation-session-title-row");
const conversationSessionTitle = document.querySelector("#conversation-session-title");
const conversationSessionRename = document.querySelector("#conversation-session-rename");
const conversationSessionArchive = document.querySelector("#conversation-session-archive");
const conversationCreateDialog = document.querySelector("#conversation-create-dialog");
const conversationCreateSurface = document.querySelector("#conversation-create-surface");
const conversationCreateWorkspaces = document.querySelector("#conversation-create-workspaces");
const conversationCreateMessage = document.querySelector("#conversation-create-message");
const conversationCreateClose = document.querySelector("#conversation-create-close");
const conversationRenameDialog = document.querySelector("#conversation-rename-dialog");
const conversationRenameForm = document.querySelector("#conversation-rename-form");
const conversationRenameInput = document.querySelector("#conversation-rename-input");
const conversationRenameMessage = document.querySelector("#conversation-rename-message");
const conversationRenameClose = document.querySelector("#conversation-rename-close");
const conversationRenameCancel = document.querySelector("#conversation-rename-cancel");
const conversationRenameConfirm = document.querySelector("#conversation-rename-confirm");
const conversationArchiveDialog = document.querySelector("#conversation-archive-dialog");
const conversationArchiveForm = document.querySelector("#conversation-archive-form");
const conversationArchiveDescription = document.querySelector("#conversation-archive-description");
const conversationArchiveMessage = document.querySelector("#conversation-archive-message");
const conversationArchiveClose = document.querySelector("#conversation-archive-close");
const conversationArchiveCancel = document.querySelector("#conversation-archive-cancel");
const conversationArchiveConfirm = document.querySelector("#conversation-archive-confirm");
const CONVERSATION_PAGE_SIZE = readConversationPageSize();
let conversationPollTimer = null;
let conversationPollFailureCount = 0;
let conversationLoadQueue = Promise.resolve();
let conversationGeneration = 0;
let conversationLoadEarlierPending = false;
let conversationTasks = [];
let conversationTotal = 0;
let conversationHasEarlier = false;
let conversationHistoryExpanded = false;
let conversationInitialized = false;
let conversationActive = false;
let conversationSession = null;
let conversationSessions = [];
let conversationCreationAvailable = false;
let conversationCreationPending = false;
let conversationWorkspaces = [];
let conversationConfirmStopUnknownTerminal = false;
let conversationSessionSwitcherSignature = "";
let conversationRenamePending = false;
let conversationArchivePending = false;

function handleConversationSessionSwitch(event) {
  const button = event.target.closest?.(".conversation-session-switch");
  if (!button || !conversationSessionSwitcher.contains(button)) {
    return;
  }
  const mode = conversationSessionNavigationMode({
    button: event.button,
    current: button.getAttribute("aria-current") === "page",
    altKey: event.altKey,
    ctrlKey: event.ctrlKey,
    metaKey: event.metaKey,
    shiftKey: event.shiftKey,
  });
  if (mode === "ignore") {
    event.preventDefault();
    return;
  }
  if (mode === "default") {
    return;
  }
  event.preventDefault();
  const url = button.dataset.sessionUrl;
  if (mode === "new-tab") {
    window.open(url, "_blank", "noopener");
    return;
  }
  switchConversationSession(button.dataset.sessionId, url);
}

function conversationDraftKey() {
  return `hub.quickInteractionDraft.v1.${conversationSessionId}`;
}

function renderConversationSessionPreview(session) {
  const title = typeof session?.title === "string" ? session.title.trim() : "";
  const displayTitle = title || "未命名 Session";
  const renameAllowed = session?.workspace_id !== "weixin-translation";
  const loadingLabel = "正在读取 Session 状态";
  conversationSessionTitle.textContent = displayTitle;
  conversationSessionTitle.title = displayTitle;
  document.title = `${displayTitle} · 快速交互`;
  conversationSessionTitleRow.hidden = false;
  conversationSessionTitleRow.setAttribute("aria-busy", "true");
  conversationSessionRename.hidden = !renameAllowed;
  conversationSessionRename.disabled = true;
  conversationSessionRename.title = loadingLabel;
  conversationSessionRename.setAttribute("aria-label", loadingLabel);
  conversationSessionArchive.disabled = true;
  conversationSessionArchive.title = loadingLabel;
  conversationSessionArchive.setAttribute("aria-label", loadingLabel);
}

function resetConversationSessionView(sessionPreview) {
  conversationPollFailureCount = 0;
  conversationLoadQueue = Promise.resolve();
  conversationLoadEarlierPending = false;
  conversationTasks = [];
  conversationTotal = 0;
  conversationHasEarlier = false;
  conversationHistoryExpanded = false;
  conversationInitialized = false;
  conversationActive = false;
  conversationSession = null;
  conversationConfirmStopUnknownTerminal = false;
  conversationFeed.replaceChildren();
  conversationLoadEarlier.hidden = true;
  conversationLoadEarlier.disabled = false;
  conversationJumpLatest.hidden = true;
  renderConversationSessionPreview(sessionPreview);
  conversationPrompt.value = readConversationDraft();
  conversationPrompt.disabled = true;
  conversationSubmit.disabled = true;
  conversationForm.setAttribute("aria-busy", "true");
  showConversationMessage(conversationHistoryMessage, "");
  showConversationMessage(conversationSubmitMessage, "");
  resizeConversationPrompt();
  updateConversationComposerActions();
}

function switchConversationSession(sessionId, url, sessionPreview = null) {
  if (!sessionId || sessionId === conversationSessionId) {
    return;
  }
  const preview = sessionPreview
    || conversationSessions.find((session) => session.id === sessionId)
    || { id: sessionId, title: null };
  saveConversationDraft();
  window.clearTimeout(conversationPollTimer);
  conversationGeneration += 1;
  conversationSessionId = sessionId;
  conversationClient = createConversationClient({
    token: conversationToken,
    sessionId,
  });
  document.body.dataset.sessionId = sessionId;
  try {
    window.history.replaceState(window.history.state, "", url);
  } catch (_error) {
    window.location.replace(url);
    return;
  }
  resetConversationSessionView(preview);
  renderConversationSessionSwitcher(conversationSessions);
  void loadConversation();
}

function renderConversationSessionSwitcher(sessions) {
  conversationSessions = sessions;
  const ordered = conversationSessionEntries(sessions);
  const labels = conversationSessionLabels(ordered);
  const signature = JSON.stringify([
    conversationSessionId,
    ordered.map((session) => [
      session.id,
      labels.get(session.id),
      session.title,
      conversationSessionStatus(session),
    ]),
  ]);
  if (signature === conversationSessionSwitcherSignature) {
    return;
  }
  conversationSessionSwitcherSignature = signature;
  const previousScrollLeft = conversationSessionSwitcher.scrollLeft;
  conversationSessionSwitcher.replaceChildren();
  conversationSessionNavigation.hidden = false;
  conversationSessionSwitcher.hidden = ordered.length === 0;
  if (ordered.length === 0) {
    return;
  }
  ordered.forEach((session) => {
    const status = conversationSessionStatus(session);
    const current = session.id === conversationSessionId;
    const working = status === "执行中";
    const title = typeof session.title === "string" ? session.title.trim() : "";
    const sessionLabel = labels.get(session.id);
    const button = document.createElement("button");
    const dot = document.createElement("span");
    const label = document.createElement("span");
    button.type = "button";
    button.className = "conversation-session-switch";
    button.dataset.sessionId = session.id;
    button.dataset.sessionUrl = `/codex/${encodeURIComponent(session.id)}/quick-interactions/conversation`;
    button.title = `${current ? "当前" : "切换到"} ${sessionLabel}${title && sessionLabel !== title ? `，${title}` : ""}，${status}`;
    button.setAttribute(
      "aria-label",
      `${sessionLabel}${title && sessionLabel !== title ? `，${title}` : ""}，${status}${current ? "，当前 Session" : ""}`,
    );
    if (current) {
      button.classList.add("is-current");
      button.setAttribute("aria-current", "page");
    }
    if (working) {
      button.classList.add("is-working");
    }
    dot.className = "conversation-session-dot";
    dot.setAttribute("aria-hidden", "true");
    label.textContent = `${sessionLabel} · ${status}`;
    button.append(dot, label);
    conversationSessionSwitcher.append(button);
  });
  if (previousScrollLeft > 0) {
    conversationSessionSwitcher.scrollLeft = previousScrollLeft;
    return;
  }
  const current = conversationSessionSwitcher.querySelector("[aria-current='page']");
  window.requestAnimationFrame(() => {
    current?.scrollIntoView({ block: "nearest", inline: "center" });
  });
}

function renderConversationSessionCreation(context) {
  conversationCreationAvailable = context.available === true;
  conversationWorkspaces = Array.isArray(context.workspaces) ? context.workspaces : [];
  const hasAvailableWorkspace = conversationWorkspaces.some(
    (workspace) => workspace.available === true,
  );
  conversationSessionCreate.disabled = conversationCreationPending
    || !conversationCreationAvailable
    || !hasAvailableWorkspace;
  const label = !conversationCreationAvailable
    ? context.unavailableReason || "Codex 当前不可用"
    : !hasAvailableWorkspace
      ? "当前没有可用工作目录"
      : conversationCreationPending
        ? "正在新建 Session"
        : "新建 Session";
  conversationSessionCreate.title = label;
  conversationSessionCreate.setAttribute("aria-label", label);
}

function closeConversationCreateDialog() {
  if (!conversationCreationPending && conversationCreateDialog.open) {
    conversationCreateDialog.close();
  }
}

function openConversationCreateDialog() {
  if (conversationSessionCreate.disabled) {
    return;
  }
  conversationCreateWorkspaces.replaceChildren();
  conversationWorkspaces.forEach((workspace) => {
    const button = document.createElement("button");
    const name = document.createElement("strong");
    const path = document.createElement("span");
    button.type = "button";
    button.className = "workspace-button";
    button.disabled = workspace.available !== true;
    button.dataset.workspaceAvailable = String(workspace.available === true);
    name.textContent = workspace.name;
    path.textContent = workspace.path;
    button.append(name, path);
    button.addEventListener("click", () => {
      void createConversationSession(workspace.id);
    });
    conversationCreateWorkspaces.append(button);
  });
  showConversationMessage(conversationCreateMessage, "");
  conversationCreateDialog.showModal();
  conversationCreateWorkspaces.querySelector("button:not(:disabled)")?.focus();
}

async function createConversationSession(workspaceId) {
  if (conversationCreationPending) {
    return;
  }
  conversationCreationPending = true;
  conversationCreateSurface.setAttribute("aria-busy", "true");
  conversationCreateClose.disabled = true;
  conversationCreateWorkspaces.querySelectorAll("button").forEach((button) => {
    button.disabled = true;
  });
  renderConversationSessionCreation({
    available: conversationCreationAvailable,
    workspaces: conversationWorkspaces,
  });
  showConversationMessage(conversationCreateMessage, "正在创建 Session…");
  const preferences = readConversationSessionCreationPreferences();
  const createWithPreferences = (model, reasoningEffort) => (
    conversationClient.createSession({
      workspaceId,
      permissionMode: preferences.permissionMode,
      model,
      reasoningEffort,
    })
  );
  try {
    let session;
    try {
      session = await createWithPreferences(
        preferences.model,
        preferences.reasoningEffort,
      );
    } catch (error) {
      if (!shouldRetryConversationCreationWithDefaults(error, preferences)) {
        throw error;
      }
      clearConversationSessionModelPreferences();
      session = await createWithPreferences(null, null);
    }
    conversationCreationPending = false;
    conversationCreateDialog.close();
    switchConversationSession(
      session.id,
      `/codex/${encodeURIComponent(session.id)}/quick-interactions/conversation`,
      session,
    );
  } catch (error) {
    conversationCreationPending = false;
    conversationCreateSurface.removeAttribute("aria-busy");
    conversationCreateClose.disabled = false;
    renderConversationSessionCreation({
      available: conversationCreationAvailable,
      workspaces: conversationWorkspaces,
    });
    conversationCreateWorkspaces.querySelectorAll("button").forEach((button) => {
      button.disabled = button.dataset.workspaceAvailable !== "true";
    });
    showConversationMessage(
      conversationCreateMessage,
      error.message || "Session 创建失败。",
      "error",
    );
  }
}

function readConversationDraft() {
  try {
    return sessionStorage.getItem(conversationDraftKey()) || "";
  } catch (_error) {
    return "";
  }
}

function saveConversationDraft() {
  try {
    if (conversationPrompt.value) {
      sessionStorage.setItem(conversationDraftKey(), conversationPrompt.value);
    } else {
      sessionStorage.removeItem(conversationDraftKey());
    }
  } catch (_error) {
    // Storage failure only affects draft persistence in this browser tab.
  }
}

function showConversationMessage(element, text, kind = "") {
  element.textContent = text;
  element.className = "message";
  if (kind) {
    element.classList.add(`message-${kind}`);
  }
}

function conversationTaskSignature(task) {
  return JSON.stringify([
    task.status,
    task.updated_at,
    task.prompt,
    task.result,
    task.error,
    task.pinned_at,
    task.notification_status,
    task.notification_error,
    task.deferred_restart_status,
    task.deferred_restart_updated_at,
    task.deferred_restart_notification_status,
    task.deferred_restart_notification_error,
    task.deferred_restart_notification_updated_at,
    conversationStatusText(task),
  ]);
}

function createConversationMeta(text) {
  const meta = document.createElement("span");
  meta.className = "conversation-message-meta";
  meta.textContent = text;
  return meta;
}

function createConversationNotification(task) {
  const labels = {
    pending: "待通知",
    sending: "通知中",
    sent: "已通知",
    failed: "通知失败",
    skipped: "未通知",
  };
  const label = labels[task.notification_status];
  if (!label) {
    return null;
  }
  const notification = createConversationMeta(label);
  notification.classList.add(
    "conversation-notification",
    `conversation-notification-${task.notification_status}`,
  );
  if (task.notification_error) {
    notification.title = task.notification_error;
    notification.setAttribute("aria-label", `${label}：${task.notification_error}`);
  }
  return notification;
}

function createRestartNotification(task) {
  const labels = {
    pending: "重启结果待通知",
    sending: "重启结果通知中",
    sent: "重启结果已通知",
    failed: "重启结果通知失败",
    skipped: "重启结果未通知",
  };
  const label = labels[task.deferred_restart_notification_status];
  if (!label) {
    return null;
  }
  const notification = createConversationMeta(label);
  notification.classList.add(
    "conversation-notification",
    `conversation-notification-${task.deferred_restart_notification_status}`,
  );
  if (task.deferred_restart_notification_error) {
    notification.title = task.deferred_restart_notification_error;
    notification.setAttribute(
      "aria-label",
      `${label}：${task.deferred_restart_notification_error}`,
    );
  }
  return notification;
}

function updateConversationTurn(turn, task) {
  const signature = conversationTaskSignature(task);
  if (turn.dataset.taskSignature === signature) {
    return;
  }
  turn.dataset.taskSignature = signature;
  turn.className = `conversation-turn conversation-turn-${task.status}`;
  turn.replaceChildren();

  const userMessage = document.createElement("div");
  const userBubble = document.createElement("div");
  const userContent = document.createElement("p");
  userMessage.className = "conversation-message conversation-message-user";
  userBubble.className = "conversation-bubble";
  userContent.textContent = task.prompt || "历史任务未保存提交内容。";
  userBubble.append(userContent);
  userMessage.append(
    userBubble,
    createConversationMeta(formatConversationTime(task.created_at)),
  );

  const assistantMessage = document.createElement("div");
  const assistantBubble = document.createElement("div");
  const assistantContent = document.createElement("p");
  const assistantMeta = document.createElement("div");
  const assistantInfo = document.createElement("div");
  const assistantTime = createConversationMeta(formatConversationTime(task.updated_at));
  const notification = createConversationNotification(task);
  const pin = document.createElement("button");
  assistantMessage.className = "conversation-message conversation-message-assistant";
  assistantBubble.className = "conversation-bubble";
  assistantMeta.className = "conversation-assistant-meta";
  assistantInfo.className = "conversation-assistant-info";
  assistantInfo.append(assistantTime);
  if (task.result || task.error) {
    assistantContent.textContent = task.result || task.error;
  } else {
    assistantContent.textContent = conversationStatusText(task);
    assistantBubble.classList.add("is-status");
  }
  if (["failed", "timed_out", "cancelled", "needs_terminal"].includes(task.status)) {
    assistantBubble.classList.add("is-error");
  }
  pin.type = "button";
  pin.className = "button-link conversation-pin";
  pin.textContent = task.pinned_at ? "取消置顶" : "置顶";
  pin.setAttribute("aria-pressed", String(Boolean(task.pinned_at)));
  pin.addEventListener("click", () => setConversationPinned(task, pin));
  assistantBubble.append(assistantContent);
  assistantMeta.append(assistantInfo);
  if (notification) {
    assistantMeta.append(notification);
  }
  assistantMeta.append(pin);
  assistantMessage.append(assistantBubble, assistantMeta);
  turn.append(userMessage, assistantMessage);
  const restartMessages = {
    succeeded: "Chub 已完成自动重启，服务已恢复。",
    start_failed: (
      `Chub 自动重启未完成：${task.deferred_restart_error
        || "旧记录没有保存具体原因，请查看 Chub 运行日志。"}`
    ),
    sensitive_task_failed: (
      "Chub 已取消自动重启：等待期间有运行资源修改任务异常结束，请检查任务结果。"
    ),
    cleared: "Chub 自动重启计划已由其他服务重启清除。",
  };
  const restartText = restartMessages[task.deferred_restart_status];
  if (restartText) {
    const restartMessage = document.createElement("div");
    const restartBubble = document.createElement("div");
    const restartContent = document.createElement("p");
    const restartMeta = document.createElement("div");
    const restartNotification = createRestartNotification(task);
    restartMessage.className = (
      "conversation-message conversation-message-assistant conversation-message-system"
    );
    restartBubble.className = "conversation-bubble is-status";
    if (
      task.deferred_restart_status === "start_failed"
      || task.deferred_restart_status === "sensitive_task_failed"
    ) {
      restartBubble.classList.add("is-error");
    }
    restartContent.textContent = restartText;
    restartBubble.append(restartContent);
    restartMeta.className = "conversation-assistant-info";
    restartMeta.append(
      createConversationMeta(
        `Chub 系统 · ${formatConversationTime(task.deferred_restart_updated_at)}`,
      ),
    );
    if (restartNotification) {
      restartMeta.append(restartNotification);
    }
    restartMessage.append(restartBubble, restartMeta);
    turn.append(restartMessage);
  }
}

function createConversationTurn(task) {
  const turn = document.createElement("article");
  turn.dataset.taskId = task.id;
  updateConversationTurn(turn, task);
  return turn;
}

function renderConversationTasks({ forceBottom = false, preservePosition = false } = {}) {
  const wasNearBottom = !preservePosition && (
    forceBottom
    || isConversationNearBottom()
  );
  const empty = conversationFeed.querySelector(".empty-state");
  if (!conversationTasks.length) {
    if (!empty) {
      const nextEmpty = document.createElement("p");
      nextEmpty.className = "empty-state conversation-empty";
      nextEmpty.textContent = "暂无快速交互记录，可以从下方发送第一条消息。";
      conversationFeed.replaceChildren(nextEmpty);
    }
    return;
  }
  empty?.remove();
  const existing = new Map(
    Array.from(conversationFeed.querySelectorAll("[data-task-id]"))
      .map((turn) => [turn.dataset.taskId, turn]),
  );
  const retained = new Set();
  conversationTasks.forEach((task, index) => {
    const turn = existing.get(task.id) || createConversationTurn(task);
    retained.add(task.id);
    updateConversationTurn(turn, task);
    const current = conversationFeed.children[index];
    if (current !== turn) {
      conversationFeed.insertBefore(turn, current || null);
    }
  });
  existing.forEach((turn, taskId) => {
    if (!retained.has(taskId)) {
      turn.remove();
    }
  });
  if (wasNearBottom) {
    window.requestAnimationFrame(() => {
      conversationScroll.scrollTop = conversationScroll.scrollHeight;
      updateConversationJumpLatest();
    });
  } else {
    updateConversationJumpLatest();
  }
}

function updateConversationJumpLatest() {
  conversationJumpLatest.hidden = isConversationNearBottom()
    || conversationTasks.length === 0;
}

function isConversationNearBottom() {
  return conversationScroll.scrollTop + conversationScroll.clientHeight
    >= conversationScroll.scrollHeight - 140;
}

function resizeConversationPrompt() {
  const keepAtBottom = isConversationNearBottom();
  conversationPrompt.style.height = "auto";
  const nextHeight = Math.min(conversationPrompt.scrollHeight, 120);
  conversationPrompt.style.height = `${nextHeight}px`;
  conversationPrompt.style.overflowY = conversationPrompt.scrollHeight > 120
    ? "auto"
    : "hidden";
  if (keepAtBottom) {
    window.requestAnimationFrame(() => {
      conversationScroll.scrollTop = conversationScroll.scrollHeight;
      updateConversationJumpLatest();
    });
  }
}

function updateConversationComposerActions() {
}

function mergeConversationTasks(tasks) {
  const merged = new Map(conversationTasks.map((task) => [task.id, task]));
  tasks.forEach((task) => merged.set(task.id, task));
  conversationTasks = Array.from(merged.values()).sort((left, right) => {
    const timeDifference = new Date(left.created_at) - new Date(right.created_at);
    return timeDifference || left.id.localeCompare(right.id);
  });
  if (conversationTotal > 0 && conversationTasks.length > conversationTotal) {
    conversationTasks = conversationTasks.slice(-conversationTotal);
  }
}

async function setConversationPinned(task, button) {
  const generation = conversationGeneration;
  const client = conversationClient;
  button.disabled = true;
  try {
    const data = await client.setPinned(task.id, !task.pinned_at);
    if (generation !== conversationGeneration) {
      return;
    }
    mergeConversationTasks([data.task]);
    renderConversationTasks();
    showConversationMessage(conversationHistoryMessage, "");
  } catch (error) {
    if (generation !== conversationGeneration) {
      return;
    }
    button.disabled = false;
    showConversationMessage(
      conversationHistoryMessage,
      error.message || "置顶状态更新失败。",
      "error",
    );
  }
}

function renderConversationSession(session) {
  conversationSession = session;
  const title = typeof session.title === "string" ? session.title.trim() : "";
  const displayTitle = title || "未命名 Session";
  const busy = conversationActive || session.quick_interaction_running === true;
  const renameAllowed = session.workspace_id !== "weixin-translation";
  conversationSessionTitle.textContent = displayTitle;
  conversationSessionTitle.title = displayTitle;
  document.title = `${displayTitle} · 快速交互`;
  conversationSessionTitleRow.hidden = false;
  conversationSessionTitleRow.removeAttribute("aria-busy");
  conversationSessionRename.hidden = !renameAllowed;
  conversationSessionRename.disabled = !renameAllowed;
  conversationSessionRename.title = "重命名 Session";
  conversationSessionRename.setAttribute("aria-label", "重命名 Session");
  const archiveReady = Boolean(session.codex_session_id);
  const archiveBusy = busy || conversationArchivePending;
  conversationSessionArchive.disabled = !archiveReady || archiveBusy;
  const archiveLabel = !archiveReady
    ? "尚未启动的 Session 无法归档"
    : archiveBusy
      ? "Session 正在执行，暂不能归档"
      : "归档 Session";
  conversationSessionArchive.title = archiveLabel;
  conversationSessionArchive.setAttribute("aria-label", archiveLabel);
  if (conversationSubmitMessage.dataset.sessionLoadError === "true") {
    delete conversationSubmitMessage.dataset.sessionLoadError;
    showConversationMessage(conversationSubmitMessage, "");
  }
  conversationConfirmStopUnknownTerminal =
    session.status === "running" && session.activity === "unknown";
  const reason = conversationSubmissionBlockReason({
    session,
    activeInteraction: conversationActive,
    promptLength: conversationPrompt.value.length,
  });
  conversationForm.setAttribute("aria-busy", String(busy));
  conversationSubmit.disabled = Boolean(reason);
  conversationSubmit.textContent = "发送";
  conversationPrompt.disabled = busy;
  if (reason && !busy) {
    showConversationMessage(conversationSubmitMessage, reason);
  } else if (busy || !conversationSubmitMessage.classList.contains("message-error")) {
    showConversationMessage(conversationSubmitMessage, "");
  }
}

function closeConversationRenameDialog() {
  if (!conversationRenamePending && conversationRenameDialog.open) {
    conversationRenameDialog.close();
  }
}

function openConversationRenameDialog() {
  if (
    !conversationSession
    || conversationSessionRename.disabled
    || conversationRenamePending
  ) {
    return;
  }
  conversationRenameInput.value = conversationSession.title?.trim() || "";
  showConversationMessage(conversationRenameMessage, "");
  conversationRenameDialog.showModal();
  window.requestAnimationFrame(() => {
    conversationRenameInput.focus();
    conversationRenameInput.select();
  });
}

async function renameConversationSession(event) {
  event.preventDefault();
  if (conversationRenamePending) {
    return;
  }
  const title = conversationRenameInput.value.trim();
  if (!title) {
    showConversationMessage(
      conversationRenameMessage,
      "请输入 Session 标题。",
      "error",
    );
    conversationRenameInput.focus();
    return;
  }
  conversationRenamePending = true;
  conversationRenameForm.setAttribute("aria-busy", "true");
  conversationRenameInput.disabled = true;
  conversationRenameClose.disabled = true;
  conversationRenameCancel.disabled = true;
  conversationRenameConfirm.disabled = true;
  const generation = conversationGeneration;
  const client = conversationClient;
  try {
    const session = await client.renameSession(title);
    if (generation !== conversationGeneration) {
      return;
    }
    renderConversationSession(session);
    conversationRenameDialog.close();
    void loadConversation();
  } catch (error) {
    if (generation !== conversationGeneration) {
      return;
    }
    showConversationMessage(
      conversationRenameMessage,
      error.message || "Session 重命名失败。",
      "error",
    );
  } finally {
    conversationRenamePending = false;
    conversationRenameForm.removeAttribute("aria-busy");
    conversationRenameInput.disabled = false;
    conversationRenameClose.disabled = false;
    conversationRenameCancel.disabled = false;
    conversationRenameConfirm.disabled = false;
  }
}

function closeConversationArchiveDialog() {
  if (!conversationArchivePending && conversationArchiveDialog.open) {
    conversationArchiveDialog.close();
  }
}

function openConversationArchiveDialog() {
  if (!conversationSession || conversationSessionArchive.disabled) {
    return;
  }
  const title = conversationSession.title?.trim() || "未命名 Session";
  conversationArchiveDescription.textContent =
    `归档“${title}”后，该 Session 将从活动列表移除，正在运行的实时终端会停止；`
    + "如已分配微信槽位，槽位也会释放。Chub 页面暂不提供恢复入口。";
  showConversationMessage(conversationArchiveMessage, "");
  conversationArchiveDialog.showModal();
  conversationArchiveConfirm.focus();
}

async function archiveConversationSession(event) {
  event.preventDefault();
  if (!conversationSession || conversationArchivePending) {
    return;
  }
  conversationArchivePending = true;
  conversationArchiveForm.setAttribute("aria-busy", "true");
  conversationArchiveClose.disabled = true;
  conversationArchiveCancel.disabled = true;
  conversationArchiveConfirm.disabled = true;
  conversationSessionArchive.disabled = true;
  showConversationMessage(conversationArchiveMessage, "");
  window.clearTimeout(conversationPollTimer);
  const generation = conversationGeneration;
  const archivedSessionId = conversationSessionId;
  const client = conversationClient;
  try {
    await client.archiveSession();
    if (generation !== conversationGeneration) {
      return;
    }
    const nextSession = firstConversationSessionAfterArchive(
      conversationSessions,
      archivedSessionId,
    );
    const nextSessionUrl = nextSession
      ? `/codex/${encodeURIComponent(nextSession.id)}/quick-interactions/conversation`
      : "/";
    if (!nextSession) {
      window.location.replace(nextSessionUrl);
      return;
    }
    conversationArchivePending = false;
    conversationArchiveForm.removeAttribute("aria-busy");
    conversationArchiveDialog.close();
    conversationSessions = conversationSessions.filter(
      (session) => session.id !== archivedSessionId,
    );
    switchConversationSession(nextSession.id, nextSessionUrl, nextSession);
  } catch (error) {
    if (generation !== conversationGeneration) {
      return;
    }
    conversationArchivePending = false;
    conversationArchiveForm.removeAttribute("aria-busy");
    conversationArchiveClose.disabled = false;
    conversationArchiveCancel.disabled = false;
    conversationArchiveConfirm.disabled = false;
    showConversationMessage(
      conversationArchiveMessage,
      error.message || "Session 归档失败。",
      "error",
    );
    renderConversationSession(conversationSession);
    void loadConversation();
  }
}

function renderConversationSessionError(error) {
  conversationSessionTitleRow.removeAttribute("aria-busy");
  conversationSessionRename.disabled = true;
  conversationSessionArchive.disabled = true;
  conversationSessionCreate.disabled = true;
  conversationSessionCreate.title = "Session 状态读取失败，暂不能新建";
  conversationSessionCreate.setAttribute(
    "aria-label",
    "Session 状态读取失败，暂不能新建",
  );
  conversationSubmit.disabled = true;
  conversationPrompt.disabled = false;
  conversationForm.setAttribute("aria-busy", "false");
  conversationSubmitMessage.dataset.sessionLoadError = "true";
  showConversationMessage(
    conversationSubmitMessage,
    error.message || "会话状态读取失败。",
    "error",
  );
}

async function performConversationLoad(generation, client) {
  window.clearTimeout(conversationPollTimer);
  const [historyResult, sessionContextResult] = await Promise.allSettled([
    client.listTasks({
      limit: CONVERSATION_PAGE_SIZE,
      order: "timeline",
    }),
    client.loadSessionContext(),
  ]);
  if (generation !== conversationGeneration) {
    return;
  }
  if (historyResult.status === "fulfilled") {
    const data = historyResult.value;
    conversationTotal = data.total;
    mergeConversationTasks(data.tasks);
    conversationActive = conversationTasks.some(
      (task) => ["requested", "running"].includes(task.status),
    );
    if (!conversationInitialized) {
      conversationHasEarlier = data.has_more;
    } else if (conversationHistoryExpanded) {
      conversationHasEarlier = conversationTasks.length < conversationTotal;
    }
    renderConversationTasks({ forceBottom: !conversationInitialized });
    conversationInitialized = true;
    conversationLoadEarlier.hidden = !conversationHasEarlier;
    showConversationMessage(conversationHistoryMessage, "");
  } else {
    showConversationMessage(
      conversationHistoryMessage,
      historyResult.reason.message || "快速交互记录读取失败。",
      "error",
    );
  }
  if (sessionContextResult.status === "fulfilled") {
    renderConversationSessionSwitcher(sessionContextResult.value.sessions);
    renderConversationSessionCreation(sessionContextResult.value);
    renderConversationSession(sessionContextResult.value.session);
  } else {
    renderConversationSessionError(sessionContextResult.reason);
  }
  const session = sessionContextResult.status === "fulfilled"
    ? sessionContextResult.value.session
    : conversationSession;
  const loadErrors = [historyResult, sessionContextResult]
    .filter((result) => result.status === "rejected")
    .map((result) => result.reason);
  if (loadErrors.length === 0) {
    conversationPollFailureCount = 0;
  } else if (loadErrors.some(isRetryableConversationError)) {
    conversationPollFailureCount += 1;
  }
  if (shouldPollConversation({
    loadFailed: loadErrors.length > 0,
    loadErrors,
    activeInteraction: conversationActive,
    notificationPending: conversationTasks.some((task) => (
      task.notification_status === "pending"
      || task.notification_status === "sending"
      || task.deferred_restart_notification_status === "pending"
      || task.deferred_restart_notification_status === "sending"
    )),
    restartPending: conversationTasks.some(
      (task) => task.deferred_restart_status === "pending"
        || task.deferred_restart_status === "started",
    ),
    session,
  }) && document.visibilityState !== "hidden") {
    conversationPollTimer = window.setTimeout(
      loadConversation,
      conversationPollDelay(conversationPollFailureCount),
    );
  }
}

function loadConversation() {
  const generation = conversationGeneration;
  const client = conversationClient;
  const queued = conversationLoadQueue.then(
    () => performConversationLoad(generation, client),
  );
  conversationLoadQueue = queued.catch(() => {});
  return queued;
}

async function performLoadEarlierConversation(generation, client) {
  const anchor = conversationFeed.querySelector("[data-task-id]");
  const anchorTop = anchor?.getBoundingClientRect().top;
  try {
    const oldest = conversationTasks[0];
    const data = await client.listTasks({
      limit: CONVERSATION_PAGE_SIZE,
      order: "timeline",
      before: { createdAt: oldest.created_at, id: oldest.id },
    });
    if (generation !== conversationGeneration) {
      return;
    }
    conversationTotal = data.total;
    conversationHistoryExpanded = true;
    conversationHasEarlier = data.has_more;
    mergeConversationTasks(data.tasks);
    renderConversationTasks({ preservePosition: true });
    conversationLoadEarlier.hidden = !conversationHasEarlier;
    if (anchor && anchorTop !== undefined) {
      conversationScroll.scrollTop += anchor.getBoundingClientRect().top - anchorTop;
    }
    showConversationMessage(conversationHistoryMessage, "");
  } catch (error) {
    if (generation !== conversationGeneration) {
      return;
    }
    showConversationMessage(
      conversationHistoryMessage,
      error.message || "更早消息读取失败。",
      "error",
    );
  } finally {
    if (generation === conversationGeneration) {
      conversationLoadEarlier.disabled = false;
    }
  }
}

function loadEarlierConversation() {
  if (
    conversationLoadEarlierPending
    || !conversationTasks.length
    || !conversationHasEarlier
  ) {
    return conversationLoadQueue;
  }
  conversationLoadEarlierPending = true;
  conversationLoadEarlier.disabled = true;
  const generation = conversationGeneration;
  const client = conversationClient;
  const queued = conversationLoadQueue.then(
    () => performLoadEarlierConversation(generation, client),
  );
  conversationLoadQueue = queued.catch(() => {});
  return queued.finally(() => {
    if (generation === conversationGeneration) {
      conversationLoadEarlierPending = false;
      conversationLoadEarlier.disabled = false;
    }
  });
}

conversationPrompt.addEventListener("input", () => {
  saveConversationDraft();
  resizeConversationPrompt();
  updateConversationComposerActions();
  if (conversationSession) {
    renderConversationSession(conversationSession);
  }
});

conversationPrompt.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!conversationSubmit.disabled) {
      conversationForm.requestSubmit();
    }
  }
});

conversationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = conversationPrompt.value.trim();
  const reason = conversationSubmissionBlockReason({
    session: conversationSession,
    activeInteraction: conversationActive,
    promptLength: conversationPrompt.value.length,
  });
  if (!canSubmitConversation({
    prompt: value,
    session: conversationSession,
    blocked: Boolean(reason),
  })) {
    return;
  }
  conversationSubmit.disabled = true;
  conversationPrompt.disabled = true;
  const generation = conversationGeneration;
  const client = conversationClient;
  try {
    const data = await client.submitTask({
      prompt: value,
      confirmStopUnknownTerminal: conversationConfirmStopUnknownTerminal,
    });
    if (generation !== conversationGeneration) {
      return;
    }
    conversationPrompt.value = "";
    saveConversationDraft();
    resizeConversationPrompt();
    updateConversationComposerActions();
    conversationConfirmStopUnknownTerminal = false;
    conversationActive = true;
    conversationTotal += conversationTasks.some((task) => task.id === data.task.id) ? 0 : 1;
    mergeConversationTasks([data.task]);
    renderConversationTasks({ forceBottom: true });
    showConversationMessage(conversationSubmitMessage, "");
    await loadConversation();
  } catch (error) {
    if (generation !== conversationGeneration) {
      return;
    }
    if (
      error.code === "quick_interaction_terminal_confirmation_required"
    ) {
      conversationConfirmStopUnknownTerminal = true;
      showConversationMessage(
        conversationSubmitMessage,
        "请确认影响后再次点击发送。",
        "error",
      );
    } else {
      showConversationMessage(
        conversationSubmitMessage,
        error.message || "快速交互提交失败。",
        "error",
      );
    }
    try {
      const session = await client.loadSession();
      if (generation !== conversationGeneration) {
        return;
      }
      renderConversationSession(session);
    } catch (sessionError) {
      renderConversationSessionError(sessionError);
    }
  }
});

conversationLoadEarlier.addEventListener("click", loadEarlierConversation);
conversationJumpLatest.addEventListener("click", () => {
  conversationScroll.scrollTo({ top: conversationScroll.scrollHeight, behavior: "smooth" });
});
conversationSessionSwitcher.addEventListener("click", handleConversationSessionSwitch);
conversationSessionSwitcher.addEventListener("auxclick", handleConversationSessionSwitch);
conversationSessionCreate.addEventListener("click", openConversationCreateDialog);
conversationCreateClose.addEventListener("click", closeConversationCreateDialog);
conversationCreateDialog.addEventListener("click", (event) => {
  if (event.target === conversationCreateDialog) {
    closeConversationCreateDialog();
  }
});
conversationCreateDialog.addEventListener("cancel", (event) => {
  if (conversationCreationPending) {
    event.preventDefault();
  }
});
conversationSessionRename.addEventListener("click", openConversationRenameDialog);
conversationSessionArchive.addEventListener("click", openConversationArchiveDialog);
conversationRenameForm.addEventListener("submit", renameConversationSession);
conversationRenameClose.addEventListener("click", closeConversationRenameDialog);
conversationRenameCancel.addEventListener("click", closeConversationRenameDialog);
conversationRenameDialog.addEventListener("click", (event) => {
  if (event.target === conversationRenameDialog) {
    closeConversationRenameDialog();
  }
});
conversationRenameDialog.addEventListener("cancel", (event) => {
  if (conversationRenamePending) {
    event.preventDefault();
  }
});
conversationArchiveForm.addEventListener("submit", archiveConversationSession);
conversationArchiveClose.addEventListener("click", closeConversationArchiveDialog);
conversationArchiveCancel.addEventListener("click", closeConversationArchiveDialog);
conversationArchiveDialog.addEventListener("click", (event) => {
  if (event.target === conversationArchiveDialog) {
    closeConversationArchiveDialog();
  }
});
conversationArchiveDialog.addEventListener("cancel", (event) => {
  if (conversationArchivePending) {
    event.preventDefault();
  }
});
conversationScroll.addEventListener("scroll", updateConversationJumpLatest, { passive: true });

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    loadConversation();
  } else {
    window.clearTimeout(conversationPollTimer);
  }
});

conversationPrompt.value = readConversationDraft();
resizeConversationPrompt();
updateConversationComposerActions();
loadConversation();
