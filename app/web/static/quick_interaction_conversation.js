"use strict";

let conversationSessionId = document.body.dataset.sessionId;
const {
  canSubmit: canSubmitConversation,
  createClient: createConversationClient,
  formatErrorMessage: formatConversationErrorMessage,
  isRetryableRequestError: isRetryableConversationError,
  pollDelay: conversationPollDelay,
  readPageSize: readConversationPageSize,
  readModelCatalog: readConversationModelCatalog,
  shouldPoll: shouldPollConversation,
  shouldSuppressReconnectError: shouldSuppressConversationReconnectError,
  submissionBlockReason: conversationSubmissionBlockReason,
} = window.QuickInteractionCore;
const {
  createView: createConversationSessionView,
  firstSessionAfterArchive: firstConversationSessionAfterArchive,
  sessionUrl: conversationSessionUrl,
} = window.QuickInteractionSession;
const {
  createView: createConversationTimelineView,
  mergeTasks: mergeConversationTaskSnapshots,
} = window.QuickInteractionTimeline;
let conversationClient = createConversationClient({
  sessionId: conversationSessionId,
});
const conversationForm = document.querySelector("#conversation-form");
const conversationPrompt = document.querySelector("#conversation-prompt");
const conversationSubmit = document.querySelector("#conversation-submit");
const conversationSubmitMessage = document.querySelector("#conversation-submit-message");
const conversationPermissionTrigger = document.querySelector("#conversation-permission-trigger");
const conversationPermissionValue = document.querySelector("#conversation-permission-value");
const conversationPermissionMenu = document.querySelector("#conversation-permission-menu");
const conversationModelTrigger = document.querySelector("#conversation-model-trigger");
const conversationModelValue = document.querySelector("#conversation-model-value");
const conversationModelMenu = document.querySelector("#conversation-model-menu");
const conversationReasoningTrigger = document.querySelector("#conversation-reasoning-trigger");
const conversationReasoningValue = document.querySelector("#conversation-reasoning-value");
const conversationReasoningMenu = document.querySelector("#conversation-reasoning-menu");
const conversationHistoryMessage = document.querySelector("#conversation-history-message");
const conversationScroll = document.querySelector("#conversation-scroll");
const conversationFeed = document.querySelector("#conversation-feed");
const conversationLoadEarlier = document.querySelector("#conversation-load-earlier");
const conversationJumpLatest = document.querySelector("#conversation-jump-latest");
const conversationSessionNavigation = document.querySelector("#conversation-session-navigation");
const conversationSessionCreate = document.querySelector("#conversation-session-create");
const conversationSessionSwitcher = document.querySelector("#conversation-session-switcher");
const conversationSessionTitleRow = document.querySelector("#conversation-session-title-row");
const conversationSessionTitle = document.querySelector("#conversation-session-title");
const conversationSessionRename = document.querySelector("#conversation-session-rename");
const conversationSessionStop = document.querySelector("#conversation-session-stop");
const conversationSessionArchive = document.querySelector("#conversation-session-archive");
const conversationSessionDelete = document.querySelector("#conversation-session-delete");
const conversationCreateDialog = document.querySelector("#conversation-create-dialog");
const conversationCreateSurface = document.querySelector("#conversation-create-surface");
const conversationCreateWorkspaces = document.querySelector("#conversation-create-workspaces");
const conversationCreateMessage = document.querySelector("#conversation-create-message");
const conversationCreateClose = document.querySelector("#conversation-create-close");
const conversationRenameDialog = document.querySelector("#conversation-rename-dialog");
const conversationRenameForm = document.querySelector("#conversation-rename-form");
const conversationRenameInput = document.querySelector("#conversation-rename-input");
const conversationRenameMessage = document.querySelector("#conversation-rename-message");
const conversationRenameClose = document.querySelector("#conversation-rename-close");
const conversationRenameCancel = document.querySelector("#conversation-rename-cancel");
const conversationRenameConfirm = document.querySelector("#conversation-rename-confirm");
const conversationStopDialog = document.querySelector("#conversation-stop-dialog");
const conversationStopForm = document.querySelector("#conversation-stop-form");
const conversationStopDescription = document.querySelector("#conversation-stop-description");
const conversationStopMessage = document.querySelector("#conversation-stop-message");
const conversationStopClose = document.querySelector("#conversation-stop-close");
const conversationStopCancel = document.querySelector("#conversation-stop-cancel");
const conversationStopConfirm = document.querySelector("#conversation-stop-confirm");
const conversationArchiveDialog = document.querySelector("#conversation-archive-dialog");
const conversationArchiveForm = document.querySelector("#conversation-archive-form");
const conversationArchiveDescription = document.querySelector("#conversation-archive-description");
const conversationArchiveMessage = document.querySelector("#conversation-archive-message");
const conversationArchiveClose = document.querySelector("#conversation-archive-close");
const conversationArchiveCancel = document.querySelector("#conversation-archive-cancel");
const conversationArchiveConfirm = document.querySelector("#conversation-archive-confirm");
const conversationDeleteDialog = document.querySelector("#conversation-delete-dialog");
const conversationDeleteForm = document.querySelector("#conversation-delete-form");
const conversationDeleteDescription = document.querySelector("#conversation-delete-description");
const conversationDeleteMessage = document.querySelector("#conversation-delete-message");
const conversationDeleteClose = document.querySelector("#conversation-delete-close");
const conversationDeleteCancel = document.querySelector("#conversation-delete-cancel");
const conversationDeleteConfirm = document.querySelector("#conversation-delete-confirm");
const CONVERSATION_PAGE_SIZE = readConversationPageSize();
const conversationSessionView = createConversationSessionView({
  documentRef: document,
  windowRef: window,
  showMessage: showConversationMessage,
  elements: {
    navigation: conversationSessionNavigation,
    switcher: conversationSessionSwitcher,
    titleRow: conversationSessionTitleRow,
    title: conversationSessionTitle,
    rename: conversationSessionRename,
    stop: conversationSessionStop,
    archive: conversationSessionArchive,
    delete: conversationSessionDelete,
    create: conversationSessionCreate,
    createDialog: conversationCreateDialog,
    createSurface: conversationCreateSurface,
    createWorkspaces: conversationCreateWorkspaces,
    createMessage: conversationCreateMessage,
    createClose: conversationCreateClose,
    renameDialog: conversationRenameDialog,
    renameForm: conversationRenameForm,
    renameInput: conversationRenameInput,
    renameMessage: conversationRenameMessage,
    renameClose: conversationRenameClose,
    renameCancel: conversationRenameCancel,
    renameConfirm: conversationRenameConfirm,
    stopDialog: conversationStopDialog,
    stopForm: conversationStopForm,
    stopDescription: conversationStopDescription,
    stopMessage: conversationStopMessage,
    stopClose: conversationStopClose,
    stopCancel: conversationStopCancel,
    stopConfirm: conversationStopConfirm,
    archiveDialog: conversationArchiveDialog,
    archiveForm: conversationArchiveForm,
    archiveDescription: conversationArchiveDescription,
    archiveMessage: conversationArchiveMessage,
    archiveClose: conversationArchiveClose,
    archiveCancel: conversationArchiveCancel,
    archiveConfirm: conversationArchiveConfirm,
    deleteDialog: conversationDeleteDialog,
    deleteForm: conversationDeleteForm,
    deleteDescription: conversationDeleteDescription,
    deleteMessage: conversationDeleteMessage,
    deleteClose: conversationDeleteClose,
    deleteCancel: conversationDeleteCancel,
    deleteConfirm: conversationDeleteConfirm,
    form: conversationForm,
    prompt: conversationPrompt,
    submit: conversationSubmit,
    submitMessage: conversationSubmitMessage,
  },
});
const conversationTimelineView = createConversationTimelineView({
  documentRef: document,
  windowRef: window,
  elements: {
    scroll: conversationScroll,
    feed: conversationFeed,
    jumpLatest: conversationJumpLatest,
  },
});
let conversationPollTimer = null;
let conversationPollFailureCount = 0;
let conversationLoadQueue = Promise.resolve();
let conversationGeneration = 0;
let conversationLoadEarlierPending = false;
let conversationTasks = [];
let conversationTotal = 0;
let conversationHasEarlier = false;
let conversationHistoryExpanded = false;
let conversationInitialized = false;
let conversationActive = false;
let conversationSession = null;
let conversationSessions = [];
let conversationCreationAvailable = false;
let conversationCreationPending = false;
let conversationWorkspaces = [];
let conversationConfirmStopUnknownTerminal = false;
let conversationSessionSwitcherSignature = "";
let conversationRenamePending = false;
let conversationStopPending = false;
let conversationArchivePending = false;
let conversationDeletePending = false;
let conversationConfigurationPending = false;
let conversationModelCatalog = null;
let conversationComposerSessionId = null;
let conversationOpenComposerMenu = null;
let conversationWorkspaceActivitySignature = "";
// The composer mirrors the current Session while its submission semantics remain session-owned.
let conversationComposerSelections = {
  permission: null,
  model: null,
  reasoningEffort: null,
};

