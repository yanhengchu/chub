"use strict";

const SESSION_TOKEN_KEY = "hub.sessionToken";
const LOCAL_TOKEN_KEY = "hub.savedToken";
const token = sessionStorage.getItem(SESSION_TOKEN_KEY) || localStorage.getItem(LOCAL_TOKEN_KEY) || "";
const FILTER_KEY = "hub.projectDocumentFilter";
const PROJECT_DOCS_REFRESH_KEY = "hub.projectDocsRefreshOnReturn";
sessionStorage.setItem(PROJECT_DOCS_REFRESH_KEY, "1");
const list = document.querySelector("#document-list");
const message = document.querySelector("#document-list-message");
const filters = document.querySelectorAll("[data-document-filter]");
let activeFilter = sessionStorage.getItem(FILTER_KEY) || "all";

function showMessage(text, kind = "") {
  message.textContent = text;
  message.className = "message";
  if (kind) {
    message.classList.add(`message-${kind}`);
  }
}

function applyFilter(filter) {
  activeFilter = filter;
  sessionStorage.setItem(FILTER_KEY, filter);
  filters.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.documentFilter === filter);
  });
  list.querySelectorAll(".design-document-item").forEach((card) => {
    const archived = card.dataset.archived === "true";
    card.hidden = filter === "current" ? archived : filter === "archived" ? !archived : false;
  });
}

async function updateArchiveState(button) {
  if (!token) {
    showMessage("请先返回首页连接节点，再管理文档归档状态。", "error");
    return;
  }
  const documentId = button.dataset.documentId;
  const archived = button.dataset.archived === "true";
  const action = archived ? "恢复" : "归档";
  if (!window.confirm(`确定${action}这份文档吗？`)) {
    return;
  }

  button.disabled = true;
  try {
    const response = await fetch(
      `/api/project-docs/${encodeURIComponent(documentId)}/archive`,
      {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ archived: !archived }),
      },
    );
    const payload = await response.json();
    if (!response.ok || payload.success !== true) {
      throw new Error(payload?.error?.message || `${action}失败。`);
    }
    const card = button.closest(".design-document-item");
    const badge = card.querySelector(".design-document-meta .badge");
    card.dataset.archived = String(payload.data.archived);
    button.dataset.archived = String(payload.data.archived);
    button.textContent = payload.data.archived ? "恢复" : "归档";
    badge.textContent = payload.data.archived ? "已归档" : payload.data.status;
    badge.className = `badge badge-${payload.data.archived ? "muted" : "success"}`;
    showMessage(`${action}成功。`, "success");
    applyFilter(activeFilter);
    button.disabled = false;
  } catch (error) {
    showMessage(error.message || `${action}失败。`, "error");
    button.disabled = false;
  }
}

filters.forEach((button) => {
  button.addEventListener("click", () => applyFilter(button.dataset.documentFilter));
});

list.addEventListener("click", (event) => {
  const button = event.target.closest(".document-archive-action");
  if (button) {
    updateArchiveState(button);
  }
});

applyFilter(activeFilter);
