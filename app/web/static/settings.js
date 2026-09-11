(() => {
"use strict";

window.initializeSettingsPage = () => {
  window.disposeSettingsPage?.();

const settingsPage = document.body.dataset.settingsPage || "";
const THEME_DETAILS_EXPANDED_KEY = "hub.themeDetailsExpanded.v1";
const settingsMessage = document.querySelector("#settings-message");
const runtimeManagementList = document.querySelector("#runtime-management-list");
const runtimeManagementStatus = document.querySelector(
  "#runtime-management-status",
);
const generalRuntimeSettingsPanel = document.querySelector(
  "#ai-runtime-general-settings",
);
const runtimeModuleInstallForm = document.querySelector("#runtime-module-install-form");
const runtimeModuleFile = document.querySelector("#runtime-module-file");
const runtimeModuleFileTrigger = document.querySelector("#runtime-module-file-trigger");
const runtimeModuleList = document.querySelector("#runtime-module-list");
const orchestrationModuleFile = document.querySelector("#orchestration-module-file");
const orchestrationModuleFileTrigger = document.querySelector("#orchestration-module-file-trigger");
const orchestrationModuleList = document.querySelector("#orchestration-module-list");
const codexDefaultRuntimeImplementation = document.querySelector(
  "#codex-default-runtime-implementation",
);
const codexRuntimeSettingsMessage = document.querySelector(
  "#codex-runtime-settings-message",
);
const quickInteractionCore = window.QuickInteractionCore;
const settingsOpenClawIntegrationList = document.querySelector(
  "#settings-openclaw-integration-list",
);
const settingsOpenClawIntegrationMessage = document.querySelector(
  "#settings-openclaw-integration-message",
);
const settingsOpenClawPatchList = document.querySelector(
  "#settings-openclaw-patch-list",
);
const settingsMaintenanceTerminal = document.querySelector(
  "#settings-maintenance-terminal",
);
const maintenanceTerminalDialog = document.querySelector(
  "#maintenance-terminal-dialog",
);
const maintenanceTerminalDialogClose = document.querySelector(
  "#maintenance-terminal-dialog-close",
);
const maintenanceTerminalDialogCancel = document.querySelector(
  "#maintenance-terminal-dialog-cancel",
);
const maintenanceTerminalDialogConfirm = document.querySelector(
  "#maintenance-terminal-dialog-confirm",
);
const maintenanceTerminalDialogFeedback = document.querySelector(
  "#maintenance-terminal-dialog-feedback",
);
const styleOptionRows = document.querySelectorAll("[data-style-option]");
const fontSizeOptionRows = document.querySelectorAll("[data-font-size-option]");
let maintenanceTerminalOpening = false;
const deploymentPackageForm = document.querySelector("#deployment-package-form");
const deploymentPackageMessage = document.querySelector("#deployment-package-message");
const deploymentPackageOutput = document.querySelector("#deployment-package-output");
const deploymentPackageCurrentAppVersion = document.querySelector("#deployment-package-current-app-version");
const deploymentPackageBuild = document.querySelector("#deployment-package-build");
const deploymentPackageChubVersion = document.querySelector("#deployment-package-chub-version");
const deploymentPackageRuntimeVersion = document.querySelector("#deployment-package-runtime-version");
const deploymentPackageWeixinVersion = document.querySelector("#deployment-package-weixin-version");
const deploymentPackageIncludeDevelopment = document.querySelector("#deployment-package-include-development");
let deploymentPackagePolling = null;

const settingsChoicePickers = new Map();
const settingsChoicePickerObservers = [];
let openSettingsChoicePicker = null;
function settingsPickerLabel(select) {
  return select.closest(".settings-field")?.querySelector("strong")?.textContent.trim()
    || "选择设置";
}

function settingsPickerOptionDescription(select, option) {
  return option.dataset.description
    || "";
}

function closeSettingsChoicePicker(picker = openSettingsChoicePicker) {
  if (!picker) return;
  picker.menu.hidden = true;
  picker.trigger.setAttribute("aria-expanded", "false");
  if (openSettingsChoicePicker === picker) {
    openSettingsChoicePicker = null;
  }
}

function positionSettingsChoicePicker(picker) {
  const triggerRect = picker.trigger.getBoundingClientRect();
  const menu = picker.menu;
  const margin = 8;
  menu.style.visibility = "hidden";
  menu.hidden = false;
  const menuRect = menu.getBoundingClientRect();
  const left = Math.max(
    margin,
    Math.min(triggerRect.left, window.innerWidth - menuRect.width - margin),
  );
  const spaceBelow = window.innerHeight - triggerRect.bottom - margin;
  const top = spaceBelow >= menuRect.height || spaceBelow >= triggerRect.top - margin
    ? Math.min(window.innerHeight - menuRect.height - margin, triggerRect.bottom + 8)
    : Math.max(margin, triggerRect.top - menuRect.height - 8);
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
  menu.style.visibility = "";
}

function renderSettingsChoicePicker(picker) {
  const { select, trigger, menu } = picker;
  const selected = select.selectedOptions[0] || select.options[0];
  trigger.querySelector("[data-settings-picker-value]").textContent = selected?.textContent || "";
  trigger.disabled = select.disabled;
  menu.replaceChildren(...Array.from(select.options, (option) => {
    const button = document.createElement("button");
    const title = document.createElement("span");
    const description = settingsPickerOptionDescription(select, option);
    button.type = "button";
    button.className = "settings-choice-picker-option";
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(option.selected));
    title.textContent = option.textContent;
    button.append(title);
    if (description) {
      const hint = document.createElement("small");
      hint.textContent = description;
      button.append(hint);
    }
    button.disabled = option.disabled;
    if (option.selected) button.classList.add("is-selected");
    button.addEventListener("click", () => {
      if (option.disabled) return;
      select.value = option.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      renderSettingsChoicePicker(picker);
      closeSettingsChoicePicker(picker);
      trigger.focus();
    });
    return button;
  }));
}

function initializeSettingsChoicePickers() {
  document.querySelectorAll("select[data-settings-picker]").forEach((select) => {
    if (settingsChoicePickers.has(select)) return;
    const picker = document.createElement("div");
    const trigger = document.createElement("button");
    const value = document.createElement("span");
    const chevron = document.createElement("span");
    const menu = document.createElement("div");
    const menuId = `${select.id}-menu`;

    picker.className = "settings-choice-picker";
    trigger.type = "button";
    trigger.className = "settings-choice-picker-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-controls", menuId);
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-label", settingsPickerLabel(select));
    value.dataset.settingsPickerValue = "";
    chevron.setAttribute("aria-hidden", "true");
    trigger.append(value, chevron);
    menu.id = menuId;
    menu.className = "settings-choice-picker-menu";
    menu.setAttribute("role", "listbox");
    menu.setAttribute("aria-label", settingsPickerLabel(select));
    menu.hidden = true;
    picker.append(trigger);
    select.after(picker);
    document.body.append(menu);

    const state = { select, trigger, menu, picker, observer: null };
    settingsChoicePickers.set(select, state);
    renderSettingsChoicePicker(state);
    trigger.addEventListener("click", () => {
      if (trigger.disabled) return;
      if (openSettingsChoicePicker === state) {
        closeSettingsChoicePicker(state);
        return;
      }
      closeSettingsChoicePicker();
      renderSettingsChoicePicker(state);
      positionSettingsChoicePicker(state);
      openSettingsChoicePicker = state;
      trigger.setAttribute("aria-expanded", "true");
      menu.querySelector(".is-selected:not(:disabled), [role='option']:not(:disabled)")?.focus();
    });
    select.addEventListener("change", () => renderSettingsChoicePicker(state));
    state.observer = new MutationObserver(() => renderSettingsChoicePicker(state));
    state.observer.observe(select, {
      attributes: true,
      attributeFilter: ["disabled"],
      childList: true,
      subtree: true,
    });
    settingsChoicePickerObservers.push(state.observer);
  });
}

const closePickerOnPointerDown = (event) => {
  if (
    openSettingsChoicePicker
    && !openSettingsChoicePicker.trigger.contains(event.target)
    && !openSettingsChoicePicker.menu.contains(event.target)
  ) {
    closeSettingsChoicePicker();
  }
};

const closePickerOnEscape = (event) => {
  if (event.key === "Escape" && openSettingsChoicePicker) {
    event.preventDefault();
    const picker = openSettingsChoicePicker;
    closeSettingsChoicePicker(picker);
    picker.trigger.focus();
  }
};

const closePickerOnResize = () => closeSettingsChoicePicker();
document.addEventListener("pointerdown", closePickerOnPointerDown);
document.addEventListener("keydown", closePickerOnEscape);
window.addEventListener("resize", closePickerOnResize);

function renderStyleSelection(style) {
  styleOptionRows.forEach((row) => {
    const selected = row.dataset.styleOption === style;
    row.classList.toggle("is-selected", selected);
    row.querySelector('input[type="radio"]').checked = selected;
  });
}

function renderFontSizeSelection(fontSize) {
  fontSizeOptionRows.forEach((row) => {
    const selected = row.dataset.fontSizeOption === fontSize;
    row.classList.toggle("is-selected", selected);
    row.querySelector('input[type="radio"]').checked = selected;
  });
}


function setRuntimeManagementStatus(text, kind = "") {
  if (!(runtimeManagementStatus instanceof HTMLElement)) return;
  runtimeManagementStatus.hidden = !text;
  runtimeManagementStatus.textContent = text;
  runtimeManagementStatus.className = `runtime-management-status${kind ? ` is-${kind}` : ""}`;
}

function renderRuntimeManagement(data) {
  if (!(runtimeManagementList instanceof HTMLElement)) return;
  const runtimeId = runtimeManagementList.dataset.runtimeId || "";
  const runtimes = (Array.isArray(data?.runtimes) ? data.runtimes : [])
    .filter((runtime) => !runtimeId || runtime.runtime_id === runtimeId);
  if (runtimeId === "codex") {
    codexRuntimeEnabled = runtimes.find((runtime) => runtime.runtime_id === "codex")?.enabled === true;
    renderRuntimePlugins();
  }
  runtimeManagementList.replaceChildren();
  for (const runtime of runtimes) {
    const field = document.createElement("section");
    field.className = "settings-field settings-field-toggle";
    const copy = document.createElement("span");
    const title = document.createElement("span");
    title.className = "settings-integration-title";
    const name = document.createElement("strong");
    name.textContent = runtime.name || runtime.runtime_id;
    const badge = document.createElement("span");
    badge.className = `badge ${runtime.healthy ? "badge-success" : "badge-muted"}`;
    badge.textContent = runtime.healthy ? "健康" : "不可用";
    title.append(name, badge);
    const identifier = document.createElement("small");
    identifier.textContent = `Runtime ID：${runtime.runtime_id}`;
    const description = document.createElement("small");
    description.textContent = runtime.enabled
      ? (runtime.healthy ? "正在接收新 AI 任务。" : (runtime.reason || "允许接收新任务，但当前 Runtime 不可用。"))
      : "已停止接收新 AI 任务；已受理任务继续收敛。";
    if (!runtime.enabled) {
      description.className = "runtime-management-item-status is-warning";
    }
    copy.append(title, identifier, description);
    const control = document.createElement("label");
    control.className = "settings-switch";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = `runtime-enabled-${runtime.runtime_id}`;
    input.checked = runtime.enabled === true;
    input.dataset.runtimeId = runtime.runtime_id;
    input.dataset.previousEnabled = String(runtime.enabled === true);
    input.setAttribute("aria-label", `${name.textContent} ${input.checked ? "正在接收新任务" : "已停止接收新任务"}`);
    input.addEventListener("change", () => void saveRuntimeEnablement(input));
    control.htmlFor = input.id;
    const track = document.createElement("span");
    track.className = "settings-switch-track";
    track.setAttribute("aria-hidden", "true");
    control.append(input, track);
    field.append(copy, control);
    runtimeManagementList.append(field);
  }
  setRuntimeManagementStatus("");
}

async function loadRuntimeManagement() {
  try {
    const response = await fetch("/api/codex/runtimes", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || payload.success !== true) throw new Error("runtime_management_unavailable");
    renderRuntimeManagement(payload.data);
  } catch (_error) {
    runtimeManagementList?.replaceChildren();
    setRuntimeManagementStatus("暂时无法读取 AI Runtime 状态。", "error");
  }
}

function runtimeModuleRow(module, { candidate = false } = {}) {
  const row = document.createElement("div");
  row.className = "settings-utility-row runtime-module-row";
  const copy = document.createElement("span");
  const title = document.createElement("strong");
  const detail = document.createElement("small");
  title.textContent = formalImplementationTitle(module.name || "Runtime", module.version);
  detail.textContent = candidate
    ? (module.description || "等待导入。")
    : (module.status === "active"
      ? (module.description || "Runtime 插件已导入。")
      : (module.reason || "Runtime 插件不可用。"));
  copy.append(title, detail);
  const actions = document.createElement("span");
  actions.className = "runtime-module-row-actions";
  if (candidate) {
    const install = document.createElement("button");
    install.className = "button-secondary";
    install.type = "button";
    install.textContent = runtimeModuleBusy ? "导入中" : "导入";
    install.disabled = runtimeModuleBusy;
    install.addEventListener("click", () => void installSelectedRuntimePlugin());
    actions.append(install);
    const remove = document.createElement("button");
    remove.className = "button-secondary";
    remove.type = "button";
    remove.textContent = "移除";
    remove.title = "移除当前选择的 Runtime ZIP";
    remove.disabled = runtimeModuleBusy;
    remove.addEventListener("click", clearSelectedRuntimePlugin);
    actions.append(remove);
  } else {
    const badge = document.createElement("span");
    badge.className = `badge ${module.status === "active" ? "badge-success" : "badge-muted"}`;
    badge.textContent = module.status === "active" ? "已启用" : "不可用";
    actions.append(badge);
    if (module.removable !== false) {
      const remove = document.createElement("button");
      remove.className = "button-danger";
      remove.type = "button";
      remove.textContent = "移除";
      remove.disabled = runtimeModuleBusy;
      remove.addEventListener("click", () => void confirmRuntimePluginRemoval(module));
      actions.append(remove);
    }
  }
  row.append(copy, actions);
  return row;
}

function moduleEmptyRow(text) {
  const row = document.createElement("div");
  row.className = "settings-utility-row runtime-module-row runtime-module-empty-row";
  const copy = document.createElement("span");
  const detail = document.createElement("small");
  detail.textContent = text;
  copy.append(detail);
  row.append(copy);
  return row;
}

let runtimeModuleCandidate = null;
let runtimeModuleBusy = false;
let runtimeModuleData = { modules: [] };
let codexRuntimeEnabled = null;
let orchestrationModuleCandidate = null;
let orchestrationModuleBusy = false;
let orchestrationModuleData = { modules: [] };

function clearSelectedRuntimePlugin() {
  if (runtimeModuleBusy) return;
  runtimeModuleCandidate = null;
  if (runtimeModuleFile instanceof HTMLInputElement) runtimeModuleFile.value = "";
  renderRuntimePlugins();
}

function clearSelectedOrchestrationPlugin() {
  if (orchestrationModuleBusy) return;
  orchestrationModuleCandidate = null;
  if (orchestrationModuleFile instanceof HTMLInputElement) orchestrationModuleFile.value = "";
  renderOrchestrationPlugins();
}

function showRuntimePluginToast(text, kind = "info") {
  window.showChubToast?.(text, { kind });
}

function setCodexRuntimeSettingsMessage(text, kind = "") {
  if (!(codexRuntimeSettingsMessage instanceof HTMLElement)) return;
  codexRuntimeSettingsMessage.textContent = text;
  codexRuntimeSettingsMessage.className = kind === "error" ? "message message-error" : "message";
}

function formalVersion(version) {
  const normalized = typeof version === "string" ? version.trim().replace(/^v/i, "") : "";
  return normalized ? `v${normalized}` : "未知版本";
}

function formalImplementationTitle(name, version) {
  return `${name || "实现"} · 正式版 ${formalVersion(version)}`;
}

function versionTitle(item) {
  const name = item.name || "Codex";
  return item.implementation_id === "builtin-dev"
    ? `${name} · 开发实现`
    : formalImplementationTitle(name, item.version);
}

function renderCodexRuntimeVersions(implementations) {
  const versions = Array.isArray(implementations?.implementations)
    ? implementations.implementations
    : [];
  if (codexDefaultRuntimeImplementation instanceof HTMLSelectElement) {
    codexDefaultRuntimeImplementation.replaceChildren();
    versions
      .filter((item) => item.enabled === true && item.healthy === true)
      .forEach((item) => {
        const option = document.createElement("option");
        option.value = item.implementation_id;
        option.textContent = versionTitle(item);
        option.dataset.description = item.implementation_id;
        option.selected = item.is_default === true;
        codexDefaultRuntimeImplementation.append(option);
      });
    codexDefaultRuntimeImplementation.disabled = runtimeModuleBusy
      || codexDefaultRuntimeImplementation.options.length === 0;
  }
}

async function saveCodexDefaultRuntimeImplementation() {
  if (!(codexDefaultRuntimeImplementation instanceof HTMLSelectElement) || runtimeModuleBusy) return;
  const implementationId = codexDefaultRuntimeImplementation.value;
  if (!implementationId) return;
  runtimeModuleBusy = true;
  renderRuntimePlugins();
  setCodexRuntimeSettingsMessage("");
  try {
    await fetchSettingsApi("/api/codex/runtime-implementations/default", {
      method: "PUT",
      body: JSON.stringify({ implementation_id: implementationId }),
      headers: { "Content-Type": "application/json" },
    });
    await loadRuntimePlugins();
  } catch (error) {
    showRuntimePluginToast(
      error instanceof Error ? error.message : "默认 Runtime 版本未能更新。",
      "error",
    );
  } finally {
    runtimeModuleBusy = false;
    await loadRuntimePlugins();
  }
}

function renderRuntimePlugins(data = runtimeModuleData) {
  runtimeModuleData = data || { modules: [] };
  const modules = Array.isArray(runtimeModuleData.modules) ? runtimeModuleData.modules : [];
  const rows = [];
  const implementations = runtimeModuleData.implementations;
  renderCodexRuntimeVersions(implementations);
  if (runtimeModuleCandidate) rows.push(runtimeModuleRow(runtimeModuleCandidate, { candidate: true }));
  rows.push(...modules.map((module) => runtimeModuleRow(module)));
  if (rows.length === 0) rows.push(moduleEmptyRow("尚未导入 Runtime 插件。"));
  runtimeModuleList?.replaceChildren(...rows);
}

async function loadRuntimePlugins() {
  try {
    const [modules, implementations] = await Promise.all([
      fetchSettingsApi("/api/runtime-modules"),
      fetchSettingsApi("/api/codex/runtime-implementations"),
    ]);
    renderRuntimePlugins({ ...modules, implementations });
  } catch (_error) {
    renderRuntimePlugins();
    runtimeModuleList?.replaceChildren();
    setCodexRuntimeSettingsMessage("暂时无法读取 Codex Runtime 版本状态。", "error");
    if (runtimeModuleList instanceof HTMLElement) {
      showRuntimePluginToast("暂时无法读取 Runtime 插件状态。", "error");
    }
  }
}

async function runtimeModuleRequest(path, { method = "POST", file } = {}) {
  const response = await fetch(path, {
    method,
    headers: file ? { "Content-Type": "application/zip", "X-Chub-Module-Filename": file.name } : undefined,
    body: file ? await file.arrayBuffer() : undefined,
    cache: "no-store",
  });
  const payload = await response.json();
  if (!response.ok || payload.success !== true) {
    throw new Error(payload?.error?.message || "Runtime 插件操作失败。");
  }
  return payload.data;
}

async function installSelectedRuntimePlugin() {
  const file = runtimeModuleCandidate?.file;
  if (!file || runtimeModuleBusy) return;
  runtimeModuleBusy = true;
  renderRuntimePlugins();
  showRuntimePluginToast("正在导入并确认 Runtime 插件。", "info");
  try {
    await runtimeModuleRequest("/api/runtime-modules/install", { file });
    runtimeModuleCandidate = null;
    if (runtimeModuleFile instanceof HTMLInputElement) runtimeModuleFile.value = "";
    showRuntimePluginToast("Runtime 插件已导入并启用。", "success");
    await loadRuntimePlugins();
  } catch (error) {
    showRuntimePluginToast(error instanceof Error ? error.message : "Runtime 插件未能导入或启用。", "error");
  } finally {
    runtimeModuleBusy = false;
    renderRuntimePlugins();
  }
}

async function confirmRuntimePluginRemoval(module) {
  if (runtimeModuleBusy || typeof showConfirmationDialog !== "function") return;
  await showConfirmationDialog({
    title: "移除 Runtime 插件",
    description: "移除后，该插件的安装代码和关联 Chub 运行态将被清理；旧版本不会保留。",
    details: [{ label: "Runtime", value: module.name || module.module_id }],
    confirmLabel: "移除",
    pendingLabel: "正在移除…",
    errorMessage: "Runtime 插件未能移除。",
    onConfirm: async () => {
      runtimeModuleBusy = true;
      renderRuntimePlugins();
      try {
        await runtimeModuleRequest(`/api/runtime-modules/${encodeURIComponent(module.module_id)}`, { method: "DELETE" });
        showRuntimePluginToast("Runtime 插件已移除。", "success");
        await loadRuntimePlugins();
      } finally {
        runtimeModuleBusy = false;
        renderRuntimePlugins();
      }
    },
  });
}

function initializeRuntimePluginInstall() {
  if (!(runtimeModuleFile instanceof HTMLInputElement)) return;
  runtimeModuleFileTrigger?.addEventListener("click", () => runtimeModuleFile.click());
  runtimeModuleFile.addEventListener("change", async () => {
    const file = runtimeModuleFile.files?.[0];
    runtimeModuleCandidate = null;
    if (!file) {
      renderRuntimePlugins();
      return;
    }
    runtimeModuleBusy = true;
    renderRuntimePlugins();
    try {
      const preview = await runtimeModuleRequest("/api/runtime-modules/inspect", { file });
      runtimeModuleCandidate = { ...preview, file };
    } catch (error) {
      runtimeModuleFile.value = "";
      showRuntimePluginToast(error instanceof Error ? error.message : "Runtime 插件清单不可读取。", "error");
    } finally {
      runtimeModuleBusy = false;
      renderRuntimePlugins();
    }
  });
  void loadRuntimePlugins();
}

function orchestrationModuleRow(module, { candidate = false } = {}) {
  const row = document.createElement("div");
  row.className = "settings-utility-row runtime-module-row";
  const copy = document.createElement("span");
  const title = document.createElement("strong");
  const detail = document.createElement("small");
  title.textContent = formalImplementationTitle(module.name || "任务编排插件", module.version);
  detail.textContent = candidate
    ? (module.description || "等待导入。")
    : (module.available
      ? (module.active ? "当前由微信任务润色使用" : (module.description || "已导入，可在对应任务设置中启用。"))
      : (module.reason || "任务编排插件当前不可用。"));
  copy.append(title, detail);
  const actions = document.createElement("span");
  actions.className = "runtime-module-row-actions";
  if (candidate) {
    const install = document.createElement("button");
    install.className = "button-secondary";
    install.type = "button";
    install.textContent = orchestrationModuleBusy ? "导入中" : "导入";
    install.disabled = orchestrationModuleBusy;
    install.addEventListener("click", () => void installSelectedOrchestrationPlugin());
    actions.append(install);
    const remove = document.createElement("button");
    remove.className = "button-secondary";
    remove.type = "button";
    remove.textContent = "移除";
    remove.title = "移除当前选择的任务编排插件 ZIP";
    remove.disabled = orchestrationModuleBusy;
    remove.addEventListener("click", clearSelectedOrchestrationPlugin);
    actions.append(remove);
  } else {
    const badge = document.createElement("span");
    badge.className = `badge ${module.available ? "badge-success" : "badge-muted"}`;
    badge.textContent = module.active ? "已启用" : (module.available ? "可用" : "不可用");
    actions.append(badge);
    if (module.removable === true) {
      const remove = document.createElement("button");
      remove.className = "button-danger";
      remove.type = "button";
      remove.textContent = "移除";
      remove.disabled = orchestrationModuleBusy;
      remove.addEventListener("click", () => void confirmOrchestrationPluginRemoval(module));
      actions.append(remove);
    }
  }
  row.append(copy, actions);
  return row;
}

function renderOrchestrationPlugins(data = orchestrationModuleData) {
  orchestrationModuleData = data || { modules: [] };
  if (!(orchestrationModuleList instanceof HTMLElement)) return;
  const modules = Array.isArray(orchestrationModuleData.modules)
    ? orchestrationModuleData.modules
    : [];
  const rows = [];
  if (orchestrationModuleCandidate) {
    rows.push(orchestrationModuleRow(orchestrationModuleCandidate, { candidate: true }));
  }
  rows.push(...modules.map((module) => orchestrationModuleRow(module)));
  if (rows.length === 0) rows.push(moduleEmptyRow("尚未导入任务编排插件。"));
  orchestrationModuleList.replaceChildren(...rows);
}

async function orchestrationModuleRequest(path, { method = "POST", file } = {}) {
  const response = await fetch(path, {
    method,
    headers: file ? { "X-Chub-Module-Filename": file.name } : undefined,
    body: file,
    cache: "no-store",
  });
  const payload = await response.json();
  if (!response.ok || payload.success !== true) {
    throw new Error(payload?.error?.message || "任务编排插件操作失败。");
  }
  return payload.data;
}

async function loadOrchestrationPlugins() {
  try {
    const data = await fetchSettingsApi("/api/settings/weixin-task-orchestration/modules", {
      cache: "no-store",
    });
    renderOrchestrationPlugins(data);
  } catch (_error) {
    renderOrchestrationPlugins();
    if (orchestrationModuleList instanceof HTMLElement) {
      orchestrationModuleList.replaceChildren();
      const message = document.createElement("p");
      message.className = "message message-error";
      message.textContent = "暂时无法读取任务编排插件状态。";
      orchestrationModuleList.append(message);
    }
  }
}

async function installSelectedOrchestrationPlugin() {
  const file = orchestrationModuleCandidate?.file;
  if (!file || orchestrationModuleBusy) return;
  orchestrationModuleBusy = true;
  renderOrchestrationPlugins();
  try {
    await orchestrationModuleRequest("/api/settings/weixin-task-orchestration/modules/install", { file });
    orchestrationModuleCandidate = null;
    if (orchestrationModuleFile instanceof HTMLInputElement) orchestrationModuleFile.value = "";
    showRuntimePluginToast("任务编排插件已导入；请在对应任务设置中选择启用。", "success");
    await loadOrchestrationPlugins();
  } catch (error) {
    showRuntimePluginToast(error instanceof Error ? error.message : "任务编排插件未能导入。", "error");
  } finally {
    orchestrationModuleBusy = false;
    renderOrchestrationPlugins();
  }
}

async function confirmOrchestrationPluginRemoval(module) {
  if (orchestrationModuleBusy || typeof showConfirmationDialog !== "function") return;
  await showConfirmationDialog({
    title: "移除任务编排插件",
    description: "移除后该插件 ZIP 产物不可恢复。当前正在使用的插件必须先切换到开发实现或其他 ZIP；仍被未结束任务引用的插件不能移除。",
    details: [{ label: "任务编排插件", value: module.name || module.implementation_ref }],
    confirmLabel: "移除",
    pendingLabel: "正在移除…",
    errorMessage: "任务编排插件未能移除。",
    onConfirm: async () => {
      orchestrationModuleBusy = true;
      renderOrchestrationPlugins();
      try {
        await orchestrationModuleRequest(
          `/api/settings/weixin-task-orchestration/modules/${encodeURIComponent(module.implementation_ref)}`,
          { method: "DELETE" },
        );
        showRuntimePluginToast(
          "任务编排插件已移除。",
          "success",
        );
        await loadOrchestrationPlugins();
      } finally {
        orchestrationModuleBusy = false;
        renderOrchestrationPlugins();
      }
    },
  });
}

function initializeOrchestrationPluginInstall() {
  if (!(orchestrationModuleFile instanceof HTMLInputElement)) return;
  orchestrationModuleFileTrigger?.addEventListener("click", () => orchestrationModuleFile.click());
  orchestrationModuleFile.addEventListener("change", async () => {
    const file = orchestrationModuleFile.files?.[0];
    orchestrationModuleCandidate = null;
    if (!file) {
      renderOrchestrationPlugins();
      return;
    }
    orchestrationModuleBusy = true;
    renderOrchestrationPlugins();
    try {
      const preview = await orchestrationModuleRequest(
        "/api/settings/weixin-task-orchestration/modules/inspect",
        { file },
      );
      orchestrationModuleCandidate = { ...preview, file };
    } catch (error) {
      orchestrationModuleFile.value = "";
      showRuntimePluginToast(error instanceof Error ? error.message : "任务编排插件清单不可读取。", "error");
    } finally {
      orchestrationModuleBusy = false;
      renderOrchestrationPlugins();
    }
  });
  void loadOrchestrationPlugins();
}

function runtimeSettingOptions(field, catalog, values) {
  if (field.id === "new-session-permission") {
    return (quickInteractionCore?.quickSessionPermissionOptions || [])
      .filter((option) => option.value !== "ask");
  }
  if (field.id === "weekly-report-runtime") {
    return [{ value: "codex", label: "Codex", description: "当前可用于周报自动化的 AI Runtime。" }];
  }
  if (field.id === "weekly-report-permission") {
    return quickInteractionCore?.quickSessionPermissionOptions || [];
  }
  if (field.id === "weekly-report-model") {
    return quickInteractionCore?.quickSessionModelOptions(
      catalog,
      values["weekly-report-model"] === "__default__" ? "" : values["weekly-report-model"],
    ).map((option) => ({
      ...option,
      value: option.value || "__default__",
    })) || [];
  }
  if (field.id === "weekly-report-reasoning") {
    return quickInteractionCore?.quickSessionReasoningOptions(
      catalog,
      values["weekly-report-model"] === "__default__" ? "" : values["weekly-report-model"],
      values["weekly-report-reasoning"] === "__default__" ? "" : values["weekly-report-reasoning"],
    ).map((option) => ({
      ...option,
      value: option.value || "__default__",
    })) || [];
  }
  return [];
}

function runtimeSettingInput(field, catalog, values) {
  if (field.input_type === "select") {
    const select = document.createElement("select");
    select.id = `runtime-setting-${field.id}`;
    select.name = field.id;
    select.dataset.runtimeSetting = field.id;
    for (const optionData of runtimeSettingOptions(field, catalog, values)) {
      const option = document.createElement("option");
      option.value = optionData.value;
      option.textContent = optionData.label;
      option.dataset.description = optionData.description || "";
      option.selected = optionData.value === field.value;
      option.disabled = optionData.disabled === true;
      select.append(option);
    }
    return select;
  }
  const input = document.createElement("input");
  input.id = `runtime-setting-${field.id}`;
  input.name = field.id;
  input.type = field.input_type === "number" ? "number" : "text";
  input.value = field.value == null ? "" : String(field.value);
  input.placeholder = field.placeholder || "";
  input.dataset.runtimeSetting = field.id;
  if (input.type === "number") {
    input.min = "1";
    input.step = "1";
  }
  return input;
}

function disposeGeneralRuntimeSettingsPickers() {
  if (!(generalRuntimeSettingsPanel instanceof HTMLElement)) return;
  for (const [select, picker] of settingsChoicePickers) {
    if (!generalRuntimeSettingsPanel.contains(select)) continue;
    if (openSettingsChoicePicker === picker) closeSettingsChoicePicker(picker);
    picker.observer?.disconnect();
    picker.menu.remove();
    picker.picker.remove();
    settingsChoicePickers.delete(select);
  }
}

function renderGeneralRuntimeSettings(data, catalog = null) {
  if (!(generalRuntimeSettingsPanel instanceof HTMLElement)) return;
  disposeGeneralRuntimeSettingsPickers();
  generalRuntimeSettingsPanel.replaceChildren();
  const sections = Array.isArray(data?.sections) ? data.sections : [];
  for (const section of sections) {
    const heading = document.createElement("h3");
    heading.textContent = section.title;
    generalRuntimeSettingsPanel.append(heading);
    if (section.description) {
      const description = document.createElement("p");
      description.className = "settings-subsection-description";
      description.textContent = section.description;
      generalRuntimeSettingsPanel.append(description);
    }
    if (!Array.isArray(section.fields) || section.fields.length === 0) continue;
    const form = document.createElement("form");
    form.className = "runtime-settings-form settings-divided-list";
    const values = Object.fromEntries(
      (Array.isArray(section.fields) ? section.fields : []).map((field) => [field.id, field.value]),
    );
    for (const field of Array.isArray(section.fields) ? section.fields : []) {
      const label = document.createElement("label");
      label.className = "settings-field";
      label.htmlFor = `runtime-setting-${field.id}`;
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      const detail = document.createElement("small");
      title.textContent = field.label;
      detail.textContent = field.description;
      copy.append(title, detail);
      const input = runtimeSettingInput(field, catalog, values);
      label.append(copy, input);
      if (input instanceof HTMLSelectElement) {
        input.dataset.settingsPicker = "";
      }
      input.addEventListener("change", () => {
        if (field.id === "weekly-report-model") {
          const reasoning = form.querySelector("[data-runtime-setting='weekly-report-reasoning']");
          if (reasoning instanceof HTMLSelectElement) reasoning.value = "__default__";
        }
        void saveGeneralRuntimeSettings(form, input, message);
      });
      form.append(label);
    }
    const message = document.createElement("p");
    message.className = "message";
    message.setAttribute("aria-live", "polite");
    form.append(message);
    generalRuntimeSettingsPanel.append(form);
  }
  initializeSettingsChoicePickers();
}

async function loadGeneralRuntimeSettings() {
  if (!(generalRuntimeSettingsPanel instanceof HTMLElement)) return;
  try {
    const [data, catalog] = await Promise.all([
      fetchSettingsApi("/api/ai/settings"),
      quickInteractionCore?.readModelCatalog().catch(() => null) || Promise.resolve(null),
    ]);
    renderGeneralRuntimeSettings(data, catalog);
  } catch (_error) {
    generalRuntimeSettingsPanel.replaceChildren();
    const message = document.createElement("p");
    message.className = "message message-error";
    message.textContent = "暂时无法读取 Runtime 默认项。";
    generalRuntimeSettingsPanel.append(message);
  }
}

async function saveGeneralRuntimeSettings(form, changedInput, message) {
  const values = {};
  form.querySelectorAll("[data-runtime-setting]").forEach((input) => {
    values[input.dataset.runtimeSetting] = input.value.trim() || null;
  });
  const inputs = Array.from(form.querySelectorAll("[data-runtime-setting]"));
  inputs.forEach((input) => { input.disabled = true; });
  setSettingsMessage(message, "");
  try {
    const data = await fetchSettingsApi("/api/ai/settings", {
      method: "PUT",
      headers: settingsHeaders(true),
      body: JSON.stringify({ values }),
    });
    const catalog = await (quickInteractionCore?.readModelCatalog().catch(() => null) || Promise.resolve(null));
    renderGeneralRuntimeSettings(data, catalog);
  } catch (_error) {
    inputs.forEach((input) => { input.disabled = false; });
    if (changedInput instanceof HTMLElement) changedInput.focus();
    setSettingsMessage(message, "保存失败，请检查配置后重试。", "error");
  }
}

async function saveRuntimeEnablement(input) {
  const previousEnabled = input.dataset.previousEnabled === "true";
  const enabled = input.checked;
  input.disabled = true;
  try {
    const response = await fetch(`/api/codex/runtimes/${encodeURIComponent(input.dataset.runtimeId)}`, {
      method: "PUT",
      headers: settingsHeaders(true),
      body: JSON.stringify({ enabled }),
    });
    const payload = await response.json();
    if (!response.ok || payload.success !== true) throw new Error("runtime_enablement_update_failed");
    renderRuntimeManagement(payload.data);
  } catch (_error) {
    input.checked = previousEnabled;
    input.disabled = false;
    setRuntimeManagementDescription("AI Runtime 任务接入策略保存失败，请稍后重试。", "error");
  }
}

function settingsHeaders(includeJson = false) {
  const headers = {};
  if (includeJson) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

function setSettingsMessage(element, text, kind = "") {
  if (!(element instanceof HTMLElement)) return;
  element.textContent = text;
  element.className = kind ? `message message-${kind}` : "message";
}

async function fetchSettingsApi(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.headers || settingsHeaders(),
  });
  const payload = await response.json();
  if (!response.ok || payload.success !== true) {
    throw new Error(payload.error?.message || payload.error?.code || "request_failed");
  }
  return payload.data;
}

