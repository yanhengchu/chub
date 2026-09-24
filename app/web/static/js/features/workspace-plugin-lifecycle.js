"use strict";

(() => {
  const request = async (path, options = {}) => {
    const response = await fetch(path, { cache: "no-store", ...options });
    const payload = await response.json();
    if (!response.ok || payload.success !== true) throw new Error(payload?.error?.message || "插件操作失败。");
    return payload.data;
  };

  const button = (label, className, handler) => {
    const element = document.createElement("button");
    element.type = "button";
    element.className = className;
    element.textContent = label;
    element.addEventListener("click", handler);
    return element;
  };

  const initialize = () => {
    const list = document.getElementById("plugin-lifecycle-list");
    const message = document.getElementById("plugin-lifecycle-message");
    const statusTargets = Object.fromEntries(
      [...document.querySelectorAll("[data-plugin-status-target]")]
        .map((target) => [target.dataset.pluginStatusTarget, target]),
    );
    const versionPickers = Object.fromEntries(
      [...document.querySelectorAll("[data-plugin-version-picker]")]
        .map((picker) => [picker.dataset.pluginVersionPicker, picker]),
    );
    if (!list && !Object.keys(statusTargets).length && !Object.keys(versionPickers).length) return;
    let data = null;
    let busy = false;
    const feedback = (text, error = false) => {
      if (!message) return;
      message.textContent = text;
      message.className = error ? "message message-error" : "message";
    };
    const perform = async (operation, { refreshNavigation = false, propagate = false } = {}) => {
      if (busy) return;
      busy = true;
      try {
        await operation();
        if (refreshNavigation) {
          window.location.reload();
          return;
        }
        await load();
      } catch (error) {
        feedback(error.message, true);
        if (propagate) throw error;
      } finally {
        busy = false;
        render();
      }
    };
    Object.entries(versionPickers).forEach(([pluginId, picker]) => {
      picker.addEventListener("change", () => {
        const artifactId = picker.value;
        if (!artifactId) return;
        void perform(() => request(`/api/plugins/${encodeURIComponent(pluginId)}/enabled`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ artifact_id: artifactId, enabled: true }),
        }));
      });
    });
    const row = (plugin, artifact) => {
      const imported = plugin.imported_artifact_ids.includes(artifact.artifact_id);
      const enabled = Array.isArray(plugin.enabled_artifact_ids) && plugin.enabled_artifact_ids.includes(artifact.artifact_id);
      const item = document.createElement("div");
      item.className = "settings-utility-row runtime-module-row";
      const copy = document.createElement("span");
      const title = document.createElement("strong");
      const detail = document.createElement("small");
      const unavailable = artifact.available === false;
      title.textContent = `${plugin.name} · ${artifact.name}`;
      const loadLabel = artifact.load_state === "loaded"
        ? (enabled ? "入口已装配；尚未接入任务阶段" : "当前仍已装配；待重载卸载（未接入阶段）")
        : artifact.load_state === "failed"
          ? `装配失败：${artifact.load_reason || "修复模块后重新加载 Web。"}`
          : artifact.load_state === "pending_reload"
            ? "待 Web 重载装配入口"
            : "未装配入口";
      const lifecycleDetail = plugin.module_type === "orchestration" && (imported || artifact.loaded)
        ? ` ${enabled ? "已启用" : "未启用"} · ${loadLabel}`
        : "";
      const description = plugin.plugin_id === "chub-task-prompt-optimizer"
        ? "优化普通任务提示词，结果返回统一分发点。"
        : artifact.description || "该版本的能力说明暂不可用。";
      detail.textContent = unavailable
        ? (artifact.reason || "该插件制品当前不可用；可恢复制品后继续使用，或移除该记录。")
        : `${description}${lifecycleDetail}`;
      copy.append(title, detail);
      const actions = document.createElement("span");
      actions.className = "runtime-module-row-actions";
      if (!imported) {
        if (!unavailable) actions.append(button("导入", "button-secondary", () => void perform(() => request(`/api/plugins/${encodeURIComponent(plugin.plugin_id)}/imports`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ artifact_id: artifact.artifact_id }) }), { refreshNavigation: true })));
      } else if (plugin.lifecycle_available === false) {
        const registered = document.createElement("small");
        registered.textContent = "已导入登记；启用、停用与加载将在后续生命周期交付项中开放。";
        actions.append(registered);
        const removeButton = button("移除登记", "button-danger", () => {
          const remove = () => perform(
            () => request(`/api/plugins/${encodeURIComponent(plugin.plugin_id)}/imports/${encodeURIComponent(artifact.artifact_id)}`, { method: "DELETE" }),
            { refreshNavigation: true, propagate: true },
          );
          if (typeof window.showConfirmationDialog !== "function") { void remove(); return; }
          const body = artifact.source === "development"
            ? "只撤销导入登记，保留仓库中的开发模块源码。"
            : "撤销导入登记并清理该插件专属安装副本，保留 ZIP 来源文件。";
          void window.showConfirmationDialog({
            title: "移除编排插件登记",
            body,
            details: [{ label: "插件", value: `${plugin.name} · ${artifact.name}` }],
            confirmLabel: "移除登记",
            pendingLabel: "正在移除…",
            errorMessage: "插件登记未能移除。",
            onConfirm: remove,
          });
        });
        if (artifact.removable === false) {
          removeButton.disabled = true;
          removeButton.title = artifact.reason || "该插件实现由宿主保留，当前不能移除。";
        }
        actions.append(removeButton);
      } else if (plugin.module_type === "orchestration" && artifact.enablement_available === false) {
        const scope = document.createElement("small");
        scope.textContent = "ZIP 制品的启用与加载属于后续独立交付项。";
        actions.append(scope);
        const removeButton = button("移除", "button-danger", () => {
          const remove = () => perform(
            () => request(`/api/plugins/${encodeURIComponent(plugin.plugin_id)}/imports/${encodeURIComponent(artifact.artifact_id)}`, { method: "DELETE" }),
            { refreshNavigation: true, propagate: true },
          );
          if (typeof window.showConfirmationDialog !== "function") { void remove(); return; }
          void window.showConfirmationDialog({
            title: "移除编排插件登记",
            body: "撤销该 ZIP 制品的导入登记，并清理其专属安装副本；来源 ZIP 保留。",
            details: [{ label: "插件", value: `${plugin.name} · ${artifact.name}` }],
            confirmLabel: "移除",
            pendingLabel: "正在移除…",
            errorMessage: "插件登记未能移除。",
            onConfirm: remove,
          });
        });
        if (artifact.removable === false) {
          removeButton.disabled = true;
          removeButton.title = artifact.reason || "该插件实现由宿主保留，当前不能移除。";
        }
        actions.append(removeButton);
      } else {
        const toggle = button(enabled ? "禁用" : "启用", "button-secondary", () => void perform(() => request(`/api/plugins/${encodeURIComponent(plugin.plugin_id)}/enabled`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ artifact_id: artifact.artifact_id, enabled: !enabled }) })));
        if ((!enabled && unavailable) || (plugin.module_type === "orchestration" && artifact.enablement_available === false)) {
          toggle.disabled = true;
          toggle.title = unavailable ? "插件制品当前不可用，无法启用。" : "当前交付项仅开放仓库开发模块的启停。";
        }
        actions.append(toggle);
        const removeButton = button("移除", "button-danger", () => {
          const remove = () => perform(
            () => request(`/api/plugins/${encodeURIComponent(plugin.plugin_id)}/imports/${encodeURIComponent(artifact.artifact_id)}`, { method: "DELETE" }),
            { refreshNavigation: true, propagate: true },
          );
          if (typeof window.showConfirmationDialog !== "function") { void remove(); return; }
          void window.showConfirmationDialog({
            title: "移除插件",
            body: plugin.module_type === "orchestration"
              ? "若已启用，将先停用再移除；已受理任务按原有快照继续处理。当前实例已装配的入口会在 Web 重载后卸载。"
              : "若已启用，将先停用再移除；已受理任务按原有快照与恢复规则继续处理。",
            details: [{ label: "插件", value: `${plugin.name} · ${artifact.name}` }],
            confirmLabel: "移除",
            pendingLabel: "正在移除…",
            errorMessage: "插件未能移除。",
            onConfirm: remove,
          });
        });
        if (artifact.removable === false) {
          removeButton.disabled = true;
          removeButton.title = artifact.reason || "该插件实现由宿主保留，当前不能移除。";
        }
        actions.append(removeButton);
      }
      item.append(copy, actions);
      return item;
    };
    const render = () => {
      const plugins = Array.isArray(data?.plugins) ? data.plugins : [];
      if (list) list.replaceChildren(...plugins.flatMap((plugin) => (plugin.artifacts || []).map((artifact) => row(plugin, artifact))));
      plugins.forEach((plugin) => {
        renderStatus(plugin, statusTargets[plugin.plugin_id]);
        renderVersionPicker(plugin, versionPickers[plugin.plugin_id]);
      });
    };
    const renderStatus = (plugin, target) => {
      if (!(target instanceof HTMLElement)) return;
      const enabled = Array.isArray(plugin.enabled_artifact_ids) ? plugin.enabled_artifact_ids : [];
      const imported = Array.isArray(plugin.imported_artifact_ids) ? plugin.imported_artifact_ids : [];
      const loadedArtifacts = Array.isArray(plugin.loaded_artifact_ids) ? plugin.loaded_artifact_ids : [];
      const selectedId = enabled[0] || imported[0] || loadedArtifacts[0] || "";
      const artifact = (plugin.artifacts || []).find((item) => item.artifact_id === selectedId);
      const source = plugin.module_type === "orchestration"
        ? (artifact?.name || "未导入")
        : artifact?.source === "development" ? "开发实现" : artifact?.version ? `正式版 v${artifact.version}` : "未导入";
      const loaded = Array.isArray(plugin.loaded_artifact_ids) && plugin.loaded_artifact_ids.includes(selectedId);
      const loadStatus = plugin.module_type === "orchestration"
        ? artifact?.load_state === "failed"
          ? `装配失败：${artifact.load_reason || "修复模块后重新加载 Web。"}`
          : loaded
            ? (enabled.includes(selectedId) ? "入口已装配，尚未接入任务阶段" : "当前仍已装配，待重载卸载（未接入阶段）")
            : enabled.includes(selectedId)
              ? "已启用，待 Web 重载装配入口"
              : "未装配入口"
        : "";
      const item = document.createElement("div");
      item.className = "workstation-status-row plugin-lifecycle-status-row";
      const copy = document.createElement("div");
      copy.className = "workstation-status-copy";
      const title = document.createElement("strong");
      const detail = document.createElement("span");
      const tone = plugin.module_type === "orchestration" && artifact?.load_state === "failed"
        ? "warning"
        : enabled.length ? "success" : "warning";
      detail.className = `workstation-status-detail workstation-status-detail-${tone}`;
      title.textContent = "当前插件状态";
      detail.textContent = plugin.module_type === "orchestration"
        ? `${source} · ${enabled.length ? "已启用" : "未启用"} · ${loadStatus || "未装配入口"}。`
        : `插件版本：${plugin.name} · ${source} · 导入状态：${imported.includes(selectedId) ? "已导入" : "未导入"} · 启用状态：${enabled.length ? "已启用" : "未启用"}${loadStatus ? ` · 加载状态：${loadStatus}` : ""}。`;
      copy.append(title, detail);
      item.append(copy);
      target.querySelector(".plugin-lifecycle-status-row")?.remove();
      target.prepend(item);
    };
    const renderVersionPicker = (plugin, picker) => {
      if (!(picker instanceof HTMLSelectElement)) return;
      const enabled = Array.isArray(plugin.enabled_artifact_ids) ? plugin.enabled_artifact_ids : [];
      const imported = Array.isArray(plugin.imported_artifact_ids) ? plugin.imported_artifact_ids : [];
      const artifacts = (plugin.artifacts || []).filter((artifact) => imported.includes(artifact.artifact_id) && artifact.available);
      picker.replaceChildren();
      artifacts.forEach((artifact) => {
        const option = document.createElement("option");
        option.value = artifact.artifact_id;
        option.textContent = `${plugin.name} · ${artifact.source === "development" ? "开发实现" : artifact.version ? `正式版 v${artifact.version}` : artifact.name}`;
        option.dataset.description = artifact.source === "development"
          ? "使用仓库固定的开发实现；仅影响之后新建的模块操作。"
          : `使用正式插件包 v${artifact.version || "未知版本"}；仅影响之后新建的模块操作。`;
        picker.append(option);
      });
      picker.value = enabled[0] || artifacts[0]?.artifact_id || "";
      picker.disabled = busy || enabled.length === 0 || artifacts.length === 0;
    };
    async function load() {
      try { data = await request("/api/plugins"); render(); feedback(""); } catch (error) { feedback(error.message, true); }
    }
    void load();
  };

  window.initializeWorkspacePluginLifecycle = initialize;
  initialize();
})();
