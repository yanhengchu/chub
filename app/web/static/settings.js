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
const runtimeDefaultImplementation = document.querySelector(
  "#runtime-default-implementation",
);
const runtimeSettingsMessage = document.querySelector(
  "#runtime-settings-message",
);
const runtimePluginStatus = document.querySelector("#runtime-plugin-status");
const settingsRuntimeId = document.body.dataset.settingsRuntimeId || "";
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
const deploymentPackageReleaseDialog = document.querySelector("#deployment-package-release-dialog");
const deploymentPackageReleaseDialogClose = document.querySelector("#deployment-package-release-dialog-close");
const deploymentPackageReleaseDialogCancel = document.querySelector("#deployment-package-release-dialog-cancel");
const deploymentPackageReleaseDialogConfirm = document.querySelector("#deployment-package-release-dialog-confirm");
const deploymentPackageReleaseDialogDetails = document.querySelector("#deployment-package-release-dialog-details");
let deploymentPackagePolling = null;
let disposeOpenClawSettings = () => {};

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
  const value = trigger.querySelector("[data-settings-picker-value]");
  if (select.hasAttribute("data-settings-picker-multiline")) {
    value.classList.add("settings-choice-picker-value-multiline");
    const title = document.createElement("strong");
    title.textContent = selected?.textContent || "";
    const description = document.createElement("small");
    description.textContent = selected?.dataset.description || "";
    value.replaceChildren(title, description);
  } else {
    value.classList.remove("settings-choice-picker-value-multiline");
    value.textContent = selected?.textContent || "";
  }
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


let runtimeImplementationSaving = false;

function setRuntimeSettingsMessage(text, kind = "") {
  if (!(runtimeSettingsMessage instanceof HTMLElement)) return;
  runtimeSettingsMessage.textContent = text;
  runtimeSettingsMessage.className = kind === "error" ? "message message-error" : "message";
}

function formalVersion(version) {
  const normalized = typeof version === "string" ? version.trim().replace(/^v/i, "") : "";
  return normalized ? `v${normalized}` : "未知版本";
}

function formalImplementationTitle(name, version) {
  return `${name || "实现"} · 正式版 ${formalVersion(version)}`;
}

function versionTitle(item) {
  const name = item.name || "Runtime";
  return item.version === "dev"
    ? `${name} · 开发实现`
    : formalImplementationTitle(name, item.version);
}

function versionDescription(item) {
  const version = formalVersion(item.version);
  return item.version === "dev"
    ? "使用仓库固定的开发实现；仅影响之后新建的 Session 和任务。"
    : `使用正式插件包 ${version}；仅影响之后新建的 Session 和任务。`;
}

function renderRuntimePluginStatus(data, errorMessage = "") {
  if (!(runtimePluginStatus instanceof HTMLElement)) return;
  const implementations = Array.isArray(data?.implementations) ? data.implementations : [];
  const selected = implementations.find((item) => item.implementation_id === data?.default_implementation_id)
    || implementations.find((item) => item.enabled === true)
    || implementations[0];
  const copy = document.createElement("div");
  const title = document.createElement("strong");
  const detail = document.createElement("span");
  const row = document.createElement("div");
  row.className = "workstation-status-row";
  copy.className = "workstation-status-copy";
  title.textContent = "插件状态";
  detail.id = "runtime-plugin-status-detail";
  detail.className = "workstation-status-detail";
  if (errorMessage) {
    detail.textContent = errorMessage;
    detail.classList.add("workstation-status-detail-failed");
  } else if (!selected) {
    detail.textContent = "当前 Runtime 尚无可用插件版本。";
    detail.classList.add("workstation-status-detail-warning");
  } else {
    const source = selected.version === "dev" ? "开发实现" : `正式版 v${formalVersion(selected.version)}`;
    const state = !selected.imported
      ? "未导入"
      : !selected.enabled
        ? "已停用"
        : selected.healthy
          ? "已启用，可用"
          : `已启用，不可用${selected.reason ? `：${selected.reason}` : ""}`;
    detail.textContent = `${selected.name} · ${source} · ${state}。`;
    detail.classList.add(
      !selected.imported || !selected.enabled
        ? "workstation-status-detail-warning"
        : selected.healthy
          ? "workstation-status-detail-success"
          : "workstation-status-detail-failed",
    );
  }
  copy.append(title, detail);
  row.append(copy);
  runtimePluginStatus.replaceChildren(row);
}

