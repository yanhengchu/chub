"use strict";

const QUICK_INTERACTION_PAGE_SIZE_KEY = "hub.quickInteractionPageSize.v1";
const settingsPage = document.body.dataset.settingsPage || "";
const CODEX_MODEL_PREFERENCE_CACHE_KEY = "hub.codexModelPreferenceCache";
const WEIXIN_TRANSLATION_SETTINGS_CACHE_KEY = "hub.weixinTranslationSettingsCache";
const OPENCLAW_WEIXIN_SETTINGS_CACHE_KEY = "hub.openclawWeixinSettingsCache.v1";
const CYBER_RAIN_SPEED_KEY = "hub.cyberRainSpeed.v1";
const CYBER_RAIN_BRIGHTNESS_KEY = "hub.cyberRainBrightness.v1";
const CYBER_RAIN_DENSITY_KEY = "hub.cyberRainDensity.v1";
const settingsMessage = document.querySelector("#settings-message");
const quickInteractionPageSize = document.querySelector(
  "#quick-interaction-page-size",
);
const codexDefaultFullAccess = document.querySelector("#codex-default-full-access");
const runtimeManagementList = document.querySelector("#runtime-management-list");
const runtimeManagementDescription = document.querySelector(
  "#runtime-management-description",
);
const codexSessionSettingsMessage = document.querySelector(
  "#codex-session-settings-message",
);
const weixinProcessingMode = document.querySelector("#weixin-processing-mode");
const weixinProcessingModeInputs = document.querySelectorAll(
  'input[name="weixin-processing-mode"]',
);
const weixinTranslationModel = document.querySelector(
  "#weixin-translation-model",
);
const weixinTranslationModelField = document.querySelector(
  "#weixin-translation-model-field",
);
const weixinTranslationModelDescription = document.querySelector(
  "#weixin-translation-model-description",
);
const weixinTranslationReasoningEffort = document.querySelector(
  "#weixin-translation-reasoning-effort",
);
const weixinTranslationReasoningEffortField = document.querySelector(
  "#weixin-translation-reasoning-effort-field",
);
const weixinTranslationReasoningEffortDescription = document.querySelector(
  "#weixin-translation-reasoning-effort-description",
);
const weixinTranslationMessage = document.querySelector(
  "#weixin-translation-message",
);
const settingsOpenClawBadge = document.querySelector("#settings-openclaw-badge");
const settingsOpenClawDetail = document.querySelector("#settings-openclaw-detail");
const settingsOpenClawOpen = document.querySelector("#settings-openclaw-open");
const settingsOpenClawOpenLabel = document.querySelector(
  "#settings-openclaw-open-label",
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
const settingsOpenClawWeixinBadge = document.querySelector(
  "#settings-openclaw-weixin-badge",
);
const settingsOpenClawWeixinDetail = document.querySelector(
  "#settings-openclaw-weixin-detail",
);
const settingsOpenClawBindWeixin = document.querySelector(
  "#settings-openclaw-bind-weixin",
);
const settingsOpenClawWeixinMessage = document.querySelector(
  "#settings-openclaw-weixin-message",
);
const openclawWeixinDialog = document.querySelector("#openclaw-weixin-dialog");
const openclawWeixinClose = document.querySelector("#openclaw-weixin-close");
const openclawWeixinAccountSummary = document.querySelector(
  "#openclaw-weixin-account-summary",
);
const openclawWeixinOwnerSummary = document.querySelector(
  "#openclaw-weixin-owner-summary",
);
const openclawWeixinQrPanel = document.querySelector("#openclaw-weixin-qr-panel");
const openclawWeixinQr = document.querySelector("#openclaw-weixin-qr");
const openclawWeixinVerifyForm = document.querySelector(
  "#openclaw-weixin-verify-form",
);
const openclawWeixinVerifyCode = document.querySelector(
  "#openclaw-weixin-verify-code",
);
const openclawWeixinMessage = document.querySelector("#openclaw-weixin-message");
const openclawWeixinCancel = document.querySelector("#openclaw-weixin-cancel");
const openclawWeixinStart = document.querySelector("#openclaw-weixin-start");
const cyberRainSpeed = document.querySelector("#cyber-rain-speed");
const cyberRainBrightness = document.querySelector("#cyber-rain-brightness");
const cyberRainDensity = document.querySelector("#cyber-rain-density");
const cyberRainSpeedValue = document.querySelector("#cyber-rain-speed-value");
const cyberRainBrightnessValue = document.querySelector("#cyber-rain-brightness-value");
const cyberRainDensityValue = document.querySelector("#cyber-rain-density-value");
const cyberStyleSettingsMessage = document.querySelector("#cyber-style-settings-message");
const styleOptionRows = document.querySelectorAll("[data-style-option]");
const cyberStyleDetails = document.querySelector("[data-cyber-style-details]");
let codexModels = [];
let codexModelsLoaded = false;
let codexCatalogDefaultModelId = "";
let codexCatalogDefaultReasoningEffort = "";
let weixinTranslationStatus = null;
let weixinTranslationStatusLive = false;
let weixinTranslationCacheRestored = false;
let weixinTranslationPollTimer = null;
let weixinTranslationRequestVersion = 0;
let settingsOpenClawStatus = null;
let settingsOpenClawWeixinState = null;
let settingsOpenClawWeixinCacheRestored = false;
let settingsOpenClawWeixinPollTimer = 0;
let settingsOpenClawWeixinPollFailures = 0;
let settingsOpenClawWeixinQrObjectUrl = "";
let settingsOpenClawWeixinQrUpdatedAt = "";
let maintenanceTerminalOpening = false;

const CODEX_REASONING_LABELS = {
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra High",
  max: "Max",
  ultra: "Ultra",
};

const settingsChoicePickers = new Map();
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

    const state = { select, trigger, menu };
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
    new MutationObserver(() => renderSettingsChoicePicker(state)).observe(select, {
      attributes: true,
      attributeFilter: ["disabled"],
      childList: true,
      subtree: true,
    });
  });
}

