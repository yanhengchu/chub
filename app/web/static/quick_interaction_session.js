"use strict";

(function exposeQuickInteractionSession(root) {
  const core = root.QuickInteractionCore
    || (typeof module !== "undefined" && module.exports
      ? require("./quick_interactions_core.js")
      : null);

  function sessionUrl(sessionId) {
    return `/codex/${encodeURIComponent(sessionId)}/quick-interactions/conversation`;
  }

  function buildSessionPreview(session) {
    const title = typeof session?.title === "string" ? session.title.trim() : "";
    const displayTitle = title || "未命名 Session";
    const renameAllowed = session?.workspace_id !== "weixin-translation";
    return Object.freeze({
      displayTitle,
      documentTitle: `${displayTitle} · 快速交互`,
      renameAllowed,
      loadingLabel: "正在读取 Session 状态",
    });
  }

  function buildSessionState({
    session,
    activeInteraction,
    archivePending,
    promptLength,
  }) {
    const preview = buildSessionPreview(session);
    const busy = activeInteraction || session.quick_interaction_running === true;
    const archiveReady = Boolean(session.can_archive);
    const archiveBusy = busy || archivePending;
    const archiveLabel = !archiveReady
      ? "尚未启动的 Session 无法归档"
      : archiveBusy
        ? "Session 正在执行，暂不能归档"
        : "归档 Session";
    const submissionReason = core.submissionBlockReason({
      session,
      activeInteraction,
      promptLength,
    });
    return Object.freeze({
      ...preview,
      busy,
      archiveReady,
      archiveBusy,
      archiveLabel,
      submissionReason,
      confirmStopUnknownTerminal: (
        session.status === "running" && session.activity === "unknown"
      ),
    });
  }

  function buildSwitcher({ sessions, currentSessionId }) {
    const ordered = core.sessionSwitcherEntries(sessions);
    const labels = core.sessionSwitcherLabels(ordered);
    const items = ordered.map((session) => {
      const status = core.sessionSwitcherStatus(session);
      const current = session.id === currentSessionId;
      const title = typeof session.title === "string" ? session.title.trim() : "";
      const label = labels.get(session.id);
      return Object.freeze({
        id: session.id,
        url: sessionUrl(session.id),
        label,
        text: `${label} · ${status}`,
        title: `${current ? "当前" : "切换到"} ${label}${title && label !== title ? `，${title}` : ""}，${status}`,
        ariaLabel: `${label}${title && label !== title ? `，${title}` : ""}，${status}${current ? "，当前 Session" : ""}`,
        current,
        working: status === "执行中",
        status,
      });
    });
    return Object.freeze({
      items,
      signature: JSON.stringify([
        currentSessionId,
        items.map((item) => [item.id, item.label, item.title, item.status]),
      ]),
    });
  }

  function buildCreationState(context, pending) {
    const workspaces = Array.isArray(context.workspaces) ? context.workspaces : [];
    const available = context.available === true;
    const hasAvailableWorkspace = workspaces.some(
      (workspace) => workspace.available === true,
    );
    const label = !available
      ? context.unavailableReason || "Codex 当前不可用"
      : !hasAvailableWorkspace
        ? "当前没有可用工作目录"
        : pending
          ? "正在新建 Session"
          : "新建 Session";
    return Object.freeze({
      available,
      workspaces,
      disabled: pending || !available || !hasAvailableWorkspace,
      label,
    });
  }

  function archiveDescription(session) {
    const title = session?.title?.trim() || "未命名 Session";
    return `归档“${title}”后，该 Session 将从活动列表移除，正在运行的实时终端会停止；`
      + "如已分配微信槽位，槽位也会释放。Chub 页面暂不提供恢复入口。";
  }

  function createView({ documentRef, windowRef, elements, showMessage }) {
    function renderPreview(session) {
      const state = buildSessionPreview(session);
      elements.title.textContent = state.displayTitle;
      elements.title.title = state.displayTitle;
      documentRef.title = state.documentTitle;
      elements.titleRow.hidden = false;
      elements.titleRow.setAttribute("aria-busy", "true");
      elements.rename.hidden = !state.renameAllowed;
      elements.rename.disabled = true;
      elements.rename.title = state.loadingLabel;
      elements.rename.setAttribute("aria-label", state.loadingLabel);
      elements.archive.disabled = true;
      elements.archive.title = state.loadingLabel;
      elements.archive.setAttribute("aria-label", state.loadingLabel);
    }

    function renderSwitcher(snapshot, previousSignature = "") {
      const state = buildSwitcher(snapshot);
      if (state.signature === previousSignature) {
        return state.signature;
      }
      const previousScrollLeft = elements.switcher.scrollLeft;
      elements.switcher.replaceChildren();
      elements.navigation.hidden = false;
      elements.switcher.hidden = state.items.length === 0;
      state.items.forEach((item) => {
        const button = documentRef.createElement("button");
        const dot = documentRef.createElement("span");
        const label = documentRef.createElement("span");
        button.type = "button";
        button.className = "conversation-session-switch";
        button.dataset.sessionId = item.id;
        button.dataset.sessionUrl = item.url;
        button.title = item.title;
        button.setAttribute("aria-label", item.ariaLabel);
        if (item.current) {
          button.classList.add("is-current");
          button.setAttribute("aria-current", "page");
        }
        if (item.working) {
          button.classList.add("is-working");
        }
        dot.className = "conversation-session-dot";
        dot.setAttribute("aria-hidden", "true");
        label.textContent = item.text;
        button.append(dot, label);
        elements.switcher.append(button);
      });
      if (previousScrollLeft > 0) {
        elements.switcher.scrollLeft = previousScrollLeft;
      } else {
        const current = elements.switcher.querySelector("[aria-current='page']");
        windowRef.requestAnimationFrame(() => {
          current?.scrollIntoView({ block: "nearest", inline: "center" });
        });
      }
      return state.signature;
    }

    function navigationRequest(event, currentSessionId) {
      const button = event.target.closest?.(".conversation-session-switch");
      if (!button || !elements.switcher.contains(button)) {
        return null;
      }
      const mode = core.sessionNavigationMode({
        button: event.button,
        current: button.dataset.sessionId === currentSessionId,
        altKey: event.altKey,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        shiftKey: event.shiftKey,
      });
      return Object.freeze({
        mode,
        sessionId: button.dataset.sessionId,
        url: button.dataset.sessionUrl,
      });
    }

    function renderCreation(context, pending) {
      const state = buildCreationState(context, pending);
      elements.create.disabled = state.disabled;
      elements.create.title = state.label;
      elements.create.setAttribute("aria-label", state.label);
      return state;
    }

    function openCreate(workspaces, onCreate) {
      elements.createWorkspaces.replaceChildren();
      workspaces.forEach((workspace) => {
        const button = documentRef.createElement("button");
        const name = documentRef.createElement("strong");
        const path = documentRef.createElement("span");
        button.type = "button";
        button.className = "workspace-button";
        button.disabled = workspace.available !== true;
        button.dataset.workspaceAvailable = String(workspace.available === true);
        name.textContent = workspace.name;
        path.textContent = workspace.path;
        button.append(name, path);
        button.addEventListener("click", () => onCreate(workspace.id));
        elements.createWorkspaces.append(button);
      });
      showMessage(elements.createMessage, "");
      elements.createDialog.showModal();
      elements.createWorkspaces.querySelector("button:not(:disabled)")?.focus();
    }

    function setCreatePending(pending) {
      elements.createSurface.toggleAttribute("aria-busy", pending);
      elements.createClose.disabled = pending;
      elements.createWorkspaces.querySelectorAll("button").forEach((button) => {
        button.disabled = pending || button.dataset.workspaceAvailable !== "true";
      });
    }

    function renderSession(snapshot) {
      const state = buildSessionState(snapshot);
      elements.title.textContent = state.displayTitle;
      elements.title.title = state.displayTitle;
      documentRef.title = state.documentTitle;
      elements.titleRow.hidden = false;
      elements.titleRow.removeAttribute("aria-busy");
      elements.rename.hidden = !state.renameAllowed;
      elements.rename.disabled = !state.renameAllowed;
      elements.rename.title = "重命名 Session";
      elements.rename.setAttribute("aria-label", "重命名 Session");
      elements.archive.disabled = !state.archiveReady || state.archiveBusy;
      elements.archive.title = state.archiveLabel;
      elements.archive.setAttribute("aria-label", state.archiveLabel);
      if (elements.submitMessage.dataset.sessionLoadError === "true") {
        delete elements.submitMessage.dataset.sessionLoadError;
        showMessage(elements.submitMessage, "");
      }
      elements.form.setAttribute("aria-busy", String(state.busy));
      elements.submit.disabled = Boolean(state.submissionReason);
      elements.submit.textContent = "发送";
      elements.prompt.disabled = state.busy;
      if (state.submissionReason && !state.busy) {
        showMessage(elements.submitMessage, state.submissionReason);
      } else if (
        state.busy
        || !elements.submitMessage.classList.contains("message-error")
      ) {
        showMessage(elements.submitMessage, "");
      }
      return state;
    }

    function openRename(session) {
      elements.renameInput.value = session.title?.trim() || "";
      showMessage(elements.renameMessage, "");
      elements.renameDialog.showModal();
      windowRef.requestAnimationFrame(() => {
        elements.renameInput.focus();
        elements.renameInput.select();
      });
    }

    function setRenamePending(pending) {
      elements.renameForm.toggleAttribute("aria-busy", pending);
      elements.renameInput.disabled = pending;
      elements.renameClose.disabled = pending;
      elements.renameCancel.disabled = pending;
      elements.renameConfirm.disabled = pending;
    }

    function openArchive(session) {
      elements.archiveDescription.textContent = archiveDescription(session);
      showMessage(elements.archiveMessage, "");
      elements.archiveDialog.showModal();
      elements.archiveConfirm.focus();
    }

    function setArchivePending(pending) {
      elements.archiveForm.toggleAttribute("aria-busy", pending);
      elements.archiveClose.disabled = pending;
      elements.archiveCancel.disabled = pending;
      elements.archiveConfirm.disabled = pending;
      if (pending) {
        elements.archive.disabled = true;
      }
    }

    function renderError(error) {
      elements.titleRow.removeAttribute("aria-busy");
      elements.rename.disabled = true;
      elements.archive.disabled = true;
      elements.create.disabled = true;
      elements.create.title = "Session 状态读取失败，暂不能新建";
      elements.create.setAttribute(
        "aria-label",
        "Session 状态读取失败，暂不能新建",
      );
      elements.submit.disabled = true;
      elements.prompt.disabled = false;
      elements.form.setAttribute("aria-busy", "false");
      elements.submitMessage.dataset.sessionLoadError = "true";
      showMessage(
        elements.submitMessage,
        error.message || "会话状态读取失败。",
        "error",
      );
    }

    return Object.freeze({
      navigationRequest,
      openArchive,
      openCreate,
      openRename,
      renderCreation,
      renderError,
      renderPreview,
      renderSession,
      renderSwitcher,
      setArchivePending,
      setCreatePending,
      setRenamePending,
    });
  }

  const quickInteractionSession = Object.freeze({
    archiveDescription,
    buildCreationState,
    buildSessionPreview,
    buildSessionState,
    buildSwitcher,
    createView,
    firstSessionAfterArchive: core.firstSessionAfterArchive,
    sessionUrl,
  });
  root.QuickInteractionSession = quickInteractionSession;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = quickInteractionSession;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