const CONVERSATION_PERMISSION_LABELS = Object.freeze({
  ask: "Ask for approval",
  "auto-review": "Approve for me",
  "full-access": "Full access",
  "read-only": "Read Only",
});
const CONVERSATION_REASONING_LABELS = Object.freeze({
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra High",
  max: "Max",
  ultra: "Ultra",
});

function notifyWorkspaceSessionActivity() {
  if (window.parent === window) return;
  const latestTask = conversationTasks.reduce((latest, task) => (
    !latest || Date.parse(task.updated_at) > Date.parse(latest.updated_at)
      ? task
      : latest
  ), null);
  const payload = {
    type: "chub.workspace.quick-session-activity",
    sessionId: conversationSessionId,
    running: conversationActive,
    updatedAt: latestTask?.updated_at || null,
  };
  const signature = JSON.stringify(payload);
  if (signature === conversationWorkspaceActivitySignature) return;
  conversationWorkspaceActivitySignature = signature;
  window.parent.postMessage(payload, window.location.origin);
}

function handleConversationSessionSwitch(event) {
  const request = conversationSessionView.navigationRequest(
    event,
    conversationSessionId,
  );
  if (!request) {
    return;
  }
  if (request.mode === "ignore") {
    event.preventDefault();
    return;
  }
  if (request.mode === "default") {
    return;
  }
  event.preventDefault();
  if (request.mode === "new-tab") {
    window.open(request.url, "_blank", "noopener");
    return;
  }
  switchConversationSession(request.sessionId, request.url);
}

function conversationDraftKey() {
  return `hub.quickInteractionDraft.v1.${conversationSessionId}`;
}

const conversationComposerMenus = [
  [conversationPermissionTrigger, conversationPermissionMenu],
  [conversationModelTrigger, conversationModelMenu],
  [conversationReasoningTrigger, conversationReasoningMenu],
];

function closeConversationComposerMenus(exceptMenu = null) {
  conversationComposerMenus.forEach(([trigger, menu]) => {
    const open = menu === exceptMenu;
    menu.hidden = !open;
    trigger.setAttribute("aria-expanded", String(open));
  });
  conversationOpenComposerMenu = exceptMenu;
}

