"use strict";

let automationPollTimer = null;
let automationBrowserState = "unavailable";
let automationBrowserProfiles = [];
let feishuQrObjectUrl = "";
let feishuQrLoading = false;
let feishuQrVersion = 0;

function automationStatusText(state) {
  return {
    idle: "尚未执行",
    queued: "等待执行",
    running: "执行中",
    success: "成功",
    failed: "失败",
  }[state] || state;
}

function automationTime(value) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? ""
    : date.toLocaleString("zh-CN", { hour12: false });
}

async function runAutomation(task, button) {
  button.disabled = true;
  button.textContent = "受理中…";
  try {
    await apiFetch(`/api/automations/${encodeURIComponent(task.id)}/run`, {
      method: "POST",
    });
    setMessage(elements.automationMessage, "");
    await loadAutomations();
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(
        elements.automationMessage,
        error.message || "自动化任务启动失败。",
        "error",
      );
    }
  } finally {
    button.textContent = "运行";
  }
}

async function controlAutomationBrowser() {
  const action = automationBrowserState === "running" ? "stop" : "start";
  if (
    action === "stop"
    && !window.confirm("确定停止 Debug Chrome 吗？已打开的调试浏览器页面会关闭。")
  ) {
    return;
  }
  elements.automationBrowserControl.disabled = true;
  elements.automationBrowserProfile.disabled = true;
  elements.automationBrowserMode.disabled = true;
  const selectedProfile = automationBrowserProfiles.find(
    (profile) => profile.id === elements.automationBrowserProfile.value,
  );
  if (action === "start" && !selectedProfile) {
    setMessage(elements.automationMessage, "请选择浏览器用户。", "error");
    await loadAutomations();
    return;
  }
  const requiresInitialization = action === "start" && !selectedProfile.initialized;
  if (
    requiresInitialization
    && !window.confirm(
      "首次使用需要复制该浏览器用户。请先完全退出默认 Chrome；复制完成后将自动启动 Debug Chrome。确定继续吗？",
    )
  ) {
    await loadAutomations();
    return;
  }
  elements.automationBrowserControl.textContent = requiresInitialization
    ? "初始化中…"
    : action === "start" ? "启动中…" : "停止中…";
  try {
    const options = { method: "POST" };
    if (action === "start") {
      options.headers = { "Content-Type": "application/json" };
      options.body = JSON.stringify({
        mode: elements.automationBrowserMode.value,
        profile_id: selectedProfile.id,
      });
    }
    const endpoint = requiresInitialization
      ? "/api/automations/browser/initialize"
      : `/api/automations/browser/${action}`;
    await apiFetch(endpoint, options);
    setMessage(elements.automationMessage, "");
    await loadAutomations();
  } catch (error) {
    if (!handleAccessError(error)) {
      await loadAutomations();
      setMessage(
        elements.automationMessage,
        error.message || "Debug Chrome 操作失败。",
        "error",
      );
    }
  }
}

async function checkFeishuEnvironment() {
  releaseFeishuQr();
  elements.automationFeishuCheck.disabled = true;
  elements.automationFeishuCheck.textContent = "检查中…";
  setBadge(elements.automationFeishuBadge, "检查中", "muted");
  try {
    await apiFetch("/api/automations/environment/feishu/check", { method: "POST" });
    setMessage(elements.automationMessage, "");
    await loadAutomations();
  } catch (error) {
    if (!handleAccessError(error)) {
      await loadAutomations();
      setMessage(
        elements.automationMessage,
        error.message || "飞书环境检查失败。",
        "error",
      );
    }
  }
}

function releaseFeishuQr() {
  feishuQrVersion += 1;
  if (feishuQrObjectUrl) {
    URL.revokeObjectURL(feishuQrObjectUrl);
    feishuQrObjectUrl = "";
  }
  feishuQrLoading = false;
  elements.automationFeishuQr.removeAttribute("src");
  elements.automationFeishuLogin.hidden = true;
}

