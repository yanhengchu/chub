(() => {
  const shell = document.querySelector(".workspace-preview-shell");
  const toggle = document.getElementById("workspace-sidebar-toggle");
  const resizer = document.getElementById("workspace-sidebar-resizer");
  if (
    !(shell instanceof HTMLElement)
    || !(toggle instanceof HTMLButtonElement)
    || !(resizer instanceof HTMLElement)
  ) {
    return;
  }

  const collapsedStorageKey = "chub.workspace.sidebarCollapsed";
  const widthStorageKey = "chub.workspace.sidebarWidth";
  const minimumSidebarWidth = 225;
  const maximumSidebarWidth = 360;
  const defaultSidebarWidth = 225;
  const sidebarWidthStep = 16;
  const compactViewport = window.matchMedia("(max-width: 760px)");

  const clampSidebarWidth = (value) => Math.min(
    maximumSidebarWidth,
    Math.max(minimumSidebarWidth, value),
  );

  const currentSidebarWidth = () => {
    const value = Number.parseFloat(
      getComputedStyle(shell).getPropertyValue("--workspace-sidebar-width"),
    );
    return Number.isFinite(value) ? value : defaultSidebarWidth;
  };

  const setSidebarWidth = (value, { persist = true } = {}) => {
    const width = clampSidebarWidth(value);
    shell.style.setProperty("--workspace-sidebar-width", `${width}px`);
    resizer.setAttribute("aria-valuenow", String(width));
    if (!persist) {
      return;
    }
    try {
      window.localStorage.setItem(widthStorageKey, String(width));
    } catch {
      // Local preference is optional; the current page remains usable.
    }
  };

  const setCollapsed = (collapsed, { persist = true } = {}) => {
    shell.classList.toggle("is-sidebar-collapsed", collapsed);
    document.documentElement.dataset.workspaceSidebarCollapsed = String(collapsed);
    toggle.setAttribute("aria-pressed", String(collapsed));
    resizer.tabIndex = collapsed ? -1 : 0;
    resizer.setAttribute("aria-hidden", String(collapsed));
    const sidebarLabel = collapsed
      ? "展开侧边栏（⌘B / Ctrl+B）"
      : "折叠侧边栏（⌘B / Ctrl+B）";
    toggle.setAttribute("aria-label", sidebarLabel);
    toggle.title = sidebarLabel;
    if (!persist) {
      return;
    }
    try {
      window.localStorage.setItem(collapsedStorageKey, String(collapsed));
    } catch {
      // Local preference is optional; the current page remains usable.
    }
  };

  try {
    setSidebarWidth(
      Number.parseFloat(window.localStorage.getItem(widthStorageKey)),
      { persist: false },
    );
    setCollapsed(
      window.localStorage.getItem(collapsedStorageKey) === "true",
      { persist: false },
    );
  } catch {
    setSidebarWidth(defaultSidebarWidth, { persist: false });
    setCollapsed(false, { persist: false });
  }

  requestAnimationFrame(() => shell.classList.add("is-layout-ready"));

  let sidebarTransitionTimer = null;
  const clearSidebarTransition = () => {
    if (sidebarTransitionTimer !== null) {
      window.clearTimeout(sidebarTransitionTimer);
      sidebarTransitionTimer = null;
    }
    shell.classList.remove("is-sidebar-opening", "is-sidebar-closing");
  };

  const expandSidebar = () => {
    clearSidebarTransition();
    if (!shell.classList.contains("is-sidebar-collapsed")) {
      return;
    }
    shell.classList.add("is-sidebar-opening");
    setCollapsed(false);
    sidebarTransitionTimer = window.setTimeout(() => {
      shell.classList.remove("is-sidebar-opening");
      sidebarTransitionTimer = null;
    }, 180);
  };

  const collapseSidebar = () => {
    clearSidebarTransition();
    if (shell.classList.contains("is-sidebar-collapsed")) {
      return;
    }
    shell.classList.add("is-sidebar-closing");
    sidebarTransitionTimer = window.setTimeout(() => {
      setCollapsed(true);
      shell.classList.remove("is-sidebar-closing");
      sidebarTransitionTimer = null;
    }, 140);
  };

  const toggleSidebar = () => {
    if (compactViewport.matches) {
      return;
    }
    if (shell.classList.contains("is-sidebar-collapsed")) {
      expandSidebar();
    } else {
      collapseSidebar();
    }
  };

  toggle.addEventListener("click", toggleSidebar);
  let resizeState = null;
  const finishResize = (event) => {
    if (resizeState?.pointerId !== event.pointerId) {
      return;
    }
    resizer.releasePointerCapture(event.pointerId);
    resizeState = null;
    shell.classList.remove("is-sidebar-resizing");
  };

  resizer.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || compactViewport.matches || shell.classList.contains("is-sidebar-collapsed")) {
      return;
    }
    resizeState = { pointerId: event.pointerId, startX: event.clientX, startWidth: currentSidebarWidth() };
    resizer.setPointerCapture(event.pointerId);
    shell.classList.add("is-sidebar-resizing");
    event.preventDefault();
  });
  resizer.addEventListener("pointermove", (event) => {
    if (resizeState?.pointerId !== event.pointerId) {
      return;
    }
    setSidebarWidth(resizeState.startWidth + event.clientX - resizeState.startX);
  });
  resizer.addEventListener("pointerup", finishResize);
  resizer.addEventListener("pointercancel", finishResize);
  resizer.addEventListener("keydown", (event) => {
    if (compactViewport.matches || shell.classList.contains("is-sidebar-collapsed")) {
      return;
    }
    const keyWidth = {
      ArrowLeft: currentSidebarWidth() - sidebarWidthStep,
      ArrowRight: currentSidebarWidth() + sidebarWidthStep,
      Home: minimumSidebarWidth,
      End: maximumSidebarWidth,
    }[event.key];
    if (keyWidth === undefined) {
      return;
    }
    event.preventDefault();
    setSidebarWidth(keyWidth);
  });
  document.addEventListener("keydown", (event) => {
    if (
      event.defaultPrevented
      || event.repeat
      || event.altKey
      || !(event.metaKey || event.ctrlKey)
      || event.key.toLowerCase() !== "b"
      || event.target instanceof HTMLInputElement
      || event.target instanceof HTMLTextAreaElement
      || event.target instanceof HTMLSelectElement
      || (event.target instanceof HTMLElement && event.target.isContentEditable)
    ) {
      return;
    }
    event.preventDefault();
    toggleSidebar();
  });
})();
