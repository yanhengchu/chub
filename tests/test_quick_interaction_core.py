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
  restartPending: core.shouldPoll({
    loadFailed: false,
    activeInteraction: false,
    restartPending: true,
    session,
  }),
  submission: {
    allowed: core.canSubmit({ prompt: "执行任务", session, blocked: false }),
    blank: core.canSubmit({ prompt: "   ", session, blocked: false }),
    missingSession: core.canSubmit({ prompt: "执行任务", session: null, blocked: false }),
    blocked: core.canSubmit({ prompt: "执行任务", session, blocked: true }),
  },
  switcherStatuses: [
    { status: "stopped", activity: "idle", permission_mode: "full-access" },
    { status: "running", activity: "working", permission_mode: "full-access" },
    { status: "error", activity: "unknown", permission_mode: "full-access" },
    { status: "stopped", activity: "idle", permission_mode: "ask" },
    { status: "running", activity: "unknown", permission_mode: "full-access" },
  ].map(core.sessionSwitcherStatus),
  switcherEntries: core.sessionSwitcherEntries([
    { id: "translation", created_at: "2026-08-15T00:00:00Z", workspace_id: "weixin-translation", weixin_session_slot: null },
    { id: "unassigned-other", created_at: "2026-08-11T00:00:00Z", weixin_session_slot: null },
    { id: "slot-nine", created_at: "2026-08-12T00:00:00Z", weixin_session_slot: 9 },
    { id: "current", created_at: "2026-08-14T00:00:00Z", weixin_session_slot: null },
    { id: "slot-two", created_at: "2026-08-13T00:00:00Z", weixin_session_slot: 2 },
    { id: "invalid-slot", created_at: "2026-08-10T00:00:00Z", weixin_session_slot: 10 },
  ]).map((session) => session.id),
  switcherLabels: Array.from(core.sessionSwitcherLabels(core.sessionSwitcherEntries([
    { id: "translation", created_at: "2026-08-15T00:00:00Z", workspace_id: "weixin-translation", weixin_session_slot: null },
    { id: "unassigned-other", created_at: "2026-08-11T00:00:00Z", weixin_session_slot: null },
    { id: "slot-nine", created_at: "2026-08-12T00:00:00Z", weixin_session_slot: 9 },
    { id: "current", created_at: "2026-08-14T00:00:00Z", weixin_session_slot: null },
    { id: "slot-two", created_at: "2026-08-13T00:00:00Z", weixin_session_slot: 2 },
    { id: "invalid-slot", created_at: "2026-08-10T00:00:00Z", weixin_session_slot: 10 },
  ])).entries()),
  firstSessionAfterArchive: core.firstSessionAfterArchive([
    { id: "older", created_at: "2026-08-12T00:00:00Z" },
    { id: "archived", created_at: "2026-08-14T00:00:00Z" },
    { id: "newest", created_at: "2026-08-15T00:00:00Z" },
  ], "archived").id,
  noSessionAfterArchive: core.firstSessionAfterArchive([
    { id: "archived", created_at: "2026-08-14T00:00:00Z" },
  ], "archived"),
  creationPreferences: core.readSessionCreationPreferences({
    getItem: (key) => ({
      "hub.codexDefaultPermission.v1": "auto-review",
      "hub.codexDefaultModel.v1": "gpt-test",
      "hub.codexDefaultReasoningEffort.v1": "high",
    })[key] || null,
  }),
  invalidCreationPreferences: core.readSessionCreationPreferences({
    getItem: (key) => key === "hub.codexDefaultPermission.v1" ? "invalid" : null,
  }),
  retriesKnownPreferenceError: core.shouldRetrySessionCreationWithDefaults(
    { code: "codex_model_unavailable" },
    { model: "gpt-test", reasoningEffort: null },
  ),
  skipsUnrelatedCreationError: core.shouldRetrySessionCreationWithDefaults(
    { code: "codex_workspace_unavailable" },
    { model: "gpt-test", reasoningEffort: null },
  ),
  currentSlottedEntries: core.sessionSwitcherEntries([
    { id: "current", created_at: "2026-08-14T00:00:00Z", weixin_session_slot: 3 },
    { id: "unassigned", created_at: "2026-08-13T00:00:00Z", weixin_session_slot: null },
    { id: "slot-one", created_at: "2026-08-12T00:00:00Z", weixin_session_slot: 1 },
  ]).map((session) => session.id),
  navigationModes: {
    switchSession: core.sessionNavigationMode({}),
    currentSession: core.sessionNavigationMode({ current: true }),
    commandClick: core.sessionNavigationMode({ metaKey: true }),
    controlClickCurrent: core.sessionNavigationMode({ current: true, ctrlKey: true }),
    shiftClick: core.sessionNavigationMode({ shiftKey: true }),
    middleClick: core.sessionNavigationMode({ button: 1 }),
    rightClick: core.sessionNavigationMode({ button: 2 }),
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
        "restartPending": True,
        "submission": {
            "allowed": True,
            "blank": False,
            "missingSession": False,
            "blocked": False,
        },
        "switcherStatuses": ["待输入", "执行中", "异常", "需终端", "状态未知"],
        "switcherEntries": [
            "current",
            "slot-two",
            "slot-nine",
            "unassigned-other",
            "invalid-slot",
        ],
        "switcherLabels": [
            ["current", "S"],
            ["slot-two", "S2"],
            ["slot-nine", "S9"],
            ["unassigned-other", "S"],
            ["invalid-slot", "S"],
        ],
        "firstSessionAfterArchive": "newest",
        "noSessionAfterArchive": None,
        "creationPreferences": {
            "permissionMode": "auto-review",
            "model": "gpt-test",
            "reasoningEffort": "high",
        },
        "invalidCreationPreferences": {
            "permissionMode": "full-access",
            "model": None,
            "reasoningEffort": None,
        },
        "retriesKnownPreferenceError": True,
        "skipsUnrelatedCreationError": False,
        "currentSlottedEntries": ["current", "unassigned", "slot-one"],
        "navigationModes": {
            "switchSession": "replace",
            "currentSession": "ignore",
            "commandClick": "new-tab",
            "controlClickCurrent": "new-tab",
            "shiftClick": "new-tab",
            "middleClick": "new-tab",
            "rightClick": "default",
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
  await client.renameSession("新标题");
  await client.archiveSession();
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
        "/api/codex/sessions/session%2Fone/title",
        "/api/codex/sessions/session%2Fone/archive",
    ]


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_quick_interaction_client_loads_session_switcher_context() -> None:
    program = """
const requests = [];
global.fetch = async (path) => {
  requests.push(String(path));
  return ({
  ok: true,
  status: 200,
  json: async () => ({
    success: true,
    data: {
      available: true,
      unavailable_reason: null,
      workspaces: [{ id: "chub", name: "Chub", path: "/workspace/chub", available: true }],
      sessions: [{ id: "session-1" }, { id: "session-2" }],
    },
  }),
  });
};
const core = require(process.argv[1]);
const client = core.createClient({ token: "", sessionId: "session-2" });
(async () => {
  const context = await client.loadSessionContext();
  const session = await client.loadSession();
  process.stdout.write(JSON.stringify({ context, session, requests }));
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

    behavior = json.loads(result.stdout)
    assert behavior == {
        "context": {
            "session": {"id": "session-2"},
            "sessions": [{"id": "session-1"}, {"id": "session-2"}],
            "available": True,
            "unavailableReason": "",
            "workspaces": [
                {
                    "id": "chub",
                    "name": "Chub",
                    "path": "/workspace/chub",
                    "available": True,
                }
            ],
        },
        "session": {"id": "session-2"},
        "requests": ["/api/codex/sessions", "/api/codex/sessions"],
    }


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_quick_interaction_context_reads_hidden_current_session_directly() -> None:
    program = """
const requests = [];
global.fetch = async (path) => {
  requests.push(String(path));
  const detail = String(path).includes("translation-session");
  return {
    ok: true,
    status: 200,
    json: async () => ({
      success: true,
      data: detail
        ? { id: "translation-session", workspace_id: "weixin-translation" }
        : { available: true, workspaces: [], sessions: [{ id: "ordinary" }] },
    }),
  };
};
const core = require(process.argv[1]);
const client = core.createClient({ token: "", sessionId: "translation-session" });
(async () => {
  const context = await client.loadSessionContext();
  process.stdout.write(JSON.stringify({ context, requests }));
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

    behavior = json.loads(result.stdout)
    assert behavior["context"]["session"]["id"] == "translation-session"
    assert behavior["context"]["sessions"] == [{"id": "ordinary"}]
    assert behavior["requests"] == [
        "/api/codex/sessions",
        "/api/codex/sessions/translation-session",
    ]


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_quick_interaction_client_creates_session_with_selected_defaults() -> None:
    program = """
let captured = null;
global.fetch = async (path, options = {}) => {
  captured = {
    path: String(path),
    method: options.method,
    body: JSON.parse(options.body),
  };
  return {
    ok: true,
    status: 200,
    json: async () => ({ success: true, data: { id: "new-session" } }),
  };
};
const core = require(process.argv[1]);
const client = core.createClient({ token: "", sessionId: "current-session" });
(async () => {
  const session = await client.createSession({
    workspaceId: "workspace/one",
    permissionMode: "auto-review",
    model: "gpt-test",
    reasoningEffort: "high",
  });
  process.stdout.write(JSON.stringify({ captured, session }));
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

    assert json.loads(result.stdout) == {
        "captured": {
            "path": "/api/codex/sessions",
            "method": "POST",
            "body": {
                "workspace_id": "workspace/one",
                "permission_mode": "auto-review",
                "model": "gpt-test",
                "reasoning_effort": "high",
            },
        },
        "session": {"id": "new-session"},
    }