function closeMaintenanceTerminalDialog() {
  if (maintenanceTerminalDialog.open) {
    maintenanceTerminalDialog.close();
  }
}

function initializeAppearanceSettings() {
  const detailsToggle = document.querySelector("[data-theme-details-toggle]");
  const detailsToggleLabel = detailsToggle.querySelector("[data-theme-details-label]");
  const detailsTransitionMs = 320;
  let detailsExpanded = false;
  try {
    detailsExpanded = localStorage.getItem(THEME_DETAILS_EXPANDED_KEY) === "true";
  } catch (_error) {
    try { localStorage.removeItem(THEME_DETAILS_EXPANDED_KEY); } catch (_storageError) { /* Ignore unavailable storage. */ }
  }
  const setDetailsExpanded = (expanded, { persist = true, animate = true } = {}) => {
    detailsExpanded = expanded;
    styleOptionRows.forEach((row) => {
      const details = row.querySelector(".theme-option-preview");
      if (expanded) {
        details.hidden = false;
        if (animate) {
          window.requestAnimationFrame(() => {
            if (detailsExpanded) row.classList.add("is-expanded");
          });
        } else {
          row.classList.add("is-expanded");
        }
      } else {
        row.classList.remove("is-expanded");
        if (animate) {
          window.setTimeout(() => {
            if (!row.classList.contains("is-expanded")) details.hidden = true;
          }, detailsTransitionMs);
        } else {
          details.hidden = true;
        }
      }
    });
    detailsToggle.setAttribute("aria-expanded", String(expanded));
    const toggleLabel = expanded ? "收起文字层级示例" : "显示文字层级示例";
    detailsToggle.setAttribute("aria-label", toggleLabel);
    detailsToggle.title = toggleLabel;
    detailsToggleLabel.textContent = toggleLabel;
    if (!persist) return;
    try { localStorage.setItem(THEME_DETAILS_EXPANDED_KEY, String(expanded)); } catch (_error) { /* Detail expansion is optional. */ }
  };
  styleOptionRows.forEach((row) => {
    const input = row.querySelector('input[type="radio"]');
    input.addEventListener("change", () => {
      if (!input.checked) return;
      const style = row.dataset.styleOption;
      const result = window.ChubTheme.applyStyle(style, { persist: true });
      renderStyleSelection(result.style);
      setSettingsMessage(
        settingsMessage,
        result.persisted ? "" : "当前浏览器无法保存主题偏好，已仅在本页临时应用。",
        result.persisted ? "" : "error",
      );
    });
  });
  fontSizeOptionRows.forEach((row) => {
    const input = row.querySelector('input[type="radio"]');
    input.addEventListener("change", () => {
      if (!input.checked) return;
      const result = window.ChubTheme.applyFontSize(row.dataset.fontSizeOption, {
        persist: true,
      });
      renderFontSizeSelection(result.fontSize);
      setSettingsMessage(
        settingsMessage,
        result.persisted ? "" : "当前浏览器无法保存文字大小偏好，已仅在本页临时应用。",
        result.persisted ? "" : "error",
      );
    });
  });
  detailsToggle.addEventListener("click", () => setDetailsExpanded(!detailsExpanded));
  setDetailsExpanded(detailsExpanded, { persist: false, animate: false });
  renderStyleSelection(window.ChubTheme.currentStyle());
  renderFontSizeSelection(window.ChubTheme.currentFontSize());
}

