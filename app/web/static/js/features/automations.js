"use strict";

let automationPollTimer = null;
let automationEnvironmentPollTimer = null;
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
    waiting: "等待更新",
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

function automationStatusKind(state) {
  return {
    success: "success",
    failed: "failed",
    queued: "muted",
    running: "timeout",
    waiting: "timeout",
    idle: "muted",
  }[state] || "muted";
}

function automationTaskStatusText(task) {
  if (task.reporting_period && task.state.status === "idle") {
    return "待下载";
  }
  return automationStatusText(task.state.status);
}

function weeklyDownloadStatus(task) {
  if (task.state.status === "success") {
    return ["下载成功", "success"];
  }
  const linkedDocuments = task.state.linked_documents || [];
  if (
    task.state.validation_status === "failed"
    && linkedDocuments.length
    && linkedDocuments.every((document) => document.status === "success")
  ) {
    return ["下载成功", "success"];
  }
  if (task.state.status === "failed") {
    return ["下载失败", "failed"];
  }
  if (task.state.status === "waiting") {
    return ["资料待更新", "timeout"];
  }
  if (task.state.status === "running") {
    return ["下载中", "timeout"];
  }
  if (task.state.status === "queued") {
    return ["等待下载", "muted"];
  }
  return ["待下载", "muted"];
}

function weeklyValidationStatus(task) {
  return {
    pending: ["校验中", "timeout"],
    waiting: ["等待各端更新", "timeout"],
    passed: ["校验通过", "success"],
    failed: ["校验失败", "failed"],
  }[task.state.validation_status] || null;
}

function appendWeeklyReportMaterials(copy, task) {
  if (!task.reporting_period || !task.main_document_name) {
    return;
  }
  const materials = document.createElement("section");
  const period = document.createElement("p");
  const mainLabel = document.createElement("p");
  const mainDocument = document.createElement("div");
  const mainDocumentName = document.createElement("span");
  const validationMessage = document.createElement("p");
  const backgroundDocuments = (task.state.linked_documents || []).filter(
    (linkedDocument) => linkedDocument.is_background,
  );
  const mainPassed = task.state.validation_status === "passed";
  materials.className = "automation-weekly-materials";
  period.className = "automation-weekly-period";
  period.textContent = `本期下载 · ${task.reporting_period}`;
  mainLabel.className = `automation-material-summary${mainPassed ? " is-success" : ""}`;
  mainLabel.textContent = mainPassed ? "主文档 · 1/1 通过" : "主文档";
  mainDocument.className = "automation-linked-document";
  mainDocumentName.className = "automation-linked-document-name";
  mainDocumentName.textContent = task.main_document_name;
  mainDocument.append(mainDocumentName);
  materials.append(period, mainLabel, mainDocument);
  backgroundDocuments.forEach((linkedDocument) => {
    const reference = document.createElement("div");
    const referenceName = document.createElement("span");
    const succeeded = linkedDocument.status === "success";
    reference.className = `automation-linked-document${succeeded ? "" : " is-failed"}`;
    referenceName.className = `automation-linked-document-name${succeeded ? "" : " is-failed"}`;
    referenceName.textContent = `上周参考 · ${linkedDocument.name}`;
    referenceName.setAttribute(
      "aria-label",
      `${linkedDocument.name}，${succeeded ? "上周参考已下载" : "上周参考下载失败"}`,
    );
    reference.append(referenceName);
    materials.append(reference);
  });
  if (
    ["failed", "waiting"].includes(task.state.validation_status)
    && task.state.validation_message
  ) {
    const waiting = task.state.validation_status === "waiting";
    validationMessage.className = `automation-material-summary${waiting ? "" : " is-failed"}`;
    validationMessage.textContent = `${waiting ? "等待原因" : "校验原因"} · ${task.state.validation_message}`;
    materials.append(validationMessage);
  }
  copy.append(materials);
}

