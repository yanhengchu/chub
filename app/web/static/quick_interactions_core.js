"use strict";

(function exposeQuickInteractionCore(root) {
  const LONG_RUNNING_THRESHOLD_MS = 10 * 60 * 1000;
  const INITIAL_POLL_DELAY_MS = 1500;
  const MAX_POLL_DELAY_MS = 10000;
  const CONNECTION_FAILURE_GRACE_ATTEMPTS = 3;
  const PAGE_SIZE_KEY = "hub.quickInteractionPageSize.v1";
  const CREATION_PREFERENCE_ERRORS = new Set([
    "codex_model_catalog_unavailable",
    "codex_model_unavailable",
    "codex_reasoning_effort_requires_model",
    "codex_reasoning_effort_unsupported",
  ]);
  const ERROR_SOURCE_LABELS = Object.freeze({
    chub: "Chub",
    runtime: "Codex CLI（上游 Runtime）",
  });

  function errorSourceLabel(source) {
    return ERROR_SOURCE_LABELS[source] || "";
  }

  function formatErrorMessage(error, fallback) {
    const message = error?.message || fallback;
    const label = errorSourceLabel(error?.source || error?.error_source);
    return label ? `${label}：${message}` : message;
  }

  async function request(path, options = {}) {
    let response;
    try {
      response = await fetch(path, {
        cache: "no-store",
        ...options,
        headers: {
          ...(options.headers || {}),
        },
      });
    } catch (_error) {
      const error = new Error("连接 Chub 失败，正在重试。");
      error.code = "chub_connection_lost";
      error.retryable = true;
      error.transport = true;
      throw error;
    }
    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      const error = new Error("连接 Chub 失败，正在重试。");
      error.code = "chub_connection_lost";
      error.retryable = true;
      error.transport = true;
      throw error;
    }
    if (!response.ok || payload.success !== true) {
      const error = new Error(payload?.error?.message || "请求失败。");
      error.code = payload?.error?.code || "request_failed";
      error.source = payload?.error?.source || null;
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
      task.status === "failed"
      && task.error === "服务重启导致正在执行的任务中断，请重新提交任务。"
    ) {
      return "服务重启中断";
    }
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

  function sessionListPath() {
    return "/api/codex/sessions";
  }

  function readSessionCreationPreferences(storage) {
    return {
      permissionMode: null,
      model: null,
      reasoningEffort: null,
    };
  }

  function clearSessionModelPreferences(_storage) {}

  function shouldRetrySessionCreationWithDefaults(error, preferences) {
    return Boolean(
      (preferences.model || preferences.reasoningEffort)
      && CREATION_PREFERENCE_ERRORS.has(error?.code),
    );
  }

  function sessionUsageBlockReason(session) {
    const owner = session?.usage?.owner || "none";
    if (owner === "external") {
      return "This is open in another app, close it there to continue here.";
    }
    if (owner === "unknown") {
      return "无法确认 Session 占用状态，请刷新后重试。";
    }
    return "";
  }

  function sessionDeleteBlockReason(session) {
    const owner = session?.usage?.owner || "none";
    return owner === "external"
      ? "This is open in another app, close it there to continue here."
      : "";
  }

  function sessionArchiveBlockReason(session) {
    const owner = session?.usage?.owner || "none";
    if (owner === "external") {
      return "This is open in another app, close it there to continue here.";
    }
    const phase = session?.usage?.phase || "unknown";
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

  function sessionUsageStatus(session) {
    const owner = session?.usage?.owner || "none";
    if (owner === "external") {
      return "其他应用占用";
    }
    if (owner === "unknown") {
      return "状态未知";
    }
    return "";
  }

  function sessionStopReady(session) {
    const owner = session?.usage?.owner || "none";
    const phase = session?.usage?.phase || "unknown";
    return (owner === "terminal" && phase === "running")
      || (
        owner === "quick_worker"
        && ["running", "waiting_result"].includes(phase)
      );
  }

  function submissionBlockReason({
    session,
    activeInteraction,
    promptLength,
  }) {
    if (!session) {
      return "正在读取会话状态…";
    }
    const usageBlock = sessionUsageBlockReason(session);
    if (usageBlock) {
      return usageBlock;
    }
    if (session.status === "error") {
      return "会话当前异常，请重试或调整配置后再提交。";
    }
    if (activeInteraction) {
      return "当前快速交互正在执行，请等待任务结束。";
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
      return "当前 Session 使用 Ask for approval，请改为只读、自动审核或完全访问权限。";
    }
    return "";
  }

  function sessionSwitcherStatus(session) {
    const usageStatus = sessionUsageStatus(session);
    if (usageStatus) {
      return usageStatus;
    }
    if (session.status === "error" || session.error) {
      return "异常";
    }
    if (
      session.quick_interaction_running === true
      || session.activity === "working"
    ) {
      return "执行中";
    }
    if (session.permission_mode === "ask") {
      return "权限需调整";
    }
    if (
      session.status === "new"
      || session.status === "stopped"
      || session.activity === "idle"
    ) {
      return "待输入";
    }
    return "状态未知";
  }

  function sessionSwitcherEntries(sessions) {
    const visible = sessions.filter(isQuickInteractionSession);
    return [...visible].sort((left, right) => {
      const createdDifference = Date.parse(right.created_at) - Date.parse(left.created_at);
      if (Number.isFinite(createdDifference) && createdDifference !== 0) {
        return createdDifference;
      }
      const leftId = String(left.id);
      const rightId = String(right.id);
      return leftId < rightId ? 1 : leftId > rightId ? -1 : 0;
    });
  }

  function isQuickInteractionSession(session) {
    return session.session_mode === "quick"
      && session.workspace_id !== "weixin-translation";
  }

  function sessionSwitcherLabels(sessions) {
    const labels = new Map();
    sessions.forEach((session) => {
      const slot = session.weixin_session_slot;
      const label = Number.isInteger(slot) && slot >= 1 && slot <= 9
        ? `S${slot}`
        : "S";
      labels.set(session.id, label);
    });
    return labels;
  }

  function firstSessionAfterArchive(sessions, archivedSessionId) {
    return sessionSwitcherEntries(sessions).find(
      (session) => session.id !== archivedSessionId,
    ) || null;
  }

  function sessionNavigationMode({
    button = 0,
    current = false,
    altKey = false,
    ctrlKey = false,
    metaKey = false,
    shiftKey = false,
  }) {
    if (button === 1 || ctrlKey || metaKey || shiftKey) {
      return "new-tab";
    }
    if (button !== 0 || altKey) {
      return "default";
    }
    return current ? "ignore" : "replace";
  }

  function isRetryableRequestError(error) {
    return error?.retryable !== false;
  }

  function isTemporaryReconnectError(error) {
    return error?.transport === true
      || (error?.retryable === true && error?.status >= 500);
  }

  function shouldSuppressReconnectError({
    loadErrors = [],
    restartPending = false,
    failureCount = 0,
  }) {
    if (
      loadErrors.length === 0
      || loadErrors.some((error) => !isTemporaryReconnectError(error))
    ) {
      return false;
    }
    if (restartPending) {
      return true;
    }
    return loadErrors.some((error) => error?.transport === true)
      && failureCount <= CONNECTION_FAILURE_GRACE_ATTEMPTS;
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
    restartPending = false,
    session,
    sessions = [],
  }) {
    const retryableLoadFailure = loadFailed
      && (loadErrors.length === 0 || loadErrors.some(isRetryableRequestError));
    if (loadFailed && loadErrors.length > 0 && !retryableLoadFailure) {
      return false;
    }
    const sessionListActive = sessions.some((item) => (
      item?.quick_interaction_running === true
      || item?.activity === "working"
      || (
        item?.status === "running"
        && item?.activity === "unknown"
      )
    ));
    return Boolean(
      retryableLoadFailure
      || activeInteraction
      || notificationPending
      || restartPending
      || sessionListActive
      || session?.quick_interaction_running === true
      || session?.activity === "working"
      || (session?.status === "running" && session.activity === "unknown")
    );
  }

  function createClient({ sessionId }) {
    const encodedSessionId = encodeURIComponent(sessionId);
    async function loadSessionContext() {
      const data = await request(sessionListPath());
      const quickCreation = data.quick_creation;
      const sessions = (Array.isArray(data.sessions) ? data.sessions : [])
        .filter(isQuickInteractionSession);
      let session = sessions.find((item) => item.id === sessionId);
      if (!session) {
        session = await request(`/api/codex/sessions/${encodedSessionId}`);
      }
      if (!session) {
        const error = new Error("会话不存在或已经归档。");
        error.code = "codex_session_not_found";
        error.source = "chub";
        error.retryable = false;
        throw error;
      }
      return {
        session,
        sessions,
        available: quickCreation?.available === true,
        unavailableReason: quickCreation?.reason || "",
        workspaces: Array.isArray(data.workspaces) ? data.workspaces : [],
      };
    }

    return Object.freeze({
      async loadSession() {
        return (await loadSessionContext()).session;
      },

      loadSessionContext,

      updateSessionConfiguration({ permissionMode, model, reasoningEffort }) {
        return request(
          `/api/codex/sessions/${encodedSessionId}/configuration`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              permission_mode: permissionMode,
              model,
              reasoning_effort: reasoningEffort,
            }),
          },
        );
      },

      createSession({ workspaceId, permissionMode, model, reasoningEffort }) {
        return request(
          "/api/codex/sessions",
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              workspace_id: workspaceId,
              session_mode: "quick",
            }),
          },
        );
      },

      renameSession(title) {
        return request(
          `/api/codex/sessions/${encodedSessionId}/title`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title }),
          },
        );
      },

      archiveSession() {
        return request(
          `/api/codex/sessions/${encodedSessionId}/archive`,
          { method: "POST" },
        );
      },

      stopSession() {
        return request(
          `/api/codex/sessions/${encodedSessionId}/stop`,
          { method: "POST" },
        );
      },

      deleteSession() {
        return request(
          `/api/codex/sessions/${encodedSessionId}`,
          { method: "DELETE" },
        );
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
          `/api/codex/sessions/${encodedSessionId}/quick-interactions?${query}`,
        );
      },

      submitTask({ prompt, confirmStopUnknownTerminal = false }) {
        return request(
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

    });
  }

  const quickInteractionCore = Object.freeze({
    canSubmit,
    clearSessionModelPreferences,
    createClient,
    errorSourceLabel,
    formatTime,
    formatErrorMessage,
    isRetryableRequestError,
    shouldSuppressReconnectError,
    pollDelay,
    readPageSize,
    readModelCatalog: () => request("/api/codex/models"),
    readSessionCreationPreferences,
    request,
    firstSessionAfterArchive,
    sessionNavigationMode,
    sessionSwitcherEntries,
    sessionSwitcherLabels,
    sessionUsageBlockReason,
    sessionDeleteBlockReason,
    sessionArchiveBlockReason,
    sessionUsageStatus,
    sessionStopReady,
    sessionSwitcherStatus,
    statusText,
    submissionBlockReason,
    shouldRetrySessionCreationWithDefaults,
    shouldPoll,
  });
  root.QuickInteractionCore = quickInteractionCore;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = quickInteractionCore;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
