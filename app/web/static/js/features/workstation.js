"use strict";

const QUICK_WORKER_ACTIVE_STATES = new Set([
  "busy",
  "draining",
  "recovering",
  "restarting",
]);
let quickWorkerState = null;
let quickWorkerPollTimer = 0;
let quickWorkerRequestInProgress = false;

function quickWorkerPresentation(state) {
  return {
    ready: ["运行正常", "success"],
    busy: ["任务处理中", "timeout"],
    draining: ["正在排空", "timeout"],
    recovering: ["正在恢复", "muted"],
    restarting: ["正在重启", "muted"],
    incompatible: ["版本不兼容", "failed"],
    unavailable: ["不可用", "failed"],
  }[state] || ["状态未知", "failed"];
}

function quickWorkerReloadInProgress() {
  return quickWorkerState?.state === "restarting" || quickWorkerRequestInProgress;
}

function syncCoreMaintenanceControls() {
  const connected = hasProtectedAccess();
  elements.restartHub.disabled = (
    !connected
    || hubRestartInProgress
    || quickWorkerReloadInProgress()
  );
  elements.quickWorkerRestart.disabled = (
    !connected
    || hubRestartInProgress
    || quickWorkerRequestInProgress
    || !quickWorkerState?.can_restart
  );
}

function clearQuickWorkerPollTimer() {
  if (quickWorkerPollTimer) {
    window.clearTimeout(quickWorkerPollTimer);
    quickWorkerPollTimer = 0;
  }
}

function resetQuickWorkerView() {
  clearQuickWorkerPollTimer();
  quickWorkerState = null;
  quickWorkerRequestInProgress = false;
  setBadge(elements.quickWorkerBadge, "正在检查");
  elements.quickWorkerDetail.textContent = "正在检查任务执行服务";
  setMessage(elements.quickWorkerMessage, "");
  setBadge(elements.chubServiceBadge, "正在检查");
  elements.chubServiceDetail.textContent = "正在检查服务状态";
  setMessage(elements.chubServiceMessage, "");
  syncCoreMaintenanceControls();
}

function renderQuickWorker(data) {
  quickWorkerState = data;
  const [badgeText, badgeKind] = quickWorkerPresentation(data.state);
  setBadge(elements.quickWorkerBadge, badgeText, badgeKind);
  elements.quickWorkerDetail.textContent = data.message;
  const failedOperation = data.operation?.status === "failed";
  setMessage(
    elements.quickWorkerMessage,
    failedOperation ? data.operation.message : "",
    failedOperation ? "error" : "",
  );
  syncCoreMaintenanceControls();
}

function scheduleQuickWorkerPoll() {
  clearQuickWorkerPollTimer();
  if (QUICK_WORKER_ACTIVE_STATES.has(quickWorkerState?.state)) {
    quickWorkerPollTimer = window.setTimeout(
      () => loadQuickWorkerStatus({ background: true }),
      1000,
    );
  }
}

async function loadQuickWorkerStatus({ background = false } = {}) {
  const requestVersion = accessVersion;
  try {
    const data = await apiFetch("/api/maintenance/quick-worker");
    if (requestVersion !== accessVersion) {
      return;
    }
    renderQuickWorker(data);
    scheduleQuickWorkerPoll();
  } catch (error) {
    if (requestVersion !== accessVersion || handleAccessError(error)) {
      return;
    }
    if (!background || !quickWorkerState) {
      setBadge(elements.quickWorkerBadge, "刷新失败", "failed");
    }
    setMessage(
      elements.quickWorkerMessage,
      quickWorkerState
        ? `状态刷新失败，当前展示上次检测结果：${error.message || "无法读取最新状态。"}`
        : error.message || "Quick Worker 状态读取失败。",
      "error",
    );
    syncCoreMaintenanceControls();
    scheduleQuickWorkerPoll();
  }
}

async function requestQuickWorkerRestart() {
  quickWorkerRequestInProgress = true;
  setBadge(elements.quickWorkerBadge, "正在下发", "muted");
  elements.quickWorkerDetail.textContent = "正在请求受控重启";
  setMessage(elements.quickWorkerMessage, "");
  syncCoreMaintenanceControls();
  try {
    const data = await apiFetch("/api/maintenance/quick-worker/restart", {
      method: "POST",
    });
    renderQuickWorker(data);
    scheduleQuickWorkerPoll();
  } catch (error) {
    await loadQuickWorkerStatus();
    setMessage(
      elements.quickWorkerMessage,
      error.message || "Quick Worker 重启请求失败。",
      "error",
    );
    throw error;
  } finally {
    quickWorkerRequestInProgress = false;
    syncCoreMaintenanceControls();
  }
}

async function refreshWorkstationEnvironment() {
  if (!hasProtectedAccess()) {
    return;
  }
  elements.refreshWorkstationEnvironment.disabled = true;
  try {
    await Promise.allSettled([
      loadStatus(),
      loadQuickWorkerStatus(),
      loadAutomationEnvironment(),
      loadOpenClaw(),
    ]);
  } finally {
    elements.refreshWorkstationEnvironment.disabled = false;
  }
}

elements.refreshWorkstationEnvironment.addEventListener(
  "click",
  refreshWorkstationEnvironment,
);
elements.quickWorkerRestart.addEventListener("click", () => {
  void showConfirmationDialog({
    title: "重启 Quick Worker",
    description: "仅在没有执行中或排队中的快速任务时可重启。重启期间 Chub Web 保持可用，但新的快速任务会暂时等待 Worker 恢复。",
    confirmLabel: "确认重启",
    pendingLabel: "正在下发…",
    errorMessage: "Quick Worker 重启失败。",
    onConfirm: requestQuickWorkerRestart,
  });
});
