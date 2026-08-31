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
let openclawOperationVersion = 0;
let openclawNodeId = "";
let openclawWeixinState = null;

const OPENCLAW_WEIXIN_ACTIVE_STATES = new Set([
  "starting",
  "waiting_scan",
  "needs_verification",
  "confirming",
  "cancelling",
]);

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

function openclawChannelPresentation(data) {
  const state = data?.channel_state;
  if (!state) {
    return ["状态未知", "muted"];
  }
  if (state === "running") {
    if (data.owner_state === "configured") {
      return ["运行正常", "success"];
    }
    if (data.owner_state === "not_configured") {
      return ["需要配置", "timeout"];
    }
    if (data.owner_state === "unavailable") {
      return ["不可检查", "muted"];
    }
    return ["状态未知", "muted"];
  }
  if (state === "degraded") {
    return ["部分异常", "timeout"];
  }
  if (state === "stopped") {
    return ["通道异常", "failed"];
  }
  if (state === "not_configured") {
    return ["未配置", "muted"];
  }
  if (state === "unavailable") {
    return ["不可检查", "muted"];
  }
  return ["检查失败", "failed"];
}

function openclawOverallMessage(data) {
  if (data.state !== "running") {
    return "";
  }
  if (data.channel_state && data.channel_state !== "running") {
    return data.channel_message || data.message || "";
  }
  if (data.channel_count > 0 && data.owner_state !== "configured") {
    return data.owner_message || data.message || "";
  }
  return "";
}

function clawbotPresentation(data) {
  if (data.state !== "running") {
    return openclawStatePresentation(data.state);
  }
  return openclawChannelPresentation(data);
}

function openclawDetailKind(data) {
  const [, kind] = clawbotPresentation(data);
  return kind === "success" ? "muted" : kind === "timeout" ? "warning" : kind;
}

function clawbotDetail(data) {
  if (data.state !== "running") {
    return data.message || "无法确认 OpenClaw Gateway 状态。";
  }
  const [gatewayText] = openclawStatePresentation(data.state);
  const [channelText] = openclawChannelPresentation(data);
  const conciseGatewayText = gatewayText === "运行正常" ? "正常" : gatewayText;
  const conciseChannelText = channelText === "运行正常" ? "正常" : channelText;
  const details = [`网关${conciseGatewayText}`, `消息通道${conciseChannelText}`];
  if (data.channel_state === "running") {
    const [ownerText] = data.owner_state === "configured"
      ? ["Owner 已配置"]
      : data.owner_state === "not_configured"
        ? ["Owner 未配置"]
        : data.owner_state === "unavailable"
          ? ["Owner 不可检查"]
          : ["Owner 状态未知"];
    details.push(ownerText);
  }
  return details.join(" · ");
}

function renderOpenClaw(data, { cache = true } = {}) {
  openclawState = data;
  elements.openclawRestart.textContent = "重启";
  setWorkstationStatus(
    elements.clawbotDetail,
    clawbotDetail(data),
    openclawDetailKind(data),
  );
  elements.clawbotDetail.title = [data.channel_message, data.owner_message]
    .filter(Boolean)
    .join("；");
  setMessage(elements.openclawMessage, openclawOverallMessage(data));

  const canStart = data.state === "stopped";
  const isRunning = ["running", "degraded"].includes(data.state);
  const weixinLoginActive = OPENCLAW_WEIXIN_ACTIVE_STATES.has(openclawWeixinState?.state);
  elements.openclawStart.hidden = !canStart;
  elements.openclawRestart.hidden = !isRunning;
  elements.openclawStop.hidden = !isRunning;
  elements.openclawStart.disabled = openclawBusy || weixinLoginActive || !canStart;
  elements.openclawRestart.disabled = openclawBusy || weixinLoginActive || !isRunning;
  elements.openclawStop.disabled = openclawBusy || weixinLoginActive || !isRunning;
  if (cache) {
    cacheOpenClawStatus(data);
  }
}

function resetOpenClawView() {
  openclawState = null;
  openclawAction = "";
  openclawBusy = false;
  openclawOperationVersion += 1;
  openclawWeixinState = null;
  setWorkstationStatus(
    elements.clawbotDetail,
    "正在检查 Gateway、消息通道与 Owner 配置",
    "warning",
  );
  elements.clawbotDetail.removeAttribute("title");
  elements.openclawStart.hidden = true;
  elements.openclawRestart.hidden = true;
  elements.openclawStop.hidden = true;
  setMessage(elements.openclawMessage, "");
  closeOpenClawWeixinDialog();
}

async function loadOpenClaw() {
  if (openclawBusy) {
    return;
  }
  const requestVersion = accessVersion;
  const operationVersion = openclawOperationVersion;
  try {
    const data = await apiFetch("/api/openclaw/status");
    if (
      requestVersion !== accessVersion
      || operationVersion !== openclawOperationVersion
      || openclawBusy
    ) {
      return;
    }
    renderOpenClaw(data);
    await loadOpenClawWeixinStatus({ requestVersion, operationVersion });
  } catch (error) {
    if (
      requestVersion !== accessVersion
      || operationVersion !== openclawOperationVersion
      || openclawBusy
      || handleAccessError(error)
    ) {
      return;
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
    // The workstation-level refresh button owns only its fan-out busy state.
  }
}

async function loadOpenClawWeixinStatus({
  requestVersion = accessVersion,
  operationVersion = openclawOperationVersion,
} = {}) {
  try {
    const weixinLogin = await apiFetch("/api/openclaw/weixin/login");
    if (
      requestVersion !== accessVersion
      || operationVersion !== openclawOperationVersion
      || openclawBusy
    ) {
      return;
    }
    openclawWeixinState = weixinLogin;
    if (openclawState) {
      renderOpenClaw(openclawState, { cache: false });
    }
  } catch (error) {
    if (
      requestVersion !== accessVersion
      || operationVersion !== openclawOperationVersion
      || openclawBusy
      || handleAccessError(error)
    ) {
      return;
    }
    openclawWeixinState = null;
    if (openclawState) {
      renderOpenClaw(openclawState, { cache: false });
    }
  }
}

function setOpenClawBusy(action) {
  openclawBusy = true;
  openclawOperationVersion += 1;
  setWorkstationStatus(
    elements.clawbotDetail,
    action === "restart"
    ? "正在执行重启与恢复"
    : action === "stop"
      ? "正在停止 OpenClaw Gateway"
      : "正在启动 OpenClaw Gateway",
    "warning",
  );
  elements.openclawStart.disabled = true;
  elements.openclawRestart.disabled = true;
  elements.openclawStop.disabled = true;
  setMessage(elements.openclawMessage, "");
}

async function controlOpenClaw(action) {
  setOpenClawBusy(action);
  try {
    const data = await apiFetch(`/api/openclaw/${action}`, { method: "POST" });
    openclawBusy = false;
    renderOpenClaw(data);
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
    ? "会短暂中断消息通道和 Agent 任务；发现插件或补丁版本不一致时，将先同步固定版本再重启，确定继续吗？"
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
