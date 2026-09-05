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
  const toolbarError = document.getElementById("workspace-toolbar-error");
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
  let toolbarFeedbackTimer = null;
  let toolbarPersistentError = "";
  let quickSessionUsageRequest = null;
  let quickSessionUsageGeneration = 0;
  const initialQuickSessionPanel = document.querySelector(
    "#workspace-section-content.workspace-inline-quick-session",
  );
  const quickSessionSelectionMessageType = "chub.workspace.quick-session-selection";
  const quickSessionActivityMessageType = "chub.workspace.quick-session-activity";
  const quickSessionInteractionMessageType = "chub.workspace.quick-session-interaction";
  const quickSessionChangedMessageType = "chub.workspace.quick-session-changed";
  const quickSessionFeedbackMessageType = "chub.workspace.quick-session-feedback";
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
    if (health instanceof HTMLElement) {
      health.lastChild.textContent = text;
      health.title = text;
    }
  };

  const cancelQuickSessionUsageRequest = () => {
    quickSessionUsageGeneration += 1;
    quickSessionUsageRequest?.abort();
    quickSessionUsageRequest = null;
  };

  const loadQuickSessionToolbarUsage = async () => {
    cancelQuickSessionUsageRequest();
    const requestGeneration = quickSessionUsageGeneration;
    const controller = new AbortController();
    quickSessionUsageRequest = controller;
    try {
      const response = await fetch("/api/ai/usage", { signal: controller.signal });
      const result = await response.json().catch(() => null);
      if (!response.ok || result?.success !== true) throw new Error("AI 额度暂不可用。");
      const usage = typeof result?.data?.display?.long === "string"
        ? result.data.display.long.trim()
        : "";
      if (
        requestGeneration === quickSessionUsageGeneration
        && window.workspaceQuickSessionOpen
      ) {
        setToolbarStatus(usage || "AI 额度暂不可用。");
      }
    } catch (error) {
      if (error?.name !== "AbortError" && requestGeneration === quickSessionUsageGeneration) {
        setToolbarStatus("AI 额度暂不可用。");
      }
    } finally {
      if (requestGeneration === quickSessionUsageGeneration) quickSessionUsageRequest = null;
    }
  };

  const setWorkspaceToolbarFeedback = (text = "", kind = "error") => {
    if (!(toolbarError instanceof HTMLElement)) return;
    toolbarError.textContent = text;
    toolbarError.className = kind === "warning"
      ? "workspace-preview-toolbar-error workspace-preview-toolbar-error-warning"
      : "workspace-preview-toolbar-error";
    toolbarError.hidden = !text;
  };

  window.setWorkspaceToolbarError = (text = "") => {
    window.clearTimeout(toolbarFeedbackTimer);
    toolbarFeedbackTimer = null;
    toolbarPersistentError = text;
    setWorkspaceToolbarFeedback(toolbarPersistentError, "error");
  };

  window.showWorkspaceToolbarFeedback = (text, kind = "error") => {
    window.clearTimeout(toolbarFeedbackTimer);
    setWorkspaceToolbarFeedback(text, kind);
    if (!text) {
      setWorkspaceToolbarFeedback(toolbarPersistentError, "error");
      return;
    }
    toolbarFeedbackTimer = window.setTimeout(() => {
      setWorkspaceToolbarFeedback(toolbarPersistentError, "error");
    }, kind === "warning" ? 5500 : 7000);
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
      window.disposeWorkspaceWorkstation?.();
      window.disposeWorkspaceAutomationControls?.();
      cancelQuickSessionUsageRequest();
      currentContent.replaceWith(nextContent);
      window.workspaceQuickSessionOpen = false;
      workspaceMain?.classList.remove("is-showing-quick-session");
      history.replaceState(history.state, "", targetUrl.href);
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
      if (compactViewport.matches) closeMobileSidebar({ restoreHistory: false });
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
      if (window.workspaceQuickSessionOpen) {
        setToolbarStatus("快速会话已加载");
        void loadQuickSessionToolbarUsage();
      }
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
    } else if (selection.type === quickSessionInteractionMessageType) {
      window.dispatchEvent(new Event("chub.workspace.session-action-menu-dismiss"));
    } else if (selection.type === quickSessionChangedMessageType) {
      window.dispatchEvent(new Event("chub.workspace.session-action-menu-dismiss"));
      if (selection.returnToWorkspace === true) {
        window.location.replace("/");
        return;
      }
      window.refreshWorkspaceSessions?.();
    } else if (selection.type === quickSessionFeedbackMessageType) {
      const text = typeof selection.text === "string" ? selection.text.trim() : "";
      const kind = selection.kind === "warning" ? "warning" : "error";
      if (text) window.showWorkspaceToolbarFeedback?.(text, kind);
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
        void loadQuickSessionToolbarUsage();
      }
    }, { once: true });
    panel.append(frame);
    window.disposeWorkspaceWorkstation?.();
    window.disposeWorkspaceAutomationControls?.();
    currentContent.replaceWith(panel);
    window.workspaceQuickSessionOpen = true;
    workspaceMain?.classList.add("is-showing-quick-session");
    clearWorkspaceSectionSelection();
    showSectionToolbarLoading("quick-session");
    setToolbarStatus("正在加载快速会话…");
    if (compactViewport.matches) closeMobileSidebar();
  };


  window.initializeWorkspaceAutomationControls = () => {
  window.disposeWorkspaceAutomationControls?.();
  const automationStartButton = document.getElementById("workspace-automation-browser-start");
  const automationStartDialog = document.getElementById("workspace-automation-browser-start-dialog");
  const automationStartForm = document.getElementById("workspace-automation-browser-start-form");
  const automationStartClose = document.getElementById("workspace-automation-browser-start-close");
  const automationStartCancel = document.getElementById("workspace-automation-browser-start-cancel");
  const automationStartConfirm = document.getElementById("workspace-automation-browser-start-confirm");
  const automationStartProfile = document.getElementById("workspace-automation-browser-profile");
  const automationBrowserDetail = document.getElementById("workspace-automation-browser-detail");
  const automationFeishuDetail = document.getElementById("workspace-automation-feishu-detail");
  const automationFeishuCheck = document.getElementById("workspace-automation-feishu-check");
  const automationCodexAccountDetail = document.getElementById("workspace-automation-codex-account-detail");
  const automationCodexAccountCheck = document.getElementById("workspace-automation-codex-account-check");
  const automationStopButton = document.getElementById("workspace-automation-browser-stop");
  const automationStopDialog = document.getElementById("workspace-automation-browser-stop-dialog");
  const automationStopForm = document.getElementById("workspace-automation-browser-stop-form");
  const automationStopClose = document.getElementById("workspace-automation-browser-stop-close");
  const automationStopCancel = document.getElementById("workspace-automation-browser-stop-cancel");
  const automationStopConfirm = document.getElementById("workspace-automation-browser-stop-confirm");
  const automationRunButtons = document.querySelectorAll(".workspace-automation-run");
  const weeklyReportRunButtons = document.querySelectorAll(".workspace-weekly-report-run");
  const weeklyReportConfirmAndRunButtons = document.querySelectorAll(
    ".workspace-weekly-report-confirm-and-run",
  );
  const weeklyReportViewSessionButtons = document.querySelectorAll(
    ".workspace-weekly-report-view-session",
  );
  let automationFeishuChecking = false;
  let automationCodexAccountChecking = false;
  let automationRefreshTimer = null;
  let automationRefreshInFlight = false;
  let automationRefreshDisposed = false;
  let automationVisibilityListener = null;
  let automationRefreshRequest = null;

  const automationSurface = document.querySelector(".workspace-automations");
  const automationRefreshIsActive = () => (
    automationSurface instanceof HTMLElement
    && automationSurface.dataset.automationRefreshActive === "true"
  );

  const scheduleAutomationRefresh = () => {
    window.clearTimeout(automationRefreshTimer);
    if (automationRefreshDisposed || !automationRefreshIsActive()) return;
    automationRefreshTimer = window.setTimeout(
      refreshWorkspaceAutomations,
      document.hidden ? 5_000 : 1_500,
    );
  };

  const refreshWorkspaceAutomations = async () => {
    if (automationRefreshDisposed || automationRefreshInFlight) return;
    const currentSurface = document.querySelector(".workspace-automations");
    if (!(currentSurface instanceof HTMLElement)) return;
    automationRefreshInFlight = true;
    const controller = new AbortController();
    automationRefreshRequest = controller;
    try {
      const response = await fetch("/?section=automations", {
        cache: "no-store",
        headers: { Accept: "text/html" },
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("无法刷新自动化状态。");
      const nextDocument = new DOMParser().parseFromString(
        await response.text(),
        "text/html",
      );
      const nextSurface = nextDocument.querySelector(".workspace-automations");
      if (!(nextSurface instanceof HTMLElement)) throw new Error("自动化状态响应无效。");
      currentSurface.replaceWith(nextSurface);
      window.initializeWorkspaceAutomationControls?.();
      return;
    } catch (error) {
      if (!automationRefreshDisposed && error?.name !== "AbortError") {
        scheduleAutomationRefresh();
      }
    } finally {
      if (automationRefreshRequest === controller) automationRefreshRequest = null;
      automationRefreshInFlight = false;
    }
  };

  window.refreshWorkspaceAutomations = refreshWorkspaceAutomations;
  window.disposeWorkspaceAutomationControls = () => {
    automationRefreshDisposed = true;
    window.clearTimeout(automationRefreshTimer);
    automationRefreshTimer = null;
    automationRefreshRequest?.abort();
    automationRefreshRequest = null;
    if (automationVisibilityListener) {
      document.removeEventListener("visibilitychange", automationVisibilityListener);
      automationVisibilityListener = null;
    }
  };

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

  const setAutomationBrowserStatus = (text, kind = "muted") => {
    if (automationBrowserDetail instanceof HTMLElement) {
      setWorkstationStatus(automationBrowserDetail, text, kind);
    }
  };

  const feishuStatusKind = (state) => {
    if (state === "available") return "success";
    if (["login_required", "checking"].includes(state)) return "warning";
    if (state === "failed") return "failed";
    return "muted";
  };

  const setAutomationFeishuStatus = (state) => {
    if (automationFeishuDetail instanceof HTMLElement) {
      setWorkstationStatus(
        automationFeishuDetail,
        state?.message || "飞书环境状态暂时无法读取。",
        feishuStatusKind(state?.state),
      );
    }
  };

  const setAutomationCodexAccountStatus = (state) => {
    if (automationCodexAccountDetail instanceof HTMLElement) {
      setWorkstationStatus(
        automationCodexAccountDetail,
        state?.message || "Codex Runtime 账户状态暂时无法读取。",
        codexAccountStatusKind(state?.state),
      );
    }
  };

  const codexAccountStatusKind = (state) => {
    if (state === "available") return "success";
    if (state === "checking") return "warning";
    if (state === "failed") return "failed";
    return "muted";
  };

  if (
    automationStartButton instanceof HTMLButtonElement
    && automationStartDialog instanceof HTMLDialogElement
    && automationStartForm instanceof HTMLFormElement
    && automationStartProfile instanceof HTMLSelectElement
  ) {
    automationStartButton.addEventListener("click", () => {
      automationStartDialog.showModal();
      automationStartConfirm?.focus();
    });
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
      closeDialog(automationStartDialog);
      automationStartButton.disabled = true;
      setAutomationBrowserStatus(
        initializing ? "正在初始化 Debug Chrome 账户。" : "正在启动 Debug Chrome。",
        "warning",
      );
      try {
        await automationRequest(
          initializing ? "/api/automations/browser/initialize" : "/api/automations/browser/start",
          { profile_id: selected.value, mode: mode.value },
        );
        setAutomationBrowserStatus(initializing
          ? "浏览器账户初始化已受理，正在刷新状态。"
          : "Debug Chrome 已启动，正在刷新状态。", "success");
        window.setTimeout(refreshWorkspaceAutomations, 500);
      } catch (error) {
        setAutomationBrowserStatus(
          error instanceof Error ? error.message : "Debug Chrome 启动失败。",
          "failed",
        );
        automationStartButton.disabled = false;
      }
    });
  }

  if (
    automationStopButton instanceof HTMLButtonElement
    && automationStopDialog instanceof HTMLDialogElement
    && automationStopForm instanceof HTMLFormElement
  ) {
    automationStopButton.addEventListener("click", () => {
      automationStopDialog.showModal();
      automationStopConfirm?.focus();
    });
    [automationStopClose, automationStopCancel].forEach((button) => {
      button?.addEventListener("click", () => closeDialog(automationStopDialog));
    });
    automationStopForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      closeDialog(automationStopDialog);
      automationStopButton.disabled = true;
      setAutomationBrowserStatus("正在停止 Debug Chrome。", "warning");
      try {
        await automationRequest("/api/automations/browser/stop");
        setAutomationBrowserStatus("Debug Chrome 已停止，正在刷新状态。", "success");
        window.setTimeout(refreshWorkspaceAutomations, 500);
      } catch (error) {
        setAutomationBrowserStatus(
          error instanceof Error ? error.message : "Debug Chrome 停止失败。",
          "failed",
        );
        automationStopButton.disabled = false;
      }
    });
  }

  if (automationFeishuCheck instanceof HTMLButtonElement) {
    automationFeishuCheck.addEventListener("click", async () => {
      if (automationFeishuChecking || automationFeishuCheck.disabled) return;
      automationFeishuChecking = true;
      automationFeishuCheck.disabled = true;
      automationFeishuCheck.textContent = "检查中…";
      setAutomationFeishuStatus({ state: "checking", message: "正在检查飞书登录状态。" });
      try {
        setAutomationFeishuStatus(
          await automationRequest("/api/automations/environment/feishu/check"),
        );
      } catch (error) {
        setAutomationFeishuStatus({
          state: "failed",
          message: error instanceof Error ? error.message : "飞书环境检查失败。",
        });
      } finally {
        automationFeishuChecking = false;
        automationFeishuCheck.disabled = false;
        automationFeishuCheck.textContent = "检查";
      }
    });
  }

  if (automationCodexAccountCheck instanceof HTMLButtonElement) {
    automationCodexAccountCheck.addEventListener("click", async () => {
      if (automationCodexAccountChecking || automationCodexAccountCheck.disabled) return;
      automationCodexAccountChecking = true;
      automationCodexAccountCheck.disabled = true;
      automationCodexAccountCheck.textContent = "检查中…";
      setAutomationCodexAccountStatus({ state: "checking", message: "正在检查 Codex Runtime 账户状态。" });
      try {
        setAutomationCodexAccountStatus(
          await automationRequest("/api/automations/environment/codex/check"),
        );
      } catch (error) {
        setAutomationCodexAccountStatus({
          state: "failed",
          message: error instanceof Error ? error.message : "Codex Runtime 账户检查失败。",
        });
      } finally {
        automationCodexAccountChecking = false;
        automationCodexAccountCheck.disabled = false;
        automationCodexAccountCheck.textContent = "检查";
      }
    });
  }

  const shouldAutomaticallyCheckAccounts = () => (
    automationBrowserDetail instanceof HTMLElement
    && automationBrowserDetail.dataset.browserState === "running"
  );
  const isUncheckedAccount = (detail) => (
    detail instanceof HTMLElement && detail.dataset.accountState === "unchecked"
  );
  if (shouldAutomaticallyCheckAccounts()) {
    if (isUncheckedAccount(automationFeishuDetail)) {
      automationFeishuCheck?.click();
    }
    if (isUncheckedAccount(automationCodexAccountDetail)) {
      automationCodexAccountCheck?.click();
    }
  }

  automationRunButtons.forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) return;
    button.addEventListener("click", async () => {
      const taskId = button.dataset.automationTaskId;
      const taskTitle = button.dataset.automationTaskTitle;
      if (!taskId || !taskTitle || button.disabled) return;
      const confirmed = await showConfirmationDialog({
        title: "运行自动化任务",
        description: `即将运行“${taskTitle}”。任务将使用当前 Debug Chrome 与登录状态，执行已配置的固定步骤。`,
        details: [{ label: "提交后", value: "任务会在此页面显示执行状态和最终结果。" }],
        confirmLabel: "确认运行",
        pendingLabel: "提交中…",
        tone: "secondary",
        errorMessage: "无法运行自动化任务。",
        onConfirm: () => automationRequest(
          `/api/automations/${encodeURIComponent(taskId)}/run`,
        ),
      });
      if (!confirmed) return;
      const taskDetail = button.closest(".workstation-status-row")
        ?.querySelector(".workstation-status-detail");
      if (taskDetail instanceof HTMLElement) {
        setWorkstationStatus(taskDetail, "任务已受理，正在刷新状态。", "warning");
      }
      button.disabled = true;
      window.setTimeout(refreshWorkspaceAutomations, 500);
    });
  });

  weeklyReportRunButtons.forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) return;
    button.addEventListener("click", async () => {
      const stage = button.dataset.weeklyReportStage;
      if ((stage !== "focus" && stage !== "report") || button.disabled) return;
      const label = stage === "focus" ? "生成工作重点确认清单" : "生成正式周报";
      const confirmed = await showConfirmationDialog({
        title: label,
        description: `将创建独立的周报生成会话并执行“${label}”。生成过程不会重新下载资料。`,
        details: [{ label: "提交后", value: "可在本步骤查看会话和最终产物。" }],
        confirmLabel: "确认运行",
        pendingLabel: "创建会话中…",
        tone: "secondary",
        errorMessage: "无法创建周报生成会话。",
        onConfirm: () => automationRequest(
          `/api/weekly-reports/current/${encodeURIComponent(stage)}/run`,
        ),
      });
      if (!confirmed) return;
      const detail = button.closest(".workstation-status-row")
        ?.querySelector(".workstation-status-detail");
      if (detail instanceof HTMLElement) {
        setWorkstationStatus(detail, "已创建生成会话，正在刷新状态。", "warning");
      }
      button.disabled = true;
      window.setTimeout(refreshWorkspaceAutomations, 500);
    });
  });

  weeklyReportConfirmAndRunButtons.forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) return;
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      const confirmed = await showConfirmationDialog({
        title: "确认并生成正式周报",
        description: "将确认当前工作重点确认清单，并立即在同一周报会话中生成正式周报。",
        details: [
          { label: "确认内容", value: "按当前确认清单生成正式周报" },
          { label: "如需调整", value: "请先查看确认清单并重新生成，再执行确认。" },
        ],
        confirmLabel: "确认并生成",
        pendingLabel: "确认并创建会话中…",
        tone: "secondary",
        errorMessage: "无法确认重点并生成正式周报。",
        onConfirm: () => automationRequest("/api/weekly-reports/current/report/confirm-and-run"),
      });
      if (!confirmed) return;
      const detail = button.closest(".workstation-status-row")
        ?.querySelector(".workstation-status-detail");
      if (detail instanceof HTMLElement) {
        setWorkstationStatus(detail, "已确认重点，正在创建正式周报会话。", "warning");
      }
      button.disabled = true;
      window.setTimeout(refreshWorkspaceAutomations, 500);
    });
  });

  weeklyReportViewSessionButtons.forEach((button) => {
    if (!(button instanceof HTMLButtonElement)) return;
    button.addEventListener("click", () => {
      const sessionId = button.dataset.weeklyReportSessionId;
      if (!sessionId) return;
      const title = button.dataset.weeklyReportSessionTitle || "周报生成会话";
      window.selectWorkspaceQuickSession?.(sessionId);
      if (typeof window.openWorkspaceQuickSession === "function") {
        window.openWorkspaceQuickSession({ id: sessionId, session_mode: "quick", title });
        return;
      }
      window.location.assign(`/?session=${encodeURIComponent(sessionId)}`);
    });
  });

  automationVisibilityListener = () => scheduleAutomationRefresh();
  document.addEventListener("visibilitychange", automationVisibilityListener);
  scheduleAutomationRefresh();

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
