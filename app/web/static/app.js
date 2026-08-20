"use strict";

const CARD_RETURN_REFRESHERS = {
  codex: () => loadCodexSessions({ refreshModelPreference: true }),
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
    elements.chubServiceDetail.textContent = "Chub Web 已重启并恢复，页面即将刷新";
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
    description: "重启会重新加载当前代码和配置，页面连接会短暂中断。独立 Worker 持有的快速任务会继续运行。",
    confirmLabel: "确认重启",
    pendingLabel: "正在下发…",
    errorMessage: "重启失败。",
    onConfirm: requestHubRestart,
  });
});

elements.refreshStatus.addEventListener("click", loadStatus);
elements.refreshAutomations.addEventListener("click", () => loadAutomations());


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
restoreCodexModelPreferenceCache();
restoreCodexQuotaCache();
connectToHub();
