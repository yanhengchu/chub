"use strict";

function projectDocumentDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function renderProjectDocuments(data) {
  elements.weeklyReportsList.replaceChildren();
  data.weekly_reports.forEach((item) => {
    const card = document.createElement("article");
    const main = document.createElement(item.available ? "a" : "div");
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    const summary = document.createElement("span");
    const footer = document.createElement("div");
    const meta = document.createElement("span");
    const badge = document.createElement("span");
    card.className = item.available
      ? "design-document-item"
      : "design-document-item design-document-item-unavailable";
    main.className = "design-document-main";
    if (item.available) {
      main.href = `/weekly-reports/${encodeURIComponent(item.period)}/${encodeURIComponent(item.report_type)}`;
    }
    copy.className = "design-document-copy";
    title.textContent = item.report_type === "focus" ? "本周重点事项" : "本周周报";
    summary.textContent = `${item.period} · ${item.summary}`;
    footer.className = "design-document-footer";
    meta.className = "design-document-meta";
    badge.className = item.available ? "badge badge-success" : "badge badge-muted";
    badge.textContent = item.status;
    copy.append(title, summary);
    main.append(copy);
    meta.append(badge);
    if (item.updated_at) {
      const time = document.createElement("time");
      time.dateTime = item.updated_at;
      time.textContent = projectDocumentDate(item.updated_at);
      meta.append(time);
    }
    footer.append(meta);
    card.append(main, footer);
    elements.weeklyReportsList.append(card);
  });
  if (!data.weekly_reports.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "暂无周报工作区。";
    elements.weeklyReportsList.append(empty);
  }
  elements.projectDocsList.replaceChildren();
  data.documents.forEach((item) => {
    const card = document.createElement("article");
    const link = document.createElement("a");
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    const summary = document.createElement("span");
    const meta = document.createElement("span");
    const badge = document.createElement("span");
    const time = document.createElement("time");
    const footer = document.createElement("div");
    const archive = document.createElement("button");
    card.className = "design-document-item";
    link.className = "design-document-main";
    link.href = `/project-docs/${encodeURIComponent(item.id)}`;
    copy.className = "design-document-copy";
    title.textContent = item.title;
    summary.textContent = item.summary;
    meta.className = "design-document-meta";
    badge.className = "badge badge-success";
    badge.textContent = item.status;
    time.dateTime = item.updated_at;
    time.textContent = projectDocumentDate(item.updated_at);
    archive.className = "button-secondary document-archive-action";
    archive.type = "button";
    archive.dataset.documentId = item.id;
    archive.textContent = "归档";
    copy.append(title, summary);
    meta.append(badge, time);
    link.append(copy);
    footer.className = "design-document-footer";
    footer.append(meta, archive);
    card.append(link, footer);
    elements.projectDocsList.append(card);
  });
  if (!data.documents.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "暂无设计文档。";
    elements.projectDocsList.append(empty);
  }
}

async function archiveProjectDocument(button) {
  const documentId = button.dataset.documentId;
  if (!documentId || !window.confirm("归档后，该文档将从首页移除。确定继续吗？")) {
    return;
  }
  button.disabled = true;
  try {
    await apiFetch(`/api/project-docs/${encodeURIComponent(documentId)}/archive`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived: true }),
    });
    setMessage(elements.projectDocsMessage, "文档已归档。", "success");
    await loadProjectDocuments({ clearMessage: false });
  } catch (error) {
    if (!handleAccessError(error)) {
      setMessage(elements.projectDocsMessage, error.message || "文档归档失败。", "error");
    }
    button.disabled = false;
  }
}

async function loadProjectDocuments({ clearMessage = true } = {}) {
  const requestVersion = accessVersion;
  elements.refreshProjectDocs.disabled = true;
  if (clearMessage) {
    setMessage(elements.projectDocsMessage, "");
  }
  try {
    const data = await apiFetch("/api/project-docs");
    if (requestVersion !== accessVersion) {
      return;
    }
    renderProjectDocuments(data);
  } catch (error) {
    if (requestVersion !== accessVersion) {
      return;
    }
    if (!handleAccessError(error)) {
      setMessage(elements.projectDocsMessage, error.message || "文档列表读取失败。", "error");
    }
  } finally {
    elements.refreshProjectDocs.disabled = false;
  }
}

elements.projectDocsList.addEventListener("click", (event) => {
  const button = event.target.closest(".document-archive-action");
  if (button) {
    archiveProjectDocument(button);
  }
});
