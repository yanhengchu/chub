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
const otherSessionActive = { status: "running", activity: "working" };
const unauthorized = { status: 401, retryable: false };
const missing = { code: "codex_session_not_found", retryable: false };
const serverError = { status: 503, retryable: true };
const networkError = new Error("network unavailable");
const transportError = { transport: true, retryable: true };
Object.defineProperty(globalThis, "sessionStorage", {
  configurable: true,
  get: () => { throw new Error("storage blocked"); },
});
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: { getItem: () => null },
});
const result = {
  reconnectErrors: {
    knownRestart: core.shouldSuppressReconnectError({
      loadErrors: [transportError],
      restartPending: true,
      failureCount: 1,
    }),
    withinGrace: core.shouldSuppressReconnectError({
      loadErrors: [transportError],
      failureCount: 3,
    }),
    afterGrace: core.shouldSuppressReconnectError({
      loadErrors: [transportError],
      failureCount: 4,
    }),
    businessError: core.shouldSuppressReconnectError({
      loadErrors: [unauthorized],
      restartPending: true,
      failureCount: 1,
    }),
    serverErrorDuringRestart: core.shouldSuppressReconnectError({
      loadErrors: [serverError],
      restartPending: true,
      failureCount: 1,
    }),
  },
  pageSizes: [
    core.readPageSize(),
    ...[null, "5", "10", "20"].map((value) => core.readPageSize({
      getItem: () => value,
    })),
    core.readPageSize({ getItem: () => { throw new Error("blocked"); } }),
  ],
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
  otherSessionActive: core.shouldPoll({
    loadFailed: false,
    activeInteraction: false,
    session,
    sessions: [session, otherSessionActive],
  }),
  allSessionsIdle: core.shouldPoll({
    loadFailed: false,
    activeInteraction: false,
    session,
    sessions: [session, { status: "stopped", activity: "idle" }],
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
    { id: "unassigned-other", created_at: "2026-08-11T00:00:00Z", session_mode: "quick", weixin_session_slot: null },
    { id: "slot-nine", created_at: "2026-08-12T00:00:00Z", session_mode: "quick", weixin_session_slot: 9 },
    { id: "current", created_at: "2026-08-14T00:00:00Z", session_mode: "quick", weixin_session_slot: null },
    { id: "slot-two", created_at: "2026-08-13T00:00:00Z", session_mode: "quick", weixin_session_slot: 2 },
    { id: "invalid-slot", created_at: "2026-08-10T00:00:00Z", session_mode: "quick", weixin_session_slot: 10 },
  ]).map((session) => session.id),
  switcherLabels: Array.from(core.sessionSwitcherLabels(core.sessionSwitcherEntries([
    { id: "translation", created_at: "2026-08-15T00:00:00Z", session_mode: "quick", workspace_id: "weixin-translation", weixin_session_slot: null },
    { id: "unassigned-other", created_at: "2026-08-11T00:00:00Z", session_mode: "quick", weixin_session_slot: null },
    { id: "slot-nine", created_at: "2026-08-12T00:00:00Z", session_mode: "quick", weixin_session_slot: 9 },
    { id: "current", created_at: "2026-08-14T00:00:00Z", session_mode: "quick", weixin_session_slot: null },
    { id: "slot-two", created_at: "2026-08-13T00:00:00Z", session_mode: "quick", weixin_session_slot: 2 },
    { id: "invalid-slot", created_at: "2026-08-10T00:00:00Z", session_mode: "quick", weixin_session_slot: 10 },
  ])).entries()),
  firstSessionAfterArchive: core.firstSessionAfterArchive([
    { id: "older", created_at: "2026-08-12T00:00:00Z", session_mode: "quick" },
    { id: "archived", created_at: "2026-08-14T00:00:00Z", session_mode: "quick" },
    { id: "newest", created_at: "2026-08-15T00:00:00Z", session_mode: "quick" },
  ], "archived").id,
  noSessionAfterArchive: core.firstSessionAfterArchive([
    { id: "archived", created_at: "2026-08-14T00:00:00Z", session_mode: "quick" },
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
  errorMessages: [
    core.formatErrorMessage({ message: "CLI failed", source: "runtime" }, "fallback"),
    core.formatErrorMessage({ message: "Worker failed", source: "chub" }, "fallback"),
    core.formatErrorMessage({ message: "Unknown failure" }, "fallback"),
  ],
  currentSlottedEntries: core.sessionSwitcherEntries([
    { id: "current", created_at: "2026-08-14T00:00:00Z", session_mode: "quick", weixin_session_slot: 3 },
    { id: "unassigned", created_at: "2026-08-13T00:00:00Z", session_mode: "quick", weixin_session_slot: null },
    { id: "slot-one", created_at: "2026-08-12T00:00:00Z", session_mode: "quick", weixin_session_slot: 1 },
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
        "reconnectErrors": {
            "knownRestart": True,
            "withinGrace": True,
            "afterGrace": False,
            "businessError": False,
            "serverErrorDuringRestart": True,
        },
        "pageSizes": [5, 5, 5, 10, 5, 5],
        "delays": [1500, 1500, 3000, 6000, 10000, 10000],
        "unauthorized": False,
        "missing": False,
        "serverError": True,
        "networkError": True,
        "activeDespitePermanentError": False,
        "notificationPending": True,
        "restartPending": True,
        "otherSessionActive": True,
        "allSessionsIdle": False,
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
            "permissionMode": None,
            "model": None,
            "reasoningEffort": None,
        },
        "invalidCreationPreferences": {
            "permissionMode": None,
            "model": None,
            "reasoningEffort": None,
        },
        "retriesKnownPreferenceError": True,
        "skipsUnrelatedCreationError": False,
        "errorMessages": [
            "Codex CLI（上游 Runtime）：CLI failed",
            "Chub：Worker failed",
            "Unknown failure",
        ],
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
def test_quick_interaction_client_marks_transport_failures_for_reconnect_handling() -> None:
    program = """
global.fetch = async () => {
  throw new TypeError("Failed to fetch");
};
const core = require(process.argv[1]);
core.request("/api/codex/sessions").then(() => {
  process.exitCode = 1;
}).catch((error) => {
  process.stdout.write(JSON.stringify({
    code: error.code,
    message: error.message,
    retryable: error.retryable,
    transport: error.transport,
  }));
});
"""
    result = subprocess.run(
        [NODE, "-e", program, str(CORE_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "code": "chub_connection_lost",
        "message": "连接 Chub 失败，正在重试。",
        "retryable": True,
        "transport": True,
    }


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_quick_interaction_usage_blocks_external_and_unknown_writers() -> None:
    program = """
const core = require(process.argv[1]);
const external = {
  status: "stopped",
  activity: "idle",
  usage: { owner: "external", phase: "unknown" },
};
const unknown = {
  status: "stopped",
  activity: "idle",
  usage: { owner: "unknown", phase: "unknown" },
};
process.stdout.write(JSON.stringify({
  externalStatus: core.sessionSwitcherStatus(external),
  unknownStatus: core.sessionSwitcherStatus(unknown),
  externalSubmit: core.submissionBlockReason({
    session: external,
    activeInteraction: false,
    promptLength: 4,
  }),
  unknownSubmit: core.submissionBlockReason({
    session: unknown,
    activeInteraction: false,
    promptLength: 4,
  }),
}));
"""
    result = subprocess.run(
        [NODE, "-e", program, str(CORE_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "externalStatus": "其他应用占用",
        "unknownStatus": "状态未知",
        "externalSubmit": "This is open in another app, close it there to continue here.",
        "unknownSubmit": "无法确认 Session 占用状态，请刷新后重试。",
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
  await client.stopSession();
  await client.deleteSession();
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
        "/api/codex/sessions/session%2Fone/stop",
        "/api/codex/sessions/session%2Fone",
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
      quick_creation: { available: true, reason: null },
      workspaces: [{ id: "chub", name: "Chub", path: "/workspace/chub", available: true }],
      sessions: [
        { id: "terminal", session_mode: "terminal" },
        { id: "session-1", session_mode: "quick" },
        { id: "session-2", session_mode: "quick" },
      ],
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
            "session": {"id": "session-2", "session_mode": "quick"},
            "sessions": [{"id": "session-1", "session_mode": "quick"}, {"id": "session-2", "session_mode": "quick"}],
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
        "session": {"id": "session-2", "session_mode": "quick"},
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
        : { available: true, workspaces: [], sessions: [{ id: "ordinary", session_mode: "quick" }] },
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
    assert behavior["context"]["sessions"] == [{"id": "ordinary", "session_mode": "quick"}]
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
                "session_mode": "quick",
            },
        },
        "session": {"id": "new-session"},
    }
