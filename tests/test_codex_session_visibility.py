import json
import shutil
import subprocess
from pathlib import Path

import pytest


NODE = shutil.which("node")
SESSIONS_SCRIPT = (
    Path(__file__).parents[1]
    / "app"
    / "web"
    / "static"
    / "js"
    / "features"
    / "codex-sessions.js"
)
QUICK_INTERACTIONS_CORE_SCRIPT = (
    Path(__file__).parents[1]
    / "app"
    / "web"
    / "static"
    / "quick_interactions_core.js"
)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_session_lists_hide_internal_translation_and_mark_mode() -> None:
    program = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const quickCore = require(process.argv[2]);
const sessions = [
  { id: "terminal", created_at: "2026-08-13T00:00:00Z", workspace_id: "chub", session_mode: "terminal" },
  { id: "translation", created_at: "2026-08-15T00:00:00Z", workspace_id: "weixin-translation" },
  { id: "quick", created_at: "2026-08-14T00:00:00Z", workspace_id: "other", session_mode: "quick" },
];
eval(`${source}\n
const ids = (items) => items.map((item) => item.id);
const visibleIds = () => ({
  home: ids(visibleCodexSessions(sessions)),
  quick: ids(quickCore.sessionSwitcherEntries(sessions)),
});
const result = {};
result.visible = visibleIds();
result.displayTitles = [
  codexSessionDisplayTitle({ title: "项目维护", session_mode: "terminal", weixin_session_slot: null }),
  codexSessionDisplayTitle({ title: "微信任务", session_mode: "quick", weixin_session_slot: 3 }),
  codexSessionDisplayTitle({ title: null, session_mode: "quick", weixin_session_slot: null }),
];
result.slotChangeDetected = codexSessionsSignature([
  { id: "quick", session_mode: "quick", weixin_session_slot: 1 },
]) !== codexSessionsSignature([
  { id: "quick", session_mode: "quick", weixin_session_slot: 2 },
]);
process.stdout.write(JSON.stringify(result));
`);
"""
    result = subprocess.run(
        [
            NODE,
            "-e",
            program,
            str(SESSIONS_SCRIPT),
            str(QUICK_INTERACTIONS_CORE_SCRIPT),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "visible": {
            "home": ["quick", "terminal"],
            "quick": ["quick"],
        },
        "displayTitles": [
            "终端 · 项目维护",
            "快速 · S3 · 微信任务",
            "快速 · 未命名 Session",
        ],
        "slotChangeDetected": True,
    }


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_session_usage_presentation_distinguishes_ownership_and_phase() -> None:
    program = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
eval(`${source}
const states = [
  { usage: { owner: "external", phase: "unknown" } },
  { usage: { owner: "unknown", phase: "unknown" } },
  { usage: { owner: "terminal", phase: "running" } },
  { usage: { owner: "terminal", phase: "idle" } },
  { usage: { owner: "terminal", phase: "unknown" } },
  { usage: { owner: "quick_worker", phase: "waiting_result" } },
  { usage: { owner: "quick_worker", phase: "running" } },
  { session_mode: "quick", activity: "idle", usage: { owner: "none", phase: "idle" } },
  { session_mode: "terminal", activity: "idle", usage: { owner: "none", phase: "idle" } },
  { status: "new", activity: "unknown" },
  { status: "error", activity: "unknown" },
  { status: "running", activity: "idle", error: "terminal_failed" },
  { status: "running", activity: "working", activity_source: "none" },
].map(sessionUsagePresentation);
const entryStates = [
  { session_mode: "quick", usage: { owner: "external", phase: "unknown" } },
  { session_mode: "quick", usage: { owner: "unknown", phase: "unknown" } },
  { session_mode: "terminal", usage: { owner: "external", phase: "unknown" } },
  { session_mode: "terminal", usage: { owner: "unknown", phase: "unknown" } },
].map((session) => sessionEntryBlocked(
  session,
  sessionUsagePresentation(session),
));
const stopStates = [
  { usage: { owner: "terminal", phase: "running" } },
  { usage: { owner: "terminal", phase: "idle" } },
  { usage: { owner: "quick_worker", phase: "waiting_result" } },
  { usage: { owner: "none", phase: "idle" } },
].map(sessionStopReady);
const renameStates = [
  { workspace_id: "chub", usage: { owner: "external", phase: "unknown" } },
  { workspace_id: "chub", usage: { owner: "unknown", phase: "unknown" } },
  { workspace_id: "weixin-translation", usage: { owner: "none", phase: "idle" } },
  { workspace_id: "chub", usage: { owner: "quick_worker", phase: "waiting_result" } },
].map((session) => sessionRenameBlockReason(
  session,
  sessionUsagePresentation(session),
));
process.stdout.write(JSON.stringify({ states, entryStates, stopStates, renameStates }));
`);
"""
    result = subprocess.run(
        [NODE, "-e", program, str(SESSIONS_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
      "states": [
        {
            "label": "其他应用 · 正在使用",
            "blocked": True,
            "title": "This is open in another app, close it there to continue here.",
        },
        {
            "label": "占用状态未知 · 请刷新",
            "blocked": True,
            "title": "无法确认 Session 占用状态，请刷新后重试。",
        },
        {"label": "实时终端 · 执行中", "blocked": False, "title": ""},
        {"label": "实时终端 · 等待输入", "blocked": False, "title": ""},
        {"label": "实时终端 · 正在使用", "blocked": False, "title": ""},
        {"label": "快速交互 · 执行中", "blocked": False, "title": ""},
        {"label": "快速交互 · 执行中", "blocked": False, "title": ""},
        {"label": "快速交互 · 待输入", "blocked": False, "title": ""},
        {"label": "实时终端 · 等待输入", "blocked": False, "title": ""},
        {"label": "尚未启动 · 可进入", "blocked": False, "title": ""},
        {"label": "会话异常 · 可重试", "blocked": False, "title": ""},
        {"label": "终端连接异常 · 可重试", "blocked": False, "title": ""},
        {"label": "活动状态未知 · 请刷新", "blocked": False, "title": ""},
      ],
      "entryStates": [False, False, True, True],
      "stopStates": [True, False, True, False],
      "renameStates": [
        "This is open in another app, close it there to continue here.",
        "",
        "内部翻译 Session 标题固定，不支持重命名。",
        "",
      ],
    }
