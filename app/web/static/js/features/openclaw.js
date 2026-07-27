"use strict";

const OPENCLAW_STATUS_CACHE_KEY = "hub.openclawStatusCache.v1";
const OPENCLAW_STATES = new Set([
  "unavailable",
  "unconfigured",
  "service_missing",
  "stopped",
  "running",
  "degraded",
  "unknown",
]);
let openclawState = null;
let openclawAction = "";
let openclawBusy = false;
let openclawNodeId = "";

function cacheOpenClawStatus(data) {
  if (!openclawNodeId || !OPENCLAW_STATES.has(data?.state)) {
    return;
  }
  try {
    sessionStorage.setItem(
      OPENCLAW_STATUS_CACHE_KEY,
      JSON.stringify({ node_id: openclawNodeId, status: data }),
    );
  } catch (_error) {
    // Storage can be unavailable; the current page still keeps in-memory state.
  }
}

function restoreOpenClawCache(nodeId) {
  openclawNodeId = nodeId || "";
  if (!openclawNodeId) {
    return false;
  }
  try {
    const cached = JSON.parse(
      sessionStorage.getItem(OPENCLAW_STATUS_CACHE_KEY) || "null",
    );
    if (
      cached?.node_id !== openclawNodeId
      || !cached.status
      || !OPENCLAW_STATES.has(cached.status.state)
    ) {
      return false;
    }
    renderOpenClaw(cached.status, { cache: false });
    return true;
  } catch (_error) {
    return false;
  }
}

function clearOpenClawCache() {
  openclawNodeId = "";
  try {
    sessionStorage.removeItem(OPENCLAW_STATUS_CACHE_KEY);
  } catch (_error) {
    // Storage can be unavailable; resetOpenClawView still clears page state.
  }
}

function openclawTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleString("zh-CN", { hour12: false });
}

function openclawStatePresentation(state) {
  return {
    unavailable: ["未安装", "muted"],
    unconfigured: ["未初始化", "timeout"],
    service_missing: ["服务未安装", "timeout"],
    stopped: ["已停止", "timeout"],
    running: ["运行正常", "success"],
    degraded: ["尚未就绪", "failed"],
    unknown: ["状态未知", "failed"],
  }[state] || ["状态未知", "failed"];
}

function renderOpenClaw(data, { cache = true } = {}) {
  openclawState = data;
  const [badgeText, badgeKind] = openclawStatePresentation(data.state);
  setBadge(elements.openclawBadge, badgeText, badgeKind);
  elements.openclawVersion.textContent = data.version || "—";
  elements.openclawService.textContent = data.service_manager
    ? `${data.service_manager} · ${data.service_loaded ? "已加载" : "未加载"}`
    : "—";
  elements.openclawBind.textContent = data.bind_mode
    ? `${data.bind_mode}${data.port ? ` · ${data.port}` : ""}`
    : "—";
  elements.openclawCheckedAt.textContent = openclawTime(data.checked_at);
  const accessUrl = data.access_url || "";
  elements.openclawAccessUrl.textContent = accessUrl || "—";
  elements.openclawAccessOpen.hidden = !accessUrl;
  elements.openclawAccessUnavailable.hidden = Boolean(accessUrl);
  elements.openclawAccessOpen.href = accessUrl || "#";
  setMessage(elements.openclawMessage, data.message || "");

  const canStart = data.state === "stopped";
  const canControlRunning = ["running", "degraded"].includes(data.state);
  elements.openclawStart.hidden = !canStart;
  elements.openclawRestart.hidden = !canControlRunning;
  elements.openclawStop.hidden = !canControlRunning;
  elements.openclawStart.disabled = openclawBusy || !canStart;
  elements.openclawRestart.disabled = openclawBusy || !canControlRunning;
  elements.openclawStop.disabled = openclawBusy || !canControlRunning;
  if (cache) {
    cacheOpenClawStatus(data);
  }
}

function resetOpenClawView() {
  openclawState = null;
  openclawAction = "";
  openclawBusy = false;
  setBadge(elements.openclawBadge, "正在检查");
  elements.openclawVersion.textContent = "—";
  elements.openclawService.textContent = "—";
  elements.openclawBind.textContent = "—";
  elements.openclawCheckedAt.textContent = "—";
  elements.openclawAccessUrl.textContent = "—";
  elements.openclawAccessOpen.hidden = true;
  elements.openclawAccessOpen.href = "#";
  elements.openclawAccessUnavailable.hidden = false;
  elements.openclawStart.hidden = true;
  elements.openclawRestart.hidden = true;
  elements.openclawStop.hidden = true;
  setMessage(elements.openclawMessage, "");
}

