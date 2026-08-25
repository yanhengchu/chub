"use strict";

const QUICK_INTERACTION_PAGE_SIZE_KEY = "hub.quickInteractionPageSize.v1";
const CODEX_DEFAULT_PERMISSION_KEY = "hub.codexDefaultPermission.v1";
const CODEX_DEFAULT_MODEL_KEY = "hub.codexDefaultModel.v1";
const CODEX_DEFAULT_REASONING_EFFORT_KEY = "hub.codexDefaultReasoningEffort.v1";
const CYBER_RAIN_SPEED_KEY = "hub.cyberRainSpeed.v1";
const CYBER_RAIN_BRIGHTNESS_KEY = "hub.cyberRainBrightness.v1";
const CYBER_RAIN_DENSITY_KEY = "hub.cyberRainDensity.v1";
const settingsMessage = document.querySelector("#settings-message");
const quickInteractionPageSize = document.querySelector(
  "#quick-interaction-page-size",
);
const codexDefaultPermission = document.querySelector("#codex-default-permission");
const codexDefaultModel = document.querySelector("#codex-default-model");
const codexDefaultReasoningEffort = document.querySelector(
  "#codex-default-reasoning-effort",
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
const weixinTranslationReasoningEffort = document.querySelector(
  "#weixin-translation-reasoning-effort",
);
const weixinTranslationReasoningEffortField = document.querySelector(
  "#weixin-translation-reasoning-effort-field",
);
const weixinTranslationMessage = document.querySelector(
  "#weixin-translation-message",
);
const cyberRainSpeed = document.querySelector("#cyber-rain-speed");
const cyberRainBrightness = document.querySelector("#cyber-rain-brightness");
const cyberRainDensity = document.querySelector("#cyber-rain-density");
const cyberRainSpeedValue = document.querySelector("#cyber-rain-speed-value");
const cyberRainBrightnessValue = document.querySelector("#cyber-rain-brightness-value");
const cyberRainDensityValue = document.querySelector("#cyber-rain-density-value");
const cyberStyleSettingsMessage = document.querySelector("#cyber-style-settings-message");
const styleOptionRows = document.querySelectorAll("[data-style-option]");
const cyberStyleDetails = document.querySelector("[data-cyber-style-details]");
const settingsNavigationLinks = document.querySelectorAll(
  ".settings-navigation-links a",
);
const settingsSections = document.querySelectorAll(".settings-content > section[id]");
let codexModels = [];
let weixinTranslationStatus = null;
let weixinTranslationPollTimer = null;
let weixinTranslationRequestVersion = 0;
let settingsScrollFrame = null;
let settingsNavigationTarget = null;

const CODEX_REASONING_LABELS = {
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra High",
  max: "Max",
  ultra: "Ultra",
};

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

function setActiveSettingsSection(sectionId) {
  settingsNavigationLinks.forEach((link) => {
    if (link.getAttribute("href") === `#${sectionId}`) {
      link.setAttribute("aria-current", "true");
    } else {
      link.removeAttribute("aria-current");
    }
  });
}

function scrollToSettingsSection(sectionId) {
  const section = document.getElementById(sectionId);
  if (!section) {
    return;
  }
  settingsNavigationTarget = sectionId;
  setActiveSettingsSection(sectionId);
  section.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "auto"
      : "smooth",
    block: "start",
  });
}

function updateActiveSettingsSection() {
  settingsScrollFrame = null;
  if (settingsNavigationTarget) {
    setActiveSettingsSection(settingsNavigationTarget);
    return;
  }
  const threshold = 120;
  let activeSection = settingsSections[0];
  settingsSections.forEach((section) => {
    if (section.getBoundingClientRect().top <= threshold) {
      activeSection = section;
    }
  });
  if (
    window.innerHeight + window.scrollY
    >= document.documentElement.scrollHeight - 2
  ) {
    activeSection = settingsSections[settingsSections.length - 1];
  }
  if (activeSection) {
    setActiveSettingsSection(activeSection.id);
  }
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

function readCodexDefaultPermission() {
  try {
    const value = localStorage.getItem(CODEX_DEFAULT_PERMISSION_KEY);
    return ["ask", "auto-review", "read-only", "full-access"].includes(value)
      ? value
      : "full-access";
  } catch (_error) {
    return "full-access";
  }
}

function saveCodexDefaultPermission(value) {
  const selected = ["ask", "auto-review", "read-only", "full-access"].includes(value)
    ? value
    : "full-access";
  try {
    localStorage.setItem(CODEX_DEFAULT_PERMISSION_KEY, selected);
    codexDefaultPermission.value = selected;
    codexSessionSettingsMessage.textContent = "";
    codexSessionSettingsMessage.className = "message";
  } catch (_error) {
    codexDefaultPermission.value = readCodexDefaultPermission();
    codexSessionSettingsMessage.textContent = "当前浏览器无法保存会话偏好。";
    codexSessionSettingsMessage.className = "message message-error";
  }
}

function readCodexPreference(key) {
  try {
    return localStorage.getItem(key) || "";
  } catch (_error) {
    return "";
  }
}

function saveCodexPreference(key, value) {
  if (value) {
    localStorage.setItem(key, value);
  } else {
    localStorage.removeItem(key);
  }
}

function createOption(value, label) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = label;
  return option;
}

