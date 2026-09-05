"use strict";

(() => {
  const STYLE_KEY = "hub.uiStyle.v1";
  const STYLE_COOKIE = "hub_ui_style";
  const FONT_SIZE_KEY = "hub.uiFontSize.v1";
  const FONT_SIZE_COOKIE = "hub_ui_font_size";
  const root = document.documentElement;
  const STYLE_SCHEMES = new Map(
    (root.dataset.uiThemeSchemes || "").split(",").flatMap((entry) => {
      const [style, scheme] = entry.split(":");
      return style && (scheme === "light" || scheme === "dark") ? [[style, scheme]] : [];
    }),
  );
  const DEFAULT_STYLE = STYLE_SCHEMES.has(root.dataset.uiThemeDefault)
    ? root.dataset.uiThemeDefault
    : STYLE_SCHEMES.keys().next().value || "standard";
  const VALID_STYLES = new Set(STYLE_SCHEMES.keys());
  const FONT_SIZE_SCALES = new Map(
    (root.dataset.uiFontSizeScales || "").split(",").flatMap((entry) => {
      const [fontSize, scale] = entry.split(":");
      return fontSize && Number.isFinite(Number(scale)) && Number(scale) > 0
        ? [[fontSize, Number(scale)]]
        : [];
    }),
  );
  const DEFAULT_FONT_SIZE = FONT_SIZE_SCALES.has(root.dataset.uiFontSizeDefault)
    ? root.dataset.uiFontSizeDefault
    : FONT_SIZE_SCALES.keys().next().value || "default";
  const VALID_FONT_SIZES = new Set(FONT_SIZE_SCALES.keys());

  function normalizeStyle(style) { return VALID_STYLES.has(style) ? style : DEFAULT_STYLE; }
  function normalizeFontSize(fontSize) {
    return VALID_FONT_SIZES.has(fontSize) ? fontSize : DEFAULT_FONT_SIZE;
  }

  function readStyle() {
    const previewStyle = root.dataset.stylePreview;
    if (previewStyle) return normalizeStyle(previewStyle);
    try {
      const storedStyle = localStorage.getItem(STYLE_KEY);
      if (storedStyle && !VALID_STYLES.has(storedStyle)) localStorage.removeItem(STYLE_KEY);
      if (VALID_STYLES.has(storedStyle)) return storedStyle;
    } catch (_error) {
      // Fall through to the server-rendered style.
    }
    return normalizeStyle(root.dataset.uiStyle);
  }

  function storeStyleCookie(style) {
    try { document.cookie = `${STYLE_COOKIE}=${style}; Path=/; Max-Age=31536000; SameSite=Lax`; }
    catch (_error) { /* Local storage remains the primary browser preference. */ }
  }

  function readFontSize() {
    try {
      const storedFontSize = localStorage.getItem(FONT_SIZE_KEY);
      if (storedFontSize && !VALID_FONT_SIZES.has(storedFontSize)) {
        localStorage.removeItem(FONT_SIZE_KEY);
      }
      if (VALID_FONT_SIZES.has(storedFontSize)) return storedFontSize;
    } catch (_error) {
      // Fall through to the server-rendered preference.
    }
    return normalizeFontSize(root.dataset.uiFontSize);
  }

  function storeFontSizeCookie(fontSize) {
    try { document.cookie = `${FONT_SIZE_COOKIE}=${fontSize}; Path=/; Max-Age=31536000; SameSite=Lax`; }
    catch (_error) { /* Local storage remains the primary browser preference. */ }
  }

  function updateColorScheme(style) {
    const scheme = STYLE_SCHEMES.get(style) || "light";
    root.style.colorScheme = scheme;
    document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", scheme);
  }

  function applyStyle(style, options = {}) {
    const selected = normalizeStyle(style);
    let persisted = true;
    if (options.persist && !root.dataset.stylePreview) {
      try {
        localStorage.setItem(STYLE_KEY, selected);
        storeStyleCookie(selected);
      } catch (_error) {
        persisted = false;
      }
    }
    root.dataset.uiStyle = selected;
    updateColorScheme(selected);
    document.dispatchEvent(new CustomEvent("chub:style-change", {
      detail: { style: selected, persisted },
    }));
    return { style: selected, persisted };
  }

  function applyFontSize(fontSize, options = {}) {
    const selected = normalizeFontSize(fontSize);
    let persisted = true;
    if (options.persist) {
      try {
        localStorage.setItem(FONT_SIZE_KEY, selected);
        storeFontSizeCookie(selected);
      } catch (_error) {
        persisted = false;
      }
    }
    root.dataset.uiFontSize = selected;
    document.dispatchEvent(new CustomEvent("chub:font-size-change", {
      detail: { fontSize: selected, persisted },
    }));
    return { fontSize: selected, persisted };
  }

  const initialStyle = readStyle();
  const initialFontSize = readFontSize();
  root.dataset.uiStyle = initialStyle;
  root.dataset.uiFontSize = initialFontSize;
  storeStyleCookie(initialStyle);
  storeFontSizeCookie(initialFontSize);
  updateColorScheme(initialStyle);
  window.ChubTheme = {
    applyStyle,
    applyFontSize,
    currentStyle: () => root.dataset.uiStyle,
    currentFontSize: () => root.dataset.uiFontSize,
  };
  document.addEventListener("DOMContentLoaded", () => {
    applyStyle(initialStyle);
    applyFontSize(initialFontSize);
  });
})();
