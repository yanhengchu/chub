"use strict";

(() => {
  const toggle = document.getElementById("deliveryline-show-collaboration-sessions");
  const message = document.getElementById("deliveryline-settings-message");
  let savedShowSessions = false;
  if (!(toggle instanceof HTMLInputElement)) return;
  const request = async (path, options = {}) => {
    const response = await fetch(path, { cache: "no-store", ...options });
    const payload = await response.json();
    if (!response.ok || payload.success !== true) throw new Error(payload?.error?.message || "Deliveryline 设置暂时不可用。");
    return payload.data;
  };
  const render = (data) => {
    savedShowSessions = data.show_sessions === true;
    toggle.checked = savedShowSessions;
    toggle.disabled = false;
  };
  toggle.addEventListener("change", async () => {
    toggle.disabled = true;
    if (message) { message.hidden = true; message.textContent = ""; }
    try {
      render(await request("/api/deliveryline/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ show_sessions: toggle.checked }),
      }));
    } catch (error) {
      if (message) { message.textContent = error.message; message.hidden = false; message.className = "message message-error"; }
      toggle.checked = savedShowSessions;
      toggle.disabled = false;
    }
  });
  request("/api/deliveryline/settings").then(render).catch((error) => {
    if (message) { message.textContent = error.message; message.hidden = false; message.className = "message message-error"; }
  });
})();
