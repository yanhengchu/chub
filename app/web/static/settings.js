"use strict";

const QUICK_INTERACTION_PAGE_SIZE_KEY = "hub.quickInteractionPageSize.v1";
const CYBER_RAIN_SPEED_KEY = "hub.cyberRainSpeed.v1";
const CYBER_RAIN_BRIGHTNESS_KEY = "hub.cyberRainBrightness.v1";
const CYBER_RAIN_DENSITY_KEY = "hub.cyberRainDensity.v1";
const settingsMessage = document.querySelector("#settings-message");
const quickInteractionPageSize = document.querySelector(
  "#quick-interaction-page-size",
);
const cyberRainSpeed = document.querySelector("#cyber-rain-speed");
const cyberRainBrightness = document.querySelector("#cyber-rain-brightness");
const cyberRainDensity = document.querySelector("#cyber-rain-density");
const cyberRainSpeedValue = document.querySelector("#cyber-rain-speed-value");
const cyberRainBrightnessValue = document.querySelector("#cyber-rain-brightness-value");
const cyberRainDensityValue = document.querySelector("#cyber-rain-density-value");
const cyberStyleSettingsMessage = document.querySelector("#cyber-style-settings-message");
const styleOptionRows = document.querySelectorAll("[data-style-option]");

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

quickInteractionPageSize.addEventListener("change", () => {
  saveQuickInteractionPageSize(quickInteractionPageSize.value);
});

quickInteractionPageSize.value = readQuickInteractionPageSize();

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
