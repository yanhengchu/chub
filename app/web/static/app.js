"use strict";

const CARD_RETURN_REFRESHERS = {
  codex: () => loadCodexSessions(),
  automations: () => refreshAutomationCard(),
  "project-docs": () => loadProjectDocuments(),
};

function refreshCardsOnReturn() {
  if (!hasProtectedAccess()) {
    return;
  }
  const now = Date.now();
  if (now - cardsRefreshAt < 500) {
    return;
  }
  cardsRefreshAt = now;
  loadStatus();
  document.querySelectorAll('[data-card-return-refresh="true"]').forEach((card) => {
    const refresh = CARD_RETURN_REFRESHERS[card.dataset.cardKey];
    if (refresh) {
      refresh();
    }
  });
}

window.addEventListener("pageshow", () => {
  refreshCardsOnReturn();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    refreshCardsOnReturn();
    if (codexShouldPoll) {
      loadCodexSessions({ background: true });
    }
  } else {
    clearCodexPollTimer();
  }
});

async function monitorHubRestart(previousInstanceId) {
  try {
    await waitForHubRestart(previousInstanceId);
    setBadge(elements.chubServiceBadge, "运行正常", "success");
    showMaintenanceCompletion(elements.chubServiceDetail, "Chub 已重启并恢复。");
    reloadDashboardAfterMaintenance();
  } catch (error) {
    if (!handleAccessError(error)) {
      setBadge(elements.chubServiceBadge, "恢复失败", "failed");
      setMessage(
        elements.chubServiceMessage,
        error.message || "重启后未能恢复连接。",
        "error",
      );
    }
  } finally {
    hubRestartInProgress = false;
    syncCoreMaintenanceControls();
  }
}

async function requestHubRestart() {
  const previousInstanceId = await hubInstanceId();
  await apiFetch("/api/maintenance/restart", { method: "POST" });
  hubRestartInProgress = true;
  setBadge(elements.chubServiceBadge, "正在重启", "muted");
  elements.chubServiceDetail.textContent = "正在等待新实例恢复";
  setMessage(elements.chubServiceMessage, "");
  syncCoreMaintenanceControls();
  setMessage(elements.globalMessage, "");
  void monitorHubRestart(previousInstanceId);
}

elements.restartHub.addEventListener("click", () => {
  void showConfirmationDialog({
    title: "重启 Chub",
    description: "重启会重新加载当前代码和配置，页面连接会短暂中断。Chub Quick Worker、Ubuntu Chub Debug Chrome 和 OpenClaw Gateway 是独立服务，不会被重启；已接受的快速任务会继续运行。实时终端连接可能中断，但 tmux 和原生 Codex 会话保留，重新进入时恢复。",
    confirmLabel: "确认重启",
    pendingLabel: "正在下发…",
    errorMessage: "重启失败。",
    onConfirm: requestHubRestart,
  });
});

elements.stopHub.addEventListener("click", () => {
  void showConfirmationDialog({
    title: "停止 Chub",
    description: "停止后当前页面会断开，首页不能继续执行启动操作；请使用本机 chub start 或系统服务入口恢复 Chub。Quick Worker、Debug Chrome 和 OpenClaw Gateway 不会被此操作停止。",
    confirmLabel: "确认停止",
    pendingLabel: "正在停止…",
    errorMessage: "Chub 停止失败。",
    onConfirm: async () => {
      await apiFetch("/api/maintenance/chub/stop", { method: "POST" });
      setBadge(elements.chubServiceBadge, "正在停止", "muted");
      elements.chubServiceDetail.textContent = "页面连接即将断开";
      elements.stopHub.disabled = true;
      elements.restartHub.disabled = true;
    },
  });
});

elements.refreshAutomations.addEventListener("click", refreshAutomationCard);


cardCollapsedState = loadCardCollapsedState();
if (typeof cardCollapsedState.workstation !== "boolean") {
  const hadEnvironmentPreference = typeof cardCollapsedState["automation-environment"] === "boolean";
  const hadOpenClawPreference = typeof cardCollapsedState.openclaw === "boolean";
  if (hadEnvironmentPreference || hadOpenClawPreference) {
    cardCollapsedState.workstation = (
      cardCollapsedState["automation-environment"] === true
      && cardCollapsedState.openclaw === true
    );
    delete cardCollapsedState["automation-environment"];
    delete cardCollapsedState.openclaw;
    saveCardCollapsedState();
  }
}
ensureCodexCard();
setupCollapsibleCards();
restoreCodexCardCache();
restoreCodexQuotaCache();
connectToHub();