document.addEventListener("pointerdown", (event) => {
  if (
    openSettingsChoicePicker
    && !openSettingsChoicePicker.trigger.contains(event.target)
    && !openSettingsChoicePicker.menu.contains(event.target)
  ) {
    closeSettingsChoicePicker();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && openSettingsChoicePicker) {
    event.preventDefault();
    const picker = openSettingsChoicePicker;
    closeSettingsChoicePicker(picker);
    picker.trigger.focus();
  }
});

window.addEventListener("resize", () => closeSettingsChoicePicker());

function renderStyleSelection(style) {
  styleOptionRows.forEach((row) => {
    const selected = row.dataset.styleOption === style;
    const badge = row.querySelector("[data-style-badge]");
    const button = row.querySelector("[data-style-apply]");
    badge.textContent = selected ? "当前风格" : "可用风格";
    badge.className = selected ? "badge badge-success" : "badge badge-muted";
    button.textContent = selected ? "使用中" : "应用";
    button.disabled = selected;
  });
  cyberStyleDetails.querySelector("summary").textContent = style === "cyber"
    ? "Cyber 参数 · 当前风格"
    : "Cyber 参数";
}

function readRangePreference(key, fallback) {
  try {
    const value = Number(localStorage.getItem(key));
    return Number.isFinite(value) && value >= 10 && value <= 100
      ? value
      : fallback;
  } catch (_error) {
    return fallback;
  }
}

