"use strict";

let activeLogSource = "operations";

async function loadLogs() {
  const requestVersion = accessVersion;
  elements.loadLogs.disabled = true;
  setMessage(elements.logsMessage, "正在读取日志…");
  try {
    const lines = elements.logLines.value;
    const data = await apiFetch(`/api/logs/page?source=${activeLogSource}&lines=${lines}`);
    if (requestVersion !== accessVersion) {
      return;
    }
    elements.logsOutput.textContent = data.lines.length
      ? data.lines.join("\n")
      : "当前日志为空。";
    elements.logsOutput.hidden = false;
    setMessage(elements.logsMessage, `已读取 ${data.count} 行日志。`, "success");
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return;
    }
    if (!handleAccessError(error)) {
      elements.logsOutput.hidden = true;
      setMessage(elements.logsMessage, error.message || "日志读取失败。", "error");
    }
  } finally {
    elements.loadLogs.disabled = false;
  }
}

elements.logTabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    activeLogSource = tab.dataset.logSource;
    elements.logTabs.forEach((item) => {
      const selected = item === tab;
      item.classList.toggle("is-active", selected);
      item.setAttribute("aria-selected", String(selected));
    });
    loadLogs();
  });
});

elements.tokenForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const token = elements.tokenInput.value.trim();
  if (!token) {
    setMessage(elements.globalMessage, "请输入 Hub Token。", "error");
    elements.tokenInput.focus();
    return;
  }
  elements.tokenInput.value = "";
  connectWithToken(token, elements.rememberToken.checked);
});

elements.refreshStatus.addEventListener("click", loadStatus);
elements.refreshAutomations.addEventListener("click", () => loadAutomations());
elements.refreshProjectDocs.addEventListener("click", loadProjectDocuments);
elements.loadLogs.addEventListener("click", loadLogs);
elements.automationBrowserControl.addEventListener("click", controlAutomationBrowser);
elements.automationBrowserProfile.addEventListener("change", () => {
  if (automationBrowserState !== "stopped") {
    return;
  }
  const selected = automationBrowserProfiles.find(
    (profile) => profile.id === elements.automationBrowserProfile.value,
  );
  elements.automationBrowserControl.textContent = selected && !selected.initialized
    ? "初始化并启动"
    : "启动";
  elements.automationBrowserControl.disabled = (
    !selected || (!selected.initialized && !selected.source_available)
  );
});
elements.automationFeishuCheck.addEventListener("click", checkFeishuEnvironment);
