"use strict";

(() => {
  const STYLE_KEY = "hub.uiStyle.v1";
  const STYLE_COOKIE = "hub_ui_style";
  const CYBER_RAIN_SPEED_KEY = "hub.cyberRainSpeed.v1";
  const CYBER_RAIN_BRIGHTNESS_KEY = "hub.cyberRainBrightness.v1";
  const CYBER_RAIN_DENSITY_KEY = "hub.cyberRainDensity.v1";
  const AI_USAGE_CACHE_KEY = "hub.aiUsageCache";
  const AI_USAGE_REFRESH_MS = 5 * 60 * 1000;
  const RAIN_STREAM_LENGTH = 16;
  const RAIN_PHRASES = [
    "good morning",
    "good evening",
    "welcome back",
    "see you soon",
    "have a nice day",
    "take your time",
    "take a break",
    "stay curious",
    "keep going",
    "keep it simple",
    "enjoy the moment",
    "one step ahead",
    "make it count",
    "here to help",
    "all is well",
    "things look good",
    "fresh start",
    "new day begins",
    "stay positive",
    "almost there",
    "on my way",
    "all set",
    "sounds good",
    "talk soon",
    "build passing",
    "tests are green",
    "code reviewed",
    "ready to ship",
    "deploy complete",
    "branch updated",
    "commit pushed",
    "issue resolved",
    "bug is fixed",
    "cache refreshed",
    "service healthy",
    "worker running",
    "session active",
    "signal stable",
    "system ready",
    "sync complete",
    "request queued",
    "task complete",
    "logs are clean",
    "release ready",
    "data synced",
    "checks passed",
    "review complete",
    "patch applied",
    "server online",
    "network stable",
    "status nominal",
    "terminal ready",
    "thanks again",
    "nice work",
    "well done",
    "please review",
    "quick update",
    "good question",
    "feedback welcome",
    "message received",
    "request received",
    "that makes sense",
    "on the same page",
    "let us discuss",
    "keep me posted",
    "happy to help",
    "thanks for this",
    "looking good",
    "works for me",
    "please continue",
    "noted thanks",
    "great idea",
    "good point",
    "let us sync",
    "talk it through",
    "clear and simple",
  ];
  const root = document.documentElement;
  let cyberRainQuotaParts = [];
  let cyberRainDynamicStreams = [];
  let aiUsageSnapshot = null;
  let aiUsageLoadedAt = 0;
  let aiUsageLoadPromise = null;
  let aiUsageVersion = 0;

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
    cyberRainDynamicStreams = [];
  }

  function randomRainText(characters) {
    let value = "";
    for (let index = 0; index < RAIN_STREAM_LENGTH; index += 1) {
      value += characters[Math.floor(Math.random() * characters.length)];
    }
    return value;
  }

  function randomBaseRain() {
    const phrase = Math.random() < 0.5;
    return {
      kind: phrase ? "phrase" : "binary",
      text: phrase ? randomPhraseRain() : randomRainText("01"),
    };
  }

  function randomPhraseRain() {
    const phrase = RAIN_PHRASES[Math.floor(Math.random() * RAIN_PHRASES.length)];
    const separator = Math.random() < 0.5 ? "." : " ";
    return phrase.replaceAll(" ", separator);
  }

  function renderRainText(stream, value, paddedLength = RAIN_STREAM_LENGTH) {
    const characters = Array.from(value);
    const padding = Math.max(0, paddedLength - characters.length);
    const nodes = [];
    for (let index = 0; index < padding; index += 1) {
      const space = document.createElement("i");
      space.dataset.rainSpace = "true";
      space.textContent = "\u00a0";
      nodes.push(space);
    }
    characters.forEach((valueCharacter) => {
      const character = document.createElement("i");
      if (/\s/u.test(valueCharacter)) {
        character.dataset.rainSpace = "true";
        character.textContent = "\u00a0";
      } else {
        character.textContent = valueCharacter;
      }
      if (nodes.length === padding) {
        character.dataset.rainStart = "true";
      }
      nodes.push(character);
    });
    stream.replaceChildren(...nodes);
  }

  function updateBaseRainStream(stream) {
    const content = randomBaseRain();
    stream.dataset.rainKind = content.kind;
    renderRainText(stream, content.text);
  }

  function quotaRainParts(longText) {
    if (typeof longText !== "string" || !longText || longText.length > 256) {
      return [];
    }
    const weekly = [];
    const today = [];
    longText.split(" · ").forEach((part) => {
      const value = part.trim();
      if (!value) {
        return;
      }
      if (/^today\b/iu.test(value)) {
        today.push(value);
      } else {
        weekly.push(value);
      }
    });
    const parts = [weekly.join(" · "), today.join(" · ")]
      .map((part) => part.toLowerCase());
    return parts.some(Boolean) ? parts : [];
  }

  function dynamicRainLength() {
    return Math.max(
      RAIN_STREAM_LENGTH,
      ...cyberRainQuotaParts.map((part) => Array.from(part).length),
    );
  }

  function scaledRainDuration(baseDuration, characterCount) {
    const rootFontSize = Number.parseFloat(
      window.getComputedStyle?.(root)?.fontSize || "16",
    ) || 16;
    const characterHeight = rootFontSize * 0.68 * 1.7;
    const viewportHeight = window.innerHeight || root.clientHeight || 800;
    const baseDistance = viewportHeight + characterHeight * RAIN_STREAM_LENGTH;
    const nextDistance = viewportHeight + characterHeight * characterCount;
    return baseDuration * nextDistance / baseDistance;
  }

  function updateDynamicRainStream(state) {
    const stream = state.stream;
    const paddedLength = dynamicRainLength();
    const quotaText = cyberRainQuotaParts[state.quotaIndex] || "";
    if (quotaText) {
      stream.dataset.rainKind = "quota";
      renderRainText(stream, quotaText, paddedLength);
    } else {
      const content = randomBaseRain();
      stream.dataset.rainKind = content.kind;
      renderRainText(stream, content.text, paddedLength);
    }
    const duration = scaledRainDuration(state.baseDuration, paddedLength);
    stream.style.animationDuration = `${duration}s`;
    return duration;
  }

  function setCyberRainQuota(longText) {
    const nextParts = quotaRainParts(longText);
    if (JSON.stringify(nextParts) === JSON.stringify(cyberRainQuotaParts)) {
      return;
    }
    cyberRainQuotaParts = nextParts;
    cyberRainDynamicStreams.forEach((state, index) => {
      const stream = state.stream;
      state.quotaIndex = index;
      stream.style.visibility = "hidden";
      stream.style.animation = "none";
      void stream.offsetHeight;
      stream.style.animation = "";
      const duration = updateDynamicRainStream(state);
      stream.style.animationDelay = `${-duration * 0.6}s`;
      stream.style.visibility = "";
    });
  }

  function readAiUsageToken() {
    try {
      return sessionStorage.getItem("hub.sessionToken")
        || localStorage.getItem("hub.savedToken")
        || "";
    } catch (_error) {
      return "";
    }
  }

  function applyAiUsageRain(data) {
    aiUsageSnapshot = data && typeof data === "object" ? data : null;
    const longText = aiUsageSnapshot?.status === "available"
      && typeof aiUsageSnapshot?.display?.long === "string"
      ? aiUsageSnapshot.display.long
      : "";
    setCyberRainQuota(longText);
  }

  async function loadAiUsage({ force = false } = {}) {
    if (root.dataset.stylePreview) {
      return null;
    }
    if (
      !force
      && aiUsageSnapshot
      && Date.now() - aiUsageLoadedAt < AI_USAGE_REFRESH_MS
    ) {
      return aiUsageSnapshot;
    }
    if (aiUsageLoadPromise) {
      return aiUsageLoadPromise;
    }
    const token = readAiUsageToken();
    const requestVersion = aiUsageVersion;
    const loadPromise = (async () => {
      try {
        const response = await fetch(
          `/api/ai/usage${force ? "?refresh=true" : ""}`,
          {
            cache: "no-store",
            headers: token ? { Authorization: `Bearer ${token}` } : {},
          },
        );
        const payload = await response.json();
        if (!response.ok || payload.success !== true) {
          throw new Error("AI usage unavailable");
        }
        if (requestVersion !== aiUsageVersion) {
          return null;
        }
        applyAiUsageRain(payload.data);
        try {
          sessionStorage.setItem(AI_USAGE_CACHE_KEY, JSON.stringify(payload.data));
        } catch (_error) {
          // A storage quota failure must not affect the current page.
        }
        aiUsageLoadedAt = Date.now();
        return payload.data;
      } catch (error) {
        if (requestVersion === aiUsageVersion) {
          applyAiUsageRain(null);
        }
        throw error;
      }
    })();
    aiUsageLoadPromise = loadPromise;
    try {
      return await loadPromise;
    } finally {
      if (aiUsageLoadPromise === loadPromise) {
        aiUsageLoadPromise = null;
      }
    }
  }

  function clearAiUsage() {
    aiUsageVersion += 1;
    aiUsageLoadPromise = null;
    aiUsageSnapshot = null;
    aiUsageLoadedAt = 0;
    applyAiUsageRain(null);
  }

  function refreshAiUsageRain() {
    if (root.dataset.uiStyle === "cyber" && !root.dataset.stylePreview) {
      void loadAiUsage().catch(() => {});
    }
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
    const minimumColumns = compact ? 3 : 2;
    const maximumColumns = compact ? 4 : 10;
    const columnCount = Math.round(
      minimumColumns + density * (maximumColumns - minimumColumns),
    );
    const matrix = document.createElement("div");
    matrix.className = "cyber-matrix";
    matrix.setAttribute("aria-hidden", "true");
    const dynamicLanes = new Set([0, columnCount - 1]);
    let dynamicIndex = 0;
    for (let columnIndex = 0; columnIndex < columnCount; columnIndex += 1) {
      const slotPosition = (columnIndex + 0.25 + Math.random() * 0.5) / columnCount;
      const streamDuration = duration * (0.88 + Math.random() * 0.24);
      const firstDelay = -Math.random() * streamDuration;
      const continuationOffset = streamDuration * (0.58 + Math.random() * 0.18);
      if (dynamicLanes.has(columnIndex)) {
        const stream = document.createElement("span");
        stream.dataset.rainLane = String(columnIndex);
        stream.dataset.rainSequence = "0";
        stream.dataset.rainDynamic = "true";
        stream.style.left = `${slotPosition * 100}%`;
        const state = {
          stream,
          baseDuration: streamDuration,
          quotaIndex: dynamicIndex,
        };
        cyberRainDynamicStreams.push(state);
        dynamicIndex += 1;
        const dynamicDuration = updateDynamicRainStream(state);
        stream.style.animationDelay = cyberRainQuotaParts.length > 0
          ? `${-dynamicDuration * 0.6}s`
          : `${firstDelay}s`;
        stream.addEventListener("animationiteration", () => {
          updateDynamicRainStream(state);
        });
        matrix.append(stream);
        continue;
      }
      for (let sequence = 0; sequence < 2; sequence += 1) {
        const stream = document.createElement("span");
        stream.dataset.rainLane = String(columnIndex);
        stream.dataset.rainSequence = String(sequence);
        stream.style.left = `${slotPosition * 100}%`;
        stream.style.animationDuration = `${streamDuration}s`;
        stream.style.animationDelay = `${firstDelay - sequence * continuationOffset}s`;
        updateBaseRainStream(stream);
        stream.addEventListener("animationiteration", () => {
          updateBaseRainStream(stream);
        });
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
        refreshAiUsageRain();
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
    setCyberRainQuota,
    loadAiUsage,
    clearAiUsage,
  };
  document.addEventListener("DOMContentLoaded", () => applyStyle(initialStyle));
  window.matchMedia("(max-width: 420px)").addEventListener("change", () => {
    if (root.dataset.uiStyle === "cyber") {
      createCyberRain();
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      refreshAiUsageRain();
    }
  });
})();
