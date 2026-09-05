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
    stopPending = false,
    archivePending = false,
    deletePending = false,
    promptLength,
  }) {
    const preview = buildSessionPreview(session);
    const busy = activeInteraction || session.quick_interaction_running === true;
    const usageBlockReason = core.sessionUsageBlockReason(session);
    const usageBlocked = Boolean(usageBlockReason);
    const externallyOccupied = session?.usage?.owner === "external";
    const renameVisible = preview.renameAllowed;
    const renameAllowed = renameVisible && !externallyOccupied;
    const deleteBlockReason = core.sessionDeleteBlockReason(session);
    const archiveBlockReason = core.sessionArchiveBlockReason(session);
    const stopReady = core.sessionStopReady(session);
    const mutationPending = stopPending || archivePending || deletePending;
    const archiveReady = Boolean(session.can_archive);
    const archiveBusy = mutationPending || Boolean(archiveBlockReason);
    const archiveLabel = archiveBlockReason
      ? archiveBlockReason
      : !archiveReady
      ? "当前 Session 暂不可归档"
      : "归档 Session";
    const stopLabel = usageBlocked
      ? usageBlockReason
      : !stopReady
        ? "当前没有正在执行的任务"
      : stopPending
        ? "正在停止 Session"
        : "停止 Session";
    const deleteLabel = deleteBlockReason
      ? deleteBlockReason
      : deletePending
      ? "正在删除 Session"
      : "删除 Session";
    const submissionReason = core.submissionBlockReason({
      session,
      activeInteraction,
      promptLength,
    });
    return Object.freeze({
      ...preview,
      renameVisible,
      renameAllowed,
      renameLabel: externallyOccupied
        ? "其他应用正在使用此 Session；仅可查看历史。"
        : "重命名 Session",
      busy,
      stopReady: stopReady && !usageBlocked,
      stopBusy: mutationPending || usageBlocked,
      stopLabel,
      archiveReady,
      archiveBusy,
      archiveLabel,
      deleteBusy: mutationPending || Boolean(deleteBlockReason),
      deleteLabel,
      submissionReason,
      usageBlocked,
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
    return `归档“${title}”后，该 Session 将从活动列表移除；`
      + "执行中的 Session 需要先等待任务结束。如已分配微信槽位，槽位也会释放。Chub 页面暂不提供恢复入口。";
  }

  function stopDescription(session) {
    const title = session?.title?.trim() || "未命名 Session";
    return `停止“${title}”将终止正在执行的快速任务并关闭实时终端；`
      + "停止后可以重新进入 Session，但在途任务不会恢复。";
  }

  function createView({
    documentRef,
    windowRef,
    elements,
    showMessage,
    showFeedback = () => {},
  }) {
    let sessionLoadError = "";
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
      elements.stop.disabled = true;
      elements.stop.title = state.loadingLabel;
      elements.stop.setAttribute("aria-label", state.loadingLabel);
      elements.archive.disabled = true;
      elements.archive.title = state.loadingLabel;
      elements.archive.setAttribute("aria-label", state.loadingLabel);
      elements.delete.disabled = true;
      elements.delete.title = state.loadingLabel;
      elements.delete.setAttribute("aria-label", state.loadingLabel);
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
      const usableWorkspaces = workspaces.filter((workspace) => workspace.available);
      elements.createWorkspaces.value = usableWorkspaces[0]?.id || "";
      elements.createWorkspacePicker.setOptions(
        usableWorkspaces.map((workspace) => ({ value: workspace.id, label: workspace.name })),
        elements.createWorkspaces.value,
      );
      elements.createConfirm.disabled = !elements.createWorkspaces.value;
      elements.createForm.onsubmit = (event) => {
        event.preventDefault();
        if (elements.createWorkspaces.value) {
          onCreate(elements.createWorkspaces.value);
        }
      };
      showMessage(elements.createMessage, "");
      elements.createDialog.showModal();
      elements.createWorkspacePicker.focus();
    }

    function setCreatePending(pending) {
      elements.createSurface.toggleAttribute("aria-busy", pending);
      elements.createClose.disabled = pending;
      elements.createCancel.disabled = pending;
      elements.createConfirm.disabled = pending || !elements.createWorkspaces.value;
      elements.createConfirm.textContent = pending ? "创建中…" : "创建";
      elements.createWorkspacePicker.setDisabled(pending);
    }

    function renderSession(snapshot) {
      const state = buildSessionState(snapshot);
      elements.title.textContent = state.displayTitle;
      elements.title.title = state.displayTitle;
      documentRef.title = state.documentTitle;
      elements.titleRow.hidden = false;
      elements.titleRow.removeAttribute("aria-busy");
      elements.rename.hidden = !state.renameVisible;
      elements.rename.disabled = !state.renameAllowed;
      elements.rename.title = state.renameLabel;
      elements.rename.setAttribute("aria-label", state.renameLabel);
      elements.stop.disabled = !state.stopReady || state.stopBusy;
      elements.stop.title = state.stopLabel;
      elements.stop.setAttribute("aria-label", state.stopLabel);
      elements.archive.disabled = !state.archiveReady || state.archiveBusy;
      elements.archive.title = state.archiveLabel;
      elements.archive.setAttribute("aria-label", state.archiveLabel);
      elements.delete.disabled = state.deleteBusy;
      elements.delete.title = state.deleteLabel;
      elements.delete.setAttribute("aria-label", state.deleteLabel);
      sessionLoadError = "";
      elements.form.setAttribute("aria-busy", String(state.busy));
      elements.submit.disabled = Boolean(state.submissionReason)
        || !elements.prompt.value.trim();
      const submitLabel = state.submissionReason || "发送";
      elements.submit.setAttribute("aria-label", submitLabel);
      elements.submit.title = submitLabel;
      elements.prompt.title = state.submissionReason || "";
      // A running task only blocks the next write. Keep the draft editable so
      // the next request can be prepared while this Session is working.
      elements.prompt.disabled = false;
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
      elements.archiveDialog.showModal();
      elements.archiveConfirm.focus();
    }

    function setArchivePending(pending) {
      elements.archiveForm.toggleAttribute("aria-busy", pending);
      elements.archiveClose.disabled = pending;
      elements.archiveCancel.disabled = pending;
      elements.archiveConfirm.disabled = pending;
      if (pending) {
        elements.stop.disabled = true;
        elements.archive.disabled = true;
        elements.delete.disabled = true;
      }
    }

    function openStop(session) {
      elements.stopDescription.textContent = stopDescription(session);
      elements.stopDialog.showModal();
      elements.stopConfirm.focus();
    }

    function setStopPending(pending) {
      elements.stopForm.toggleAttribute("aria-busy", pending);
      elements.stopClose.disabled = pending;
      elements.stopCancel.disabled = pending;
      elements.stopConfirm.disabled = pending;
      if (pending) {
        elements.stop.disabled = true;
        elements.archive.disabled = true;
        elements.delete.disabled = true;
      }
    }

    function deleteDescription(session) {
      const title = session?.title?.trim() || "未命名 Session";
      return `删除“${title}”后，该 Session 将永久删除，无法恢复；`
        + "执行中的 Quick Worker 任务会在删除过程中先停止，实时终端也会关闭；"
        + "如已分配微信槽位，槽位也会释放。";
    }

    function openDelete(session) {
      elements.deleteDescription.textContent = deleteDescription(session);
      elements.deleteDialog.showModal();
      elements.deleteConfirm.focus();
    }

    function setDeletePending(pending) {
      elements.deleteForm.toggleAttribute("aria-busy", pending);
      elements.deleteClose.disabled = pending;
      elements.deleteCancel.disabled = pending;
      elements.deleteConfirm.disabled = pending;
      if (pending) {
        elements.stop.disabled = true;
        elements.archive.disabled = true;
        elements.delete.disabled = true;
      }
    }

    function renderError(error) {
      elements.titleRow.removeAttribute("aria-busy");
      elements.rename.disabled = true;
      elements.stop.disabled = true;
      elements.archive.disabled = true;
      elements.delete.disabled = true;
      elements.create.disabled = true;
      elements.create.title = "Session 状态读取失败，暂不能新建";
      elements.create.setAttribute(
        "aria-label",
        "Session 状态读取失败，暂不能新建",
      );
      elements.submit.disabled = true;
      elements.prompt.disabled = false;
      elements.form.setAttribute("aria-busy", "false");
      const message = core.formatErrorMessage(error, "会话状态读取失败。");
      if (message !== sessionLoadError) {
        sessionLoadError = message;
        showFeedback(message, "error");
      }
    }

    return Object.freeze({
      navigationRequest,
      openArchive,
      openDelete,
      openStop,
      openCreate,
      openRename,
      renderCreation,
      renderError,
      renderPreview,
      renderSession,
      renderSwitcher,
      setArchivePending,
      setCreatePending,
      setDeletePending,
      setRenamePending,
      setStopPending,
    });
  }

  const quickInteractionSession = Object.freeze({
    archiveDescription,
    stopDescription,
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
