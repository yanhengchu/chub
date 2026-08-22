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
let openclawWeixinPollTimer = 0;
let openclawWeixinPollFailures = 0;
let openclawWeixinQrObjectUrl = "";
let openclawWeixinQrUpdatedAt = "";
let openclawWeixinStatusAvailable = false;

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

function clawbotDetail(data) {
  const [gatewayText] = openclawStatePresentation(data.state);
  const [channelText] = openclawChannelPresentation(data);
  const conciseGatewayText = gatewayText === "运行正常" ? "正常" : gatewayText;
  const conciseChannelText = channelText === "运行正常" ? "正常" : channelText;
  return `网关${conciseGatewayText} · 消息通道${conciseChannelText}`;
}

function renderOpenClawWeixinContext(data) {
  elements.openclawWeixinAccountSummary.textContent = data?.channel_message
    || "当前消息通道状态不可用。";
  elements.openclawWeixinOwnerSummary.textContent = data?.owner_message
    || "当前 Owner 授权状态不可用。";
}

function renderOpenClaw(data, { cache = true } = {}) {
  openclawState = data;
  elements.openclawRestart.textContent = "重启与恢复";
  const [badgeText, badgeKind] = clawbotPresentation(data);
  setBadge(elements.clawbotBadge, badgeText, badgeKind);
  elements.clawbotDetail.textContent = clawbotDetail(data);
  elements.clawbotDetail.title = [data.channel_message, data.owner_message]
    .filter(Boolean)
    .join("；");
  setMessage(elements.openclawMessage, openclawOverallMessage(data));
  renderOpenClawWeixinContext(data);

  const canStart = data.state === "stopped";
  const canRecover = data.installed && data.state !== "service_missing";
  const weixinLoginActive = OPENCLAW_WEIXIN_ACTIVE_STATES.has(openclawWeixinState?.state);
  elements.openclawBindWeixin.hidden = !data.installed || !data.configured;
  elements.openclawStart.hidden = !canStart;
  elements.openclawRestart.hidden = !canRecover;
  elements.openclawBindWeixin.disabled = (
    openclawBusy
    || weixinLoginActive
    || !openclawWeixinStatusAvailable
  );
  elements.openclawStart.disabled = openclawBusy || weixinLoginActive || !canStart;
  elements.openclawRestart.disabled = openclawBusy || weixinLoginActive || !canRecover;
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
  openclawWeixinStatusAvailable = false;
  openclawWeixinPollFailures = 0;
  setBadge(elements.clawbotBadge, "正在检查");
  elements.clawbotDetail.textContent = "交互面：正在检查微信消息通道";
  elements.clawbotDetail.removeAttribute("title");
  renderOpenClawWeixinContext(null);
  elements.openclawBindWeixin.hidden = true;
  elements.openclawStart.hidden = true;
  elements.openclawRestart.hidden = true;
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
    elements.openclawBindWeixin.disabled = true;
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
    openclawWeixinStatusAvailable = true;
    elements.openclawBindWeixin.removeAttribute("title");
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
    openclawWeixinStatusAvailable = false;
    elements.openclawBindWeixin.disabled = true;
    elements.openclawBindWeixin.title = "微信绑定状态暂时无法读取";
  }
}

function setOpenClawBusy(action) {
  openclawBusy = true;
  openclawOperationVersion += 1;
  const busyPresentation = {
    start: ["正在启动", "muted"],
    stop: ["正在停止", "timeout"],
    restart: ["正在重启", "muted"],
  }[action];
  setBadge(
    elements.clawbotBadge,
    busyPresentation[0],
    busyPresentation[1],
  );
  elements.clawbotDetail.textContent = action === "restart"
    ? "正在执行重启与恢复"
    : "正在启动微信消息通道";
  elements.openclawBindWeixin.disabled = true;
  elements.openclawStart.disabled = true;
  elements.openclawRestart.disabled = true;
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
    ? "确认重启与恢复 OpenClaw"
    : "确认停止 OpenClaw";
  elements.openclawDialogMessage.textContent = restarting
    ? "会短暂中断消息通道和 Agent 任务；发现插件或补丁版本不一致时，将先同步固定版本再重启，确定继续吗？"
    : "停止会中断当前频道连接和 Agent 任务，确定继续吗？";
  elements.openclawDialogConfirm.textContent = restarting ? "确认重启与恢复" : "确认停止";
  elements.openclawDialogConfirm.className = restarting
    ? "button-secondary"
    : "button-danger";
  elements.openclawDialog.showModal();
}