function initializeDiagnosticsSettings() {
  settingsMaintenanceTerminal.addEventListener("click", () => {
    if (!maintenanceTerminalOpening) {
      maintenanceTerminalDialogFeedback.textContent = "";
      maintenanceTerminalDialogFeedback.className = "message";
      maintenanceTerminalDialog.showModal();
    }
  });
  maintenanceTerminalDialogClose.addEventListener("click", closeMaintenanceTerminalDialog);
  maintenanceTerminalDialogCancel.addEventListener("click", closeMaintenanceTerminalDialog);
  maintenanceTerminalDialog.addEventListener("click", (event) => {
    if (event.target === maintenanceTerminalDialog) closeMaintenanceTerminalDialog();
  });
  maintenanceTerminalDialogConfirm.addEventListener("click", async () => {
    if (maintenanceTerminalOpening) return;
    const terminalWindow = window.open("", "_blank");
    if (!terminalWindow) {
      maintenanceTerminalDialogFeedback.textContent = "浏览器阻止了维护终端窗口，请允许此站点打开弹窗后重试。";
      maintenanceTerminalDialogFeedback.className = "message message-error";
      return;
    }
    terminalWindow.opener = null;
    maintenanceTerminalOpening = true;
    settingsMaintenanceTerminal.disabled = true;
    maintenanceTerminalDialogConfirm.disabled = true;
    try {
      const data = await fetchSettingsApi("/api/maintenance-terminal/access", { method: "POST", headers: settingsHeaders(true) });
      terminalWindow.location.replace(data.terminal_url);
      closeMaintenanceTerminalDialog();
    } catch (_error) {
      terminalWindow.close();
      maintenanceTerminalDialogFeedback.textContent = "维护终端暂时无法启动，请检查 ttyd 和 zsh。";
      maintenanceTerminalDialogFeedback.className = "message message-error";
    } finally {
      maintenanceTerminalOpening = false;
      settingsMaintenanceTerminal.disabled = false;
      maintenanceTerminalDialogConfirm.disabled = false;
    }
  });
  initializeDeploymentPackageSettings();
}