function renderRuntimeImplementations(implementations) {
  const versions = Array.isArray(implementations?.implementations)
    ? implementations.implementations
    : [];
  if (runtimeDefaultImplementation instanceof HTMLSelectElement) {
    const availableVersions = versions.filter(
      (item) => item.imported !== false && item.healthy === true,
    );
    const selectedVersion = availableVersions.find((item) => item.is_default === true)
      || availableVersions[0];
    runtimeDefaultImplementation.replaceChildren();
    availableVersions.forEach((item) => {
        const option = document.createElement("option");
        option.value = item.implementation_id;
        option.textContent = versionTitle(item);
        option.dataset.description = versionDescription(item);
        option.selected = item === selectedVersion;
        runtimeDefaultImplementation.append(option);
      });
    runtimeDefaultImplementation.disabled = runtimeImplementationSaving
      || !selectedVersion
      || selectedVersion.enabled !== true;
    const picker = settingsChoicePickers.get(runtimeDefaultImplementation);
    if (picker) renderSettingsChoicePicker(picker);
  }
}

async function saveRuntimeDefaultImplementation() {
  if (!(runtimeDefaultImplementation instanceof HTMLSelectElement) || runtimeImplementationSaving) return;
  const implementationId = runtimeDefaultImplementation.value;
  if (!implementationId) return;
  runtimeImplementationSaving = true;
  renderRuntimeImplementations();
  setRuntimeSettingsMessage("");
  try {
    const query = settingsRuntimeId
      ? `?runtime_id=${encodeURIComponent(settingsRuntimeId)}`
      : "";
    await fetchSettingsApi(`/api/ai/runtime-implementations/default${query}`, {
      method: "PUT",
      body: JSON.stringify({ implementation_id: implementationId }),
      headers: { "Content-Type": "application/json" },
    });
    await loadRuntimePlugins();
  } catch (error) {
    setRuntimeSettingsMessage(
      error instanceof Error ? error.message : "默认 Runtime 版本未能更新。",
      "error",
    );
  } finally {
    runtimeImplementationSaving = false;
    await loadRuntimePlugins();
  }
}