function positionConversationComposerMenu(trigger, menu) {
  const triggerRect = trigger.getBoundingClientRect();
  menu.style.position = "fixed";
  menu.style.left = "0px";
  menu.style.right = "auto";
  menu.style.top = "auto";
  menu.style.bottom = "0px";
  menu.style.transform = "none";
  const menuRect = menu.getBoundingClientRect();
  const horizontalMargin = 8;
  const left = Math.max(
    horizontalMargin,
    Math.min(
      triggerRect.left,
      window.innerWidth - menuRect.width - horizontalMargin,
    ),
  );
  const spaceAbove = triggerRect.top - horizontalMargin;
  const spaceBelow = window.innerHeight - triggerRect.bottom - horizontalMargin;
  if (menuRect.height <= spaceAbove || spaceAbove >= spaceBelow) {
    menu.style.left = `${left}px`;
    menu.style.bottom = `${Math.max(8, window.innerHeight - triggerRect.top + 8)}px`;
  } else {
    menu.style.left = `${left}px`;
    menu.style.top = `${Math.min(
      window.innerHeight - menuRect.height - horizontalMargin,
      triggerRect.bottom + 8,
    )}px`;
    menu.style.bottom = "auto";
  }
}

function openConversationComposerMenu(trigger, menu) {
  if (trigger.disabled) {
    return;
  }
  const open = menu.hidden;
  closeConversationComposerMenus(open ? menu : null);
  if (open) {
    positionConversationComposerMenu(trigger, menu);
    conversationOpenComposerMenu = menu;
    menu.querySelector('[role="option"]:not(:disabled)')?.focus();
  }
}

function conversationModelInfo(modelId) {
  return conversationModelCatalog?.models?.find((model) => model.id === modelId) || null;
}

function conversationOptionButton({ value, label, description, selected, disabled = false }) {
  const button = document.createElement("button");
  const title = document.createElement("span");
  button.type = "button";
  button.className = "conversation-composer-control conversation-setting-option";
  button.setAttribute("role", "option");
  button.dataset.value = value;
  button.disabled = disabled;
  button.setAttribute("aria-selected", String(selected));
  if (disabled) {
    button.setAttribute("aria-disabled", "true");
  }
  if (selected) {
    button.classList.add("is-selected");
  }
  title.textContent = label;
  button.append(title);
  if (description) {
    const hint = document.createElement("small");
    hint.textContent = description;
    button.append(hint);
  }
  return button;
}

function renderConversationComposerOptions(session) {
  if (!session) {
    conversationPermissionTrigger.disabled = true;
    conversationModelTrigger.disabled = true;
    conversationReasoningTrigger.disabled = true;
    return;
  }
  if (conversationComposerSessionId !== session.id) {
    conversationComposerSessionId = session.id;
    conversationComposerSelections = {
      permission: session.permission_mode || "full-access",
      model: session.model || "",
      reasoningEffort: session.reasoning_effort || "",
    };
  }

  const permission = conversationComposerSelections.permission || session.permission_mode || "full-access";
  const permissionLabel = CONVERSATION_PERMISSION_LABELS[permission] || permission;
  conversationPermissionValue.textContent = permissionLabel;
  conversationPermissionTrigger.setAttribute("aria-label", `权限：${permissionLabel}`);
  conversationPermissionTrigger.disabled = conversationConfigurationPending;
  conversationPermissionTrigger.title = permission === "ask"
    ? "Ask for approval 需要进入实时终端"
    : "查看权限选项";
  conversationPermissionMenu.querySelectorAll("[role='option']").forEach((option) => {
    option.setAttribute("aria-selected", String(option.dataset.value === permission));
    option.classList.toggle("is-selected", option.dataset.value === permission);
  });

  const models = Array.isArray(conversationModelCatalog?.models)
    ? conversationModelCatalog.models
    : [];
  const selectedModel = conversationComposerSelections.model || "";
  const currentModel = conversationModelInfo(
    selectedModel || conversationModelCatalog?.default_model,
  )
    || (session.model ? { id: session.model, name: session.model, levels: [] } : null);
  conversationModelMenu.replaceChildren(
    conversationOptionButton({
      value: "",
      label: "跟随 Codex 默认",
      description: conversationModelCatalog?.default_model
        ? `当前默认 ${conversationModelInfo(conversationModelCatalog.default_model)?.name || conversationModelCatalog.default_model}`
        : "使用 Runtime 默认模型",
      selected: selectedModel === "",
      disabled: false,
    }),
    ...models.map((model) => conversationOptionButton({
      value: model.id,
      label: model.name || model.id,
      description: model.description || "",
      selected: model.id === selectedModel,
    })),
    ...(currentModel && !models.some((model) => model.id === currentModel.id)
      ? [conversationOptionButton({
        value: currentModel.id,
        label: currentModel.name,
        description: "当前 Session 配置",
        selected: currentModel.id === selectedModel,
      })]
      : []),
  );
  conversationModelValue.textContent = currentModel?.name
    || (conversationModelCatalog?.default_model
      ? `跟随默认 · ${conversationModelInfo(conversationModelCatalog.default_model)?.name || conversationModelCatalog.default_model}`
      : "跟随默认");
  conversationModelTrigger.setAttribute(
    "aria-label",
    `模型：${conversationModelValue.textContent}`,
  );
  conversationModelTrigger.disabled = conversationConfigurationPending;

  const levels = Array.isArray(currentModel?.levels) ? currentModel.levels : [];
  const selectedLevel = conversationComposerSelections.reasoningEffort || "";
  const defaultLevel = currentModel?.default_level
    || conversationModelCatalog?.default_reasoning_effort
    || "";
  conversationReasoningMenu.replaceChildren(
    conversationOptionButton({
      value: "",
      label: "跟随模型默认",
      description: defaultLevel
        ? `当前默认 ${CONVERSATION_REASONING_LABELS[defaultLevel] || defaultLevel}`
        : "当前模型未提供默认等级",
      selected: selectedLevel === "",
    }),
    ...levels.map((level) => conversationOptionButton({
      value: level.id,
      label: CONVERSATION_REASONING_LABELS[level.id] || level.id,
      description: level.description || "",
      selected: level.id === selectedLevel,
      disabled: !selectedModel,
    })),
  );
  conversationReasoningValue.textContent = selectedLevel
    ? (CONVERSATION_REASONING_LABELS[selectedLevel] || selectedLevel)
    : (defaultLevel
      ? (CONVERSATION_REASONING_LABELS[defaultLevel] || defaultLevel)
      : "跟随默认");
  conversationReasoningTrigger.setAttribute(
    "aria-label",
    `推理等级：${conversationReasoningValue.textContent}`,
  );
  conversationReasoningTrigger.disabled = conversationConfigurationPending;
}

