"use strict";

const list = document.querySelector("#detail-automation-list");
const message = document.querySelector("#detail-automation-message");
const badge = document.querySelector("#detail-browser-badge");
const count = document.querySelector("#detail-automation-count");
const refresh = document.querySelector("#refresh-automations");
let pollTimer = null;

function showMessage(text, kind = "") {
  message.textContent = text;
  message.className = "message";
  if (kind) {
    message.classList.add(`message-${kind}`);
  }
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.headers,
  });
  const payload = await response.json();
  if (!response.ok || payload.success !== true) {
    throw new Error(payload?.error?.message || "请求失败。");
  }
  return payload.data;
}

function stateText(value) {
  return { idle: "尚未执行", queued: "等待执行", running: "执行中", waiting: "等待更新", success: "成功", failed: "失败" }[value] || value;
}

function stateKind(value) {
  return {
    success: "success",
    failed: "failed",
    queued: "muted",
    running: "timeout",
    waiting: "timeout",
    idle: "muted",
  }[value] || "muted";
}

function taskStatusText(task) {
  if (task.reporting_period && task.state.status === "idle") {
    return "待下载";
  }
  return stateText(task.state.status);
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
    reference.append(referenceName);
    materials.append(reference);
  });
  copy.append(materials);
}

async function run(task, button, browserState) {
  button.disabled = true;
  const shouldStartBrowser = browserState === "stopped";
  button.textContent = shouldStartBrowser ? "启动浏览器…" : "受理中…";
  try {
    if (shouldStartBrowser) {
      await request("/api/automations/browser/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "headless" }),
      });
    }
    button.textContent = "受理中…";
    await request(`/api/automations/${encodeURIComponent(task.id)}/run`, { method: "POST" });
    showMessage("");
    await load();
  } catch (error) {
    showMessage(error.message, "error");
    button.disabled = false;
    button.textContent = shouldStartBrowser ? "启动并运行" : "运行";
  }
}

function render(data) {
  list.replaceChildren();
  const running = data.browser_state === "running";
  const environmentChecking = data.feishu_environment.state === "checking";
  badge.textContent = `${data.browser_message}${data.browser_mode ? ` · ${data.browser_mode}` : ""}`;
  badge.className = `badge badge-${running ? "success" : data.browser_state === "stopped" ? "timeout" : "failed"}`;
  count.textContent = `共 ${data.tasks.length} 个任务 · 已启用 ${data.enabled_count} 个`;
  let active = false;
  data.tasks.forEach((task) => {
    const item = document.createElement("article");
    const copy = document.createElement("div");
    const heading = document.createElement("div");
    const title = document.createElement("strong");
    const status = document.createElement("span");
    const validationStatus = document.createElement("span");
    const button = document.createElement("button");
    const busy = ["queued", "running"].includes(task.state.status);
    active = active || busy;
    item.className = "automation-item";
    copy.className = "automation-item-copy";
    heading.className = "automation-item-heading";
    title.textContent = task.title;
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
      status.className = `badge badge-${stateKind(task.state.status)}`;
      status.textContent = taskStatusText(task);
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
      || environmentChecking
    );
    button.addEventListener("click", () => run(task, button, data.browser_state));
    heading.append(title, status);
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
    list.append(item);
  });
  if (!data.tasks.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "暂无自动化任务。";
    list.append(empty);
  }
  return active;
}

async function load() {
  refresh.disabled = true;
  try {
    const data = await request("/api/automations?all_tasks=true");
    showMessage("");
    const active = render(data);
    if (pollTimer) {
      window.clearTimeout(pollTimer);
      pollTimer = null;
    }
    if (active) {
      pollTimer = window.setTimeout(load, 1000);
    }
  } catch (error) {
    badge.textContent = "检查失败";
    badge.className = "badge badge-failed";
    showMessage(error.message, "error");
  } finally {
    refresh.disabled = false;
  }
}

refresh.addEventListener("click", load);
load();
