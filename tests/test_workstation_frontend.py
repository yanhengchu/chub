import json
import shutil
import subprocess
from pathlib import Path

import pytest


NODE = shutil.which("node")
WORKSTATION_SCRIPT = (
    Path(__file__).parents[1]
    / "app"
    / "web"
    / "static"
    / "js"
    / "features"
    / "workstation.js"
)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_maintenance_reload_only_fires_once_for_the_requested_operation() -> None:
    program = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const element = () => ({
  disabled: false,
  hidden: false,
  textContent: "",
  addEventListener() {},
  replaceChildren(...children) { this.children = children; },
});
globalThis.document = {
  createDocumentFragment() {
    return {
      append(...children) { this.children = [...(this.children || []), ...children]; },
    };
  },
  createElement() {
    return {
      className: "",
      textContent: "",
      classList: { add() {} },
      setAttribute() {},
      append() {},
    };
  },
};
const elementCache = {};
globalThis.elements = new Proxy({}, {
  get(_target, property) {
    elementCache[property] ||= element();
    return elementCache[property];
  },
});
globalThis.window = {
  clearTimeout() {},
  setTimeout() { return 1; },
};
const sessionValues = {};
globalThis.sessionStorage = {
  getItem(key) { return sessionValues[key] || null; },
  setItem(key, value) { sessionValues[key] = String(value); },
  removeItem(key) { delete sessionValues[key]; },
};
globalThis.hasProtectedAccess = () => true;
globalThis.hubRestartInProgress = false;
globalThis.setBadge = () => {};
globalThis.setMessage = () => {};
globalThis.apiFetch = async () => ({});
globalThis.handleAccessError = () => false;
globalThis.loadStatus = async () => {};
globalThis.loadAutomationEnvironment = async () => {};
globalThis.loadOpenClaw = async () => {};
globalThis.showConfirmationDialog = async () => {};
globalThis.showMaintenanceCompletion = (element, message) => {
  element.textContent = `${message} 浏览器将在稍后自动刷新页面。`;
};
let reloads = 0;
globalThis.reloadDashboardAfterMaintenance = () => { reloads += 1; };

eval(`${source}\n
renderQuickWorker({ state: "ready", message: "ready", can_restart: true,
  operation: { operation_id: "old", status: "succeeded", message: "done" } });
const workerHistorical = reloads;
quickWorkerReloadOperationId = "failed";
renderQuickWorker({ state: "unavailable", message: "failed", can_restart: false,
  operation: { operation_id: "failed", status: "failed", message: "failed" } });
const workerFailed = reloads;
const workerFailureCleared = quickWorkerReloadOperationId === null;
renderQuickWorker({ state: "ready", message: "ready", can_restart: true,
  operation: { operation_id: "failed", status: "succeeded", message: "done" } });
const workerAfterFailure = reloads;
quickWorkerReloadOperationId = "current";
renderQuickWorker({ state: "ready", message: "ready", can_restart: true,
  operation: { operation_id: "other", status: "succeeded", message: "done" } });
renderQuickWorker({ state: "ready", message: "ready", can_restart: true,
  operation: { operation_id: "current", status: "succeeded", message: "done" } });
const workerCompletionDetail = elementCache.quickWorkerDetail.textContent;
renderQuickWorker({ state: "ready", message: "ready", can_restart: true,
  operation: { operation_id: "current", status: "succeeded", message: "done" } });
const workerSucceeded = reloads;

systemUpgradeState = {
  state: "failed",
  message: "failed",
  can_start: true,
  writes_blocked: true,
  operation: { operation_id: "failed-upgrade", status: "failed" },
};
quickWorkerState = { state: "ready", message: "ready", can_restart: true };
syncCoreMaintenanceControls();
const failedUpgradeControls = {
  restartHub: elementCache.restartHub.disabled,
  quickWorkerRestart: elementCache.quickWorkerRestart.disabled,
  systemUpgradeStart: elementCache.systemUpgradeStart.disabled,
};
quickWorkerState = { state: "unavailable", message: "暂不可用" };
const unknownTaskDetail = systemUpgradeImpactDetails()[0].value;
systemUpgradeState.operation.status = "started";
syncCoreMaintenanceControls();
const activeUpgradeControls = {
  restartHub: elementCache.restartHub.disabled,
  quickWorkerRestart: elementCache.quickWorkerRestart.disabled,
  systemUpgradeStart: elementCache.systemUpgradeStart.disabled,
};

renderSystemUpgrade({ state: "available", message: "ready",
  plan: { plan_id: "runtime-recovery" },
  operation: { operation_id: "old", status: "succeeded" } });
const recoveryOnlyButton = elementCache.systemUpgradeStart.textContent;
const upgradeHistorical = reloads;
systemUpgradeReloadOperationId = "current";
renderSystemUpgrade({ state: "available", message: "ready",
  operation: { operation_id: "other", status: "succeeded" } });
renderSystemUpgrade({ state: "failed", message: "failed",
  operation: { operation_id: "current", status: "failed" } });
const upgradeNonMatching = reloads;
renderSystemUpgrade({ state: "available", message: "ready",
  operation: { operation_id: "current", status: "succeeded", message: "ready" } });
const upgradeCompletionDetail = elementCache.systemUpgradeDetail.textContent;
renderSystemUpgrade({ state: "available", message: "ready",
  operation: { operation_id: "current", status: "succeeded", message: "ready" } });
const upgradeSucceeded = reloads;

rememberSystemUpgradeReload("persisted");
systemUpgradeReloadOperationId = readSystemUpgradeReloadOperationId();
renderSystemUpgrade({ state: "available", message: "ready",
  operation: { operation_id: "persisted", status: "succeeded", message: "ready" } });
const restoredUpgradeReload = reloads;
const restoredUpgradeMarker = sessionStorage.getItem(SYSTEM_UPGRADE_RELOAD_KEY);

process.stdout.write(JSON.stringify({
  workerHistorical,
  workerFailed,
  workerFailureCleared,
  workerAfterFailure,
  workerSucceeded,
  workerCompletionDetail,
  failedUpgradeControls,
  activeUpgradeControls,
  unknownTaskDetail,
  recoveryOnlyButton,
  upgradeHistorical,
  upgradeNonMatching,
  upgradeSucceeded,
  upgradeCompletionDetail,
  restoredUpgradeReload,
  restoredUpgradeMarker,
}));`);
"""
    result = subprocess.run(
        [NODE, "-e", program, str(WORKSTATION_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "workerHistorical": 0,
        "workerFailed": 0,
        "workerFailureCleared": True,
        "workerAfterFailure": 0,
        "workerSucceeded": 1,
        "workerCompletionDetail": "done 浏览器将在稍后自动刷新页面。",
        "failedUpgradeControls": {
            "restartHub": False,
            "quickWorkerRestart": False,
            "systemUpgradeStart": False,
        },
        "activeUpgradeControls": {
            "restartHub": True,
            "quickWorkerRestart": True,
            "systemUpgradeStart": True,
        },
        "unknownTaskDetail": "任务数量暂无法确认；恢复流程会按固定边界清理。",
        "recoveryOnlyButton": "运行态恢复",
        "upgradeHistorical": 1,
        "upgradeNonMatching": 1,
        "upgradeSucceeded": 2,
        "upgradeCompletionDetail": "状态：已完成。ready 浏览器将在稍后自动刷新页面。",
        "restoredUpgradeReload": 3,
        "restoredUpgradeMarker": None,
    }