async function selectConversationComposerOption(kind, value) {
  if (!conversationSession || conversationConfigurationPending) {
    return;
  }
  const previous = {
    permission: conversationSession.permission_mode || "full-access",
    model: conversationSession.model || "",
    reasoningEffort: conversationSession.reasoning_effort || "",
  };
  conversationComposerSelections = {
    ...conversationComposerSelections,
    [kind]: value,
  };
  if (kind === "model") {
    conversationComposerSelections.reasoningEffort = "";
  }
  closeConversationComposerMenus();
  renderConversationComposerOptions(conversationSession);
  conversationConfigurationPending = true;
  renderConversationComposerOptions(conversationSession);
  const generation = conversationGeneration;
  try {
    const updated = await conversationClient.updateSessionConfiguration({
      permissionMode: conversationComposerSelections.permission,
      model: conversationComposerSelections.model || null,
      reasoningEffort: conversationComposerSelections.reasoningEffort || null,
    });
    if (generation !== conversationGeneration) return;
    conversationSession = updated;
    conversationComposerSessionId = updated.id;
    conversationComposerSelections = {
      permission: updated.permission_mode,
      model: updated.model || "",
      reasoningEffort: updated.reasoning_effort || "",
    };
  } catch (error) {
    if (generation === conversationGeneration) {
      conversationSession = {
        ...conversationSession,
        permission_mode: previous.permission,
        model: previous.model || null,
        reasoning_effort: previous.reasoningEffort || null,
      };
      conversationComposerSelections = previous;
      showConversationMessage(
        conversationSubmitMessage,
        formatConversationErrorMessage(error, "Session 配置保存失败。"),
        "error",
      );
    }
  } finally {
    if (generation === conversationGeneration) {
      conversationConfigurationPending = false;
      renderConversationComposerOptions(conversationSession);
    }
  }
}

async function loadConversationModelCatalog() {
  try {
    conversationModelCatalog = await readConversationModelCatalog();
    if (conversationSession) {
      renderConversationComposerOptions(conversationSession);
    }
  } catch (_error) {
    // The current Session values remain usable when the model catalog is unavailable.
  }
}

function renderConversationSessionPreview(session) {
  conversationSessionView.renderPreview(session);
}

function resetConversationSessionView(sessionPreview) {
  conversationPollFailureCount = 0;
  conversationLoadQueue = Promise.resolve();
  conversationLoadEarlierPending = false;
  conversationTasks = [];
  conversationTotal = 0;
  conversationHasEarlier = false;
  conversationHistoryExpanded = false;
  conversationInitialized = false;
  conversationActive = false;
  conversationSession = null;
  conversationConfigurationPending = false;
  conversationComposerSessionId = null;
  conversationComposerSelections = {
    permission: null,
    model: null,
    reasoningEffort: null,
  };
  renderConversationComposerOptions(null);
  closeConversationComposerMenus();
  conversationConfirmStopUnknownTerminal = false;
  conversationStopPending = false;
  conversationDeletePending = false;
  conversationFeed.replaceChildren();
  conversationLoadEarlier.hidden = true;
  conversationLoadEarlier.disabled = false;
  conversationJumpLatest.hidden = true;
  renderConversationSessionPreview(sessionPreview);
  conversationPrompt.value = readConversationDraft();
  conversationPrompt.disabled = true;
  conversationSubmit.disabled = true;
  conversationForm.setAttribute("aria-busy", "true");
  showConversationMessage(conversationHistoryMessage, "");
  showConversationMessage(conversationSubmitMessage, "");
  resizeConversationPrompt();
}

function switchConversationSession(sessionId, url, sessionPreview = null) {
  if (!sessionId || sessionId === conversationSessionId) {
    return;
  }
  const preview = sessionPreview
    || conversationSessions.find((session) => session.id === sessionId)
    || { id: sessionId, title: null };
  saveConversationDraft();
  window.clearTimeout(conversationPollTimer);
  conversationGeneration += 1;
  conversationSessionId = sessionId;
  conversationClient = createConversationClient({
    sessionId,
  });
  document.body.dataset.sessionId = sessionId;
  const targetUrl = new URL(url, window.location.origin);
  if (new URL(window.location.href).searchParams.get("embedded") === "workspace") {
    targetUrl.searchParams.set("embedded", "workspace");
  }
  const historyUrl = `${targetUrl.pathname}${targetUrl.search}${targetUrl.hash}`;
  try {
    window.history.replaceState(window.history.state, "", historyUrl);
  } catch (_error) {
    window.location.replace(historyUrl);
    return;
  }
  if (window.parent !== window) {
    window.parent.postMessage(
      { type: "chub.workspace.quick-session-selection", sessionId },
      window.location.origin,
    );
  }
  resetConversationSessionView(preview);
  renderConversationSessionSwitcher(conversationSessions);
  void loadConversation();
}

function renderConversationSessionSwitcher(sessions) {
  conversationSessions = sessions;
  conversationSessionSwitcherSignature = conversationSessionView.renderSwitcher(
    { sessions, currentSessionId: conversationSessionId },
    conversationSessionSwitcherSignature,
  );
}

function renderConversationSessionCreation(context) {
  conversationCreationAvailable = context.available === true;
  conversationWorkspaces = Array.isArray(context.workspaces) ? context.workspaces : [];
  conversationSessionView.renderCreation(context, conversationCreationPending);
}