async function loadRuntimePlugins() {
  if (!settingsRuntimeId) return;
  try {
    const implementations = await fetchSettingsApi(
      `/api/ai/runtime-implementations?runtime_id=${encodeURIComponent(settingsRuntimeId)}`,
    );
    renderRuntimePluginStatus(implementations);
    renderRuntimeImplementations(implementations);
    setRuntimeSettingsMessage("");
  } catch (_error) {
    renderRuntimePluginStatus(null, "暂时无法读取 Runtime 插件状态。");
    setRuntimeSettingsMessage("暂时无法读取 Runtime 版本状态。", "error");
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
  const internalVisibilitySetting = generalRuntimeSettingsPanel.querySelector(
    "#internal-session-visibility-setting",
  );
  const internalVisibilityFeedback = generalRuntimeSettingsPanel.querySelector(
    "#internal-session-visibility-feedback",
  );
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
        if (field.id === "session-default-runtime") {
          // Model identifiers and reasoning levels belong to the selected
          // Runtime. Do not carry a Codex or another Runtime's value across
          // this boundary; the successful save reloads the target catalog.
          const model = form.querySelector("[data-runtime-setting='session-default-model']");
          const reasoning = form.querySelector("[data-runtime-setting='session-default-reasoning']");
          if (model instanceof HTMLSelectElement) model.value = "__default__";
          if (reasoning instanceof HTMLSelectElement) reasoning.value = "__default__";
        } else if (field.id === "session-default-model") {
          const reasoning = form.querySelector("[data-runtime-setting='session-default-reasoning']");
          if (reasoning instanceof HTMLSelectElement) reasoning.value = "__default__";
        }
        void saveGeneralRuntimeSettings(form, input, message);
      });
      form.append(label);
    }
    if (section.id === "session-defaults" && internalVisibilitySetting instanceof HTMLElement) {
      form.append(internalVisibilitySetting);
    }
    const message = document.createElement("p");
    message.className = "message";
    message.setAttribute("aria-live", "polite");
    form.append(message);
    generalRuntimeSettingsPanel.append(form);
  }
  if (internalVisibilityFeedback instanceof HTMLElement) {
    generalRuntimeSettingsPanel.append(internalVisibilityFeedback);
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
    const internalVisibilitySetting = generalRuntimeSettingsPanel.querySelector(
      "#internal-session-visibility-setting",
    );
    const internalVisibilityFeedback = generalRuntimeSettingsPanel.querySelector(
      "#internal-session-visibility-feedback",
    );
    generalRuntimeSettingsPanel.replaceChildren();
    const message = document.createElement("p");
    message.className = "message message-error";
    message.textContent = "暂时无法读取 Runtime 默认项。";
    generalRuntimeSettingsPanel.append(message);
    if (internalVisibilitySetting instanceof HTMLElement) {
      generalRuntimeSettingsPanel.append(internalVisibilitySetting);
    }
    if (internalVisibilityFeedback instanceof HTMLElement) {
      generalRuntimeSettingsPanel.append(internalVisibilityFeedback);
    }
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

function initializePromptOptimizerSettings() {
  const mode = document.querySelector("#prompt-optimizer-mode");
  const pluginStatus = document.querySelector("#prompt-optimizer-plugin-status");
  const message = document.querySelector("#prompt-optimizer-settings-message");
  if (!(mode instanceof HTMLSelectElement)) return;

  const endpoint = "/api/plugins/chub-task-prompt-optimizer/settings";
  let current = null;
  let busy = false;
  const render = (data) => {
    current = data;
    const autoOption = mode.querySelector('option[value="auto"]');
    if (autoOption instanceof HTMLOptionElement) {
      autoOption.disabled = data.auto_available !== true;
      autoOption.textContent = "auto";
      autoOption.dataset.description = data.auto_available === true
        ? "AI Agent 生成优化后的提示词"
        : data.auto_unavailable_reason || "AI 优化尚未就绪";
    }
    mode.value = data.mode;
    mode.disabled = busy;
    const picker = settingsChoicePickers.get(mode);
    if (picker) renderSettingsChoicePicker(picker);
    if (pluginStatus) {
      const status = data.plugin_enabled ? "插件已启用" : "插件已停用，设置保留但暂不生效";
      pluginStatus.textContent = `${status}；${data.entry_loaded ? "入口已装配" : "入口尚未装配"}。`;
    }
  };
  const load = async () => {
    try {
      render(await fetchSettingsApi(endpoint));
      setSettingsMessage(message, "");
    } catch (error) {
      mode.disabled = true;
      setSettingsMessage(message, error instanceof Error ? error.message : "无法读取插件设置。", "error");
    }
  };
  mode.addEventListener("change", async () => {
    if (busy || !current) return;
    const previousMode = current.mode;
    const nextMode = mode.value;
    if (nextMode === previousMode) return;

    busy = true;
    mode.disabled = true;
    setSettingsMessage(message, "正在保存…");
    try {
      render(await fetchSettingsApi(endpoint, {
        method: "PUT",
        headers: settingsHeaders(true),
        body: JSON.stringify({ mode: nextMode }),
      }));
      setSettingsMessage(message, "");
    } catch (error) {
      render(current);
      setSettingsMessage(message, error instanceof Error ? error.message : "插件设置未能保存。", "error");
    } finally {
      busy = false;
      if (current) render(current);
    }
  });
  void load();
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
  let latestGeneration = null;
  let latestOperation = null;
  let latestSourceVersions = null;
  let latestAppVersion = null;
  let generationRequested = false;
  let generatedForVersion = null;
  const releaseNoteDraftToken = crypto.randomUUID();
  const releaseNoteDraftHeaders = (includeJson = false) => ({
    ...settingsHeaders(includeJson),
    "X-Chub-Release-Note-Draft-Token": releaseNoteDraftToken,
  });

  const generationActive = () => (
    latestGeneration?.status === "requested" || latestGeneration?.status === "running"
  );
  const publishActive = () => (
    latestOperation?.status === "requested" || latestOperation?.status === "started"
  );
  const generationRequired = () => deploymentPackageReleaseNote.value.trim() === "";
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
    latestSourceVersions = data.source_versions || null;
    latestAppVersion = typeof data.app_version === "string" ? data.app_version : null;
    const busy = publishActive() || generationActive();
    inputs.forEach((input) => { input.disabled = busy; });
    const sourceVersions = data.source_versions || {};
    const runtimeVersion = latestAppVersion ? `当前运行 Web：v${latestAppVersion}。` : "";
    const reloadHint = latestAppVersion && sourceVersions.chub && latestAppVersion !== sourceVersions.chub
      ? "运行中的 Web 尚未加载当前源码；如需切换运行版本，待 Worker 空闲后先 reload Worker，再重启 Web。"
      : "";
    deploymentPackageCurrentAppVersion.textContent = `当前源码：Chub v${sourceVersions.chub || "未知"}；Runtime v${sourceVersions.runtime || "未知"}。${runtimeVersion}${reloadHint} 可同版本重发，或输入更高的 MAJOR.MINOR.PATCH 版本。`;
    deploymentPackageChubVersion.value = configuration.chub_release_version || "";
    deploymentPackageIncludeDevelopment.checked = configuration.include_development_sources === true;
    if (generationRequested
      && latestGeneration?.status === "succeeded"
      && typeof data.generated_release_note === "string"
      && data.generated_release_note.trim()) {
      deploymentPackageReleaseNote.value = data.generated_release_note;
      generatedForVersion = latestGeneration.target_version || deploymentPackageChubVersion.value.trim();
    }
    updateAction();
    updatePolling();
    if (publishActive()) {
      setSettingsMessage(deploymentPackageMessage, latestOperation.message || "正在发布版本。", "");
      return;
    }
    if (generationActive() || latestGeneration?.status === "failed") {
      setSettingsMessage(deploymentPackageMessage, latestGeneration?.message || "正在生成发版说明。", latestGeneration?.status === "failed" ? "error" : "");
      return;
    }
    if (generationRequested && latestGeneration?.status === "succeeded") {
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
      setSettingsMessage(
        deploymentPackageMessage,
        [
          "最近一次成功发布的产物如下。正式包不会自动更新当前运行中的 Web 或 Quick Worker，不会推送本地 Git 提交或 tag，也不会导入或启用随包插件 ZIP；这些后续动作由维护者按需单独执行。",
          details,
        ].filter(Boolean).join("\n"),
        "",
      );
    } else if (latestOperation?.status === "failed") {
      setSettingsMessage(deploymentPackageMessage, latestOperation.message || "版本发布失败。", "error");
    }
  };
  const load = async () => {
    try {
      render(await fetchSettingsApi(
        "/api/settings/deployment-package",
        { headers: releaseNoteDraftHeaders() },
      ));
    }
    catch (_error) { setSettingsMessage(deploymentPackageMessage, "暂时无法读取版本发布配置。", "error"); }
  };
  const runAction = async (confirmed = false) => {
    const generating = generationRequired();
    if (!generating && !confirmed && deploymentPackageReleaseDialog instanceof HTMLDialogElement) {
      const target = deploymentPackageChubVersion.value.trim();
      if (!target) {
        setSettingsMessage(deploymentPackageMessage, "请填写发布版本。", "error");
        return;
      }
      deploymentPackageBuild.disabled = true;
      try {
        const preview = await fetchSettingsApi("/api/settings/deployment-package/release-preview", {
          method: "POST",
          headers: settingsHeaders(true),
          body: JSON.stringify({ release_version: target }),
        });
        if (deploymentPackageReleaseDialogDetails instanceof HTMLElement) {
          const shortCommit = (commit) => typeof commit === "string" ? commit.slice(0, 12) : "尚未创建";
          const tagBefore = preview.current_tag_commit
            ? `当前 ${preview.tag_name}：${shortCommit(preview.current_tag_commit)}`
            : `当前 ${preview.tag_name}：尚不存在`;
          if (preview.release_kind === "same_version_republish") {
            deploymentPackageReleaseDialogDetails.textContent = `目标版本：v${target}（同版本重发）。当前 HEAD：${shortCommit(preview.head_commit)}。${tagBefore}。发布后 ${preview.tag_name} 将指向当前 HEAD；不会创建版本提交。`;
          } else if (preview.release_kind === "version_upgrade") {
            deploymentPackageReleaseDialogDetails.textContent = `目标版本：v${target}（升级发布）。当前 HEAD：${shortCommit(preview.head_commit)}。${tagBefore}。发布后 ${preview.tag_name} 将指向本次新建的版本提交。`;
          } else {
            const mismatches = Array.isArray(preview.mismatched_declarations)
              && preview.mismatched_declarations.length
              ? `不一致项：${preview.mismatched_declarations.join("、")}。`
              : "目标不满足发布条件。";
            deploymentPackageReleaseDialogDetails.textContent = `目标版本：v${target}。${mismatches} 服务端会在提交前执行最终校验，不会生成不合规发布。`;
          }
        }
        deploymentPackageReleaseDialog.showModal();
      } catch (error) {
        setSettingsMessage(deploymentPackageMessage, error instanceof Error ? error.message : "无法读取本次发布预览。", "error");
      } finally {
        updateAction();
      }
      return;
    }
    inputs.forEach((input) => { input.disabled = true; });
    deploymentPackageBuild.disabled = true;
    try {
      if (generating) {
        generationRequested = true;
        generatedForVersion = null;
        deploymentPackageReleaseNote.value = "";
        render(await fetchSettingsApi("/api/settings/deployment-package/release-note", {
          method: "POST",
          headers: releaseNoteDraftHeaders(true),
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
      render(await fetchSettingsApi("/api/settings/deployment-package/build", {
        method: "POST",
        headers: settingsHeaders(true),
        body: JSON.stringify(configuration),
      }));
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
  deploymentPackageReleaseDialogClose?.addEventListener("click", () => deploymentPackageReleaseDialog?.close());
  deploymentPackageReleaseDialogCancel?.addEventListener("click", () => deploymentPackageReleaseDialog?.close());
  deploymentPackageReleaseDialogConfirm?.addEventListener("click", () => {
    deploymentPackageReleaseDialog?.close();
    void runAction(true);
  });
  deploymentPackageChubVersion.addEventListener("input", () => {
    if (generatedForVersion && deploymentPackageChubVersion.value.trim() !== generatedForVersion) {
      generatedForVersion = null;
      deploymentPackageReleaseNote.value = "";
    }
    updateAction();
  });
  deploymentPackageReleaseNote.addEventListener("input", () => {
    generatedForVersion = null;
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
  const byId = (id) => document.getElementById(id);
  const elements = {
    detail: byId("settings-openclaw-detail"),
    start: byId("settings-openclaw-start"),
    restart: byId("settings-openclaw-restart"),
    weixinDetail: byId("settings-openclaw-weixin-detail"),
    bindWeixin: byId("settings-openclaw-bind-weixin"),
    runtimeMessage: byId("settings-openclaw-runtime-message"),
    weixinDialog: byId("settings-openclaw-weixin-dialog"),
    weixinClose: byId("settings-openclaw-weixin-close"),
    weixinAccountSummary: byId("settings-openclaw-weixin-account-summary"),
    weixinOwnerSummary: byId("settings-openclaw-weixin-owner-summary"),
    weixinQrPanel: byId("settings-openclaw-weixin-qr-panel"),
    weixinQr: byId("settings-openclaw-weixin-qr"),
    weixinVerifyForm: byId("settings-openclaw-weixin-verify-form"),
    weixinVerifyCode: byId("settings-openclaw-weixin-verify-code"),
    weixinMessage: byId("settings-openclaw-weixin-message"),
    weixinCancel: byId("settings-openclaw-weixin-cancel"),
    weixinStart: byId("settings-openclaw-weixin-start"),
  };
  if (!Object.values(elements).every((element) => element instanceof HTMLElement)) return;

  const activeLoginStates = new Set([
    "starting",
    "waiting_scan",
    "needs_verification",
    "confirming",
    "cancelling",
  ]);
  let disposed = false;
  let loading = false;
  let operating = false;
  let status = null;
  let login = null;
  let integration = null;
  let pollTimer = 0;
  let qrObjectUrl = "";
  let qrUpdatedAt = "";
  const requestAbortController = new AbortController();

  const setStatus = (target, text, kind = "muted") => {
    target.textContent = text;
    target.className = `workstation-status-detail workstation-status-detail-${kind}`;
  };

  const syncControls = () => {
    const gatewayReady = Boolean(status?.installed && status?.configured);
    const gatewayStopped = status?.state === "stopped";
    const gatewayRestartable = ["running", "degraded"].includes(status?.state);
    const activeLogin = activeLoginStates.has(login?.state);
    elements.start.hidden = !gatewayStopped;
    elements.restart.hidden = !gatewayRestartable;
    elements.start.disabled = loading || operating || !gatewayStopped;
    elements.restart.disabled = loading || operating || !gatewayReady || !gatewayRestartable;
    elements.bindWeixin.disabled = loading || operating || !gatewayReady || activeLogin || !login;
  };

  const renderRuntime = () => {
    const gatewayVersion = typeof status?.version === "string" && status.version
      ? `OpenClaw / Gateway v${status.version} · `
      : "";
    const gatewayDetail = status?.state === "running"
      ? "Gateway 运行正常并已通过连接探测。"
      : status?.message || "暂时无法读取 OpenClaw Gateway 状态。";
    const gatewayKind = status?.state === "running"
      ? "success"
      : ["degraded", "stopped", "unconfigured", "service_missing"].includes(status?.state)
        ? "warning"
        : ["unavailable", "unknown"].includes(status?.state) ? "failed" : "muted";
    setStatus(elements.detail, `${gatewayVersion}${gatewayDetail}`, gatewayKind);
    elements.weixinAccountSummary.textContent = status?.channel_message || "当前消息通道状态不可用。";
    elements.weixinOwnerSummary.textContent = status?.owner_message || "当前 Owner 授权状态不可用。";
    const activePresentation = {
      starting: ["正在准备微信绑定。", "warning"],
      waiting_scan: ["等待使用手机微信扫码。", "warning"],
      needs_verification: ["等待提交手机显示的验证码。", "warning"],
      confirming: ["正在确认微信连接。", "warning"],
      cancelling: ["正在取消微信绑定。", "warning"],
    }[login?.state];
    const channelPresentation = {
      running: [status?.channel_message, "success"],
      degraded: [status?.channel_message, "warning"],
      stopped: [status?.channel_message, "failed"],
      not_configured: [status?.channel_message, "muted"],
      unavailable: [status?.channel_message, "muted"],
      unknown: [status?.channel_message, "failed"],
    }[status?.channel_state];
    const [weixinDetail, weixinKind] = activePresentation
      || channelPresentation
      || ["暂时无法读取微信 ClawBot 状态。", "muted"];
    const adapterVersion = typeof integration?.weixin_adapter?.version === "string"
      && integration.weixin_adapter.version
      ? `微信 ClawBot v${integration.weixin_adapter.version} · `
      : "";
    setStatus(
      elements.weixinDetail,
      `${adapterVersion}${weixinDetail || "暂时无法读取微信 ClawBot 状态。"}`,
      weixinKind,
    );
    elements.bindWeixin.textContent = status?.channel_state === "running" ? "重新绑定微信" : "绑定微信";
    syncControls();
  };

  const releaseQr = () => {
    if (qrObjectUrl) URL.revokeObjectURL(qrObjectUrl);
    qrObjectUrl = "";
    qrUpdatedAt = "";
    elements.weixinQr.removeAttribute("src");
    elements.weixinQrPanel.hidden = true;
  };

  const stopPolling = () => {
    if (pollTimer) window.clearTimeout(pollTimer);
    pollTimer = 0;
  };

  const closeWeixinDialog = () => {
    stopPolling();
    releaseQr();
    elements.weixinVerifyForm.hidden = true;
    elements.weixinVerifyCode.value = "";
    if (elements.weixinDialog.open) elements.weixinDialog.close();
  };

  const loadQr = async (updatedAt) => {
    if (disposed || (qrObjectUrl && qrUpdatedAt === updatedAt)) return;
    try {
      const response = await fetch("/api/openclaw/weixin/login/qr", {
        cache: "no-store",
        signal: requestAbortController.signal,
      });
      if (disposed || !response.ok || !elements.weixinDialog.open) {
        throw new Error("weixin_qr_unavailable");
      }
      releaseQr();
      qrObjectUrl = URL.createObjectURL(await response.blob());
      qrUpdatedAt = updatedAt;
      elements.weixinQr.src = qrObjectUrl;
      elements.weixinQrPanel.hidden = false;
    } catch {
      if (!disposed) setSettingsMessage(elements.weixinMessage, "微信绑定二维码读取失败。", "error");
    }
  };

  const renderWeixinLogin = (nextLogin) => {
    const previousState = login?.state;
    login = nextLogin;
    const active = activeLoginStates.has(login.state);
    elements.weixinCancel.hidden = !active || login.state === "cancelling";
    elements.weixinStart.hidden = active;
    elements.weixinStart.textContent = status?.channel_state === "running" || login.state !== "idle"
      ? "重新生成二维码"
      : "生成二维码";
    elements.weixinVerifyForm.hidden = login.state !== "needs_verification";
    setSettingsMessage(
      elements.weixinMessage,
      login.message,
      login.state === "succeeded" ? "success" : login.state === "failed" ? "error" : "",
    );
    if (login.qr_available) void loadQr(login.updated_at);
    else releaseQr();
    renderRuntime();
    if (login.state === "succeeded" && previousState !== "succeeded") void load();
  };

  const pollWeixinLogin = async () => {
    stopPolling();
    if (disposed || !elements.weixinDialog.open) return;
    try {
      renderWeixinLogin(await fetchSettingsApi("/api/openclaw/weixin/login", { cache: "no-store" }));
      if (!disposed && activeLoginStates.has(login?.state)) {
        pollTimer = window.setTimeout(pollWeixinLogin, 1000);
      }
    } catch (error) {
      if (disposed) return;
      setSettingsMessage(
        elements.weixinMessage,
        error instanceof Error ? error.message : "微信绑定状态读取失败。",
        "error",
      );
      if (elements.weixinDialog.open) pollTimer = window.setTimeout(pollWeixinLogin, 2000);
    }
  };

  const load = async () => {
    loading = true;
    syncControls();
    try {
      const nextStatus = await fetchSettingsApi("/api/openclaw/status", { cache: "no-store" });
      if (disposed) return;
      status = nextStatus;
      if (status?.installed !== true) {
        login = null;
        integration = await fetchSettingsApi("/api/openclaw/integration", { cache: "no-store" });
        if (disposed) return;
        renderRuntime();
        render(status, integration);
        setSettingsMessage(elements.runtimeMessage, "");
        return;
      }
      const [nextLogin, nextIntegration] = await Promise.all([
        fetchSettingsApi("/api/openclaw/weixin/login", { cache: "no-store" }),
        fetchSettingsApi("/api/openclaw/integration", { cache: "no-store" }),
      ]);
      if (disposed) return;
      login = nextLogin;
      integration = nextIntegration;
      renderRuntime();
      render(status, integration);
      setSettingsMessage(elements.runtimeMessage, "");
    } catch (error) {
      if (disposed) return;
      const message = error instanceof Error ? error.message : "第三方服务状态读取失败。";
      setStatus(elements.detail, message, "failed");
      setStatus(elements.weixinDetail, message, "failed");
      setSettingsMessage(elements.runtimeMessage, message, "error");
    } finally {
      loading = false;
      syncControls();
    }
  };

  const controlGateway = async (action) => {
    operating = true;
    setSettingsMessage(elements.runtimeMessage, "");
    setStatus(
      elements.detail,
      action === "restart"
        ? "正在重启与恢复 OpenClaw Gateway，并确认 Gateway 与消息通道最终状态。"
        : "正在启动 OpenClaw Gateway，并确认最终状态。",
      "warning",
    );
    syncControls();
    try {
      status = await fetchSettingsApi(`/api/openclaw/${action}`, { method: "POST" });
      [login, integration] = await Promise.all([
        fetchSettingsApi("/api/openclaw/weixin/login", { cache: "no-store" }),
        fetchSettingsApi("/api/openclaw/integration", { cache: "no-store" }),
      ]);
      renderRuntime();
      render(status, integration);
    } catch (error) {
      const message = error instanceof Error ? error.message : "OpenClaw Gateway 操作失败。";
      setStatus(elements.detail, message, "failed");
      setSettingsMessage(elements.runtimeMessage, message, "error");
    } finally {
      operating = false;
      syncControls();
    }
  };

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
  const renderPatches = (patches, notInstalled) => {
    settingsOpenClawPatchList.replaceChildren(...(patches.length
      ? patches.map((patch) => createRow(
        `${patch.identifier}${patch.version ? ` @ ${patch.version}` : ""}`,
        notInstalled
          ? `${patch.scope === "runtime-dist" ? "OpenClaw 运行产物补丁" : "微信 ClawBot 适配器补丁"}；当前节点未安装 OpenClaw，尚未部署或核验。`
          : `${patch.scope === "runtime-dist" ? "OpenClaw 运行产物补丁" : "微信 ClawBot 适配器补丁"}；内容仅在重启与恢复时核验。`,
        patch.state,
      ))
      : [createRow("补丁状态", "暂时无法读取已登记的兼容补丁基线。", "unavailable")]));
  };
  const render = (status, data) => {
    if (!settingsOpenClawIntegrationList || !settingsOpenClawPatchList) return;
    if (!status?.installed) {
      settingsOpenClawIntegrationList.replaceChildren(createRow(
        "OpenClaw",
        status?.message || "当前节点未安装 OpenClaw。",
        "not_installed",
      ));
      renderPatches(Array.isArray(data?.patches) ? data.patches : [], true);
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
    renderPatches(patches, false);
    setSettingsMessage(settingsOpenClawIntegrationMessage, "");
  };
  elements.start.addEventListener("click", () => { void controlGateway("start"); });
  elements.restart.addEventListener("click", () => {
    void showConfirmationDialog({
      title: "重启与恢复 OpenClaw Gateway",
      body: "Gateway 和微信消息通道会短暂中断。Chub 将先检查固定插件和补丁基线，再确认 Gateway 与消息通道最终状态。",
      confirmLabel: "确认重启与恢复",
      tone: "secondary",
      closeOnConfirm: true,
      onConfirm: () => controlGateway("restart"),
    });
  });
  elements.bindWeixin.addEventListener("click", async () => {
    elements.weixinDialog.showModal();
    setSettingsMessage(elements.weixinMessage, "正在读取微信绑定状态…");
    await pollWeixinLogin();
  });
  elements.weixinClose.addEventListener("click", closeWeixinDialog);
  elements.weixinStart.addEventListener("click", async () => {
    elements.weixinStart.disabled = true;
    setSettingsMessage(elements.weixinMessage, "正在生成微信绑定二维码…");
    try {
      renderWeixinLogin(await fetchSettingsApi("/api/openclaw/weixin/login", { method: "POST" }));
      pollTimer = window.setTimeout(pollWeixinLogin, 500);
    } catch (error) {
      setSettingsMessage(
        elements.weixinMessage,
        error instanceof Error ? error.message : "微信绑定启动失败。",
        "error",
      );
    } finally {
      elements.weixinStart.disabled = false;
    }
  });
  elements.weixinCancel.addEventListener("click", async () => {
    elements.weixinCancel.disabled = true;
    try {
      renderWeixinLogin(await fetchSettingsApi("/api/openclaw/weixin/login", { method: "DELETE" }));
    } catch (error) {
      setSettingsMessage(
        elements.weixinMessage,
        error instanceof Error ? error.message : "微信绑定取消失败。",
        "error",
      );
    } finally {
      elements.weixinCancel.disabled = false;
    }
  });
  elements.weixinVerifyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const code = elements.weixinVerifyCode.value.trim();
    if (!code) return;
    try {
      elements.weixinVerifyCode.value = "";
      renderWeixinLogin(await fetchSettingsApi("/api/openclaw/weixin/login/verify", {
        method: "POST",
        headers: settingsHeaders(true),
        body: JSON.stringify({ code }),
      }));
    } catch (error) {
      setSettingsMessage(
        elements.weixinMessage,
        error instanceof Error ? error.message : "验证码提交失败。",
        "error",
      );
    }
  });
  elements.weixinDialog.addEventListener("click", (event) => {
    if (event.target === elements.weixinDialog) closeWeixinDialog();
  });
  elements.weixinDialog.addEventListener("close", () => {
    stopPolling();
    releaseQr();
  });
  disposeOpenClawSettings = () => {
    if (disposed) return;
    disposed = true;
    requestAbortController.abort();
    stopPolling();
    releaseQr();
  };
  void load();
}

if (settingsPage === "appearance") {
  initializeAppearanceSettings();
} else if (settingsPage === "diagnostics") {
  initializeDiagnosticsSettings();
} else if (settingsPage === "runtime-detail") {
  window.initializeWorkspacePluginLifecycle?.();
  if (runtimeDefaultImplementation instanceof HTMLSelectElement) {
    initializeSettingsChoicePickers();
    runtimeDefaultImplementation.addEventListener(
      "change",
      () => void saveRuntimeDefaultImplementation(),
    );
    void loadRuntimePlugins();
  }
} else if (settingsPage === "session") {
  void loadGeneralRuntimeSettings();
  window.initializeSessionVisibilitySettings?.();
} else if (settingsPage === "runtime") {
  window.initializeWorkspacePluginLifecycle?.();
} else if (settingsPage === "chub-task-prompt-optimizer") {
  initializeSettingsChoicePickers();
  initializePromptOptimizerSettings();
} else if (document.querySelector("[data-plugin-version-picker]")) {
  initializeSettingsChoicePickers();
  window.initializeWorkspacePluginLifecycle?.();
} else if (settingsPage === "openclaw") {
  initializeOpenClawSettings();
}

  window.disposeSettingsPage = () => {
    disposeOpenClawSettings();
    disposeOpenClawSettings = () => {};
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
