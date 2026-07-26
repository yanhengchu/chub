"use strict";

const sessionId = document.body.dataset.sessionId;
const token = sessionStorage.getItem("hub.sessionToken")
  || localStorage.getItem("hub.savedToken")
  || "";
const form = document.querySelector("#quick-interaction-form");
const composerHeading = form.querySelector(".quick-interaction-page-heading");
const composerBody = document.querySelector("#quick-interaction-composer-body");
const prompt = document.querySelector("#quick-interaction-prompt");
const submit = document.querySelector("#quick-interaction-submit");
const submitMessage = document.querySelector("#quick-interaction-submit-message");
const sessionMeta = document.querySelector("#quick-interaction-session-meta");
const warning = document.querySelector("#quick-interaction-warning");
const historyMessage = document.querySelector("#quick-interaction-history-message");
const history = document.querySelector("#quick-interaction-history");
const loadMore = document.querySelector("#quick-interaction-load-more");
const PAGE_SIZE = 3;
let pollTimer = null;
let loadedTasks = [];
let totalTasks = 0;
let loadQueue = Promise.resolve();
let appendPending = false;
let currentSession = null;
let confirmStopUnknownTerminal = false;
let composerCollapsed = false;
let composerStateInitialized = false;

function showMessage(element, text, kind = "") {
  element.textContent = text;
  element.className = "message";
  if (kind) {
    element.classList.add(`message-${kind}`);
  }
}

function formatTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("zh-CN", { hour12: false });
}

function statusText(status) {
  return {
    requested: "等待执行",
    running: "执行中",
    succeeded: "已完成",
    failed: "执行失败",
    timed_out: "执行超时",
    needs_terminal: "需要实时终端",
  }[status] || status;
}

async function request(path, options = {}) {
  if (!token) {
    const error = new Error("请先返回首页连接节点。");
    error.code = "access_required";
    throw error;
  }
  const response = await fetch(path, {
    cache: "no-store",
    ...options,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });
  const payload = await response.json();
  if (!response.ok || payload.success !== true) {
    const error = new Error(payload?.error?.message || "请求失败。");
    error.code = payload?.error?.code || "request_failed";
    throw error;
  }
  return payload.data;
}

