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
    const statusTargets = {
      "codex-runtime": document.getElementById("runtime-plugin-status"),
      "weixin-orchestration": document.querySelector(".workspace-task-orchestration-list"),
      deliveryline: document.getElementById("deliveryline-plugin-status"),
    };
    const deliverylineVersion = document.getElementById("deliveryline-plugin-version");
    if (!list && !Object.values(statusTargets).some((target) => target instanceof HTMLElement)) return;
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
    if (deliverylineVersion instanceof HTMLSelectElement) {
      deliverylineVersion.addEventListener("change", () => {
        const artifactId = deliverylineVersion.value;
        if (!artifactId) return;
        void perform(() => request("/api/plugins/deliveryline/enabled", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ artifact_id: artifactId, enabled: true }),
        }));
      });
    }
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
      detail.textContent = unavailable
        ? (artifact.reason || "该插件制品当前不可用；可恢复制品后继续使用，或移除该记录。")
        : (artifact.description || "该版本的能力说明暂不可用。");
      copy.append(title, detail);
      const actions = document.createElement("span");
      actions.className = "runtime-module-row-actions";
      if (!imported) actions.append(button("导入", "button-secondary", () => void perform(() => request(`/api/plugins/${encodeURIComponent(plugin.plugin_id)}/imports`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ artifact_id: artifact.artifact_id }) }), { refreshNavigation: true })));
      else {
        const toggle = button(enabled ? "禁用" : "启用", "button-secondary", () => void perform(() => request(`/api/plugins/${encodeURIComponent(plugin.plugin_id)}/enabled`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ artifact_id: artifact.artifact_id, enabled: !enabled }) })));
        if (!enabled && unavailable) {
          toggle.disabled = true;
          toggle.title = "插件制品当前不可用，无法启用。";
        }
        actions.append(toggle);
        actions.append(button("移除", "button-danger", () => {
          const remove = () => perform(
            () => request(`/api/plugins/${encodeURIComponent(plugin.plugin_id)}/imports/${encodeURIComponent(artifact.artifact_id)}`, { method: "DELETE" }),
            { refreshNavigation: true, propagate: true },
          );
          if (typeof window.showConfirmationDialog !== "function") { void remove(); return; }
          void window.showConfirmationDialog({
            title: "移除插件",
            description: "移除会停止该实现用于后续新任务；已受理任务按原有快照与恢复规则继续处理。",
            details: [{ label: "插件", value: `${plugin.name} · ${artifact.name}` }],
            confirmLabel: "移除",
            pendingLabel: "正在移除…",
            errorMessage: "插件未能移除。",
            onConfirm: remove,
          });
        }));
      }
      item.append(copy, actions);
      return item;
    };
    const render = () => {
      const plugins = Array.isArray(data?.plugins) ? data.plugins : [];
      if (list) list.replaceChildren(...plugins.flatMap((plugin) => (plugin.artifacts || []).map((artifact) => row(plugin, artifact))));
      plugins.forEach((plugin) => {
        renderStatus(plugin, statusTargets[plugin.plugin_id]);
        if (plugin.plugin_id === "deliveryline") renderDeliverylineVersion(plugin);
      });
    };
    const renderStatus = (plugin, target) => {
      if (!(target instanceof HTMLElement)) return;
      const enabled = Array.isArray(plugin.enabled_artifact_ids) ? plugin.enabled_artifact_ids : [];
      const imported = Array.isArray(plugin.imported_artifact_ids) ? plugin.imported_artifact_ids : [];
      const selectedId = enabled[0] || imported[0] || "";
      const artifact = (plugin.artifacts || []).find((item) => item.artifact_id === selectedId);
      const source = artifact?.source === "development" ? "开发实现" : artifact?.version ? `正式版 v${artifact.version}` : "未导入";
      const item = document.createElement("div");
      item.className = "workstation-status-row plugin-lifecycle-status-row";
      const copy = document.createElement("div");
      copy.className = "workstation-status-copy";
      const title = document.createElement("strong");
      const detail = document.createElement("span");
      detail.className = `workstation-status-detail workstation-status-detail-${enabled.length ? "success" : "warning"}`;
      title.textContent = "当前插件状态";
      detail.textContent = `插件版本：${plugin.name} · ${source} · 导入状态：${selectedId ? "已导入" : "未导入"} · 启用状态：${enabled.length ? "已启用" : "未启用"}。`;
      copy.append(title, detail);
      item.append(copy);
      if (target.classList.contains("workspace-task-orchestration-list") || target.id === "deliveryline-plugin-status") {
        target.querySelector(".plugin-lifecycle-status-row")?.remove();
        target.prepend(item);
      } else {
        target.replaceChildren(item);
      }
    };
    const renderDeliverylineVersion = (plugin) => {
      if (!(deliverylineVersion instanceof HTMLSelectElement)) return;
      const enabled = Array.isArray(plugin.enabled_artifact_ids) ? plugin.enabled_artifact_ids : [];
      const imported = Array.isArray(plugin.imported_artifact_ids) ? plugin.imported_artifact_ids : [];
      const artifacts = (plugin.artifacts || []).filter(
        (artifact) => imported.includes(artifact.artifact_id) && artifact.available,
      );
      const selectedId = enabled[0] || "";
      deliverylineVersion.replaceChildren();
      artifacts.forEach((artifact) => {
        const option = document.createElement("option");
        option.value = artifact.artifact_id;
        option.textContent = `${plugin.name} · ${artifact.source === "development" ? "开发实现" : artifact.version ? `正式版 v${artifact.version}` : artifact.name}`;
        deliverylineVersion.append(option);
      });
      deliverylineVersion.value = selectedId || artifacts[0]?.artifact_id || "";
      deliverylineVersion.disabled = busy || enabled.length === 0 || artifacts.length === 0;
    };
    async function load() {
      try { data = await request("/api/plugins"); render(); feedback(""); } catch (error) { feedback(error.message, true); }
    }
    void load();
  };

  window.initializeWorkspacePluginLifecycle = initialize;
  initialize();
})();