function initializeDeploymentPackageSettings() {
  if (!(deploymentPackageForm instanceof HTMLFormElement)) return;
  const inputs = [
    deploymentPackageChubVersion,
    deploymentPackageRuntimeVersion,
    deploymentPackageWeixinVersion,
    deploymentPackageIncludeDevelopment,
  ].filter((item) => item instanceof HTMLInputElement);
  const render = (data) => {
    const configuration = data.configuration || {};
    inputs.forEach((input) => { input.disabled = false; });
    deploymentPackageCurrentAppVersion.textContent = `当前应用版本：v${data.app_version || "未知"}`;
    deploymentPackageOutput.textContent = data.output_directory || "输出目录暂时无法读取。";
    deploymentPackageChubVersion.value = configuration.chub_release_version || "";
    deploymentPackageRuntimeVersion.value = configuration.runtime_release_version || "";
    deploymentPackageWeixinVersion.value = configuration.weixin_release_version || "";
    deploymentPackageIncludeDevelopment.checked = configuration.include_development_sources === true;
    const operation = data.operation;
    if (operation?.status === "requested" || operation?.status === "started") {
      deploymentPackageBuild.disabled = true;
      setSettingsMessage(deploymentPackageMessage, operation.message || "正在构建正式部署包。", "");
      if (deploymentPackagePolling === null) {
        deploymentPackagePolling = window.setInterval(() => void load(), 1000);
      }
    } else {
      deploymentPackageBuild.disabled = false;
      if (deploymentPackagePolling !== null) {
        window.clearInterval(deploymentPackagePolling);
        deploymentPackagePolling = null;
      }
      if (operation?.status === "succeeded") {
        setSettingsMessage(deploymentPackageMessage, `${operation.message} ${operation.artifact_name || ""} · ${operation.artifact_size || 0} bytes`, "");
      } else if (operation?.status === "failed") {
        setSettingsMessage(deploymentPackageMessage, operation.message || "正式部署包构建失败。", "error");
      }
    }
  };
  const load = async () => {
    try { render(await fetchSettingsApi("/api/settings/deployment-package")); }
    catch (_error) { setSettingsMessage(deploymentPackageMessage, "暂时无法读取部署包发布配置。", "error"); }
  };
  deploymentPackageForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const configuration = {
      chub_release_version: deploymentPackageChubVersion.value.trim(),
      runtime_release_version: deploymentPackageRuntimeVersion.value.trim(),
      weixin_release_version: deploymentPackageWeixinVersion.value.trim(),
      include_development_sources: deploymentPackageIncludeDevelopment.checked,
    };
    inputs.forEach((input) => { input.disabled = true; });
    deploymentPackageBuild.disabled = true;
    try {
      await fetchSettingsApi("/api/settings/deployment-package", { method: "PUT", headers: settingsHeaders(true), body: JSON.stringify(configuration) });
      render(await fetchSettingsApi("/api/settings/deployment-package/build", { method: "POST", headers: settingsHeaders(true) }));
    } catch (_error) {
      inputs.forEach((input) => { input.disabled = false; });
      deploymentPackageBuild.disabled = false;
      setSettingsMessage(deploymentPackageMessage, "保存发布配置或启动构建失败。", "error");
    }
  });
  void load();
}