function taskSignature(task) {
  return JSON.stringify([
    task.status,
    task.updated_at,
    task.prompt,
    task.result,
    task.error,
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
    const time = document.createElement("span");
    const promptLabel = document.createElement("span");
    const promptContent = document.createElement("pre");
    item.className = `quick-interaction-history-item quick-interaction-${task.status}`;
    heading.className = "quick-interaction-history-heading";
    status.textContent = statusText(task.status);
    time.textContent = formatTime(task.updated_at);
    promptLabel.className = "quick-interaction-history-label";
    promptLabel.textContent = "提交内容";
    promptContent.className = "quick-interaction-history-content";
    promptContent.textContent = task.prompt || "历史任务未保存提交内容。";
    heading.append(status, time);
    item.append(heading, promptLabel, promptContent);
    if (task.result || task.error) {
      const resultLabel = document.createElement("span");
      const result = document.createElement("pre");
      resultLabel.className = "quick-interaction-history-label";
      resultLabel.textContent = task.result ? "执行结果" : "失败原因";
      result.className = "quick-interaction-history-content";
      result.textContent = task.result || task.error;
      item.append(resultLabel, result);
    }
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
  if (!session) {
    return "正在读取会话状态…";
  }
  if (!session.codex_session_id) {
    return "会话尚未启动，请先通过实时终端建立会话。";
  }
  if (session.status === "error") {
    return "会话当前异常，请先通过实时终端重试。";
  }
  if (session.quick_interaction_running) {
    return "当前快速交互正在执行，请等待任务结束。";
  }
  if (session.activity === "working") {
    return session.activity_source === "terminal"
      ? "实时终端正在执行，请等待当前任务结束。"
      : "当前会话正在执行，请等待任务结束。";
  }
  if (session.permission_mode === "ask") {
    return "Ask for approval 需要进入实时终端完成审批。";
  }
  return "";
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

function setComposerCollapsed(collapsed) {
  if (composerCollapsed === collapsed) {
    return;
  }
  composerCollapsed = collapsed;
  const anchor = visibleHistoryAnchor();
  const anchorTop = anchor?.getBoundingClientRect().top;
  form.classList.toggle("is-collapsed", collapsed);
  composerHeading.setAttribute("aria-expanded", String(!collapsed));
  composerBody.inert = collapsed;
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
    if (now - startedAt < 220) {
      window.requestAnimationFrame(preserveAnchor);
    }
  };
  window.requestAnimationFrame(preserveAnchor);
}

function renderSession(session) {
  currentSession = session;
  if (submitMessage.dataset.sessionLoadError === "true") {
    delete submitMessage.dataset.sessionLoadError;
    showMessage(submitMessage, "");
  }
  if (!composerStateInitialized) {
    composerStateInitialized = true;
    setComposerCollapsed(session.quick_interaction_running === true);
  }
  const title = session.title || session.workspace_name || "Codex Session";
  sessionMeta.textContent = title;
  confirmStopUnknownTerminal =
    session.status === "running" && session.activity === "unknown";
  warning.hidden = !confirmStopUnknownTerminal;
  const reason = submissionBlockReason(session);
  form.setAttribute("aria-busy", String(session.quick_interaction_running === true));
  submit.disabled = Boolean(reason);
  prompt.disabled = session.quick_interaction_running === true;
  if (reason && !session.quick_interaction_running) {
    showMessage(submitMessage, reason);
  } else if (
    session.quick_interaction_running
    || !submitMessage.classList.contains("message-error")
  ) {
    showMessage(submitMessage, "");
  }
}

function renderSessionLoadError(error) {
  submit.disabled = true;
  prompt.disabled = false;
  form.setAttribute("aria-busy", "false");
  submitMessage.dataset.sessionLoadError = "true";
  showMessage(
    submitMessage,
    error.message || "会话状态读取失败。",
    "error",
  );
}

async function loadSession() {
  const data = await request("/api/codex/sessions");
  const session = data.sessions.find((item) => item.id === sessionId);
  if (!session) {
    throw new Error("会话不存在或已经归档。");
  }
  renderSession(session);
  return session;
}

async function performLoad({ append = false } = {}) {
  window.clearTimeout(pollTimer);
  const offset = append ? loadedTasks.length : 0;
  const [historyResult, sessionResult] = await Promise.allSettled([
    request(
      `/api/codex/sessions/${encodeURIComponent(sessionId)}/quick-interactions`
      + `?offset=${offset}&limit=${PAGE_SIZE}`,
    ),
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
  const loadFailed = historyResult.status === "rejected"
    || sessionResult.status === "rejected";
  const shouldPoll = loadFailed
    || active
    || session?.activity === "working"
    || (session?.status === "running" && session.activity === "unknown");
  if (shouldPoll && document.visibilityState !== "hidden") {
    pollTimer = window.setTimeout(load, 1500);
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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = prompt.value.trim();
  if (!value || !currentSession) {
    return;
  }
  submit.disabled = true;
  prompt.disabled = true;
  showMessage(submitMessage, "正在提交快速交互…");
  try {
    await request(
      `/api/codex/sessions/${encodeURIComponent(sessionId)}/quick-interactions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt: value,
          confirm_stop_unknown_terminal: confirmStopUnknownTerminal,
        }),
      },
    );
    prompt.value = "";
    setComposerCollapsed(true);
    confirmStopUnknownTerminal = false;
    warning.hidden = true;
    showMessage(submitMessage, "");
    await load();
  } catch (error) {
    if (error.code === "quick_interaction_terminal_confirmation_required") {
      confirmStopUnknownTerminal = true;
      warning.hidden = false;
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

load();
