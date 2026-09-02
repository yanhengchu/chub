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

function quickWorkerStatusKind(state) {
  return {
    ready: "muted",
    busy: "warning",
    draining: "warning",
    recovering: "warning",
    restarting: "warning",
    incompatible: "failed",
    unavailable: "failed",
    stopped: "muted",
  }[state] || "failed";
}

function aiRuntimeStatusKind(data) {
  const runtimeItems = Array.isArray(data.runtimes) ? data.runtimes : [];
  const hasAvailable = runtimeItems.some((item) => item.state === "available");
  if (runtimeItems.some((item) => item.state === "unavailable")) {
    return hasAvailable ? "warning" : "failed";
  }
  if (runtimeItems.some((item) => item.state === "disabled")) {
    return "warning";
  }
  return {
    available: "muted",
    disabled: "warning",
    unavailable: "failed",
    unconfigured: "muted",
    unknown: "failed",
  }[data.runtime_state] || "failed";
}

function aiRuntimeDetail(data) {
  const labels = {
    available: "可用",
    disabled: "已停用",
    unavailable: "不可用",
  };
  const runtimeItems = Array.isArray(data.runtimes) ? data.runtimes : [];
  if (runtimeItems.length) {
    return runtimeItems
      .map((item) => `${item.name}：${labels[item.state] || "状态未知"}`)
      .join(" · ");
  }
  if (data.runtime_message) {
    return data.runtime_message;
  }
  return "AI Runtime 状态暂无法确认。";
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

function systemUpgradeCompletionPresentation(data) {
  return "已完成";
}

function systemUpgradePresentation(data) {
  return {
    idle: "可执行",
    available: "可执行",
    preparing: "正在准备",
    draining: "正在停止任务",
    archiving: "正在清理",
    cleaning: "正在清理",
    restarting: "正在恢复",
    succeeded: "已完成",
    failed: "恢复失败",
    blocked: "升级功能未就绪",
  }[data.state] || "状态未知";
}

function systemUpgradeDetail(data) {
  if (["idle", "available"].includes(data.state)) {
    if (data.plan_unavailable) {
      return "状态：仅可恢复。升级方案不可用。";
    }
    const points = Array.isArray(data.plan?.upgrade_points)
      ? data.plan.upgrade_points
      : [];
    if (points.length) {
      return `状态：待升级。${points.join(" · ")}`;
    }
    return "状态：无待升级。";
  }
  return `状态：${systemUpgradePresentation(data)}。${data.message}`;
}

function renderSystemUpgrade(data) {
  systemUpgradeState = data;
  elements.systemUpgradeStart.textContent = "升级与恢复";
  elements.systemUpgradeDetail.textContent = systemUpgradeDetail(data);
  syncCoreMaintenanceControls();
  if (
    systemUpgradeReloadOperationId
    && data.operation?.operation_id === systemUpgradeReloadOperationId
    && data.operation.status === "succeeded"
  ) {
    showMaintenanceCompletion(
      elements.systemUpgradeDetail,
      `状态：${systemUpgradeCompletionPresentation(data)}。${data.operation.message || data.message}`,
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
  setWorkstationStatus(elements.quickWorkerDetail, "正在检查任务执行服务", "warning");
  setMessage(elements.quickWorkerMessage, "");
  setWorkstationStatus(elements.aiRuntimeDetail, "正在检查 AI Runtime", "warning");
  setWorkstationStatus(elements.chubServiceDetail, "正在检查控制面状态", "warning");
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
  setWorkstationStatus(
    elements.quickWorkerDetail,
    data.message,
    quickWorkerStatusKind(data.state),
  );
  setWorkstationStatus(
    elements.aiRuntimeDetail,
    aiRuntimeDetail(data),
    aiRuntimeStatusKind(data),
  );
  elements.quickWorkerRestart.textContent = "重启";
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
    setWorkstationStatus(elements.quickWorkerDetail, data.operation.message, "success");
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

function deferQuickWorkerStatusDuringHubRestart() {
  clearQuickWorkerPollTimer();
  setWorkstationStatus(
    elements.quickWorkerDetail,
    "Chub 正在重启，Quick Worker 状态将在控制面恢复后重新确认。",
    "warning",
  );
  setMessage(elements.quickWorkerMessage, "");
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
    if (hubRestartInProgress) {
      deferQuickWorkerStatusDuringHubRestart();
      return;
    }
    if (!background || !quickWorkerState) {
      setWorkstationStatus(
        elements.quickWorkerDetail,
        "无法读取最新任务执行服务状态，保留上次结果。",
        "failed",
      );
    }
    setMessage(
      elements.quickWorkerMessage,
      quickWorkerState
        ? `状态刷新失败，当前展示上次检测结果：${error.message || "无法读取最新状态。"}`
        : error.message || "Chub Quick Worker 状态读取失败。",
      "error",
    );
    syncCoreMaintenanceControls();
    scheduleQuickWorkerPoll();
  }
}

async function requestQuickWorkerRestart() {
  quickWorkerRequestInProgress = true;
  setWorkstationStatus(elements.quickWorkerDetail, "正在请求受控重启", "warning");
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
      error.message || "Chub Quick Worker 重启请求失败。",
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
  const taskCountKnown = ["ready", "busy", "draining", "incompatible"].includes(
    quickWorkerState?.state,
  );
  const sessionCount = Number(systemUpgradeState?.plan?.session_count || 0);
  return [
    {
      label: "快速任务",
      value: !taskCountKnown
        ? "任务数量暂无法确认；恢复流程会按固定边界清理。"
        : taskCount
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
      value: "Chub Quick Worker 与 Chub 将依次重启。",
    },
  ];
}

async function refreshCoreCapabilities() {
  if (!hasProtectedAccess()) {
    return;
  }
  elements.refreshCoreCapabilities.disabled = true;
  try {
    await Promise.allSettled([
      loadStatus(),
      loadQuickWorkerStatus(),
      loadSystemUpgradeStatus(),
      loadAutomationEnvironment(),
    ]);
  } finally {
    elements.refreshCoreCapabilities.disabled = false;
  }
}

async function refreshThirdPartyServices() {
  if (!hasProtectedAccess()) {
    return;
  }
  elements.refreshThirdPartyServices.disabled = true;
  try {
    await loadOpenClaw();
  } finally {
    elements.refreshThirdPartyServices.disabled = false;
  }
}

elements.refreshCoreCapabilities.addEventListener(
  "click",
  refreshCoreCapabilities,
);
elements.refreshThirdPartyServices.addEventListener(
  "click",
  refreshThirdPartyServices,
);
elements.quickWorkerRestart.addEventListener("click", () => {
  void showConfirmationDialog({
    title: "重启 Chub Quick Worker",
    description: "排队任务会被取消，执行中的任务会停止并标记为未完成，任务不会自动重试。Chub、OpenClaw Gateway 和实时终端不受影响。",
    confirmLabel: "确认重启",
    pendingLabel: "正在下发…",
    errorMessage: "Chub Quick Worker 重启失败。",
    onConfirm: requestQuickWorkerRestart,
  });
});

elements.systemUpgradeStart.addEventListener("click", () => {
  const recoveryOnly = systemUpgradeState?.plan?.plan_id === "runtime-recovery";
  const resume = systemUpgradeState?.resume === true;
  void showConfirmationDialog({
    title: "升级与恢复",
    description: resume
      ? "将继续上次未完成的恢复操作，按已保存的安全检查点恢复 Chub AI 运行态、Chub Web 与 Quick Worker。"
      : recoveryOnly
      ? "准备中的升级方案不可用，本次只重建当前版本的 Chub AI 运行态、Chub Web 与 Quick Worker，不升级代码版本。"
      : "本次将重建 Chub AI 运行态、Chub Web 与 Quick Worker。",
    details: systemUpgradeImpactDetails(),
    confirmLabel: resume ? "继续恢复" : "确认升级与恢复",
    pendingLabel: resume ? "正在继续…" : "正在开始…",
    errorMessage: resume ? "恢复操作未能继续。" : "升级与恢复未能启动。",
    onConfirm: startSystemUpgrade,
  });
});