function initializeOpenClawSettings() {
  const presentation = {
    verified: ["已匹配", "success"],
    mismatch: ["不匹配", "failed"],
    unavailable: ["不可检查", "muted"],
    unknown: ["状态未知", "timeout"],
    declared: ["已登记", "muted"],
  };
  const createRow = (title, detail, state) => {
    const row = document.createElement("div");
    const copy = document.createElement("span");
    const heading = document.createElement("span");
    const strong = document.createElement("strong");
    const badge = document.createElement("span");
    const small = document.createElement("small");
    const [label, tone] = presentation[state] || presentation.unknown;
    row.className = "settings-utility-row settings-integration-row";
    heading.className = "settings-integration-title";
    strong.textContent = title;
    badge.className = `badge badge-${tone}`;
    badge.textContent = label;
    small.textContent = detail;
    heading.append(strong, badge);
    copy.append(heading, small);
    row.append(copy);
    return row;
  };
  const render = (data) => {
    if (!settingsOpenClawIntegrationList || !settingsOpenClawPatchList) return;
    settingsOpenClawIntegrationList.replaceChildren(
      createRow(
        "微信 ClawBot 适配器",
        `当前 ${data.weixin_adapter.version || "未知"} · 基线 ${data.weixin_adapter.expected_version || "未知"} · ${data.weixin_adapter.message}`,
        data.weixin_adapter.state,
      ),
      createRow(
        "Chub 插件",
        `当前 ${data.chub_plugin.version || "未知"} · 基线 ${data.chub_plugin.expected_version || "未知"} · ${data.chub_plugin.message}`,
        data.chub_plugin.state,
      ),
    );
    const patches = Array.isArray(data.patches) ? data.patches : [];
    settingsOpenClawPatchList.replaceChildren(...(patches.length
      ? patches.map((patch) => createRow(
        `${patch.identifier}${patch.version ? ` @ ${patch.version}` : ""}`,
        `${patch.scope === "runtime-dist" ? "OpenClaw 运行产物补丁" : "微信 ClawBot 适配器补丁"}；内容仅在重启与恢复时核验。`,
        patch.state,
      ))
      : [createRow("补丁状态", "当前组合不满足已验收基线，未读取补丁清单。", "unavailable")]));
    setSettingsMessage(settingsOpenClawIntegrationMessage, "");
  };
  const load = async () => {
    try {
      const data = await fetchSettingsApi("/api/openclaw/integration");
      render(data);
    } catch (_error) {
      settingsOpenClawIntegrationList?.replaceChildren(createRow(
        "集成状态",
        "暂时无法读取插件配置和补丁清单。",
        "unknown",
      ));
      settingsOpenClawPatchList?.replaceChildren();
      setSettingsMessage(
        settingsOpenClawIntegrationMessage,
        "请确认当前连接位于可信网络，并稍后刷新页面重试。",
        "error",
      );
    }
  };
  void load();
}

