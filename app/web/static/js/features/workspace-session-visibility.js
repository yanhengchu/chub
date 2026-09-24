"use strict";

(() => {
  window.initializeSessionVisibilitySettings = () => {
    window.disposeSessionVisibilitySettings?.();

    const control = document.getElementById("show-internal-sessions");
    const feedback = document.getElementById("internal-session-visibility-feedback");
    if (!(control instanceof HTMLInputElement) || !(feedback instanceof HTMLElement)) return;
    let disposed = false;
    let busy = false;

    const setFeedback = (text = "") => {
      if (disposed) return;
      feedback.textContent = text;
      feedback.className = text ? "message message-error" : "message";
    };
    const request = async (options = {}) => {
      const response = await fetch("/api/settings/internal-session-visibility", {
        cache: "no-store",
        ...options,
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok || payload?.success !== true) {
        throw new Error(payload?.error?.message || "内部会话显示设置暂时不可用。");
      }
      return payload.data;
    };
    const render = (data) => {
      if (disposed) return;
      control.checked = data?.show_internal_sessions === true;
      control.disabled = busy;
      setFeedback();
    };
    const refresh = async () => {
      try {
        render(await request());
      } catch (error) {
        if (disposed) return false;
        control.disabled = true;
        setFeedback(error instanceof Error ? error.message : "内部会话显示设置暂时不可用。");
      }
    };
    control.addEventListener("change", async () => {
      if (disposed || busy) return;
      const requested = control.checked;
      busy = true;
      control.disabled = true;
      setFeedback();
      try {
        render(await request({
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ show_internal_sessions: requested }),
        }));
        window.refreshWorkspaceSessions?.();
      } catch (error) {
        if (!disposed) {
          control.checked = !requested;
          setFeedback(error instanceof Error ? error.message : "内部会话显示设置保存失败，请稍后重试。");
        }
      } finally {
        busy = false;
        control.disabled = false;
      }
    });

    void refresh();
    window.disposeSessionVisibilitySettings = () => {
      disposed = true;
    };
  };

  window.initializeSessionVisibilitySettings();
})();