async function loadFeishuQr() {
  if (feishuQrLoading || feishuQrObjectUrl || !hasProtectedAccess()) {
    return;
  }
  const requestVersion = accessVersion;
  const qrVersion = feishuQrVersion;
  feishuQrLoading = true;
  try {
    const response = await fetch("/api/automations/environment/feishu/qr", {
      headers: authorizationHeaders(),
      cache: "no-store",
    });
    if (response.status === 401) {
      throw { code: "invalid_credentials", message: "Token 无效或已变更。" };
    }
    if (!response.ok) {
      throw { code: "feishu_qr_unavailable", message: "飞书登录二维码读取失败。" };
    }
    const blob = await response.blob();
    if (requestVersion !== accessVersion || qrVersion !== feishuQrVersion) {
      return;
    }
    feishuQrObjectUrl = URL.createObjectURL(blob);
    elements.automationFeishuQr.src = feishuQrObjectUrl;
  } catch (error) {
    if (requestVersion !== accessVersion || handleAccessError(error)) {
      return;
    }
    setMessage(
      elements.automationMessage,
      error.message || "飞书登录二维码读取失败。",
      "error",
    );
  } finally {
    feishuQrLoading = false;
  }
}

function renderAutomations(data) {
  elements.automationList.replaceChildren();
  const browserRunning = data.browser_state === "running";
  automationBrowserProfiles = data.browser_profiles || [];
  const previousProfile = elements.automationBrowserProfile.value;
  elements.automationBrowserProfile.replaceChildren();
  automationBrowserProfiles.forEach((profile) => {
    const option = document.createElement("option");
    option.value = profile.id;
    const availability = profile.initialized
      ? profile.source_available ? "" : " · 源用户已不存在"
      : " · 未初始化";
    option.textContent = `${profile.name}${availability}`;
    elements.automationBrowserProfile.append(option);
  });
  const preferredProfile = automationBrowserProfiles.find(
    (profile) => profile.id === previousProfile,
  ) || automationBrowserProfiles.find(
    (profile) => profile.id === data.browser_profile_id,
  ) || automationBrowserProfiles.find(
    (profile) => profile.active && profile.initialized,
  ) || automationBrowserProfiles.find(
    (profile) => profile.initialized,
  ) || automationBrowserProfiles[0];
  if (preferredProfile) {
    elements.automationBrowserProfile.value = preferredProfile.id;
  }
  const selectedProfile = automationBrowserProfiles.find(
    (profile) => profile.id === elements.automationBrowserProfile.value,
  );
  const initializing = automationBrowserProfiles.some(
    (profile) => profile.initialization_state === "running",
  );
  const feishuChecking = data.feishu_environment.state === "checking";
  const automationBusy = initializing || feishuChecking || data.tasks.some((task) => ["queued", "running"].includes(task.state.status));
  automationBrowserState = data.browser_state;
  setBadge(
    elements.automationBrowserBadge,
    `${data.browser_message}${data.browser_profile_name ? ` · ${data.browser_profile_name}` : ""}${data.browser_mode ? ` · ${data.browser_mode}` : ""}`,
    browserRunning ? "success" : data.browser_state === "stopped" ? "timeout" : "failed",
  );
  elements.automationBrowserControl.disabled = (
    !["running", "stopped"].includes(data.browser_state)
    || (browserRunning && automationBusy)
    || (!browserRunning && (!selectedProfile || !selectedProfile.source_available && !selectedProfile.initialized))
    || initializing
  );
  elements.automationBrowserProfile.hidden = data.browser_state !== "stopped";
  elements.automationBrowserProfile.disabled = data.browser_state !== "stopped" || initializing;
  elements.automationBrowserMode.hidden = data.browser_state !== "stopped";
  elements.automationBrowserMode.disabled = data.browser_state !== "stopped" || initializing;
  elements.automationBrowserControl.textContent = browserRunning
    ? "停止"
    : initializing
      ? "初始化中…"
      : selectedProfile && !selectedProfile.initialized
        ? "初始化并启动"
        : "启动";
  const failedInitialization = selectedProfile?.initialization_state === "failed"
    ? selectedProfile
    : null;
  if (failedInitialization?.initialization_message) {
    setMessage(elements.automationMessage, failedInitialization.initialization_message, "error");
  } else if (data.browser_profiles_error && !automationBrowserProfiles.length) {
    setMessage(elements.automationMessage, data.browser_profiles_error, "error");
  }
  const feishuTime = automationTime(data.feishu_environment.checked_at);
  const feishuBadgeKind = {
    available: "success",
    login_required: "timeout",
    failed: "failed",
    browser_stopped: "muted",
    checking: "muted",
    unchecked: "muted",
  }[data.feishu_environment.state] || "muted";
  setBadge(
    elements.automationFeishuBadge,
    `${data.feishu_environment.message}${feishuTime ? ` · ${feishuTime}` : ""}`,
    feishuBadgeKind,
  );
  elements.automationFeishuCheck.disabled = !browserRunning || automationBusy;
  elements.automationFeishuCheck.textContent = feishuChecking
    ? "检查中…"
    : data.feishu_environment.state === "unchecked"
      || data.feishu_environment.state === "browser_stopped"
      ? "检查"
      : "重新检查";
  if (
    data.feishu_environment.state === "login_required"
    && data.feishu_environment.qr_available
  ) {
    elements.automationFeishuLogin.hidden = false;
    loadFeishuQr();
  } else {
    releaseFeishuQr();
  }
  elements.automationCount.textContent = `已启用 ${data.enabled_count} 个任务`;

  if (!data.enabled) {
    setMessage(elements.automationMessage, "自动化任务未启用。", "error");
    return false;
  }
  if (!data.tasks.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "暂无自动化任务，请先配置 automations.yaml 或 automations.local.yaml。";
    elements.automationList.append(empty);
    return false;
  }

  let active = false;
  data.tasks.forEach((task) => {
    const item = document.createElement("article");
    const copy = document.createElement("div");
    const name = document.createElement("strong");
    const status = document.createElement("span");
    const reason = document.createElement("span");
    const button = document.createElement("button");
    const busy = ["queued", "running"].includes(task.state.status);
    active = active || busy;
    item.className = "automation-item";
    copy.className = "automation-item-copy";
    name.textContent = task.name;
    const time = automationTime(task.state.finished_at || task.state.started_at);
    status.className = "automation-item-status";
    status.textContent = `${automationStatusText(task.state.status)}${time ? ` · ${time}` : ""}`;
    reason.className = "automation-item-reason";
    reason.textContent = task.state.message || "暂无状态说明";
    button.type = "button";
    button.className = "button-secondary automation-run";
    button.textContent = busy ? "执行中…" : "运行";
    button.disabled = !browserRunning || !task.enabled || busy || feishuChecking;
    button.addEventListener("click", () => runAutomation(task, button));
    copy.append(name, status, reason);
    if (task.state.linked_documents?.length) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `关联文档明细（${task.state.linked_documents.length}）`;
      details.className = "automation-linked-details";
      details.append(summary);
      task.state.linked_documents.forEach((linkedDocument) => {
        const row = document.createElement("span");
        row.textContent = `${linkedDocument.status === "success" ? "成功" : "失败"} · ${linkedDocument.name} · ${linkedDocument.message}`;
        details.append(row);
      });
      copy.append(details);
    }
    item.append(copy, button);
    elements.automationList.append(item);
  });
  return active || initializing;
}

async function loadAutomations() {
  const requestVersion = accessVersion;
  elements.refreshAutomations.disabled = true;
  try {
    const data = await apiFetch("/api/automations");
    if (requestVersion !== accessVersion) {
      return;
    }
    setMessage(elements.automationMessage, "");
    const active = renderAutomations(data);
    if (automationPollTimer) {
      window.clearTimeout(automationPollTimer);
      automationPollTimer = null;
    }
    if (active) {
      automationPollTimer = window.setTimeout(loadAutomations, 1000);
    }
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return;
    }
    if (!handleAccessError(error)) {
      automationBrowserState = "unknown";
      setBadge(elements.automationBrowserBadge, "检查失败", "failed");
      elements.automationBrowserControl.disabled = true;
      elements.automationBrowserProfile.hidden = true;
      elements.automationBrowserProfile.disabled = true;
      elements.automationBrowserMode.hidden = true;
      elements.automationBrowserMode.disabled = true;
      setBadge(elements.automationFeishuBadge, "检查失败", "failed");
      elements.automationFeishuCheck.disabled = true;
      setMessage(elements.automationMessage, error.message || "自动化任务读取失败。", "error");
    }
  } finally {
    elements.refreshAutomations.disabled = false;
  }
}
