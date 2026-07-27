"use strict";

const CARD_RETURN_REFRESHERS = {
  codex: () => loadCodexSessions(),
  openclaw: () => loadOpenClaw(),
  "project-docs": () => loadProjectDocuments(),
};

function refreshCardsOnReturn() {
  if (!activeToken) {
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

async function restartHub() {
  elements.restartDialogConfirm.disabled = true;
  setMessage(elements.globalMessage, "正在下发重启命令…");
  try {
    const previousInstanceId = await hubInstanceId();
    await apiFetch("/api/maintenance/restart", { method: "POST" });
    elements.restartDialog.close();
    setMessage(elements.globalMessage, "重启命令已下发，正在等待 Hub 恢复…");
    await waitForHubRestart(previousInstanceId);
    setMessage(elements.globalMessage, "Chub 已恢复，正在同步卡片状态…");
    await refreshCardsAfterRestart();
    setMessage(elements.globalMessage, "Chub 已重启并恢复连接。", "success");
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.globalMessage, error.message || "重启失败。", "error");
    }
  } finally {
    elements.restartDialogConfirm.disabled = false;
  }
}

elements.restartHub.addEventListener("click", () => {
  if (!elements.restartDialog.open) {
    elements.restartDialog.showModal();
  }
});
elements.restartDialogClose.addEventListener("click", () => elements.restartDialog.close());
elements.restartDialogCancel.addEventListener("click", () => elements.restartDialog.close());
elements.restartDialogConfirm.addEventListener("click", restartHub);
elements.restartDialog.addEventListener("click", (event) => {
  if (event.target === elements.restartDialog) {
    elements.restartDialog.close();
  }
});

elements.clearToken.addEventListener("click", () => {
  if (!window.confirm("确定退出当前节点吗？此设备保存的 Hub Token 将被清除。")) {
    return;
  }
  connectionAttempt += 1;
  activeToken = "";
  accessVersion += 1;
  removeStoredToken();
  elements.tokenInput.value = "";
  elements.rememberToken.checked = false;
  clearProtectedView();
  showDisconnectedView("已退出，凭证已从此浏览器清除。", "success");
});

cardCollapsedState = loadCardCollapsedState();
const savedSessionToken = sessionStorage.getItem(SESSION_TOKEN_KEY);
const savedLocalToken = localStorage.getItem(LOCAL_TOKEN_KEY);
const savedToken = savedSessionToken || savedLocalToken || "";
ensureCodexCard();
setupCollapsibleCards();
if (savedToken) {
  restoreCodexCardCache();
  elements.rememberToken.checked = Boolean(savedLocalToken);
  setBadge(elements.accessBadge, "自动连接");
  connectWithToken(savedToken, Boolean(savedLocalToken), true);
} else {
  showDisconnectedView();
}
