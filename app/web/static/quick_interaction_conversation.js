"use strict";

const conversationSessionId = document.body.dataset.sessionId;
const {
  canSubmit: canSubmitConversation,
  createClient: createConversationClient,
  formatTime: formatConversationTime,
  isRetryableRequestError: isRetryableConversationError,
  pollDelay: conversationPollDelay,
  readPageSize: readConversationPageSize,
  readToken: readConversationToken,
  sessionNavigationMode: conversationSessionNavigationMode,
  sessionSwitcherStatus: conversationSessionStatus,
  shouldPoll: shouldPollConversation,
  statusText: conversationStatusText,
  submissionBlockReason: conversationSubmissionBlockReason,
} = window.QuickInteractionCore;
const conversationToken = readConversationToken();
const conversationClient = createConversationClient({
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
const conversationSessionSwitcher = document.querySelector("#conversation-session-switcher");
const CONVERSATION_PAGE_SIZE = readConversationPageSize();
const CONVERSATION_SESSION_NUMBERS_KEY = "hub.quickInteractionSessionNumbers.v1";
const conversationDraftKey = `hub.quickInteractionDraft.v1.${conversationSessionId}`;
let conversationPollTimer = null;
let conversationPollFailureCount = 0;
let conversationLoadQueue = Promise.resolve();
let conversationLoadEarlierPending = false;
let conversationTasks = [];
let conversationTotal = 0;
let conversationHasEarlier = false;
let conversationHistoryExpanded = false;
let conversationInitialized = false;
let conversationActive = false;
let conversationSession = null;
let conversationConfirmStopUnknownTerminal = false;
let conversationSessionSwitcherSignature = "";

function readConversationSessionNumbers(sessions) {
  const activeIds = new Set(sessions.map((session) => session.id));
  const fallbackSessions = [...sessions].sort((left, right) => {
    const createdDifference = new Date(left.created_at) - new Date(right.created_at);
    return createdDifference || left.id.localeCompare(right.id);
  });
  const fallback = new Map(
    fallbackSessions.map((session, index) => [session.id, index + 1]),
  );
  try {
    const stored = JSON.parse(
      localStorage.getItem(CONVERSATION_SESSION_NUMBERS_KEY) || "{}",
    );
    const entries = stored?.sessions && typeof stored.sessions === "object"
      ? stored.sessions
      : {};
    const assigned = new Map();
    const used = new Set();
    Object.entries(entries).forEach(([sessionId, number]) => {
      if (
        activeIds.has(sessionId)
        && Number.isInteger(number)
        && number > 0
        && !used.has(number)
      ) {
        assigned.set(sessionId, number);
        used.add(number);
      }
    });
    let next = Number.isInteger(stored?.next) && stored.next > 0
      ? stored.next
      : 1;
    fallbackSessions.forEach((session) => {
      if (assigned.has(session.id)) {
        return;
      }
      while (used.has(next)) {
        next += 1;
      }
      assigned.set(session.id, next);
      used.add(next);
      next += 1;
    });
    localStorage.setItem(
      CONVERSATION_SESSION_NUMBERS_KEY,
      JSON.stringify({ next, sessions: Object.fromEntries(assigned) }),
    );
    return assigned;
  } catch (_error) {
    return fallback;
  }
}

function handleConversationSessionSwitch(event) {
  const link = event.target.closest?.(".conversation-session-switch");
  if (!link || !conversationSessionSwitcher.contains(link)) {
    return;
  }
  const mode = conversationSessionNavigationMode({
    button: event.button,
    current: link.getAttribute("aria-current") === "page",
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
  window.location.replace(link.href);
}

function renderConversationSessionSwitcher(sessions) {
  const numbers = readConversationSessionNumbers(sessions);
  const ordered = [...sessions].sort((left, right) => (
    numbers.get(left.id) - numbers.get(right.id)
  ));
  const signature = JSON.stringify(ordered.map((session) => [
    session.id,
    numbers.get(session.id),
    conversationSessionStatus(session),
  ]));
  if (signature === conversationSessionSwitcherSignature) {
    return;
  }
  conversationSessionSwitcherSignature = signature;
  const previousScrollLeft = conversationSessionSwitcher.scrollLeft;
  conversationSessionSwitcher.replaceChildren();
  conversationSessionSwitcher.hidden = ordered.length <= 1;
  if (ordered.length <= 1) {
    return;
  }
  ordered.forEach((session) => {
    const number = numbers.get(session.id);
    const status = conversationSessionStatus(session);
    const current = session.id === conversationSessionId;
    const working = status === "执行中";
    const link = document.createElement("a");
    const dot = document.createElement("span");
    const label = document.createElement("span");
    link.className = "conversation-session-switch";
    link.href = `/codex/${encodeURIComponent(session.id)}/quick-interactions/conversation`;
    link.title = `切换到 Session ${number}，${status}`;
    link.setAttribute(
      "aria-label",
      `Session ${number}，${status}${current ? "，当前 Session" : ""}`,
    );
    if (current) {
      link.classList.add("is-current");
      link.setAttribute("aria-current", "page");
    }
    if (working) {
      link.classList.add("is-working");
    }
    dot.className = "conversation-session-dot";
    dot.setAttribute("aria-hidden", "true");
    label.textContent = `${number} · ${status}`;
    link.append(dot, label);
    conversationSessionSwitcher.append(link);
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

function readConversationDraft() {
  try {
    return sessionStorage.getItem(conversationDraftKey) || "";
  } catch (_error) {
    return "";
  }
}

function saveConversationDraft() {
  try {
    if (conversationPrompt.value) {
      sessionStorage.setItem(conversationDraftKey, conversationPrompt.value);
    } else {
      sessionStorage.removeItem(conversationDraftKey);
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
    start_failed: "Chub 自动重启未能启动，当前服务仍在运行。",
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
    if (task.deferred_restart_status === "start_failed") {
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
  button.disabled = true;
  try {
    const data = await conversationClient.setPinned(task.id, !task.pinned_at);
    mergeConversationTasks([data.task]);
    renderConversationTasks();
    showConversationMessage(conversationHistoryMessage, "");
  } catch (error) {
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
  const busy = conversationActive || session.quick_interaction_running === true;
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

function renderConversationSessionError(error) {
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

async function performConversationLoad() {
  window.clearTimeout(conversationPollTimer);
  const [historyResult, sessionContextResult] = await Promise.allSettled([
    conversationClient.listTasks({
      limit: CONVERSATION_PAGE_SIZE,
      order: "timeline",
    }),
    conversationClient.loadSessionContext(),
  ]);
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
  const queued = conversationLoadQueue.then(performConversationLoad);
  conversationLoadQueue = queued.catch(() => {});
  return queued;
}

async function performLoadEarlierConversation() {
  const anchor = conversationFeed.querySelector("[data-task-id]");
  const anchorTop = anchor?.getBoundingClientRect().top;
  try {
    const oldest = conversationTasks[0];
    const data = await conversationClient.listTasks({
      limit: CONVERSATION_PAGE_SIZE,
      order: "timeline",
      before: { createdAt: oldest.created_at, id: oldest.id },
    });
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
    showConversationMessage(
      conversationHistoryMessage,
      error.message || "更早消息读取失败。",
      "error",
    );
  } finally {
    conversationLoadEarlier.disabled = false;
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
  const queued = conversationLoadQueue.then(performLoadEarlierConversation);
  conversationLoadQueue = queued.catch(() => {});
  return queued.finally(() => {
    conversationLoadEarlierPending = false;
    conversationLoadEarlier.disabled = false;
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
  try {
    const data = await conversationClient.submitTask({
      prompt: value,
      confirmStopUnknownTerminal: conversationConfirmStopUnknownTerminal,
    });
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
      renderConversationSession(await conversationClient.loadSession());
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
