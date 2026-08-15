"use strict";

(function exposeQuickInteractionTimeline(root) {
  const core = root.QuickInteractionCore
    || (typeof module !== "undefined" && module.exports
      ? require("./quick_interactions_core.js")
      : null);

  const NOTIFICATION_LABELS = Object.freeze({
    pending: "待通知",
    sending: "通知中",
    sent: "已通知",
    failed: "通知失败",
    skipped: "未通知",
  });
  const RESTART_NOTIFICATION_LABELS = Object.freeze({
    pending: "重启结果待通知",
    sending: "重启结果通知中",
    sent: "重启结果已通知",
    failed: "重启结果通知失败",
    skipped: "重启结果未通知",
  });

  function taskSignature(task) {
    return JSON.stringify([
      task.status,
      task.updated_at,
      task.prompt,
      task.result,
      task.error,
      task.pinned_at,
      task.notification_status,
      task.notification_error,
      task.deferred_restart_status,
      task.deferred_restart_updated_at,
      task.deferred_restart_notification_status,
      task.deferred_restart_notification_error,
      task.deferred_restart_notification_updated_at,
      core.statusText(task),
    ]);
  }

  function notificationState(status, error, labels) {
    const label = labels[status];
    if (!label) {
      return null;
    }
    return Object.freeze({ status, label, error: error || "" });
  }

  function restartState(task) {
    const messages = {
      succeeded: "Chub 已完成自动重启，服务已恢复。",
      start_failed: (
        `Chub 自动重启未完成：${task.deferred_restart_error
          || "旧记录没有保存具体原因，请查看 Chub 运行日志。"}`
      ),
      sensitive_task_failed: (
        "Chub 已取消自动重启：等待期间有运行资源修改任务异常结束，请检查任务结果。"
      ),
      cleared: "Chub 自动重启计划已由其他服务重启清除。",
    };
    const text = messages[task.deferred_restart_status];
    if (!text) {
      return null;
    }
    return Object.freeze({
      text,
      error: ["start_failed", "sensitive_task_failed"].includes(
        task.deferred_restart_status,
      ),
      time: core.formatTime(task.deferred_restart_updated_at),
      notification: notificationState(
        task.deferred_restart_notification_status,
        task.deferred_restart_notification_error,
        RESTART_NOTIFICATION_LABELS,
      ),
    });
  }

  function buildTaskState(task) {
    const hasResult = Boolean(task.result || task.error);
    return Object.freeze({
      signature: taskSignature(task),
      turnClass: `conversation-turn conversation-turn-${task.status}`,
      prompt: task.prompt || "历史任务未保存提交内容。",
      createdTime: core.formatTime(task.created_at),
      assistantText: hasResult ? task.result || task.error : core.statusText(task),
      assistantTime: core.formatTime(task.updated_at),
      statusOnly: !hasResult,
      error: ["failed", "timed_out", "cancelled", "needs_terminal"].includes(
        task.status,
      ),
      pinned: Boolean(task.pinned_at),
      notification: notificationState(
        task.notification_status,
        task.notification_error,
        NOTIFICATION_LABELS,
      ),
      restart: restartState(task),
    });
  }

  function mergeTasks(current, incoming, total) {
    const merged = new Map(current.map((task) => [task.id, task]));
    incoming.forEach((task) => merged.set(task.id, task));
    let result = Array.from(merged.values()).sort((left, right) => {
      const timeDifference = new Date(left.created_at) - new Date(right.created_at);
      return timeDifference || left.id.localeCompare(right.id);
    });
    if (total > 0 && result.length > total) {
      result = result.slice(-total);
    }
    return result;
  }

  function createView({ documentRef, windowRef, elements, onTogglePinned }) {
    function createMeta(text) {
      const meta = documentRef.createElement("span");
      meta.className = "conversation-message-meta";
      meta.textContent = text;
      return meta;
    }

    function createNotification(state) {
      if (!state) {
        return null;
      }
      const notification = createMeta(state.label);
      notification.classList.add(
        "conversation-notification",
        `conversation-notification-${state.status}`,
      );
      if (state.error) {
        notification.title = state.error;
        notification.setAttribute("aria-label", `${state.label}：${state.error}`);
      }
      return notification;
    }

    function updateTurn(turn, task) {
      const state = buildTaskState(task);
      if (turn.dataset.taskSignature === state.signature) {
        return;
      }
      turn.dataset.taskSignature = state.signature;
      turn.className = state.turnClass;
      turn.replaceChildren();

      const userMessage = documentRef.createElement("div");
      const userBubble = documentRef.createElement("div");
      const userContent = documentRef.createElement("p");
      userMessage.className = "conversation-message conversation-message-user";
      userBubble.className = "conversation-bubble";
      userContent.textContent = state.prompt;
      userBubble.append(userContent);
      userMessage.append(userBubble, createMeta(state.createdTime));

      const assistantMessage = documentRef.createElement("div");
      const assistantBubble = documentRef.createElement("div");
      const assistantContent = documentRef.createElement("p");
      const assistantMeta = documentRef.createElement("div");
      const assistantInfo = documentRef.createElement("div");
      const notification = createNotification(state.notification);
      const pin = documentRef.createElement("button");
      assistantMessage.className = "conversation-message conversation-message-assistant";
      assistantBubble.className = "conversation-bubble";
      assistantMeta.className = "conversation-assistant-meta";
      assistantInfo.className = "conversation-assistant-info";
      assistantInfo.append(createMeta(state.assistantTime));
      assistantContent.textContent = state.assistantText;
      if (state.statusOnly) {
        assistantBubble.classList.add("is-status");
      }
      if (state.error) {
        assistantBubble.classList.add("is-error");
      }
      pin.type = "button";
      pin.className = "button-link conversation-pin";
      pin.textContent = state.pinned ? "取消置顶" : "置顶";
      pin.setAttribute("aria-pressed", String(state.pinned));
      pin.addEventListener("click", () => onTogglePinned(task, pin));
      assistantBubble.append(assistantContent);
      assistantMeta.append(assistantInfo);
      if (notification) {
        assistantMeta.append(notification);
      }
      assistantMeta.append(pin);
      assistantMessage.append(assistantBubble, assistantMeta);
      turn.append(userMessage, assistantMessage);

      if (state.restart) {
        const restartMessage = documentRef.createElement("div");
        const restartBubble = documentRef.createElement("div");
        const restartContent = documentRef.createElement("p");
        const restartMeta = documentRef.createElement("div");
        const restartNotification = createNotification(state.restart.notification);
        restartMessage.className = (
          "conversation-message conversation-message-assistant conversation-message-system"
        );
        restartBubble.className = "conversation-bubble is-status";
        if (state.restart.error) {
          restartBubble.classList.add("is-error");
        }
        restartContent.textContent = state.restart.text;
        restartBubble.append(restartContent);
        restartMeta.className = "conversation-assistant-info";
        restartMeta.append(createMeta(`Chub 系统 · ${state.restart.time}`));
        if (restartNotification) {
          restartMeta.append(restartNotification);
        }
        restartMessage.append(restartBubble, restartMeta);
        turn.append(restartMessage);
      }
    }

    function createTurn(task) {
      const turn = documentRef.createElement("article");
      turn.dataset.taskId = task.id;
      updateTurn(turn, task);
      return turn;
    }

    function isNearBottom() {
      return elements.scroll.scrollTop + elements.scroll.clientHeight
        >= elements.scroll.scrollHeight - 140;
    }

    function updateJumpLatest(taskCount) {
      elements.jumpLatest.hidden = isNearBottom() || taskCount === 0;
    }

    function render(tasks, { forceBottom = false, preservePosition = false } = {}) {
      const wasNearBottom = !preservePosition && (forceBottom || isNearBottom());
      const empty = elements.feed.querySelector(".empty-state");
      if (!tasks.length) {
        if (!empty) {
          const nextEmpty = documentRef.createElement("p");
          nextEmpty.className = "empty-state conversation-empty";
          nextEmpty.textContent = "暂无快速交互记录，可以从下方发送第一条消息。";
          elements.feed.replaceChildren(nextEmpty);
        }
        return;
      }
      empty?.remove();
      const existing = new Map(
        Array.from(elements.feed.querySelectorAll("[data-task-id]"))
          .map((turn) => [turn.dataset.taskId, turn]),
      );
      const retained = new Set();
      tasks.forEach((task, index) => {
        const turn = existing.get(task.id) || createTurn(task);
        retained.add(task.id);
        updateTurn(turn, task);
        const current = elements.feed.children[index];
        if (current !== turn) {
          elements.feed.insertBefore(turn, current || null);
        }
      });
      existing.forEach((turn, taskId) => {
        if (!retained.has(taskId)) {
          turn.remove();
        }
      });
      if (wasNearBottom) {
        windowRef.requestAnimationFrame(() => {
          elements.scroll.scrollTop = elements.scroll.scrollHeight;
          updateJumpLatest(tasks.length);
        });
      } else {
        updateJumpLatest(tasks.length);
      }
    }

    function resizePrompt(prompt, taskCount) {
      const keepAtBottom = isNearBottom();
      prompt.style.height = "auto";
      const nextHeight = Math.min(prompt.scrollHeight, 120);
      prompt.style.height = `${nextHeight}px`;
      prompt.style.overflowY = prompt.scrollHeight > 120 ? "auto" : "hidden";
      if (keepAtBottom) {
        windowRef.requestAnimationFrame(() => {
          elements.scroll.scrollTop = elements.scroll.scrollHeight;
          updateJumpLatest(taskCount);
        });
      }
    }

    function captureTopAnchor() {
      const element = elements.feed.querySelector("[data-task-id]");
      return element
        ? Object.freeze({ element, top: element.getBoundingClientRect().top })
        : null;
    }

    function restoreTopAnchor(anchor) {
      if (anchor) {
        elements.scroll.scrollTop += anchor.element.getBoundingClientRect().top - anchor.top;
      }
    }

    function scrollToLatest(behavior = "smooth") {
      elements.scroll.scrollTo({ top: elements.scroll.scrollHeight, behavior });
    }

    return Object.freeze({
      captureTopAnchor,
      isNearBottom,
      render,
      resizePrompt,
      restoreTopAnchor,
      scrollToLatest,
      updateJumpLatest,
    });
  }

  const quickInteractionTimeline = Object.freeze({
    buildTaskState,
    createView,
    mergeTasks,
    taskSignature,
  });
  root.QuickInteractionTimeline = quickInteractionTimeline;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = quickInteractionTimeline;
  }
}(typeof globalThis === "undefined" ? this : globalThis));
