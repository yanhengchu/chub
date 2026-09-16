(() => {
  let pollTimer = null;
  let request = null;

  const PENDING_STATUSES = new Set(["submitting", "requested", "running"]);

  const clearPoll = () => {
    if (pollTimer !== null) window.clearTimeout(pollTimer);
    pollTimer = null;
  };

  const statusText = (run) => {
    if (PENDING_STATUSES.has(run.status)) return "正在搜索公开网页…";
    if (run.status === "failed") return run.error || "AI 搜索未能完成。";
    return run.summary || "AI 搜索已完成。";
  };

  const renderResults = (container, run) => {
    container.replaceChildren();
    const summary = document.createElement("p");
    summary.className = run.status === "failed" ? "message message-error" : "workspace-search-summary";
    summary.textContent = statusText(run);
    container.append(summary);
    if (PENDING_STATUSES.has(run.status) || run.status === "failed") return;
    if (!Array.isArray(run.results) || run.results.length === 0) {
      const empty = document.createElement("p");
      empty.className = "workspace-search-feedback";
      empty.textContent = "没有可展示的可靠候选。";
      container.append(empty);
      return;
    }
    const list = document.createElement("div");
    list.className = "workspace-search-results";
    run.results.forEach((result) => {
      const item = document.createElement("article");
      item.className = "workspace-search-result";
      const link = document.createElement("a");
      link.href = result.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = result.title;
      item.append(link);
      const source = document.createElement("p");
      source.className = "workspace-search-result-meta";
      source.textContent = result.source || "网页";
      item.append(source);
      if (result.description) {
        const description = document.createElement("p");
        description.className = "workspace-search-result-description";
        description.textContent = result.description;
        item.append(description);
      }
      list.append(item);
    });
    container.append(list);
  };

  const renderHistory = (container, runs) => {
    container.replaceChildren();
    if (!Array.isArray(runs) || runs.length === 0) {
      const empty = document.createElement("p");
      empty.className = "empty-state";
      empty.textContent = "暂无搜索记录。";
      container.append(empty);
      return;
    }
    runs.forEach((run) => {
      const link = document.createElement("a");
      link.className = "workspace-search-history-item";
      link.href = `/?section=search&search=${encodeURIComponent(run.id)}`;
      const title = document.createElement("strong");
      title.className = "workspace-search-history-title";
      title.textContent = run.query;
      const detail = document.createElement("small");
      detail.className = "workspace-search-history-description";
      detail.textContent = statusText(run);
      link.append(title, detail);
      container.append(link);
    });
  };

  const readJson = async (url, options = {}) => {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => null);
    if (!response.ok || payload?.success !== true) {
      const error = new Error(payload?.error?.message || "AI 搜索暂时不可用。");
      error.code = payload?.error?.code;
      throw error;
    }
    return payload.data;
  };

  const initializeHome = () => {
    const form = document.getElementById("workspace-ai-search-form");
    const query = document.getElementById("workspace-ai-search-query");
    const submit = document.getElementById("workspace-ai-search-submit");
    const history = document.getElementById("workspace-ai-search-history");
    const feedback = document.getElementById("workspace-ai-search-submit-feedback");
    if (!(form instanceof HTMLFormElement) || !(query instanceof HTMLInputElement)
      || !(submit instanceof HTMLButtonElement) || !(history instanceof HTMLElement)
      || !(feedback instanceof HTMLElement)) return;
    const setFeedback = (message = "", tone = "error") => {
      feedback.textContent = message;
      feedback.hidden = !message;
      feedback.className = `workspace-search-submit-feedback message${message ? ` message-${tone}` : ""}`;
    };
    const refresh = async () => {
      const data = await readJson("/api/search/session", { cache: "no-store" });
      renderHistory(history, data.runs);
      submit.disabled = Boolean(data.current && PENDING_STATUSES.has(data.current.status));
    };
    void refresh().catch((error) => {
      const message = document.createElement("p");
      message.className = "message message-error";
      message.textContent = error.message;
      history.replaceChildren(message);
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const value = query.value.trim();
      if (value.length < 2) {
        query.focus();
        return;
      }
      request?.abort();
      request = new AbortController();
      submit.disabled = true;
      setFeedback();
      try {
        const data = await readJson("/api/search/session", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query: value }),
          signal: request.signal,
        });
        if (!data.current?.id) throw new Error("搜索任务状态无法确认。");
        window.location.assign(`/?section=search&search=${encodeURIComponent(data.current.id)}`);
      } catch (error) {
        if (error?.name !== "AbortError") {
          const message = error instanceof Error ? error.message : "AI 搜索暂时不可用。";
          const accepted = error?.code === "ai_search_submission_recording_pending";
          setFeedback(message, accepted ? "warning" : "error");
          if (accepted) {
            void refresh().catch(() => {});
          } else {
            submit.disabled = false;
          }
        }
      } finally {
        if (request?.signal.aborted === false) request = null;
      }
    });
  };

  const initializeDetail = () => {
    const detail = document.querySelector(".workspace-search-detail[data-search-id]");
    const status = document.getElementById("workspace-search-detail-status");
    const query = document.getElementById("workspace-search-detail-query");
    const prompt = document.getElementById("workspace-search-detail-prompt");
    const results = document.getElementById("workspace-ai-search-detail-results");
    if (!(detail instanceof HTMLElement) || !(status instanceof HTMLElement)
      || !(query instanceof HTMLElement) || !(prompt instanceof HTMLElement) || !(results instanceof HTMLElement)) return;
    const id = detail.dataset.searchId;
    if (!id) return;
    const refresh = async () => {
      const run = await readJson(`/api/search/runs/${encodeURIComponent(id)}`, { cache: "no-store" });
      query.textContent = run.query;
      prompt.textContent = run.prompt;
      status.hidden = true;
      status.textContent = "";
      status.className = "workspace-search-feedback";
      renderResults(results, run);
      clearPoll();
      if (PENDING_STATUSES.has(run.status)) {
        pollTimer = window.setTimeout(() => void refresh().catch((error) => {
          status.className = "message message-error";
          status.textContent = error.message;
          status.hidden = false;
        }), 1500);
      }
    };
    void refresh().catch((error) => {
      status.className = "message message-error";
      status.textContent = error.message;
      status.hidden = false;
    });
  };

  window.disposeWorkspaceSearch = () => {
    request?.abort();
    request = null;
    clearPoll();
  };

  window.initializeWorkspaceSearch = () => {
    window.disposeWorkspaceSearch?.();
    initializeHome();
    initializeDetail();
  };

  window.initializeWorkspaceSearch?.();
})();
