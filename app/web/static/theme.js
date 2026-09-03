"use strict";

(() => {
  const STYLE_KEY = "hub.uiStyle.v1";
  const STYLE_COOKIE = "hub_ui_style";
  const root = document.documentElement;
  const VALID_STYLES = new Set(["standard", "code-dark"]);

  function normalizeStyle(style) { return VALID_STYLES.has(style) ? style : "standard"; }

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

  function updateColorScheme(style) {
    const scheme = style === "code-dark" ? "dark" : "light";
    root.style.colorScheme = scheme;
    document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", scheme);
  }

  function applyStyle(style, options = {}) {
    const selected = normalizeStyle(style);
    if (options.persist && !root.dataset.stylePreview) {
      try { localStorage.setItem(STYLE_KEY, selected); } catch (_error) { return false; }
      storeStyleCookie(selected);
    }
    root.dataset.uiStyle = selected;
    updateColorScheme(selected);
    document.dispatchEvent(new CustomEvent("chub:style-change", { detail: { style: selected } }));
    return true;
  }

  const initialStyle = readStyle();
  root.dataset.uiStyle = initialStyle;
  storeStyleCookie(initialStyle);
  updateColorScheme(initialStyle);
  window.ChubTheme = { applyStyle, currentStyle: () => root.dataset.uiStyle };
  document.addEventListener("DOMContentLoaded", () => applyStyle(initialStyle));
})();
