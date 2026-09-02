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
  let restartFailed = false;
  try {
    await waitForHubRestart(previousInstanceId);
    setWorkstationStatus(elements.chubServiceDetail, "Chub 已重启并恢复。", "success");
    showMaintenanceCompletion(elements.chubServiceDetail, "Chub 已重启并恢复。");
    reloadDashboardAfterMaintenance();
  } catch (error) {
    restartFailed = true;
    if (!handleAccessError(error)) {
      setWorkstationStatus(
        elements.chubServiceDetail,
        "重启后未能恢复控制面连接。",
        "failed",
      );
      setMessage(
        elements.chubServiceMessage,
        error.message || "重启后未能恢复连接。",
        "error",
      );
    }
  } finally {
    hubRestartInProgress = false;
    syncCoreMaintenanceControls();
    if (restartFailed) {
      void loadQuickWorkerStatus();
    }
  }
}

async function requestHubRestart() {
  const previousInstanceId = await hubInstanceId();
  await apiFetch("/api/maintenance/restart", { method: "POST" });
  hubRestartInProgress = true;
  setWorkstationStatus(elements.chubServiceDetail, "正在等待新实例恢复", "warning");
  setMessage(elements.chubServiceMessage, "");
  deferQuickWorkerStatusDuringHubRestart();
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
