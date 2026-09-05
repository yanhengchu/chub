(() => {
"use strict";

window.initializeSettingsPage = () => {
  window.disposeSettingsPage?.();

const QUICK_INTERACTION_PAGE_SIZE_KEY = "hub.quickInteractionPageSize.v1";
const settingsPage = document.body.dataset.settingsPage || "";
const THEME_DETAILS_EXPANDED_KEY = "hub.themeDetailsExpanded.v1";
const settingsMessage = document.querySelector("#settings-message");
const quickInteractionPageSize = document.querySelector(
  "#quick-interaction-page-size",
);
const codexDefaultFullAccess = document.querySelector("#codex-default-full-access");
const runtimeManagementList = document.querySelector("#runtime-management-list");
const runtimeManagementDescription = document.querySelector(
  "#runtime-management-description",
);
const generalRuntimeSettingsPanel = document.querySelector(
  "#ai-runtime-general-settings",
);
const quickInteractionCore = window.QuickInteractionCore;
const codexSessionSettingsMessage = document.querySelector(
  "#codex-session-settings-message",
);
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

const settingsChoicePickers = new Map();
const settingsChoicePickerObservers = [];
let openSettingsChoicePicker = null;
const SETTINGS_PICKER_DESCRIPTIONS = Object.freeze({
  "quick-interaction-page-size": {
    5: "每次加载 5 条交互记录",
    10: "每次加载 10 条交互记录",
  },
});

function settingsPickerLabel(select) {
  return select.closest(".settings-field")?.querySelector("strong")?.textContent.trim()
    || "选择设置";
}

function settingsPickerOptionDescription(select, option) {
  return option.dataset.description
    || SETTINGS_PICKER_DESCRIPTIONS[select.id]?.[option.value]
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

function readQuickInteractionPageSize() {
  try {
    return localStorage.getItem(QUICK_INTERACTION_PAGE_SIZE_KEY) === "10"
      ? "10"
      : "5";
  } catch (_error) {
    return "5";
  }
}

function saveQuickInteractionPageSize(value) {
  const selected = value === "10" ? "10" : "5";
  try {
    localStorage.setItem(QUICK_INTERACTION_PAGE_SIZE_KEY, selected);
    quickInteractionPageSize.value = selected;
    settingsMessage.textContent = "";
    settingsMessage.className = "message";
  } catch (_error) {
    quickInteractionPageSize.value = readQuickInteractionPageSize();
    settingsMessage.textContent = "当前浏览器无法保存界面偏好。";
    settingsMessage.className = "message message-error";
  }
}

async function loadCodexSessionDefaults() {
  try {
    const response = await fetch("/api/codex/session-defaults", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || payload.success !== true) throw new Error("session_defaults_unavailable");
    codexDefaultFullAccess.checked = payload.data.permission_mode === "full-access";
  } catch (_error) {
    codexDefaultFullAccess.checked = true;
    codexSessionSettingsMessage.textContent = "暂时无法读取新建 Session 默认权限。";
    codexSessionSettingsMessage.className = "message message-error";
  }
}

function setRuntimeManagementDescription(text, kind = "") {
  if (!(runtimeManagementDescription instanceof HTMLElement)) return;
  runtimeManagementDescription.textContent = text;
  runtimeManagementDescription.classList.toggle("message-error", kind === "error");
}

function renderRuntimeManagement(data) {
  if (!(runtimeManagementList instanceof HTMLElement)) return;
  const runtimeId = runtimeManagementList.dataset.runtimeId || "";
  const runtimes = (Array.isArray(data?.runtimes) ? data.runtimes : [])
    .filter((runtime) => !runtimeId || runtime.runtime_id === runtimeId);
  runtimeManagementList.replaceChildren();
  for (const runtime of runtimes) {
    const field = document.createElement("label");
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
    copy.append(title, identifier, description);
    const control = document.createElement("span");
    control.className = "settings-switch";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = runtime.enabled === true;
    input.dataset.runtimeId = runtime.runtime_id;
    input.dataset.previousEnabled = String(runtime.enabled === true);
    input.setAttribute("aria-label", `${name.textContent} ${input.checked ? "正在接收新任务" : "已停止接收新任务"}`);
    input.addEventListener("change", () => void saveRuntimeEnablement(input));
    const track = document.createElement("span");
    track.className = "settings-switch-track";
    track.setAttribute("aria-hidden", "true");
    control.append(input, track);
    field.append(copy, control);
    runtimeManagementList.append(field);
  }
  if (data?.basic_mode === true) {
    setRuntimeManagementDescription("所有 AI Runtime 已停止接收新任务，Chub 当前处于基础功能模式。");
  } else {
    setRuntimeManagementDescription(
      "允许后可创建并提交该 Runtime 的 AI 任务；停止接入不会中断已受理任务。",
    );
  }
}

async function loadRuntimeManagement() {
  try {
    const response = await fetch("/api/codex/runtimes", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok || payload.success !== true) throw new Error("runtime_management_unavailable");
    renderRuntimeManagement(payload.data);
  } catch (_error) {
    runtimeManagementList?.replaceChildren();
    setRuntimeManagementDescription("暂时无法读取 AI Runtime 状态。", "error");
  }
}

function runtimeSettingOptions(field, catalog, values) {
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
    const form = document.createElement("form");
    form.className = "runtime-settings-form";
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
    message.textContent = "暂时无法读取 AI Runtime 通用配置。";
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

async function saveCodexSessionDefaults() {
  const previous = codexDefaultFullAccess.checked;
  codexDefaultFullAccess.disabled = true;
  try {
    const response = await fetch("/api/codex/session-defaults", {
      method: "PUT",
      headers: settingsHeaders(true),
      body: JSON.stringify({
        permission_mode: codexDefaultFullAccess.checked ? "full-access" : "read-only",
      }),
    });
    const payload = await response.json();
    if (!response.ok || payload.success !== true) throw new Error("session_defaults_update_failed");
    codexSessionSettingsMessage.textContent = "";
    codexSessionSettingsMessage.className = "message";
  } catch (_error) {
    codexDefaultFullAccess.checked = previous;
    codexSessionSettingsMessage.textContent = "新建 Session 默认权限保存失败，请稍后重试。";
    codexSessionSettingsMessage.className = "message message-error";
  } finally {
    codexDefaultFullAccess.disabled = false;
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
} else if (settingsPage === "runtime") {
  void loadGeneralRuntimeSettings();
} else if (settingsPage === "task-orchestration") {
  window.initializeWorkspaceTaskOrchestration?.();
} else if (settingsPage === "session-defaults") {
  quickInteractionPageSize.value = readQuickInteractionPageSize();
  initializeSettingsChoicePickers();
  quickInteractionPageSize.addEventListener("change", () => saveQuickInteractionPageSize(quickInteractionPageSize.value));
  codexDefaultFullAccess.addEventListener("change", saveCodexSessionDefaults);
  loadCodexSessionDefaults();
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
  };
};

window.initializeSettingsPage();
})();