function closeOpenClawDialog() {
  openclawAction = "";
  elements.openclawDialog.close();
}

function releaseOpenClawWeixinQr() {
  if (openclawWeixinQrObjectUrl) {
    URL.revokeObjectURL(openclawWeixinQrObjectUrl);
    openclawWeixinQrObjectUrl = "";
  }
  openclawWeixinQrUpdatedAt = "";
  elements.openclawWeixinQr.removeAttribute("src");
  elements.openclawWeixinQrPanel.hidden = true;
}

function stopOpenClawWeixinPolling() {
  if (openclawWeixinPollTimer) {
    clearTimeout(openclawWeixinPollTimer);
    openclawWeixinPollTimer = 0;
  }
}

function closeOpenClawWeixinDialog() {
  stopOpenClawWeixinPolling();
  releaseOpenClawWeixinQr();
  elements.openclawWeixinVerifyForm.hidden = true;
  elements.openclawWeixinVerifyCode.value = "";
  if (elements.openclawWeixinDialog.open) {
    elements.openclawWeixinDialog.close();
  }
}

async function loadOpenClawWeixinQr(updatedAt) {
  if (
    openclawWeixinQrObjectUrl
    && openclawWeixinQrUpdatedAt === updatedAt
  ) {
    return;
  }
  const requestVersion = accessVersion;
  try {
    const response = await fetch("/api/openclaw/weixin/login/qr", {
      headers: authorizationHeaders(),
      cache: "no-store",
    });
    if (response.status === 401) {
      throw { code: "invalid_credentials", message: "Token 无效或已变更。" };
    }
    if (!response.ok) {
      throw { code: "weixin_qr_unavailable", message: "微信绑定二维码读取失败。" };
    }
    const blob = await response.blob();
    if (requestVersion !== accessVersion || !elements.openclawWeixinDialog.open) {
      return;
    }
    releaseOpenClawWeixinQr();
    openclawWeixinQrObjectUrl = URL.createObjectURL(blob);
    openclawWeixinQrUpdatedAt = updatedAt;
    elements.openclawWeixinQr.src = openclawWeixinQrObjectUrl;
    elements.openclawWeixinQrPanel.hidden = false;
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(
        elements.openclawWeixinMessage,
        error.message || "微信绑定二维码读取失败。",
        "error",
      );
    }
  }
}

function renderOpenClawWeixinLogin(data) {
  const previousState = openclawWeixinState?.state;
  openclawWeixinState = data;
  openclawWeixinStatusAvailable = true;
  const active = OPENCLAW_WEIXIN_ACTIVE_STATES.has(data.state);
  const needsVerification = data.state === "needs_verification";
  elements.openclawWeixinCancel.hidden = !active || data.state === "cancelling";
  elements.openclawWeixinStart.hidden = active;
  elements.openclawWeixinStart.textContent = data.state === "idle"
    ? "生成二维码"
    : "重新生成";
  elements.openclawWeixinVerifyForm.hidden = !needsVerification;
  setMessage(
    elements.openclawWeixinMessage,
    data.message,
    data.state === "succeeded"
      ? "success"
      : data.state === "failed"
        ? "error"
        : "",
  );
  if (data.qr_available) {
    loadOpenClawWeixinQr(data.updated_at);
  } else {
    releaseOpenClawWeixinQr();
  }
  if (openclawState) {
    renderOpenClaw(openclawState, { cache: false });
  }
  if (data.state === "succeeded" && previousState !== "succeeded") {
    loadOpenClaw();
  }
}

