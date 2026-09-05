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
  closeOnConfirm = false,
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
      closeOnConfirm,
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
    if (current.closeOnConfirm) {
      finishConfirmationDialog(true);
      void Promise.resolve()
        .then(current.onConfirm)
        .catch(() => undefined);
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

(() => {
  let openChoicePicker = null;

  const closeChoicePicker = (picker = openChoicePicker) => {
    if (!picker) return;
    picker.menu.hidden = true;
    picker.trigger.setAttribute("aria-expanded", "false");
    if (openChoicePicker === picker) openChoicePicker = null;
  };

  const positionChoicePicker = (picker) => {
    const margin = 8;
    const gap = 6;
    const triggerRect = picker.trigger.getBoundingClientRect();
    const menu = picker.menu;
    menu.style.width = picker.matchTriggerWidth ? `${triggerRect.width}px` : "";
    menu.style.visibility = "hidden";
    menu.hidden = false;
    const menuRect = menu.getBoundingClientRect();
    const availableBelow = window.innerHeight - triggerRect.bottom - gap - margin;
    const availableAbove = triggerRect.top - gap - margin;
    const openAbove = picker.preferAbove
      ? availableAbove > 0
      : availableBelow < menuRect.height && availableAbove > availableBelow;
    const availableHeight = Math.max(0, openAbove ? availableAbove : availableBelow);
    const preferredLeft = picker.alignEnd
      ? triggerRect.right - menuRect.width
      : triggerRect.left;
    const left = Math.max(
      margin,
      Math.min(preferredLeft, window.innerWidth - menuRect.width - margin),
    );
    const top = openAbove
      ? Math.max(margin, triggerRect.top - gap - Math.min(menuRect.height, availableHeight))
      : triggerRect.bottom + gap;
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.style.maxHeight = `${availableHeight}px`;
    menu.style.visibility = "";
  };

  window.createChoicePicker = ({
    trigger,
    value,
    menu,
    onSelect,
    optionClassName = "settings-choice-picker-option",
    matchTriggerWidth = true,
    preferAbove = false,
    alignEnd = false,
  }) => {
    if (!(trigger instanceof HTMLButtonElement)
      || !(value instanceof HTMLElement)
      || !(menu instanceof HTMLElement)
      || typeof onSelect !== "function") {
      return null;
    }

    let options = [];
    let selectedValue = "";
    let disabled = false;

    const render = () => {
      const selected = options.find((option) => option.value === selectedValue);
      value.textContent = selected?.label || "";
      trigger.disabled = disabled || options.length === 0;
      menu.replaceChildren(...options.map((option) => {
        const button = document.createElement("button");
        const label = document.createElement("span");
        button.type = "button";
        button.className = optionClassName;
        button.setAttribute("role", "option");
        button.setAttribute("aria-selected", String(option.value === selectedValue));
        label.textContent = option.label;
        button.append(label);
        if (option.description) {
          const description = document.createElement("small");
          description.textContent = option.description;
          button.append(description);
        }
        if (option.value === selectedValue) button.classList.add("is-selected");
        button.addEventListener("click", () => {
          selectedValue = option.value;
          onSelect(selectedValue);
          render();
          closeChoicePicker(state);
          trigger.focus();
        });
        return button;
      }));
    };

    const state = {
      trigger,
      menu,
      matchTriggerWidth,
      preferAbove,
      alignEnd,
      focus: () => trigger.focus(),
      setDisabled(nextDisabled) {
        disabled = nextDisabled === true;
        trigger.disabled = disabled || options.length === 0;
        if (trigger.disabled) closeChoicePicker(state);
      },
      setOptions(nextOptions, nextSelectedValue) {
        options = Array.isArray(nextOptions) ? nextOptions : [];
        selectedValue = options.some((option) => option.value === nextSelectedValue)
          ? nextSelectedValue
          : options[0]?.value || "";
        render();
      },
    };

    trigger.addEventListener("click", () => {
      if (trigger.disabled) return;
      if (openChoicePicker === state) {
        closeChoicePicker(state);
        return;
      }
      closeChoicePicker();
      openChoicePicker = state;
      if (!matchTriggerWidth) {
        menu.style.width = "";
      }
      positionChoicePicker(state);
      trigger.setAttribute("aria-expanded", "true");
      menu.querySelector(".is-selected, [role='option']")?.focus();
    });
    return state;
  };

  document.addEventListener("pointerdown", (event) => {
    if (openChoicePicker
      && !openChoicePicker.trigger.contains(event.target)
      && !openChoicePicker.menu.contains(event.target)) {
      closeChoicePicker();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !openChoicePicker) return;
    event.preventDefault();
    const picker = openChoicePicker;
    closeChoicePicker(picker);
    picker.focus();
  });
  window.addEventListener("resize", () => closeChoicePicker());
})();

(() => {
  let toast = null;
  let toastTimer = null;

  const dismissToast = () => {
    window.clearTimeout(toastTimer);
    toastTimer = null;
    if (toast) toast.hidden = true;
  };

  const getToast = () => {
    if (toast || !document.body) return toast;
    const message = document.createElement("p");
    const dismiss = document.createElement("button");
    toast = document.createElement("section");
    toast.className = "chub-toast";
    toast.hidden = true;
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-live", "polite");
    message.className = "chub-toast-message";
    dismiss.type = "button";
    dismiss.className = "chub-toast-dismiss";
    dismiss.textContent = "x";
    dismiss.setAttribute("aria-label", "关闭提示");
    dismiss.title = "关闭提示";
    dismiss.addEventListener("click", dismissToast);
    toast.append(message, dismiss);
    document.body.append(toast);
    return toast;
  };

  window.showChubToast = (text, { kind = "error", duration } = {}) => {
    const content = typeof text === "string" ? text.trim() : "";
    const target = getToast();
    if (!content || !target) {
      dismissToast();
      return;
    }
    const tone = kind === "warning" ? "warning" : "error";
    const message = target.querySelector(".chub-toast-message");
    if (!(message instanceof HTMLElement)) return;
    window.clearTimeout(toastTimer);
    message.textContent = content;
    target.className = `chub-toast chub-toast-${tone}`;
    target.hidden = false;
    toastTimer = window.setTimeout(
      dismissToast,
      Number.isFinite(duration) ? duration : tone === "warning" ? 5500 : 7000,
    );
  };
})();