async function loadOpenClaw() {
  const requestVersion = accessVersion;
  elements.refreshOpenclaw.disabled = true;
  try {
    const data = await apiFetch("/api/openclaw/status");
    if (requestVersion !== accessVersion) {
      return;
    }
    renderOpenClaw(data);
  } catch (error) {
    if (requestVersion !== accessVersion || handleAccessError(error)) {
      return;
    }
    if (!openclawState) {
      setBadge(elements.openclawBadge, "刷新失败", "failed");
    }
    setMessage(
      elements.openclawMessage,
      openclawState
        ? `状态刷新失败，当前展示上次检测结果：${error.message || "无法读取最新状态。"}`
        : error.message || "OpenClaw 状态读取失败。",
      "error",
    );
    elements.openclawStart.disabled = true;
    elements.openclawRestart.disabled = true;
    elements.openclawStop.disabled = true;
  } finally {
    elements.refreshOpenclaw.disabled = false;
  }
}

function setOpenClawBusy(action) {
  openclawBusy = true;
  elements.refreshOpenclaw.disabled = true;
  elements.openclawStart.disabled = true;
  elements.openclawRestart.disabled = true;
  elements.openclawStop.disabled = true;
  setMessage(
    elements.openclawMessage,
    {
      start: "正在启动 OpenClaw Gateway…",
      stop: "正在停止 OpenClaw Gateway…",
      restart: "正在重启 OpenClaw Gateway…",
    }[action],
  );
}

async function controlOpenClaw(action) {
  setOpenClawBusy(action);
  try {
    const data = await apiFetch(`/api/openclaw/${action}`, { method: "POST" });
    openclawBusy = false;
    renderOpenClaw(data);
    setMessage(
      elements.openclawMessage,
      action === "stop"
        ? "OpenClaw Gateway 已停止。"
        : `OpenClaw Gateway 已${action === "start" ? "启动" : "重启"}并恢复就绪。`,
      "success",
    );
  } catch (error) {
    if (!handleAccessError(error)) {
      openclawBusy = false;
      if (openclawState) {
        renderOpenClaw(openclawState);
      }
      setMessage(
        elements.openclawMessage,
        error.message || "OpenClaw 维护操作失败。",
        "error",
      );
    }
  } finally {
    openclawBusy = false;
    elements.refreshOpenclaw.disabled = false;
  }
}

function requestOpenClawAction(action) {
  if (action === "start") {
    controlOpenClaw(action);
    return;
  }
  openclawAction = action;
  const restarting = action === "restart";
  elements.openclawDialogTitle.textContent = restarting
    ? "确认重启 OpenClaw"
    : "确认停止 OpenClaw";
  elements.openclawDialogMessage.textContent = restarting
    ? "重启会短暂中断当前频道连接和 Agent 任务，确定继续吗？"
    : "停止会中断当前频道连接和 Agent 任务，确定继续吗？";
  elements.openclawDialogConfirm.textContent = restarting ? "确认重启" : "确认停止";
  elements.openclawDialogConfirm.className = restarting
    ? "button-secondary"
    : "button-danger";
  elements.openclawDialog.showModal();
}

function closeOpenClawDialog() {
  openclawAction = "";
  elements.openclawDialog.close();
}

elements.refreshOpenclaw.addEventListener("click", loadOpenClaw);
elements.openclawStart.addEventListener("click", () => requestOpenClawAction("start"));
elements.openclawRestart.addEventListener("click", () => requestOpenClawAction("restart"));
elements.openclawStop.addEventListener("click", () => requestOpenClawAction("stop"));
elements.openclawDialogClose.addEventListener("click", closeOpenClawDialog);
elements.openclawDialogCancel.addEventListener("click", closeOpenClawDialog);
elements.openclawDialogConfirm.addEventListener("click", () => {
  const action = openclawAction;
  openclawAction = "";
  elements.openclawDialog.close();
  if (action) {
    controlOpenClaw(action);
  }
});
elements.openclawDialog.addEventListener("click", (event) => {
  if (event.target === elements.openclawDialog) {
    closeOpenClawDialog();
  }
});
elements.openclawDialog.addEventListener("close", () => {
  openclawAction = "";
});
