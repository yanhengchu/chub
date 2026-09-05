"use strict";

(() => {
  const createButton = document.getElementById("workspace-session-create");
  const sessionList = document.getElementById("workspace-session-list");
  const quickSessionToolbar = document.getElementById("workspace-quick-session-toolbar");
  const dialog = document.getElementById("workspace-session-create-dialog");
  const form = document.getElementById("workspace-session-create-form");
  const workspaceSelect = document.getElementById("workspace-session-workspace");
  const workspaceTrigger = document.getElementById("workspace-session-workspace-trigger");
  const workspaceValue = document.getElementById("workspace-session-workspace-value");
  const workspaceMenu = document.getElementById("workspace-session-workspace-menu");
  const closeButton = document.getElementById("workspace-session-create-close");
  const cancelButton = document.getElementById("workspace-session-create-cancel");
  const confirmButton = document.getElementById("workspace-session-create-confirm");
  const createMessage = document.getElementById("workspace-session-create-message");
  const renameDialog = document.getElementById("workspace-session-rename-dialog");
  const renameForm = document.getElementById("workspace-session-rename-form");
  const renameInput = document.getElementById("workspace-session-rename-input");
  const renameMessage = document.getElementById("workspace-session-rename-message");
  const renameCloseButton = document.getElementById("workspace-session-rename-close");
  const renameCancelButton = document.getElementById("workspace-session-rename-cancel");
  const renameConfirmButton = document.getElementById("workspace-session-rename-confirm");

  if (
    !(createButton instanceof HTMLButtonElement)
    || !(sessionList instanceof HTMLElement)
    || !(quickSessionToolbar instanceof HTMLElement)
    || !(dialog instanceof HTMLDialogElement)
    || !(form instanceof HTMLFormElement)
    || !(workspaceSelect instanceof HTMLInputElement)
    || !(workspaceTrigger instanceof HTMLButtonElement)
    || !(workspaceValue instanceof HTMLElement)
    || !(workspaceMenu instanceof HTMLElement)
    || !(confirmButton instanceof HTMLButtonElement)
    || !(createMessage instanceof HTMLElement)
    || !(renameDialog instanceof HTMLDialogElement)
    || !(renameForm instanceof HTMLFormElement)
    || !(renameInput instanceof HTMLInputElement)
    || !(renameMessage instanceof HTMLElement)
    || !(renameConfirmButton instanceof HTMLButtonElement)
  ) {
    return;
  }

  let creation = { quick: { available: false }, terminal: { available: false } };
  let workspaces = [];
  let refreshTimer = null;
  let sessionRequestGeneration = 0;
  let creating = false;
  let hasSessionSnapshot = false;
  let sessionsById = new Map();
  const pendingSessionMutations = new Set();
  let activeQuickSessionId = new URL(window.location.href).searchParams.get("session");
  let renameSessionId = null;
  let renaming = false;
  let sessionActionMenu = null;
  let openSessionActionSessionId = null;
  let openSessionActionTrigger = null;
  let sidebarMessageClearTimer = null;
  let sidebarMessageVisibleUntil = 0;
  const sessionCacheKey = "chub.workspace.sessions.v1";
  const sidebarMessageMinimumVisibleMs = 6000;

  const sessionModeInputs = Array.from(
    form.querySelectorAll('input[name="workspace-session-mode"]'),
  ).filter((input) => input instanceof HTMLInputElement);
  const workspacePicker = window.createChoicePicker?.({
    trigger: workspaceTrigger,
    value: workspaceValue,
    menu: workspaceMenu,
    onSelect: (workspaceId) => {
      workspaceSelect.value = workspaceId;
    },
  });

  if (!workspacePicker) return;

  const setMessage = (element, text, kind = "") => {
    element.textContent = text;
    element.className = kind ? `message message-${kind}` : "message";
  };

  const setSidebarMessage = (text = "") => {
    window.clearTimeout(sidebarMessageClearTimer);
    if (text) {
      sidebarMessageVisibleUntil = Date.now() + sidebarMessageMinimumVisibleMs;
      window.setWorkspaceToolbarError?.(text);
      return;
    }
    const remaining = sidebarMessageVisibleUntil - Date.now();
    if (remaining > 0) {
      sidebarMessageClearTimer = window.setTimeout(() => {
        sidebarMessageVisibleUntil = 0;
        window.setWorkspaceToolbarError?.();
      }, remaining);
      return;
    }
    window.setWorkspaceToolbarError?.();
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
    const owner = session.usage?.owner;
    const phase = session.usage?.phase;
    if (owner === "external") return "其他应用 · 正在使用";
    if (owner === "unknown") return "占用状态未知 · 请刷新";
    if (session.error === "terminal_backend_failed") return "终端连接异常 · 可重试";
    if (session.status === "error" || session.error) return "会话异常 · 可重试";
    if (session.status === "new") {
      return session.session_mode === "terminal" ? "尚未启动 · 可进入" : "等待输入";
    }
    if (session.session_mode === "terminal" && owner === "terminal") {
      if (phase === "running" || session.activity === "working") {
        return "执行中";
      }
      if (phase === "unknown") return "正在使用";
      return "等待输入";
    }
    if (session.quick_interaction_running || session.activity === "working") {
      return "执行中";
    }
    if (session.activity === "unknown") return "活动状态未知 · 请刷新";
    return "等待输入";
  };

  const sessionTimeLabel = (session) => {
    return relativeTime(session.last_activity_at || session.created_at);
  };

  const sessionTitle = (session) => session.title || session.workspace_name || "未命名 Session";

  const isVisibleQuickSession = (session) => (
    session.session_mode === "quick" && session.workspace_id !== "weixin-translation"
  );

  const quickSessionLabel = (session) => {
    const slot = session.weixin_session_slot;
    return Number.isInteger(slot) && slot >= 1 && slot <= 9 ? `S${slot}` : "S";
  };

  const sessionIsExternallyOccupied = (session) => session.usage?.owner === "external";

  const sessionNeedsRefresh = (session) => {
    const usage = session.usage;
    return usage?.owner === "external"
      || usage?.owner === "unknown"
      || (usage?.owner === "terminal" && usage.phase === "unknown")
      || session.activity === "unknown";
  };

  const sessionIsWorking = (session) => {
    const usage = session.usage;
    const usageRunning = usage && (
      (usage.owner === "terminal" && usage.phase === "running")
      || (usage.owner === "quick_worker" && ["running", "waiting_result"].includes(usage.phase))
    );
    return session.quick_interaction_running || session.activity === "working" || usageRunning;
  };

  const sessionHasActiveExecution = (session) => {
    const usage = session.usage;
    return Boolean(usage && (
      (usage.owner === "terminal" && usage.phase === "running")
      || (usage.owner === "quick_worker" && ["running", "waiting_result"].includes(usage.phase))
    ));
  };

  const sessionMoreState = (session) => {
    const mutationPending = pendingSessionMutations.has(session.id);
    const activeExecution = sessionHasActiveExecution(session);
    if (mutationPending) {
      return {
        rename: { disabled: true, title: "操作进行中" },
        stop: { disabled: true, title: "操作进行中" },
        archive: { disabled: true, title: "操作进行中" },
        delete: { disabled: true, title: "操作进行中" },
      };
    }
    return {
      rename: { disabled: false, title: "重命名 Session" },
      stop: {
        disabled: !activeExecution,
        title: activeExecution ? "停止 Session" : "当前没有正在执行的任务",
      },
      archive: {
        disabled: session.can_archive === false || activeExecution,
        title: activeExecution
          ? "Session 当前正在执行，请先停止或等待任务结束后再归档。"
          : session.can_archive === false
            ? "当前 Session 暂不可归档"
            : "归档 Session",
      },
      delete: { disabled: false, title: "永久删除 Session" },
    };
  };

  const sessionActionIcon = (action) => {
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("fill", "none");
    icon.setAttribute("stroke", "currentColor");
    icon.setAttribute("stroke-linecap", "round");
    icon.setAttribute("stroke-linejoin", "round");
    icon.setAttribute("aria-hidden", "true");
    icon.setAttribute("focusable", "false");
    const paths = {
      rename: ["M12 20h9", "M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"],
      stop: ["M6 6h12v12H6Z"],
      archive: ["M3 5h18v4H3Z", "M5 9v10h14V9", "M10 13h4"],
      delete: ["M4 7h16", "M10 11v6", "M14 11v6", "m6 7 1 13h10l1-13", "M9 7V4h6v3"],
    };
    (paths[action] || []).forEach((definition) => {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", definition);
      icon.append(path);
    });
    return icon;
  };

  const ensureSessionActionMenu = () => {
    if (sessionActionMenu instanceof HTMLElement) return sessionActionMenu;
    const menu = document.createElement("div");
    menu.id = "workspace-session-action-menu";
    menu.className = "workspace-session-action-menu";
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", "Session 操作");
    menu.hidden = true;
    [
      ["rename", "重命名"],
      ["stop", "停止"],
      ["archive", "归档"],
      ["delete", "删除"],
    ].forEach(([action, label]) => {
      const actionButton = document.createElement("button");
      const actionLabel = document.createElement("span");
      actionButton.type = "button";
      actionButton.className = "workspace-session-action";
      if (action === "delete") actionButton.classList.add("is-danger");
      actionButton.dataset.sessionAction = action;
      actionButton.setAttribute("role", "menuitem");
      actionLabel.textContent = label;
      actionButton.append(sessionActionIcon(action), actionLabel);
      menu.append(actionButton);
    });
    menu.addEventListener("click", (event) => {
      const actionButton = event.target.closest?.("[data-session-action]");
      if (!(actionButton instanceof HTMLButtonElement) || actionButton.disabled) return;
      const session = sessionsById.get(openSessionActionSessionId);
      if (!session || sessionIsExternallyOccupied(session)) return;
      closeSessionActionMenu();
      if (actionButton.dataset.sessionAction === "rename") {
        openRenameDialog(session);
      } else {
        confirmSessionMutation(session, actionButton.dataset.sessionAction);
      }
    });
    document.body.append(menu);
    sessionActionMenu = menu;
    return menu;
  };

  const closeSessionActionMenu = () => {
    if (!(sessionActionMenu instanceof HTMLElement)) return;
    sessionActionMenu.hidden = true;
    if (openSessionActionTrigger instanceof HTMLButtonElement) {
      openSessionActionTrigger.setAttribute("aria-expanded", "false");
    }
    openSessionActionSessionId = null;
    openSessionActionTrigger = null;
  };

  const toggleSessionActionMenu = (trigger, session, clickPoint = null) => {
    if (sessionIsExternallyOccupied(session)) return;
    const menu = ensureSessionActionMenu();
    const open = menu.hidden || openSessionActionSessionId !== session.id;
    closeSessionActionMenu();
    if (!open) return;
    const state = sessionMoreState(session);
    menu.querySelectorAll("[data-session-action]").forEach((actionButton) => {
      const action = state[actionButton.dataset.sessionAction];
      if (!(actionButton instanceof HTMLButtonElement) || !action) return;
      actionButton.disabled = action.disabled;
      actionButton.title = action.title;
    });
    menu.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    const triggerRect = trigger.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    const anchorX = Number.isFinite(clickPoint?.x) ? clickPoint.x : triggerRect.right;
    const anchorY = Number.isFinite(clickPoint?.y) ? clickPoint.y : triggerRect.bottom;
    const left = Math.min(
      window.innerWidth - menuRect.width - 8,
      Math.max(8, anchorX),
    );
    const top = anchorY + menuRect.height + 8 > window.innerHeight
      ? Math.max(8, anchorY - menuRect.height - 8)
      : anchorY + 8;
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    openSessionActionSessionId = session.id;
    openSessionActionTrigger = trigger;
    menu.querySelector("[data-session-action]:not(:disabled)")?.focus();
  };

  const quickSessionUrl = (sessionId) => (
    `/codex/${encodeURIComponent(sessionId)}/quick-interactions/conversation`
  );

  const opensSessionInNewTab = (event) => (
    event.button === 1 || event.metaKey || event.ctrlKey || event.shiftKey
  );

  const updateSessionButton = (button, session) => {
    const dot = button.querySelector(".workspace-preview-session-dot");
    const title = button.querySelector(".workspace-preview-session-content strong");
    const meta = button.querySelector(".workspace-preview-session-content small");
    const running = session.quick_interaction_running || session.activity === "working";
    const name = sessionTitle(session);
    const externallyOccupied = sessionIsExternallyOccupied(session);
    const externalQuickReadOnly = externallyOccupied && session.session_mode === "quick";

    button.disabled = externallyOccupied && !externalQuickReadOnly;
    button.title = externallyOccupied
      ? externalQuickReadOnly
        ? "其他应用正在使用此 Session；仅可查看历史。"
        : "其他应用正在使用此 Session；释放后会自动恢复。"
      : "";
    button.setAttribute(
      "aria-label",
      externallyOccupied
        ? externalQuickReadOnly
          ? `以只读方式打开 Session：${name}`
          : `Session 正由其他应用使用：${name}`
        : `打开 Session：${name}`,
    );
    const current = session.session_mode === "quick" && session.id === activeQuickSessionId;
    button.classList.toggle("is-current", current);
    const row = button.closest(".workspace-preview-session-row");
    row?.classList.toggle("is-current", current);
    row?.classList.toggle("is-externally-occupied", externallyOccupied);
    dot?.classList.toggle("is-running", running);
    if (title) {
      title.firstElementChild.textContent = name;
      updateSessionMarquee(title);
    }
    if (meta) {
      meta.firstElementChild.textContent = `${sessionState(session)} · ${sessionTimeLabel(session)}`;
      updateSessionMarquee(meta);
    }
  };

  const updateQuickSessionToolbarButton = (button, session) => {
    const label = quickSessionLabel(session);
    const name = sessionTitle(session);
    const state = sessionState(session);
    const externallyOccupied = sessionIsExternallyOccupied(session);
    const current = session.id === activeQuickSessionId;
    const running = sessionIsWorking(session);
    const dot = button.querySelector(".workspace-quick-session-toolbar-dot");

    button.textContent = label;
    button.classList.toggle("is-current", current);
    button.classList.toggle("is-running", running);
    button.classList.toggle("is-externally-occupied", externallyOccupied);
    button.title = `${label} · ${name} · ${state}`;
    button.setAttribute(
      "aria-label",
      current
        ? `当前快速会话：${name}，${state}`
        : externallyOccupied
          ? `以只读方式打开快速会话：${name}，${state}`
          : `打开快速会话：${name}，${state}`,
    );
    if (dot) {
      dot.classList.toggle("is-running", running);
      button.append(dot);
    }
  };

  const createQuickSessionToolbarButton = (session) => {
    const button = document.createElement("button");
    const dot = document.createElement("span");
    button.type = "button";
    button.className = "workspace-quick-session-toolbar-button";
    button.dataset.sessionId = session.id;
    dot.className = "workspace-quick-session-toolbar-dot";
    dot.setAttribute("aria-hidden", "true");
    button.append(dot);
    const openFromEvent = (event) => {
      if (event.defaultPrevented) return;
      const currentSession = sessionsById.get(button.dataset.sessionId);
      if (!currentSession) return;
      event.preventDefault();
      void openSession(currentSession, button, {
        newTab: opensSessionInNewTab(event),
      });
    };
    button.addEventListener("click", (event) => {
      if (event.button === 0) openFromEvent(event);
    });
    button.addEventListener("auxclick", (event) => {
      if (event.button === 1) openFromEvent(event);
    });
    return button;
  };

  const renderQuickSessionToolbar = (orderedSessions) => {
    const quickSessions = orderedSessions.filter(isVisibleQuickSession);
    quickSessionToolbar.hidden = !quickSessions.length;
    if (!quickSessions.length) {
      quickSessionToolbar.replaceChildren();
      return;
    }
    const existingButtons = new Map(
      [...quickSessionToolbar.querySelectorAll(":scope > .workspace-quick-session-toolbar-button")]
        .map((button) => [button.dataset.sessionId, button]),
    );
    existingButtons.forEach((button, sessionId) => {
      if (!quickSessions.some((session) => session.id === sessionId)) button.remove();
    });
    quickSessions.forEach((session, index) => {
      const button = existingButtons.get(session.id) || createQuickSessionToolbarButton(session);
      updateQuickSessionToolbarButton(button, session);
      if (quickSessionToolbar.children[index] !== button) {
        quickSessionToolbar.insertBefore(button, quickSessionToolbar.children[index] || null);
      }
    });
  };

  const updateSessionMarquee = (element) => {
    window.requestAnimationFrame(() => {
      const distance = Math.max(0, element.scrollWidth - element.clientWidth);
      element.classList.toggle("is-overflowing", distance > 1);
      element.style.setProperty("--workspace-session-marquee-distance", `${distance}px`);
      element.style.setProperty("--workspace-session-marquee-duration", `${distance / 40}s`);
    });
  };

  const createSessionButton = (session) => {
    const row = document.createElement("div");
    const button = document.createElement("button");
    const dot = document.createElement("span");
    const content = document.createElement("span");
    const title = document.createElement("strong");
    const meta = document.createElement("small");
    const titleText = document.createElement("span");
    const metaText = document.createElement("span");
    const actions = document.createElement("div");
    const more = document.createElement("button");
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");

    button.type = "button";
    button.className = "workspace-preview-session";
    button.dataset.sessionId = session.id;
    dot.className = "workspace-preview-session-dot";
    dot.setAttribute("aria-hidden", "true");
    content.className = "workspace-preview-session-content";
    title.append(titleText);
    meta.append(metaText);
    content.append(title, meta);
    button.append(dot, content);
    const openFromEvent = (event) => {
      if (event.defaultPrevented) return;
      const currentSession = sessionsById.get(button.dataset.sessionId);
      if (!currentSession) return;
      event.preventDefault();
      void openSession(currentSession, button, {
        newTab: opensSessionInNewTab(event),
      });
    };
    button.addEventListener("click", (event) => {
      if (event.button === 0) openFromEvent(event);
    });
    button.addEventListener("auxclick", (event) => {
      if (event.button === 1) openFromEvent(event);
    });
    row.className = "workspace-preview-session-row";
    row.dataset.sessionId = session.id;
    actions.className = "workspace-preview-session-actions";
    more.type = "button";
    more.className = "workspace-preview-session-more";
    more.dataset.sessionId = session.id;
    more.setAttribute("aria-label", `更多操作：${sessionTitle(session)}`);
    more.setAttribute("aria-haspopup", "menu");
    more.setAttribute("aria-expanded", "false");
    more.title = "更多操作";
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("fill", "currentColor");
    icon.setAttribute("aria-hidden", "true");
    icon.setAttribute("focusable", "false");
    [5, 12, 19].forEach((cx) => {
      const dotIcon = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dotIcon.setAttribute("cx", String(cx));
      dotIcon.setAttribute("cy", "12");
      dotIcon.setAttribute("r", "1.5");
      icon.append(dotIcon);
    });
    more.append(icon);
    ensureSessionActionMenu();
    more.setAttribute("aria-controls", "workspace-session-action-menu");
    more.addEventListener("click", (event) => {
      const currentSession = sessionsById.get(more.dataset.sessionId);
      if (!currentSession) return;
      const clickPoint = event.detail > 0
        ? { x: event.clientX, y: event.clientY }
        : null;
      toggleSessionActionMenu(more, currentSession, clickPoint);
    });
    actions.append(more);
    row.append(button, actions);
    row.addEventListener("pointerenter", (event) => {
      if (event.pointerType !== "mouse") return;
      updateSessionMarquee(title);
      updateSessionMarquee(meta);
    });
    return row;
  };

  const renderSessions = (sessions) => {
    const orderedSessions = [...sessions].sort(
      (left, right) => Date.parse(right.created_at) - Date.parse(left.created_at),
    );
    sessionsById = new Map(orderedSessions.map((session) => [session.id, session]));
    renderQuickSessionToolbar(orderedSessions);

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
      const existingRows = new Map(
        [...items.querySelectorAll(":scope > .workspace-preview-session-row")]
          .map((row) => [row.dataset.sessionId, row]),
      );
      existingRows.forEach((row, sessionId) => {
        if (!groupSessions.some((session) => session.id === sessionId)) row.remove();
      });
      groupSessions.forEach((session, index) => {
        const row = existingRows.get(session.id) || createSessionButton(session);
        const button = row.querySelector(".workspace-preview-session");
        const more = row.querySelector(".workspace-preview-session-more");
        if (!(button instanceof HTMLButtonElement) || !(more instanceof HTMLButtonElement)) return;
        updateSessionButton(button, session);
        more.hidden = sessionIsExternallyOccupied(session);
        if (more.hidden && openSessionActionSessionId === session.id) closeSessionActionMenu();
        more.setAttribute("aria-label", `更多操作：${sessionTitle(session)}`);
        if (items.children[index] !== row) {
          items.insertBefore(row, items.children[index] || null);
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

    const selectedWorkspaceId = usableWorkspaces.some(
      (workspace) => workspace.id === workspaceSelect.value,
    ) ? workspaceSelect.value : usableWorkspaces[0]?.id || "";
    workspaceSelect.value = selectedWorkspaceId;
    workspacePicker.setOptions(
      usableWorkspaces.map((workspace) => ({ value: workspace.id, label: workspace.name })),
      selectedWorkspaceId,
    );
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
    if (sessions.some((session) => (
      session.quick_interaction_running
      || session.activity === "working"
      || sessionNeedsRefresh(session)
    ))) {
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
    const requestGeneration = ++sessionRequestGeneration;
    try {
      const data = await request("/api/codex/sessions");
      if (requestGeneration !== sessionRequestGeneration) return;
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
      if (requestGeneration !== sessionRequestGeneration) return;
      if (!hasSessionSnapshot) {
        createButton.disabled = true;
        createButton.title = error.message;
        setSidebarMessage(error.message || "会话读取失败，请稍后重试。");
        return;
      }
      setSidebarMessage("会话状态暂时无法更新，正在显示最近一次列表。");
    }
  };

  const openSession = async (session, button, { newTab = false } = {}) => {
    closeSessionActionMenu();
    setSidebarMessage("");
    if (session.session_mode === "quick" && newTab) {
      window.open(quickSessionUrl(session.id), "_blank", "noopener");
      return;
    }

    // The terminal URL contains a short-lived server-issued ticket. Open the
    // blank tab inside the user gesture, then replace it after the ticket API
    // responds so browsers do not block the requested new tab.
    const terminalTab = newTab ? window.open("", "_blank") : null;
    if (newTab && !terminalTab) {
      setSidebarMessage("浏览器阻止了新标签页，请允许此站点打开弹窗后重试。");
      return;
    }
    if (terminalTab) terminalTab.opener = null;
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
        window.location.assign(quickSessionUrl(session.id));
        return;
      }
      const data = await request(`/api/codex/sessions/${encodeURIComponent(session.id)}/access`, { method: "POST" });
      if (terminalTab) {
        terminalTab.location.replace(data.terminal_url);
        button.disabled = false;
        return;
      }
      window.location.assign(data.terminal_url);
    } catch (error) {
      terminalTab?.close();
      setSidebarMessage(error.message || "打开 Session 失败，请稍后重试。");
      button.disabled = false;
      loadSessions();
    }
  };

  const closeRenameDialog = () => {
    if (!renaming && renameDialog.open) renameDialog.close();
  };

  const openRenameDialog = (session) => {
    if (sessionIsExternallyOccupied(session)) return;
    renameSessionId = session.id;
    renameInput.value = sessionTitle(session);
    setMessage(renameMessage, "");
    renameDialog.showModal();
    renameInput.focus();
    renameInput.select();
  };

  const requestSessionMutation = async (session, action) => {
    const sessionId = encodeURIComponent(session.id);
    if (action === "stop") {
      await request(`/api/codex/sessions/${sessionId}/stop`, { method: "POST" });
    } else if (action === "archive") {
      await request(`/api/codex/sessions/${sessionId}/archive`, { method: "POST" });
    } else if (action === "delete") {
      await request(`/api/codex/sessions/${sessionId}`, { method: "DELETE" });
    }
    await loadSessions();
  };

  const confirmSessionMutation = (session, action) => {
    if (
      !session
      || sessionIsExternallyOccupied(session)
      || typeof showConfirmationDialog !== "function"
    ) return;
    const name = sessionTitle(session);
    const descriptions = {
      stop: `停止“${name}”将终止当前执行中的任务或实时终端。停止后可以再次进入 Session，但在途任务不会恢复。`,
      archive: `归档“${name}”后，该 Session 将从活动列表移除。如已分配微信槽位，槽位也会释放。Chub 页面暂不提供恢复入口。`,
      delete: `删除“${name}”会永久移除该 Session 及其 Chub 记录，无法恢复。`,
    };
    const labels = {
      stop: ["停止 Session", "确认停止", "secondary"],
      archive: ["归档 Session", "确认归档", "danger"],
      delete: ["删除 Session", "确认删除", "danger"],
    };
    const [title, confirmLabel, tone] = labels[action];
    void showConfirmationDialog({
      title,
      description: descriptions[action],
      confirmLabel,
      tone,
      closeOnConfirm: true,
      onConfirm: async () => {
        if (pendingSessionMutations.has(session.id)) return;
        pendingSessionMutations.add(session.id);
        setSidebarMessage(`${title}中…`);
        try {
          await requestSessionMutation(session, action);
        } catch (error) {
          setSidebarMessage(error.message || `${title}失败。`);
        } finally {
          pendingSessionMutations.delete(session.id);
        }
      },
    });
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

  window.refreshWorkspaceSessions = () => {
    void loadSessions();
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
    quickSessionToolbar.querySelectorAll(".workspace-quick-session-toolbar-button").forEach((button) => {
      if (button.dataset.sessionId === sessionId) {
        updateQuickSessionToolbarButton(button, updatedSession);
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

  const eventIsInsideSessionActionMenu = (event) => (
    event.target instanceof Element
    && Boolean(event.target.closest(".workspace-session-action-menu"))
  );

  document.addEventListener("pointerdown", (event) => {
    if (!eventIsInsideSessionActionMenu(event)) closeSessionActionMenu();
  });
  document.addEventListener("click", (event) => {
    if (event.detail === 0 && !eventIsInsideSessionActionMenu(event)) {
      closeSessionActionMenu();
    }
  });
  window.addEventListener("chub.workspace.session-action-menu-dismiss", closeSessionActionMenu);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSessionActionMenu();
  });
  renameCloseButton?.addEventListener("click", closeRenameDialog);
  renameCancelButton?.addEventListener("click", closeRenameDialog);
  renameDialog.addEventListener("cancel", (event) => {
    if (renaming) event.preventDefault();
  });
  renameDialog.addEventListener("click", (event) => {
    if (event.target === renameDialog) closeRenameDialog();
  });
  renameForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = renameInput.value.trim();
    if (!renameSessionId || !title) return;
    if (sessionIsExternallyOccupied(sessionsById.get(renameSessionId))) {
      setMessage(renameMessage, "该 Session 正由其他应用占用，暂不能重命名。", "error");
      return;
    }
    renaming = true;
    renameInput.disabled = true;
    renameConfirmButton.disabled = true;
    renameConfirmButton.textContent = "保存中…";
    if (renameCloseButton instanceof HTMLButtonElement) renameCloseButton.disabled = true;
    if (renameCancelButton instanceof HTMLButtonElement) renameCancelButton.disabled = true;
    setMessage(renameMessage, "");
    try {
      await request(`/api/codex/sessions/${encodeURIComponent(renameSessionId)}/title`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      renameDialog.close();
      await loadSessions();
    } catch (error) {
      setMessage(renameMessage, error.message || "Session 重命名失败，请稍后重试。", "error");
    } finally {
      renaming = false;
      renameSessionId = null;
      renameInput.disabled = false;
      renameConfirmButton.disabled = false;
      renameConfirmButton.textContent = "保存";
      if (renameCloseButton instanceof HTMLButtonElement) renameCloseButton.disabled = false;
      if (renameCancelButton instanceof HTMLButtonElement) renameCancelButton.disabled = false;
    }
  });

  const closeDialog = () => {
    if (!creating && dialog.open) dialog.close();
  };

  createButton.addEventListener("click", () => {
    setMessage(createMessage, "");
    dialog.showModal();
    window.requestAnimationFrame(() => {
      if (dialog.open && !confirmButton.disabled) confirmButton.focus();
    });
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
    workspacePicker.setDisabled(true);
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
      workspacePicker.setDisabled(false);
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
