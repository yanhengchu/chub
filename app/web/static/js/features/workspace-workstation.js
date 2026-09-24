"use strict";

window.initializeWorkspaceWorkstation = () => {
  window.disposeWorkspaceWorkstation?.();
  const byId = (id) => document.getElementById(id);
  const elements = {
    health: byId("workspace-preview-health"),
    runtimeSummary: byId("workspace-runtime-summary"),
    runtimeSummaryDetail: byId("workspace-runtime-summary-detail"),
    tailnetSummary: byId("workspace-tailnet-summary"),
    tailnetSummaryDetail: byId("workspace-tailnet-summary-detail"),
    systemSummary: byId("workspace-system-summary"),
    systemSummaryDetail: byId("workspace-system-summary-detail"),
    chubDetail: byId("workspace-chub-detail"),
    chubRestart: byId("workspace-chub-restart"),
    workerDetail: byId("workspace-worker-detail"),
    workerRestart: byId("workspace-worker-restart"),
    upgradeDetail: byId("workspace-upgrade-detail"),
    upgradeStart: byId("workspace-upgrade-start"),
    developmentEnvironment: byId("workspace-development-environment"),
    developmentRuntimeList: byId("workspace-development-runtime-list"),
    developmentBusinessList: byId("workspace-development-business-list"),
  };
  if (!Object.values(elements).every((element) => element instanceof HTMLElement)) return;

  let workerState = null;
  let upgradeState = null;
  let hubRestarting = false;
  let workerRestarting = false;
  let upgradeStarting = false;
  let developmentSnapshot = null;
  let workerTimer = 0;
  let workerRetryDelay = 1000;
  let upgradeTimer = 0;
  let pageReloadTimer = 0;
  const pendingWaits = new Map();
  let disposed = false;
  let workerIsCurrent = false;
  let upgradeIsCurrent = false;
  let snapshot = { status: null, worker: null, upgrade: null };
  const snapshotCacheKey = "chub.workspace.workstation.v1";
  const developmentSnapshotCacheKey = "chub.workspace.development.v1";
  const requestAbortController = new AbortController();

  const request = async (path, options = {}) => {
    const { timeoutMs, ...fetchOptions } = options;
    const timeoutController = timeoutMs ? new AbortController() : null;
    const abortForDispose = () => timeoutController?.abort();
    let timeout = 0;
    let response;
    if (timeoutController) {
      requestAbortController.signal.addEventListener("abort", abortForDispose, { once: true });
      timeout = window.setTimeout(() => timeoutController.abort(), timeoutMs);
    }
    try {
      response = await fetch(path, {
        ...fetchOptions,
        signal: timeoutController?.signal || requestAbortController.signal,
      });
    } catch (error) {
      if (requestAbortController.signal.aborted) throw error;
      if (error?.name === "AbortError") {
        throw new Error("请求超时，请稍后重试。");
      }
      throw new Error("无法连接 Chub，请检查服务和网络。");
    } finally {
      if (timeout) window.clearTimeout(timeout);
      if (timeoutController) {
        requestAbortController.signal.removeEventListener("abort", abortForDispose);
      }
    }
    const payload = await response.json().catch(() => null);
    if (!response.ok || payload?.success !== true) {
      throw new Error(payload?.error?.message || `请求失败（HTTP ${response.status}）。`);
    }
    return payload.data;
  };

  const waitFor = (delay) => new Promise((resolve) => {
    const timer = window.setTimeout(() => {
      pendingWaits.delete(timer);
      resolve();
    }, delay);
    pendingWaits.set(timer, resolve);
  });

  const cancelPendingWaits = () => {
    pendingWaits.forEach((resolve, timer) => {
      window.clearTimeout(timer);
      resolve();
    });
    pendingWaits.clear();
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

  const readDevelopmentSnapshot = () => {
    try {
      const cached = JSON.parse(
        window.sessionStorage.getItem(developmentSnapshotCacheKey) || "null",
      );
      if (
        !isSnapshotValue(cached?.runtime)
      ) return null;
      return cached;
    } catch {
      return null;
    }
  };

  const cacheDevelopmentSnapshot = (runtime, runtimeManagement, businessPlugins, runtimePlugin) => {
    try {
      window.sessionStorage.setItem(
        developmentSnapshotCacheKey,
        JSON.stringify({ runtime, runtimeManagement, businessPlugins, runtimePlugin }),
      );
    } catch {
      // The latest server data remains usable when browser storage is unavailable.
    }
  };

  const setStatus = (target, text, kind = "muted") => {
    target.textContent = text;
    target.className = `workstation-status-detail workstation-status-detail-${kind}`;
  };

  const setSummaryStatus = (target, text, kind = "muted") => {
    target.textContent = text;
    target.className = `workspace-preview-summary-status workspace-preview-summary-status-${kind}`;
  };

  const toolbarStatus = (data) => {
    const platform = data.node?.detected_platform || "unknown";
    return `${platform === "macos" ? "macOS" : platform} · Chub 可用`;
  };

  const setToolbarStatus = (text) => {
    elements.health.lastChild.textContent = text;
  };

  const showToolbarFeedback = (text, kind = "error") => {
    window.showWorkspaceToolbarFeedback?.(text, kind);
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

  const tailnetSummary = (state) => ({
    available: ["已就绪", "远程访问", "success"],
    unavailable: ["未启用", "仅本机访问", "muted"],
    unknown: ["未检查", "远程访问", "muted"],
  })[state] || ["状态未知", "远程访问", "muted"];

  const upgradeLabel = (data) => ({
    idle: "可执行",
    preparing: "正在准备",
    rebuilding: "正在重建",
    succeeded: "已完成",
    failed: "未完成",
    unknown: "状态未知",
  })[data.state] || "状态未知";

  const syncControls = () => {
    if (disposed) return;
    const upgradeRunning = Boolean(["requested", "started"].includes(upgradeState?.operation?.status));
    elements.chubRestart.disabled = hubRestarting || upgradeRunning;
    elements.workerRestart.disabled = workerRestarting || !workerState?.can_restart || upgradeRunning;
    elements.upgradeStart.disabled = upgradeStarting || !upgradeIsCurrent || !upgradeState?.can_start;
  };

  const renderStatus = (data) => {
    const system = data.system;
    elements.systemSummary.textContent = `CPU ${Math.round(system.cpu_percent)}%`;
    elements.systemSummaryDetail.textContent = `内存 ${Math.round(system.memory_percent)}% · 磁盘 ${Math.round(system.disk_percent)}%`;
    setStatus(
      elements.chubDetail,
      `Chub v${data.hub.version} · Web 控制面（页面与 API）· 重启不影响 Quick Worker 与已受理任务`,
      "success",
    );
    const [summary, summaryDetail, summaryKind] = tailnetSummary(data.tailnet.state);
    setSummaryStatus(elements.tailnetSummary, summary, summaryKind);
    elements.tailnetSummaryDetail.textContent = data.tailnet.endpoints?.length
      ? `监听 ${data.tailnet.endpoints.join(" · ")}`
      : summaryDetail;
  };

  const renderWorker = (data) => {
    workerState = data;
    const operationFailed = data.operation?.status === "failed";
    const workerVersionNumber = data.worker_version?.match(/^quick-worker-(\d+)(?:-|$)/)?.[1];
    const workerVersion = workerVersionNumber ? `v${workerVersionNumber}` : data.worker_version;
    const versionDetail = data.worker_version && data.protocol_version
      ? `Worker ${workerVersion} · 协议 v${data.protocol_version}${data.state === "incompatible" && data.expected_protocol_version ? `（当前 Chub 需要 v${data.expected_protocol_version}）` : ""} · `
      : "";
    const readyDetail = data.worker_version && data.state === "ready"
      ? `Worker ${workerVersion} · 后台 AI 任务执行服务 · 重启会中断在途任务`
      : `${versionDetail}${data.message}`;
    setStatus(
      elements.workerDetail,
      operationFailed || data.state === "restarting"
        ? (operationFailed ? data.operation.message : data.message)
        : readyDetail,
      operationFailed ? "failed" : workerKind(data.state),
    );
    setSummaryStatus(
      elements.runtimeSummary,
      data.runtime_state === "available" ? "可用" : runtimeDetail(data),
      runtimeKind(data),
    );
    elements.runtimeSummaryDetail.textContent = runtimeDetail(data);
    syncControls();
  };

  const renderUpgrade = (data) => {
    upgradeState = data;
    if (data.state === "succeeded") {
      setStatus(elements.upgradeDetail, "可按当前代码、配置和 requirements 重建 Chub 工作站。", "success");
    } else if (["preparing", "rebuilding"].includes(data.state)) {
      setStatus(
        elements.upgradeDetail,
        "状态：重建已启动。当前页面将短暂断开，无法实时显示进度；请稍后手动刷新本页查看最终结果。",
        "warning",
      );
    } else {
      setStatus(elements.upgradeDetail, `状态：${upgradeLabel(data)}。${data.message}`, data.state === "failed" ? "failed" : data.can_start ? "success" : "warning");
    }
    syncControls();
  };

  const formalVersion = (version) => {
    const normalized = typeof version === "string" ? version.trim().replace(/^v/i, "") : "";
    return normalized ? `v${normalized}` : "未知版本";
  };

  const renderDevelopment = (runtime, runtimeManagement, businessPlugins, runtimePlugin) => {
    const artifactTitle = (plugin, artifact, includeArtifactName = false) => {
      const name = plugin?.name || "未知插件";
      const artifactName = includeArtifactName ? ` · ${artifact?.name || "未知实现"}` : "";
      return artifact?.source === "development"
        ? `${name}${artifactName} · 开发实现`
        : artifact?.version
          ? `${name}${artifactName} · 正式版 ${formalVersion(artifact.version)}`
          : `${name} · 未知版本`;
    };
    const renderRuntimeArtifacts = (plugin) => {
      const imported = Array.isArray(plugin?.imported_artifact_ids) ? plugin.imported_artifact_ids : [];
      const enabled = Array.isArray(plugin?.enabled_artifact_ids) ? plugin.enabled_artifact_ids : [];
      const artifacts = Array.isArray(plugin?.artifacts) ? plugin.artifacts : [];
      const importedArtifacts = artifacts.filter((artifact) => imported.includes(artifact.artifact_id));
      elements.developmentRuntimeList.replaceChildren(...importedArtifacts.map((artifact) => {
        const row = document.createElement("div");
        row.className = "workstation-status-row";
        const copy = document.createElement("div");
        copy.className = "workstation-status-copy";
        const title = document.createElement("strong");
        title.textContent = artifactTitle(plugin, artifact, true);
        const detail = document.createElement("span");
        const isEnabled = enabled.includes(artifact.artifact_id);
        setStatus(
          detail,
          `导入状态：已导入 · 启用状态：${isEnabled ? "已启用" : "未启用"}。`,
          isEnabled ? "success" : "warning",
        );
        copy.append(title, detail);
        row.append(copy);
        return row;
      }));
      return importedArtifacts.length > 0;
    };
    const runtimeInstalled = renderRuntimeArtifacts(runtimePlugin);
    const importedBusinessPlugins = (Array.isArray(businessPlugins) ? businessPlugins : [])
      .filter((plugin) => Array.isArray(plugin.imported_artifact_ids) && plugin.imported_artifact_ids.length > 0);
    elements.developmentBusinessList.replaceChildren(...importedBusinessPlugins.flatMap((plugin) => {
      const enabled = Array.isArray(plugin.enabled_artifact_ids) ? plugin.enabled_artifact_ids : [];
      return plugin.imported_artifact_ids.map((artifactId) => {
        const artifact = (plugin.artifacts || []).find((item) => item.artifact_id === artifactId);
        const row = document.createElement("div");
        row.className = "workstation-status-row";
        const copy = document.createElement("div");
        copy.className = "workstation-status-copy";
        const title = document.createElement("strong");
        title.textContent = artifactTitle(plugin, artifact, true);
        const detail = document.createElement("span");
        setStatus(detail, `导入状态：已导入 · 启用状态：${enabled.includes(artifactId) ? "已启用" : "未启用"}。`, enabled.includes(artifactId) ? "success" : "warning");
        copy.append(title, detail);
        row.append(copy);
        return row;
      });
    }));
    elements.developmentEnvironment.hidden = !runtimeInstalled && importedBusinessPlugins.length === 0;
    syncControls();
  };

  const scheduleWorkerRefresh = (delay = 1000) => {
    window.clearTimeout(workerTimer);
    if (!disposed && (!workerIsCurrent || ["busy", "draining", "recovering", "restarting"].includes(workerState?.state))) {
      workerTimer = window.setTimeout(loadWorker, delay);
    }
  };

  const scheduleUpgradeRefresh = () => {
    window.clearTimeout(upgradeTimer);
    if (!disposed && ["preparing", "rebuilding"].includes(upgradeState?.state)) {
      upgradeTimer = window.setTimeout(loadUpgrade, 1000);
    }
  };

  const loadStatus = async () => {
    try {
      const data = await request("/api/status");
      if (disposed) return false;
      renderStatus(data);
      snapshot.status = data;
      cacheSnapshot();
      setToolbarStatus(toolbarStatus(data));
    } catch (error) {
      if (disposed || error?.name === "AbortError") return false;
      if (!snapshot.status) {
        setStatus(elements.chubDetail, error.message || "无法读取当前控制面状态。", "failed");
      } else {
        showToolbarFeedback(error.message || "Chub 状态读取失败。");
      }
      setToolbarStatus("工作台状态暂时无法更新");
      return false;
    }
    return true;
  };

  const loadWorker = async () => {
    try {
      const data = await request("/api/maintenance/quick-worker", { timeoutMs: 12000 });
      if (disposed) return false;
      renderWorker(data);
      snapshot.worker = data;
      workerIsCurrent = true;
      workerRetryDelay = 1000;
      cacheSnapshot();
    } catch (error) {
      if (disposed || error?.name === "AbortError") return false;
      workerIsCurrent = false;
      if (!snapshot.worker) {
        setStatus(elements.workerDetail, error.message || "无法读取当前任务执行服务状态。", "failed");
      } else {
        showToolbarFeedback(error.message || "Quick Worker 状态读取失败。");
      }
      const retryDelay = workerRetryDelay;
      workerRetryDelay = Math.min(workerRetryDelay * 2, 10000);
      scheduleWorkerRefresh(retryDelay);
      return false;
    }
    scheduleWorkerRefresh();
    return true;
  };

  const loadUpgrade = async () => {
    try {
      const data = await request("/api/maintenance/workstation-rebuild", { cache: "no-store" });
      if (disposed) return false;
      renderUpgrade(data);
      snapshot.upgrade = data;
      upgradeIsCurrent = true;
      cacheSnapshot();
    } catch (error) {
      if (disposed || error?.name === "AbortError") return false;
      if (!snapshot.upgrade) {
        setStatus(elements.upgradeDetail, error.message || "工作站重建状态读取失败。", "failed");
      }
      return false;
    }
    scheduleUpgradeRefresh();
    return true;
  };

  const loadDevelopment = async () => {
    try {
      const [runtime, runtimeManagement, lifecycle] = await Promise.all([
        request("/api/ai/runtime-implementations", { cache: "no-store" }),
        request("/api/ai/runtimes", { cache: "no-store" }),
        request("/api/plugins", { cache: "no-store" }),
      ]);
      if (disposed) return false;
      const runtimePlugin = lifecycle?.plugins?.find((plugin) => plugin.plugin_id === "runtime") || null;
      const businessPlugins = Array.isArray(lifecycle?.plugins)
        ? lifecycle.plugins.filter((plugin) => plugin.plugin_id !== "runtime")
        : [];
      developmentSnapshot = { runtime, runtimeManagement, businessPlugins, runtimePlugin };
      renderDevelopment(runtime, runtimeManagement, businessPlugins, runtimePlugin);
      cacheDevelopmentSnapshot(runtime, runtimeManagement, businessPlugins, runtimePlugin);
      return true;
    } catch (error) {
      if (disposed || error?.name === "AbortError") return false;
      if (!developmentSnapshot) {
        elements.developmentEnvironment.hidden = true;
      } else {
        showToolbarFeedback(error.message || "插件状态读取失败。");
      }
      return false;
    }
  };

  const waitForRestart = async (previousInstanceId) => {
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await waitFor(500);
      if (disposed) throw new DOMException("Workspace workstation was disposed.", "AbortError");
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
      await waitFor(500);
      if (disposed) throw new DOMException("Workspace workstation was disposed.", "AbortError");
      let data;
      try {
        data = await request("/api/maintenance/quick-worker", { cache: "no-store", timeoutMs: 12000 });
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
    setStatus(elements.chubDetail, "正在重启 Web 控制面，并确认新实例健康。", "warning");
    try {
      const previous = await request("/api/health", { cache: "no-store" });
      await request("/api/maintenance/restart", { method: "POST" });
      await waitForRestart(previous.instance_id);
      setStatus(
        elements.chubDetail,
        "Web 控制面已重启并恢复。浏览器将在稍后自动刷新页面。",
        "success",
      );
      setToolbarStatus("Chub Web 控制面已重启并恢复。");
      if (!disposed) pageReloadTimer = window.setTimeout(() => window.location.reload(), 2000);
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
      if (!disposed) pageReloadTimer = window.setTimeout(() => window.location.reload(), 2000);
    } finally {
      workerRestarting = false;
      syncControls();
    }
  };

  const startUpgrade = async () => {
    upgradeStarting = true;
    syncControls();
    try {
      const data = await request("/api/maintenance/workstation-rebuild", { method: "POST" });
      const operationId = data.operation?.operation_id;
      if (!operationId) throw new Error("工作站重建未能创建可确认的维护操作。");
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
  const cachedDevelopmentSnapshot = readDevelopmentSnapshot();
  if (cachedDevelopmentSnapshot) {
    developmentSnapshot = cachedDevelopmentSnapshot;
    renderDevelopment(
      cachedDevelopmentSnapshot.runtime,
      cachedDevelopmentSnapshot.runtimeManagement,
      cachedDevelopmentSnapshot.businessPlugins,
      cachedDevelopmentSnapshot.runtimePlugin || cachedDevelopmentSnapshot.codex,
    );
  }
  syncControls();

  window.disposeWorkspaceWorkstation = () => {
    if (disposed) return;
    disposed = true;
    requestAbortController.abort();
    window.clearTimeout(workerTimer);
    window.clearTimeout(upgradeTimer);
    window.clearTimeout(pageReloadTimer);
    cancelPendingWaits();
  };

  elements.chubRestart.addEventListener("click", () => {
    void showConfirmationDialog({
      title: "重启 Chub Web 控制面",
      body: "重启 Chub 的页面与 API 服务，加载新页面与代码。\nQuick Worker 不会重启。\n已受理的 AI 任务和原生 Runtime Session 会继续运行。\n未提交的页面输入可能丢失。\n页面会短暂断开；确认新实例已启动且健康后，浏览器会自动刷新。",
      confirmLabel: "确认重启",
      pendingLabel: "正在重启 Web 控制面，并确认新实例健康…",
      tone: "secondary",
      errorMessage: "Chub 重启失败。",
      onConfirm: restartChub,
    });
  });
  elements.workerRestart.addEventListener("click", () => {
    void showConfirmationDialog({
      title: "重启 Chub Quick Worker",
      body: "重启后台 AI 任务执行服务，加载新的 Worker 代码与协议。\n排队任务会取消。\n执行中的任务会停止并标记为未完成，且不会自动重试。\nChub 页面与 API、OpenClaw 和原生 Runtime Session 不会重启。",
      confirmLabel: "确认重启",
      pendingLabel: "正在停止任务并等待新 Worker 就绪…",
      errorMessage: "Quick Worker 重启失败。",
      onConfirm: restartWorker,
    });
  });
  elements.upgradeStart.addEventListener("click", () => {
    void showConfirmationDialog({
      title: "重建 Chub 工作站",
      body: "将使用当前代码、配置和 requirements 重建 Chub 工作站。\n缺失或变更的项目 Python 依赖会自动同步。\n当前 Chub 任务会结束，不会恢复或自动重试。\nChub 自有的可重建运行态会清理，并导入启用当前默认 Runtime。\n配置、日志、文档、原生 Runtime Session、OpenClaw 数据和浏览器资料会保留。\n页面会短暂断开；稍后刷新或重新尝试即可。",
      confirmLabel: "确认重建",
      pendingLabel: "正在准备运行环境并重建工作站…",
      errorMessage: "工作站重建未能启动。",
      onConfirm: startUpgrade,
    });
  });

  void Promise.all([loadStatus(), loadWorker(), loadUpgrade()]);
  void loadDevelopment();
};

window.initializeWorkspaceWorkstation();
