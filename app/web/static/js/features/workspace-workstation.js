"use strict";

window.initializeWorkspaceWorkstation = () => {
  window.disposeWorkspaceWorkstation?.();
  const byId = (id) => document.getElementById(id);
  const elements = {
    health: byId("workspace-preview-health"),
    refresh: byId("workspace-workstation-refresh"),
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
    developmentRefresh: byId("workspace-development-refresh"),
    developmentEnvironment: byId("workspace-development-environment"),
    developmentCodexRow: byId("workspace-development-codex-row"),
    developmentCodexDetail: byId("workspace-development-codex-detail"),
    developmentWeixinRow: byId("workspace-development-weixin-row"),
    developmentWeixinDetail: byId("workspace-development-weixin-detail"),
    developmentDeliverylineRow: byId("workspace-development-deliveryline-row"),
    developmentDeliverylineDetail: byId("workspace-development-deliveryline-detail"),
    thirdPartyRefresh: byId("workspace-third-party-refresh"),
    openclawDetail: byId("workspace-openclaw-detail"),
    openclawStart: byId("workspace-openclaw-start"),
    openclawRestart: byId("workspace-openclaw-restart"),
    openclawWeixinDetail: byId("workspace-openclaw-weixin-detail"),
    openclawBindWeixin: byId("workspace-openclaw-bind-weixin"),
    openclawWeixinDialog: byId("workspace-openclaw-weixin-dialog"),
    openclawWeixinClose: byId("workspace-openclaw-weixin-close"),
    openclawWeixinAccountSummary: byId("workspace-openclaw-weixin-account-summary"),
    openclawWeixinOwnerSummary: byId("workspace-openclaw-weixin-owner-summary"),
    openclawWeixinQrPanel: byId("workspace-openclaw-weixin-qr-panel"),
    openclawWeixinQr: byId("workspace-openclaw-weixin-qr"),
    openclawWeixinVerifyForm: byId("workspace-openclaw-weixin-verify-form"),
    openclawWeixinVerifyCode: byId("workspace-openclaw-weixin-verify-code"),
    openclawWeixinMessage: byId("workspace-openclaw-weixin-message"),
    openclawWeixinCancel: byId("workspace-openclaw-weixin-cancel"),
    openclawWeixinStart: byId("workspace-openclaw-weixin-start"),
  };
  if (!Object.values(elements).every((element) => element instanceof HTMLElement)) return;

  let workerState = null;
  let upgradeState = null;
  let hubRestarting = false;
  let workerRestarting = false;
  let upgradeStarting = false;
  let openclawOperating = false;
  let thirdPartyLoading = false;
  let developmentLoading = false;
  let developmentRefreshing = false;
  let developmentSnapshot = null;
  let openclawStatus = null;
  let openclawIntegration = null;
  let openclawWeixinLogin = null;
  let openclawWeixinPollTimer = 0;
  let openclawWeixinQrObjectUrl = "";
  let openclawWeixinQrUpdatedAt = "";
  let workerTimer = 0;
  let workerRetryDelay = 1000;
  let upgradeTimer = 0;
  let pageReloadTimer = 0;
  const pendingWaits = new Map();
  let disposed = false;
  let statusIsCurrent = false;
  let workerIsCurrent = false;
  let upgradeIsCurrent = false;
  let snapshot = { status: null, worker: null, upgrade: null };
  const snapshotCacheKey = "chub.workspace.workstation.v1";
  const thirdPartySnapshotCacheKey = "chub.workspace.thirdParty.v1";
  const developmentSnapshotCacheKey = "chub.workspace.development.v1";
  const workbenchStatusLoadingMinimumMs = 220;
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

  const readThirdPartySnapshot = () => {
    try {
      const cached = JSON.parse(
        window.sessionStorage.getItem(thirdPartySnapshotCacheKey) || "null",
      );
      if (!isSnapshotValue(cached?.status) || !isSnapshotValue(cached?.login)) {
        return null;
      }
      return cached;
    } catch {
      return null;
    }
  };

  const cacheThirdPartySnapshot = (status, login, integration) => {
    try {
      window.sessionStorage.setItem(
        thirdPartySnapshotCacheKey,
        JSON.stringify({ status, login, integration }),
      );
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
        || !isSnapshotValue(cached?.orchestration)
        || !isSnapshotValue(cached?.modules)
      ) return null;
      return cached;
    } catch {
      return null;
    }
  };

  const cacheDevelopmentSnapshot = (runtime, runtimeManagement, orchestration, modules, deliveryline, codex, weixin) => {
    try {
      window.sessionStorage.setItem(
        developmentSnapshotCacheKey,
        JSON.stringify({ runtime, runtimeManagement, orchestration, modules, deliveryline, codex, weixin }),
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

  const setMessage = (target, text = "", kind = "") => {
    target.textContent = text;
    target.className = kind ? `message message-${kind}` : "message";
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
    unavailable: ["不可用", "远程访问", "warning"],
    unknown: ["状态未知", "远程访问", "muted"],
  })[state] || ["状态未知", "远程访问", "muted"];

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
    if (disposed) return;
    const upgradeRunning = Boolean(upgradeState?.operation?.status === "started");
    elements.chubRestart.disabled = hubRestarting || upgradeRunning;
    elements.workerRestart.disabled = workerRestarting || !workerState?.can_restart || upgradeRunning;
    elements.upgradeStart.disabled = upgradeStarting || !upgradeIsCurrent || !upgradeState?.can_start;
    elements.developmentRefresh.disabled = developmentLoading || developmentRefreshing;
    const gatewayReady = Boolean(openclawStatus?.installed && openclawStatus?.configured);
    const activeLogin = ["starting", "waiting_scan", "needs_verification", "confirming", "cancelling"].includes(openclawWeixinLogin?.state);
    const gatewayStopped = openclawStatus?.state === "stopped";
    const gatewayRestartable = ["running", "degraded"].includes(openclawStatus?.state);
    elements.openclawStart.hidden = !gatewayStopped;
    elements.openclawRestart.hidden = !gatewayRestartable;
    elements.openclawStart.disabled = openclawOperating || upgradeRunning || openclawStatus?.state !== "stopped";
    elements.openclawRestart.disabled = openclawOperating || upgradeRunning || !gatewayReady || !gatewayRestartable;
    elements.thirdPartyRefresh.disabled = thirdPartyLoading;
    elements.openclawBindWeixin.disabled = thirdPartyLoading || openclawOperating || !gatewayReady || activeLogin || !openclawWeixinLogin;
  };

  const renderStatus = (data) => {
    const system = data.system;
    elements.systemSummary.textContent = `CPU ${Math.round(system.cpu_percent)}%`;
    elements.systemSummaryDetail.textContent = `内存 ${Math.round(system.memory_percent)}% · 磁盘 ${Math.round(system.disk_percent)}%`;
    setStatus(
      elements.chubDetail,
      `Chub v${data.hub.version} · ${system.operating_system} ${system.operating_system_version} · Python ${system.python_version}`,
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
      ? `Worker ${workerVersion} · 协议 v${data.protocol_version}${data.state === "incompatible" && data.expected_protocol_version ? `（需要 v${data.expected_protocol_version}）` : ""} · `
      : "";
    setStatus(
      elements.workerDetail,
      operationFailed || data.state === "restarting"
        ? (operationFailed ? data.operation.message : data.message)
        : `${versionDetail}${data.message}`,
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
    setStatus(elements.upgradeDetail, `状态：${upgradeLabel(data)}。${data.message}`, data.state === "failed" ? "failed" : data.can_start ? "success" : "warning");
    syncControls();
  };

  const formalVersion = (version) => {
    const normalized = typeof version === "string" ? version.trim().replace(/^v/i, "") : "";
    return normalized ? `v${normalized}` : "未知版本";
  };

  const renderDevelopment = (runtime, runtimeManagement, orchestration, modules, deliveryline, codex, weixin) => {
    const artifactTitle = (plugin, artifactId) => {
      const artifact = (plugin?.artifacts || []).find((item) => item.artifact_id === artifactId);
      const name = plugin?.plugin_id === "codex-runtime" ? "Codex" : plugin?.name || "未知插件";
      return artifact?.source === "development"
        ? `${name} · 开发实现`
        : artifact?.version
          ? `${name} · 正式版 ${formalVersion(artifact.version)}`
          : `${name} · 未知版本`;
    };
    const renderLifecyclePlugin = (plugin, row, detail) => {
      const imported = Array.isArray(plugin?.imported_artifact_ids) ? plugin.imported_artifact_ids : [];
      const enabled = Array.isArray(plugin?.enabled_artifact_ids) ? plugin.enabled_artifact_ids : [];
      row.hidden = imported.length === 0;
      if (imported.length === 0) return false;
      const artifactId = enabled[0] || imported[0];
      const isEnabled = enabled.length > 0;
      setStatus(
        detail,
        `插件版本：${artifactTitle(plugin, artifactId)} · 导入状态：已导入 · 启用状态：${isEnabled ? "已启用" : "未启用"}。`,
        isEnabled ? "success" : "warning",
      );
      return true;
    };
    const codexInstalled = renderLifecyclePlugin(codex, elements.developmentCodexRow, elements.developmentCodexDetail);
    const weixinInstalled = renderLifecyclePlugin(weixin, elements.developmentWeixinRow, elements.developmentWeixinDetail);
    elements.developmentEnvironment.hidden = !codexInstalled && !weixinInstalled;
    const deliverylineInstalled = Array.isArray(deliveryline?.imported_artifact_ids) && deliveryline.imported_artifact_ids.length > 0;
    elements.developmentDeliverylineRow.hidden = !deliverylineInstalled;
    const deliverylineEnabled = Array.isArray(deliveryline?.enabled_artifact_ids) && deliveryline.enabled_artifact_ids.length > 0;
    if (deliverylineInstalled) {
      const deliverylineArtifactId = deliverylineEnabled
        ? deliveryline.enabled_artifact_ids[0]
        : deliveryline.imported_artifact_ids[0];
      const deliverylineArtifact = (deliveryline.artifacts || []).find(
        (item) => item.artifact_id === deliverylineArtifactId,
      );
      const deliverylineVersion = deliverylineArtifact?.source === "development"
        ? "Deliveryline · 开发实现"
        : deliverylineArtifact?.version
          ? `Deliveryline · 正式版 ${formalVersion(deliverylineArtifact.version)}`
          : "Deliveryline · 未知版本";
      setStatus(
        elements.developmentDeliverylineDetail,
        `插件版本：${deliverylineVersion} · 导入状态：已导入 · 启用状态：${deliverylineEnabled ? "已启用" : "未启用"}。`,
        deliverylineEnabled ? "success" : "warning",
      );
    }
    elements.developmentEnvironment.hidden = elements.developmentEnvironment.hidden && !deliverylineInstalled;
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
    if (!disposed && ["preparing", "draining", "archiving", "cleaning", "restarting"].includes(upgradeState?.state)) {
      upgradeTimer = window.setTimeout(loadUpgrade, 1000);
    }
  };

  const loadStatus = async () => {
    try {
      const data = await request("/api/status");
      if (disposed) return false;
      renderStatus(data);
      snapshot.status = data;
      statusIsCurrent = true;
      cacheSnapshot();
    } catch (error) {
      if (disposed || error?.name === "AbortError") return false;
      if (!snapshot.status) {
        setStatus(elements.chubDetail, error.message || "无法读取当前控制面状态。", "failed");
      } else {
        showToolbarFeedback(error.message || "Chub 状态读取失败。");
      }
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
      const data = await request("/api/maintenance/system-upgrade");
      if (disposed) return false;
      renderUpgrade(data);
      snapshot.upgrade = data;
      upgradeIsCurrent = true;
      cacheSnapshot();
    } catch (error) {
      if (disposed || error?.name === "AbortError") return false;
      if (!snapshot.upgrade) {
        setStatus(elements.upgradeDetail, error.message || "升级与恢复状态读取失败。", "failed");
      }
      return false;
    }
    scheduleUpgradeRefresh();
    return true;
  };

  const renderOpenClaw = (status, login, integration = openclawIntegration) => {
    openclawStatus = status;
    openclawIntegration = integration;
    openclawWeixinLogin = login;
    const gatewayVersion = typeof status?.version === "string" && status.version
      ? `OpenClaw / Gateway v${status.version} · `
      : "";
    const gatewayDetail = status?.state === "running"
      ? "Gateway 运行正常并已通过连接探测。"
      : status?.message || "暂时无法读取 OpenClaw Gateway 状态。";
    const gatewayKind = ["running"].includes(status?.state)
      ? "success"
      : ["degraded", "stopped", "unconfigured", "service_missing"].includes(status?.state)
        ? "warning"
        : ["unavailable", "unknown"].includes(status?.state) ? "failed" : "muted";
    setStatus(elements.openclawDetail, `${gatewayVersion}${gatewayDetail}`, gatewayKind);
    elements.openclawWeixinAccountSummary.textContent = status?.channel_message || "当前消息通道状态不可用。";
    elements.openclawWeixinOwnerSummary.textContent = status?.owner_message || "当前 Owner 授权状态不可用。";
    const activePresentation = {
      starting: ["正在准备微信绑定。", "warning"],
      waiting_scan: ["等待使用手机微信扫码。", "warning"],
      needs_verification: ["等待提交手机显示的验证码。", "warning"],
      confirming: ["正在确认微信连接。", "warning"],
      cancelling: ["正在取消微信绑定。", "warning"],
    }[login?.state];
    const channelPresentation = {
      running: [status?.channel_message, "success"],
      degraded: [status?.channel_message, "warning"],
      stopped: [status?.channel_message, "failed"],
      not_configured: [status?.channel_message, "muted"],
      unavailable: [status?.channel_message, "muted"],
      unknown: [status?.channel_message, "failed"],
    }[status?.channel_state];
    const [weixinDetail, weixinKind] = activePresentation
      || channelPresentation
      || ["暂时无法读取微信 ClawBot 状态。", "muted"];
    const weixinAdapterVersion = typeof integration?.weixin_adapter?.version === "string"
      && integration.weixin_adapter.version
      ? `微信 ClawBot v${integration.weixin_adapter.version} · `
      : "";
    setStatus(
      elements.openclawWeixinDetail,
      `${weixinAdapterVersion}${weixinDetail || "暂时无法读取微信 ClawBot 状态。"}`,
      weixinKind,
    );
    elements.openclawBindWeixin.textContent = status?.channel_state === "running" ? "重新绑定微信" : "绑定微信";
    syncControls();
  };

  const loadThirdParty = async () => {
    thirdPartyLoading = true;
    syncControls();
    try {
      const [status, login, integration] = await Promise.all([
        request("/api/openclaw/status", { cache: "no-store" }),
        request("/api/openclaw/weixin/login", { cache: "no-store" }),
        request("/api/openclaw/integration", { cache: "no-store" }).catch(() => undefined),
      ]);
      if (disposed) return false;
      renderOpenClaw(status, login, integration);
      cacheThirdPartySnapshot(status, login, openclawIntegration);
      return true;
    } catch (error) {
      if (disposed || error?.name === "AbortError") return false;
      if (!openclawStatus) {
        setStatus(elements.openclawDetail, error.message || "暂时无法读取 OpenClaw Gateway 状态。", "failed");
        setStatus(elements.openclawWeixinDetail, error.message || "暂时无法读取微信 ClawBot 状态。", "failed");
      } else {
        showToolbarFeedback(error.message || "第三方服务状态读取失败。");
      }
      syncControls();
      return false;
    } finally {
      thirdPartyLoading = false;
      syncControls();
    }
  };

  const loadDevelopment = async () => {
    developmentLoading = true;
    syncControls();
    try {
      const [runtime, runtimeManagement, lifecycle] = await Promise.all([
        request("/api/codex/runtime-implementations", { cache: "no-store" }),
        request("/api/codex/runtimes", { cache: "no-store" }),
        request("/api/plugins", { cache: "no-store" }),
      ]);
      if (disposed) return false;
      const codex = lifecycle?.plugins?.find((plugin) => plugin.plugin_id === "codex-runtime") || null;
      const weixin = lifecycle?.plugins?.find((plugin) => plugin.plugin_id === "weixin-orchestration") || null;
      const enabledIds = Array.isArray(weixin?.enabled_artifact_ids) ? weixin.enabled_artifact_ids : [];
      const activeId = enabledIds[0] || "";
      const activeModule = activeId.startsWith("orchestration:") ? activeId.slice("orchestration:".length) : null;
      const modules = { modules: (weixin?.artifacts || [])
        .filter((item) => item.artifact_id?.startsWith("orchestration:"))
        .map((item) => ({ ...item, implementation_ref: item.artifact_id.slice("orchestration:".length) })) };
      const orchestration = {
        implementation: activeId === "development:weixin-orchestration" ? "weixin-orchestration-dev" : activeModule ? "module" : "disabled",
        module_ref: activeModule,
        enabled: activeId !== "",
        development_available: Boolean((weixin?.artifacts || []).find((item) => item.artifact_id === "development:weixin-orchestration")?.available),
      };
      const deliveryline = lifecycle?.plugins?.find((plugin) => plugin.plugin_id === "deliveryline") || null;
      developmentSnapshot = { runtime, runtimeManagement, orchestration, modules, deliveryline, codex, weixin };
      renderDevelopment(runtime, runtimeManagement, orchestration, modules, deliveryline, codex, weixin);
      cacheDevelopmentSnapshot(runtime, runtimeManagement, orchestration, modules, deliveryline, codex, weixin);
      return true;
    } catch (error) {
      if (disposed || error?.name === "AbortError") return false;
      if (!developmentSnapshot) {
        elements.developmentEnvironment.hidden = true;
      } else {
        showToolbarFeedback(error.message || "插件状态读取失败。");
      }
      return false;
    } finally {
      developmentLoading = false;
      syncControls();
    }
  };

  const refreshDevelopment = async () => {
    developmentRefreshing = true;
    syncControls();
    setStatus(elements.developmentCodexDetail, "正在重新读取当前 Codex Runtime 实现。", "warning");
    setStatus(elements.developmentWeixinDetail, "正在重新读取当前微信任务润色实现。", "warning");
    try {
      await loadDevelopment();
    } finally {
      developmentRefreshing = false;
      syncControls();
    }
  };

  const controlOpenClaw = async (action) => {
    openclawOperating = true;
    syncControls();
    if (action === "restart") {
      setStatus(
        elements.openclawDetail,
        "正在重启与恢复 OpenClaw Gateway，并确认 Gateway 与消息通道最终状态。",
        "warning",
      );
    } else if (action === "start") {
      setStatus(elements.openclawDetail, "正在启动 OpenClaw Gateway，并确认最终状态。", "warning");
    }
    try {
      const status = await request(`/api/openclaw/${action}`, { method: "POST" });
      const login = await request("/api/openclaw/weixin/login", { cache: "no-store" });
      renderOpenClaw(status, login);
      cacheThirdPartySnapshot(status, login, openclawIntegration);
    } catch (error) {
      const message = error.message || "OpenClaw Gateway 操作失败。";
      setStatus(elements.openclawDetail, message, "failed");
      showToolbarFeedback(message);
    } finally {
      openclawOperating = false;
      syncControls();
    }
  };

  const releaseOpenClawWeixinQr = () => {
    if (openclawWeixinQrObjectUrl) URL.revokeObjectURL(openclawWeixinQrObjectUrl);
    openclawWeixinQrObjectUrl = "";
    openclawWeixinQrUpdatedAt = "";
    elements.openclawWeixinQr.removeAttribute("src");
    elements.openclawWeixinQrPanel.hidden = true;
  };

  const stopOpenClawWeixinPolling = () => {
    if (openclawWeixinPollTimer) window.clearTimeout(openclawWeixinPollTimer);
    openclawWeixinPollTimer = 0;
  };

  const closeOpenClawWeixinDialog = () => {
    stopOpenClawWeixinPolling();
    releaseOpenClawWeixinQr();
    elements.openclawWeixinVerifyForm.hidden = true;
    elements.openclawWeixinVerifyCode.value = "";
    if (elements.openclawWeixinDialog.open) elements.openclawWeixinDialog.close();
  };

  const loadOpenClawWeixinQr = async (updatedAt) => {
    if (disposed || (openclawWeixinQrObjectUrl && openclawWeixinQrUpdatedAt === updatedAt)) return;
    try {
      const response = await fetch("/api/openclaw/weixin/login/qr", {
        cache: "no-store",
        signal: requestAbortController.signal,
      });
      if (disposed || !response.ok || !elements.openclawWeixinDialog.open) throw new Error("weixin_qr_unavailable");
      releaseOpenClawWeixinQr();
      openclawWeixinQrObjectUrl = URL.createObjectURL(await response.blob());
      openclawWeixinQrUpdatedAt = updatedAt;
      elements.openclawWeixinQr.src = openclawWeixinQrObjectUrl;
      elements.openclawWeixinQrPanel.hidden = false;
    } catch {
      if (disposed) return;
      setMessage(elements.openclawWeixinMessage, "微信绑定二维码读取失败。", "error");
    }
  };

  const renderOpenClawWeixinLogin = (login) => {
    const previousState = openclawWeixinLogin?.state;
    openclawWeixinLogin = login;
    const active = ["starting", "waiting_scan", "needs_verification", "confirming", "cancelling"].includes(login.state);
    const needsVerification = login.state === "needs_verification";
    elements.openclawWeixinCancel.hidden = !active || login.state === "cancelling";
    elements.openclawWeixinStart.hidden = active;
    elements.openclawWeixinStart.textContent = openclawStatus?.channel_state === "running" || login.state !== "idle" ? "重新生成二维码" : "生成二维码";
    elements.openclawWeixinVerifyForm.hidden = !needsVerification;
    setMessage(elements.openclawWeixinMessage, login.message, login.state === "succeeded" ? "success" : login.state === "failed" ? "error" : "");
    if (login.qr_available) void loadOpenClawWeixinQr(login.updated_at);
    else releaseOpenClawWeixinQr();
    if (openclawStatus) renderOpenClaw(openclawStatus, login);
    if (login.state === "succeeded" && previousState !== "succeeded") void loadThirdParty();
  };

  const pollOpenClawWeixinLogin = async () => {
    stopOpenClawWeixinPolling();
    if (disposed || !elements.openclawWeixinDialog.open) return;
    try {
      const login = await request("/api/openclaw/weixin/login", { cache: "no-store" });
      renderOpenClawWeixinLogin(login);
      if (!disposed && ["starting", "waiting_scan", "needs_verification", "confirming", "cancelling"].includes(login.state)) {
        openclawWeixinPollTimer = window.setTimeout(pollOpenClawWeixinLogin, 1000);
      }
    } catch (error) {
      if (disposed || error?.name === "AbortError") return;
      setMessage(elements.openclawWeixinMessage, error.message || "微信绑定状态读取失败。", "error");
      if (!disposed && elements.openclawWeixinDialog.open) openclawWeixinPollTimer = window.setTimeout(pollOpenClawWeixinLogin, 2000);
    }
  };

  const refresh = async () => {
    const refreshStartedAt = window.performance.now();
    elements.refresh.disabled = true;
    statusIsCurrent = false;
    workerIsCurrent = false;
    upgradeIsCurrent = false;
    syncControls();
    setToolbarStatus("正在读取工作台状态…");
    const results = await Promise.all([loadStatus(), loadWorker(), loadUpgrade()]);
    if (disposed) return;
    const remainingLoadingTime = Math.max(
      0,
      workbenchStatusLoadingMinimumMs - (window.performance.now() - refreshStartedAt),
    );
    if (remainingLoadingTime) {
      await waitFor(remainingLoadingTime);
    }
    if (disposed) return;
    if (results.every(Boolean) && snapshot.status) {
      setToolbarStatus(toolbarStatus(snapshot.status));
    } else {
      setToolbarStatus("工作台状态暂时无法更新");
    }
    elements.refresh.disabled = false;
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
  const cachedThirdPartySnapshot = readThirdPartySnapshot();
  if (cachedThirdPartySnapshot) {
    renderOpenClaw(
      cachedThirdPartySnapshot.status,
      cachedThirdPartySnapshot.login,
      cachedThirdPartySnapshot.integration,
    );
  }
  const cachedDevelopmentSnapshot = readDevelopmentSnapshot();
  if (cachedDevelopmentSnapshot) {
    developmentSnapshot = cachedDevelopmentSnapshot;
    renderDevelopment(
      cachedDevelopmentSnapshot.runtime,
      cachedDevelopmentSnapshot.runtimeManagement,
      cachedDevelopmentSnapshot.orchestration,
      cachedDevelopmentSnapshot.modules,
      cachedDevelopmentSnapshot.deliveryline,
      cachedDevelopmentSnapshot.codex,
      cachedDevelopmentSnapshot.weixin,
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
    stopOpenClawWeixinPolling();
    releaseOpenClawWeixinQr();
  };

  elements.refresh.addEventListener("click", () => { void refresh(); });
  elements.thirdPartyRefresh.addEventListener("click", () => { void loadThirdParty(); });
  elements.developmentRefresh.addEventListener("click", () => { void refreshDevelopment(); });
  elements.openclawStart.addEventListener("click", () => { void controlOpenClaw("start"); });
  elements.openclawRestart.addEventListener("click", () => {
    void showConfirmationDialog({
      title: "重启与恢复 OpenClaw Gateway",
      description: "Gateway 和微信消息通道会短暂中断。Chub 将先检查固定插件和补丁基线，再确认 Gateway 与消息通道最终状态。",
      confirmLabel: "确认重启与恢复",
      tone: "secondary",
      closeOnConfirm: true,
      onConfirm: () => controlOpenClaw("restart"),
    });
  });
  elements.openclawBindWeixin.addEventListener("click", async () => {
    elements.openclawWeixinDialog.showModal();
    setMessage(elements.openclawWeixinMessage, "正在读取微信绑定状态…");
    await pollOpenClawWeixinLogin();
  });
  elements.openclawWeixinClose.addEventListener("click", closeOpenClawWeixinDialog);
  elements.openclawWeixinStart.addEventListener("click", async () => {
    elements.openclawWeixinStart.disabled = true;
    setMessage(elements.openclawWeixinMessage, "正在生成微信绑定二维码…");
    try {
      renderOpenClawWeixinLogin(await request("/api/openclaw/weixin/login", { method: "POST" }));
      openclawWeixinPollTimer = window.setTimeout(pollOpenClawWeixinLogin, 500);
    } catch (error) {
      setMessage(elements.openclawWeixinMessage, error.message || "微信绑定启动失败。", "error");
    } finally {
      elements.openclawWeixinStart.disabled = false;
    }
  });
  elements.openclawWeixinCancel.addEventListener("click", async () => {
    elements.openclawWeixinCancel.disabled = true;
    try {
      renderOpenClawWeixinLogin(await request("/api/openclaw/weixin/login", { method: "DELETE" }));
    } catch (error) {
      setMessage(elements.openclawWeixinMessage, error.message || "微信绑定取消失败。", "error");
    } finally {
      elements.openclawWeixinCancel.disabled = false;
    }
  });
  elements.openclawWeixinVerifyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const code = elements.openclawWeixinVerifyCode.value.trim();
    if (!code) return;
    try {
      elements.openclawWeixinVerifyCode.value = "";
      renderOpenClawWeixinLogin(await request("/api/openclaw/weixin/login/verify", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      }));
    } catch (error) {
      setMessage(elements.openclawWeixinMessage, error.message || "验证码提交失败。", "error");
    }
  });
  elements.openclawWeixinDialog.addEventListener("click", (event) => {
    if (event.target === elements.openclawWeixinDialog) closeOpenClawWeixinDialog();
  });
  elements.openclawWeixinDialog.addEventListener("close", () => {
    stopOpenClawWeixinPolling();
    releaseOpenClawWeixinQr();
  });
  elements.chubRestart.addEventListener("click", () => {
    void showConfirmationDialog({
      title: "重启 Chub",
      description: "只重启 Chub Web 控制面；已接受的快速任务、Quick Worker 和原生 Codex 不会被停止。",
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
      description: "排队任务会取消，执行中的快速任务会停止并标记为未完成，且不会自动重试。Chub 和 OpenClaw 不受影响。",
      confirmLabel: "确认重启",
      pendingLabel: "正在下发…",
      errorMessage: "Quick Worker 重启失败。",
      onConfirm: restartWorker,
    });
  });
  elements.upgradeStart.addEventListener("click", () => {
    void showConfirmationDialog({
      title: "升级与恢复",
      description: "此操作会清理 Chub 自有 AI 运行状态并重启 Chub Web 与 Quick Worker；本地关联 Session 会按固定边界清理，Codex 原生会话保留。",
      confirmLabel: upgradeState?.resume ? "继续恢复" : "确认升级与恢复",
      pendingLabel: "正在开始…",
      errorMessage: "升级与恢复未能启动。",
      onConfirm: startUpgrade,
    });
  });

  void refresh();
  void loadDevelopment();
  void loadThirdParty();
};

window.initializeWorkspaceWorkstation();
