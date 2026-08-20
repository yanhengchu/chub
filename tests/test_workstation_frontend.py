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
  textContent: "",
  addEventListener() {},
});
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
renderQuickWorker({ state: "ready", message: "ready", can_restart: true,
  operation: { operation_id: "current", status: "succeeded", message: "done" } });
const workerSucceeded = reloads;

renderSystemUpgrade({ state: "available", message: "ready",
  operation: { operation_id: "old", status: "succeeded" } });
const upgradeHistorical = reloads;
systemUpgradeReloadOperationId = "current";
renderSystemUpgrade({ state: "available", message: "ready",
  operation: { operation_id: "other", status: "succeeded" } });
renderSystemUpgrade({ state: "failed", message: "failed",
  operation: { operation_id: "current", status: "failed" } });
const upgradeNonMatching = reloads;
renderSystemUpgrade({ state: "available", message: "ready",
  operation: { operation_id: "current", status: "succeeded" } });
renderSystemUpgrade({ state: "available", message: "ready",
  operation: { operation_id: "current", status: "succeeded" } });
const upgradeSucceeded = reloads;

process.stdout.write(JSON.stringify({
  workerHistorical,
  workerFailed,
  workerFailureCleared,
  workerAfterFailure,
  workerSucceeded,
  upgradeHistorical,
  upgradeNonMatching,
  upgradeSucceeded,
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
        "upgradeHistorical": 1,
        "upgradeNonMatching": 1,
        "upgradeSucceeded": 2,
    }
