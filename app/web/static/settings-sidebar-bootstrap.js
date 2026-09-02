(() => {
  const widthStorageKey = "chub.sidebarWidth";
  const minimumSidebarWidth = 225;
  const maximumSidebarWidth = 360;

  try {
    const savedWidth = Number.parseFloat(window.localStorage.getItem(widthStorageKey));
    if (Number.isFinite(savedWidth)) {
      const width = Math.min(maximumSidebarWidth, Math.max(minimumSidebarWidth, savedWidth));
      document.documentElement.style.setProperty("--settings-sidebar-preload-width", `${width}px`);
    }
  } catch {
    // Stored layout preferences are optional; CSS defaults remain usable.
  }
})();