async function pollOpenClawWeixinLogin() {
  stopOpenClawWeixinPolling();
  if (!elements.openclawWeixinDialog.open) {
    return;
  }
  try {
    const data = await apiFetch("/api/openclaw/weixin/login");
    openclawWeixinPollFailures = 0;
    renderOpenClawWeixinLogin(data);
    if (OPENCLAW_WEIXIN_ACTIVE_STATES.has(data.state)) {
      openclawWeixinPollTimer = setTimeout(pollOpenClawWeixinLogin, 1000);
    }
  } catch (error) {
    if (!handleAccessError(error)) {
      openclawWeixinPollFailures += 1;
      setMessage(
        elements.openclawWeixinMessage,
        error.message || "微信绑定状态读取失败。",
        "error",
      );
      if (
        elements.openclawWeixinDialog.open
      ) {
        const retryDelay = Math.min(
          5000,
          1000 * (2 ** Math.min(openclawWeixinPollFailures - 1, 3)),
        );
        openclawWeixinPollTimer = setTimeout(
          pollOpenClawWeixinLogin,
          retryDelay,
        );
      }
    }
  }
}

async function openOpenClawWeixinDialog() {
  elements.openclawWeixinDialog.showModal();
  setMessage(elements.openclawWeixinMessage, "正在读取微信绑定状态…");
  openclawWeixinPollFailures = 0;
  await pollOpenClawWeixinLogin();
}

async function startOpenClawWeixinLogin() {
  elements.openclawWeixinStart.disabled = true;
  setMessage(elements.openclawWeixinMessage, "正在生成微信绑定二维码…");
  try {
    const data = await apiFetch("/api/openclaw/weixin/login", { method: "POST" });
    renderOpenClawWeixinLogin(data);
    openclawWeixinPollTimer = setTimeout(pollOpenClawWeixinLogin, 500);
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(
        elements.openclawWeixinMessage,
        error.message || "微信绑定启动失败。",
        "error",
      );
    }
  } finally {
    elements.openclawWeixinStart.disabled = false;
  }
}

async function cancelOpenClawWeixinLogin() {
  elements.openclawWeixinCancel.disabled = true;
  try {
    const data = await apiFetch("/api/openclaw/weixin/login", { method: "DELETE" });
    renderOpenClawWeixinLogin(data);
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(
        elements.openclawWeixinMessage,
        error.message || "微信绑定取消失败。",
        "error",
      );
    }
  } finally {
    elements.openclawWeixinCancel.disabled = false;
  }
}

elements.openclawBindWeixin.addEventListener("click", openOpenClawWeixinDialog);
elements.openclawStart.addEventListener("click", () => requestOpenClawAction("start"));
elements.openclawRestart.addEventListener("click", () => requestOpenClawAction("restart"));
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
elements.openclawWeixinClose.addEventListener("click", closeOpenClawWeixinDialog);
elements.openclawWeixinStart.addEventListener("click", startOpenClawWeixinLogin);
elements.openclawWeixinCancel.addEventListener("click", cancelOpenClawWeixinLogin);
elements.openclawWeixinVerifyForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const code = elements.openclawWeixinVerifyCode.value.trim();
  if (!code) {
    return;
  }
  try {
    const data = await apiFetch("/api/openclaw/weixin/login/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });
    elements.openclawWeixinVerifyCode.value = "";
    renderOpenClawWeixinLogin(data);
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(
        elements.openclawWeixinMessage,
        error.message || "验证码提交失败。",
        "error",
      );
    }
  }
});
elements.openclawWeixinDialog.addEventListener("click", (event) => {
  if (event.target === elements.openclawWeixinDialog) {
    closeOpenClawWeixinDialog();
  }
});
elements.openclawWeixinDialog.addEventListener("close", () => {
  stopOpenClawWeixinPolling();
  releaseOpenClawWeixinQr();
});