async function runAutomation(task, button) {
  button.disabled = true;
  const shouldStartBrowser = automationBrowserState === "stopped";
  button.textContent = shouldStartBrowser ? "启动浏览器…" : "受理中…";
  try {
    if (shouldStartBrowser) {
      await apiFetch("/api/automations/browser/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "headless" }),
      });
      await loadAutomationEnvironment();
    }
    button.textContent = "受理中…";
    await apiFetch(`/api/automations/${encodeURIComponent(task.id)}/run`, {
      method: "POST",
    });
    setMessage(elements.automationMessage, "");
    await Promise.all([loadAutomations(), loadAutomationEnvironment()]);
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(
        elements.automationMessage,
        error.message || "自动化任务启动失败。",
        "error",
      );
    }
  } finally {
    button.textContent = shouldStartBrowser ? "启动并运行" : "运行";
  }
}

function selectedAutomationBrowserMode() {
  return Array.from(elements.automationBrowserModeInputs).find(
    (input) => input.checked,
  )?.value || "headless";
}

function selectedAutomationBrowserProfile() {
  return automationBrowserProfiles.find(
    (profile) => profile.id === elements.automationBrowserProfile.value,
  );
}

function updateAutomationBrowserDialog() {
  const selectedProfile = selectedAutomationBrowserProfile();
  const requiresInitialization = selectedProfile && !selectedProfile.initialized;
  const cannotInitialize = requiresInitialization && !selectedProfile.source_available;
  elements.automationBrowserDialogConfirm.disabled = !selectedProfile || cannotInitialize;
  elements.automationBrowserDialogConfirm.textContent = requiresInitialization
    ? "初始化并启动"
    : "启动";
}

function closeAutomationBrowserDialog() {
  if (elements.automationBrowserDialog.open) {
    elements.automationBrowserDialog.close();
  }
}

function openAutomationBrowserDialog() {
  updateAutomationBrowserDialog();
  if (!elements.automationBrowserDialog.open) {
    elements.automationBrowserDialog.showModal();
  }
}

async function startAutomationBrowser(event) {
  event.preventDefault();
  const selectedProfile = selectedAutomationBrowserProfile();
  if (!selectedProfile) {
    setMessage(elements.automationEnvironmentMessage, "请选择浏览器账户。", "error");
    return;
  }
  if (!selectedProfile.initialized && !selectedProfile.source_available) {
    setMessage(elements.automationEnvironmentMessage, "该浏览器账户已不可用。", "error");
    return;
  }
  const requiresInitialization = !selectedProfile.initialized;
  if (
    requiresInitialization
    && !window.confirm(
      "首次使用需要复制该浏览器账户。请先完全退出默认 Chrome；复制完成后将自动启动 Debug Chrome。确定继续吗？",
    )
  ) {
    return;
  }
  elements.automationBrowserControl.disabled = true;
  elements.automationBrowserProfile.disabled = true;
  elements.automationBrowserModeInputs.forEach((input) => {
    input.disabled = true;
  });
  elements.automationBrowserDialogCancel.disabled = true;
  elements.automationBrowserDialogClose.disabled = true;
  elements.automationBrowserDialogConfirm.disabled = true;
  elements.automationBrowserDialogConfirm.textContent = requiresInitialization
    ? "初始化中…"
    : "启动中…";
  elements.automationBrowserControl.textContent = requiresInitialization
    ? "初始化中…"
    : "启动中…";
  try {
    await apiFetch(
      requiresInitialization
        ? "/api/automations/browser/initialize"
        : "/api/automations/browser/start",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode: selectedAutomationBrowserMode(),
          profile_id: selectedProfile.id,
        }),
      },
    );
    closeAutomationBrowserDialog();
    setMessage(elements.automationEnvironmentMessage, "");
    await Promise.all([loadAutomationEnvironment(), loadAutomations()]);
  } catch (error) {
    if (!handleAccessError(error)) {
      await Promise.all([loadAutomationEnvironment(), loadAutomations()]);
      setMessage(
        elements.automationEnvironmentMessage,
        error.message || "Debug Chrome 启动失败。",
        "error",
      );
    }
  }
}

