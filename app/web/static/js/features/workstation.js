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
let quickWorkerReloadOperationId = null;
let systemUpgradeState = null;
let systemUpgradePollTimer = 0;
let systemUpgradeRequestInProgress = false;
const SYSTEM_UPGRADE_RELOAD_KEY = "chub.systemUpgradeReload.v1";

function readSystemUpgradeReloadOperationId() {
  try {
    return sessionStorage.getItem(SYSTEM_UPGRADE_RELOAD_KEY) || null;
  } catch {
    return null;
  }
}

function rememberSystemUpgradeReload(operationId) {
  try {
    if (operationId) {
      sessionStorage.setItem(SYSTEM_UPGRADE_RELOAD_KEY, operationId);
    } else {
      sessionStorage.removeItem(SYSTEM_UPGRADE_RELOAD_KEY);
    }
  } catch {
    // The current page can still complete the operation if browser storage is unavailable.
  }
}

let systemUpgradeReloadOperationId = readSystemUpgradeReloadOperationId();

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

function systemUpgradeIsRunning() {
  return (
    systemUpgradeRequestInProgress
    || ["requested", "started"].includes(systemUpgradeState?.operation?.status)
  );
}

function syncCoreMaintenanceControls() {
  const connected = hasProtectedAccess();
  elements.restartHub.disabled = (
    !connected
    || hubRestartInProgress
    || systemUpgradeIsRunning()
  );
  elements.quickWorkerRestart.disabled = (
    !connected
    || quickWorkerRequestInProgress
    || systemUpgradeIsRunning()
    || !quickWorkerState?.can_restart
  );
  elements.systemUpgradeStart.disabled = (
    !connected
    || systemUpgradeRequestInProgress
    || systemUpgradeIsRunning()
    || !systemUpgradeState?.can_start
  );
}

function clearSystemUpgradePollTimer() {
  if (systemUpgradePollTimer) {
    window.clearTimeout(systemUpgradePollTimer);
    systemUpgradePollTimer = 0;
  }
}

function systemUpgradePresentation(state) {
  return {
    available: "可执行",
    preparing: "正在准备",
    draining: "正在停止任务",
    cleaning: "正在清理",
    restarting: "正在恢复",
    succeeded: "已完成",
    failed: "恢复失败",
    blocked: "暂不可用",
  }[state] || "状态未知";
}

function renderSystemUpgrade(data) {
  systemUpgradeState = data;
  elements.systemUpgradeDetail.textContent = `状态：${systemUpgradePresentation(data.state)}。${data.message}`;
  syncCoreMaintenanceControls();
  if (
    systemUpgradeReloadOperationId
    && data.operation?.operation_id === systemUpgradeReloadOperationId
    && data.operation.status === "succeeded"
  ) {
    showMaintenanceCompletion(
      elements.systemUpgradeDetail,
      `状态：已完成。${data.operation.message || data.message}`,
    );
    systemUpgradeReloadOperationId = null;
    rememberSystemUpgradeReload(null);
    reloadDashboardAfterMaintenance();
  }
}

function scheduleSystemUpgradePoll() {
  clearSystemUpgradePollTimer();
  if (["preparing", "draining", "cleaning", "restarting"].includes(systemUpgradeState?.state)) {
    systemUpgradePollTimer = window.setTimeout(
      () => loadSystemUpgradeStatus({ background: true }),
      1000,
    );
  }
}

async function loadSystemUpgradeStatus({ background = false } = {}) {
  if (!hasProtectedAccess()) {
    return;
  }
  try {
    renderSystemUpgrade(await apiFetch("/api/maintenance/system-upgrade"));
  } catch (error) {
    if (!background && !handleAccessError(error)) {
      elements.systemUpgradeDetail.textContent = `状态：读取失败。${error.message || "无法读取维护状态。"}`;
    }
  }
  scheduleSystemUpgradePoll();
}

async function startSystemUpgrade() {
  const fingerprint = systemUpgradeState?.plan?.fingerprint;
  if (!fingerprint) {
    throw new Error("恢复方案尚未就绪。");
  }
  systemUpgradeRequestInProgress = true;
  syncCoreMaintenanceControls();
  try {
    const data = await apiFetch("/api/maintenance/system-upgrade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fingerprint }),
    });
    systemUpgradeReloadOperationId = data.operation?.operation_id || null;
    rememberSystemUpgradeReload(systemUpgradeReloadOperationId);
    renderSystemUpgrade(data);
  } finally {
    systemUpgradeRequestInProgress = false;
    syncCoreMaintenanceControls();
    scheduleSystemUpgradePoll();
  }
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
  quickWorkerReloadOperationId = null;
  setBadge(elements.quickWorkerBadge, "正在检查");
  elements.quickWorkerDetail.textContent = "正在检查任务执行服务";
  setMessage(elements.quickWorkerMessage, "");
  setBadge(elements.chubServiceBadge, "正在检查");
  elements.chubServiceDetail.textContent = "正在检查服务状态";
  setMessage(elements.chubServiceMessage, "");
  clearSystemUpgradePollTimer();
  systemUpgradeState = null;
  systemUpgradeRequestInProgress = false;
  systemUpgradeReloadOperationId = readSystemUpgradeReloadOperationId();
  elements.systemUpgradeDetail.textContent = "状态：正在检查运行态恢复条件";
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
  const matchesRequestedReload = (
    quickWorkerReloadOperationId
    && data.operation?.operation_id === quickWorkerReloadOperationId
  );
  if (matchesRequestedReload && data.operation?.status === "failed") {
    quickWorkerReloadOperationId = null;
  }
  if (matchesRequestedReload && data.operation?.status === "succeeded") {
    setBadge(elements.quickWorkerBadge, "重启完成", "success");
    showMaintenanceCompletion(elements.quickWorkerDetail, data.operation.message);
    quickWorkerReloadOperationId = null;
    reloadDashboardAfterMaintenance();
  }
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
    quickWorkerReloadOperationId = ["restarting", "succeeded"].includes(
      data.operation?.status,
    ) ? data.operation.operation_id : null;
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

function systemUpgradeImpactDetails() {
  const activeTasks = Number(quickWorkerState?.active_tasks || 0);
  const queuedTasks = Number(quickWorkerState?.queued_tasks || 0);
  const taskCount = activeTasks + queuedTasks;
  const sessionCount = Number(systemUpgradeState?.plan?.session_count || 0);
  return [
    {
      label: "快速任务",
      value: taskCount
        ? `${taskCount} 个在途或排队任务将停止。`
        : "当前没有在途或排队任务。",
    },
    {
      label: "Chub Session",
      value: sessionCount
        ? `${sessionCount} 个本地关联将清理；Codex 原生会话保留。`
        : "当前没有本地关联；Codex 原生会话保留。",
    },
    {
      label: "服务切换",
      value: "Quick Worker 与 Chub Web 将依次重启。",
    },
  ];
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
      loadSystemUpgradeStatus(),
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

elements.systemUpgradeStart.addEventListener("click", () => {
  void showConfirmationDialog({
    title: "系统升级与恢复",
    description: "以下运行态将按当前状态重建。",
    details: systemUpgradeImpactDetails(),
    confirmLabel: "确认升级与恢复",
    pendingLabel: "正在开始…",
    errorMessage: "系统升级与恢复未能启动。",
    onConfirm: startSystemUpgrade,
  });
});
