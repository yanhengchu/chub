"use strict";

const sessionId = document.body.dataset.sessionId;
const {
  createClient,
  engineLabel,
  formatTime,
  isRetryableRequestError,
  pollDelay,
  readPageSize: readQuickInteractionPageSize,
  readToken: readQuickInteractionToken,
  shouldPoll,
  statusText,
  submissionBlockReason: sharedSubmissionBlockReason,
} = window.QuickInteractionCore;
const token = readQuickInteractionToken();
const quickInteractionClient = createClient({ token, sessionId });
const form = document.querySelector("#quick-interaction-form");
const composerHeading = form.querySelector(".quick-interaction-page-heading");
const composerBody = document.querySelector("#quick-interaction-composer-body");
const prompt = document.querySelector("#quick-interaction-prompt");
const engineToggle = document.querySelector("#quick-interaction-engine");
const submit = document.querySelector("#quick-interaction-submit");
const submitMessage = document.querySelector("#quick-interaction-submit-message");
const historyMessage = document.querySelector("#quick-interaction-history-message");
const history = document.querySelector("#quick-interaction-history");
const loadMore = document.querySelector("#quick-interaction-load-more");
const PAGE_SIZE = readQuickInteractionPageSize();
const COMPOSER_COLLAPSE_ANIMATION_MS = 320;
const COMPOSER_COLLAPSE_FALLBACK_MS = 380;
let pollTimer = null;
let pollFailureCount = 0;
let loadedTasks = [];
let totalTasks = 0;
let loadQueue = Promise.resolve();
let appendPending = false;
let currentSession = null;
let confirmStopUnknownTerminal = false;
let composerCollapsed = false;
let composerStateInitialized = false;
let selectedEngine = "codex_cli";
let activeInteraction = false;

function showMessage(element, text, kind = "") {
  element.textContent = text;
  element.className = "message";
  if (kind) {
    element.classList.add(`message-${kind}`);
  }
}

function taskSignature(task) {
  return JSON.stringify([
    task.status,
    task.updated_at,
    task.prompt,
    task.result,
    task.error,
    task.pinned_at,
    task.engine,
    task.provider,
    task.model,
    task.notification_status,
    task.notification_error,
    statusText(task),
  ]);
}

function updateTaskItem(item, task) {
  const signature = taskSignature(task);
  if (item.dataset.taskSignature === signature) {
    return;
  }
  item.dataset.taskSignature = signature;
  item.className = `quick-interaction-history-item quick-interaction-${task.status}`;
  item.replaceChildren();
  {
    const heading = document.createElement("div");
    const status = document.createElement("strong");
    const meta = document.createElement("div");
    const engine = document.createElement("span");
    const time = document.createElement("span");
    const pinButton = document.createElement("button");
    const promptLabel = document.createElement("span");
    const promptContent = document.createElement("pre");
    item.className = `quick-interaction-history-item quick-interaction-${task.status}`;
    heading.className = "quick-interaction-history-heading";
    meta.className = "quick-interaction-history-meta";
    status.textContent = statusText(task);
    engine.className = "quick-interaction-engine-label";
    engine.textContent = task.engine === "bedrock_api"
      ? "Amazon Bedrock API"
      : "Codex CLI";
    if (task.engine === "bedrock_api" && task.model) {
      engine.title = `${task.provider || ""}${task.provider ? " · " : ""}${task.model}`;
    }
    time.textContent = formatTime(task.updated_at);
    pinButton.className = "button-secondary quick-interaction-pin";
    pinButton.type = "button";
    pinButton.textContent = task.pinned_at ? "取消置顶" : "置顶";
    pinButton.setAttribute("aria-label", pinButton.textContent);
    pinButton.setAttribute("aria-pressed", String(Boolean(task.pinned_at)));
    pinButton.addEventListener("click", () => setTaskPinned(task, pinButton));
    promptLabel.className = "quick-interaction-history-label";
    promptLabel.textContent = "提交内容";
    promptContent.className = "quick-interaction-history-content";
    promptContent.textContent = task.prompt || "历史任务未保存提交内容。";
    if (task.notification_status === "sent") {
      const notification = document.createElement("span");
      notification.textContent = "微信通知已发送";
      meta.append(engine, time, notification, pinButton);
    } else if (task.notification_status === "failed" || task.notification_status === "skipped") {
      const notification = document.createElement("span");
      notification.textContent = task.notification_error || "微信通知未送达";
      meta.append(engine, time, notification, pinButton);
    } else {
      meta.append(engine, time, pinButton);
    }
    heading.append(status, meta);
    item.append(heading, promptLabel, promptContent);
    if (task.result || task.error) {
      const resultLabel = document.createElement("span");
      const result = document.createElement("pre");
      resultLabel.className = "quick-interaction-history-label";
      resultLabel.textContent = task.result
        ? task.engine === "bedrock_api" ? "回答结果" : "执行结果"
        : "失败原因";
      result.className = "quick-interaction-history-content";
      result.textContent = task.result || task.error;
      item.append(resultLabel, result);
    }
  }
}

