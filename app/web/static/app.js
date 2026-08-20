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

elements.tokenForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const token = elements.tokenInput.value.trim();
  if (!token) {
    setMessage(elements.globalMessage, "请输入 Hub Token。", "error");
    elements.tokenInput.focus();
    return;
  }
  elements.tokenInput.value = "";
  connectWithToken(token, elements.rememberToken.checked);
});
elements.refreshStatus.addEventListener("click", loadStatus);
elements.refreshAutomations.addEventListener("click", () => loadAutomations());

elements.clearToken.addEventListener("click", () => {
  void showConfirmationDialog({
    title: "退出当前节点",
    description: "退出会清除此浏览器保存的 Hub Token，并隐藏当前节点的受保护内容。",
    confirmLabel: "确认退出",
    pendingLabel: "正在退出…",
    onConfirm: () => {
      connectionAttempt += 1;
      activeToken = "";
      tailscaleAccess = false;
      accessVersion += 1;
      removeStoredToken();
      elements.tokenInput.value = "";
      elements.rememberToken.checked = false;
      clearProtectedView();
      showDisconnectedView("已退出，凭证已从此浏览器清除。", "success");
    },
  });
});

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
const savedSessionToken = sessionStorage.getItem(SESSION_TOKEN_KEY);
const savedLocalToken = localStorage.getItem(LOCAL_TOKEN_KEY);
const savedToken = savedSessionToken || savedLocalToken || "";
ensureCodexCard();
setupCollapsibleCards();
restoreCodexCardCache();
restoreCodexModelPreferenceCache();
restoreCodexQuotaCache();
if (savedLocalToken) {
  elements.rememberToken.checked = Boolean(savedLocalToken);
}
connectWithTailscale(savedToken, Boolean(savedLocalToken));