function defaultModelOptionLabel() {
  return "跟随 Codex 默认";
}

function defaultReasoningOptionLabel() {
  return "跟随 Codex 默认";
}

function selectedCodexModel() {
  return codexModels.find((model) => model.id === codexDefaultModel.value) || null;
}

function renderCodexReasoningLevels(preferred = "") {
  const model = selectedCodexModel();
  const options = [createOption("", defaultReasoningOptionLabel())];
  if (model) {
    model.levels.forEach((level) => {
      options.push(
        createOption(level.id, CODEX_REASONING_LABELS[level.id] || level.id),
      );
    });
  }
  codexDefaultReasoningEffort.replaceChildren(...options);
  const supported = model?.levels.some((level) => level.id === preferred);
  codexDefaultReasoningEffort.value = supported ? preferred : "";
  codexDefaultReasoningEffort.disabled = !model;
  return Boolean(supported || !preferred);
}

function renderCodexModels(data) {
  const models = data.models;
  const storedModel = readCodexPreference(CODEX_DEFAULT_MODEL_KEY);
  const storedEffort = readCodexPreference(
    CODEX_DEFAULT_REASONING_EFFORT_KEY,
  );
  const preferredModel = storedModel || data.default_model || "";
  const preferredEffort = storedEffort || data.default_reasoning_effort || "";
  codexModels = models;
  const options = [createOption("", defaultModelOptionLabel())];
  models.forEach((model) => {
    options.push(createOption(model.id, model.name));
  });
  codexDefaultModel.replaceChildren(...options);
  const modelAvailable = models.some((model) => model.id === preferredModel);
  codexDefaultModel.value = modelAvailable ? preferredModel : "";
  codexDefaultModel.disabled = false;
  const effortAvailable = renderCodexReasoningLevels(
    modelAvailable ? preferredEffort : "",
  );
  if (
    (!modelAvailable && storedModel)
    || (!storedModel && storedEffort)
    || !effortAvailable
  ) {
    try {
      saveCodexPreference(CODEX_DEFAULT_MODEL_KEY, codexDefaultModel.value);
      saveCodexPreference(
        CODEX_DEFAULT_REASONING_EFFORT_KEY,
        codexDefaultReasoningEffort.value,
      );
      codexSessionSettingsMessage.textContent =
        "之前保存的模型或等级当前不可用，已改为节点默认。";
      codexSessionSettingsMessage.className = "message message-error";
    } catch (_error) {
      codexSessionSettingsMessage.textContent = "当前浏览器无法保存会话偏好。";
      codexSessionSettingsMessage.className = "message message-error";
    }
  }
  if (weixinTranslationStatus !== null) {
    renderWeixinTranslationModelSettings(weixinTranslationStatus);
  }
}

function renderWeixinTranslationModelSettings(status) {
  const options = [createOption("", defaultModelOptionLabel())];
  if (
    status.model
    && !codexModels.some((model) => model.id === status.model)
  ) {
    options.push(createOption(status.model, `${status.model}（当前不可用）`));
  }
  codexModels.forEach((model) => {
    options.push(createOption(model.id, model.name));
  });
  weixinTranslationModel.replaceChildren(...options);
  weixinTranslationModel.value = status.model || "";
  weixinTranslationModel.disabled = codexModels.length === 0 && !status.model;

  const model = codexModels.find(
    (item) => item.id === weixinTranslationModel.value,
  );
  const levels = [createOption("", "跟随模型默认")];
  if (model) {
    model.levels.forEach((level) => {
      levels.push(
        createOption(level.id, CODEX_REASONING_LABELS[level.id] || level.id),
      );
    });
  }
  weixinTranslationReasoningEffort.replaceChildren(...levels);
  weixinTranslationReasoningEffort.value = model
    && model.levels.some((level) => level.id === status.reasoning_effort)
    ? status.reasoning_effort
    : "";
  weixinTranslationReasoningEffort.disabled = !model;
  weixinTranslationModelField.hidden = false;
  weixinTranslationReasoningEffortField.hidden = false;
}