function closeConversationCreateDialog() {
  if (!conversationCreationPending && conversationCreateDialog.open) {
    conversationCreateDialog.close();
  }
}

function openConversationCreateDialog() {
  if (conversationSessionCreate.disabled) {
    return;
  }
  conversationSessionView.openCreate(
    conversationWorkspaces,
    (workspaceId) => void createConversationSession(workspaceId),
  );
}

async function createConversationSession(workspaceId) {
  if (conversationCreationPending) {
    return;
  }
  conversationCreationPending = true;
  conversationSessionView.setCreatePending(true);
  renderConversationSessionCreation({
    available: conversationCreationAvailable,
    workspaces: conversationWorkspaces,
  });
  showConversationMessage(conversationCreateMessage, "正在创建 Session…");
  try {
    const session = await conversationClient.createSession({ workspaceId });
    conversationCreationPending = false;
    conversationCreateDialog.close();
    switchConversationSession(
      session.id,
      conversationSessionUrl(session.id),
      session,
    );
  } catch (error) {
    conversationCreationPending = false;
    conversationSessionView.setCreatePending(false);
    renderConversationSessionCreation({
      available: conversationCreationAvailable,
      workspaces: conversationWorkspaces,
    });
    showConversationMessage(
      conversationCreateMessage,
      formatConversationErrorMessage(error, "Session 创建失败。"),
      "error",
    );
  }
}

function readConversationDraft() {
  try {
    return sessionStorage.getItem(conversationDraftKey()) || "";
  } catch (_error) {
    return "";
  }
}

function saveConversationDraft() {
  try {
    if (conversationPrompt.value) {
      sessionStorage.setItem(conversationDraftKey(), conversationPrompt.value);
    } else {
      sessionStorage.removeItem(conversationDraftKey());
    }
  } catch (_error) {
    // Storage failure only affects draft persistence in this browser tab.
  }
}

function showConversationMessage(element, text, kind = "") {
  element.textContent = text;
  element.className = "message";
  if (kind) {
    element.classList.add(`message-${kind}`);
  }
}

function renderConversationTasks({ forceBottom = false, preservePosition = false } = {}) {
  conversationTimelineView.render(
    conversationTasks,
    { forceBottom, preservePosition },
  );
}

function updateConversationJumpLatest() {
  conversationTimelineView.updateJumpLatest(conversationTasks.length);
}

function isConversationNearBottom() {
  return conversationTimelineView.isNearBottom();
}

function resizeConversationPrompt() {
  conversationTimelineView.resizePrompt(conversationPrompt, conversationTasks.length);
}

function mergeConversationTasks(tasks) {
  conversationTasks = mergeConversationTaskSnapshots(
    conversationTasks,
    tasks,
    conversationTotal,
  );
}

function renderConversationSession(session) {
  conversationSession = session;
  renderConversationComposerOptions(session);
  const state = conversationSessionView.renderSession({
    session,
    activeInteraction: conversationActive,
    stopPending: conversationStopPending,
    archivePending: conversationArchivePending,
    deletePending: conversationDeletePending,
    promptLength: conversationPrompt.value.length,
  });
  conversationConfirmStopUnknownTerminal = state.confirmStopUnknownTerminal;
}

function closeConversationRenameDialog() {
  if (!conversationRenamePending && conversationRenameDialog.open) {
    conversationRenameDialog.close();
  }
}

function openConversationRenameDialog() {
  if (
    !conversationSession
    || conversationSessionRename.disabled
    || conversationRenamePending
  ) {
    return;
  }
  conversationSessionView.openRename(conversationSession);
}

function closeConversationStopDialog() {
  if (!conversationStopPending && conversationStopDialog.open) {
    conversationStopDialog.close();
  }
}

function openConversationStopDialog() {
  if (
    !conversationSession
    || conversationSessionStop.disabled
    || conversationStopPending
  ) {
    return;
  }
  conversationSessionView.openStop(conversationSession);
}

async function stopConversationSession(event) {
  event.preventDefault();
  if (!conversationSession || conversationStopPending) {
    return;
  }
  conversationStopPending = true;
  conversationSessionView.setStopPending(true);
  showConversationMessage(conversationStopMessage, "");
  window.clearTimeout(conversationPollTimer);
  const generation = conversationGeneration;
  const client = conversationClient;
  try {
    const session = await client.stopSession();
    if (generation !== conversationGeneration) {
      return;
    }
    conversationActive = false;
    renderConversationSession(session);
    conversationStopDialog.close();
    await loadConversation();
  } catch (error) {
    if (generation !== conversationGeneration) {
      return;
    }
    showConversationMessage(
      conversationStopMessage,
      formatConversationErrorMessage(error, "Session 停止失败。"),
      "error",
    );
    void loadConversation();
  } finally {
    if (generation === conversationGeneration) {
      conversationStopPending = false;
      if (conversationSession) {
        renderConversationSession(conversationSession);
      }
    }
  }
}

async function renameConversationSession(event) {
  event.preventDefault();
  if (conversationRenamePending) {
    return;
  }
  const title = conversationRenameInput.value.trim();
  if (!title) {
    showConversationMessage(
      conversationRenameMessage,
      "请输入 Session 标题。",
      "error",
    );
    conversationRenameInput.focus();
    return;
  }
  conversationRenamePending = true;
  conversationSessionView.setRenamePending(true);
  const generation = conversationGeneration;
  const client = conversationClient;
  try {
    const session = await client.renameSession(title);
    if (generation !== conversationGeneration) {
      return;
    }
    renderConversationSession(session);
    conversationRenameDialog.close();
    void loadConversation();
  } catch (error) {
    if (generation !== conversationGeneration) {
      return;
    }
    showConversationMessage(
      conversationRenameMessage,
      formatConversationErrorMessage(error, "Session 重命名失败。"),
      "error",
    );
  } finally {
    conversationRenamePending = false;
    conversationSessionView.setRenamePending(false);
  }
}