async function setTaskPinned(task, button) {
  button.disabled = true;
  try {
    await quickInteractionClient.setPinned(task.id, !task.pinned_at);
    loadedTasks = [];
    totalTasks = 0;
    await load();
    showMessage(historyMessage, "");
  } catch (error) {
    button.disabled = false;
    showMessage(
      historyMessage,
      error.message || "置顶状态更新失败。",
      "error",
    );
  }
}

function createTaskItem(task) {
  const item = document.createElement("article");
  item.dataset.taskId = task.id;
  updateTaskItem(item, task);
  return item;
}

function renderTasks(tasks) {
  const empty = history.querySelector(".empty-state");
  if (!tasks.length) {
    if (!empty) {
      const nextEmpty = document.createElement("p");
      nextEmpty.className = "empty-state";
      nextEmpty.textContent = "暂无快速交互记录。";
      history.replaceChildren(nextEmpty);
    }
    return false;
  }
  empty?.remove();
  const existing = new Map(
    Array.from(history.querySelectorAll("[data-task-id]"))
      .map((item) => [item.dataset.taskId, item]),
  );
  const retainedIds = new Set();
  tasks.forEach((task, index) => {
    const item = existing.get(task.id) || createTaskItem(task);
    retainedIds.add(task.id);
    updateTaskItem(item, task);
    const current = history.children[index];
    if (current !== item) {
      history.insertBefore(item, current || null);
    }
  });
  existing.forEach((item, taskId) => {
    if (!retainedIds.has(taskId)) {
      item.remove();
    }
  });
  return tasks.some((task) => ["requested", "running"].includes(task.status));
}

function mergeLatestTasks(tasks) {
  const latestIds = new Set(tasks.map((task) => task.id));
  return [
    ...tasks,
    ...loadedTasks.filter((task) => !latestIds.has(task.id)),
  ];
}

function submissionBlockReason(session) {
  return sharedSubmissionBlockReason({
    session,
    activeInteraction,
    engine: selectedEngine,
    promptLength: prompt.value.length,
  });
}

function setEngine(engine) {
  selectedEngine = engine === "bedrock_api" ? "bedrock_api" : "codex_cli";
  const nextEngine = selectedEngine === "codex_cli" ? "bedrock_api" : "codex_cli";
  engineToggle.textContent = engineLabel(selectedEngine);
  engineToggle.dataset.engine = selectedEngine;
  engineToggle.title = `点击切换为${engineLabel(nextEngine)}`;
  engineToggle.setAttribute(
    "aria-label",
    `当前执行方式为${engineLabel(selectedEngine)}，点击切换为${engineLabel(nextEngine)}`,
  );
  prompt.placeholder = selectedEngine === "bedrock_api"
    ? "输入要交给 Amazon Bedrock API 回答的问题…"
    : "输入要交给 Codex 执行的需求…";
  prompt.maxLength = selectedEngine === "bedrock_api" ? 4000 : 8000;
  submit.textContent = selectedEngine === "bedrock_api" ? "提问" : "执行";
  showMessage(submitMessage, "");
  if (currentSession) {
    renderSession(currentSession);
  }
}