async function syncCodexSessionDefaults() {
  const response = await fetch("/api/codex/session-defaults", {
    method: "PUT",
    headers: settingsHeaders(true),
    body: JSON.stringify({
      model: codexDefaultModel.value || null,
      reasoning_effort: codexDefaultReasoningEffort.value || null,
    }),
  });
  const payload = await response.json();
  if (!response.ok || payload.success !== true) {
    throw new Error(payload.error?.code || "session_defaults_update_failed");
  }
}

async function loadCodexModels() {
  try {
    const response = await fetch("/api/codex/models");
    const payload = await response.json();
    if (!response.ok || payload.success !== true || !Array.isArray(payload.data?.models)) {
      throw new Error("model_catalog_unavailable");
    }
    renderCodexModels(payload.data);
    if (
      readCodexPreference(CODEX_DEFAULT_MODEL_KEY)
      || readCodexPreference(CODEX_DEFAULT_REASONING_EFFORT_KEY)
    ) {
      try {
        await syncCodexSessionDefaults();
      } catch (_error) {
        codexSessionSettingsMessage.textContent =
          "浏览器偏好已保存，但节点默认同步失败；微信新建仍会使用旧默认。";
        codexSessionSettingsMessage.className = "message message-error";
      }
    }
  } catch (_error) {
    try {
      saveCodexPreference(CODEX_DEFAULT_MODEL_KEY, "");
      saveCodexPreference(CODEX_DEFAULT_REASONING_EFFORT_KEY, "");
    } catch (_storageError) {
      // The error message below already covers unavailable browser storage.
    }
    codexDefaultModel.replaceChildren(
      createOption("", defaultModelOptionLabel()),
    );
    codexDefaultModel.value = "";
    codexDefaultModel.disabled = true;
    codexDefaultReasoningEffort.replaceChildren(
      createOption("", defaultReasoningOptionLabel()),
    );
    codexDefaultReasoningEffort.value = "";
    codexDefaultReasoningEffort.disabled = true;
    codexSessionSettingsMessage.textContent =
      "暂时无法读取 Codex 模型，已改为跟随 Codex 默认。";
    codexSessionSettingsMessage.className = "message message-error";
  }
}

function settingsHeaders(includeJson = false) {
  const headers = {};
  if (includeJson) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

function renderWeixinTranslationStatus(status) {
  weixinTranslationStatus = status;
  const selected = status.mode || (status.enabled ? "auto" : "direct");
  for (const input of weixinProcessingModeInputs) {
    input.checked = input.value === selected;
  }
  renderWeixinTranslationModelSettings(status);
  weixinProcessingMode.disabled = false;
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
    return true;
  } catch (_error) {
    if (requestVersion !== weixinTranslationRequestVersion) {
      return false;
    }
    weixinProcessingMode.disabled = true;
    weixinTranslationModel.disabled = true;
    weixinTranslationReasoningEffort.disabled = true;
    weixinTranslationModelField.hidden = true;
    weixinTranslationReasoningEffortField.hidden = true;
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
  } catch (_error) {
    await loadWeixinTranslationStatus(
      "翻译模型设置结果未知，请稍后刷新页面重试。",
    );
  }
}

quickInteractionPageSize.addEventListener("change", () => {
  saveQuickInteractionPageSize(quickInteractionPageSize.value);
});

quickInteractionPageSize.value = readQuickInteractionPageSize();
codexDefaultPermission.value = readCodexDefaultPermission();

codexDefaultPermission.addEventListener("change", () => {
  saveCodexDefaultPermission(codexDefaultPermission.value);
});

codexDefaultModel.addEventListener("change", async () => {
  try {
    saveCodexPreference(CODEX_DEFAULT_MODEL_KEY, codexDefaultModel.value);
    renderCodexReasoningLevels();
    saveCodexPreference(CODEX_DEFAULT_REASONING_EFFORT_KEY, "");
    codexDefaultModel.disabled = true;
    codexDefaultReasoningEffort.disabled = true;
    await syncCodexSessionDefaults();
    codexDefaultModel.disabled = false;
    codexDefaultReasoningEffort.disabled = !selectedCodexModel();
    codexSessionSettingsMessage.textContent = "";
    codexSessionSettingsMessage.className = "message";
  } catch (_error) {
    codexDefaultModel.disabled = false;
    codexDefaultReasoningEffort.disabled = !selectedCodexModel();
    codexSessionSettingsMessage.textContent = "节点默认保存失败，请稍后重试。";
    codexSessionSettingsMessage.className = "message message-error";
  }
});

