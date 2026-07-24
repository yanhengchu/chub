const sessionId = document.body.dataset.sessionId;
const pageId = document.body.dataset.pageId;
const CODEX_REFRESH_KEY = "hub.codexRefreshOnReturn";
sessionStorage.setItem(CODEX_REFRESH_KEY, "1");

async function checkTerminalOwnership() {
  if (!sessionId || !pageId || document.visibilityState === "hidden") {
    return;
  }
  try {
    const response = await fetch(
      `/codex/${encodeURIComponent(sessionId)}/connection/${encodeURIComponent(pageId)}`,
      { cache: "no-store", credentials: "same-origin" },
    );
    if (response.status === 404) {
      window.location.replace("/?view=codex");
      return;
    }
    if (!response.ok) {
      return;
    }
    const state = await response.json();
    if (state.state === "displaced" || state.state === "closed") {
      window.location.replace("/?view=codex");
    }
  } catch (_error) {
    // A temporary network failure must not navigate away from a working terminal.
  }
}

window.setInterval(checkTerminalOwnership, 1000);
document.addEventListener("visibilitychange", checkTerminalOwnership);
