"use strict";

(() => {
  const STYLE_KEY = "hub.uiStyle.v1";
  const STYLE_COOKIE = "hub_ui_style";
  const CYBER_RAIN_SPEED_KEY = "hub.cyberRainSpeed.v1";
  const CYBER_RAIN_BRIGHTNESS_KEY = "hub.cyberRainBrightness.v1";
  const CYBER_RAIN_DENSITY_KEY = "hub.cyberRainDensity.v1";
  const root = document.documentElement;

  function readStyle() {
    const forcedStyle = root.dataset.stylePreview;
    if (forcedStyle === "standard" || forcedStyle === "cyber") {
      return forcedStyle;
    }
    try {
      const storedStyle = localStorage.getItem(STYLE_KEY);
      if (storedStyle === "standard" || storedStyle === "cyber") {
        return storedStyle;
      }
    } catch (_error) {
      // Fall through to the server-rendered style.
    }
    return root.dataset.uiStyle === "cyber" ? "cyber" : "standard";
  }

  function storeStyleCookie(style) {
    try {
      document.cookie = `${STYLE_COOKIE}=${style}; Path=/; Max-Age=31536000; SameSite=Lax`;
    } catch (_error) {
      // Local storage remains the primary browser preference.
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

  function updateColorScheme(style) {
    const scheme = style === "cyber" ? "dark" : "light";
    root.style.colorScheme = scheme;
    document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", scheme);
  }

  function removeCyberRain() {
    document.querySelector(".cyber-matrix")?.remove();
  }

  function createCyberRain() {
    if (!document.body || root.dataset.uiStyle !== "cyber") {
      return;
    }
    removeCyberRain();
    const speed = readRangePreference(CYBER_RAIN_SPEED_KEY, 60);
    const brightness = readRangePreference(CYBER_RAIN_BRIGHTNESS_KEY, 70) / 100;
    const density = readRangePreference(CYBER_RAIN_DENSITY_KEY, 50) / 100;
    const minBrightness = brightness * 0.5;
    const brightnessRange = brightness - minBrightness;
    const duration = 38 - speed * 0.3;
    root.style.setProperty("--cyber-rain-duration-main", `${duration}s`);
    root.style.setProperty("--cyber-rain-min", String(minBrightness));
    root.style.setProperty("--cyber-rain-step-1", String(minBrightness + brightnessRange * 0.17));
    root.style.setProperty("--cyber-rain-step-2", String(minBrightness + brightnessRange * 0.4));
    root.style.setProperty("--cyber-rain-step-3", String(minBrightness + brightnessRange * 0.63));
    root.style.setProperty("--cyber-rain-step-4", String(minBrightness + brightnessRange * 0.86));
    root.style.setProperty("--cyber-rain-max", String(brightness));

    const compact = window.matchMedia("(max-width: 420px)").matches;
    const minimumColumns = 2;
    const maximumColumns = compact ? 4 : 10;
    const columnCount = Math.round(
      minimumColumns + density * (maximumColumns - minimumColumns),
    );
    const matrix = document.createElement("div");
    matrix.className = "cyber-matrix";
    matrix.setAttribute("aria-hidden", "true");
    for (let columnIndex = 0; columnIndex < columnCount; columnIndex += 1) {
      const slotPosition = (columnIndex + 0.25 + Math.random() * 0.5) / columnCount;
      const streamDuration = duration * (0.88 + Math.random() * 0.24);
      const firstDelay = -Math.random() * streamDuration;
      const continuationOffset = streamDuration * (0.58 + Math.random() * 0.18);
      for (let sequence = 0; sequence < 2; sequence += 1) {
        const stream = document.createElement("span");
        stream.dataset.rainLane = String(columnIndex);
        stream.dataset.rainSequence = String(sequence);
        stream.style.left = `${slotPosition * 100}%`;
        stream.style.animationDuration = `${streamDuration}s`;
        stream.style.animationDelay = `${firstDelay - sequence * continuationOffset}s`;
        for (let characterIndex = 0; characterIndex < 16; characterIndex += 1) {
          const character = document.createElement("i");
          character.textContent = Math.random() < 0.5 ? "0" : "1";
          stream.append(character);
        }
        matrix.append(stream);
      }
    }
    document.body.prepend(matrix);
  }

  function applyStyle(style, options = {}) {
    const selected = style === "cyber" ? "cyber" : "standard";
    if (options.persist && !root.dataset.stylePreview) {
      try {
        localStorage.setItem(STYLE_KEY, selected);
      } catch (_error) {
        return false;
      }
      storeStyleCookie(selected);
    }
    root.dataset.uiStyle = selected;
    updateColorScheme(selected);
    if (document.body) {
      if (selected === "cyber") {
        createCyberRain();
      } else {
        removeCyberRain();
      }
    }
    document.dispatchEvent(new CustomEvent("chub:style-change", {
      detail: { style: selected },
    }));
    return true;
  }

  const initialStyle = readStyle();
  root.dataset.uiStyle = initialStyle;
  storeStyleCookie(initialStyle);
  updateColorScheme(initialStyle);
  window.ChubTheme = {
    applyStyle,
    currentStyle: () => root.dataset.uiStyle,
    refreshCyberRain: createCyberRain,
  };
  document.addEventListener("DOMContentLoaded", () => applyStyle(initialStyle));
  window.matchMedia("(max-width: 420px)").addEventListener("change", () => {
    if (root.dataset.uiStyle === "cyber") {
      createCyberRain();
    }
  });
})();
