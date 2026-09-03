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
  let navigationRequestId = 0;
  let navigationController = null;
  const applicationReturnLinks = document.querySelectorAll(
    "#settings-return-application, .settings-mobile-nav-external",
  );
  const isModifiedPrimaryClick = (event) => (
    event.defaultPrevented
    || (typeof event.button === "number" && event.button !== 0)
    || event.metaKey
    || event.ctrlKey
    || event.shiftKey
    || event.altKey
  );

  const updateActiveSettingsNavigation = (targetUrl) => {
    const targetPath = new URL(targetUrl, window.location.href).pathname;
    document.querySelectorAll(".settings-navigation-link, .settings-mobile-nav-link").forEach((item) => {
      const itemUrl = item instanceof HTMLAnchorElement
        ? item.href
        : item instanceof HTMLButtonElement
          ? new URL(item.dataset.settingsUrl || "", window.location.href).href
          : "";
      const selected = itemUrl && new URL(itemUrl, window.location.href).pathname === targetPath;
      if (selected) item.setAttribute("aria-current", "page");
      else item.removeAttribute("aria-current");
    });
  };

  const hasWorkspaceReturnHistory = (link) => {
    const returnTarget = new URL(link.href);
    if (!new Set(["/", "/workspace"]).has(returnTarget.pathname)) return false;
    if (!document.referrer) return false;
    const referrer = new URL(document.referrer);
    return (
      referrer.origin === window.location.origin
      && referrer.pathname === returnTarget.pathname
      && referrer.search === returnTarget.search
    );
  };

  applicationReturnLinks.forEach((link) => {
    link.addEventListener("click", (event) => {
      if (isModifiedPrimaryClick(event) || !hasWorkspaceReturnHistory(link)) return;
      event.preventDefault();
      window.history.back();
    });
  });

  // Settings tabs use the same in-place replacement model as workspace
  // sections. The address remains shareable, but all tab switches keep the
  // original application page as the single Back target.
  const replaceSettingsPage = async (event) => {
    if (isModifiedPrimaryClick(event)) return;
    const target = event.target;
    const navigation = target instanceof Element
      ? target.closest("a.settings-navigation-link")
        || target.closest("button.settings-mobile-nav-link")
      : null;
    if (!(navigation instanceof HTMLAnchorElement) && !(navigation instanceof HTMLButtonElement)) return;
    const targetUrl = navigation instanceof HTMLAnchorElement
      ? new URL(navigation.href)
      : new URL(navigation.dataset.settingsUrl || "", window.location.href);
    if (!targetUrl.pathname.startsWith("/settings/")) return;
    const returnUrl = new URL(window.location.href).searchParams.get("return_to");
    if (returnUrl && !targetUrl.searchParams.has("return_to")) {
      targetUrl.searchParams.set("return_to", returnUrl);
    }
    event.preventDefault();
    if (navigation.getAttribute("aria-current") === "page") return;
    const currentMain = document.getElementById("settings-workspace-main");
    const currentDialogs = document.getElementById("settings-page-dialogs");
    if (!(currentMain instanceof HTMLElement) || !(currentDialogs instanceof HTMLElement)) {
      window.location.replace(targetUrl.href);
      return;
    }
    navigationRequestId += 1;
    const requestId = navigationRequestId;
    navigationController?.abort();
    navigationController = new AbortController();
    navigation.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(targetUrl, {
        headers: { Accept: "text/html" },
        signal: navigationController.signal,
      });
      if (!response.ok) throw new Error("无法加载设置页面。");
      const nextDocument = new DOMParser().parseFromString(await response.text(), "text/html");
      const nextMain = nextDocument.getElementById("settings-workspace-main");
      const nextDialogs = nextDocument.getElementById("settings-page-dialogs");
      if (!(nextMain instanceof HTMLElement) || !(nextDialogs instanceof HTMLElement)) {
        throw new Error("设置页面响应无效。");
      }
      if (requestId !== navigationRequestId) return;
      window.disposeSettingsPage?.();
      currentDialogs.replaceWith(nextDialogs);
      currentMain.replaceWith(nextMain);
      document.body.dataset.settingsPage = nextDocument.body.dataset.settingsPage || "";
      document.title = nextDocument.title;
      history.replaceState(history.state, "", targetUrl.href);
      updateActiveSettingsNavigation(targetUrl.href);
      window.initializeSettingsPage?.();
    } catch (error) {
      if (error.name === "AbortError" || requestId !== navigationRequestId) return;
      window.location.replace(targetUrl.href);
    } finally {
      navigation.removeAttribute("aria-busy");
    }
  };
  document.addEventListener("click", replaceSettingsPage);

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