function updateCyberControl(input, output, key) {
  const value = Math.min(100, Math.max(10, Number(input.value)));
  try {
    localStorage.setItem(key, String(value));
    input.value = String(value);
    output.value = `${value}%`;
    cyberStyleSettingsMessage.textContent = "";
    cyberStyleSettingsMessage.className = "message";
    if (window.ChubTheme.currentStyle() === "cyber") {
      window.ChubTheme.refreshCyberRain();
    }
  } catch (_error) {
    cyberStyleSettingsMessage.textContent = "当前浏览器无法保存界面偏好。";
    cyberStyleSettingsMessage.className = "message message-error";
  }
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

function createOption(value, label, description = "") {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  if (description) option.dataset.description = description;
  return option;
}

function defaultModelOptionLabel() {
  return "跟随 Codex 默认";
}

function defaultModelDescription(model) {
  const modelName = model?.name || model?.id || "不可用";
  const modelId = model?.id && model?.name !== model.id ? `（${model.id}）` : "";
  const level = codexCatalogDefaultReasoningEffort || model?.default_level || "不可用";
  return `当前 Codex 默认 · ${modelName}${modelId} · ${CODEX_REASONING_LABELS[level] || level}`;
}

function defaultReasoningOptionLabel() {
  return "跟随模型默认";
}

function defaultReasoningDescription(level) {
  return level
    ? `当前默认 ${CODEX_REASONING_LABELS[level] || level}`
    : "当前模型未提供默认等级";
}

function renderCodexModels(data) {
  codexModels = data.models;
  codexCatalogDefaultModelId = data.default_model || "";
  codexCatalogDefaultReasoningEffort = data.default_reasoning_effort || "";
  if (weixinTranslationStatus !== null) {
    renderWeixinTranslationModelSettings(weixinTranslationStatus);
  }
}

function renderWeixinTranslationModelSettings(status) {
  const defaultModel = codexModels.find(
    (model) => model.id === codexCatalogDefaultModelId,
  );
  const options = [createOption(
    "",
    defaultModelOptionLabel(),
    defaultModelDescription(defaultModel),
  )];
  if (
    status.model
    && !codexModels.some((model) => model.id === status.model)
  ) {
    options.push(createOption(status.model, `${status.model}（当前不可用）`, "当前保存的模型"));
  }
  codexModels.forEach((model) => {
    options.push(createOption(model.id, model.name, model.description || ""));
  });
  weixinTranslationModel.replaceChildren(...options);
  weixinTranslationModel.value = status.model || "";
  weixinTranslationModel.disabled = (
    !codexModelsLoaded
    || !weixinTranslationStatusLive
    || (codexModels.length === 0 && !status.model)
  );

  const model = codexModels.find(
    (item) => item.id === (weixinTranslationModel.value || codexCatalogDefaultModelId),
  );
  const effectiveModelName = model?.name || model?.id || "不可用";
  const effectiveLevel = status.reasoning_effort
    || (!status.model && codexCatalogDefaultReasoningEffort)
    || model?.default_level
    || "不可用";
  const effectiveLevelName = CODEX_REASONING_LABELS[effectiveLevel] || effectiveLevel;
  weixinTranslationModelDescription.textContent = status.model
    ? `当前使用 ${effectiveModelName}；只影响之后新提交的文本优化任务。`
    : `跟随 Codex 默认，当前为 ${effectiveModelName} · ${effectiveLevelName}。`;
  weixinTranslationReasoningEffortDescription.textContent = status.reasoning_effort
    ? `当前使用 ${effectiveLevelName}；只影响之后新提交的文本优化任务。`
    : `跟随模型默认，当前为 ${effectiveLevelName}。`;
  const levels = [createOption(
    "",
    "跟随模型默认",
    defaultReasoningDescription(
      status.reasoning_effort
      || (!status.model && codexCatalogDefaultReasoningEffort)
      || model?.default_level,
    ),
  )];
  if (model) {
    model.levels.forEach((level) => {
      levels.push(
        createOption(
          level.id,
          CODEX_REASONING_LABELS[level.id] || level.id,
          level.description || "",
        ),
      );
    });
  }
  weixinTranslationReasoningEffort.replaceChildren(...levels);
  weixinTranslationReasoningEffort.value = model
    && model.levels.some((level) => level.id === status.reasoning_effort)
    ? status.reasoning_effort
    : "";
  weixinTranslationReasoningEffort.disabled = (
    !codexModelsLoaded
    || !weixinTranslationStatusLive
    || !model
  );
  weixinTranslationModelField.hidden = false;
  weixinTranslationReasoningEffortField.hidden = false;
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
  runtimeManagementDescription.textContent = text;
  runtimeManagementDescription.classList.toggle("message-error", kind === "error");
}

function renderRuntimeManagement(data) {
  const runtimes = Array.isArray(data?.runtimes) ? data.runtimes : [];
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
    const description = document.createElement("small");
    description.textContent = runtime.enabled
      ? (runtime.healthy ? "已启用，可创建并提交新的 AI 任务。" : (runtime.reason || "已启用，但当前不可提交任务。"))
      : "已停用，不再接受新的 AI 任务；已受理任务继续收敛。";
    copy.append(title, description);
    const control = document.createElement("span");
    control.className = "settings-switch";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = runtime.enabled === true;
    input.dataset.runtimeId = runtime.runtime_id;
    input.dataset.previousEnabled = String(runtime.enabled === true);
    input.setAttribute("aria-label", `${name.textContent} ${input.checked ? "已启用" : "已停用"}`);
    input.addEventListener("change", () => void saveRuntimeEnablement(input));
    const track = document.createElement("span");
    track.className = "settings-switch-track";
    track.setAttribute("aria-hidden", "true");
    control.append(input, track);
    field.append(copy, control);
    runtimeManagementList.append(field);
  }
  if (data?.basic_mode === true) {
    setRuntimeManagementDescription("所有 AI Runtime 已停用，Chub 当前处于基础功能模式。");
  } else {
    setRuntimeManagementDescription(
      "启用后可创建并提交该 Runtime 的 AI 任务；关闭不会中断已受理任务。",
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
    runtimeManagementList.replaceChildren();
    setRuntimeManagementDescription("暂时无法读取 AI Runtime 状态。", "error");
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
    setRuntimeManagementDescription("AI Runtime 状态保存失败，请稍后重试。", "error");
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

async function loadCodexModels() {
  try {
    const cached = JSON.parse(
      sessionStorage.getItem(CODEX_MODEL_PREFERENCE_CACHE_KEY) || "null",
    );
    if (Array.isArray(cached?.models)) {
      codexModels = cached.models;
      codexCatalogDefaultModelId = cached.default_model || "";
      codexCatalogDefaultReasoningEffort = cached.default_reasoning_effort || "";
      if (weixinTranslationStatus !== null) {
        renderWeixinTranslationModelSettings(weixinTranslationStatus);
      }
    }
  } catch (_error) {
    try {
      sessionStorage.removeItem(CODEX_MODEL_PREFERENCE_CACHE_KEY);
    } catch (_storageError) {
      // A storage failure must not block the live catalog request.
    }
  }
  try {
    const response = await fetch("/api/codex/models");
    const payload = await response.json();
    if (!response.ok || payload.success !== true || !Array.isArray(payload.data?.models)) {
      throw new Error("model_catalog_unavailable");
    }
    codexModelsLoaded = true;
    renderCodexModels(payload.data);
    try {
      sessionStorage.setItem(
        CODEX_MODEL_PREFERENCE_CACHE_KEY,
        JSON.stringify(payload.data),
      );
    } catch (_storageError) {
      // A storage failure must not affect the live settings.
    }
  } catch (_error) {
    codexModels = [];
    weixinTranslationModelDescription.textContent = "无法读取 Codex 当前默认模型。";
    weixinTranslationReasoningEffortDescription.textContent = "无法读取 Codex 当前默认推理等级。";
    if (weixinTranslationStatus !== null && codexModels.length > 0) {
      renderWeixinTranslationModelSettings(weixinTranslationStatus);
    }
  }
}

function settingsHeaders(includeJson = false) {
  const headers = {};
  if (includeJson) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

function renderWeixinTranslationStatus(status, { live = true } = {}) {
  weixinTranslationStatus = status;
  if (live) {
    weixinTranslationStatusLive = true;
  }
  const selected = status.mode || (status.enabled ? "auto" : "direct");
  for (const input of weixinProcessingModeInputs) {
    input.checked = input.value === selected;
  }
  if (codexModelsLoaded || codexModels.length > 0) {
    renderWeixinTranslationModelSettings(status);
  }
  weixinProcessingMode.disabled = !weixinTranslationStatusLive;
  const active = Number(status.queued || 0) + Number(status.running || 0);
  const parts = [];
  if (active > 0) {
    parts.push(`${active} 项文本优化仍在处理中`);
  }
  if (!status.weixin_chub_mode_enabled) {
    parts.push("微信 Chub 模式当前未启用");
  }
  weixinTranslationMessage.textContent = parts.join(" · ");
  weixinTranslationMessage.className = "message";
  if (weixinTranslationPollTimer !== null) {
    window.clearTimeout(weixinTranslationPollTimer);
    weixinTranslationPollTimer = null;
  }
  if (active > 0) {
    weixinTranslationPollTimer = window.setTimeout(() => {
      loadWeixinTranslationStatus(
        "暂时无法刷新文本优化任务状态，正在重试。",
        true,
      );
    }, 2000);
  }
}

async function loadWeixinTranslationStatus(
  failureMessage = "暂时无法读取节点状态，请确认访问凭证或 Tailnet 连接。",
  retry = false,
) {
  const requestVersion = ++weixinTranslationRequestVersion;
  if (!weixinTranslationCacheRestored) {
    weixinTranslationCacheRestored = true;
    try {
      const cached = JSON.parse(
        sessionStorage.getItem(WEIXIN_TRANSLATION_SETTINGS_CACHE_KEY) || "null",
      );
      if (cached && typeof cached === "object") {
        renderWeixinTranslationStatus(cached, { live: false });
      }
    } catch (_error) {
      try {
        sessionStorage.removeItem(WEIXIN_TRANSLATION_SETTINGS_CACHE_KEY);
      } catch (_storageError) {
        // A storage failure must not block the live settings request.
      }
    }
  }
  weixinProcessingMode.disabled = true;
  try {
    const response = await fetch("/api/settings/weixin-translation", {
      headers: settingsHeaders(),
    });
    const payload = await response.json();
    if (!response.ok || payload.success !== true) {
      throw new Error(payload.error?.code || "settings_unavailable");
    }
    if (requestVersion !== weixinTranslationRequestVersion) {
      return false;
    }
    renderWeixinTranslationStatus(payload.data);
    try {
      sessionStorage.setItem(
        WEIXIN_TRANSLATION_SETTINGS_CACHE_KEY,
        JSON.stringify(payload.data),
      );
    } catch (_storageError) {
      // A storage failure must not affect the live settings.
    }
    return true;
  } catch (_error) {
    if (requestVersion !== weixinTranslationRequestVersion) {
      return false;
    }
    weixinProcessingMode.disabled = true;
    weixinTranslationModel.disabled = true;
    weixinTranslationReasoningEffort.disabled = true;
    weixinTranslationMessage.textContent = failureMessage;
    weixinTranslationMessage.className = "message message-error";
    if (retry) {
      weixinTranslationPollTimer = window.setTimeout(() => {
        loadWeixinTranslationStatus(failureMessage, true);
      }, 5000);
    }
    return false;
  }
}

async function saveWeixinTranslationStatus(mode) {
  weixinTranslationRequestVersion += 1;
  if (weixinTranslationPollTimer !== null) {
    window.clearTimeout(weixinTranslationPollTimer);
    weixinTranslationPollTimer = null;
  }
  weixinProcessingMode.disabled = true;
  try {
    const response = await fetch("/api/settings/weixin-translation", {
      method: "PUT",
      headers: settingsHeaders(true),
      body: JSON.stringify({ mode }),
    });
    const payload = await response.json();
    if (!response.ok || payload.success !== true) {
      throw new Error(payload.error?.code || "settings_update_failed");
    }
    renderWeixinTranslationStatus(payload.data);
    try {
      sessionStorage.setItem(
        WEIXIN_TRANSLATION_SETTINGS_CACHE_KEY,
        JSON.stringify(payload.data),
      );
    } catch (_storageError) {
      // A storage failure must not affect the live settings.
    }
  } catch (_error) {
    await loadWeixinTranslationStatus(
      "设置结果未知，请稍后刷新页面重试。",
    );
  }
}

async function saveWeixinTranslationModelSettings() {
  weixinTranslationRequestVersion += 1;
  if (weixinTranslationPollTimer !== null) {
    window.clearTimeout(weixinTranslationPollTimer);
    weixinTranslationPollTimer = null;
  }
  weixinTranslationModel.disabled = true;
  weixinTranslationReasoningEffort.disabled = true;
  const model = weixinTranslationModel.value || null;
  const reasoningEffort = model
    ? (weixinTranslationReasoningEffort.value || null)
    : null;
  try {
    const response = await fetch("/api/settings/weixin-translation", {
      method: "PUT",
      headers: settingsHeaders(true),
      body: JSON.stringify({
        model,
        reasoning_effort: reasoningEffort,
      }),
    });
    const payload = await response.json();
    if (!response.ok || payload.success !== true) {
      throw new Error(payload.error?.code || "settings_update_failed");
    }
    renderWeixinTranslationStatus(payload.data);
    try {
      sessionStorage.setItem(
        WEIXIN_TRANSLATION_SETTINGS_CACHE_KEY,
        JSON.stringify(payload.data),
      );
    } catch (_storageError) {
      // A storage failure must not affect the live settings.
    }
  } catch (_error) {
    await loadWeixinTranslationStatus(
      "翻译模型设置结果未知，请稍后刷新页面重试。",
    );
  }
}

const OPENCLAW_WEIXIN_ACTIVE_STATES = new Set([
  "starting",
  "waiting_scan",
  "needs_verification",
  "confirming",
  "cancelling",
]);

function setOpenClawWeixinMessage(element, text, kind = "") {
  element.textContent = text;
  element.className = kind ? `message message-${kind}` : "message";
}

function setOpenClawWeixinBadge(text, kind = "muted") {
  if (!settingsOpenClawWeixinBadge) return;
  settingsOpenClawWeixinBadge.textContent = text;
  settingsOpenClawWeixinBadge.className = `badge badge-${kind}`;
}

function setOpenClawBadge(text, kind = "muted") {
  if (!settingsOpenClawBadge) return;
  settingsOpenClawBadge.textContent = text;
  settingsOpenClawBadge.className = `badge badge-${kind}`;
}

function localOpenClawAccessUrl(status) {
  const url = status?.local_access_url;
  return typeof url === "string" && /^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}\/$/.test(url)
    ? url
    : "";
}

function renderOpenClawGatewaySettings(status, { live = true } = {}) {
  const presentation = {
    unavailable: ["未安装", "muted"],
    unconfigured: ["未初始化", "timeout"],
    service_missing: ["服务未安装", "timeout"],
    stopped: ["已停止", "timeout"],
    running: ["运行正常", "success"],
    degraded: ["尚未就绪", "timeout"],
    unknown: ["状态未知", "failed"],
  }[status?.state] || ["不可检查", "muted"];
  setOpenClawBadge(presentation[0], presentation[1]);
  const detail = status?.message || "暂时无法读取 OpenClaw Gateway 状态。";
  if (settingsOpenClawDetail) {
    settingsOpenClawDetail.textContent = live
      ? detail
      : `当前展示上次检测结果：${detail}`;
  }

  const accessUrl = live ? localOpenClawAccessUrl(status) : "";
  if (!settingsOpenClawOpen || !settingsOpenClawOpenLabel) {
    return;
  }
  if (accessUrl) {
    settingsOpenClawOpen.href = accessUrl;
    settingsOpenClawOpen.removeAttribute("aria-disabled");
    settingsOpenClawOpenLabel.textContent = "打开";
  } else {
    settingsOpenClawOpen.removeAttribute("href");
    settingsOpenClawOpen.setAttribute("aria-disabled", "true");
    settingsOpenClawOpenLabel.textContent = "不可用";
  }
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

function renderOpenClawWeixinContext(status) {
  if (!openclawWeixinAccountSummary || !openclawWeixinOwnerSummary) return;
  openclawWeixinAccountSummary.textContent = status?.channel_message
    || "当前消息通道状态不可用。";
  openclawWeixinOwnerSummary.textContent = status?.owner_message
    || "当前 Owner 授权状态不可用。";
}

function renderOpenClawWeixinSettings(status, login, { live = true } = {}) {
  settingsOpenClawStatus = status;
  settingsOpenClawWeixinState = login;
  renderOpenClawGatewaySettings(status, { live });
  renderOpenClawWeixinContext(status);

  if (!status?.installed) {
    setOpenClawWeixinBadge("未安装", "muted");
    settingsOpenClawWeixinDetail.textContent = "OpenClaw Gateway 尚未安装。";
    settingsOpenClawBindWeixin.hidden = true;
    return;
  }
  if (!status.configured) {
    setOpenClawWeixinBadge("未初始化", "timeout");
    settingsOpenClawWeixinDetail.textContent = "OpenClaw Gateway 尚未完成初始化。";
    settingsOpenClawBindWeixin.hidden = true;
    return;
  }

  const active = OPENCLAW_WEIXIN_ACTIVE_STATES.has(login?.state);
  const activePresentation = {
    waiting_scan: ["等待扫码", "timeout", login?.message],
    needs_verification: ["等待验证", "timeout", login?.message],
    confirming: ["确认中", "muted", login?.message],
    starting: ["准备中", "muted", login?.message],
    cancelling: ["取消中", "muted", login?.message],
  }[login?.state];
  const channelPresentation = {
    running: ["微信通道已连接", "success", status.channel_message],
    degraded: ["微信通道部分异常", "timeout", status.channel_message],
    stopped: ["微信通道异常", "failed", status.channel_message],
    not_configured: ["微信通道未配置", "muted", status.channel_message],
    unavailable: ["微信通道不可检查", "muted", status.channel_message],
    unknown: ["微信通道状态未知", "failed", status.channel_message],
  }[status.channel_state];
  const presentation = activePresentation
    || channelPresentation
    || (login?.state === "succeeded"
      ? ["微信通道已连接", "success", login.message]
      : ["微信通道状态未知", "failed", "暂时无法确认微信消息通道状态。"]);
  setOpenClawWeixinBadge(presentation[0], presentation[1]);
  const detail = presentation[2] || "微信通道状态暂时不可用。";
  settingsOpenClawWeixinDetail.textContent = live
    ? detail
    : `当前展示上次检测结果：${detail}`;
  settingsOpenClawBindWeixin.hidden = false;
  settingsOpenClawBindWeixin.disabled = !live || active || !login;
  settingsOpenClawBindWeixin.textContent = status.channel_state === "running"
    ? "重新绑定微信"
    : "绑定微信";
}

function restoreOpenClawWeixinSettingsCache() {
  if (settingsOpenClawWeixinCacheRestored) {
    return;
  }
  settingsOpenClawWeixinCacheRestored = true;
  try {
    const cached = JSON.parse(
      sessionStorage.getItem(OPENCLAW_WEIXIN_SETTINGS_CACHE_KEY) || "null",
    );
    if (cached?.status && cached?.login) {
      renderOpenClawWeixinSettings(cached.status, cached.login, { live: false });
    }
  } catch (_error) {
    try {
      sessionStorage.removeItem(OPENCLAW_WEIXIN_SETTINGS_CACHE_KEY);
    } catch (_storageError) {
      // A storage failure must not block the live settings request.
    }
  }
}

function cacheOpenClawWeixinSettings(status, login) {
  try {
    sessionStorage.setItem(
      OPENCLAW_WEIXIN_SETTINGS_CACHE_KEY,
      JSON.stringify({ status, login }),
    );
  } catch (_error) {
    // Caching is an optional page-recovery enhancement.
  }
}

async function loadOpenClawWeixinSettings() {
  restoreOpenClawWeixinSettingsCache();
  if (!settingsOpenClawStatus || !settingsOpenClawWeixinState) {
    settingsOpenClawBindWeixin.hidden = true;
  }
  settingsOpenClawBindWeixin.disabled = true;
  try {
    const [status, login] = await Promise.all([
      fetchSettingsApi("/api/openclaw/status"),
      fetchSettingsApi("/api/openclaw/weixin/login"),
    ]);
    renderOpenClawWeixinSettings(status, login);
    cacheOpenClawWeixinSettings(status, login);
    setOpenClawWeixinMessage(settingsOpenClawWeixinMessage, "");
    return true;
  } catch (_error) {
    if (settingsOpenClawStatus && settingsOpenClawWeixinState) {
      renderOpenClawWeixinSettings(
        settingsOpenClawStatus,
        settingsOpenClawWeixinState,
        { live: false },
      );
      setOpenClawWeixinMessage(
        settingsOpenClawWeixinMessage,
        "状态刷新失败，当前展示上次检测结果。",
        "error",
      );
      return false;
    }
    setOpenClawWeixinBadge("不可检查", "muted");
    settingsOpenClawWeixinDetail.textContent = "暂时无法读取 OpenClaw 与微信绑定状态。";
    renderOpenClawGatewaySettings(null);
    setOpenClawWeixinMessage(
      settingsOpenClawWeixinMessage,
      "请确认当前连接位于可信网络，并稍后刷新页面重试。",
      "error",
    );
    return false;
  }
}

function releaseOpenClawWeixinQr() {
  if (settingsOpenClawWeixinQrObjectUrl) {
    URL.revokeObjectURL(settingsOpenClawWeixinQrObjectUrl);
    settingsOpenClawWeixinQrObjectUrl = "";
  }
  settingsOpenClawWeixinQrUpdatedAt = "";
  openclawWeixinQr.removeAttribute("src");
  openclawWeixinQrPanel.hidden = true;
}

function stopOpenClawWeixinPolling() {
  if (settingsOpenClawWeixinPollTimer) {
    window.clearTimeout(settingsOpenClawWeixinPollTimer);
    settingsOpenClawWeixinPollTimer = 0;
  }
}

function closeOpenClawWeixinDialog() {
  stopOpenClawWeixinPolling();
  releaseOpenClawWeixinQr();
  openclawWeixinVerifyForm.hidden = true;
  openclawWeixinVerifyCode.value = "";
  if (openclawWeixinDialog.open) {
    openclawWeixinDialog.close();
  }
}

async function loadOpenClawWeixinQr(updatedAt) {
  if (
    settingsOpenClawWeixinQrObjectUrl
    && settingsOpenClawWeixinQrUpdatedAt === updatedAt
  ) {
    return;
  }
  try {
    const response = await fetch("/api/openclaw/weixin/login/qr", {
      headers: settingsHeaders(),
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error("weixin_qr_unavailable");
    }
    const blob = await response.blob();
    if (!openclawWeixinDialog.open) {
      return;
    }
    releaseOpenClawWeixinQr();
    settingsOpenClawWeixinQrObjectUrl = URL.createObjectURL(blob);
    settingsOpenClawWeixinQrUpdatedAt = updatedAt;
    openclawWeixinQr.src = settingsOpenClawWeixinQrObjectUrl;
    openclawWeixinQrPanel.hidden = false;
  } catch (_error) {
    setOpenClawWeixinMessage(
      openclawWeixinMessage,
      "微信绑定二维码读取失败。",
      "error",
    );
  }
}

function renderOpenClawWeixinLogin(login) {
  const previousState = settingsOpenClawWeixinState?.state;
  settingsOpenClawWeixinState = login;
  const active = OPENCLAW_WEIXIN_ACTIVE_STATES.has(login.state);
  const needsVerification = login.state === "needs_verification";
  openclawWeixinCancel.hidden = !active || login.state === "cancelling";
  openclawWeixinStart.hidden = active;
  openclawWeixinStart.textContent = (
    settingsOpenClawStatus?.channel_state === "running"
    || login.state !== "idle"
  )
    ? "重新生成二维码"
    : "生成二维码";
  openclawWeixinVerifyForm.hidden = !needsVerification;
  setOpenClawWeixinMessage(
    openclawWeixinMessage,
    login.message,
    login.state === "succeeded"
      ? "success"
      : login.state === "failed"
        ? "error"
        : "",
  );
  if (login.qr_available) {
    void loadOpenClawWeixinQr(login.updated_at);
  } else {
    releaseOpenClawWeixinQr();
  }
  if (settingsOpenClawStatus) {
    renderOpenClawWeixinSettings(settingsOpenClawStatus, login);
  }
  if (login.state === "succeeded" && previousState !== "succeeded") {
    void loadOpenClawWeixinSettings();
  }
}

async function pollOpenClawWeixinLogin() {
  stopOpenClawWeixinPolling();
  if (!openclawWeixinDialog.open) {
    return;
  }
  try {
    const login = await fetchSettingsApi("/api/openclaw/weixin/login");
    settingsOpenClawWeixinPollFailures = 0;
    renderOpenClawWeixinLogin(login);
    if (OPENCLAW_WEIXIN_ACTIVE_STATES.has(login.state)) {
      settingsOpenClawWeixinPollTimer = window.setTimeout(
        pollOpenClawWeixinLogin,
        1000,
      );
    }
  } catch (_error) {
    settingsOpenClawWeixinPollFailures += 1;
    setOpenClawWeixinMessage(
      openclawWeixinMessage,
      "微信绑定状态读取失败。",
      "error",
    );
    if (openclawWeixinDialog.open) {
      const retryDelay = Math.min(
        5000,
        1000 * (2 ** Math.min(settingsOpenClawWeixinPollFailures - 1, 3)),
      );
      settingsOpenClawWeixinPollTimer = window.setTimeout(
        pollOpenClawWeixinLogin,
        retryDelay,
      );
    }
  }
}

async function openOpenClawWeixinDialog() {
  if (!await loadOpenClawWeixinSettings()) {
    return;
  }
  openclawWeixinDialog.showModal();
  setOpenClawWeixinMessage(openclawWeixinMessage, "正在读取微信绑定状态…");
  settingsOpenClawWeixinPollFailures = 0;
  await pollOpenClawWeixinLogin();
}

async function startOpenClawWeixinLogin() {
  openclawWeixinStart.disabled = true;
  setOpenClawWeixinMessage(openclawWeixinMessage, "正在生成微信绑定二维码…");
  try {
    const login = await fetchSettingsApi("/api/openclaw/weixin/login", {
      method: "POST",
    });
    renderOpenClawWeixinLogin(login);
    settingsOpenClawWeixinPollTimer = window.setTimeout(
      pollOpenClawWeixinLogin,
      500,
    );
  } catch (_error) {
    setOpenClawWeixinMessage(openclawWeixinMessage, "微信绑定启动失败。", "error");
  } finally {
    openclawWeixinStart.disabled = false;
  }
}

async function cancelOpenClawWeixinLogin() {
  openclawWeixinCancel.disabled = true;
  try {
    renderOpenClawWeixinLogin(
      await fetchSettingsApi("/api/openclaw/weixin/login", { method: "DELETE" }),
    );
  } catch (_error) {
    setOpenClawWeixinMessage(openclawWeixinMessage, "微信绑定取消失败。", "error");
  } finally {
    openclawWeixinCancel.disabled = false;
  }
}

function closeMaintenanceTerminalDialog() {
  if (maintenanceTerminalDialog.open) {
    maintenanceTerminalDialog.close();
  }
}

function initializeAppearanceSettings() {
  cyberRainSpeed.value = String(readRangePreference(CYBER_RAIN_SPEED_KEY, 60));
  cyberRainBrightness.value = String(readRangePreference(CYBER_RAIN_BRIGHTNESS_KEY, 70));
  cyberRainDensity.value = String(readRangePreference(CYBER_RAIN_DENSITY_KEY, 50));
  cyberRainSpeedValue.value = `${cyberRainSpeed.value}%`;
  cyberRainBrightnessValue.value = `${cyberRainBrightness.value}%`;
  cyberRainDensityValue.value = `${cyberRainDensity.value}%`;
  cyberRainSpeed.addEventListener("input", () => updateCyberControl(cyberRainSpeed, cyberRainSpeedValue, CYBER_RAIN_SPEED_KEY));
  cyberRainBrightness.addEventListener("input", () => updateCyberControl(cyberRainBrightness, cyberRainBrightnessValue, CYBER_RAIN_BRIGHTNESS_KEY));
  cyberRainDensity.addEventListener("input", () => updateCyberControl(cyberRainDensity, cyberRainDensityValue, CYBER_RAIN_DENSITY_KEY));
  styleOptionRows.forEach((row) => {
    row.querySelector("[data-style-apply]").addEventListener("click", () => {
      const style = row.dataset.styleOption;
      if (window.ChubTheme.applyStyle(style, { persist: true })) {
        renderStyleSelection(style);
        cyberStyleDetails.open = style === "cyber";
        cyberStyleSettingsMessage.textContent = "";
        cyberStyleSettingsMessage.className = "message";
      } else {
        cyberStyleSettingsMessage.textContent = "当前浏览器无法保存界面偏好。";
        cyberStyleSettingsMessage.className = "message message-error";
      }
    });
  });
  renderStyleSelection(window.ChubTheme.currentStyle());
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
    maintenanceTerminalOpening = true;
    settingsMaintenanceTerminal.disabled = true;
    maintenanceTerminalDialogConfirm.disabled = true;
    try {
      const data = await fetchSettingsApi("/api/maintenance-terminal/access", { method: "POST", headers: settingsHeaders(true) });
      window.open(data.terminal_url, "_blank", "noopener");
      closeMaintenanceTerminalDialog();
    } catch (_error) {
      maintenanceTerminalDialogFeedback.textContent = "维护终端暂时无法启动，请检查 ttyd 和 zsh。";
      maintenanceTerminalDialogFeedback.className = "message message-error";
    } finally {
      maintenanceTerminalOpening = false;
      settingsMaintenanceTerminal.disabled = false;
      maintenanceTerminalDialogConfirm.disabled = false;
    }
  });
}

function initializeWeixinTextSettings() {
  initializeSettingsChoicePickers();
  loadCodexModels();
  loadWeixinTranslationStatus();
  for (const input of weixinProcessingModeInputs) {
    input.addEventListener("change", () => {
      if (input.checked) saveWeixinTranslationStatus(input.value);
    });
  }
  weixinTranslationModel.addEventListener("change", () => {
    const model = codexModels.find((item) => item.id === weixinTranslationModel.value);
    weixinTranslationReasoningEffort.value = model?.levels.some((level) => level.id === model.default_level)
      ? model.default_level
      : "";
    saveWeixinTranslationModelSettings();
  });
  weixinTranslationReasoningEffort.addEventListener("change", saveWeixinTranslationModelSettings);
}

function initializeOpenClawSettings() {
  loadOpenClawWeixinSettings();
  settingsOpenClawBindWeixin.addEventListener("click", openOpenClawWeixinDialog);
  openclawWeixinClose.addEventListener("click", closeOpenClawWeixinDialog);
  openclawWeixinStart.addEventListener("click", startOpenClawWeixinLogin);
  openclawWeixinCancel.addEventListener("click", cancelOpenClawWeixinLogin);
  openclawWeixinVerifyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const code = openclawWeixinVerifyCode.value.trim();
    if (!code) return;
    try {
      openclawWeixinVerifyCode.value = "";
      renderOpenClawWeixinLogin(await fetchSettingsApi("/api/openclaw/weixin/login/verify", { method: "POST", headers: settingsHeaders(true), body: JSON.stringify({ code }) }));
    } catch (_error) {
      setOpenClawWeixinMessage(openclawWeixinMessage, "验证码提交失败。", "error");
    }
  });
  openclawWeixinDialog.addEventListener("click", (event) => {
    if (event.target === openclawWeixinDialog) closeOpenClawWeixinDialog();
  });
  openclawWeixinDialog.addEventListener("close", () => {
    stopOpenClawWeixinPolling();
    releaseOpenClawWeixinQr();
  });
}

if (settingsPage === "quick-interaction") {
  quickInteractionPageSize.value = readQuickInteractionPageSize();
  initializeSettingsChoicePickers();
  quickInteractionPageSize.addEventListener("change", () => saveQuickInteractionPageSize(quickInteractionPageSize.value));
} else if (settingsPage === "appearance") {
  initializeAppearanceSettings();
} else if (settingsPage === "diagnostics") {
  initializeDiagnosticsSettings();
} else if (settingsPage === "runtime") {
  loadRuntimeManagement();
} else if (settingsPage === "session-defaults") {
  codexDefaultFullAccess.addEventListener("change", saveCodexSessionDefaults);
  loadCodexSessionDefaults();
} else if (settingsPage === "weixin-text") {
  initializeWeixinTextSettings();
} else if (settingsPage === "openclaw") {
  initializeOpenClawSettings();
}
