"use strict";

(() => {
  window.initializeWorkspaceTaskOrchestration = () => {
    window.disposeWorkspaceTaskOrchestration?.();

    const message = document.getElementById("workspace-task-orchestration-message");
    const orchestrationList = document.querySelector(".workspace-task-orchestration-list");
    const implementationTrigger = document.getElementById("workspace-task-implementation-trigger");
    const implementationValue = document.getElementById("workspace-task-implementation-value");
    const implementationMenu = document.getElementById("workspace-task-implementation-menu");
    const implementationDescription = document.getElementById("workspace-task-implementation-description");
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
    const showInternalNativeSession = document.getElementById(
      "workspace-task-show-internal-native-session",
    );

    if (
      !(message instanceof HTMLElement)
      || !(orchestrationList instanceof HTMLElement)
      || !(implementationTrigger instanceof HTMLButtonElement)
      || !(implementationValue instanceof HTMLElement)
      || !(implementationMenu instanceof HTMLElement)
      || !(implementationDescription instanceof HTMLElement)
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
      || !(showInternalNativeSession instanceof HTMLInputElement)
      || typeof window.createChoicePicker !== "function"
    ) {
      return;
    }

    const implementationRow = implementationTrigger.closest(".workspace-task-orchestration-field");
    const internalSessionRow = showInternalNativeSession.closest(".workspace-task-orchestration-field");
    if (implementationRow instanceof HTMLElement && internalSessionRow instanceof HTMLElement) {
      orchestrationList.insertBefore(internalSessionRow, implementationRow);
    }
    const pluginEnabledRow = document.createElement("section");
    pluginEnabledRow.className = "workspace-task-orchestration-field settings-field settings-field-toggle";
    const pluginEnabledCopy = document.createElement("span");
    const pluginEnabledTitle = document.createElement("strong");
    pluginEnabledTitle.textContent = "插件是否启用";
    const pluginEnabledDescription = document.createElement("small");
    pluginEnabledDescription.textContent = "关闭后，后续微信普通文本不进入润色插件流程；已受理任务不受影响。";
    pluginEnabledCopy.append(pluginEnabledTitle, pluginEnabledDescription);
    const pluginEnabledControl = document.createElement("label");
    pluginEnabledControl.className = "settings-switch";
    const pluginEnabledInput = document.createElement("input");
    pluginEnabledInput.type = "checkbox";
    pluginEnabledInput.id = "workspace-task-plugin-enabled";
    pluginEnabledInput.setAttribute("aria-label", "插件是否启用");
    const pluginEnabledTrack = document.createElement("span");
    pluginEnabledTrack.className = "settings-switch-track";
    pluginEnabledTrack.setAttribute("aria-hidden", "true");
    pluginEnabledControl.htmlFor = pluginEnabledInput.id;
    pluginEnabledControl.append(pluginEnabledInput, pluginEnabledTrack);
    pluginEnabledRow.append(pluginEnabledCopy, pluginEnabledControl);
    orchestrationList.prepend(pluginEnabledRow);
    const processingTitle = document.getElementById("workspace-task-processing-title");
    if (processingTitle instanceof HTMLElement) {
      processingTitle.textContent = "润色模式";
      processingTitle.nextElementSibling.textContent = "选择微信 ClawBot 普通文本的直接执行、自动润色或润色后确认。";
    }
    processingMenu.setAttribute("aria-label", "润色模式");

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
    let orchestration = null;
    let modules = [];
    let loading = false;
    let saving = false;
    let disposed = false;

    const setMessage = (text = "", kind = "") => {
      message.textContent = text;
      message.className = kind ? `message message-${kind}` : "message";
    };
    const implementationPicker = window.createChoicePicker({
      trigger: implementationTrigger,
      value: implementationValue,
      menu: implementationMenu,
      optionClassName: "conversation-composer-control conversation-setting-option",
      matchTriggerWidth: false,
      alignEnd: true,
      onSelect: (implementation) => void saveImplementation(implementation),
    });
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
    if (!implementationPicker || !processingPicker || !modelPicker || !reasoningPicker) return;

    const setPickersDisabled = (disabled) => {
      processingPicker.setDisabled(disabled);
      implementationPicker.setDisabled(disabled);
      modelPicker.setDisabled(disabled);
      reasoningPicker.setDisabled(disabled);
      showInternalNativeSession.disabled = disabled;
      pluginEnabledInput.disabled = disabled;
    };
    const apiRequest = async (path, options = {}) => {
      const response = await fetch(path, options);
      const payload = await response.json().catch(() => null);
      if (!response.ok || payload?.success !== true) {
        throw new Error(payload?.error?.message || "暂时无法读取任务编排配置。");
      }
      return payload.data;
    };
    const formalVersion = (version) => {
      const normalized = typeof version === "string" ? version.trim().replace(/^v/i, "") : "";
      return normalized ? `v${normalized}` : "未知版本";
    };
    const render = () => {
      if (disposed || !status || !catalog || !orchestration) return;
      const models = Array.isArray(catalog.models) ? catalog.models : [];
      const implementationOptions = [
        {
          value: "weixin-orchestration-dev",
          label: "微信任务润色 · 开发实现",
          description: orchestration.development_available
            ? "使用仓库固定的开发阶段；只影响之后新接收的润色任务。"
            : "当前源码不可用，不能用于新任务。",
        },
      ];
      modules.filter((item) => item.available).forEach((item) => implementationOptions.push({
        value: `module:${item.implementation_ref}`,
        label: `微信任务润色 · 正式版 ${formalVersion(item.version)}`,
        description: `ZIP 模块 · ${item.version} · 仅影响之后新接收的润色任务。`,
      }));
      const selectedImplementation = orchestration.implementation === "module"
        ? `module:${orchestration.module_ref || ""}`
        : orchestration.implementation || "weixin-orchestration-dev";
      const selectedModule = orchestration.implementation === "module"
        ? modules.find((item) => item.implementation_ref === orchestration.module_ref)
        : null;
      implementationPicker.setOptions(
        implementationOptions,
        selectedImplementation,
      );
      implementationDescription.textContent = orchestration.implementation === "module"
        ? (orchestration.module_available && selectedModule?.available
          ? `当前使用正式版 ${formalVersion(selectedModule.version)}；只影响之后新接收的润色任务。`
          : "当前选择的正式版不可用；请选择开发实现或其他可用 ZIP。")
        : (orchestration.development_available
          ? "当前使用开发实现；只影响之后新接收的润色任务。"
          : "当前开发实现不可用；请选择可用 ZIP。")
      implementationTrigger.setAttribute("aria-label", `编排实现：${implementationValue.textContent}`);
      const selectedMode = status.mode || (status.enabled ? "auto" : "direct");
      processingPicker.setOptions([
        { value: "direct", label: "直接执行", description: "直接提交原始文本，不执行润色。" },
        { value: "auto", label: "自动润色后执行", description: "先润色文本，再自动提交。" },
        { value: "confirm", label: "自动润色后确认执行", description: "先润色文本，确认后再提交。" },
      ], selectedMode);
      processingTrigger.setAttribute("aria-label", `润色模式：${processingValue.textContent}`);
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
      showInternalNativeSession.checked = status.show_internal_native_session === true;
      pluginEnabledInput.checked = orchestration.enabled === true;
      pluginEnabledInput.setAttribute(
        "aria-label",
        `插件是否启用：${pluginEnabledInput.checked ? "已启用" : "未启用"}`,
      );
      const active = Number(status.queued || 0) + Number(status.running || 0);
      const notes = [];
      if (active > 0) notes.push(`${active} 项文本优化仍在处理中`);
      if (Number(status.native_cleanup_pending || 0) > 0) {
        notes.push(
          `${status.native_cleanup_pending} 个历史翻译 Session 等待清理${
            status.native_cleanup_error ? `：${status.native_cleanup_error}` : ""
          }`,
        );
      } else if (status.native_cleanup_error) {
        notes.push(`历史翻译 Session 清理状态未知：${status.native_cleanup_error}`);
      }
      if (!status.weixin_chub_mode_enabled) notes.push("微信 Chub 模式当前未启用");
      setMessage(
        notes.join(" · "),
        status.native_cleanup_retry_required ? "error" : "",
      );
      modelPicker.setDisabled(saving || loading || (models.length === 0 && !status.model));
      reasoningPicker.setDisabled(saving || loading || !effectiveModel);
      processingPicker.setDisabled(saving || loading);
      implementationPicker.setDisabled(
        saving
        || loading
        || (!orchestration.development_available
          && !modules.some((item) => item.available)),
      );
      showInternalNativeSession.disabled = saving || loading;
      pluginEnabledInput.disabled = saving || loading;
    };
    const load = async () => {
      if (loading || disposed) return;
      loading = true;
      setPickersDisabled(true);
      setMessage("");
      try {
        const [nextStatus, nextCatalog, nextOrchestration, nextModules] = await Promise.all([
          apiRequest("/api/settings/weixin-translation", { cache: "no-store" }),
          apiRequest("/api/codex/models", { cache: "no-store" }),
          apiRequest("/api/settings/weixin-task-orchestration", { cache: "no-store" }),
          apiRequest("/api/settings/weixin-task-orchestration/modules", { cache: "no-store" }),
        ]);
        if (!Array.isArray(nextCatalog?.models)) throw new Error("暂时无法读取 Codex 模型目录。");
        if (!disposed) {
          status = nextStatus;
          catalog = nextCatalog;
          orchestration = nextOrchestration;
          modules = Array.isArray(nextModules?.modules) ? nextModules.modules : [];
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

    const saveImplementation = async (selection) => {
      if (!orchestration || saving || disposed) return;
      const moduleRef = typeof selection === "string" && selection.startsWith("module:")
        ? selection.slice("module:".length)
        : null;
      const implementation = moduleRef ? "module" : selection;
      saving = true;
      render();
      try {
        const nextOrchestration = await apiRequest(
          "/api/settings/weixin-task-orchestration",
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(moduleRef ? { implementation, module_ref: moduleRef } : { implementation }),
          },
        );
        if (!disposed) orchestration = nextOrchestration;
      } catch (error) {
        if (!disposed) {
          setMessage(
            error instanceof Error
              ? error.message
              : "任务编排实现保存失败，请稍后刷新页面重试。",
            "error",
          );
          await load();
        }
      } finally {
        saving = false;
        render();
      }
    };

    const savePluginEnabled = async () => {
      if (!orchestration || saving || disposed) return;
      const enabled = pluginEnabledInput.checked;
      // Keep the user-selected value through the immediate loading render.
      // The server response remains authoritative and restores it on failure.
      orchestration = { ...orchestration, enabled };
      saving = true;
      render();
      try {
        const nextOrchestration = await apiRequest(
          "/api/settings/weixin-task-orchestration",
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled }),
          },
        );
        if (!disposed) orchestration = nextOrchestration;
      } catch (error) {
        if (!disposed) {
          setMessage(
            error instanceof Error ? error.message : "插件启用状态保存失败，请稍后刷新页面重试。",
            "error",
          );
          await load();
        }
      } finally {
        saving = false;
        render();
      }
    };

    showInternalNativeSession.addEventListener("change", () => {
      void save(
        { show_internal_native_session: showInternalNativeSession.checked },
        "内部翻译 Session 显示设置保存失败，请稍后刷新页面重试。",
      );
    });
    pluginEnabledInput.addEventListener("change", () => {
      void savePluginEnabled();
    });

    window.disposeWorkspaceTaskOrchestration = () => {
      disposed = true;
    };
    void load();
  };

  window.initializeWorkspaceTaskOrchestration();
})();
