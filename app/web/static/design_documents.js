"use strict";

const FILTER_KEY = "hub.projectDocumentFilter";
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
  const documentId = button.dataset.documentId;
  const archived = button.dataset.archived === "true";
  const action = archived ? "恢复显示" : "隐藏";
  const card = button.closest(".design-document-item");
  const title = card?.querySelector(".design-document-copy strong")?.textContent.trim()
    || "这份文档";
  if (!documentId) {
    return;
  }
  await showConfirmationDialog({
    title: `${action}项目资料`,
    description: archived
      ? `恢复显示“${title}”后，该资料会重新显示在首页。`
      : `隐藏“${title}”后，该资料不再显示在首页，但仍保留在“已隐藏”列表中。此操作不会移动或冻结仓库文件。`,
    confirmLabel: `确认${action}`,
    pendingLabel: `${action}中…`,
    tone: archived ? "secondary" : "danger",
    errorMessage: `${action}失败。`,
    onConfirm: async () => {
      button.disabled = true;
      try {
        const response = await fetch(
          `/api/project-docs/${encodeURIComponent(documentId)}/archive`,
          {
            method: "PUT",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ archived: !archived }),
          },
        );
        const payload = await response.json();
        if (!response.ok || payload.success !== true) {
          throw new Error(payload?.error?.message || `${action}失败。`);
        }
        const badge = card.querySelector(".design-document-meta .badge");
        card.dataset.archived = String(payload.data.archived);
        button.dataset.archived = String(payload.data.archived);
        button.textContent = payload.data.archived ? "恢复显示" : "隐藏";
        badge.textContent = payload.data.archived ? "已隐藏" : payload.data.status;
        badge.className = `badge badge-${payload.data.archived ? "muted" : "success"}`;
        showMessage(`${action}成功。`, "success");
        applyFilter(activeFilter);
      } finally {
        button.disabled = false;
      }
    },
  });
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
