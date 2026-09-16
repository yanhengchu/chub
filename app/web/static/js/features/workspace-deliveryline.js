(() => {
  let lines = [];
  let selectedId = null;
  let refreshTimer = null;

  const request = async (path, options = {}) => {
    const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
    const body = await response.json().catch(() => null);
    if (!response.ok || body?.success !== true) throw new Error(body?.error?.message || "交付线操作失败。");
    return body.data;
  };
  const text = (value) => String(value || "");
  const list = (value) => Array.isArray(value) ? value : [];
  const linesFromText = (value) => text(value).split("\n").map((item) => item.trim()).filter(Boolean);

  const appendLinkedText = (element, value) => {
    const source = text(value || "待补充");
    const pattern = /https?:\/\/[^\s<>"']+/g;
    let cursor = 0;
    source.replace(pattern, (match, index) => {
      const url = match.replace(/[),.;!?]+$/, "");
      element.append(document.createTextNode(source.slice(cursor, index)));
      if (url) {
        const link = document.createElement("a");
        link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; link.textContent = url;
        element.append(link, document.createTextNode(match.slice(url.length)));
      } else element.append(document.createTextNode(match));
      cursor = index + match.length;
      return match;
    });
    element.append(document.createTextNode(source.slice(cursor)));
  };

  const node = (tag, value = "", className = "") => {
    const element = document.createElement(tag);
    element.textContent = value;
    element.className = className;
    return element;
  };

  const field = (label, value, linked = false) => {
    const section = node("section", "", "deliveryline-preview-field");
    const content = node("strong");
    if (linked) appendLinkedText(content, value); else content.textContent = value || "待确认";
    section.append(node("span", label), content);
    return section;
  };

  const comparisonField = (label, currentValue, suggestedValue, currentLabel = "当前档案") => {
    const section = node("section", "", "deliveryline-ai-comparison-field");
    const current = node("div", "", "deliveryline-ai-comparison-value is-current");
    const suggested = node("div", "", "deliveryline-ai-comparison-value is-suggested");
    current.append(node("span", currentLabel), node("strong", currentValue || "未确认"));
    suggested.append(node("span", "AI 建议"), node("strong", suggestedValue || "未提供"));
    section.append(node("h6", label), current, suggested);
    return section;
  };

  const bulletSection = (title, values, className = "") => {
    const section = node("section", "", `deliveryline-collaboration-open-questions ${className}`.trim());
    section.append(node("h6", title));
    const items = node("ul", "", "deliveryline-collaboration-questions");
    values.forEach((value) => items.append(node("li", value)));
    section.append(items);
    return section;
  };

  const updateSummary = (data) => {
    const values = [data.pending_clarification, data.planning, data.change_assessment, data.ended_lines?.length || 0];
    document.querySelectorAll(".deliveryline-workbench-summary strong").forEach((element, index) => { element.textContent = String(values[index] || 0); });
  };

  const lineRow = (line, ended = false) => {
    const button = node("button", "", "deliveryline-requirement-row");
    button.type = "button";
    button.dataset.deliverylineSelect = line.id;
    const copy = node("span");
    copy.append(node("strong", `${line.title || "未命名交付线"} · ${line.id}`), node("small", ended ? "已结束" : line.status === "待澄清" ? "等待整体澄清" : "整体目标已确认"));
    const badge = node("span", ended ? "已结束" : line.status, ended ? "badge badge-success" : "badge badge-timeout");
    button.append(copy, badge);
    return button;
  };

  const renderLists = (active, ended) => {
    const activeList = document.getElementById("deliveryline-active-list");
    const activeEmpty = document.getElementById("deliveryline-active-empty");
    const endedSection = document.getElementById("deliveryline-ended-section");
    const endedList = document.getElementById("deliveryline-ended-list");
    if (activeList) {
      activeList.replaceChildren(...active.map((line) => lineRow(line)));
      activeList.hidden = active.length === 0;
    }
    if (activeEmpty) activeEmpty.hidden = active.length > 0;
    if (endedList) endedList.replaceChildren(...ended.map((line) => lineRow(line, true)));
    if (endedSection) endedSection.hidden = ended.length === 0;
  };

  const render = () => {
    const detail = document.getElementById("deliveryline-detail");
    const aiButton = document.getElementById("deliveryline-ai-clarify");
    const endButton = document.getElementById("deliveryline-end");
    const deleteButton = document.getElementById("deliveryline-delete");
    const selected = lines.find((item) => item.id === selectedId) || lines[0];
    selectedId = selected?.id || null;
    document.querySelectorAll("[data-deliveryline-select]").forEach((button) => button.classList.toggle("is-selected", button.dataset.deliverylineSelect === selectedId));
    if (!detail || !selected) {
      if (detail) detail.hidden = true;
      if (aiButton) aiButton.disabled = true;
      if (endButton) endButton.disabled = true;
      if (deleteButton) deleteButton.disabled = true;
      return;
    }
    detail.hidden = false;
    detail.textContent = "";
    const collaboration = selected.collaboration;
    const busy = collaboration?.status === "requested" || collaboration?.status === "running";
    if (aiButton instanceof HTMLButtonElement) {
      aiButton.disabled = selected.status !== "待澄清" || busy;
      aiButton.textContent = busy ? "AI 澄清处理中" : collaboration?.status === "suggested" ? "继续 AI 整体澄清" : "AI 整体澄清";
    }
    if (endButton instanceof HTMLButtonElement) endButton.disabled = busy || selected.status === "已结束";
    if (deleteButton instanceof HTMLButtonElement) deleteButton.disabled = busy;

    const identifier = node("header", "", "deliveryline-detail-preview-identifier");
    identifier.append(node("p", "整体交付线", "section-kicker"), node("strong", `${selected.title || "未命名交付线"} · ${selected.id}`));
    const source = node("p", "", "deliveryline-detail-preview-description");
    appendLinkedText(source, selected.original_request_content);
    identifier.append(source);

    const overview = node("section", "", "deliveryline-line-overview");
    const header = node("header");
    header.append(node("p", selected.goal_confirmed ? "已确认整体目标" : "整体目标待澄清", "section-kicker"), node("h4", selected.goal_confirmed ? selected.title : "等待 AI 整体澄清"), node("p", selected.goal_confirmed ? `当前为整体目标 V${selected.goal_versions.length}，后续可进入交付项规划。` : "AI 先理解资料并给出候选；维护者确认或修正后才会形成交付项。", "deliveryline-current-stage-goal"));
    const fields = node("div", "", "deliveryline-preview-fields is-source");
    fields.append(field("状态", selected.status), field("资料定位", selected.source_role), field("整体目标", selected.overall_goal || "待通过 AI 整体澄清确认"), field("目标版本", selected.goal_confirmed ? `V${selected.goal_versions.length}` : "尚未建立"));
    const suggestion = collaboration?.status === "suggested" ? collaboration.suggestion : null;
    if (!suggestion) {
      overview.append(header, fields, field("原始资料", selected.original_request_content, true));
      if (collaboration?.sources?.length) overview.append(bulletSection("本轮资料来源", collaboration.sources.map((item) => item.label || "资料来源")));
      if (collaboration?.error) overview.append(node("p", collaboration.error, "deliveryline-collaboration-status is-error"));
    } else {
      const candidate = node("section", "", "deliveryline-ai-comparison");
      const candidateHeader = node("header");
      candidateHeader.append(node("h5", "AI 整体澄清对照"), node("p", "候选内容尚未写入交付线。请对照当前档案确认或修正后，再确认整体目标。"));
      const comparison = node("div", "", "deliveryline-ai-comparison-list");
      comparison.append(
        comparisonField("资料定位", selected.source_role, suggestion.source_role),
        comparisonField("交付线标题", selected.title, suggestion.title),
        comparisonField("整体目标", selected.overall_goal, suggestion.overall_goal),
        comparisonField("已确认事实", list(selected.confirmed_facts).join("\n"), list(suggestion.known_facts).join("\n")),
        comparisonField("范围与边界", selected.scope_boundary, suggestion.scope_boundary),
        comparisonField("仍待确认事项", list(selected.open_questions).join("\n"), list(suggestion.open_questions).join("\n")),
        comparisonField("AI 推断，待维护者判断", "不写入正式档案", list(suggestion.assumptions).join("\n"), "处理规则"),
      );
      candidate.append(candidateHeader, comparison);
      const actions = node("div", "", "deliveryline-preview-actions");
      const confirm = node("button", "确认整体目标", "button-secondary"); confirm.type = "button"; confirm.dataset.deliverylineConfirmGoal = "";
      const rerun = node("button", "带意见再次澄清", "button-link"); rerun.type = "button"; rerun.dataset.deliverylineShowComment = "";
      actions.append(confirm, rerun); candidate.append(actions);
      overview.append(candidate);
    }
    if (collaboration?.status === "suggested" && collaboration?.suggestion && detail.dataset.deliverylineCommentOpen === "true") {
      const comment = node("section", "", "deliveryline-collaboration-comment");
      const input = document.createElement("textarea"); input.rows = 3; input.maxLength = 4000; input.placeholder = "说明需要修正、补充或确认的内容"; input.dataset.deliverylineComment = "";
      const submit = node("button", "再次 AI 整体澄清", "button-secondary"); submit.type = "button"; submit.dataset.deliverylineAiStart = "";
      comment.append(input, submit); overview.append(comment);
    }
    if (selected.goal_confirmed) {
      overview.append(node("p", "当前尚未创建交付项。后续规划确认后，才在交付项列表中生成和推进具体工作。", "deliveryline-current-stage-goal"));
    }
    detail.append(identifier, overview);
    if (busy) { window.clearTimeout(refreshTimer); refreshTimer = window.setTimeout(() => load().catch((error) => window.setWorkspaceToolbarError?.(error.message)), 1600); }
  };

  const load = async () => {
    const data = await request("/api/deliveryline");
    const active = list(data.lines);
    const ended = list(data.ended_lines);
    lines = [...active, ...ended];
    updateSummary(data);
    renderLists(active, ended);
    render();
  };

  window.initializeWorkspaceDeliveryline = () => {
    const root = document.querySelector("[data-deliveryline-enabled]");
    if (!(root instanceof HTMLElement) || root.dataset.deliverylineEnabled !== "true") return;
    const createDialog = document.getElementById("deliveryline-editor");
    const createForm = document.getElementById("deliveryline-editor-form");
    const createFeedback = document.getElementById("deliveryline-editor-feedback");
    const goalDialog = document.getElementById("deliveryline-goal-confirmation");
    const goalForm = document.getElementById("deliveryline-goal-confirmation-form");
    const goalFeedback = document.getElementById("deliveryline-goal-confirmation-feedback");
    const create = document.getElementById("deliveryline-create");
    const ai = document.getElementById("deliveryline-ai-clarify");
    const end = document.getElementById("deliveryline-end");
    const remove = document.getElementById("deliveryline-delete");
    if (!(createDialog instanceof HTMLDialogElement) || !(createForm instanceof HTMLFormElement) || !(goalDialog instanceof HTMLDialogElement) || !(goalForm instanceof HTMLFormElement)) return;
    create?.addEventListener("click", () => { createForm.reset(); createFeedback.hidden = true; createDialog.showModal(); window.requestAnimationFrame(() => document.getElementById("deliveryline-create-description")?.focus()); });
    ai?.addEventListener("click", async () => {
      const selected = lines.find((item) => item.id === selectedId);
      if (!selected) return;
      try {
        await request(`/api/deliveryline/lines/${selected.id}/ai-clarification`, { method: "POST", body: JSON.stringify({}) });
        await load();
      } catch (error) {
        window.setWorkspaceToolbarError?.(error.message);
      }
    });
    remove?.addEventListener("click", async () => {
      const selected = lines.find((item) => item.id === selectedId);
      if (!selected) return;
      try {
        const confirmed = await showConfirmationDialog({
          title: "删除交付线",
          description: "删除会移除原始资料、已确认目标和本地协作记录，无法恢复。",
          details: [{ label: "交付线", value: selected.title || selected.id }],
          confirmLabel: "删除",
          errorMessage: "删除交付线失败。",
          onConfirm: () => request(`/api/deliveryline/lines/${selected.id}`, { method: "DELETE" }),
        });
        if (confirmed) { selectedId = null; await load(); }
      } catch (error) { window.setWorkspaceToolbarError?.(error.message); }
    });
    end?.addEventListener("click", async () => {
      const selected = lines.find((item) => item.id === selectedId);
      if (!selected) return;
      try {
        const confirmed = await showConfirmationDialog({
          title: "结束交付线",
          description: "结束后不会再发起整体澄清或生成新的交付项，已有资料会保留。",
          details: [{ label: "交付线", value: selected.title || selected.id }],
          confirmLabel: "结束交付线",
          errorMessage: "结束交付线失败。",
          onConfirm: () => request(`/api/deliveryline/lines/${selected.id}/end`, { method: "PUT" }),
        });
        if (confirmed) await load();
      } catch (error) { window.setWorkspaceToolbarError?.(error.message); }
    });
    root.addEventListener("click", async (event) => {
      const button = event.target instanceof Element ? event.target.closest("button") : null;
      if (!button) return;
      const id = button.dataset.deliverylineSelect;
      if (id) { selectedId = id; const detail = document.getElementById("deliveryline-detail"); if (detail) delete detail.dataset.deliverylineCommentOpen; render(); return; }
      const selected = lines.find((item) => item.id === selectedId);
      try {
        if (button.matches("[data-deliveryline-show-comment]")) { const detail = document.getElementById("deliveryline-detail"); if (detail) detail.dataset.deliverylineCommentOpen = "true"; render(); return; }
        if (button.matches("[data-deliveryline-ai-start]") && selected) {
          const comment = root.querySelector("[data-deliveryline-comment]")?.value || "";
          await request(`/api/deliveryline/lines/${selected.id}/ai-clarification`, { method: "POST", body: JSON.stringify({ comment }) });
          const detail = document.getElementById("deliveryline-detail"); if (detail) delete detail.dataset.deliverylineCommentOpen;
          await load(); return;
        }
        if (button.matches("[data-deliveryline-confirm-goal]") && selected) {
          const suggestion = selected.collaboration?.suggestion;
          if (!suggestion) throw new Error("当前没有可确认的 AI 整体澄清候选。");
          goalForm.reset(); goalFeedback.hidden = true;
          goalForm.elements.title.value = suggestion.title || "";
          goalForm.elements.source_role.value = suggestion.source_role || "混合资料";
          goalForm.elements.overall_goal.value = suggestion.overall_goal || "";
          goalForm.elements.confirmed_facts.value = list(suggestion.known_facts).join("\n");
          goalForm.elements.scope_boundary.value = suggestion.scope_boundary || "";
          goalForm.elements.open_questions.value = list(suggestion.open_questions).join("\n");
          goalDialog.showModal(); window.requestAnimationFrame(() => goalForm.elements.title.focus()); return;
        }
      } catch (error) { window.setWorkspaceToolbarError?.(error.message); }
    });
    root.addEventListener("click", (event) => { const button = event.target instanceof Element ? event.target.closest("[data-deliveryline-close]") : null; if (button) createDialog.close(); const goalClose = event.target instanceof Element ? event.target.closest("[data-deliveryline-goal-close]") : null; if (goalClose) goalDialog.close(); });
    createForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      try { await request("/api/deliveryline/lines", { method: "POST", body: JSON.stringify({ source: document.getElementById("deliveryline-create-description")?.value || "" }) }); createDialog.close(); await load(); }
      catch (error) { createFeedback.textContent = error.message; createFeedback.hidden = false; }
    });
    goalForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const selected = lines.find((item) => item.id === selectedId);
      if (!selected) return;
      try {
        await request(`/api/deliveryline/lines/${selected.id}/confirm-goal`, { method: "POST", body: JSON.stringify({ title: goalForm.elements.title.value, source_role: goalForm.elements.source_role.value, overall_goal: goalForm.elements.overall_goal.value, confirmed_facts: linesFromText(goalForm.elements.confirmed_facts.value), scope_boundary: goalForm.elements.scope_boundary.value, open_questions: linesFromText(goalForm.elements.open_questions.value) }) });
        goalDialog.close(); await load();
      } catch (error) { goalFeedback.textContent = error.message; goalFeedback.hidden = false; }
    });
    load().catch((error) => window.setWorkspaceToolbarError?.(error.message));
  };
  window.initializeWorkspaceDeliveryline();
})();
