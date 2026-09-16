"use strict";

const CATEGORY_FILTER_KEY = "hub.projectDocumentCategoryFilter";
const STATUS_FILTER_KEY = "hub.projectDocumentStatusFilter";
const DISPLAY_FILTER_KEY = "hub.projectDocumentDisplayFilter";
const list = document.querySelector("#document-list");
const message = document.querySelector("#document-list-message");
const filterEmpty = document.querySelector("#document-filter-empty");
const categoryFilters = document.querySelectorAll("[data-document-category-filter]");
const statusFilters = document.querySelectorAll("[data-document-status-filter]");
const displayFilters = document.querySelectorAll("[data-document-display-filter]");
const coreDocumentStatuses = new Set(
  Array.from(statusFilters, (button) => button.dataset.documentStatusFilter)
    .filter((status) => !["all", "other"].includes(status)),
);
let activeCategoryFilter = sessionStorage.getItem(CATEGORY_FILTER_KEY) || "all";
let activeStatusFilter = sessionStorage.getItem(STATUS_FILTER_KEY) || "all";
let activeDisplayFilter = sessionStorage.getItem(DISPLAY_FILTER_KEY) || "all";

function showMessage(text, kind = "") {
  message.textContent = text;
  message.className = "message";
  if (kind) {
    message.classList.add(`message-${kind}`);
  }
}

function applyFilters() {
  categoryFilters.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.documentCategoryFilter === activeCategoryFilter);
  });
  statusFilters.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.documentStatusFilter === activeStatusFilter);
  });
  displayFilters.forEach((button) => {
    button.classList.toggle("is-active", button.dataset.documentDisplayFilter === activeDisplayFilter);
  });
  const cards = list.querySelectorAll(".design-document-item");
  let visibleCount = 0;
  cards.forEach((card) => {
    const archived = card.dataset.archived === "true";
    const categoryMatches = activeCategoryFilter === "all" || card.dataset.category === activeCategoryFilter;
    const statusMatches = activeStatusFilter === "all"
      ? true
      : activeStatusFilter === "hidden"
        ? archived
        : activeStatusFilter === "other"
          ? !coreDocumentStatuses.has(card.dataset.status)
          : card.dataset.status === activeStatusFilter;
    const displayMatches = activeDisplayFilter === "all"
      ? true
      : activeDisplayFilter === "visible"
        ? !archived
        : archived;
    card.hidden = !categoryMatches || !statusMatches || !displayMatches;
    if (!card.hidden) visibleCount += 1;
  });
  list.querySelectorAll("[data-document-category-group]").forEach((group) => {
    group.hidden = !Array.from(group.querySelectorAll(".design-document-item"))
      .some((card) => !card.hidden);
  });
  filterEmpty.hidden = cards.length === 0 || visibleCount > 0;
}

async function updateArchiveState(button) {
  const documentId = button.dataset.documentId;
  const archived = button.dataset.archived === "true";
  const action = archived ? "显示" : "隐藏";
  const card = button.closest(".design-document-item");
  const title = card?.querySelector(".design-document-copy strong")?.textContent.trim()
    || "这份文档";
  if (!documentId) {
    return;
  }
  await showConfirmationDialog({
    title: `${action}项目资料`,
    body: archived
      ? `显示“${title}”后，该资料会重新显示在首页。`
      : `隐藏“${title}”后，该资料不再显示在首页，但仍保留在“已隐藏”列表中。此操作不会移动或冻结仓库文件。`,
    confirmLabel: `确认${action}`,
    tone: archived ? "secondary" : "danger",
    closeOnConfirm: true,
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
        const badges = card.querySelector(".design-document-badges");
        const archivedBadge = badges.querySelector(".document-archived-badge");
        card.dataset.archived = String(payload.data.archived);
        button.dataset.archived = String(payload.data.archived);
        button.textContent = payload.data.archived ? "显示" : "隐藏";
        if (payload.data.archived && !archivedBadge) {
          const badge = document.createElement("span");
          badge.className = "badge badge-muted document-archived-badge";
          badge.textContent = "已隐藏";
          badges.append(badge);
        } else if (!payload.data.archived && archivedBadge) {
          archivedBadge.remove();
        }
        showMessage(`${action}成功。`, "success");
        applyFilters();
      } catch (error) {
        showMessage(error.message || `${action}失败。`, "error");
      } finally {
        button.disabled = false;
      }
    },
  });
}

categoryFilters.forEach((button) => {
  button.addEventListener("click", () => {
    activeCategoryFilter = button.dataset.documentCategoryFilter;
    sessionStorage.setItem(CATEGORY_FILTER_KEY, activeCategoryFilter);
    applyFilters();
  });
});

statusFilters.forEach((button) => {
  button.addEventListener("click", () => {
    activeStatusFilter = button.dataset.documentStatusFilter;
    sessionStorage.setItem(STATUS_FILTER_KEY, activeStatusFilter);
    applyFilters();
  });
});

displayFilters.forEach((button) => {
  button.addEventListener("click", () => {
    activeDisplayFilter = button.dataset.documentDisplayFilter;
    sessionStorage.setItem(DISPLAY_FILTER_KEY, activeDisplayFilter);
    applyFilters();
  });
});

list.addEventListener("click", (event) => {
  const button = event.target.closest(".document-archive-action");
  if (button) {
    updateArchiveState(button);
  }
});

if (!Array.from(categoryFilters).some((button) => button.dataset.documentCategoryFilter === activeCategoryFilter)) {
  activeCategoryFilter = "all";
}
if (!Array.from(statusFilters).some((button) => button.dataset.documentStatusFilter === activeStatusFilter)) {
  activeStatusFilter = "all";
}
if (!Array.from(displayFilters).some((button) => button.dataset.documentDisplayFilter === activeDisplayFilter)) {
  activeDisplayFilter = "all";
}
applyFilters();
