"use strict";

function renderStatus(data) {
  elements.connectedSummary.textContent =
    `CPU ${data.system.cpu_percent.toFixed(1)}% · ` +
    `内存 ${data.system.memory_percent.toFixed(1)}% · ` +
    `磁盘 ${data.system.disk_percent.toFixed(1)}%`;
  setWorkstationStatus(
    elements.chubServiceDetail,
    `v${data.hub.version} · 当前实例可响应`,
  );
  setMessage(elements.chubServiceMessage, "");
  syncCoreMaintenanceControls();
}

async function loadStatus() {
  const requestVersion = accessVersion;
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
      setWorkstationStatus(
        elements.chubServiceDetail,
        "无法读取最新控制面状态，保留上次结果。",
        "failed",
      );
      setMessage(
        elements.chubServiceMessage,
        "Chub 状态刷新失败，当前展示上次检测结果。",
        "error",
      );
    }
  }
}
