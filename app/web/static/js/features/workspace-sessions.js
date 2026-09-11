"use strict";

(() => {
  const createButton = document.getElementById("workspace-session-create");
  const sessionSection = document.getElementById("workspace-preview-sessions");
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
    || !(sessionSection instanceof HTMLElement)
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

  let creation = { quick: { available: false } };
  let workspaces = [];
  let refreshTimer = null;
  let sessionRequestGeneration = 0;
  let creating = false;
  let hasSessionSnapshot = false;
  let sessionsById = new Map();
  let runtimeSessionGroups = [];
  let nativeSessions = [];
  const pendingSessionMutations = new Set();
  const pendingNativeSessionMutations = new Set();
  let activeQuickSessionId = new URL(window.location.href).searchParams.get("session");
  let renameSessionId = null;
  let renaming = false;
  let sessionActionMenu = null;
  let openSessionActionSessionId = null;
  let openNativeSessionActionRef = null;
  let openSessionActionTrigger = null;
  let sidebarMessageClearTimer = null;
  let sidebarMessageVisibleUntil = 0;
  const sessionCacheKey = "chub.workspace.sessions.v1";
  const sidebarMessageMinimumVisibleMs = 6000;

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

  const setSidebarMessage = (text = "", { minimumVisibleMs = sidebarMessageMinimumVisibleMs } = {}) => {
    window.clearTimeout(sidebarMessageClearTimer);
    if (text) {
      sidebarMessageVisibleUntil = Date.now() + minimumVisibleMs;
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
      const error = new Error(payload?.error?.message || `请求失败（HTTP ${response.status}）。`);
      error.code = payload?.error?.code || null;
      throw error;
    }
    return payload.data;
  };

  const isSessionListData = (data) => (
    data
    && typeof data === "object"
    && typeof data.runtime_registered === "boolean"
    && Array.isArray(data.sessions)
    && Array.isArray(data.workspaces)
  );

  const runtimeGroups = (data) => {
    if (Array.isArray(data.runtime_groups)) {
      return data.runtime_groups.filter((group) => (
        group
        && typeof group.runtime_id === "string"
        && typeof group.name === "string"
        && group.name.trim()
      ));
    }
    // Keep a previously cached Session snapshot usable while upgrading the Web API.
    return data.runtime_registered ? [{ runtime_id: "codex", name: "Codex" }] : [];
  };

  const nativeSessionList = (data) => (
    Array.isArray(data.native_sessions)
      ? data.native_sessions.filter((session) => (
        session
        && typeof session.cwd === "string"
        && typeof session.created_at === "string"
        && typeof session.updated_at === "string"
      ))
      : []
  );

  const readCachedSessions = () => {
    try {
      const data = JSON.parse(window.sessionStorage.getItem(sessionCacheKey) || "null");
      return isSessionListData(data) ? { ...data, native_sessions: [] } : null;
    } catch {
      return null;
    }
  };

  const cacheSessions = (data) => {
    try {
      window.sessionStorage.setItem(
        sessionCacheKey,
        JSON.stringify({ ...data, native_sessions: [] }),
      );
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
    if (session.status === "error" || session.error) return "会话异常 · 可重试";
    if (session.status === "new") return "等待输入";
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

  const isVisibleQuickSession = (session) => session.workspace_id !== "weixin-translation";

  const quickSessionLabel = (session) => {
    const slot = session.weixin_session_slot;
    return Number.isInteger(slot) && slot >= 1 && slot <= 9 ? `S${slot}` : "S";
  };

  const sessionIsExternallyOccupied = (session) => session.usage?.owner === "external";

  const nativeSessionIsUnavailable = (session) => (
    session.writer_lock_state === "unknown"
    || session.chub_writer_lock_state === "unknown"
    || (session.writer_lock_state === "held" && session.chub_writer_lock_state !== "held")
  );

  const sessionNeedsRefresh = (session) => {
    const usage = session.usage;
    return usage?.owner === "external"
      || usage?.owner === "unknown"
      || session.activity === "unknown";
  };

  const sessionIsWorking = (session) => {
    const usage = session.usage;
    const usageRunning = usage && (
      usage.owner === "quick_worker" && ["running", "waiting_result"].includes(usage.phase)
    );
    return session.quick_interaction_running || session.activity === "working" || usageRunning;
  };

  const sessionHasActiveExecution = (session) => {
    const usage = session.usage;
    return Boolean(usage && (
      usage.owner === "quick_worker" && ["running", "waiting_result"].includes(usage.phase)
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
      const action = actionButton.dataset.sessionAction;
      const nativeActionRef = openNativeSessionActionRef;
      if (nativeActionRef) {
        closeSessionActionMenu();
        if (action === "archive" || action === "delete") {
          confirmNativeSessionMutation(nativeActionRef, action);
        }
        return;
      }
      const session = sessionsById.get(openSessionActionSessionId);
      if (!session || sessionIsExternallyOccupied(session)) return;
      closeSessionActionMenu();
      if (action === "rename") {
        openRenameDialog(session);
      } else {
        confirmSessionMutation(session, action);
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
    openNativeSessionActionRef = null;
    openSessionActionTrigger = null;
  };

  const toggleSessionActionMenu = (trigger, session, clickPoint = null, { nativeActionRef = null } = {}) => {
    if (!nativeActionRef && sessionIsExternallyOccupied(session)) return;
    const menu = ensureSessionActionMenu();
    const open = menu.hidden || (nativeActionRef
      ? openNativeSessionActionRef !== nativeActionRef
      : openSessionActionSessionId !== session.id);
    closeSessionActionMenu();
    if (!open) return;
    const state = nativeActionRef
      ? {
        archive: { disabled: false, title: "归档 Native Session" },
        delete: { disabled: false, title: "永久删除 Native Session" },
      }
      : sessionMoreState(session);
    menu.querySelectorAll("[data-session-action]").forEach((actionButton) => {
      const action = state[actionButton.dataset.sessionAction];
      if (!(actionButton instanceof HTMLButtonElement)) return;
      actionButton.hidden = nativeActionRef
        && !["archive", "delete"].includes(actionButton.dataset.sessionAction);
      if (!action) return;
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
    openSessionActionSessionId = nativeActionRef ? null : session.id;
    openNativeSessionActionRef = nativeActionRef;
    openSessionActionTrigger = trigger;
    menu.querySelector("[data-session-action]:not(:disabled):not([hidden])")?.focus();
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
    const externalQuickReadOnly = externallyOccupied;

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
    const current = session.id === activeQuickSessionId;
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
        ? `当前Chub Session：${name}，${state}`
        : externallyOccupied
          ? `以只读方式打开Chub Session：${name}，${state}`
          : `打开Chub Session：${name}，${state}`,
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

  const createUnavailableRuntimeCreateButton = (runtimeGroup) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "workspace-preview-create";
    button.dataset.runtimeId = runtimeGroup.runtime_id;
    button.disabled = true;
    button.title = `${runtimeGroup.name} 尚未提供新建 Session 入口。`;
    button.textContent = "+ New Session";
    return button;
  };

  const nativeSessionDetailLines = (session) => {
    const timestamp = session.updated_at || session.created_at;
    return [
      `目录：${session.cwd}`,
      `时间：${new Date(timestamp).toLocaleString("zh-CN")}`,
    ];
  };

  const nativeSessionTitle = (session) => {
    const title = typeof session.title === "string" ? session.title.trim() : "";
    return title || "标题暂未读取到";
  };

  const renderNativeSessions = (items, sessions) => {
    let group = items.querySelector(':scope > .workspace-preview-session-group[data-session-group="native-sessions"]');
    const unboundSessions = sessions.filter((session) => !session.chub_session_id);
    if (!unboundSessions.length) {
      group?.remove();
      return;
    }
    if (!group) {
      group = document.createElement("section");
      const heading = document.createElement("p");
      const list = document.createElement("div");
      group.className = "workspace-preview-session-group workspace-preview-native-session-group";
      group.dataset.sessionGroup = "native-sessions";
      heading.className = "workspace-preview-session-group-title";
      heading.textContent = "Native";
      list.className = "workspace-preview-session-group-list";
      group.append(heading, list);
    }
    const list = group.querySelector(".workspace-preview-session-group-list");
    if (!(list instanceof HTMLElement)) return;
    const ordered = [...unboundSessions].sort(
      (left, right) => Date.parse(right.updated_at) - Date.parse(left.updated_at),
    );
    list.replaceChildren(...ordered.map((session) => {
      const unavailable = nativeSessionIsUnavailable(session);
      const row = document.createElement("article");
      const title = document.createElement("strong");
      const details = document.createElement("div");
      row.className = "workspace-preview-native-session";
      row.classList.toggle("is-unavailable", unavailable);
      title.textContent = nativeSessionTitle(session);
      details.className = "workspace-preview-native-session-details";
      nativeSessionDetailLines(session).forEach((line) => {
        const detail = document.createElement("small");
        const detailText = document.createElement("span");
        detailText.textContent = line;
        detail.append(detailText);
        details.append(detail);
      });
      row.append(title, details);
      row.addEventListener("pointerenter", (event) => {
        if (event.pointerType !== "mouse") return;
        row.querySelectorAll(".workspace-preview-native-session small").forEach(updateSessionMarquee);
      });
      if (session.native_action_ref) {
        const actions = document.createElement("div");
        const more = document.createElement("button");
        const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        row.classList.add("has-actions");
        actions.className = "workspace-preview-native-session-actions";
        more.type = "button";
        more.className = "workspace-preview-session-more";
        more.setAttribute("aria-label", `更多操作：${title.textContent}`);
        more.setAttribute("aria-haspopup", "menu");
        more.setAttribute("aria-expanded", "false");
        more.setAttribute("aria-controls", "workspace-session-action-menu");
        more.title = "更多操作";
        icon.setAttribute("viewBox", "0 0 24 24");
        icon.setAttribute("fill", "currentColor");
        icon.setAttribute("aria-hidden", "true");
        [5, 12, 19].forEach((cx) => {
          const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
          dot.setAttribute("cx", String(cx));
          dot.setAttribute("cy", "12");
          dot.setAttribute("r", "1.5");
          icon.append(dot);
        });
        more.append(icon);
        ensureSessionActionMenu();
        more.addEventListener("click", (event) => {
          const clickPoint = event.detail > 0 ? { x: event.clientX, y: event.clientY } : null;
          toggleSessionActionMenu(more, null, clickPoint, {
            nativeActionRef: session.native_action_ref,
          });
        });
        actions.append(more);
        row.append(actions);
      }
      return row;
    }));
    items.append(group);
  };

  const renderSessions = (
    sessions,
    visibleRuntimeGroups = runtimeSessionGroups,
    visibleNativeSessions = nativeSessions,
  ) => {
    const orderedSessions = [...sessions].sort(
      (left, right) => Date.parse(right.created_at) - Date.parse(left.created_at),
    );
    sessionsById = new Map(orderedSessions.map((session) => [session.id, session]));
    renderQuickSessionToolbar(orderedSessions);

    const activeRuntimeIds = new Set(visibleRuntimeGroups.map((group) => group.runtime_id));
    sessionList.querySelectorAll(":scope > .workspace-preview-runtime-session-group").forEach((group) => {
      if (!activeRuntimeIds.has(group.dataset.runtimeId)) group.remove();
    });

    visibleRuntimeGroups.forEach((runtimeGroup, runtimeIndex) => {
      const runtimeId = runtimeGroup.runtime_id;
      const runtimeSessions = orderedSessions.filter((session) => session.runtime_id === runtimeId);
      let runtimeContainer = sessionList.querySelector(
        `:scope > .workspace-preview-runtime-session-group[data-runtime-id="${runtimeId}"]`,
      );
      if (!runtimeContainer) {
        runtimeContainer = document.createElement("section");
        const heading = document.createElement("p");
        const items = document.createElement("div");
        runtimeContainer.className = "workspace-preview-runtime-session-group";
        runtimeContainer.dataset.runtimeId = runtimeId;
        heading.className = "workspace-preview-runtime-session-title";
        items.className = "workspace-preview-runtime-session-list";
        runtimeContainer.append(heading, items);
      }
      const heading = runtimeContainer.querySelector(".workspace-preview-runtime-session-title");
      const items = runtimeContainer.querySelector(".workspace-preview-runtime-session-list");
      if (!(heading instanceof HTMLElement) || !(items instanceof HTMLElement)) return;
      heading.textContent = `${runtimeGroup.name} Sessions`;

      if (runtimeId === "codex") {
        if (createButton.parentElement !== runtimeContainer) {
          runtimeContainer.insertBefore(createButton, items);
        }
      } else if (!runtimeContainer.querySelector(":scope > .workspace-preview-create")) {
        runtimeContainer.insertBefore(createUnavailableRuntimeCreateButton(runtimeGroup), items);
      }

      const runtimeNativeSessions = runtimeId === "codex" ? visibleNativeSessions : [];
      if (!runtimeSessions.length && !runtimeNativeSessions.length) {
        items.querySelectorAll(":scope > .workspace-preview-session-group").forEach((group) => group.remove());
        let empty = items.querySelector(":scope > .empty-state");
        if (!empty) {
          empty = document.createElement("p");
          empty.className = "empty-state";
          empty.textContent = "暂无 Session。";
          items.append(empty);
        }
      } else {
        items.querySelector(":scope > .empty-state")?.remove();
        [
          { title: "Chub" },
        ].forEach(({ title }) => {
          const groupSessions = runtimeSessions;
          const groupId = title.toLowerCase().replaceAll(" ", "-");
          let group = items.querySelector(`:scope > .workspace-preview-session-group[data-session-group="${groupId}"]`);
          if (!groupSessions.length) {
            group?.remove();
            return;
          }
          if (!group) {
            group = document.createElement("section");
            const groupHeading = document.createElement("p");
            const groupItems = document.createElement("div");
            group.className = "workspace-preview-session-group";
            group.dataset.sessionGroup = groupId;
            groupHeading.className = "workspace-preview-session-group-title";
            groupHeading.textContent = title;
            groupItems.className = "workspace-preview-session-group-list";
            group.append(groupHeading, groupItems);
          }
          const groupItems = group.querySelector(".workspace-preview-session-group-list");
          if (!(groupItems instanceof HTMLElement)) return;
          const existingRows = new Map(
            [...groupItems.querySelectorAll(":scope > .workspace-preview-session-row")]
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
            if (groupItems.children[index] !== row) {
              groupItems.insertBefore(row, groupItems.children[index] || null);
            }
          });
          items.append(group);
        });
        renderNativeSessions(items, runtimeNativeSessions);
      }
      if (sessionList.children[runtimeIndex] !== runtimeContainer) {
        sessionList.insertBefore(runtimeContainer, sessionList.children[runtimeIndex] || null);
      }
    });
    if (!visibleRuntimeGroups.some((group) => group.runtime_id === "codex")) {
      createButton.remove();
    }
  };

  const syncCreation = ({ resetSelection = false } = {}) => {
    const usableWorkspaces = workspaces.filter((workspace) => workspace.available);
    const available = creation.quick.available;
    createButton.disabled = !available || !usableWorkspaces.length;
    createButton.title = createButton.disabled
      ? creation.quick.reason || "当前没有可用工作目录"
      : "";

    const preferredWorkspaceId = usableWorkspaces.find(
      (workspace) => workspace.id === "chub",
    )?.id || usableWorkspaces[0]?.id || "";
    const selectedWorkspaceId = !resetSelection && usableWorkspaces.some(
      (workspace) => workspace.id === workspaceSelect.value,
    ) ? workspaceSelect.value : preferredWorkspaceId;
    workspaceSelect.value = selectedWorkspaceId;
    workspacePicker.setOptions(
      usableWorkspaces.map((workspace) => ({ value: workspace.id, label: workspace.name })),
      selectedWorkspaceId,
    );
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
      item.id === activeQuickSessionId
    ));
    if (!session) {
      activeQuickSessionId = null;
      clearSelectedQuickSessionLocation();
      if (window.workspaceQuickSessionOpen) {
        window.location.replace("/");
        return true;
      }
      renderSessions(sessions);
      setSidebarMessage("此前选择的Chub Session已不可用，已回到工作台。");
      return true;
    }
    if (window.workspaceQuickSessionOpen) return false;
    window.openWorkspaceQuickSession?.(session);
    return false;
  };

  const applySessionData = (data, { restoreSelectedSession = false } = {}) => {
    const groups = runtimeGroups(data);
    runtimeSessionGroups = groups;
    nativeSessions = nativeSessionList(data);
    sessionSection.hidden = groups.length === 0;
    creation = {
      quick: data.quick_creation || { available: false },
    };
    workspaces = data.workspaces;
    syncCreation();
    renderSessions(data.sessions, groups, nativeSessions);
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
      nativeSessions = [];
      renderSessions([...sessionsById.values()], runtimeSessionGroups, nativeSessions);
      setSidebarMessage("会话状态暂时无法更新；Native Sessions 暂不展示。");
    }
  };

  const openSession = async (session, button, { newTab = false } = {}) => {
    closeSessionActionMenu();
    setSidebarMessage("");
    if (newTab) {
      window.open(quickSessionUrl(session.id), "_blank", "noopener");
      return;
    }

    button.disabled = true;
    try {
      activeQuickSessionId = session.id;
      setSelectedQuickSessionLocation(session.id);
      renderSessions([...sessionsById.values()]);
      if (typeof window.openWorkspaceQuickSession === "function") {
        window.openWorkspaceQuickSession(session);
        button.disabled = false;
        return;
      }
      window.location.assign(quickSessionUrl(session.id));
    } catch (error) {
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
    } else if (action === "chub-only-delete") {
      await request(`/api/codex/sessions/${sessionId}/management`, { method: "DELETE" });
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
      stop: `停止“${name}”将终止当前执行中的任务。停止后可以再次进入 Session，但在途任务不会恢复。`,
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
        setSidebarMessage(`${title}中…`, { minimumVisibleMs: 0 });
        try {
          await requestSessionMutation(session, action);
        } catch (error) {
          if (action === "delete" && error?.code !== "quick_interaction_cancel_failed") {
            confirmChubOnlyDelete(session, error.message || "Native Session 无法删除。");
          } else {
            setSidebarMessage(error.message || `${title}失败。`);
          }
        } finally {
          pendingSessionMutations.delete(session.id);
        }
      },
    });
  };

  const confirmChubOnlyDelete = (session, nativeError) => {
    if (!session || typeof showConfirmationDialog !== "function") return;
    void showConfirmationDialog({
      title: "仅删除 Chub 记录",
      description: `Native Session 未能删除：${nativeError}。继续后将移除 Chub 保存的 Session、任务记录和关联槽位，但保留 Native Session。此操作无法恢复。`,
      confirmLabel: "确认仅删除 Chub 记录",
      tone: "danger",
      closeOnConfirm: true,
      onConfirm: async () => {
        if (pendingSessionMutations.has(session.id)) return;
        pendingSessionMutations.add(session.id);
        setSidebarMessage("删除 Chub 记录中…", { minimumVisibleMs: 0 });
        try {
          await requestSessionMutation(session, "chub-only-delete");
        } catch (error) {
          setSidebarMessage(error.message || "删除 Chub 记录失败。");
        } finally {
          pendingSessionMutations.delete(session.id);
        }
      },
    });
  };

  const requestNativeSessionMutation = async (nativeActionRef, action) => {
    const reference = encodeURIComponent(nativeActionRef);
    if (action === "archive") {
      await request(`/api/codex/native-sessions/${reference}/archive`, { method: "POST" });
    } else if (action === "delete") {
      await request(`/api/codex/native-sessions/${reference}`, { method: "DELETE" });
    }
  };

  const confirmNativeSessionMutation = (nativeActionRef, action) => {
    if (!nativeActionRef || typeof showConfirmationDialog !== "function") return;
    const labels = {
      archive: ["归档 Native Session", "确认归档"],
      delete: ["删除 Native Session", "确认删除"],
    };
    const descriptions = {
      archive: "归档将只移除 Runtime 原生会话，不会改动 Chub Session、任务或槽位。Chub 页面暂不提供恢复入口。",
      delete: "删除将永久移除 Runtime 原生会话，不会改动 Chub Session、任务或槽位，且无法恢复。",
    };
    const [title, confirmLabel] = labels[action];
    void showConfirmationDialog({
      title,
      description: descriptions[action],
      confirmLabel,
      tone: "danger",
      closeOnConfirm: true,
      onConfirm: async () => {
        if (pendingNativeSessionMutations.has(nativeActionRef)) return;
        pendingNativeSessionMutations.add(nativeActionRef);
        setSidebarMessage(`${title}中…`, { minimumVisibleMs: 0 });
        try {
          await requestNativeSessionMutation(nativeActionRef, action);
        } catch (error) {
          setSidebarMessage(error.message || `${title}失败。`);
        } finally {
          pendingNativeSessionMutations.delete(nativeActionRef);
          await loadSessions();
        }
      },
    });
  };

  window.selectWorkspaceQuickSession = (sessionId) => {
    if (typeof sessionId !== "string" || !sessionId) return false;
    const session = sessionsById.get(sessionId);
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
    syncCreation({ resetSelection: true });
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
    if (!workspaceSelect.value) return;

    creating = true;
    confirmButton.disabled = true;
    confirmButton.textContent = "创建中…";
    workspacePicker.setDisabled(true);
    if (closeButton instanceof HTMLButtonElement) closeButton.disabled = true;
    if (cancelButton instanceof HTMLButtonElement) cancelButton.disabled = true;
    setMessage(createMessage, "正在创建 Session…");
    try {
      await request("/api/codex/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace_id: workspaceSelect.value }),
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
