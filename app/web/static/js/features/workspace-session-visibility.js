"use strict";

(() => {
  window.initializeSessionVisibilitySettings = () => {
    window.disposeSessionVisibilitySettings?.();

    const panel = document.getElementById("internal-session-visibility-settings");
    if (!(panel instanceof HTMLElement)) return;

    const deliverylineToggle = document.getElementById(
      "deliveryline-show-collaboration-sessions",
    );
    const translationToggle = document.getElementById(
      "workspace-task-show-internal-native-session",
    );
    const searchToggle = document.getElementById("search-show-sessions");
    const controls = [deliverylineToggle, translationToggle, searchToggle].filter(
      (control) => control instanceof HTMLInputElement,
    );
    let disposed = false;

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
    const bind = ({ control, path, field, feedback, failureMessage }) => {
      if (!(control instanceof HTMLInputElement)) return;
      let saved = false;
      const render = (data) => {
        if (disposed) return;
        saved = data?.[field] === true;
        control.checked = saved;
        control.disabled = false;
        setFeedback(feedback);
      };
      control.addEventListener("change", async () => {
        if (disposed) return;
        control.disabled = true;
        setFeedback(feedback);
        try {
          render(await request(path, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [field]: control.checked }),
          }));
        } catch (error) {
          if (disposed) return;
          control.checked = saved;
          control.disabled = false;
          setFeedback(feedback, error instanceof Error ? error.message : failureMessage);
        }
      });
      request(path).then(render).catch((error) => {
        setFeedback(feedback, error instanceof Error ? error.message : failureMessage);
      });
    };

    if (controls.length === 0) return;
    bind({
      control: deliverylineToggle,
      path: "/api/deliveryline/settings",
      field: "show_sessions",
      feedback: feedbackFor("deliveryline"),
      failureMessage: "Deliveryline 协作 Session 显示设置保存失败，请稍后刷新页面重试。",
    });
    bind({
      control: translationToggle,
      path: "/api/settings/weixin-translation",
      field: "show_internal_native_session",
      feedback: feedbackFor("translation"),
      failureMessage: "内部翻译 Session 显示设置保存失败，请稍后刷新页面重试。",
    });
    bind({
      control: searchToggle,
      path: "/api/search/settings",
      field: "show_sessions",
      feedback: feedbackFor("search"),
      failureMessage: "搜索 Session 显示设置保存失败，请稍后刷新页面重试。",
    });
    window.disposeSessionVisibilitySettings = () => {
      disposed = true;
    };
  };

  window.initializeSessionVisibilitySettings();
})();
