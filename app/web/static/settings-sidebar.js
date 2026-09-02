(() => {
  const shell = document.querySelector(".settings-workspace-shell");
  const resizer = document.getElementById("settings-sidebar-resizer");
  if (!(shell instanceof HTMLElement) || !(resizer instanceof HTMLElement)) {
    return;
  }

  const widthStorageKey = "chub.sidebarWidth";
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
      getComputedStyle(shell).getPropertyValue("--settings-sidebar-width"),
    );
    return Number.isFinite(value) ? value : defaultSidebarWidth;
  };

  const setSidebarWidth = (value, { persist = true } = {}) => {
    const width = Number.isFinite(value)
      ? clampSidebarWidth(value)
      : defaultSidebarWidth;
    shell.style.setProperty("--settings-sidebar-width", `${width}px`);
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

  try {
    setSidebarWidth(
      Number.parseFloat(window.localStorage.getItem(widthStorageKey)),
      { persist: false },
    );
  } catch {
    setSidebarWidth(defaultSidebarWidth, { persist: false });
  }

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
    if (event.button !== 0 || compactViewport.matches) {
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
    if (compactViewport.matches) {
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

})();
