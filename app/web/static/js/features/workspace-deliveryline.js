(() => {
  let records = [];
  let selectedId = null;
  let workflowStages = [];
  const collaborationCommentFields = new Set();
  const collaborationCommentValues = new Map();
  let collaborationRefreshTimer = null;

  const request = async (path, options = {}) => {
    const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
    const body = await response.json().catch(() => null);
    if (!response.ok || body?.success !== true) throw new Error(body?.error?.message || "需求操作失败。");
    return body.data;
  };

  const text = (value) => String(value || "");

  const render = () => {
    const detail = document.getElementById("deliveryline-detail");
    const selected = records.find((item) => item.id === selectedId) || records[0];
    selectedId = selected?.id || null;
    document.querySelectorAll("[data-deliveryline-select]").forEach((button) => button.classList.toggle("is-selected", button.dataset.deliverylineSelect === selectedId));
    if (!selected || !(detail instanceof HTMLElement)) {
      if (detail) detail.hidden = true;
      return;
    }
    detail.hidden = false;
    const isInitialized = selected.is_initialized;
    const canCollaborate = selected.current_stage === "需求提出" && selected.delivery_status !== "已归档";
    const stages = workflowStages.length ? workflowStages : [{ name: selected.current_stage, substages: [] }];
    const fieldDefinitions = [["title", "标题", selected.title], ["background", "背景与问题", selected.background], ["delivery_goal", "交付目标", selected.delivery_goal], ["scope", "本次范围", selected.scope], ["out_of_scope", "不做什么", selected.out_of_scope], ["constraints", "约束与依赖", selected.constraints], ["acceptance_criteria", "验收标准", selected.acceptance_criteria], ["risks_and_open_items", "风险与待确认事项", selected.risks_and_open_items]];
    detail.textContent = "";
    detail.classList.toggle("is-initialized", isInitialized);
    const stagesList = document.createElement("ol"); stagesList.className = "deliveryline-preview-stages";
    const node = (tag, value = "", className = "") => { const element = document.createElement(tag); element.textContent = value; element.className = className; return element; };
    const requirementId = node("header", "", "deliveryline-detail-preview-identifier");
    requirementId.append(node("strong", `${selected.title || "未命名需求"} · ${selected.id}`));
    const progress = node("section", "", "deliveryline-detail-preview-progress");
    const stageTrack = node("section", "", "deliveryline-detail-preview-progress-track");
    const appendLinkedText = (element, value) => {
      const source = text(value || "待补充");
      const pattern = /https?:\/\/[^\s<>"']+/g;
      let cursor = 0;
      source.replace(pattern, (match, index) => {
        const url = match.replace(/[),.;!?]+$/, "");
        element.append(document.createTextNode(source.slice(cursor, index)));
        if (!url) {
          element.append(document.createTextNode(match));
        } else {
          const link = document.createElement("a");
          link.href = url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = url;
          element.append(link, document.createTextNode(match.slice(url.length)));
        }
        cursor = index + match.length;
        return match;
      });
      element.append(document.createTextNode(source.slice(cursor)));
    };
    if (selected.original_request_content) { const originalDescription = node("p", "", "deliveryline-detail-preview-description is-collapsed"); appendLinkedText(originalDescription, selected.original_request_content); requirementId.append(originalDescription); if (selected.original_request_content.length > 240) { const toggle = node("button", "展开", "button-link deliveryline-description-toggle"); toggle.type = "button"; toggle.dataset.deliverylineToggleDescription = ""; toggle.setAttribute("aria-expanded", "false"); requirementId.append(toggle); } }
    const stageIndex = Math.max(0, stages.findIndex((stage) => stage.name === selected.current_stage));
    const currentStage = stages[stageIndex] || { name: selected.current_stage, substages: [] };
    const stageStatus = (index) => index < stageIndex ? ["is-complete", "✓", "已完成"] : index === stageIndex ? ["is-current", String(index + 1), "进行中"] : ["is-pending", String(index + 1), "未开始"];
    stages.forEach((stage, index) => { const item = node("li"); const [className, symbol, status] = stageStatus(index); item.className = className; item.append(node("span", symbol, "deliveryline-stage-node"), node("strong", stage.name), node("small", status)); stagesList.append(item); });
    const mobileSummary = node("div", "", "deliveryline-mobile-workflow-summary"); const workflowToggle = node("button", "查看全部流程", "button-link"); workflowToggle.type = "button"; workflowToggle.dataset.deliverylineToggleWorkflow = ""; workflowToggle.setAttribute("aria-expanded", "false"); mobileSummary.append(node("span", `${currentStage.name} · 阶段 ${stageIndex + 1} / ${stages.length}`), workflowToggle); stageTrack.append(mobileSummary, stagesList);
    const substage = node("section", "", "deliveryline-preview-substages");
    const substageList = node("ol");
    const intakeComplete = Boolean(selected.original_request_content) || stageIndex > 0;
    const archiveCompletionCurrent = selected.current_stage === "需求提出" && selected.delivery_status !== "已归档";
    const currentSubstageIndex = currentStage.name === "需求提出" ? (archiveCompletionCurrent ? 1 : 0) : -1;
    const currentSubstage = currentStage.substages[currentSubstageIndex] || null;
    if (currentStage.substages.length) {
      currentStage.substages.forEach((substageDefinition, index) => { const complete = index === 0 ? intakeComplete : index < currentSubstageIndex; const active = index === currentSubstageIndex; const status = complete ? ["is-complete", "✓", "已完成"] : active ? ["is-current", String(index + 1), "进行中"] : ["is-pending", "", "未开始"]; const item = node("li"); item.className = status[0]; item.append(node("span", status[1], "deliveryline-substage-node"), node("strong", substageDefinition.name), node("small", status[2])); substageList.append(item); });
      substage.append(substageList);
    }
    progress.append(node("p", "推进进度", "section-kicker"), stageTrack);
    if (currentStage.substages.length) {
      progress.append(substage);
    }
    const collaboration = selected.collaboration;
    const fieldGrid = (fields, className = "") => {
      const grid = node("div", "", `deliveryline-preview-fields ${className}`.trim());
      fields.forEach(([key, name, value]) => {
        const field = node("section", "", "deliveryline-preview-field");
        const content = node("strong");
        appendLinkedText(content, value);
        field.append(node("span", name), content);
        const suggestion = collaboration?.status === "suggested" ? collaboration.suggestion?.fields?.[key] : null;
        if (suggestion && suggestion.status !== "accepted") {
          const preview = node("div", "", "deliveryline-field-suggestion");
          const suggestionCopy = node("p");
          suggestionCopy.append(node("span", "AI 建议："), node("strong", suggestion.value));
          preview.append(suggestionCopy);
          const commentId = `${selected.id}:${key}`;
          const commentOpen = collaborationCommentFields.has(commentId) && suggestion.status === "suggested";
          if (suggestion.status === "suggested") {
            if (!commentOpen) {
              const actions = node("div", "", "deliveryline-field-suggestion-actions");
              const accept = node("button", "采纳", "button-secondary"); accept.type = "button"; accept.dataset.deliverylineAiFieldAccept = key;
              const comment = node("button", "建议", "button-link"); comment.type = "button"; comment.dataset.deliverylineAiFieldComment = key;
              actions.append(accept, comment); preview.append(actions);
            }
          } else if (suggestion.status === "applying") preview.append(node("p", "正在采纳建议。", "deliveryline-field-suggestion-status"));
          else if (suggestion.status === "commented") preview.append(node("p", `已记录建议：${suggestion.comment}`, "deliveryline-field-suggestion-status"));
          if (commentOpen) {
            const form = node("div", "", "deliveryline-collaboration-comment");
            const input = document.createElement("textarea"); input.rows = 2; input.maxLength = 4000; input.placeholder = `输入对“${name}”的建议`; input.dataset.deliverylineAiFieldCommentInput = key; input.value = collaborationCommentValues.get(commentId) || "";
            form.append(input); preview.append(form);
          }
          field.append(preview);
        }
        grid.append(field);
      });
      return grid;
    };
    const current = node("section", "", "deliveryline-detail-preview-current"); const currentHeader = node("header"); const currentObjective = currentSubstage?.objective || "详细阶段契约待制定。"; currentHeader.append(node("p", "当前阶段", "section-kicker"), node("h4", currentSubstage ? `${currentStage.name} · ${currentSubstage.name}` : currentStage.name), node("p", `${currentSubstage ? "当前目标" : "阶段说明"}：${currentObjective}`, "deliveryline-current-stage-goal"));
    const content = (() => { const groups = node("div", "", "deliveryline-preview-field-groups"); const group = node("section", "", "deliveryline-preview-field-group is-source"); group.append(node("h5", selected.current_stage === "需求提出" ? "待补充内容" : "需求档案"), fieldGrid(fieldDefinitions)); if (collaboration?.sources?.length) { const sources = node("ul", "", "deliveryline-collaboration-questions"); collaboration.sources.forEach((source) => sources.append(node("li", source.label || "资料来源"))); const sourceGroup = node("section", "", "deliveryline-collaboration-open-questions"); sourceGroup.append(node("h6", "本轮资料来源"), sources); group.append(sourceGroup); } if (collaboration?.error) group.append(node("p", collaboration.error, "deliveryline-collaboration-status is-error")); if (collaboration?.status === "suggested" && collaboration.suggestion?.open_questions?.length) { const questions = node("ul", "", "deliveryline-collaboration-questions"); collaboration.suggestion.open_questions.forEach((question) => questions.append(node("li", question))); const openQuestions = node("section", "", "deliveryline-collaboration-open-questions"); openQuestions.append(node("h6", "仍待确认"), questions); group.append(openQuestions); } groups.append(group); return groups; })();
    if (selected.delivery_status !== "已归档") { const actions = node("div", "", "deliveryline-preview-actions"); const collaborationRunning = collaboration?.status === "requested" || collaboration?.status === "running"; const unresolvedSuggestions = collaboration?.status === "suggested" && Object.values(collaboration?.suggestion?.fields || {}).some((item) => item.status === "suggested" || item.status === "applying"); const hasRemainingSuggestions = collaboration?.status === "suggested" && collaboration.can_continue; const noFieldsToRevise = collaboration?.status === "suggested" && !collaboration.can_continue; if (canCollaborate) { if (collaborationRunning) { const processing = node("button", "AI 协作处理中", "button-secondary"); processing.type = "button"; processing.disabled = true; actions.append(processing); } else { const collaborate = node("button", hasRemainingSuggestions ? "继续 AI 协作" : "AI 协作", "button-secondary"); collaborate.type = "button"; collaborate.dataset.deliverylineAiStart = ""; collaborate.disabled = Boolean(noFieldsToRevise); if (noFieldsToRevise) collaborate.title = "所有字段建议均已采纳，无需再次生成。"; actions.append(collaborate); } const confirmStage = node("button", "阶段确认", "button-secondary"); confirmStage.type = "button"; confirmStage.dataset.deliverylineSubmitReview = ""; confirmStage.disabled = selected.current_stage !== "需求提出" || selected.readiness_missing.length > 0 || Boolean(unresolvedSuggestions) || collaborationRunning; if (confirmStage.disabled) confirmStage.title = collaborationRunning ? "请等待当前 AI 协作完成。" : unresolvedSuggestions ? "请先逐项处理当前 AI 建议。" : "请先补全当前阶段所需内容。"; actions.append(confirmStage); } if (!isInitialized) { const edit = node("button", "编辑档案", "button-link"); edit.type = "button"; edit.dataset.deliverylineEdit = ""; actions.append(edit); } const archive = node("button", "归档", "button-danger"); archive.type = "button"; archive.dataset.deliverylineArchive = ""; const remove = node("button", "删除", "button-danger"); remove.type = "button"; remove.dataset.deliverylineDelete = ""; actions.append(archive, remove); current.append(currentHeader, content, actions); } else current.append(currentHeader, content);
    if (selected.current_stage === "需求提出") { detail.append(requirementId, progress, current); if (collaboration?.status === "requested" || collaboration?.status === "running") { window.clearTimeout(collaborationRefreshTimer); collaborationRefreshTimer = window.setTimeout(() => load().catch((error) => window.setWorkspaceToolbarError?.(error.message)), 1600); } return; }
    const layout = node("div", "", "deliveryline-detail-preview-layout");
    const context = node("aside", "", "deliveryline-detail-preview-context"); const readiness = node("section", "", "deliveryline-preview-readiness"); const readinessHeader = node("header"); readinessHeader.append(node("h4", "评审准备度"), node("p", "进入评审前仍需确认的事项。")); const readinessList = node("ul"); (selected.readiness_missing.length ? selected.readiness_missing : ["需求档案已具备提交评审条件"]).forEach((item) => readinessList.append(node("li", item, selected.readiness_missing.length ? "is-pending" : "is-ready"))); readiness.append(readinessHeader, readinessList);
    const activity = node("section", "", "deliveryline-detail-preview-activity"); const activityHeader = node("header"); activityHeader.append(node("h4", "活动记录"), node("p", "需求档案的最近更新。")); activity.append(activityHeader); selected.activity.slice().reverse().slice(0, 4).forEach((item) => { const row = node("p"); row.append(node("strong", item.action), node("span", item.summary)); activity.append(row); }); context.append(readiness, activity); layout.append(current, context);
    detail.append(requirementId, progress, layout);
    if (collaboration?.status === "requested" || collaboration?.status === "running") { window.clearTimeout(collaborationRefreshTimer); collaborationRefreshTimer = window.setTimeout(() => load().catch((error) => window.setWorkspaceToolbarError?.(error.message)), 1600); }
  };

  const load = async () => { const data = await request("/api/deliveryline"); records = [...data.requirements, ...data.archived_requirements]; workflowStages = Array.isArray(data.workflow_stages) ? data.workflow_stages : []; render(); };

  window.initializeWorkspaceDeliveryline = () => {
    const root = document.querySelector("[data-deliveryline-enabled]");
    if (!(root instanceof HTMLElement) || root.dataset.deliverylineEnabled !== "true") return;
    const dialog = document.getElementById("deliveryline-editor");
    const form = document.getElementById("deliveryline-editor-form");
    const feedback = document.getElementById("deliveryline-editor-feedback");
    const create = document.getElementById("deliveryline-create");
    const description = document.getElementById("deliveryline-editor-description");
    if (!(dialog instanceof HTMLDialogElement) || !(form instanceof HTMLFormElement)) return;
    let editingId = null;
    const open = (item = null) => {
      editingId = item?.id || null;
      form.reset();
      form.querySelector("[data-deliveryline-create-field]").hidden = Boolean(item);
      form.querySelector("[data-deliveryline-edit-fields]").hidden = !item;
      document.getElementById("deliveryline-editor-title").textContent = item ? "编辑需求档案" : "新建需求";
      document.getElementById("deliveryline-editor-submit").textContent = item ? "保存" : "创建";
      if (description) description.textContent = item
        ? "补充需求背景、目标、范围与验收信息，形成可进入评审的需求档案。"
        : "将首次提出的原始需求内容直接入库；可输入一句话、链接或混合内容。";
      if (item) Object.entries(item).forEach(([key, value]) => { const field = form.elements.namedItem(key); if (field && "value" in field) field.value = value || ""; });
      const initialFocus = item ? form.elements.namedItem("title") : document.getElementById("deliveryline-create-description");
      feedback.hidden = true; dialog.showModal();
      window.requestAnimationFrame(() => initialFocus?.focus());
    };
    create?.addEventListener("click", () => open());
    root.addEventListener("click", async (event) => {
      const button = event.target instanceof Element ? event.target.closest("button") : null;
      if (!button) return;
      if (button.matches("[data-deliveryline-toggle-workflow]")) { const track = button.closest(".deliveryline-detail-preview-progress-track"); const expanded = track?.classList.toggle("is-expanded"); button.textContent = expanded ? "收起完整流程" : "查看全部流程"; button.setAttribute("aria-expanded", String(Boolean(expanded))); return; }
      if (button.matches("[data-deliveryline-toggle-description]")) { const description = button.previousElementSibling; const expanded = description?.classList.toggle("is-collapsed") === false; button.textContent = expanded ? "收起" : "展开"; button.setAttribute("aria-expanded", String(Boolean(expanded))); return; }
      const id = button.dataset.deliverylineSelect;
      if (id) { selectedId = id; collaborationCommentFields.clear(); collaborationCommentValues.clear(); render(); return; }
      const selected = records.find((item) => item.id === selectedId);
      try {
        if (button.matches("[data-deliveryline-ai-start]") && selected) {
          const comments = {};
          for (const [commentId, value] of collaborationCommentValues.entries()) {
            if (!commentId.startsWith(`${selected.id}:`)) continue;
            const field = commentId.slice(selected.id.length + 1);
            if (!value.trim()) throw new Error("请填写已选择字段的建议后再进行 AI 协作。");
            comments[field] = value.trim();
          }
          await request(`/api/deliveryline/requirements/${selected.id}/ai-collaboration`, { method: "POST", body: JSON.stringify({ comments }) });
          collaborationCommentFields.clear(); collaborationCommentValues.clear();
          await load();
          return;
        }
        if (button.matches("[data-deliveryline-ai-field-accept]") && selected) {
          const field = button.dataset.deliverylineAiFieldAccept;
          await request(`/api/deliveryline/requirements/${selected.id}/ai-collaboration/fields/${encodeURIComponent(field)}/accept`, { method: "POST" });
          collaborationCommentFields.delete(`${selected.id}:${field}`); collaborationCommentValues.delete(`${selected.id}:${field}`);
          await load();
          return;
        }
        if (button.matches("[data-deliveryline-ai-field-comment]") && selected) {
          const field = button.dataset.deliverylineAiFieldComment;
          const commentId = `${selected.id}:${field}`;
          collaborationCommentFields.add(commentId);
          if (!collaborationCommentValues.has(commentId)) collaborationCommentValues.set(commentId, "");
          render();
          window.requestAnimationFrame(() => root.querySelector(`[data-deliveryline-ai-field-comment-input="${field}"]`)?.focus());
          return;
        }
        if (button.matches("[data-deliveryline-edit]") && selected) open(selected);
        if (button.matches("[data-deliveryline-submit-review]") && selected) {
          const confirmed = await window.showConfirmationDialog?.({
            title: "确认当前阶段",
            description: "确认后，需求将从需求提出进入需求评审。当前阶段的字段和已采纳内容会保留；未处理的 AI 建议不能进入下一阶段。",
            details: [{ label: "需求", value: selected.title || `未命名需求 · ${selected.id}` }],
            confirmLabel: "确认阶段",
            errorMessage: "阶段确认失败。",
            onConfirm: async () => request(`/api/deliveryline/requirements/${selected.id}/submit-review`, { method: "POST" }),
          });
          if (confirmed) window.location.reload();
          return;
        }
        if (button.matches("[data-deliveryline-archive]") && selected) {
          const confirmed = await window.showConfirmationDialog?.({
            title: "归档需求",
            description: "归档后，该需求将从当前队列移除。业务档案仍会保留，后续不能在本阶段页面继续推进。",
            details: [{ label: "需求", value: selected.title || `未命名需求 · ${selected.id}` }],
            confirmLabel: "归档",
            errorMessage: "需求归档失败。",
            onConfirm: async () => request(`/api/deliveryline/requirements/${selected.id}/archive`, { method: "PUT" }),
          });
          if (confirmed) window.location.reload();
        }
        if (button.matches("[data-deliveryline-delete]") && selected) {
          const confirmed = await window.showConfirmationDialog?.({
            title: "删除需求",
            description: "删除后会永久移除此需求档案及其活动记录，Deliveryline 无法恢复。",
            details: [{ label: "需求", value: selected.title || `未命名需求 · ${selected.id}` }],
            confirmLabel: "删除",
            errorMessage: "需求删除失败。",
            onConfirm: async () => request(`/api/deliveryline/requirements/${selected.id}`, { method: "DELETE" }),
          });
          if (confirmed) window.location.reload();
        }
      } catch (error) { window.showWorkspaceToolbarFeedback?.(error.message, "warning"); }
    });
    root.addEventListener("input", (event) => {
      const input = event.target instanceof HTMLTextAreaElement ? event.target : null;
      if (!input?.dataset.deliverylineAiFieldCommentInput || !selectedId) return;
      collaborationCommentValues.set(`${selectedId}:${input.dataset.deliverylineAiFieldCommentInput}`, input.value);
    });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      try {
        if (editingId) {
          const payload = Object.fromEntries(new FormData(form));
          await request(`/api/deliveryline/requirements/${editingId}`, { method: "PUT", body: JSON.stringify(payload) });
        } else await request("/api/deliveryline/requirements", { method: "POST", body: JSON.stringify({ description: document.getElementById("deliveryline-create-description").value }) });
        window.location.reload();
      } catch (error) { feedback.textContent = error.message; feedback.hidden = false; }
    });
    dialog.querySelectorAll("[data-deliveryline-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
    load().catch((error) => window.setWorkspaceToolbarError?.(error.message));
  };
  window.initializeWorkspaceDeliveryline();
})();
