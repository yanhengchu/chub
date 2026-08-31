"use strict";

function setMessage(target, message, kind = "") {
  target.textContent = message;
  target.className = "message";
  if (kind) {
    target.classList.add(`message-${kind}`);
  }
}

function setBadge(target, label, kind = "muted") {
  target.textContent = label;
  target.className = `badge badge-${kind}`;
}

function setWorkstationStatus(target, message, kind = "muted") {
  target.textContent = message;
  target.className = `workstation-status-detail workstation-status-detail-${kind}`;
}

const confirmationDialog = document.querySelector("#confirmation-dialog");
const confirmationDialogForm = document.querySelector("#confirmation-dialog-form");
const confirmationDialogTitle = document.querySelector("#confirmation-dialog-title");
const confirmationDialogDescription = document.querySelector("#confirmation-dialog-description");
let confirmationDialogDetails = document.querySelector("#confirmation-dialog-details");
const confirmationDialogMessage = document.querySelector("#confirmation-dialog-message");
const confirmationDialogClose = document.querySelector("#confirmation-dialog-close");
const confirmationDialogCancel = document.querySelector("#confirmation-dialog-cancel");
const confirmationDialogConfirm = document.querySelector("#confirmation-dialog-confirm");
let confirmationDialogRequest = null;
let confirmationDialogBusy = false;

function setConfirmationDialogBusy(busy) {
  confirmationDialogBusy = busy;
  confirmationDialogForm.setAttribute("aria-busy", String(busy));
  confirmationDialogClose.disabled = busy;
  confirmationDialogCancel.disabled = busy;
  confirmationDialogConfirm.disabled = busy;
  confirmationDialogConfirm.textContent = busy
    ? confirmationDialogRequest.pendingLabel
    : confirmationDialogRequest.confirmLabel;
}

function finishConfirmationDialog(confirmed) {
  const current = confirmationDialogRequest;
  if (!current) {
    return;
  }
  confirmationDialogRequest = null;
  confirmationDialogBusy = false;
  confirmationDialogForm.removeAttribute("aria-busy");
  confirmationDialogClose.disabled = false;
  confirmationDialogCancel.disabled = false;
  confirmationDialogConfirm.disabled = false;
  if (confirmationDialog.open) {
    confirmationDialog.close();
  }
  current.resolve(confirmed);
}

function dismissConfirmationDialog() {
  if (!confirmationDialogBusy) {
    finishConfirmationDialog(false);
  }
}

function confirmationDialogDetailsTarget() {
  if (confirmationDialogDetails || !confirmationDialogDescription) {
    return confirmationDialogDetails;
  }
  const details = document.createElement("ul");
  details.id = "confirmation-dialog-details";
  details.className = "confirmation-dialog-details";
  details.hidden = true;
  confirmationDialogDescription.after(details);
  confirmationDialogDetails = details;
  return details;
}

function showConfirmationDialog({
  title,
  description,
  details = [],
  confirmLabel = "确认",
  pendingLabel = "处理中…",
  tone = "danger",
  errorMessage = "操作失败。",
  onConfirm,
}) {
  if (!confirmationDialog || confirmationDialogRequest || typeof onConfirm !== "function") {
    return Promise.resolve(false);
  }
  confirmationDialogTitle.textContent = title;
  confirmationDialogDescription.textContent = description;
  const detailsTarget = confirmationDialogDetailsTarget();
  if (detailsTarget) {
    detailsTarget.replaceChildren();
  }
  for (const { label, value } of details) {
    const item = document.createElement("li");
    const itemLabel = document.createElement("strong");
    const itemValue = document.createElement("span");
    itemLabel.textContent = label;
    itemValue.textContent = value;
    item.append(itemLabel, itemValue);
    detailsTarget?.append(item);
  }
  if (detailsTarget) {
    detailsTarget.hidden = details.length === 0;
  }
  confirmationDialogConfirm.textContent = confirmLabel;
  confirmationDialogConfirm.className = tone === "danger"
    ? "button-danger"
    : "button-secondary";
  setMessage(confirmationDialogMessage, "");
  return new Promise((resolve) => {
    confirmationDialogRequest = {
      confirmLabel,
      errorMessage,
      onConfirm,
      pendingLabel,
      resolve,
    };
    confirmationDialog.showModal();
    confirmationDialogConfirm.focus();
  });
}

if (confirmationDialog) {
  confirmationDialogForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const current = confirmationDialogRequest;
    if (!current || confirmationDialogBusy) {
      return;
    }
    setConfirmationDialogBusy(true);
    setMessage(confirmationDialogMessage, "");
    try {
      await current.onConfirm();
      finishConfirmationDialog(true);
    } catch (error) {
      setConfirmationDialogBusy(false);
      setMessage(
        confirmationDialogMessage,
        typeof error?.message === "string" && error.message
          ? error.message
          : current.errorMessage,
        "error",
      );
    }
  });
  confirmationDialogClose.addEventListener("click", dismissConfirmationDialog);
  confirmationDialogCancel.addEventListener("click", dismissConfirmationDialog);
  confirmationDialog.addEventListener("click", (event) => {
    if (event.target === confirmationDialog) {
      dismissConfirmationDialog();
    }
  });
  confirmationDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    dismissConfirmationDialog();
  });
}