if (settingsPage === "appearance") {
  initializeAppearanceSettings();
} else if (settingsPage === "diagnostics") {
  initializeDiagnosticsSettings();
} else if (settingsPage === "runtime-detail") {
  loadRuntimeManagement();
  if (codexDefaultRuntimeImplementation instanceof HTMLSelectElement) {
    initializeSettingsChoicePickers();
    codexDefaultRuntimeImplementation.addEventListener(
      "change",
      () => void saveCodexDefaultRuntimeImplementation(),
    );
    void loadRuntimePlugins();
  }
} else if (settingsPage === "runtime") {
  void loadGeneralRuntimeSettings();
  initializeRuntimePluginInstall();
  initializeOrchestrationPluginInstall();
} else if (settingsPage === "task-orchestration") {
  window.initializeWorkspaceTaskOrchestration?.();
} else if (settingsPage === "openclaw") {
  initializeOpenClawSettings();
}

  window.disposeSettingsPage = () => {
    window.disposeWorkspaceTaskOrchestration?.();
    closeSettingsChoicePicker();
    settingsChoicePickerObservers.forEach((observer) => observer.disconnect());
    settingsChoicePickers.forEach(({ menu }) => menu.remove());
    document.removeEventListener("pointerdown", closePickerOnPointerDown);
    document.removeEventListener("keydown", closePickerOnEscape);
    window.removeEventListener("resize", closePickerOnResize);
    if (deploymentPackagePolling !== null) window.clearInterval(deploymentPackagePolling);
  };
};

window.initializeSettingsPage();
})();
