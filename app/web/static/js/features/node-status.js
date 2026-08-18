"use strict";

function renderStatus(data) {
  elements.connectedSummary.textContent =
    `CPU ${data.system.cpu_percent.toFixed(1)}% · ` +
    `内存 ${data.system.memory_percent.toFixed(1)}% · ` +
    `磁盘 ${data.system.disk_percent.toFixed(1)}%`;
  setBadge(elements.chubServiceBadge, "运行正常", "success");
  elements.chubServiceDetail.textContent = `v${data.hub.version} · 当前实例`;
  setMessage(elements.chubServiceMessage, "");
  syncCoreMaintenanceControls();
}

async function loadStatus() {
  const requestVersion = accessVersion;
  elements.refreshStatus.disabled = true;
  try {
    const data = await apiFetch("/api/status");
    if (requestVersion !== accessVersion) {
      return;
    }
    renderStatus(data);
    showConnectedView(data);
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return;
    }
    if (!handleAccessError(error)) {
      setBadge(elements.connectionBadge, "刷新失败", "failed");
      setBadge(elements.chubServiceBadge, "刷新失败", "failed");
      setMessage(
        elements.chubServiceMessage,
        "Chub Web 状态刷新失败，当前展示上次检测结果。",
        "error",
      );
    }
  } finally {
    elements.refreshStatus.disabled = false;
  }
}
