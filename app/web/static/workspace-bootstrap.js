(() => {
  const widthStorageKey = "chub.workspace.sidebarWidth";
  const collapsedStorageKey = "chub.workspace.sidebarCollapsed";
  const minimumSidebarWidth = 225;
  const maximumSidebarWidth = 360;

  try {
    const savedWidth = Number.parseFloat(window.localStorage.getItem(widthStorageKey));
    if (Number.isFinite(savedWidth)) {
      const width = Math.min(maximumSidebarWidth, Math.max(minimumSidebarWidth, savedWidth));
      document.documentElement.style.setProperty("--workspace-sidebar-preload-width", `${width}px`);
    }
    document.documentElement.dataset.workspaceSidebarCollapsed = String(
      window.localStorage.getItem(collapsedStorageKey) === "true",
    );
  } catch {
    // Stored layout preferences are optional; CSS defaults remain usable.
  }
})();
