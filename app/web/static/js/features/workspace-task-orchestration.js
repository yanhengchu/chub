"use strict";

(() => {
  window.initializeWorkspaceTaskOrchestration = () => {
    window.disposeWorkspaceTaskOrchestration?.();

    const message = document.getElementById("workspace-task-orchestration-message");
    const processingTrigger = document.getElementById("workspace-task-processing-trigger");
    const processingValue = document.getElementById("workspace-task-processing-value");
    const processingMenu = document.getElementById("workspace-task-processing-menu");
    const modelTrigger = document.getElementById("workspace-task-model-trigger");
    const modelValue = document.getElementById("workspace-task-model-value");
    const modelMenu = document.getElementById("workspace-task-model-menu");
    const modelDescription = document.getElementById("workspace-task-model-description");
    const reasoningTrigger = document.getElementById("workspace-task-reasoning-trigger");
    const reasoningValue = document.getElementById("workspace-task-reasoning-value");
    const reasoningMenu = document.getElementById("workspace-task-reasoning-menu");
    const reasoningDescription = document.getElementById("workspace-task-reasoning-description");

    if (
      !(message instanceof HTMLElement)
      || !(processingTrigger instanceof HTMLButtonElement)
      || !(processingValue instanceof HTMLElement)
      || !(processingMenu instanceof HTMLElement)
      || !(modelTrigger instanceof HTMLButtonElement)
      || !(modelValue instanceof HTMLElement)
      || !(modelMenu instanceof HTMLElement)
      || !(modelDescription instanceof HTMLElement)
      || !(reasoningTrigger instanceof HTMLButtonElement)
      || !(reasoningValue instanceof HTMLElement)
      || !(reasoningMenu instanceof HTMLElement)
      || !(reasoningDescription instanceof HTMLElement)
      || typeof window.createChoicePicker !== "function"
    ) {
      return;
    }

    const reasoningLabels = {
      low: "Low",
      medium: "Medium",
      high: "High",
      xhigh: "Extra High",
      max: "Max",
      ultra: "Ultra",
    };
    let status = null;
    let catalog = null;
    let loading = false;
    let saving = false;
    let disposed = false;

    const setMessage = (text = "", kind = "") => {
      message.textContent = text;
      message.className = kind ? `message message-${kind}` : "message";
    };
    const processingPicker = window.createChoicePicker({
      trigger: processingTrigger,
      value: processingValue,
      menu: processingMenu,
      optionClassName: "conversation-composer-control conversation-setting-option",
      matchTriggerWidth: false,
      alignEnd: true,
      onSelect: (mode) => void save({ mode }, "设置结果未知，请稍后刷新页面重试。"),
    });
    const modelPicker = window.createChoicePicker({
      trigger: modelTrigger,
      value: modelValue,
      menu: modelMenu,
      optionClassName: "conversation-composer-control conversation-setting-option",
      matchTriggerWidth: false,
      alignEnd: true,
      onSelect: (model) => {
        const selected = catalog?.models?.find((item) => item.id === model);
        status = {
          ...status,
          model: model || null,
          reasoning_effort: model ? (selected?.default_level || null) : null,
        };
        render();
        void saveModelSettings();
      },
    });
    const reasoningPicker = window.createChoicePicker({
      trigger: reasoningTrigger,
      value: reasoningValue,
      menu: reasoningMenu,
      optionClassName: "conversation-composer-control conversation-setting-option",
      matchTriggerWidth: false,
      alignEnd: true,
      onSelect: (reasoningEffort) => {
        status = { ...status, reasoning_effort: reasoningEffort || null };
        render();
        void saveModelSettings();
      },
    });
    if (!processingPicker || !modelPicker || !reasoningPicker) return;

    const setPickersDisabled = (disabled) => {
      processingPicker.setDisabled(disabled);
      modelPicker.setDisabled(disabled);
      reasoningPicker.setDisabled(disabled);
    };
    const apiRequest = async (path, options = {}) => {
      const response = await fetch(path, options);
      const payload = await response.json().catch(() => null);
      if (!response.ok || payload?.success !== true) {
        throw new Error(payload?.error?.message || "暂时无法读取任务编排配置。");
      }
      return payload.data;
    };
    const render = () => {
      if (disposed || !status || !catalog) return;
      const models = Array.isArray(catalog.models) ? catalog.models : [];
      const selectedMode = status.mode || (status.enabled ? "auto" : "direct");
      processingPicker.setOptions([
        { value: "direct", label: "直接执行", description: "直接提交原始文本，不执行润色。" },
        { value: "auto", label: "自动润色后执行", description: "先润色文本，再自动提交。" },
        { value: "confirm", label: "自动润色后确认执行", description: "先润色文本，确认后再提交。" },
      ], selectedMode);
      processingTrigger.setAttribute("aria-label", `任务处理：${processingValue.textContent}`);
      const defaultModel = models.find((item) => item.id === catalog.default_model);
      const modelOptions = [{
        value: "",
        label: "跟随 Codex 默认",
        description: defaultModel?.name
          ? `当前默认 ${defaultModel.name}`
          : "使用 Runtime 默认模型",
      }];
      if (status.model && !models.some((item) => item.id === status.model)) {
        modelOptions.push({
          value: status.model,
          label: status.model,
          description: "当前任务配置，模型目录中不可用",
        });
      }
      models.forEach((item) => modelOptions.push({
        value: item.id,
        label: item.name || item.id,
        description: item.description || "",
      }));
      modelPicker.setOptions(modelOptions, status.model || "");
      const effectiveModel = models.find(
        (item) => item.id === (status.model || catalog.default_model),
      );
      const effectiveReasoning = status.reasoning_effort
        || (!status.model && catalog.default_reasoning_effort)
        || effectiveModel?.default_level
        || "不可用";
      const effectiveReasoningLabel = reasoningLabels[effectiveReasoning] || effectiveReasoning;
      modelDescription.textContent = status.model
        ? `当前使用 ${effectiveModel?.name || effectiveModel?.id || status.model}；只影响之后新提交的文本优化任务。`
        : `跟随 Codex 默认，当前为 ${effectiveModel?.name || effectiveModel?.id || "不可用"} · ${effectiveReasoningLabel}。`;
      modelTrigger.setAttribute("aria-label", `模型：${modelValue.textContent}`);
      reasoningDescription.textContent = status.reasoning_effort
        ? `当前使用 ${effectiveReasoningLabel}；只影响之后新提交的文本优化任务。`
        : `跟随模型默认，当前为 ${effectiveReasoningLabel}。`;
      const levels = [{
        value: "",
        label: "跟随模型默认",
        description: effectiveModel?.default_level
          ? `当前默认 ${reasoningLabels[effectiveModel.default_level] || effectiveModel.default_level}`
          : "当前模型未提供默认等级",
      }];
      effectiveModel?.levels?.forEach((item) => levels.push({
        value: item.id,
        label: reasoningLabels[item.id] || item.id,
        description: item.description || "",
      }));
      reasoningPicker.setOptions(levels, status.reasoning_effort || "");
      reasoningTrigger.setAttribute("aria-label", `推理等级：${reasoningValue.textContent}`);
      const active = Number(status.queued || 0) + Number(status.running || 0);
      const notes = [];
      if (active > 0) notes.push(`${active} 项文本优化仍在处理中`);
      if (!status.weixin_chub_mode_enabled) notes.push("微信 Chub 模式当前未启用");
      setMessage(notes.join(" · "));
      modelPicker.setDisabled(saving || loading || (models.length === 0 && !status.model));
      reasoningPicker.setDisabled(saving || loading || !effectiveModel);
      processingPicker.setDisabled(saving || loading);
    };
    const load = async () => {
      if (loading || disposed) return;
      loading = true;
      setPickersDisabled(true);
      setMessage("");
      try {
        const [nextStatus, nextCatalog] = await Promise.all([
          apiRequest("/api/settings/weixin-translation", { cache: "no-store" }),
          apiRequest("/api/codex/models", { cache: "no-store" }),
        ]);
        if (!Array.isArray(nextCatalog?.models)) throw new Error("暂时无法读取 Codex 模型目录。");
        if (!disposed) {
          status = nextStatus;
          catalog = nextCatalog;
        }
      } catch (error) {
        if (!disposed) setMessage(
          error instanceof Error ? error.message : "暂时无法读取任务编排配置。",
          "error",
        );
      } finally {
        loading = false;
        render();
      }
    };
    const save = async (payload, failureMessage) => {
      if (!status || !catalog || saving || disposed) return;
      saving = true;
      render();
      try {
        const nextStatus = await apiRequest("/api/settings/weixin-translation", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!disposed) status = nextStatus;
      } catch {
        if (!disposed) {
          setMessage(failureMessage, "error");
          await load();
        }
      } finally {
        saving = false;
        render();
      }
    };
    const saveModelSettings = () => save({
      model: status?.model || null,
      reasoning_effort: status?.model ? (status.reasoning_effort || null) : null,
    }, "文本优化运行参数保存失败，请稍后刷新页面重试。");

    window.disposeWorkspaceTaskOrchestration = () => {
      disposed = true;
    };
    void load();
  };

  window.initializeWorkspaceTaskOrchestration();
})();
