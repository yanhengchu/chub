(() => {
  let records = [];
  let selectedId = null;

  const request = async (path, options = {}) => {
    const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
    const body = await response.json().catch(() => null);
    if (!response.ok || body?.success !== true) throw new Error(body?.error?.message || "需求操作失败。");
    return body.data;
  };

  const text = (value) => String(value || "");

  const render = () => {
    const detail = document.getElementById("deliveryline-detail");
    const title = document.getElementById("deliveryline-preview-detail-title");
    const selected = records.find((item) => item.id === selectedId) || records[0];
    selectedId = selected?.id || null;
    document.querySelectorAll("[data-deliveryline-select]").forEach((button) => button.classList.toggle("is-selected", button.dataset.deliverylineSelect === selectedId));
    if (!selected || !(detail instanceof HTMLElement)) {
      if (detail) detail.hidden = true;
      if (title) title.textContent = "请选择或新建需求";
      return;
    }
    detail.hidden = false;
    const isInitialized = selected.is_initialized;
    const displayTitle = selected.title || `未命名需求 · ${selected.id}`;
    if (title) { title.textContent = displayTitle; title.title = displayTitle; }
    const stages = ["需求提出", "需求评审", "方案设计", "开发实现", "自动化测试", "测试验收"];
    const fieldGroups = [["需求概要", [["背景与问题", selected.background], ["交付目标", selected.delivery_goal], ["本次范围", selected.scope], ["不做什么", selected.out_of_scope]]], ["约束与验收", [["约束与依赖", selected.constraints], ["验收标准", selected.acceptance_criteria], ["风险与待确认事项", selected.risks_and_open_items]]], ["来源记录", [["原始需求内容", selected.original_request_content || "历史需求未保留"]]]];
    const initialFieldGroups = [["仍需补充的需求内容", [["标题", selected.title], ["背景与问题", selected.background], ["交付目标", selected.delivery_goal], ["本次范围", selected.scope], ["不做什么", selected.out_of_scope], ["约束与依赖", selected.constraints], ["验收标准", selected.acceptance_criteria], ["风险与待确认事项", selected.risks_and_open_items]]], ["来源记录", [["原始需求内容", selected.original_request_content || "历史需求未保留"]]]];
    detail.textContent = "";
    detail.classList.toggle("is-initialized", isInitialized);
    const stagesList = document.createElement("ol"); stagesList.className = "deliveryline-preview-stages";
    const node = (tag, value = "", className = "") => { const element = document.createElement(tag); element.textContent = value; element.className = className; return element; };
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
    stages.forEach((name, index) => { const item = node("li"); if (name === selected.current_stage) item.className = "is-current"; item.append(node("span", String(index + 1)), node("strong", name)); stagesList.append(item); });
    const fieldGrid = (fields, className = "") => { const grid = node("div", "", `deliveryline-preview-fields ${className}`.trim()); fields.forEach(([name, value]) => { const field = node("section"); const content = node("strong"); appendLinkedText(content, value); field.append(node("span", name), content); grid.append(field); }); return grid; };
    const current = node("section", "", "deliveryline-detail-preview-current"); const currentHeader = node("header"); currentHeader.append(node("p", selected.delivery_status === "已归档" ? "已归档需求" : (isInitialized ? "需求提出 · 已入库" : "当前工作"), "section-kicker"), node("h4", isInitialized ? "当前工作目标" : selected.current_stage), node("p", isInitialized ? "明确需求目标、范围、约束和验收预期，形成可编辑的需求档案。" : selected.next_action));
    const content = (() => { const groups = node("div", "", "deliveryline-preview-field-groups"); const sourceGroups = isInitialized ? initialFieldGroups : fieldGroups; sourceGroups.forEach(([heading, fields], index) => { const group = node("section", "", `deliveryline-preview-field-group${index === sourceGroups.length - 1 ? " is-source" : ""}`); group.append(node("h5", heading), fieldGrid(fields)); groups.append(group); }); return groups; })();
    if (selected.delivery_status !== "已归档") { const actions = node("div", "", "deliveryline-preview-actions"); (isInitialized ? [["AI 协助", "button-secondary", null, true], ["归档", "button-danger", "deliverylineArchive"], ["删除", "button-danger", "deliverylineDelete"]] : [["编辑档案", "button-link", "deliverylineEdit"], ["标记为可评审", "button-secondary", "deliverylineSubmitReview"], ["归档", "button-danger", "deliverylineArchive"], ["删除", "button-danger", "deliverylineDelete"]]).forEach(([label, className, key, unavailable]) => { const button = node("button", label, className); button.type = "button"; if (key) button.dataset[key] = ""; if (unavailable) { button.disabled = true; button.title = "AI 初步整理将在后续阶段接入。"; } if (key === "deliverylineSubmitReview" && selected.current_stage !== "需求提出") button.disabled = true; actions.append(button); }); current.append(currentHeader, content, actions); } else current.append(currentHeader, content);
    if (isInitialized) { detail.append(current); return; }
    const layout = node("div", "", "deliveryline-detail-preview-layout");
    const context = node("aside", "", "deliveryline-detail-preview-context"); const readiness = node("section", "", "deliveryline-preview-readiness"); const readinessHeader = node("header"); readinessHeader.append(node("h4", "评审准备度"), node("p", "进入评审前仍需确认的事项。")); const readinessList = node("ul"); (selected.readiness_missing.length ? selected.readiness_missing : ["需求档案已具备提交评审条件"]).forEach((item) => readinessList.append(node("li", item, selected.readiness_missing.length ? "is-pending" : "is-ready"))); readiness.append(readinessHeader, readinessList);
    const activity = node("section", "", "deliveryline-detail-preview-activity"); const activityHeader = node("header"); activityHeader.append(node("h4", "活动记录"), node("p", "需求档案的最近更新。")); activity.append(activityHeader); selected.activity.slice().reverse().slice(0, 4).forEach((item) => { const row = node("p"); row.append(node("strong", item.action), node("span", item.summary)); activity.append(row); }); context.append(readiness, activity); layout.append(current, context);
    detail.append(stagesList, layout);
  };

  const load = async () => { const data = await request("/api/deliveryline"); records = [...data.requirements, ...data.archived_requirements]; render(); };

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
      const id = button.dataset.deliverylineSelect;
      if (id) { selectedId = id; render(); return; }
      const selected = records.find((item) => item.id === selectedId);
      try {
        if (button.matches("[data-deliveryline-edit]") && selected) open(selected);
        if (button.matches("[data-deliveryline-submit-review]") && selected) {
          await request(`/api/deliveryline/requirements/${selected.id}/submit-review`, { method: "POST" });
          window.location.reload();
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
