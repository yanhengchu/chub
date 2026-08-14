"use strict";

const CARD_RETURN_REFRESHERS = {
  codex: () => loadCodexSessions(),
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
    await refreshCardsAfterRestart();
    setMessage(elements.globalMessage, "");
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(
        elements.globalMessage,
        error.message || "重启后未能恢复连接。",
        "error",
      );
    }
  } finally {
    hubRestartInProgress = false;
    elements.restartHub.disabled = !hasProtectedAccess();
  }
}

async function requestHubRestart() {
  const previousInstanceId = await hubInstanceId();
  await apiFetch("/api/maintenance/restart", { method: "POST" });
  hubRestartInProgress = true;
  elements.restartHub.disabled = true;
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
const savedSessionToken = sessionStorage.getItem(SESSION_TOKEN_KEY);
const savedLocalToken = localStorage.getItem(LOCAL_TOKEN_KEY);
const savedToken = savedSessionToken || savedLocalToken || "";
ensureCodexCard();
setupCollapsibleCards();
restoreCodexCardCache();
restoreCodexQuotaCache();
if (savedLocalToken) {
  elements.rememberToken.checked = Boolean(savedLocalToken);
}
connectWithTailscale(savedToken, Boolean(savedLocalToken));
