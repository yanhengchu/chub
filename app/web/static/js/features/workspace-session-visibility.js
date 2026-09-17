"use strict";

(() => {
  window.initializeSessionVisibilitySettings = () => {
    window.disposeSessionVisibilitySettings?.();

    const panel = document.getElementById("internal-session-visibility-settings");
    if (!(panel instanceof HTMLElement)) return;

    const bulkToggle = document.getElementById("internal-session-visibility-toggle");
    const bulkFeedback = document.getElementById("internal-session-visibility-feedback");
    const configs = [
      { control: document.getElementById("deliveryline-show-collaboration-sessions"), path: "/api/deliveryline/settings", field: "show_sessions", feedback: "deliveryline" },
      { control: document.getElementById("workspace-task-show-internal-native-session"), path: "/api/settings/weixin-translation", field: "show_internal_native_session", feedback: "translation" },
      { control: document.getElementById("today-focus-show-sessions"), path: "/api/today-focus/settings", field: "show_sessions", feedback: "today-focus" },
      { control: document.getElementById("deployment-package-show-release-note-session"), path: "/api/settings/deployment-package/release-note-session", field: "show_sessions", feedback: "deployment-package" },
    ].filter((config) => config.control instanceof HTMLInputElement);
    let disposed = false;
    let busy = false;

    const feedbackFor = (name) => panel.querySelector(
      `[data-session-visibility-feedback="${name}"]`,
    );
    const setFeedback = (feedback, text = "") => {
      if (disposed || !(feedback instanceof HTMLElement)) return;
      feedback.textContent = text;
    };
    const request = async (path, options = {}) => {
      const response = await fetch(path, { cache: "no-store", ...options });
      const payload = await response.json().catch(() => null);
      if (!response.ok || payload?.success !== true) {
        throw new Error(payload?.error?.message || "内部会话显示设置暂时不可用。");
      }
      return payload.data;
    };
    const allShown = () => configs.length > 0 && configs.every((config) => config.saved === true);
    const updateBulkToggle = () => {
      if (disposed || !(bulkToggle instanceof HTMLButtonElement)) return;
      const loaded = configs.length > 0 && configs.every((config) => config.loaded === true);
      bulkToggle.textContent = allShown() ? "隐藏" : "展示";
      bulkToggle.disabled = busy || !loaded;
    };
    const setControlsDisabled = (disabled) => {
      configs.forEach((config) => {
        config.control.disabled = disabled || config.loaded !== true;
      });
    };
    const render = (config, data) => {
      if (disposed) return;
      config.saved = data?.[config.field] === true;
      config.loaded = true;
      config.control.checked = config.saved;
      config.control.disabled = busy;
      setFeedback(feedbackFor(config.feedback));
      updateBulkToggle();
    };
    const refresh = async (config, showError = true) => {
      try {
        render(config, await request(config.path));
        return true;
      } catch (error) {
        if (disposed) return false;
        config.loaded = false;
        config.control.disabled = true;
        if (showError) {
          setFeedback(feedbackFor(config.feedback), error instanceof Error ? error.message : "内部会话显示设置暂时不可用。");
        }
        updateBulkToggle();
        return false;
      }
    };
    const refreshAll = async () => Promise.all(configs.map((config) => refresh(config)));

    if (configs.length === 0) return;
    configs.forEach((config) => {
      config.saved = false;
      config.loaded = false;
      config.control.addEventListener("change", async () => {
        if (disposed || busy) return;
        busy = true;
        setControlsDisabled(true);
        updateBulkToggle();
        setFeedback(feedbackFor(config.feedback));
        try {
          render(config, await request(config.path, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [config.field]: config.control.checked }),
          }));
        } catch (error) {
          if (!disposed) {
            config.control.checked = config.saved;
            setFeedback(feedbackFor(config.feedback), error instanceof Error ? error.message : "内部会话显示设置保存失败，请稍后刷新页面重试。");
          }
        } finally {
          busy = false;
          setControlsDisabled(false);
          updateBulkToggle();
        }
      });
    });

    bulkToggle?.addEventListener("click", async () => {
      if (disposed || busy || !configs.every((config) => config.loaded === true)) return;
      busy = true;
      setControlsDisabled(true);
      updateBulkToggle();
      setFeedback(bulkFeedback);
      try {
        await request("/api/settings/internal-session-visibility", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ show_sessions: !allShown() }),
        });
      } catch (error) {
        if (!disposed) {
          setFeedback(bulkFeedback, error instanceof Error ? error.message : "部分内部会话显示设置未完成。");
        }
      } finally {
        await refreshAll();
        busy = false;
        setControlsDisabled(false);
        updateBulkToggle();
      }
    });
    void refreshAll();
    window.disposeSessionVisibilitySettings = () => {
      disposed = true;
    };
  };

  window.initializeSessionVisibilitySettings();
})();