function closeConversationArchiveDialog() {
  if (!conversationArchivePending && conversationArchiveDialog.open) {
    conversationArchiveDialog.close();
  }
}

function closeConversationDeleteDialog() {
  if (!conversationDeletePending && conversationDeleteDialog.open) {
    conversationDeleteDialog.close();
  }
}

function openConversationArchiveDialog() {
  if (!conversationSession || conversationSessionArchive.disabled) {
    return;
  }
  conversationSessionView.openArchive(conversationSession);
}

function openConversationDeleteDialog() {
  if (!conversationSession || conversationSessionDelete.disabled) {
    return;
  }
  conversationSessionView.openDelete(conversationSession);
}

async function archiveConversationSession(event) {
  event.preventDefault();
  if (!conversationSession || conversationArchivePending) {
    return;
  }
  conversationArchivePending = true;
  conversationSessionView.setArchivePending(true);
  showConversationMessage(conversationArchiveMessage, "");
  window.clearTimeout(conversationPollTimer);
  const generation = conversationGeneration;
  const archivedSessionId = conversationSessionId;
  const client = conversationClient;
  try {
    await client.archiveSession();
    if (generation !== conversationGeneration) {
      return;
    }
    const nextSession = firstConversationSessionAfterArchive(
      conversationSessions,
      archivedSessionId,
    );
    const nextSessionUrl = nextSession
      ? conversationSessionUrl(nextSession.id)
      : "/";
    if (!nextSession) {
      window.location.replace(nextSessionUrl);
      return;
    }
    conversationArchivePending = false;
    conversationSessionView.setArchivePending(false);
    conversationArchiveDialog.close();
    conversationSessions = conversationSessions.filter(
      (session) => session.id !== archivedSessionId,
    );
    switchConversationSession(nextSession.id, nextSessionUrl, nextSession);
  } catch (error) {
    if (generation !== conversationGeneration) {
      return;
    }
    if (error?.code === "codex_session_not_found") {
      // The list can be stale while another reconciliation or client has
      // already removed this Session. Archive is then already complete from
      // the user's perspective; leave the retired conversation page.
      window.location.replace("/");
      return;
    }
    conversationArchivePending = false;
    conversationSessionView.setArchivePending(false);
    showConversationMessage(
      conversationArchiveMessage,
      formatConversationErrorMessage(error, "Session 归档失败。"),
      "error",
    );
    renderConversationSession(conversationSession);
    void loadConversation();
  }
}

async function deleteConversationSession(event) {
  event.preventDefault();
  if (!conversationSession || conversationDeletePending) {
    return;
  }
  conversationDeletePending = true;
  conversationSessionView.setDeletePending(true);
  showConversationMessage(conversationDeleteMessage, "");
  window.clearTimeout(conversationPollTimer);
  const generation = conversationGeneration;
  const deletedSessionId = conversationSessionId;
  const client = conversationClient;
  try {
    await client.deleteSession();
    if (generation !== conversationGeneration) {
      return;
    }
    const nextSession = firstConversationSessionAfterArchive(
      conversationSessions,
      deletedSessionId,
    );
    const nextSessionUrl = nextSession
      ? conversationSessionUrl(nextSession.id)
      : "/";
    if (!nextSession) {
      window.location.replace(nextSessionUrl);
      return;
    }
    conversationDeletePending = false;
    conversationSessionView.setDeletePending(false);
    conversationDeleteDialog.close();
    conversationSessions = conversationSessions.filter(
      (session) => session.id !== deletedSessionId,
    );
    switchConversationSession(nextSession.id, nextSessionUrl, nextSession);
  } catch (error) {
    if (generation !== conversationGeneration) {
      return;
    }
    if (error?.code === "codex_session_not_found") {
      // The list can be stale while another reconciliation or client has
      // already removed this Session. Deletion is already complete from the
      // user's perspective; leave the retired conversation page.
      window.location.replace("/");
      return;
    }
    conversationDeletePending = false;
    conversationSessionView.setDeletePending(false);
    showConversationMessage(
      conversationDeleteMessage,
      formatConversationErrorMessage(error, "Session 删除失败。"),
      "error",
    );
    renderConversationSession(conversationSession);
    void loadConversation();
  }
}

function renderConversationSessionError(error) {
  conversationSessionView.renderError(error);
}

function conversationRestartPending() {
  return conversationTasks.some(
    (task) => task.deferred_restart_status === "pending"
      || task.deferred_restart_status === "started",
  );
}

