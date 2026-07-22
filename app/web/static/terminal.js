const sessionId = document.body.dataset.sessionId;
const pageId = document.body.dataset.pageId;
const returnLink = document.querySelector("#return-codex");
const CODEX_RETURN_KEY = "hub.codexReturnToDashboard";
const CODEX_REFRESH_KEY = "hub.codexRefreshOnReturn";

function returnToDashboard(event) {
  if (sessionStorage.getItem(CODEX_RETURN_KEY) !== "1") {
    return;
  }
  event.preventDefault();
  sessionStorage.removeItem(CODEX_RETURN_KEY);
  sessionStorage.setItem(CODEX_REFRESH_KEY, "1");
  window.history.back();
}

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
returnLink.addEventListener("click", returnToDashboard);
