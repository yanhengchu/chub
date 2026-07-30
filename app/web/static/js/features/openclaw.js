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

function openclawChannelPresentation(data) {
  const state = data?.channel_state;
  if (!state) {
    return "—";
  }
  if (state === "running") {
    return `${data.channel_running_count}/${data.channel_count} 运行正常`;
  }
  if (state === "degraded") {
    return `${data.channel_running_count}/${data.channel_count} 运行正常`;
  }
  if (state === "stopped") {
    return `${data.channel_count} 个异常`;
  }
  if (state === "not_configured") {
    return "未配置";
  }
  if (state === "unavailable") {
    return "不可检查";
  }
  return "检查失败";
}

function openclawOwnerPresentation(data) {
  if (data?.owner_state === "configured") {
    return `${data.owner_count} 个 Owner`;
  }
  if (data?.owner_state === "not_configured") {
    return "未配置";
  }
  if (data?.owner_state === "unavailable") {
    return "不可检查";
  }
  return "检查失败";
}

function openclawOverallPresentation(data) {
  const gatewayPresentation = openclawStatePresentation(data.state);
  if (data.state !== "running") {
    return gatewayPresentation;
  }
  const channelLimited = Boolean(data.channel_state) && data.channel_state !== "running";
  const ownerLimited = data.channel_count > 0 && data.owner_state !== "configured";
  return channelLimited || ownerLimited
    ? ["功能受限", "timeout"]
    : gatewayPresentation;
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

function renderOpenClaw(data, { cache = true } = {}) {
  openclawState = data;
  const [badgeText, badgeKind] = openclawOverallPresentation(data);
  setBadge(elements.openclawBadge, badgeText, badgeKind);
  elements.openclawVersion.textContent = data.version || "—";
  elements.openclawService.textContent = data.service_manager
    ? `${data.service_manager} · ${data.service_loaded ? "已加载" : "未加载"}`
    : "—";
  elements.openclawChannels.textContent = openclawChannelPresentation(data);
  elements.openclawChannels.title = data.channel_message || "";
  elements.openclawOwner.textContent = openclawOwnerPresentation(data);
  elements.openclawOwner.title = data.owner_message || "";
  elements.openclawBind.textContent = data.bind_mode
    ? `${data.bind_mode}${data.port ? ` · ${data.port}` : ""}`
    : "—";
  elements.openclawCheckedAt.textContent = openclawTime(data.checked_at);
  const accessUrl = data.access_url || "";
  elements.openclawAccessUrl.textContent = accessUrl || "—";
  elements.openclawAccessOpen.hidden = !accessUrl;
  elements.openclawAccessUnavailable.hidden = Boolean(accessUrl);
  elements.openclawAccessOpen.href = accessUrl || "#";
  setMessage(elements.openclawMessage, openclawOverallMessage(data));

  const canStart = data.state === "stopped";
  const canControlRunning = ["running", "degraded"].includes(data.state);
  const weixinLoginActive = OPENCLAW_WEIXIN_ACTIVE_STATES.has(openclawWeixinState?.state);
  elements.openclawBindWeixin.hidden = !data.installed || !data.configured;
  elements.openclawStart.hidden = !canStart;
  elements.openclawRestart.hidden = !canControlRunning;
  elements.openclawStop.hidden = !canControlRunning;
  elements.openclawBindWeixin.disabled = (
    openclawBusy
    || weixinLoginActive
    || !openclawWeixinStatusAvailable
  );
  elements.openclawStart.disabled = openclawBusy || weixinLoginActive || !canStart;
  elements.openclawRestart.disabled = openclawBusy || weixinLoginActive || !canControlRunning;
  elements.openclawStop.disabled = openclawBusy || weixinLoginActive || !canControlRunning;
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
  setBadge(elements.openclawBadge, "正在检查");
  elements.openclawVersion.textContent = "—";
  elements.openclawService.textContent = "—";
  elements.openclawChannels.textContent = "—";
  elements.openclawChannels.removeAttribute("title");
  elements.openclawOwner.textContent = "—";
  elements.openclawOwner.removeAttribute("title");
  elements.openclawBind.textContent = "—";
  elements.openclawCheckedAt.textContent = "—";
  elements.openclawAccessUrl.textContent = "—";
  elements.openclawAccessOpen.hidden = true;
  elements.openclawAccessOpen.href = "#";
  elements.openclawAccessUnavailable.hidden = false;
  elements.openclawBindWeixin.hidden = true;
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
  elements.refreshOpenclaw.disabled = true;
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
    elements.openclawBindWeixin.disabled = true;
  } finally {
    if (!openclawBusy) {
      elements.refreshOpenclaw.disabled = false;
    }
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
    elements.openclawBadge,
    busyPresentation[0],
    busyPresentation[1],
  );
  elements.refreshOpenclaw.disabled = true;
  elements.openclawBindWeixin.disabled = true;
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

elements.refreshOpenclaw.addEventListener("click", loadOpenClaw);
elements.openclawBindWeixin.addEventListener("click", openOpenClawWeixinDialog);
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
