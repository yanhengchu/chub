import json
import shutil
import subprocess
from pathlib import Path

import pytest


NODE = shutil.which("node")
CORE_SCRIPT = (
    Path(__file__).parents[1]
    / "app"
    / "web"
    / "static"
    / "quick_interactions_core.js"
)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_quick_interaction_polling_retries_only_recoverable_failures() -> None:
    program = """
const core = require(process.argv[1]);
const session = { status: "stopped", activity: "idle" };
const unauthorized = { status: 401, retryable: false };
const missing = { code: "codex_session_not_found", retryable: false };
const serverError = { status: 503, retryable: true };
const networkError = new Error("network unavailable");
Object.defineProperty(globalThis, "sessionStorage", {
  configurable: true,
  get: () => { throw new Error("storage blocked"); },
});
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: { getItem: (key) => key === "hub.savedToken" ? "saved-token" : null },
});
const result = {
  pageSizes: [
    core.readPageSize(),
    ...[null, "5", "10", "20"].map((value) => core.readPageSize({
      getItem: () => value,
    })),
    core.readPageSize({ getItem: () => { throw new Error("blocked"); } }),
  ],
  tokenWithBlockedSessionStorage: core.readToken(),
  delays: [0, 1, 2, 3, 4, 8].map(core.pollDelay),
  unauthorized: core.shouldPoll({
    loadFailed: true,
    loadErrors: [unauthorized],
    activeInteraction: false,
    session,
  }),
  missing: core.shouldPoll({
    loadFailed: true,
    loadErrors: [missing],
    activeInteraction: false,
    session,
  }),
  serverError: core.shouldPoll({
    loadFailed: true,
    loadErrors: [serverError],
    activeInteraction: false,
    session,
  }),
  networkError: core.shouldPoll({
    loadFailed: true,
    loadErrors: [networkError],
    activeInteraction: false,
    session,
  }),
  activeDespitePermanentError: core.shouldPoll({
    loadFailed: true,
    loadErrors: [unauthorized],
    activeInteraction: true,
    session,
  }),
  notificationPending: core.shouldPoll({
    loadFailed: false,
    activeInteraction: false,
    notificationPending: true,
    session,
  }),
  submission: {
    allowed: core.canSubmit({ prompt: "执行任务", session, blocked: false }),
    blank: core.canSubmit({ prompt: "   ", session, blocked: false }),
    missingSession: core.canSubmit({ prompt: "执行任务", session: null, blocked: false }),
    blocked: core.canSubmit({ prompt: "执行任务", session, blocked: true }),
  },
};
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        [NODE, "-e", program, str(CORE_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )

    behavior = json.loads(result.stdout)
    assert behavior == {
        "pageSizes": [5, 5, 5, 10, 5, 5],
        "tokenWithBlockedSessionStorage": "saved-token",
        "delays": [1500, 1500, 3000, 6000, 10000, 10000],
        "unauthorized": False,
        "missing": False,
        "serverError": True,
        "networkError": True,
        "activeDespitePermanentError": False,
        "notificationPending": True,
        "submission": {
            "allowed": True,
            "blank": False,
            "missingSession": False,
            "blocked": False,
        },
    }


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_quick_interaction_client_uses_selected_page_size_and_cursor() -> None:
    program = """
const requests = [];
global.fetch = async (path) => {
  requests.push(String(path));
  return {
    ok: true,
    status: 200,
    json: async () => ({ success: true, data: { tasks: [], total: 0, has_more: false } }),
  };
};
const core = require(process.argv[1]);
const client = core.createClient({ token: "", sessionId: "session/one" });
(async () => {
  await client.listTasks({ limit: 5, order: "task" });
  await client.listTasks({
    limit: 10,
    order: "timeline",
    before: { createdAt: "2026-08-02T10:00:00+08:00", id: "task/one" },
  });
  process.stdout.write(JSON.stringify(requests));
})().catch((error) => {
  process.stderr.write(String(error));
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        [NODE, "-e", program, str(CORE_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )

    requests = json.loads(result.stdout)
    assert requests == [
        "/api/codex/sessions/session%2Fone/quick-interactions?limit=5&order=task",
        "/api/codex/sessions/session%2Fone/quick-interactions?limit=10&order=timeline&before_created_at=2026-08-02T10%3A00%3A00%2B08%3A00&before_id=task%2Fone",
    ]