async function performConversationLoad(generation, client) {
  window.clearTimeout(conversationPollTimer);
  const [historyResult, sessionContextResult] = await Promise.allSettled([
    client.listTasks({
      limit: CONVERSATION_PAGE_SIZE,
      order: "timeline",
    }),
    client.loadSessionContext(),
  ]);
  if (generation !== conversationGeneration) {
    return;
  }
  const loadErrors = [historyResult, sessionContextResult]
    .filter((result) => result.status === "rejected")
    .map((result) => result.reason);
  if (loadErrors.length === 0) {
    conversationPollFailureCount = 0;
  } else if (loadErrors.some(isRetryableConversationError)) {
    conversationPollFailureCount += 1;
  }
  const restartPending = conversationRestartPending();
  const suppressReconnectError = shouldSuppressConversationReconnectError({
    loadErrors,
    restartPending,
    failureCount: conversationPollFailureCount,
  });
  if (historyResult.status === "fulfilled") {
    const data = historyResult.value;
    conversationTotal = data.total;
    mergeConversationTasks(data.tasks);
    conversationActive = conversationTasks.some(
      (task) => ["requested", "running"].includes(task.status),
    );
    notifyWorkspaceSessionActivity();
    if (!conversationInitialized) {
      conversationHasEarlier = data.has_more;
    } else if (conversationHistoryExpanded) {
      conversationHasEarlier = conversationTasks.length < conversationTotal;
    }
    renderConversationTasks({ forceBottom: !conversationInitialized });
    conversationInitialized = true;
    conversationLoadEarlier.hidden = !conversationHasEarlier;
    showConversationMessage(conversationHistoryMessage, "");
  } else if (suppressReconnectError) {
    showConversationMessage(conversationHistoryMessage, "");
  } else {
    showConversationMessage(
      conversationHistoryMessage,
      historyResult.reason.message || "快速交互记录读取失败。",
      "error",
    );
  }
  if (sessionContextResult.status === "fulfilled") {
    renderConversationSessionSwitcher(sessionContextResult.value.sessions);
    renderConversationSessionCreation(sessionContextResult.value);
    renderConversationSession(sessionContextResult.value.session);
  } else if (!suppressReconnectError) {
    renderConversationSessionError(sessionContextResult.reason);
  } else {
    showConversationMessage(conversationSubmitMessage, "");
  }
  const session = sessionContextResult.status === "fulfilled"
    ? sessionContextResult.value.session
    : conversationSession;
  if (shouldPollConversation({
    loadFailed: loadErrors.length > 0,
    loadErrors,
    activeInteraction: conversationActive,
    notificationPending: conversationTasks.some((task) => (
      task.notification_status === "pending"
      || task.notification_status === "sending"
      || task.deferred_restart_notification_status === "pending"
      || task.deferred_restart_notification_status === "sending"
    )),
    restartPending,
    session,
    sessions: sessionContextResult.status === "fulfilled"
      ? sessionContextResult.value.sessions
      : [],
  }) && document.visibilityState !== "hidden") {
    conversationPollTimer = window.setTimeout(
      loadConversation,
      conversationPollDelay(conversationPollFailureCount),
    );
  }
}

function loadConversation() {
  const generation = conversationGeneration;
  const client = conversationClient;
  const queued = conversationLoadQueue.then(
    () => performConversationLoad(generation, client),
  );
  conversationLoadQueue = queued.catch(() => {});
  return queued;
}

async function performLoadEarlierConversation(generation, client) {
  const anchor = conversationTimelineView.captureTopAnchor();
  try {
    const oldest = conversationTasks[0];
    const data = await client.listTasks({
      limit: CONVERSATION_PAGE_SIZE,
      order: "timeline",
      before: { createdAt: oldest.created_at, id: oldest.id },
    });
    if (generation !== conversationGeneration) {
      return;
    }
    conversationTotal = data.total;
    conversationHistoryExpanded = true;
    conversationHasEarlier = data.has_more;
    mergeConversationTasks(data.tasks);
    renderConversationTasks({ preservePosition: true });
    conversationLoadEarlier.hidden = !conversationHasEarlier;
    conversationTimelineView.restoreTopAnchor(anchor);
    showConversationMessage(conversationHistoryMessage, "");
  } catch (error) {
    if (generation !== conversationGeneration) {
      return;
    }
    if (shouldSuppressConversationReconnectError({
      loadErrors: [error],
      restartPending: conversationRestartPending(),
      failureCount: conversationPollFailureCount + 1,
    })) {
      showConversationMessage(conversationHistoryMessage, "");
    } else {
      showConversationMessage(
        conversationHistoryMessage,
        formatConversationErrorMessage(error, "更早消息读取失败。"),
        "error",
      );
    }
  } finally {
    if (generation === conversationGeneration) {
      conversationLoadEarlier.disabled = false;
    }
  }
}

function loadEarlierConversation() {
  if (
    conversationLoadEarlierPending
    || !conversationTasks.length
    || !conversationHasEarlier
  ) {
    return conversationLoadQueue;
  }
  conversationLoadEarlierPending = true;
  conversationLoadEarlier.disabled = true;
  const generation = conversationGeneration;
  const client = conversationClient;
  const queued = conversationLoadQueue.then(
    () => performLoadEarlierConversation(generation, client),
  );
  conversationLoadQueue = queued.catch(() => {});
  return queued.finally(() => {
    if (generation === conversationGeneration) {
      conversationLoadEarlierPending = false;
      conversationLoadEarlier.disabled = false;
    }
  });
}

conversationPrompt.addEventListener("input", () => {
  saveConversationDraft();
  resizeConversationPrompt();
  if (conversationSession) {
    renderConversationSession(conversationSession);
  }
});

conversationPrompt.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!conversationSubmit.disabled) {
      conversationForm.requestSubmit();
    }
  }
});

conversationForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const value = conversationPrompt.value.trim();
  const reason = conversationSubmissionBlockReason({
    session: conversationSession,
    activeInteraction: conversationActive,
    promptLength: conversationPrompt.value.length,
  });
  if (!canSubmitConversation({
    prompt: value,
    session: conversationSession,
    blocked: Boolean(reason),
  })) {
    return;
  }
  const submittedPrompt = conversationPrompt.value;
  conversationSubmit.disabled = true;
  const generation = conversationGeneration;
  const client = conversationClient;
  try {
    const data = await client.submitTask({
      prompt: value,
      confirmStopUnknownTerminal: conversationConfirmStopUnknownTerminal,
    });
    if (generation !== conversationGeneration) {
      return;
    }
    if (conversationPrompt.value === submittedPrompt) {
      conversationPrompt.value = "";
    }
    saveConversationDraft();
    resizeConversationPrompt();
    conversationConfirmStopUnknownTerminal = false;
    conversationActive = true;
    conversationTotal += conversationTasks.some((task) => task.id === data.task.id) ? 0 : 1;
    mergeConversationTasks([data.task]);
    notifyWorkspaceSessionActivity();
    renderConversationTasks({ forceBottom: true });
    showConversationMessage(conversationSubmitMessage, "");
    await loadConversation();
  } catch (error) {
    if (generation !== conversationGeneration) {
      return;
    }
    if (
      error.code === "quick_interaction_terminal_confirmation_required"
    ) {
      conversationConfirmStopUnknownTerminal = true;
      showConversationMessage(
        conversationSubmitMessage,
        "请确认影响后再次点击发送。",
        "error",
      );
    } else {
      showConversationMessage(
        conversationSubmitMessage,
        formatConversationErrorMessage(error, "快速交互提交失败。"),
        "error",
      );
    }
    try {
      const session = await client.loadSession();
      if (generation !== conversationGeneration) {
        return;
      }
      renderConversationSession(session);
    } catch (sessionError) {
      if (generation !== conversationGeneration) {
        return;
      }
      renderConversationSessionError(sessionError);
    }
  }
});

