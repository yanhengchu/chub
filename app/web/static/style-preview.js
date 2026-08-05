"use strict";

const previewDialog = document.querySelector("#preview-dialog");
const previewDialogOpen = document.querySelector("#preview-dialog-open");
const previewDialogClose = document.querySelector("#preview-dialog-close");
const previewDialogConfirm = document.querySelector("#preview-dialog-confirm");
const previewDangerDialogOpen = document.querySelector("#preview-danger-dialog-open");
const previewDialogDescription = document.querySelector("#preview-dialog-description");
cardCollapsedState = {};
setupCollapsibleCards();

document.querySelectorAll("[data-preview-feedback]").forEach((button) => {
  button.addEventListener("click", () => {
    const card = button.closest(".card");
    const message = card?.querySelector("[data-preview-message]");
    if (message) {
      message.textContent = button.dataset.previewFeedback;
    }
  });
});

previewDialogOpen.addEventListener("click", () => {
  previewDialogDescription.textContent = document.body.classList.contains("cyber-preview")
    ? "Cyber 使用命令式说明和终端化主次按钮表达操作影响。"
    : "Standard 使用清晰的说明和明确的主次按钮表达操作影响。";
  previewDialog.showModal();
});

previewDangerDialogOpen.addEventListener("click", () => {
  previewDialogDescription.textContent = "危险操作需要明确说明影响范围，并由用户再次确认。此处仅展示样式，不会执行操作。";
  previewDialog.showModal();
});

previewDialogClose.addEventListener("click", () => {
  previewDialog.close();
});

previewDialogConfirm.addEventListener("click", () => {
  previewDialog.close();
});