function visibleHistoryAnchor() {
  if (window.scrollY <= 0) {
    return null;
  }
  return Array.from(history.querySelectorAll("[data-task-id]")).find((item) => {
    const bounds = item.getBoundingClientRect();
    return bounds.bottom > 0 && bounds.top < window.innerHeight;
  }) || null;
}

function setComposerCollapsed(collapsed, { animate = true } = {}) {
  if (composerCollapsed === collapsed) {
    return;
  }
  composerCollapsed = collapsed;
  if (!animate) {
    form.classList.add("is-initializing");
  }
  const anchor = collapsed ? visibleHistoryAnchor() : null;
  const anchorTop = anchor?.getBoundingClientRect().top;
  form.classList.toggle("is-collapsed", collapsed);
  composerHeading.setAttribute("aria-expanded", String(!collapsed));
  composerBody.inert = collapsed;
  if (!animate) {
    window.requestAnimationFrame(() => form.classList.remove("is-initializing"));
  }
  if (!anchor) {
    return;
  }
  const startedAt = performance.now();
  const preserveAnchor = (now) => {
    if (!anchor.isConnected) {
      return;
    }
    const offset = anchor.getBoundingClientRect().top - anchorTop;
    if (Math.abs(offset) > 0.5) {
      window.scrollBy(0, offset);
    }
    if (now - startedAt < COMPOSER_COLLAPSE_ANIMATION_MS) {
      window.requestAnimationFrame(preserveAnchor);
    }
  };
  window.requestAnimationFrame(preserveAnchor);
}

function waitForComposerCollapse() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    let fallbackTimer = null;
    const finish = () => {
      window.clearTimeout(fallbackTimer);
      composerBody.removeEventListener("transitionend", handleTransitionEnd);
      resolve();
    };
    const handleTransitionEnd = (event) => {
      if (event.propertyName === "grid-template-rows") {
        finish();
      }
    };
    composerBody.addEventListener("transitionend", handleTransitionEnd);
    fallbackTimer = window.setTimeout(finish, COMPOSER_COLLAPSE_FALLBACK_MS);
  });
}

function renderSession(session) {
  currentSession = session;
  if (submitMessage.dataset.sessionLoadError === "true") {
    delete submitMessage.dataset.sessionLoadError;
    showMessage(submitMessage, "");
  }
  if (!composerStateInitialized) {
    composerStateInitialized = true;
    setComposerCollapsed(
      session.quick_interaction_running === true
        || session.llm_interaction_running === true,
      { animate: false },
    );
  }
  confirmStopUnknownTerminal =
    session.status === "running" && session.activity === "unknown";
  const reason = submissionBlockReason(session);
  const busy = activeInteraction
    || session.quick_interaction_running === true
    || session.llm_interaction_running === true;
  form.setAttribute("aria-busy", String(busy));
  submit.disabled = Boolean(reason);
  prompt.disabled = busy;
  engineToggle.disabled = busy;
  if (reason && !busy) {
    showMessage(submitMessage, reason);
  } else if (
    busy
    || !submitMessage.classList.contains("message-error")
  ) {
    showMessage(submitMessage, "");
  }
}

function renderSessionLoadError(error) {
  submit.disabled = true;
  prompt.disabled = false;
  engineToggle.disabled = false;
  form.setAttribute("aria-busy", "false");
  submitMessage.dataset.sessionLoadError = "true";
  showMessage(
    submitMessage,
    error.message || "会话状态读取失败。",
    "error",
  );
}

async function loadSession() {
  const session = await quickInteractionClient.loadSession();
  renderSession(session);
  return session;
}

