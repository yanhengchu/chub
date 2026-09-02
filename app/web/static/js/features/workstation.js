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
const SYSTEM_UPGRADE_COMPONENT_LABELS = Object.freeze({
  chub_web: "Chub",
  python_dependencies: "Python 依赖",
  service_definitions: "服务定义",
  quick_worker: "Chub Quick Worker",
});
const SYSTEM_UPGRADE_COMPONENT_STATUS = Object.freeze({
  pending: "等待确认",
  succeeded: "已确认",
  degraded: "已降级",
  failed: "失败",
});
const SYSTEM_UPGRADE_OPERATION_STATUS = Object.freeze({
  requested: "已受理",
  started: "执行中",
  succeeded: "已完成",
  failed: "失败",
});
const SYSTEM_UPGRADE_TIMELINE = Object.freeze([
  { key: "requested", label: "请求已记录" },
  { key: "started", label: "执行器已启动" },
  { key: "draining", label: "停止任务与写入" },
  { key: "cleaning", label: "清理 Chub 运行态" },
  { key: "restarting", label: "重建并恢复服务" },
  { key: "verifying", label: "确认最终健康状态" },
]);
const SYSTEM_UPGRADE_STAGE_INDEX = Object.freeze({
  waiting_for_writes: 1,
  draining_worker: 2,
  freezing_sessions: 2,
  archiving_sessions: 3,
  cleaning_state: 3,
  launching_services: 4,
  restarting_services: 4,
  verifying_new_instance: 5,
  completed: 6,
});

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
  elements.stopHub.disabled = (
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
  elements.quickWorkerStart.disabled = (
    !connected
    || quickWorkerRequestInProgress
    || systemUpgradeIsRunning()
    || !quickWorkerState?.can_start
  );
  elements.quickWorkerStop.disabled = (
    !connected
    || quickWorkerRequestInProgress
    || systemUpgradeIsRunning()
    || !quickWorkerState?.can_stop
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

function renderSystemUpgradeComponents(data) {
  const components = data.operation?.components;
  if (!Array.isArray(components) || components.length === 0) {
    elements.systemUpgradeComponents.replaceChildren();
    return;
  }
  const fragment = document.createDocumentFragment();
  components.forEach((item) => {
    const label = SYSTEM_UPGRADE_COMPONENT_LABELS[item.component] || item.component;
    const status = SYSTEM_UPGRADE_COMPONENT_STATUS[item.status] || "状态未知";
    const chip = document.createElement("span");
    chip.className = `maintenance-component maintenance-component-${item.status}`;
    chip.textContent = `${label}：${status}`;
    fragment.append(chip);
  });
  elements.systemUpgradeComponents.replaceChildren(fragment);
}

function systemUpgradeHasDegradedComponent(data) {
  return Array.isArray(data.operation?.components)
    && data.operation.components.some((item) => item.status === "degraded");
}

function systemUpgradeCompletionPresentation(data) {
  return systemUpgradeHasDegradedComponent(data)
    ? "已完成（有独立组件降级）"
    : "已完成";
}

function systemUpgradePresentation(data) {
  if (data.state === "succeeded" && systemUpgradeHasDegradedComponent(data)) {
    return "已完成（有独立组件降级）";
  }
  return {
    available: "可执行",
    preparing: "正在准备",
    draining: "正在停止任务",
    cleaning: "正在清理",
    restarting: "正在恢复",
    succeeded: "已完成",
    failed: "恢复失败",
    blocked: "升级功能未就绪",
  }[data.state] || "状态未知";
}

function systemUpgradeUpdatedAt(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
}

function systemUpgradeTimelineIndex(operation) {
  if (operation.status === "requested") {
    return 0;
  }
  if (operation.status === "succeeded") {
    return SYSTEM_UPGRADE_TIMELINE.length;
  }
  return SYSTEM_UPGRADE_STAGE_INDEX[operation.failed_stage || operation.stage] ?? 0;
}

function renderSystemUpgradeTimeline(operation) {
  const currentIndex = systemUpgradeTimelineIndex(operation);
  const failed = operation.status === "failed";
  const fragment = document.createDocumentFragment();
  SYSTEM_UPGRADE_TIMELINE.forEach((item, index) => {
    const step = document.createElement("li");
    const marker = document.createElement("span");
    const label = document.createElement("span");
    const isComplete = index < currentIndex;
    const isCurrent = index === currentIndex && currentIndex < SYSTEM_UPGRADE_TIMELINE.length;
    const isFailed = failed && isCurrent;
    step.className = "maintenance-timeline-step";
    if (isComplete) {
      step.classList.add("is-complete");
    } else if (isFailed) {
      step.classList.add("is-failed");
    } else if (isCurrent) {
      step.classList.add("is-current");
    }
    marker.className = "maintenance-timeline-marker";
    marker.setAttribute("aria-hidden", "true");
    label.textContent = isFailed
      ? `${item.label}：失败`
      : item.label;
    step.append(marker, label);
    fragment.append(step);
  });
  elements.systemUpgradeFlow.replaceChildren(fragment);
  elements.systemUpgradeFlow.hidden = false;
}

function renderSystemUpgradeOperation(data) {
  const operation = data.operation;
  if (!operation) {
    elements.systemUpgradeOperation.textContent = "最近操作：暂无已记录的升级操作";
    elements.systemUpgradeFlow.replaceChildren();
    elements.systemUpgradeFlow.hidden = true;
    return;
  }
  const active = ["requested", "started"].includes(operation.status);
  const status = SYSTEM_UPGRADE_OPERATION_STATUS[operation.status] || "状态未知";
  const timestamp = systemUpgradeUpdatedAt(operation.updated_at);
  const timeText = timestamp ? ` · ${timestamp}` : "";
  const operationLabel = active ? "当前操作" : "最近操作";
  elements.systemUpgradeOperation.textContent = `${operationLabel}：${status}${timeText} · 操作 ID：${operation.operation_id}`;
  renderSystemUpgradeTimeline(operation);
}

function renderSystemUpgrade(data) {
  systemUpgradeState = data;
  elements.systemUpgradeStart.textContent = "升级与恢复";
  elements.systemUpgradeDetail.textContent = `状态：${systemUpgradePresentation(data)}。${data.message}`;
  renderSystemUpgradeComponents(data);
  renderSystemUpgradeOperation(data);
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
  elements.quickWorkerStart.hidden = true;
  elements.quickWorkerRestart.hidden = true;
  elements.quickWorkerStop.hidden = true;
  setWorkstationStatus(elements.chubServiceDetail, "正在检查控制面状态", "warning");
  setMessage(elements.chubServiceMessage, "");
  clearSystemUpgradePollTimer();
  systemUpgradeState = null;
  systemUpgradeRequestInProgress = false;
  systemUpgradeReloadOperationId = readSystemUpgradeReloadOperationId();
  elements.systemUpgradeDetail.textContent = "状态：正在检查运行态恢复条件";
  elements.systemUpgradeComponents.replaceChildren();
  elements.systemUpgradeOperation.textContent = "";
  elements.systemUpgradeFlow.replaceChildren();
  elements.systemUpgradeFlow.hidden = true;
  syncCoreMaintenanceControls();
}

function renderQuickWorker(data) {
  quickWorkerState = data;
  setWorkstationStatus(
    elements.quickWorkerDetail,
    data.message,
    quickWorkerStatusKind(data.state),
  );
  const stopped = data.state === "stopped";
  elements.quickWorkerStart.hidden = !stopped;
  elements.quickWorkerRestart.hidden = stopped || !data.can_restart;
  elements.quickWorkerStop.hidden = stopped || !data.can_stop;
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

async function requestQuickWorkerServiceAction(action) {
  quickWorkerRequestInProgress = true;
  setWorkstationStatus(
    elements.quickWorkerDetail,
    action === "start" ? "正在启动 Quick Worker" : "正在停止 Quick Worker",
    "warning",
  );
  setMessage(elements.quickWorkerMessage, "");
  syncCoreMaintenanceControls();
  try {
    const data = await apiFetch(`/api/maintenance/quick-worker/${action}`, {
      method: "POST",
    });
    renderQuickWorker(data);
  } catch (error) {
    await loadQuickWorkerStatus();
    setMessage(
      elements.quickWorkerMessage,
      error.message || `Quick Worker ${action === "start" ? "启动" : "停止"}失败。`,
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
elements.quickWorkerStart.addEventListener("click", () => {
  void requestQuickWorkerServiceAction("start");
});
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
elements.quickWorkerStop.addEventListener("click", () => {
  void showConfirmationDialog({
    title: "停止 Chub Quick Worker",
    description: "停止前必须没有排队或执行中的快速任务；停止后页面和微信快速任务将暂时无法提交，实时终端不受影响。",
    confirmLabel: "确认停止",
    pendingLabel: "正在停止…",
    errorMessage: "Chub Quick Worker 停止失败。",
    onConfirm: () => requestQuickWorkerServiceAction("stop"),
  });
});

elements.systemUpgradeStart.addEventListener("click", () => {
  const recoveryOnly = systemUpgradeState?.plan?.plan_id === "runtime-recovery";
  void showConfirmationDialog({
    title: "升级与恢复",
    description: recoveryOnly
      ? "准备中的升级方案不可用，本次只重建当前版本运行态，不升级代码版本。"
      : "以下运行态将按当前状态重建。",
    details: systemUpgradeImpactDetails(),
    confirmLabel: "确认升级与恢复",
    pendingLabel: "正在开始…",
    errorMessage: "升级与恢复未能启动。",
    onConfirm: startSystemUpgrade,
  });
});
