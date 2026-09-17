(() => {
  let pollTimer = null;
  let request = null;

  const PENDING_STATUSES = new Set(["submitting", "requested", "running"]);

  const clearPoll = () => {
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = null;
  };

  const readJson = async (url, options = {}) => {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => null);
    if (!response.ok || payload?.success !== true) {
      const error = new Error(payload?.error?.message || "今日关注暂时不可用。");
      error.code = payload?.error?.code;
      throw error;
    }
    return payload.data;
  };

  const formatUpdatedAt = (value) => {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "已更新";
    return `更新于 ${new Intl.DateTimeFormat("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).format(date)}`;
  };

  const renderResults = (container, run) => {
    container.replaceChildren();
    if (!run) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "尚未生成今日 AI 动态。";
      container.append(empty);
      return;
    }
    const summary = document.createElement("p");
    summary.className = run.status === "failed"
      ? "message message-error"
      : "workspace-today-focus-summary";
    summary.textContent = run.status === "failed"
      ? (run.error || "今日 AI 动态未能完成。")
      : (run.summary || "尚未生成今日 AI 动态。")
    container.append(summary);
    if (run.status === "failed" || !Array.isArray(run.results) || run.results.length === 0) return;
    const list = document.createElement("div");
    list.className = "workspace-today-focus-list";
    run.results.forEach((result) => {
      const item = document.createElement("article");
      item.className = "workspace-today-focus-result";
      const link = document.createElement("a");
      link.href = result.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = result.title;
      const source = document.createElement("p");
      source.className = "workspace-today-focus-result-meta";
      source.textContent = result.source || "网页";
      item.append(link, source);
      if (result.description) {
        const description = document.createElement("p");
        description.className = "workspace-today-focus-result-description";
        description.textContent = result.description;
        item.append(description);
      }
      list.append(item);
    });
    container.append(list);
  };

  const initializeTodayFocus = () => {
    const refreshButton = document.getElementById("workspace-today-focus-refresh");
    const status = document.getElementById("workspace-today-focus-status");
    const results = document.getElementById("workspace-today-focus-results");
    if (!(refreshButton instanceof HTMLButtonElement)
      || !(status instanceof HTMLElement) || !(results instanceof HTMLElement)) return;

    const showError = (error) => {
      status.textContent = error instanceof Error ? error.message : "今日关注暂时不可用。";
    };
    const refresh = async () => {
      const data = await readJson("/api/today-focus", { cache: "no-store" });
      const current = data?.current;
      const latest = data?.latest;
      refreshButton.disabled = Boolean(current && PENDING_STATUSES.has(current.status));
      renderResults(results, latest);
      if (current && PENDING_STATUSES.has(current.status)) {
        status.textContent = "正在更新今日 AI 动态…";
        clearPoll();
        pollTimer = window.setTimeout(() => void refresh().catch(showError), 1500);
      } else if (latest?.status === "failed") {
        status.textContent = "上次更新失败。";
      } else if (latest?.updated_at) {
        status.textContent = formatUpdatedAt(latest.updated_at);
      } else {
        status.textContent = "尚未生成今日 AI 动态。";
      }
    };

    void refresh().catch(showError);
    refreshButton.addEventListener("click", async () => {
      request?.abort();
      request = new AbortController();
      refreshButton.disabled = true;
      status.textContent = "正在提交今日 AI 动态…";
      try {
        await refreshTodayFocus();
      } catch (error) {
        if (error?.name !== "AbortError") showError(error);
        refreshButton.disabled = false;
      } finally {
        if (request?.signal.aborted === false) request = null;
      }
    });

    const refreshTodayFocus = async () => {
      await readJson("/api/today-focus/refresh", {
        method: "POST",
        signal: request.signal,
      });
      await refresh();
    };
  };

  window.disposeWorkspaceSearch = () => {
    request?.abort();
    request = null;
    clearPoll();
  };

  window.initializeWorkspaceSearch = () => {
    window.disposeWorkspaceSearch?.();
    initializeTodayFocus();
  };

  window.initializeWorkspaceSearch?.();
})();