async function performLoad({ append = false } = {}) {
  window.clearTimeout(pollTimer);
  const offset = append ? loadedTasks.length : 0;
  const [historyResult, sessionResult] = await Promise.allSettled([
    quickInteractionClient.listTasks({ offset, limit: PAGE_SIZE }),
    loadSession(),
  ]);
  let active = loadedTasks.some(
    (task) => ["requested", "running"].includes(task.status),
  );
  if (historyResult.status === "fulfilled") {
    const data = historyResult.value;
    loadedTasks = (append
      ? [...loadedTasks, ...data.tasks]
      : mergeLatestTasks(data.tasks)).slice(0, data.total);
    totalTasks = data.total;
    active = renderTasks(loadedTasks);
    activeInteraction = active;
    loadMore.hidden = loadedTasks.length >= totalTasks;
    showMessage(historyMessage, "");
  } else {
    const error = historyResult.reason;
    showMessage(
      historyMessage,
      error.message || "快速交互记录读取失败。",
      "error",
    );
  }
  if (sessionResult.status === "rejected") {
    renderSessionLoadError(sessionResult.reason);
  }
  const session = sessionResult.status === "fulfilled"
    ? sessionResult.value
    : currentSession;
  if (session) {
    renderSession(session);
  }
  const loadFailed = historyResult.status === "rejected"
    || sessionResult.status === "rejected";
  const loadErrors = [historyResult, sessionResult]
    .filter((result) => result.status === "rejected")
    .map((result) => result.reason);
  if (!loadFailed) {
    pollFailureCount = 0;
  } else if (loadErrors.some(isRetryableRequestError)) {
    pollFailureCount += 1;
  }
  const keepPolling = shouldPoll({
    loadFailed,
    loadErrors,
    activeInteraction: active,
    notificationPending: loadedTasks.some((task) => (
      task.notification_status === "pending"
      || task.notification_status === "sending"
    )),
    session,
  });
  if (keepPolling && document.visibilityState !== "hidden") {
    pollTimer = window.setTimeout(load, pollDelay(pollFailureCount));
  }
}

function load({ append = false } = {}) {
  if (append && appendPending) {
    return loadQueue;
  }
  if (append) {
    appendPending = true;
    loadMore.disabled = true;
  }
  const queued = loadQueue.then(() => performLoad({ append }));
  loadQueue = queued.catch(() => {});
  if (!append) {
    return queued;
  }
  return queued.finally(() => {
    appendPending = false;
    loadMore.disabled = false;
  });
}

composerHeading.addEventListener("click", () => {
  setComposerCollapsed(!composerCollapsed);
});

composerHeading.addEventListener("keydown", (event) => {
  if (!["Enter", " "].includes(event.key)) {
    return;
  }
  event.preventDefault();
  setComposerCollapsed(!composerCollapsed);
});

engineToggle.addEventListener("click", () => {
  setEngine(selectedEngine === "codex_cli" ? "bedrock_api" : "codex_cli");
});

prompt.addEventListener("input", () => {
  if (currentSession) {
    renderSession(currentSession);
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = prompt.value.trim();
  if (!value || !currentSession) {
    return;
  }
  submit.disabled = true;
  prompt.disabled = true;
  try {
    await quickInteractionClient.submitTask({
      prompt: value,
      engine: selectedEngine,
      confirmStopUnknownTerminal:
        selectedEngine === "codex_cli" && confirmStopUnknownTerminal,
    });
    prompt.value = "";
    confirmStopUnknownTerminal = false;
    showMessage(submitMessage, "");
    setComposerCollapsed(true);
    await waitForComposerCollapse();
    await load();
  } catch (error) {
    if (
      selectedEngine === "codex_cli"
      && error.code === "quick_interaction_terminal_confirmation_required"
    ) {
      confirmStopUnknownTerminal = true;
      showMessage(submitMessage, "请确认影响后再次点击执行。", "error");
    } else {
      showMessage(
        submitMessage,
        error.message || "快速交互提交失败。",
        "error",
      );
    }
    try {
      await loadSession();
    } catch (sessionError) {
      renderSessionLoadError(sessionError);
    }
  }
});

loadMore.addEventListener("click", () => load({ append: true }));

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    load();
  } else {
    window.clearTimeout(pollTimer);
  }
});

window.addEventListener("pageshow", () => {
  setEngine("codex_cli");
});

setEngine("codex_cli");
load();
