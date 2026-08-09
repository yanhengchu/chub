"use strict";

(function exposeQuickInteractionCore(root) {
  const LONG_RUNNING_THRESHOLD_MS = 10 * 60 * 1000;
  const INITIAL_POLL_DELAY_MS = 1500;
  const MAX_POLL_DELAY_MS = 10000;
  const PAGE_SIZE_KEY = "hub.quickInteractionPageSize.v1";

  async function request(token, path, options = {}) {
    const response = await fetch(path, {
      cache: "no-store",
      ...options,
      headers: {
        ...(options.headers || {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
    const payload = await response.json();
    if (!response.ok || payload.success !== true) {
      const error = new Error(payload?.error?.message || "请求失败。");
      error.code = payload?.error?.code || "request_failed";
      error.status = response.status;
      error.retryable = response.status === 408
        || response.status === 429
        || response.status >= 500;
      throw error;
    }
    return payload.data;
  }

  function formatTime(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? ""
      : date.toLocaleString("zh-CN", { hour12: false });
  }

  function statusText(task) {
    if (
      task.status === "running"
      && Date.now() - new Date(task.created_at).getTime() >= LONG_RUNNING_THRESHOLD_MS
    ) {
      return "执行时间较长，仍在运行";
    }
    return {
      requested: "等待执行",
      running: "执行中",
      succeeded: "已完成",
      failed: "执行失败",
      timed_out: "执行超时",
      cancelled: "已停止",
      needs_terminal: "需要实时终端",
    }[task.status] || task.status;
  }

  function canSubmit({ prompt, session, blocked }) {
    return Boolean(prompt.trim() && session && !blocked);
  }

  function readStoredValue(storageName, key) {
    try {
      return root[storageName]?.getItem(key) || "";
    } catch (_error) {
      return "";
    }
  }

  function readPageSize(storage) {
    try {
      const value = storage === undefined
        ? readStoredValue("localStorage", PAGE_SIZE_KEY)
        : storage?.getItem(PAGE_SIZE_KEY);
      return value === "10" ? 10 : 5;
    } catch (_error) {
      return 5;
    }
  }

  function readToken() {
    return readStoredValue("sessionStorage", "hub.sessionToken")
      || readStoredValue("localStorage", "hub.savedToken");
  }

  function submissionBlockReason({
    session,
    activeInteraction,
    promptLength,
  }) {
    if (!session) {
      return "正在读取会话状态…";
    }
    if (activeInteraction) {
      return "当前快速交互正在执行，请等待任务结束。";
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

  function isRetryableRequestError(error) {
    return error?.retryable !== false;
  }

  function pollDelay(failureCount = 0) {
    if (failureCount <= 1) {
      return INITIAL_POLL_DELAY_MS;
    }
    return Math.min(
      MAX_POLL_DELAY_MS,
      INITIAL_POLL_DELAY_MS * (2 ** (failureCount - 1)),
    );
  }

  function shouldPoll({
    loadFailed,
    loadErrors = [],
    activeInteraction,
    notificationPending = false,
    session,
  }) {
    const retryableLoadFailure = loadFailed
      && (loadErrors.length === 0 || loadErrors.some(isRetryableRequestError));
    if (loadFailed && loadErrors.length > 0 && !retryableLoadFailure) {
      return false;
    }
    return Boolean(
      retryableLoadFailure
      || activeInteraction
      || notificationPending
      || session?.quick_interaction_running === true
      || session?.activity === "working"
      || (session?.status === "running" && session.activity === "unknown")
    );
  }

  function createClient({ token, sessionId }) {
    const encodedSessionId = encodeURIComponent(sessionId);
    return Object.freeze({
      async loadSession() {
        const data = await request(token, "/api/codex/sessions");
        const session = data.sessions.find((item) => item.id === sessionId);
        if (!session) {
          const error = new Error("会话不存在或已经归档。");
          error.code = "codex_session_not_found";
          error.retryable = false;
          throw error;
        }
        return session;
      },

      listTasks({
        offset = 0,
        limit = 5,
        order = "task",
        before = null,
      } = {}) {
        const query = new URLSearchParams({
          limit: String(limit),
          order,
        });
        if (before) {
          query.set("before_created_at", before.createdAt);
          query.set("before_id", before.id);
        } else if (offset) {
          query.set("offset", String(offset));
        }
        return request(
          token,
          `/api/codex/sessions/${encodedSessionId}/quick-interactions?${query}`,
        );
      },

      submitTask({ prompt, confirmStopUnknownTerminal = false }) {
        return request(
          token,
          `/api/codex/sessions/${encodedSessionId}/quick-interactions`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              prompt,
              confirm_stop_unknown_terminal: confirmStopUnknownTerminal,
            }),
          },
        );
      },

      setPinned(taskId, pinned) {
        return request(
          token,
          `/api/codex/sessions/${encodedSessionId}`
          + `/quick-interactions/${encodeURIComponent(taskId)}/pin`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pinned }),
          },
        );
      },
    });
  }

  const quickInteractionCore = Object.freeze({
    canSubmit,
    createClient,
    formatTime,
    isRetryableRequestError,
    pollDelay,
    readPageSize,
    readToken,
    request,
    statusText,
    submissionBlockReason,
    shouldPoll,
  });
  root.QuickInteractionCore = quickInteractionCore;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = quickInteractionCore;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
