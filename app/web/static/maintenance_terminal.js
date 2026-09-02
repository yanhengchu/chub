const pageId = document.body.dataset.pageId;

async function checkMaintenanceTerminalOwnership() {
  if (!pageId || document.visibilityState === "hidden") {
    return;
  }
  try {
    const response = await fetch(
      `/maintenance-terminal/connection/${encodeURIComponent(pageId)}`,
      { cache: "no-store", credentials: "same-origin" },
    );
    if (response.status === 404) {
      window.location.replace("/settings#utility-settings");
      return;
    }
    if (!response.ok) {
      return;
    }
    const state = await response.json();
    if (state.state === "displaced" || state.state === "closed") {
      window.location.replace("/settings#utility-settings");
    }
  } catch (_error) {
    // Do not leave a working terminal after a temporary network error.
  }
}

window.setInterval(checkMaintenanceTerminalOwnership, 1000);
document.addEventListener("visibilitychange", checkMaintenanceTerminalOwnership);