conversationLoadEarlier.addEventListener("click", loadEarlierConversation);
conversationJumpLatest.addEventListener("click", () => {
  conversationTimelineView.scrollToLatest();
});
conversationSessionSwitcher.addEventListener("click", handleConversationSessionSwitch);
conversationSessionSwitcher.addEventListener("auxclick", handleConversationSessionSwitch);
conversationSessionCreate.addEventListener("click", openConversationCreateDialog);
conversationCreateClose.addEventListener("click", closeConversationCreateDialog);
conversationCreateDialog.addEventListener("click", (event) => {
  if (event.target === conversationCreateDialog) {
    closeConversationCreateDialog();
  }
});
conversationCreateDialog.addEventListener("cancel", (event) => {
  if (conversationCreationPending) {
    event.preventDefault();
  }
});
conversationSessionRename.addEventListener("click", openConversationRenameDialog);
conversationSessionStop.addEventListener("click", openConversationStopDialog);
conversationSessionArchive.addEventListener("click", openConversationArchiveDialog);
conversationSessionDelete.addEventListener("click", openConversationDeleteDialog);
conversationRenameForm.addEventListener("submit", renameConversationSession);
conversationRenameClose.addEventListener("click", closeConversationRenameDialog);
conversationRenameCancel.addEventListener("click", closeConversationRenameDialog);
conversationRenameDialog.addEventListener("click", (event) => {
  if (event.target === conversationRenameDialog) {
    closeConversationRenameDialog();
  }
});
conversationRenameDialog.addEventListener("cancel", (event) => {
  if (conversationRenamePending) {
    event.preventDefault();
  }
});
conversationStopForm.addEventListener("submit", stopConversationSession);
conversationStopClose.addEventListener("click", closeConversationStopDialog);
conversationStopCancel.addEventListener("click", closeConversationStopDialog);
conversationStopDialog.addEventListener("click", (event) => {
  if (event.target === conversationStopDialog) {
    closeConversationStopDialog();
  }
});
conversationStopDialog.addEventListener("cancel", (event) => {
  if (conversationStopPending) {
    event.preventDefault();
  }
});
conversationArchiveForm.addEventListener("submit", archiveConversationSession);
conversationArchiveClose.addEventListener("click", closeConversationArchiveDialog);
conversationArchiveCancel.addEventListener("click", closeConversationArchiveDialog);
conversationArchiveDialog.addEventListener("click", (event) => {
  if (event.target === conversationArchiveDialog) {
    closeConversationArchiveDialog();
  }
});
conversationArchiveDialog.addEventListener("cancel", (event) => {
  if (conversationArchivePending) {
    event.preventDefault();
  }
});
conversationDeleteForm.addEventListener("submit", deleteConversationSession);
conversationDeleteClose.addEventListener("click", closeConversationDeleteDialog);
conversationDeleteCancel.addEventListener("click", closeConversationDeleteDialog);
conversationDeleteDialog.addEventListener("click", (event) => {
  if (event.target === conversationDeleteDialog) {
    closeConversationDeleteDialog();
  }
});
conversationDeleteDialog.addEventListener("cancel", (event) => {
  if (conversationDeletePending) {
    event.preventDefault();
  }
});
conversationScroll.addEventListener("scroll", updateConversationJumpLatest, { passive: true });

conversationComposerMenus.forEach(([trigger, menu]) => {
  trigger.addEventListener("click", () => {
    openConversationComposerMenu(trigger, menu);
  });
  menu.addEventListener("click", (event) => {
    const option = event.target.closest?.('[role="option"]');
    if (!option || option.disabled) {
      return;
    }
    const kind = option.closest("[data-composer-option]")?.dataset.composerOption;
    if (kind) {
      selectConversationComposerOption(
        kind === "reasoning" ? "reasoningEffort" : kind,
        option.dataset.value || "",
      );
    }
  });
});

document.addEventListener("click", (event) => {
  if (!event.target.closest(".conversation-composer-option")) {
    closeConversationComposerMenus();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeConversationComposerMenus();
  }
});

document.addEventListener("keydown", (event) => {
  if (
    event.defaultPrevented
    || event.repeat
    || event.altKey
    || !(event.metaKey || event.ctrlKey)
    || event.key.toLowerCase() !== "b"
    || window.parent === window
    || typeof window.parent.toggleWorkspaceSidebar !== "function"
  ) {
    return;
  }
  event.preventDefault();
  window.parent.toggleWorkspaceSidebar();
});

window.addEventListener("resize", () => {
  if (!conversationOpenComposerMenu) {
    return;
  }
  const entry = conversationComposerMenus.find(([, menu]) => (
    menu === conversationOpenComposerMenu
  ));
  if (entry) {
    positionConversationComposerMenu(entry[0], entry[1]);
  }
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    loadConversation();
  } else {
    window.clearTimeout(conversationPollTimer);
  }
});

conversationPrompt.value = readConversationDraft();
resizeConversationPrompt();
loadConversation();
loadConversationModelCatalog();
