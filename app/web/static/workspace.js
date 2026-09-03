(() => {
  const shell = document.querySelector(".workspace-preview-shell");
  const toggle = document.getElementById("workspace-sidebar-toggle");
  const resizer = document.getElementById("workspace-sidebar-resizer");
  const sidebar = document.getElementById("workspace-sidebar");
  const workspaceMain = document.querySelector(".workspace-preview-main");
  const sidebarScrim = document.getElementById("workspace-sidebar-scrim");
  const sidebarClose = document.getElementById("workspace-sidebar-close");
  const sectionNavigations = document.querySelectorAll(
    "[data-workspace-section-navigation]",
  );
  if (
    !(shell instanceof HTMLElement)
    || !(toggle instanceof HTMLButtonElement)
    || !(resizer instanceof HTMLElement)
    || !(sidebar instanceof HTMLElement)
    || !(sidebarScrim instanceof HTMLButtonElement)
    || !(sidebarClose instanceof HTMLButtonElement)
  ) {
    return;
  }

  const collapsedStorageKey = "chub.workspace.sidebarCollapsed";
  const widthStorageKey = "chub.sidebarWidth";
  const minimumSidebarWidth = 225;
  const maximumSidebarWidth = 360;
  const defaultSidebarWidth = 225;
  const sidebarWidthStep = 16;
  const compactViewport = window.matchMedia("(max-width: 760px)");
  const mobileSidebarHistoryStateKey = "chubWorkspaceMobileSidebar";
  const sectionToolbarLoadingStatus = {
    workbench: "正在读取工作台状态…",
    automations: "正在读取自动化状态…",
    "project-docs": "正在读取项目资料…",
  };
  const sectionToolbarLoadedStatus = {
    automations: "自动化已加载",
    "project-docs": "项目资料已加载",
  };
  const toolbarLoadingMinimumMs = 220;
  let toolbarTransitionId = 0;
  const initialQuickSessionPanel = document.querySelector(
    "#workspace-section-content.workspace-inline-quick-session",
  );
  const quickSessionSelectionMessageType = "chub.workspace.quick-session-selection";
  const quickSessionActivityMessageType = "chub.workspace.quick-session-activity";
  window.workspaceQuickSessionOpen = initialQuickSessionPanel instanceof HTMLElement;

  const preserveWorkspaceReturnTarget = (event) => {
    const target = event.target;
    const link = target instanceof Element ? target.closest("a[href]") : null;
    if (!(link instanceof HTMLAnchorElement)) return;
    const targetUrl = new URL(link.href);
    if (targetUrl.origin !== window.location.origin || targetUrl.pathname !== "/settings") {
      return;
    }
    const currentUrl = new URL(window.location.href);
    if (!new Set(["/", "/workspace"]).has(currentUrl.pathname)) return;
    targetUrl.searchParams.set(
      "return_to",
      `${currentUrl.pathname}${currentUrl.search}`,
    );
    link.href = targetUrl.href;
  };
  document.addEventListener("click", preserveWorkspaceReturnTarget);

  const setToolbarStatus = (text) => {
    const health = document.getElementById("workspace-preview-health");
    if (health instanceof HTMLElement) health.lastChild.textContent = text;
  };

  const showSectionToolbarLoading = (section) => {
    toolbarTransitionId += 1;
    setToolbarStatus(sectionToolbarLoadingStatus[section] || sectionToolbarLoadingStatus.workbench);
  };

  const finishSectionToolbarLoading = (section) => {
    if (section === "workbench") return;
    const transitionId = toolbarTransitionId;
    window.setTimeout(() => {
      if (transitionId === toolbarTransitionId) {
        setToolbarStatus(sectionToolbarLoadedStatus[section] || "工作台已加载");
      }
    }, toolbarLoadingMinimumMs);
  };

  const clearWorkspaceSectionSelection = () => {
    sectionNavigations.forEach((navigation) => {
      navigation.querySelectorAll("a").forEach((item) => {
        item.classList.remove("is-current");
        item.removeAttribute("aria-current");
      });
    });
  };

  // Workspace sections share the sidebar and its live Session list. Replace
  // only the right-side section, while keeping one browser history level.
  const replaceWorkspaceSection = async (event) => {
    if (
      event.defaultPrevented
      || event.button !== 0
      || event.metaKey
      || event.ctrlKey
      || event.shiftKey
      || event.altKey
    ) {
      return;
    }
    const target = event.target;
    const link = target instanceof Element
      ? target.closest("a")
      : null;
    if (!(link instanceof HTMLAnchorElement)) {
      return;
    }
    const targetUrl = new URL(link.href);
    if (!new Set(["/", "/workspace"]).has(targetUrl.pathname)) {
      link.blur();
      return;
    }
    event.preventDefault();
    if (link.getAttribute("aria-current") === "page" && !window.workspaceQuickSessionOpen) {
      return;
    }
    if (window.workspaceQuickSessionOpen) {
      window.clearWorkspaceQuickSessionSelection?.();
    }
    const currentContent = document.getElementById("workspace-section-content");
    if (!(currentContent instanceof HTMLElement)) {
      window.location.replace(link.href);
      return;
    }
    const targetSection = targetUrl.searchParams.get("section") || "workbench";
    showSectionToolbarLoading(targetSection);
    link.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(targetUrl, { headers: { Accept: "text/html" } });
      if (!response.ok) throw new Error("无法加载工作台分区。");
      const documentFragment = new DOMParser().parseFromString(await response.text(), "text/html");
      const nextContent = documentFragment.getElementById("workspace-section-content");
      if (!(nextContent instanceof HTMLElement)) throw new Error("工作台分区响应无效。");
      currentContent.replaceWith(nextContent);
      window.workspaceQuickSessionOpen = false;
      workspaceMain?.classList.remove("is-showing-quick-session");
      history.replaceState(history.state, "", link.href);
      sectionNavigations.forEach((navigation) => {
        navigation.querySelectorAll("a").forEach((item) => {
          const selected = item.href === link.href;
          item.classList.toggle("is-current", selected);
          if (selected) item.setAttribute("aria-current", "page");
          else item.removeAttribute("aria-current");
        });
      });
      window.initializeWorkspaceAutomationControls?.();
      window.initializeWorkspaceWorkstation?.();
      finishSectionToolbarLoading(targetSection);
      if (compactViewport.matches) closeMobileSidebar();
    } catch {
      window.location.replace(link.href);
    } finally {
      link.removeAttribute("aria-busy");
    }
  };
  sectionNavigations.forEach((navigation) => {
    navigation.addEventListener("click", replaceWorkspaceSection);
  });
  finishSectionToolbarLoading(
    new URL(window.location.href).searchParams.get("section") || "workbench",
  );
  if (initialQuickSessionPanel instanceof HTMLElement) {
    clearWorkspaceSectionSelection();
    setToolbarStatus("正在加载快速会话…");
    initialQuickSessionPanel.querySelector("iframe")?.addEventListener("load", () => {
      if (window.workspaceQuickSessionOpen) setToolbarStatus("快速会话已加载");
    }, { once: true });
  }

  window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;
    const selection = event.data;
    if (!selection || typeof selection.sessionId !== "string") {
      return;
    }
    const frame = document.querySelector(
      "#workspace-section-content.workspace-inline-quick-session iframe",
    );
    if (!(frame instanceof HTMLIFrameElement) || event.source !== frame.contentWindow) return;
    if (selection.type === quickSessionSelectionMessageType) {
      window.selectWorkspaceQuickSession?.(selection.sessionId);
    } else if (selection.type === quickSessionActivityMessageType) {
      window.updateWorkspaceQuickSessionActivity?.(
        selection.sessionId,
        selection.running === true,
        selection.updatedAt,
      );
    }
  });

  window.openWorkspaceQuickSession = (session) => {
    if (!session || session.session_mode !== "quick" || typeof session.id !== "string") return;
    const currentContent = document.getElementById("workspace-section-content");
    if (!(currentContent instanceof HTMLElement)) {
      window.location.assign(`/codex/${encodeURIComponent(session.id)}/quick-interactions/conversation`);
      return;
    }
    const panel = document.createElement("section");
    const frame = document.createElement("iframe");
    panel.id = "workspace-section-content";
    panel.className = "workspace-inline-quick-session";
    panel.setAttribute("aria-label", "快速会话");
    frame.className = "workspace-inline-quick-session-frame";
    frame.title = session.title || session.workspace_name || "快速会话";
    frame.src = `/codex/${encodeURIComponent(session.id)}/quick-interactions/conversation?embedded=workspace`;
    frame.addEventListener("load", () => {
      if (window.workspaceQuickSessionOpen && document.getElementById("workspace-section-content") === panel) {
        setToolbarStatus("快速会话已加载");
      }
    }, { once: true });
    panel.append(frame);
    currentContent.replaceWith(panel);
    window.workspaceQuickSessionOpen = true;
    workspaceMain?.classList.add("is-showing-quick-session");
    clearWorkspaceSectionSelection();
    showSectionToolbarLoading("quick-session");
    setToolbarStatus("正在加载快速会话…");
    if (compactViewport.matches) closeMobileSidebar();
  };


  window.initializeWorkspaceAutomationControls = () => {
  const automationStartButton = document.getElementById("workspace-automation-browser-start");
  const automationStartDialog = document.getElementById("workspace-automation-browser-start-dialog");
  const automationStartForm = document.getElementById("workspace-automation-browser-start-form");
  const automationStartClose = document.getElementById("workspace-automation-browser-start-close");
  const automationStartCancel = document.getElementById("workspace-automation-browser-start-cancel");
  const automationStartProfile = document.getElementById("workspace-automation-browser-profile");
  const automationStartMessage = document.getElementById("workspace-automation-browser-start-message");
  const automationStopButton = document.getElementById("workspace-automation-browser-stop");
  const automationStopDialog = document.getElementById("workspace-automation-browser-stop-dialog");
  const automationStopForm = document.getElementById("workspace-automation-browser-stop-form");
  const automationStopClose = document.getElementById("workspace-automation-browser-stop-close");
  const automationStopCancel = document.getElementById("workspace-automation-browser-stop-cancel");
  const automationStopMessage = document.getElementById("workspace-automation-browser-stop-message");

  const automationRequest = async (path, payload) => {
    const response = await fetch(path, {
      method: "POST",
      headers: payload ? { "Content-Type": "application/json" } : undefined,
      body: payload ? JSON.stringify(payload) : undefined,
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || result?.success !== true) {
      throw new Error(result?.error?.message || "自动化环境操作失败。");
    }
    return result.data;
  };

  const closeDialog = (dialog) => {
    if (dialog instanceof HTMLDialogElement && dialog.open) {
      dialog.close();
    }
  };

  if (
    automationStartButton instanceof HTMLButtonElement
    && automationStartDialog instanceof HTMLDialogElement
    && automationStartForm instanceof HTMLFormElement
    && automationStartProfile instanceof HTMLSelectElement
    && automationStartMessage instanceof HTMLElement
  ) {
    automationStartButton.addEventListener("click", () => automationStartDialog.showModal());
    [automationStartClose, automationStartCancel].forEach((button) => {
      button?.addEventListener("click", () => closeDialog(automationStartDialog));
    });
    automationStartForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const selected = automationStartProfile.selectedOptions[0];
      const mode = document.querySelector('input[name="workspace-automation-browser-mode"]:checked');
      if (!(selected instanceof HTMLOptionElement) || !(mode instanceof HTMLInputElement)) {
        return;
      }
      const initializing = selected.dataset.initialized !== "true";
      const confirm = automationStartForm.querySelector('button[type="submit"]');
      if (confirm instanceof HTMLButtonElement) {
        confirm.disabled = true;
        confirm.textContent = initializing ? "初始化中…" : "启动中…";
      }
      automationStartMessage.className = "message";
      automationStartMessage.textContent = "";
      try {
        await automationRequest(
          initializing ? "/api/automations/browser/initialize" : "/api/automations/browser/start",
          { profile_id: selected.value, mode: mode.value },
        );
        automationStartMessage.className = "message message-success";
        automationStartMessage.textContent = initializing
          ? "浏览器账户初始化已受理，正在刷新状态。"
          : "Debug Chrome 已启动，正在刷新状态。";
        window.setTimeout(() => window.location.reload(), 500);
      } catch (error) {
        automationStartMessage.className = "message message-error";
        automationStartMessage.textContent = error instanceof Error ? error.message : "自动化环境操作失败。";
        if (confirm instanceof HTMLButtonElement) {
          confirm.disabled = false;
          confirm.textContent = "启动";
        }
      }
    });
  }

  if (
    automationStopButton instanceof HTMLButtonElement
    && automationStopDialog instanceof HTMLDialogElement
    && automationStopForm instanceof HTMLFormElement
    && automationStopMessage instanceof HTMLElement
  ) {
    automationStopButton.addEventListener("click", () => automationStopDialog.showModal());
    [automationStopClose, automationStopCancel].forEach((button) => {
      button?.addEventListener("click", () => closeDialog(automationStopDialog));
    });
    automationStopForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const confirm = automationStopForm.querySelector('button[type="submit"]');
      if (confirm instanceof HTMLButtonElement) {
        confirm.disabled = true;
        confirm.textContent = "停止中…";
      }
      automationStopMessage.className = "message";
      automationStopMessage.textContent = "";
      try {
        await automationRequest("/api/automations/browser/stop");
        automationStopMessage.className = "message message-success";
        automationStopMessage.textContent = "Debug Chrome 已停止，正在刷新状态。";
        window.setTimeout(() => window.location.reload(), 500);
      } catch (error) {
        automationStopMessage.className = "message message-error";
        automationStopMessage.textContent = error instanceof Error ? error.message : "自动化环境操作失败。";
        if (confirm instanceof HTMLButtonElement) {
          confirm.disabled = false;
          confirm.textContent = "确认停止";
        }
      }
    });
  }

  };
  window.initializeWorkspaceAutomationControls();

  const clampSidebarWidth = (value) => Math.min(
    maximumSidebarWidth,
    Math.max(minimumSidebarWidth, value),
  );

  const currentSidebarWidth = () => {
    const value = Number.parseFloat(
      getComputedStyle(shell).getPropertyValue("--workspace-sidebar-width"),
    );
    return Number.isFinite(value) ? value : defaultSidebarWidth;
  };

  const setSidebarWidth = (value, { persist = true } = {}) => {
    const width = Number.isFinite(value)
      ? clampSidebarWidth(value)
      : defaultSidebarWidth;
    shell.style.setProperty("--workspace-sidebar-width", `${width}px`);
    resizer.setAttribute("aria-valuenow", String(width));
    if (!persist) {
      return;
    }
    try {
      window.localStorage.setItem(widthStorageKey, String(width));
    } catch {
      // Local preference is optional; the current page remains usable.
    }
  };

  const setCollapsed = (collapsed, { persist = true } = {}) => {
    shell.classList.toggle("is-sidebar-collapsed", collapsed);
    document.documentElement.dataset.workspaceSidebarCollapsed = String(collapsed);
    toggle.setAttribute("aria-pressed", String(collapsed));
    resizer.tabIndex = collapsed ? -1 : 0;
    resizer.setAttribute("aria-hidden", String(collapsed));
    const sidebarLabel = collapsed
      ? "展开侧边栏（⌘B / Ctrl+B）"
      : "折叠侧边栏（⌘B / Ctrl+B）";
    toggle.setAttribute("aria-label", sidebarLabel);
    toggle.title = sidebarLabel;
    if (!persist) {
      return;
    }
    try {
      window.localStorage.setItem(collapsedStorageKey, String(collapsed));
    } catch {
      // Local preference is optional; the current page remains usable.
    }
  };

  const setMobileSidebarOpen = (open) => {
    shell.classList.toggle("is-mobile-sidebar-open", open);
    sidebar.inert = !open;
    sidebarScrim.inert = !open;
    toggle.setAttribute("aria-pressed", String(open));
    toggle.setAttribute("aria-expanded", String(open));
    const sidebarLabel = open ? "关闭侧边栏导航" : "打开侧边栏导航";
    toggle.setAttribute("aria-label", sidebarLabel);
    toggle.title = sidebarLabel;
  };

  const hasMobileSidebarHistoryState = () => (
    history.state?.[mobileSidebarHistoryStateKey] === true
  );

  const openMobileSidebar = () => {
    if (shell.classList.contains("is-mobile-sidebar-open")) {
      return;
    }
    if (!hasMobileSidebarHistoryState()) {
      history.pushState(
        { ...(history.state ?? {}), [mobileSidebarHistoryStateKey]: true },
        "",
        window.location.href,
      );
    }
    setMobileSidebarOpen(true);
  };

  const closeMobileSidebar = ({ restoreHistory = true } = {}) => {
    if (!shell.classList.contains("is-mobile-sidebar-open")) {
      return;
    }
    setMobileSidebarOpen(false);
    if (restoreHistory && hasMobileSidebarHistoryState()) {
      history.back();
    }
  };

  try {
    setSidebarWidth(
      Number.parseFloat(window.localStorage.getItem(widthStorageKey)),
      { persist: false },
    );
    setCollapsed(
      window.localStorage.getItem(collapsedStorageKey) === "true",
      { persist: false },
    );
  } catch {
    setSidebarWidth(defaultSidebarWidth, { persist: false });
    setCollapsed(false, { persist: false });
  }

  const syncViewportLayout = () => {
    if (compactViewport.matches) {
      closeMobileSidebar();
      setMobileSidebarOpen(false);
      return;
    }
    shell.classList.remove("is-mobile-sidebar-open");
    sidebar.inert = false;
    toggle.removeAttribute("aria-expanded");
    const collapsed = shell.classList.contains("is-sidebar-collapsed");
    toggle.setAttribute("aria-pressed", String(collapsed));
    const sidebarLabel = collapsed
      ? "展开侧边栏（⌘B / Ctrl+B）"
      : "折叠侧边栏（⌘B / Ctrl+B）";
    toggle.setAttribute("aria-label", sidebarLabel);
    toggle.title = sidebarLabel;
  };
  syncViewportLayout();
  compactViewport.addEventListener("change", syncViewportLayout);

  requestAnimationFrame(() => shell.classList.add("is-layout-ready"));

  let sidebarTransitionTimer = null;
  const clearSidebarTransition = () => {
    if (sidebarTransitionTimer !== null) {
      window.clearTimeout(sidebarTransitionTimer);
      sidebarTransitionTimer = null;
    }
    shell.classList.remove("is-sidebar-opening", "is-sidebar-closing");
  };

  const expandSidebar = () => {
    clearSidebarTransition();
    if (!shell.classList.contains("is-sidebar-collapsed")) {
      return;
    }
    shell.classList.add("is-sidebar-opening");
    setCollapsed(false);
    sidebarTransitionTimer = window.setTimeout(() => {
      shell.classList.remove("is-sidebar-opening");
      sidebarTransitionTimer = null;
    }, 180);
  };

  const collapseSidebar = () => {
    clearSidebarTransition();
    if (shell.classList.contains("is-sidebar-collapsed")) {
      return;
    }
    shell.classList.add("is-sidebar-closing");
    sidebarTransitionTimer = window.setTimeout(() => {
      setCollapsed(true);
      shell.classList.remove("is-sidebar-closing");
      sidebarTransitionTimer = null;
    }, 140);
  };

  const toggleSidebar = () => {
    if (compactViewport.matches) {
      if (shell.classList.contains("is-mobile-sidebar-open")) {
        closeMobileSidebar();
      } else {
        openMobileSidebar();
      }
      return;
    }
    if (shell.classList.contains("is-sidebar-collapsed")) {
      expandSidebar();
    } else {
      collapseSidebar();
    }
  };
  window.toggleWorkspaceSidebar = toggleSidebar;

  toggle.addEventListener("click", toggleSidebar);
  sidebarClose.addEventListener("click", () => closeMobileSidebar());
  sidebarScrim.addEventListener("click", () => closeMobileSidebar());
  document.addEventListener("pointerdown", (event) => {
    if (
      !compactViewport.matches
      || !shell.classList.contains("is-mobile-sidebar-open")
      || !(event.target instanceof Node)
      || sidebar.contains(event.target)
    ) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    closeMobileSidebar();
  }, true);
  window.addEventListener("popstate", () => {
    if (compactViewport.matches) {
      setMobileSidebarOpen(false);
    }
  });
  let resizeState = null;
  const finishResize = (event) => {
    if (resizeState?.pointerId !== event.pointerId) {
      return;
    }
    resizer.releasePointerCapture(event.pointerId);
    resizeState = null;
    shell.classList.remove("is-sidebar-resizing");
  };

  resizer.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || compactViewport.matches || shell.classList.contains("is-sidebar-collapsed")) {
      return;
    }
    resizeState = { pointerId: event.pointerId, startX: event.clientX, startWidth: currentSidebarWidth() };
    resizer.setPointerCapture(event.pointerId);
    shell.classList.add("is-sidebar-resizing");
    event.preventDefault();
  });
  resizer.addEventListener("pointermove", (event) => {
    if (resizeState?.pointerId !== event.pointerId) {
      return;
    }
    setSidebarWidth(resizeState.startWidth + event.clientX - resizeState.startX);
  });
  resizer.addEventListener("pointerup", finishResize);
  resizer.addEventListener("pointercancel", finishResize);
  resizer.addEventListener("keydown", (event) => {
    if (compactViewport.matches || shell.classList.contains("is-sidebar-collapsed")) {
      return;
    }
    const keyWidth = {
      ArrowLeft: currentSidebarWidth() - sidebarWidthStep,
      ArrowRight: currentSidebarWidth() + sidebarWidthStep,
      Home: minimumSidebarWidth,
      End: maximumSidebarWidth,
    }[event.key];
    if (keyWidth === undefined) {
      return;
    }
    event.preventDefault();
    setSidebarWidth(keyWidth);
  });
  document.addEventListener("keydown", (event) => {
    if (
      event.defaultPrevented
      || event.repeat
      || event.altKey
      || !(event.metaKey || event.ctrlKey)
      || event.key.toLowerCase() !== "b"
      || event.target instanceof HTMLInputElement
      || event.target instanceof HTMLTextAreaElement
      || event.target instanceof HTMLSelectElement
      || (event.target instanceof HTMLElement && event.target.isContentEditable)
    ) {
      return;
    }
    event.preventDefault();
    toggleSidebar();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && compactViewport.matches) {
      closeMobileSidebar();
    }
  });
})();
