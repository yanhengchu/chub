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
const CONVERSATION_PAGE_SIZE = readConversationPageSize();
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
  const [historyResult, sessionResult] = await Promise.allSettled([
    conversationClient.listTasks({
      limit: CONVERSATION_PAGE_SIZE,
      order: "timeline",
    }),
    conversationClient.loadSession(),
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
  if (sessionResult.status === "fulfilled") {
    renderConversationSession(sessionResult.value);
  } else {
    renderConversationSessionError(sessionResult.reason);
  }
  const session = sessionResult.status === "fulfilled"
    ? sessionResult.value
    : conversationSession;
  const loadErrors = [historyResult, sessionResult]
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
    )),
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
conversationScroll.addEventListener("scroll", updateConversationJumpLatest, { passive: true });

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    loadConversation();
  } else {
    window.clearTimeout(conversationPollTimer);
  }
});

resizeConversationPrompt();
updateConversationComposerActions();
loadConversation();