async function stopAutomationBrowser() {
  if (!window.confirm("确定停止 Debug Chrome 吗？已打开的调试浏览器页面会关闭。")) {
    return;
  }
  elements.automationBrowserControl.disabled = true;
  elements.automationBrowserControl.textContent = "停止中…";
  try {
    await apiFetch("/api/automations/browser/stop", { method: "POST" });
    setMessage(elements.automationEnvironmentMessage, "");
    await Promise.all([loadAutomationEnvironment(), loadAutomations()]);
  } catch (error) {
    if (!handleAccessError(error)) {
      await Promise.all([loadAutomationEnvironment(), loadAutomations()]);
      setMessage(
        elements.automationEnvironmentMessage,
        error.message || "Debug Chrome 操作失败。",
        "error",
      );
    }
  }
}

function controlAutomationBrowser() {
  if (automationBrowserState === "running") {
    return stopAutomationBrowser();
  }
  openAutomationBrowserDialog();
}

async function checkFeishuEnvironment() {
  releaseFeishuQr();
  elements.automationFeishuCheck.disabled = true;
  elements.automationFeishuCheck.textContent = "检查中…";
  setBadge(elements.automationFeishuBadge, "检查中", "muted");
  try {
    await apiFetch("/api/automations/environment/feishu/check", { method: "POST" });
    setMessage(elements.automationEnvironmentMessage, "");
    await loadAutomationEnvironment();
  } catch (error) {
    if (!handleAccessError(error)) {
      await loadAutomationEnvironment();
      setMessage(
        elements.automationEnvironmentMessage,
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
      elements.automationEnvironmentMessage,
      error.message || "飞书登录二维码读取失败。",
      "error",
    );
  } finally {
    feishuQrLoading = false;
  }
}

function renderAutomationEnvironment(data) {
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
  elements.automationBrowserProfile.disabled = data.browser_state !== "stopped" || initializing;
  elements.automationBrowserModeInputs.forEach((input) => {
    input.disabled = data.browser_state !== "stopped" || initializing;
  });
  elements.automationBrowserControl.textContent = browserRunning
    ? "停止"
    : initializing
      ? "初始化中…"
      : "启动";
  if (elements.automationBrowserDialog.open) {
    updateAutomationBrowserDialog();
    elements.automationBrowserDialogConfirm.disabled =
      elements.automationBrowserControl.disabled;
  }
  const failedInitialization = selectedProfile?.initialization_state === "failed"
    ? selectedProfile
    : null;
  if (failedInitialization?.initialization_message) {
    setMessage(elements.automationEnvironmentMessage, failedInitialization.initialization_message, "error");
  } else if (data.browser_profiles_error && !automationBrowserProfiles.length) {
    setMessage(elements.automationEnvironmentMessage, data.browser_profiles_error, "error");
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
  if (!data.enabled) {
    setMessage(elements.automationEnvironmentMessage, "自动化任务未启用。", "error");
  }

  return initializing || feishuChecking;
}

function renderAutomations(data) {
  elements.automationList.replaceChildren();
  const browserRunning = data.browser_state === "running";
  const feishuChecking = data.feishu_environment.state === "checking";
  automationBrowserState = data.browser_state;
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
    const heading = document.createElement("div");
    const name = document.createElement("strong");
    const status = document.createElement("span");
    const validationStatus = document.createElement("span");
    const button = document.createElement("button");
    const busy = ["queued", "running"].includes(task.state.status);
    active = active || busy;
    item.className = "automation-item";
    copy.className = "automation-item-copy";
    heading.className = "automation-item-heading";
    name.textContent = task.title;
    if (task.reporting_period) {
      const [downloadText, downloadKind] = weeklyDownloadStatus(task);
      const validation = weeklyValidationStatus(task);
      status.className = `badge badge-${downloadKind}`;
      status.textContent = downloadText;
      if (validation) {
        validationStatus.className = `badge badge-${validation[1]}`;
        validationStatus.textContent = validation[0];
      }
    } else {
      status.className = `badge badge-${automationStatusKind(task.state.status)}`;
      status.textContent = automationTaskStatusText(task);
    }
    button.type = "button";
    button.className = "button-secondary automation-run";
    button.textContent = busy
      ? "执行中…"
      : data.browser_state === "stopped" ? "启动并运行" : "运行";
    button.disabled = (
      !["running", "stopped"].includes(data.browser_state)
      || !task.enabled
      || busy
      || feishuChecking
    );
    button.addEventListener("click", () => runAutomation(task, button));
    heading.append(name, status);
    if (validationStatus.textContent) {
      heading.append(validationStatus);
    }
    copy.append(heading);
    appendWeeklyReportMaterials(copy, task);
    const currentDocuments = (task.state.linked_documents || []).filter(
      (linkedDocument) => !linkedDocument.is_background,
    );
    if (currentDocuments.length) {
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      const linkedSuccesses = currentDocuments.filter(
        (linkedDocument) => linkedDocument.status === "success",
      ).length;
      const linkedFailures = currentDocuments.filter(
        (linkedDocument) => linkedDocument.status === "failed",
      ).length;
      const linkedWaiting = currentDocuments.some(
        (linkedDocument) => linkedDocument.status === "waiting",
      );
      summary.textContent = linkedWaiting
        ? "各端周报 · 等待更新"
        : `各端周报 · ${linkedSuccesses}/${currentDocuments.length} 通过`;
      details.className = "automation-linked-details";
      summary.className = `automation-material-summary is-${linkedFailures ? "failed" : linkedWaiting ? "timeout" : "success"}`;
      details.open = linkedFailures > 0 || linkedWaiting;
      details.append(summary);
      currentDocuments.forEach((linkedDocument) => {
        const row = document.createElement("div");
        const documentName = document.createElement("span");
        const succeeded = linkedDocument.status === "success";
        const waiting = linkedDocument.status === "waiting";
        row.className = `automation-linked-document${succeeded ? "" : waiting ? " is-waiting" : " is-failed"}`;
        documentName.className = `automation-linked-document-name${succeeded ? "" : waiting ? " is-waiting" : " is-failed"}`;
        documentName.textContent = linkedDocument.name;
        documentName.setAttribute(
          "aria-label",
          `${linkedDocument.name}，${succeeded ? "成功" : waiting ? "等待更新" : "失败"}`,
        );
        row.append(documentName);
        details.append(row);
      });
      copy.append(details);
    }
    item.append(copy, button);
    elements.automationList.append(item);
  });
  return active;
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
      setMessage(elements.automationMessage, error.message || "自动化任务读取失败。", "error");
    }
  } finally {
    elements.refreshAutomations.disabled = false;
  }
}