codexDefaultReasoningEffort.addEventListener("change", async () => {
  try {
    saveCodexPreference(
      CODEX_DEFAULT_REASONING_EFFORT_KEY,
      codexDefaultReasoningEffort.value,
    );
    codexDefaultModel.disabled = true;
    codexDefaultReasoningEffort.disabled = true;
    await syncCodexSessionDefaults();
    codexDefaultModel.disabled = false;
    codexDefaultReasoningEffort.disabled = false;
    codexSessionSettingsMessage.textContent = "";
    codexSessionSettingsMessage.className = "message";
  } catch (_error) {
    codexDefaultModel.disabled = false;
    codexDefaultReasoningEffort.disabled = !selectedCodexModel();
    codexSessionSettingsMessage.textContent = "节点默认保存失败，请稍后重试。";
    codexSessionSettingsMessage.className = "message message-error";
  }
});

loadCodexModels();
loadWeixinTranslationStatus();

for (const input of weixinProcessingModeInputs) {
  input.addEventListener("change", () => {
    if (input.checked) saveWeixinTranslationStatus(input.value);
  });
}

weixinTranslationModel.addEventListener("change", () => {
  const model = codexModels.find(
    (item) => item.id === weixinTranslationModel.value,
  );
  if (model) {
    const defaultLevel = model.default_level || "";
    const supported = model.levels.some((level) => level.id === defaultLevel);
    weixinTranslationReasoningEffort.value = supported ? defaultLevel : "";
  } else {
    weixinTranslationReasoningEffort.value = "";
  }
  saveWeixinTranslationModelSettings();
});

weixinTranslationReasoningEffort.addEventListener("change", () => {
  saveWeixinTranslationModelSettings();
});

cyberRainSpeed.value = String(readRangePreference(CYBER_RAIN_SPEED_KEY, 60));
cyberRainBrightness.value = String(readRangePreference(CYBER_RAIN_BRIGHTNESS_KEY, 70));
cyberRainDensity.value = String(readRangePreference(CYBER_RAIN_DENSITY_KEY, 50));
cyberRainSpeedValue.value = `${cyberRainSpeed.value}%`;
cyberRainBrightnessValue.value = `${cyberRainBrightness.value}%`;
cyberRainDensityValue.value = `${cyberRainDensity.value}%`;

cyberRainSpeed.addEventListener("input", () => {
  updateCyberControl(cyberRainSpeed, cyberRainSpeedValue, CYBER_RAIN_SPEED_KEY);
});

cyberRainBrightness.addEventListener("input", () => {
  updateCyberControl(
    cyberRainBrightness,
    cyberRainBrightnessValue,
    CYBER_RAIN_BRIGHTNESS_KEY,
  );
});

cyberRainDensity.addEventListener("input", () => {
  updateCyberControl(cyberRainDensity, cyberRainDensityValue, CYBER_RAIN_DENSITY_KEY);
});

styleOptionRows.forEach((row) => {
  const button = row.querySelector("[data-style-apply]");
  button.addEventListener("click", () => {
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

settingsNavigationLinks.forEach((link) => {
  link.addEventListener("click", (event) => {
    event.preventDefault();
    scrollToSettingsSection(link.getAttribute("href").slice(1));
  });
});

window.addEventListener("scroll", () => {
  if (settingsScrollFrame === null) {
    settingsScrollFrame = window.requestAnimationFrame(updateActiveSettingsSection);
  }
}, { passive: true });

function releaseSettingsNavigationTarget() {
  settingsNavigationTarget = null;
}

window.addEventListener("wheel", releaseSettingsNavigationTarget, { passive: true });
window.addEventListener("touchstart", releaseSettingsNavigationTarget, { passive: true });
window.addEventListener("pointerdown", releaseSettingsNavigationTarget, { passive: true });
window.addEventListener("keydown", (event) => {
  if (
    ["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "]
      .includes(event.key)
  ) {
    releaseSettingsNavigationTarget();
  }
});

renderStyleSelection(window.ChubTheme.currentStyle());
updateActiveSettingsSection();
