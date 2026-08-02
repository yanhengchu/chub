"use strict";

const QUICK_INTERACTION_VIEW_KEY = "hub.quickInteractionView.v1";
const QUICK_INTERACTION_PAGE_SIZE_KEY = "hub.quickInteractionPageSize.v1";
const quickInteractionViewInputs = document.querySelectorAll(
  'input[name="quick-interaction-view"]',
);
const settingsMessage = document.querySelector("#settings-message");
const quickInteractionPageSize = document.querySelector(
  "#quick-interaction-page-size",
);

function normalizeQuickInteractionView(value) {
  return value === "conversation" ? "conversation" : "task";
}

function readQuickInteractionView() {
  try {
    return normalizeQuickInteractionView(
      localStorage.getItem(QUICK_INTERACTION_VIEW_KEY),
    );
  } catch (_error) {
    return "task";
  }
}

function renderQuickInteractionView(value) {
  const selected = normalizeQuickInteractionView(value);
  quickInteractionViewInputs.forEach((input) => {
    const active = input.value === selected;
    const option = input.closest(".settings-option");
    const state = option.querySelector("[data-setting-state]");
    input.checked = active;
    option.classList.toggle("is-selected", active);
    state.hidden = !active;
  });
}

function saveQuickInteractionView(value) {
  const selected = normalizeQuickInteractionView(value);
  try {
    localStorage.setItem(QUICK_INTERACTION_VIEW_KEY, selected);
    renderQuickInteractionView(selected);
    settingsMessage.textContent = selected === "conversation"
      ? "已切换为会话视图，下次从首页进入快速交互时生效。"
      : "已切换为任务视图，下次从首页进入快速交互时生效。";
    settingsMessage.className = "message message-success";
  } catch (_error) {
    renderQuickInteractionView(readQuickInteractionView());
    settingsMessage.textContent = "当前浏览器无法保存界面偏好。";
    settingsMessage.className = "message message-error";
  }
}

function readQuickInteractionPageSize() {
  try {
    return localStorage.getItem(QUICK_INTERACTION_PAGE_SIZE_KEY) === "10"
      ? "10"
      : "5";
  } catch (_error) {
    return "5";
  }
}

function saveQuickInteractionPageSize(value) {
  const selected = value === "10" ? "10" : "5";
  try {
    localStorage.setItem(QUICK_INTERACTION_PAGE_SIZE_KEY, selected);
    quickInteractionPageSize.value = selected;
    settingsMessage.textContent = `已设置为每页 ${selected} 条，下次进入快速交互时生效。`;
    settingsMessage.className = "message message-success";
  } catch (_error) {
    quickInteractionPageSize.value = readQuickInteractionPageSize();
    settingsMessage.textContent = "当前浏览器无法保存界面偏好。";
    settingsMessage.className = "message message-error";
  }
}

quickInteractionViewInputs.forEach((input) => {
  input.addEventListener("change", () => {
    if (input.checked) {
      saveQuickInteractionView(input.value);
    }
  });
});

quickInteractionPageSize.addEventListener("change", () => {
  saveQuickInteractionPageSize(quickInteractionPageSize.value);
});

renderQuickInteractionView(readQuickInteractionView());
quickInteractionPageSize.value = readQuickInteractionPageSize();
