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
const cyberRainSpeed = document.querySelector("#cyber-rain-speed");
const cyberRainBrightness = document.querySelector("#cyber-rain-brightness");
const cyberRainDensity = document.querySelector("#cyber-rain-density");
const cyberRainSpeedValue = document.querySelector("#cyber-rain-speed-value");
const cyberRainBrightnessValue = document.querySelector("#cyber-rain-brightness-value");
const cyberRainDensityValue = document.querySelector("#cyber-rain-density-value");
const cyberStyleSettingsMessage = document.querySelector("#cyber-style-settings-message");
const styleOptionRows = document.querySelectorAll("[data-style-option]");
let codexModels = [];

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
    settingsMessage.textContent = `已设置为每页 ${selected} 条，下次进入快速交互时生效。`;
    settingsMessage.className = "message message-success";
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
    codexSessionSettingsMessage.textContent = "已保存，之后新建的 Session 将使用该权限。";
    codexSessionSettingsMessage.className = "message message-success";
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

function settingsToken() {
  try {
    return (
      sessionStorage.getItem("hub.sessionToken")
      || localStorage.getItem("hub.savedToken")
      || ""
    );
  } catch (_error) {
    return "";
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

function renderCodexModels(models) {
  const preferredModel = readCodexPreference(CODEX_DEFAULT_MODEL_KEY);
  const preferredEffort = readCodexPreference(
    CODEX_DEFAULT_REASONING_EFFORT_KEY,
  );
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
    (!modelAvailable && preferredModel)
    || (!preferredModel && preferredEffort)
    || !effortAvailable
  ) {
    try {
      saveCodexPreference(CODEX_DEFAULT_MODEL_KEY, codexDefaultModel.value);
      saveCodexPreference(
        CODEX_DEFAULT_REASONING_EFFORT_KEY,
        codexDefaultReasoningEffort.value,
      );
      codexSessionSettingsMessage.textContent =
        "之前保存的模型或等级当前不可用，已改为跟随 Codex 默认。";
      codexSessionSettingsMessage.className = "message message-error";
    } catch (_error) {
      codexSessionSettingsMessage.textContent = "当前浏览器无法保存会话偏好。";
      codexSessionSettingsMessage.className = "message message-error";
    }
  }
}

async function loadCodexModels() {
  const token = settingsToken();
  try {
    const response = await fetch("/api/codex/models", {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    const payload = await response.json();
    if (!response.ok || payload.success !== true || !Array.isArray(payload.data?.models)) {
      throw new Error("model_catalog_unavailable");
    }
    renderCodexModels(payload.data.models);
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

quickInteractionPageSize.addEventListener("change", () => {
  saveQuickInteractionPageSize(quickInteractionPageSize.value);
});

quickInteractionPageSize.value = readQuickInteractionPageSize();
codexDefaultPermission.value = readCodexDefaultPermission();

codexDefaultPermission.addEventListener("change", () => {
  saveCodexDefaultPermission(codexDefaultPermission.value);
});

codexDefaultModel.addEventListener("change", () => {
  try {
    saveCodexPreference(CODEX_DEFAULT_MODEL_KEY, codexDefaultModel.value);
    renderCodexReasoningLevels();
    saveCodexPreference(CODEX_DEFAULT_REASONING_EFFORT_KEY, "");
    codexSessionSettingsMessage.textContent = "";
    codexSessionSettingsMessage.className = "message";
  } catch (_error) {
    renderCodexModels(codexModels);
    codexSessionSettingsMessage.textContent = "当前浏览器无法保存会话偏好。";
    codexSessionSettingsMessage.className = "message message-error";
  }
});

codexDefaultReasoningEffort.addEventListener("change", () => {
  try {
    saveCodexPreference(
      CODEX_DEFAULT_REASONING_EFFORT_KEY,
      codexDefaultReasoningEffort.value,
    );
    codexSessionSettingsMessage.textContent = "";
    codexSessionSettingsMessage.className = "message";
  } catch (_error) {
    renderCodexReasoningLevels(
      readCodexPreference(CODEX_DEFAULT_REASONING_EFFORT_KEY),
    );
    codexSessionSettingsMessage.textContent = "当前浏览器无法保存会话偏好。";
    codexSessionSettingsMessage.className = "message message-error";
  }
});

loadCodexModels();

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
    } else {
      cyberStyleSettingsMessage.textContent = "当前浏览器无法保存界面偏好。";
      cyberStyleSettingsMessage.className = "message message-error";
    }
  });
});

renderStyleSelection(window.ChubTheme.currentStyle());
