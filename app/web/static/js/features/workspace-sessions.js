"use strict";

(() => {
  const createButton = document.getElementById("workspace-session-create");
  const sessionList = document.getElementById("workspace-session-list");
  const message = document.getElementById("workspace-session-message");
  const dialog = document.getElementById("workspace-session-create-dialog");
  const form = document.getElementById("workspace-session-create-form");
  const workspaceSelect = document.getElementById("workspace-session-workspace");
  const closeButton = document.getElementById("workspace-session-create-close");
  const cancelButton = document.getElementById("workspace-session-create-cancel");
  const confirmButton = document.getElementById("workspace-session-create-confirm");
  const createMessage = document.getElementById("workspace-session-create-message");

  if (
    !(createButton instanceof HTMLButtonElement)
    || !(sessionList instanceof HTMLElement)
    || !(message instanceof HTMLElement)
    || !(dialog instanceof HTMLDialogElement)
    || !(form instanceof HTMLFormElement)
    || !(workspaceSelect instanceof HTMLSelectElement)
    || !(confirmButton instanceof HTMLButtonElement)
    || !(createMessage instanceof HTMLElement)
  ) {
    return;
  }

  let creation = { quick: { available: false }, terminal: { available: false } };
  let workspaces = [];
  let refreshTimer = null;
  let creating = false;
  let hasSessionSnapshot = false;
  let sessionsById = new Map();
  let activeQuickSessionId = new URL(window.location.href).searchParams.get("session");
  const sessionCacheKey = "chub.workspace.sessions.v1";

  const sessionModeInputs = Array.from(
    form.querySelectorAll('input[name="workspace-session-mode"]'),
  ).filter((input) => input instanceof HTMLInputElement);

  const setMessage = (element, text, kind = "") => {
    element.textContent = text;
    element.className = kind ? `message message-${kind}` : "message";
  };

  const setSidebarMessage = (text = "") => {
    message.textContent = text;
    message.classList.toggle("is-visible", Boolean(text));
  };

  const request = async (path, options = {}) => {
    let response;
    try {
      response = await fetch(path, options);
    } catch {
      throw new Error("无法连接 Chub，请检查服务和网络。");
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok || payload?.success !== true) {
      throw new Error(payload?.error?.message || `请求失败（HTTP ${response.status}）。`);
    }
    return payload.data;
  };

  const isSessionListData = (data) => (
    data
    && typeof data === "object"
    && Array.isArray(data.sessions)
    && Array.isArray(data.workspaces)
  );

  const readCachedSessions = () => {
    try {
      const data = JSON.parse(window.sessionStorage.getItem(sessionCacheKey) || "null");
      return isSessionListData(data) ? data : null;
    } catch {
      return null;
    }
  };

  const cacheSessions = (data) => {
    try {
      window.sessionStorage.setItem(sessionCacheKey, JSON.stringify(data));
    } catch {
      // The latest server response remains usable when browser storage is unavailable.
    }
  };

  const setSelectedQuickSessionLocation = (sessionId) => {
    const url = new URL(window.location.href);
    url.searchParams.delete("section");
    url.searchParams.set("session", sessionId);
    window.history.replaceState(window.history.state, "", url);
  };

  const clearSelectedQuickSessionLocation = () => {
    const url = new URL(window.location.href);
    url.searchParams.delete("session");
    window.history.replaceState(window.history.state, "", url);
  };

  const relativeTime = (value) => {
    const timestamp = Date.parse(value);
    if (!Number.isFinite(timestamp)) return "时间未知";
    const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60000));
    if (minutes < 1) return "刚刚";
    if (minutes < 60) return `${minutes} 分钟前`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} 小时前`;
    return `${Math.floor(hours / 24)} 天前`;
  };

  const sessionState = (session) => {
    if (session.quick_interaction_running || session.activity === "working") {
      return "执行中";
    }
    if (session.status === "error" || session.error) return "会话异常";
    if (session.activity === "unknown") return "状态待确认";
    return session.session_mode === "terminal" ? "实时终端 · 等待输入" : "快速交互 · 等待输入";
  };

  const sessionTimeLabel = (session) => {
    return relativeTime(session.last_activity_at || session.created_at);
  };

  const sessionTitle = (session) => session.title || session.workspace_name || "未命名 Session";

  const updateSessionButton = (button, session) => {
    const dot = button.querySelector(".workspace-preview-session-dot");
    const title = button.querySelector(".workspace-preview-session-content strong");
    const meta = button.querySelector(".workspace-preview-session-content small");
    const running = session.quick_interaction_running || session.activity === "working";
    const name = sessionTitle(session);

    button.setAttribute("aria-label", `打开 Session：${name}`);
    button.classList.toggle(
      "is-current",
      session.session_mode === "quick" && session.id === activeQuickSessionId,
    );
    dot?.classList.toggle("is-running", running);
    if (title) title.textContent = name;
    if (meta) meta.textContent = `${sessionState(session)} · ${sessionTimeLabel(session)}`;
  };

  const createSessionButton = (session) => {
    const button = document.createElement("button");
    const dot = document.createElement("span");
    const content = document.createElement("span");
    const title = document.createElement("strong");
    const meta = document.createElement("small");

    button.type = "button";
    button.className = "workspace-preview-session";
    button.dataset.sessionId = session.id;
    dot.className = "workspace-preview-session-dot";
    dot.setAttribute("aria-hidden", "true");
    content.className = "workspace-preview-session-content";
    content.append(title, meta);
    button.append(dot, content);
    button.addEventListener("click", () => {
      const currentSession = sessionsById.get(button.dataset.sessionId);
      if (currentSession) openSession(currentSession, button);
    });
    return button;
  };

  const renderSessions = (sessions) => {
    const orderedSessions = [...sessions].sort(
      (left, right) => Date.parse(right.created_at) - Date.parse(left.created_at),
    );
    sessionsById = new Map(orderedSessions.map((session) => [session.id, session]));

    if (!orderedSessions.length) {
      sessionList.querySelectorAll(":scope > .workspace-preview-session-group").forEach((group) => group.remove());
      let empty = sessionList.querySelector(":scope > .empty-state");
      if (!empty) {
        empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "暂无 Session。";
        sessionList.append(empty);
      }
      return;
    }

    sessionList.querySelector(":scope > .empty-state")?.remove();
    [
      { mode: "quick", title: "快速会话" },
      { mode: "terminal", title: "实时会话" },
    ].forEach(({ mode, title }) => {
      const groupSessions = orderedSessions.filter((session) => session.session_mode === mode);
      let group = sessionList.querySelector(`:scope > .workspace-preview-session-group[data-session-mode="${mode}"]`);
      if (!groupSessions.length) {
        group?.remove();
        return;
      }
      if (!group) {
        group = document.createElement("section");
        const heading = document.createElement("p");
        const items = document.createElement("div");
        group.className = "workspace-preview-session-group";
        group.dataset.sessionMode = mode;
        heading.className = "workspace-preview-session-group-title";
        heading.textContent = title;
        items.className = "workspace-preview-session-group-list";
        group.append(heading, items);
      }
      const items = group.querySelector(".workspace-preview-session-group-list");
      if (!(items instanceof HTMLElement)) return;
      const existingButtons = new Map(
        [...items.querySelectorAll(":scope > .workspace-preview-session")]
          .map((button) => [button.dataset.sessionId, button]),
      );
      existingButtons.forEach((button, sessionId) => {
        if (!groupSessions.some((session) => session.id === sessionId)) button.remove();
      });
      groupSessions.forEach((session, index) => {
        const button = existingButtons.get(session.id) || createSessionButton(session);
        updateSessionButton(button, session);
        if (items.children[index] !== button) {
          items.insertBefore(button, items.children[index] || null);
        }
      });
      sessionList.append(group);
    });
  };

  const syncCreation = () => {
    const usableWorkspaces = workspaces.filter((workspace) => workspace.available);
    const available = creation.quick.available || creation.terminal.available;
    createButton.disabled = !available || !usableWorkspaces.length;
    createButton.title = createButton.disabled
      ? creation.quick.reason || creation.terminal.reason || "当前没有可用工作目录"
      : "";

    workspaceSelect.replaceChildren();
    usableWorkspaces.forEach((workspace) => {
      const option = document.createElement("option");
      option.value = workspace.id;
      option.textContent = workspace.name;
      workspaceSelect.append(option);
    });
    sessionModeInputs.forEach((input) => {
      input.disabled = !creation[input.value]?.available;
    });
    const selected = sessionModeInputs.find((input) => input.checked && !input.disabled);
    if (!selected) {
      const firstAvailable = sessionModeInputs.find((input) => !input.disabled);
      if (firstAvailable) firstAvailable.checked = true;
    }
  };

  const scheduleRefresh = (sessions) => {
    window.clearTimeout(refreshTimer);
    if (sessions.some((session) => session.quick_interaction_running || session.activity === "working")) {
      refreshTimer = window.setTimeout(loadSessions, 3000);
    }
  };

  const restoreSelectedQuickSession = (sessions) => {
    if (!activeQuickSessionId) return false;
    const session = sessions.find((item) => (
      item.id === activeQuickSessionId && item.session_mode === "quick"
    ));
    if (!session) {
      activeQuickSessionId = null;
      clearSelectedQuickSessionLocation();
      if (window.workspaceQuickSessionOpen) {
        window.location.replace("/");
        return true;
      }
      renderSessions(sessions);
      setSidebarMessage("此前选择的快速会话已不可用，已回到工作台。");
      return true;
    }
    if (window.workspaceQuickSessionOpen) return false;
    window.openWorkspaceQuickSession?.(session);
    return false;
  };

  const applySessionData = (data, { restoreSelectedSession = false } = {}) => {
    creation = {
      quick: data.quick_creation || { available: false },
      terminal: data.terminal_creation || { available: false },
    };
    workspaces = data.workspaces;
    syncCreation();
    renderSessions(data.sessions);
    hasSessionSnapshot = true;
    return restoreSelectedSession && restoreSelectedQuickSession(data.sessions);
  };

  const loadSessions = async () => {
    try {
      const data = await request("/api/codex/sessions");
      if (!isSessionListData(data)) {
        throw new Error("Chub 返回了无法识别的会话数据。");
      }
      const selectedSessionUnavailable = applySessionData(data, {
        restoreSelectedSession: true,
      });
      cacheSessions(data);
      if (!selectedSessionUnavailable) setSidebarMessage("");
      scheduleRefresh(data.sessions);
    } catch (error) {
      if (!hasSessionSnapshot) {
        createButton.disabled = true;
        createButton.title = error.message;
        setSidebarMessage(error.message || "会话读取失败，请稍后重试。");
        return;
      }
      setSidebarMessage("会话状态暂时无法更新，正在显示最近一次列表。");
    }
  };

  const openSession = async (session, button) => {
    setSidebarMessage("");
    button.disabled = true;
    try {
      if (session.session_mode === "quick") {
        activeQuickSessionId = session.id;
        setSelectedQuickSessionLocation(session.id);
        renderSessions([...sessionsById.values()]);
        if (typeof window.openWorkspaceQuickSession === "function") {
          window.openWorkspaceQuickSession(session);
          button.disabled = false;
          return;
        }
        window.location.assign(`/codex/${encodeURIComponent(session.id)}/quick-interactions/conversation`);
        return;
      }
      const data = await request(`/api/codex/sessions/${encodeURIComponent(session.id)}/access`, { method: "POST" });
      window.location.assign(data.terminal_url);
    } catch (error) {
      setSidebarMessage(error.message || "打开 Session 失败，请稍后重试。");
      button.disabled = false;
      loadSessions();
    }
  };

  window.selectWorkspaceQuickSession = (sessionId) => {
    if (typeof sessionId !== "string" || !sessionId) return false;
    const session = sessionsById.get(sessionId);
    if (session && session.session_mode !== "quick") return false;
    activeQuickSessionId = sessionId;
    setSelectedQuickSessionLocation(sessionId);
    renderSessions([...sessionsById.values()]);
    if (!session) void loadSessions();
    return true;
  };

  window.clearWorkspaceQuickSessionSelection = () => {
    if (!activeQuickSessionId) return;
    activeQuickSessionId = null;
    clearSelectedQuickSessionLocation();
    renderSessions([...sessionsById.values()]);
  };

  window.updateWorkspaceQuickSessionActivity = (sessionId, running, updatedAt) => {
    const session = sessionsById.get(sessionId);
    if (!session) {
      void loadSessions();
      return false;
    }
    const activityTime = typeof updatedAt === "string" ? updatedAt : session.last_activity_at;
    const updatedSession = {
      ...session,
      quick_interaction_running: running,
      quick_interaction_updated_at: running ? activityTime : null,
      last_activity_at: activityTime || session.last_activity_at,
    };
    sessionsById.set(sessionId, updatedSession);
    sessionList.querySelectorAll(".workspace-preview-session").forEach((button) => {
      if (button.dataset.sessionId === sessionId) {
        updateSessionButton(button, updatedSession);
      }
    });
    const cachedSessions = readCachedSessions();
    if (cachedSessions) {
      cachedSessions.sessions = cachedSessions.sessions.map((item) => (
        item.id === sessionId ? updatedSession : item
      ));
      cacheSessions(cachedSessions);
    }
    scheduleRefresh([...sessionsById.values()]);
    return true;
  };

  const closeDialog = () => {
    if (!creating && dialog.open) dialog.close();
  };

  createButton.addEventListener("click", () => {
    setMessage(createMessage, "");
    dialog.showModal();
  });
  closeButton?.addEventListener("click", closeDialog);
  cancelButton?.addEventListener("click", closeDialog);
  dialog.addEventListener("cancel", (event) => {
    if (creating) event.preventDefault();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const selectedMode = sessionModeInputs.find((input) => input.checked && !input.disabled);
    if (!workspaceSelect.value || !selectedMode) return;

    creating = true;
    confirmButton.disabled = true;
    confirmButton.textContent = "创建中…";
    workspaceSelect.disabled = true;
    sessionModeInputs.forEach((input) => { input.disabled = true; });
    if (closeButton instanceof HTMLButtonElement) closeButton.disabled = true;
    if (cancelButton instanceof HTMLButtonElement) cancelButton.disabled = true;
    setMessage(createMessage, "正在创建 Session…");
    try {
      await request("/api/codex/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: workspaceSelect.value, session_mode: selectedMode.value }),
      });
      dialog.close();
      await loadSessions();
    } catch (error) {
      setMessage(createMessage, error.message || "Session 创建失败，请稍后重试。", "error");
    } finally {
      creating = false;
      confirmButton.disabled = false;
      confirmButton.textContent = "创建";
      workspaceSelect.disabled = false;
      if (closeButton instanceof HTMLButtonElement) closeButton.disabled = false;
      if (cancelButton instanceof HTMLButtonElement) cancelButton.disabled = false;
      syncCreation();
    }
  });

  const cachedSessions = readCachedSessions();
  if (cachedSessions) {
    applySessionData(cachedSessions);
  }
  loadSessions();
})();
