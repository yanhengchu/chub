"use strict";

window.initializeWorkspaceWorkstation = () => {
  const byId = (id) => document.getElementById(id);
  const elements = {
    health: byId("workspace-preview-health"),
    refresh: byId("workspace-workstation-refresh"),
    chubSummary: byId("workspace-chub-summary"),
    chubSummaryDetail: byId("workspace-chub-summary-detail"),
    workerSummary: byId("workspace-worker-summary"),
    workerSummaryDetail: byId("workspace-worker-summary-detail"),
    runtimeSummary: byId("workspace-runtime-summary"),
    runtimeSummaryDetail: byId("workspace-runtime-summary-detail"),
    systemSummary: byId("workspace-system-summary"),
    systemSummaryDetail: byId("workspace-system-summary-detail"),
    chubDetail: byId("workspace-chub-detail"),
    chubMessage: byId("workspace-chub-message"),
    chubRestart: byId("workspace-chub-restart"),
    workerDetail: byId("workspace-worker-detail"),
    workerMessage: byId("workspace-worker-message"),
    workerRestart: byId("workspace-worker-restart"),
    runtimeDetail: byId("workspace-runtime-detail"),
    upgradeDetail: byId("workspace-upgrade-detail"),
    upgradeStart: byId("workspace-upgrade-start"),
    deviceName: byId("workspace-device-name"),
    deviceDetail: byId("workspace-device-detail"),
  };
  if (!Object.values(elements).every((element) => element instanceof HTMLElement)) return;

  let workerState = null;
  let upgradeState = null;
  let hubRestarting = false;
  let workerRestarting = false;
  let upgradeStarting = false;
  let workerTimer = 0;
  let upgradeTimer = 0;
  let statusIsCurrent = false;
  let workerIsCurrent = false;
  let upgradeIsCurrent = false;
  let snapshot = { status: null, worker: null, upgrade: null };
  const snapshotCacheKey = "chub.workspace.workstation.v1";

  const request = async (path, options = {}) => {
    let response;
    try {
      response = await fetch(path, options);
    } catch {
      throw new Error("无法连接 Chub，请检查服务和网络。");
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok || payload?.success !== true) {
      throw new Error(payload?.error?.message || `请求失败（HTTP ${response.status}）。`);
    }
    return payload.data;
  };

  const isSnapshotValue = (value) => value && typeof value === "object";

  const readSnapshot = () => {
    try {
      const cached = JSON.parse(window.sessionStorage.getItem(snapshotCacheKey) || "null");
      if (!isSnapshotValue(cached)) return null;
      return {
        status: isSnapshotValue(cached.status) ? cached.status : null,
        worker: isSnapshotValue(cached.worker) ? cached.worker : null,
        upgrade: isSnapshotValue(cached.upgrade) ? cached.upgrade : null,
      };
    } catch {
      return null;
    }
  };

  const cacheSnapshot = () => {
    try {
      window.sessionStorage.setItem(snapshotCacheKey, JSON.stringify(snapshot));
    } catch {
      // The latest server data remains usable when browser storage is unavailable.
    }
  };

  const setStatus = (target, text, kind = "muted") => {
    target.textContent = text;
    target.className = `workstation-status-detail workstation-status-detail-${kind}`;
  };

  const setMessage = (target, text = "", kind = "") => {
    target.textContent = text;
    target.className = kind ? `message message-${kind}` : "message";
  };

  const toolbarStatus = (data) => {
    const platform = data.node?.detected_platform || "unknown";
    return `${data.node?.name || "Chub"} · ${platform === "macos" ? "macOS" : platform} · Chub 可用`;
  };

  const setToolbarStatus = (text) => {
    elements.health.lastChild.textContent = text;
  };

  const bytes = (value) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric) || numeric < 0) return "未知";
    if (numeric < 1024 ** 3) return `${Math.round(numeric / 1024 ** 2)} MB`;
    return `${(numeric / 1024 ** 3).toFixed(1)} GB`;
  };

  const uptime = (seconds) => {
    const value = Math.max(0, Number(seconds) || 0);
    const days = Math.floor(value / 86400);
    const hours = Math.floor((value % 86400) / 3600);
    return days ? `${days} 天 ${hours} 小时` : `${hours} 小时`;
  };

  const workerLabel = (state) => ({
    ready: "可用",
    busy: "执行中",
    draining: "正在停止任务",
    recovering: "正在恢复",
    restarting: "正在重启",
    incompatible: "版本不兼容",
    unavailable: "不可用",
    stopped: "已停止",
  })[state] || "状态未知";

  const workerKind = (state) => (["unavailable", "incompatible"].includes(state)
    ? "failed"
    : ["busy", "draining", "recovering", "restarting"].includes(state)
      ? "warning"
      : "success");

  const runtimeKind = (data) => {
    if (data.runtime_state === "unavailable" || data.runtime_state === "unknown") return "failed";
    if (data.runtime_state === "disabled") return "warning";
    return "success";
  };

  const runtimeDetail = (data) => {
    const labels = { available: "可用", disabled: "已停用", unavailable: "不可用" };
    if (Array.isArray(data.runtimes) && data.runtimes.length) {
      return data.runtimes.map((runtime) => `${runtime.name}：${labels[runtime.state] || "状态未知"}`).join(" · ");
    }
    return data.runtime_message || "未配置 AI Runtime。";
  };

  const upgradeLabel = (data) => ({
    idle: "无待升级",
    available: "可执行",
    blocked: "暂不可用",
    preparing: "正在准备",
    draining: "正在停止任务",
    archiving: "正在清理",
    cleaning: "正在清理",
    restarting: "正在恢复",
    succeeded: "已完成",
    failed: "恢复失败",
  })[data.state] || "状态未知";

  const syncControls = () => {
    const upgradeRunning = Boolean(upgradeState?.operation?.status === "started");
    elements.chubRestart.disabled = hubRestarting || upgradeRunning;
    elements.workerRestart.disabled = workerRestarting || !workerIsCurrent || !upgradeIsCurrent || !workerState?.can_restart || upgradeRunning;
    elements.upgradeStart.disabled = upgradeStarting || !upgradeIsCurrent || !upgradeState?.can_start;
  };

  const renderStatus = (data) => {
    const system = data.system;
    elements.chubSummary.textContent = `v${data.hub.version}`;
    elements.chubSummaryDetail.textContent = `${data.node.name} · 已运行 ${uptime(system.uptime_seconds)}`;
    elements.systemSummary.textContent = `CPU ${Math.round(system.cpu_percent)}%`;
    elements.systemSummaryDetail.textContent = `内存 ${Math.round(system.memory_percent)}% · 磁盘 ${Math.round(system.disk_percent)}%`;
    setStatus(elements.chubDetail, `${system.operating_system} ${system.operating_system_version} · Python ${system.python_version}`, "success");
    elements.deviceName.textContent = `${data.node.name} · 已运行 ${uptime(system.uptime_seconds)}`;
    elements.deviceDetail.textContent = `CPU ${Math.round(system.cpu_percent)}% · 内存 ${bytes(system.memory_used_bytes)} / ${bytes(system.memory_total_bytes)} · 磁盘 ${bytes(system.disk_used_bytes)} / ${bytes(system.disk_total_bytes)}`;
  };

  const renderWorker = (data) => {
    workerState = data;
    const taskCount = Number(data.active_tasks || 0) + Number(data.queued_tasks || 0);
    elements.workerSummary.textContent = workerLabel(data.state);
    elements.workerSummaryDetail.textContent = taskCount ? `${taskCount} 个在途或排队任务` : data.message;
    setStatus(elements.workerDetail, data.message, workerKind(data.state));
    elements.runtimeSummary.textContent = data.runtime_state === "available" ? "可用" : runtimeDetail(data);
    elements.runtimeSummaryDetail.textContent = runtimeDetail(data);
    setStatus(elements.runtimeDetail, runtimeDetail(data), runtimeKind(data));
    setMessage(elements.workerMessage, data.operation?.status === "failed" ? data.operation.message : "", data.operation?.status === "failed" ? "error" : "");
    syncControls();
  };

  const renderUpgrade = (data) => {
    upgradeState = data;
    setStatus(elements.upgradeDetail, `状态：${upgradeLabel(data)}。${data.message}`, data.state === "failed" ? "failed" : data.can_start ? "success" : "warning");
    syncControls();
  };

  const scheduleWorkerRefresh = () => {
    window.clearTimeout(workerTimer);
    if (["busy", "draining", "recovering", "restarting"].includes(workerState?.state)) {
      workerTimer = window.setTimeout(loadWorker, 1000);
    }
  };

  const scheduleUpgradeRefresh = () => {
    window.clearTimeout(upgradeTimer);
    if (["preparing", "draining", "archiving", "cleaning", "restarting"].includes(upgradeState?.state)) {
      upgradeTimer = window.setTimeout(loadUpgrade, 1000);
    }
  };

  const loadStatus = async () => {
    try {
      const data = await request("/api/status");
      renderStatus(data);
      snapshot.status = data;
      statusIsCurrent = true;
      cacheSnapshot();
      setMessage(elements.chubMessage, "");
    } catch (error) {
      if (!snapshot.status) {
        setStatus(elements.chubDetail, "无法读取当前控制面状态。", "failed");
      }
      setMessage(elements.chubMessage, error.message || "Chub 状态读取失败。", "error");
      return false;
    }
    return true;
  };

  const loadWorker = async () => {
    try {
      const data = await request("/api/maintenance/quick-worker");
      renderWorker(data);
      snapshot.worker = data;
      workerIsCurrent = true;
      cacheSnapshot();
    } catch (error) {
      if (!snapshot.worker) {
        setStatus(elements.workerDetail, "无法读取当前任务执行服务状态。", "failed");
      }
      setMessage(elements.workerMessage, error.message || "Quick Worker 状态读取失败。", "error");
      return false;
    }
    scheduleWorkerRefresh();
    return true;
  };

  const loadUpgrade = async () => {
    try {
      const data = await request("/api/maintenance/system-upgrade");
      renderUpgrade(data);
      snapshot.upgrade = data;
      upgradeIsCurrent = true;
      cacheSnapshot();
    } catch (error) {
      if (!snapshot.upgrade) {
        setStatus(elements.upgradeDetail, error.message || "升级与恢复状态读取失败。", "failed");
      }
      return false;
    }
    scheduleUpgradeRefresh();
    return true;
  };

  const refresh = async () => {
    elements.refresh.disabled = true;
    statusIsCurrent = false;
    workerIsCurrent = false;
    upgradeIsCurrent = false;
    syncControls();
    setToolbarStatus("正在读取工作台状态…");
    const results = await Promise.all([loadStatus(), loadWorker(), loadUpgrade()]);
    if (results.every(Boolean) && snapshot.status) {
      setToolbarStatus(toolbarStatus(snapshot.status));
    } else {
      setToolbarStatus("工作台状态暂时无法更新");
    }
    elements.refresh.disabled = false;
  };

  const waitForRestart = async (previousInstanceId) => {
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      try {
        const data = await request("/api/health", { cache: "no-store" });
        if (data.instance_id !== previousInstanceId) return;
      } catch {
        // The old instance is expected to disappear before the new instance is ready.
      }
    }
    throw new Error("重启后未能确认新的 Chub 实例，请稍后刷新页面检查状态。");
  };

  const waitForWorkerRestart = async (operationId) => {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 500));
      let data;
      try {
        data = await request("/api/maintenance/quick-worker", { cache: "no-store" });
      } catch {
        // Worker handoff can briefly make its status unavailable before the
        // maintenance operation records its final state.
        continue;
      }
      renderWorker(data);
      snapshot.worker = data;
      workerIsCurrent = true;
      cacheSnapshot();
      const operation = data.operation;
      if (operation?.operation_id !== operationId) continue;
      if (operation.status === "succeeded") return;
      if (operation.status === "failed") {
        throw new Error(operation.message || "Quick Worker 重启失败，请查看日志详情。");
      }
    }
    throw new Error("Quick Worker 重启结果暂时无法确认，请稍后刷新页面检查状态。");
  };

  const restartChub = async () => {
    hubRestarting = true;
    syncControls();
    setStatus(elements.chubDetail, "正在请求 Chub 重启，并等待新实例确认。", "warning");
    try {
      const previous = await request("/api/health", { cache: "no-store" });
      await request("/api/maintenance/restart", { method: "POST" });
      await waitForRestart(previous.instance_id);
      setStatus(
        elements.chubDetail,
        "Chub 已重启并恢复。浏览器将在稍后自动刷新页面。",
        "success",
      );
      setToolbarStatus("Chub 已重启并恢复。");
      window.setTimeout(() => window.location.reload(), 2000);
    } finally {
      hubRestarting = false;
      syncControls();
    }
  };

  const restartWorker = async () => {
    workerRestarting = true;
    syncControls();
    try {
      const data = await request("/api/maintenance/quick-worker/restart", { method: "POST" });
      const operationId = data.operation?.operation_id;
      if (!operationId) {
        throw new Error("Quick Worker 重启未能创建可确认的维护操作。");
      }
      renderWorker(data);
      snapshot.worker = data;
      workerIsCurrent = true;
      cacheSnapshot();
      await waitForWorkerRestart(operationId);
      setStatus(
        elements.workerDetail,
        "Quick Worker 已重启并恢复。浏览器将在稍后自动刷新页面。",
        "success",
      );
      setToolbarStatus("Quick Worker 已重启并恢复。");
      window.setTimeout(() => window.location.reload(), 2000);
    } finally {
      workerRestarting = false;
      syncControls();
    }
  };

  const startUpgrade = async () => {
    const fingerprint = upgradeState?.plan?.fingerprint;
    if (!fingerprint) throw new Error("恢复方案尚未就绪，请先刷新状态。");
    upgradeStarting = true;
    syncControls();
    try {
      const data = await request("/api/maintenance/system-upgrade", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fingerprint }),
      });
      renderUpgrade(data);
      snapshot.upgrade = data;
      upgradeIsCurrent = true;
      cacheSnapshot();
      scheduleUpgradeRefresh();
    } finally {
      upgradeStarting = false;
      syncControls();
    }
  };

  const cachedSnapshot = readSnapshot();
  if (cachedSnapshot) {
    snapshot = cachedSnapshot;
    if (snapshot.status) renderStatus(snapshot.status);
    if (snapshot.worker) renderWorker(snapshot.worker);
    if (snapshot.upgrade) renderUpgrade(snapshot.upgrade);
  }
  syncControls();

  elements.refresh.addEventListener("click", () => { void refresh(); });
  elements.chubRestart.addEventListener("click", () => {
    void showConfirmationDialog({
      title: "重启 Chub",
      description: "只重启 Chub Web 控制面；已接受的快速任务、Quick Worker、实时终端和原生 Codex 不会被停止。",
      confirmLabel: "确认重启",
      pendingLabel: "正在等待新实例…",
      tone: "secondary",
      errorMessage: "Chub 重启失败。",
      onConfirm: restartChub,
    });
  });
  elements.workerRestart.addEventListener("click", () => {
    void showConfirmationDialog({
      title: "重启 Chub Quick Worker",
      description: "排队任务会取消，执行中的快速任务会停止并标记为未完成，且不会自动重试。Chub、OpenClaw 和实时终端不受影响。",
      confirmLabel: "确认重启",
      pendingLabel: "正在下发…",
      errorMessage: "Quick Worker 重启失败。",
      onConfirm: restartWorker,
    });
  });
  elements.upgradeStart.addEventListener("click", () => {
    void showConfirmationDialog({
      title: "升级与恢复",
      description: "此操作会重建 Chub AI 运行态、Chub Web 与 Quick Worker；本地关联 Session 会按固定边界清理，Codex 原生会话保留。",
      confirmLabel: upgradeState?.resume ? "继续恢复" : "确认升级与恢复",
      pendingLabel: "正在开始…",
      errorMessage: "升级与恢复未能启动。",
      onConfirm: startUpgrade,
    });
  });

  void refresh();
};

window.initializeWorkspaceWorkstation();