async function loadAutomationEnvironment() {
  const requestVersion = accessVersion;
  elements.refreshAutomationEnvironment.disabled = true;
  try {
    const data = await apiFetch("/api/automations");
    if (requestVersion !== accessVersion) {
      return;
    }
    setMessage(elements.automationEnvironmentMessage, "");
    const active = renderAutomationEnvironment(data);
    if (automationEnvironmentPollTimer) {
      window.clearTimeout(automationEnvironmentPollTimer);
      automationEnvironmentPollTimer = null;
    }
    if (active) {
      automationEnvironmentPollTimer = window.setTimeout(loadAutomationEnvironment, 1000);
    }
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return;
    }
    if (!handleAccessError(error)) {
      automationBrowserState = "unknown";
      setBadge(elements.automationBrowserBadge, "检查失败", "failed");
      elements.automationBrowserControl.disabled = true;
      elements.automationBrowserProfile.disabled = true;
      elements.automationBrowserModeInputs.forEach((input) => {
        input.disabled = true;
      });
      elements.automationBrowserDialogConfirm.disabled = true;
      setBadge(elements.automationFeishuBadge, "检查失败", "failed");
      elements.automationFeishuCheck.disabled = true;
      setMessage(
        elements.automationEnvironmentMessage,
        error.message || "自动化环境读取失败。",
        "error",
      );
    }
  } finally {
    elements.refreshAutomationEnvironment.disabled = false;
  }
}
