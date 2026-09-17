(() => {
"use strict";

window.initializeSettingsPage = () => {
  window.disposeSettingsPage?.();

const settingsPage = document.body.dataset.settingsPage || "";
const THEME_DETAILS_EXPANDED_KEY = "hub.themeDetailsExpanded.v1";
const settingsMessage = document.querySelector("#settings-message");
const generalRuntimeSettingsPanel = document.querySelector(
  "#ai-runtime-general-settings",
);
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
const deploymentPackageCurrentAppVersion = document.querySelector("#deployment-package-current-app-version");
const deploymentPackageBuild = document.querySelector("#deployment-package-build");
const deploymentPackageChubVersion = document.querySelector("#deployment-package-chub-version");
const deploymentPackageIncludeDevelopment = document.querySelector("#deployment-package-include-development");
const deploymentPackageReleaseNote = document.querySelector("#deployment-package-release-note");
const deploymentPackageOpenOutput = document.querySelector("#deployment-package-open-output");
let deploymentPackagePolling = null;

const settingsChoicePickers = new Map();
const settingsChoicePickerObservers = [];
let openSettingsChoicePicker = null;
function settingsPickerLabel(select) {
  return select.getAttribute("aria-label")
    || select.closest(".settings-field")?.querySelector("strong")?.textContent.trim()
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


let codexRuntimeSaving = false;

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
  return item.implementation_id === "codex-runtime-dev"
    ? `${name} · 开发实现`
    : formalImplementationTitle(name, item.version);
}

function versionDescription(item) {
  const version = formalVersion(item.version);
  return item.implementation_id === "codex-runtime-dev"
    ? "使用仓库固定的开发实现；仅影响之后新建的 Session 和任务。"
    : `使用正式插件包 ${version}；仅影响之后新建的 Session 和任务。`;
}

function renderCodexRuntimeVersions(implementations) {
  const versions = Array.isArray(implementations?.implementations)
    ? implementations.implementations
    : [];
  if (codexDefaultRuntimeImplementation instanceof HTMLSelectElement) {
    const availableVersions = versions.filter(
      (item) => item.imported !== false && item.healthy === true,
    );
    const selectedVersion = availableVersions.find((item) => item.is_default === true)
      || availableVersions[0];
    codexDefaultRuntimeImplementation.replaceChildren();
    availableVersions.forEach((item) => {
        const option = document.createElement("option");
        option.value = item.implementation_id;
        option.textContent = versionTitle(item);
        option.dataset.description = versionDescription(item);
        option.selected = item === selectedVersion;
        codexDefaultRuntimeImplementation.append(option);
      });
    codexDefaultRuntimeImplementation.disabled = codexRuntimeSaving
      || !selectedVersion
      || selectedVersion.enabled !== true;
  }
}

async function saveCodexDefaultRuntimeImplementation() {
  if (!(codexDefaultRuntimeImplementation instanceof HTMLSelectElement) || codexRuntimeSaving) return;
  const implementationId = codexDefaultRuntimeImplementation.value;
  if (!implementationId) return;
  codexRuntimeSaving = true;
  renderCodexRuntimeVersions();
  setCodexRuntimeSettingsMessage("");
  try {
    await fetchSettingsApi("/api/ai/runtime-implementations/default", {
      method: "PUT",
      body: JSON.stringify({ implementation_id: implementationId }),
      headers: { "Content-Type": "application/json" },
    });
    await loadRuntimePlugins();
  } catch (error) {
    setCodexRuntimeSettingsMessage(
      error instanceof Error ? error.message : "默认 Runtime 版本未能更新。",
      "error",
    );
  } finally {
    codexRuntimeSaving = false;
    await loadRuntimePlugins();
  }
}

async function loadRuntimePlugins() {
  try {
    const implementations = await fetchSettingsApi("/api/ai/runtime-implementations");
    renderCodexRuntimeVersions(implementations);
    setCodexRuntimeSettingsMessage("");
  } catch (_error) {
    setCodexRuntimeSettingsMessage("暂时无法读取 Codex Runtime 版本状态。", "error");
  }
}

function runtimeSettingOptions(field, catalog, values) {
  if (Array.isArray(field.options) && field.options.length) {
    return field.options;
  }
  if (field.id === "session-default-permission") {
    return (quickInteractionCore?.quickSessionPermissionOptions || [])
      .filter((option) => option.value !== "ask");
  }
  if (field.id === "session-default-model") {
    return quickInteractionCore?.quickSessionModelOptions(
      catalog,
      values["session-default-model"] === "__default__" ? "" : values["session-default-model"],
    ).map((option) => ({
      ...option,
      value: option.value || "__default__",
    })) || [];
  }
  if (field.id === "session-default-reasoning") {
    return quickInteractionCore?.quickSessionReasoningOptions(
      catalog,
      values["session-default-model"] === "__default__" ? "" : values["session-default-model"],
      values["session-default-reasoning"] === "__default__" ? "" : values["session-default-reasoning"],
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
  const showSectionMetadata = sections.length > 1;
  for (const section of sections) {
    if (showSectionMetadata) {
      const heading = document.createElement("h3");
      heading.textContent = section.title;
      generalRuntimeSettingsPanel.append(heading);
      if (section.description) {
        const description = document.createElement("p");
        description.className = "settings-subsection-description";
        description.textContent = section.description;
        generalRuntimeSettingsPanel.append(description);
      }
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
        if (field.id === "session-default-model") {
          const reasoning = form.querySelector("[data-runtime-setting='session-default-reasoning']");
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
    deploymentPackageIncludeDevelopment,
    deploymentPackageReleaseNote,
  ].filter((item) => item instanceof HTMLInputElement || item instanceof HTMLTextAreaElement);
  let noteDirty = false;
  let latestGeneration = null;
  let latestOperation = null;

  const generationActive = () => (
    latestGeneration?.status === "requested" || latestGeneration?.status === "running"
  );
  const publishActive = () => (
    latestOperation?.status === "requested" || latestOperation?.status === "started"
  );
  const generationRequired = () => (
    deploymentPackageReleaseNote.value.trim() === ""
    || (latestGeneration?.status === "stale" && !noteDirty)
  );
  const updateAction = () => {
    if (!(deploymentPackageBuild instanceof HTMLButtonElement)) return;
    const busy = publishActive() || generationActive();
    deploymentPackageBuild.disabled = busy;
    if (publishActive()) {
      deploymentPackageBuild.textContent = "正在发布";
    } else if (generationActive()) {
      deploymentPackageBuild.textContent = "正在生成";
    } else {
      deploymentPackageBuild.textContent = generationRequired()
        ? "生成发版说明"
        : "发布版本";
    }
  };
  const updatePolling = () => {
    if (publishActive() || generationActive()) {
      if (deploymentPackagePolling === null) {
        deploymentPackagePolling = window.setInterval(() => void load(), 1000);
      }
    } else if (deploymentPackagePolling !== null) {
      window.clearInterval(deploymentPackagePolling);
      deploymentPackagePolling = null;
    }
  };
  const render = (data) => {
    const configuration = data.configuration || {};
    latestOperation = data.operation || null;
    latestGeneration = data.release_note_generation || null;
    const busy = publishActive() || generationActive();
    inputs.forEach((input) => { input.disabled = busy; });
    const sourceVersions = data.source_versions || {};
    deploymentPackageCurrentAppVersion.textContent = `已提交版本：Chub v${sourceVersions.chub || "未知"}；Runtime v${sourceVersions.runtime || "未知"}；微信编排 v${sourceVersions.weixin || "未知"}。发布版本必须一致。`;
    deploymentPackageChubVersion.value = configuration.chub_release_version || "";
    deploymentPackageIncludeDevelopment.checked = configuration.include_development_sources === true;
    deploymentPackageReleaseNote.value = configuration.release_note || "";
    noteDirty = false;
    updateAction();
    updatePolling();
    if (publishActive()) {
      setSettingsMessage(deploymentPackageMessage, latestOperation.message || "正在发布版本。", "");
      return;
    }
    if (generationActive() || latestGeneration?.status === "failed" || latestGeneration?.status === "stale") {
      setSettingsMessage(deploymentPackageMessage, latestGeneration?.message || "正在生成发版说明。", latestGeneration?.status === "failed" ? "error" : "");
      return;
    }
    if (latestGeneration?.status === "succeeded") {
      setSettingsMessage(deploymentPackageMessage, latestGeneration.message || "发版说明已生成，可编辑后发布。", "");
    }
    if (latestOperation?.status === "succeeded") {
      const moduleSummary = Array.isArray(latestOperation.bundled_modules)
        ? latestOperation.bundled_modules.map((module) => {
          const identity = module.implementation_id || module.module_id || "未知模块";
          return `${identity} v${module.version || "未知"} · ${module.sha256 || "无摘要"}`;
        }).join("\n")
        : "";
      const details = [
        latestOperation.artifact_name ? `${latestOperation.artifact_name} · ${latestOperation.artifact_size || 0} bytes` : "",
        latestOperation.build_id ? `构建标识：${latestOperation.build_id}` : "",
        latestOperation.built_at ? `构建时间：${new Date(latestOperation.built_at).toLocaleString()}` : "",
        latestOperation.sha256 ? `SHA-256：${latestOperation.sha256}` : "",
        latestOperation.git_commit ? `提交：${latestOperation.git_commit}` : "",
        latestOperation.tag_name ? `本地 tag：${latestOperation.tag_name}` : "",
        latestOperation.release_record_name ? `发版记录：${latestOperation.release_record_name}` : "",
        latestOperation.release_note ? `发版说明：\n${latestOperation.release_note}` : "",
        moduleSummary ? `随包模块：\n${moduleSummary}` : "",
      ].filter(Boolean).join("\n");
      setSettingsMessage(deploymentPackageMessage, ["最近一次成功发布的产物如下。", details].filter(Boolean).join("\n"), "");
    } else if (latestOperation?.status === "failed") {
      setSettingsMessage(deploymentPackageMessage, latestOperation.message || "版本发布失败。", "error");
    }
  };
  const load = async () => {
    try { render(await fetchSettingsApi("/api/settings/deployment-package")); }
    catch (_error) { setSettingsMessage(deploymentPackageMessage, "暂时无法读取版本发布配置。", "error"); }
  };
  const runAction = async () => {
    const generating = generationRequired();
    inputs.forEach((input) => { input.disabled = true; });
    deploymentPackageBuild.disabled = true;
    try {
      if (generating) {
        render(await fetchSettingsApi("/api/settings/deployment-package/release-note", {
          method: "POST",
          headers: settingsHeaders(true),
          body: JSON.stringify({
            release_version: deploymentPackageChubVersion.value.trim(),
            include_development_sources: deploymentPackageIncludeDevelopment.checked,
          }),
        }));
        return;
      }
      const configuration = {
        release_version: deploymentPackageChubVersion.value.trim(),
        include_development_sources: deploymentPackageIncludeDevelopment.checked,
        release_note: deploymentPackageReleaseNote.value.trim(),
      };
      await fetchSettingsApi("/api/settings/deployment-package", { method: "PUT", headers: settingsHeaders(true), body: JSON.stringify(configuration) });
      render(await fetchSettingsApi("/api/settings/deployment-package/build", { method: "POST", headers: settingsHeaders(true) }));
    } catch (error) {
      inputs.forEach((input) => { input.disabled = false; });
      updateAction();
      setSettingsMessage(deploymentPackageMessage, error instanceof Error ? error.message : "版本发布操作未能完成。", "error");
    }
  };
  deploymentPackageForm.addEventListener("submit", (event) => {
    event.preventDefault();
    void runAction();
  });
  deploymentPackageBuild.addEventListener("click", () => void runAction());
  deploymentPackageReleaseNote.addEventListener("input", () => {
    noteDirty = true;
    updateAction();
  });
  deploymentPackageOpenOutput.addEventListener("click", async () => {
    deploymentPackageOpenOutput.disabled = true;
    try {
      await fetchSettingsApi("/api/settings/deployment-package/open-output", {
        method: "POST",
        headers: settingsHeaders(true),
      });
      setSettingsMessage(deploymentPackageMessage, "已请求使用本机文件管理器打开发布产物目录。", "");
    } catch (error) {
      setSettingsMessage(deploymentPackageMessage, error instanceof Error ? error.message : "无法打开本机发版产物目录。", "error");
    } finally {
      deploymentPackageOpenOutput.disabled = false;
    }
  });
  void load();
}

function initializeOpenClawSettings() {
  const presentation = {
    verified: ["已匹配", "success"],
    mismatch: ["不匹配", "failed"],
    unavailable: ["不可检查", "muted"],
    not_installed: ["未安装", "muted"],
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
  const render = (status, data) => {
    if (!settingsOpenClawIntegrationList || !settingsOpenClawPatchList) return;
    if (!status?.installed) {
      settingsOpenClawIntegrationList.replaceChildren(createRow(
        "OpenClaw",
        status?.message || "当前节点未安装 OpenClaw。",
        "not_installed",
      ));
      settingsOpenClawPatchList.replaceChildren();
      setSettingsMessage(settingsOpenClawIntegrationMessage, "");
      return;
    }
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
      const status = await fetchSettingsApi("/api/openclaw/status");
      if (!status?.installed) {
        render(status, null);
        return;
      }
      const data = await fetchSettingsApi("/api/openclaw/integration");
      render(status, data);
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
  window.initializeWorkspacePluginLifecycle?.();
  if (codexDefaultRuntimeImplementation instanceof HTMLSelectElement) {
    initializeSettingsChoicePickers();
    codexDefaultRuntimeImplementation.addEventListener(
      "change",
      () => void saveCodexDefaultRuntimeImplementation(),
    );
    void loadRuntimePlugins();
  }
} else if (settingsPage === "session") {
  void loadGeneralRuntimeSettings();
  window.initializeSessionVisibilitySettings?.();
} else if (settingsPage === "runtime") {
  window.initializeWorkspacePluginLifecycle?.();
} else if (settingsPage === "task-orchestration") {
  window.initializeWorkspaceTaskOrchestration?.();
  window.initializeWorkspacePluginLifecycle?.();
} else if (settingsPage === "deliveryline") {
  initializeSettingsChoicePickers();
  window.initializeWorkspacePluginLifecycle?.();
} else if (settingsPage === "openclaw") {
  initializeOpenClawSettings();
}

  window.disposeSettingsPage = () => {
    window.disposeSessionVisibilitySettings?.();
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
