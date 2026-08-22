import json
import shutil
import subprocess
from pathlib import Path

import pytest


NODE = shutil.which("node")
STATIC_ROOT = Path(__file__).parents[1] / "app" / "web" / "static"
SESSION_SCRIPT = STATIC_ROOT / "quick_interaction_session.js"
TIMELINE_SCRIPT = STATIC_ROOT / "quick_interaction_timeline.js"


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_quick_interaction_session_boundary_builds_read_only_views() -> None:
    program = """
const sessionView = require(process.argv[1]);
const session = {
  id: "session/one",
  title: "  Main Session  ",
  created_at: "2026-08-15T08:00:00Z",
      can_archive: true,
      workspace_id: "chub",
      session_mode: "quick",
      status: "stopped",
  activity: "idle",
  permission_mode: "full-access",
  quick_interaction_running: false,
};
const busySession = {
  ...session,
  quick_interaction_running: true,
  usage: { owner: "quick_worker", phase: "waiting_result" },
};
const externallyUsedSession = {
  ...session,
  usage: { owner: "external", phase: "unknown" },
};
const uncertainSession = {
  ...session,
  usage: { owner: "unknown", phase: "unknown" },
};
const result = {
  preview: sessionView.buildSessionPreview(session),
  ready: sessionView.buildSessionState({
    session,
    activeInteraction: false,
    archivePending: false,
    promptLength: 4,
  }),
  busy: sessionView.buildSessionState({
    session: busySession,
    activeInteraction: true,
    archivePending: false,
    promptLength: 4,
  }),
  externallyUsed: sessionView.buildSessionState({
    session: externallyUsedSession,
    activeInteraction: false,
    archivePending: false,
    promptLength: 4,
  }),
  uncertain: sessionView.buildSessionState({
    session: uncertainSession,
    activeInteraction: false,
    archivePending: false,
    promptLength: 4,
  }),
  creation: sessionView.buildCreationState({
    available: true,
    workspaces: [
      { id: "blocked", available: false },
      { id: "chub", available: true },
    ],
  }, false),
  switcher: sessionView.buildSwitcher({
    sessions: [
      session,
      {
            id: "session-two",
            title: "Other",
            created_at: "2026-08-14T08:00:00Z",
            session_mode: "quick",
            status: "running",
        activity: "working",
        permission_mode: "full-access",
        weixin_session_slot: 2,
      },
    ],
    currentSessionId: session.id,
  }),
  archiveDescription: sessionView.archiveDescription(session),
  stopDescription: sessionView.stopDescription(session),
  url: sessionView.sessionUrl(session.id),
};
process.stdout.write(JSON.stringify(result));
"""
    result = subprocess.run(
        [NODE, "-e", program, str(SESSION_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )

    behavior = json.loads(result.stdout)
    assert behavior["preview"] == {
        "displayTitle": "Main Session",
        "documentTitle": "Main Session · 快速交互",
        "renameAllowed": True,
        "loadingLabel": "正在读取 Session 状态",
    }
    assert behavior["ready"]["busy"] is False
    assert behavior["ready"]["stopReady"] is False
    assert behavior["ready"]["archiveReady"] is True
    assert behavior["ready"]["archiveBusy"] is False
    assert behavior["ready"]["deleteBusy"] is False
    assert behavior["ready"]["submissionReason"] == ""
    assert behavior["busy"]["busy"] is True
    assert behavior["busy"]["stopReady"] is True
    assert behavior["busy"]["archiveBusy"] is True
    assert behavior["busy"]["archiveLabel"] == "Session 当前正在执行，请等待任务结束后再归档。"
    assert behavior["busy"]["submissionReason"] == "当前快速交互正在执行，请等待任务结束。"
    assert behavior["externallyUsed"]["usageBlocked"] is True
    assert behavior["externallyUsed"]["stopReady"] is False
    assert behavior["externallyUsed"]["archiveBusy"] is True
    assert behavior["externallyUsed"]["deleteBusy"] is True
    assert behavior["externallyUsed"]["archiveLabel"] == "This is open in another app, close it there to continue here."
    assert behavior["externallyUsed"]["submissionReason"] == "This is open in another app, close it there to continue here."
    assert behavior["uncertain"]["usageBlocked"] is True
    assert behavior["uncertain"]["archiveBusy"] is False
    assert behavior["uncertain"]["archiveLabel"] == "归档 Session"
    assert behavior["uncertain"]["deleteBusy"] is False
    assert behavior["creation"]["disabled"] is False
    assert behavior["creation"]["label"] == "新建 Session"
    assert [item["id"] for item in behavior["switcher"]["items"]] == [
        "session/one",
        "session-two",
    ]
    assert behavior["switcher"]["items"][0]["current"] is True
    assert behavior["switcher"]["items"][1]["text"] == "S2 · 执行中"
    assert "Chub 页面暂不提供恢复入口" in behavior["archiveDescription"]
    assert "将终止正在执行的快速任务并关闭实时终端" in behavior["stopDescription"]
    assert "在途任务不会恢复" in behavior["stopDescription"]
    assert behavior["url"] == "/codex/session%2Fone/quick-interactions/conversation"


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_quick_interaction_timeline_boundary_merges_and_formats_snapshots() -> None:
    program = """
const timeline = require(process.argv[1]);
const oldTask = {
  id: "old",
  status: "succeeded",
  prompt: "old prompt",
  result: "old result",
  created_at: "2026-08-15T08:00:00Z",
  updated_at: "2026-08-15T08:01:00Z",
  notification_status: "sent",
};
const updatedOldTask = { ...oldTask, result: "updated result" };
const failedTask = {
  id: "failed",
  status: "failed",
  prompt: "run",
  error: "failed result",
  error_source: "runtime",
  created_at: "2026-08-15T09:00:00Z",
  updated_at: "2026-08-15T09:01:00Z",
  notification_status: "failed",
  notification_error: "recipient unavailable",
  deferred_restart_status: "start_failed",
  deferred_restart_updated_at: "2026-08-15T09:02:00Z",
  deferred_restart_notification_status: "failed",
  deferred_restart_notification_error: "route unavailable",
};
const timedOutTask = {
  id: "timed-out",
  status: "timed_out",
  prompt: "slow run",
  error: "timed out",
  error_source: "chub",
  created_at: "2026-08-15T09:10:00Z",
  updated_at: "2026-08-15T09:11:00Z",
};
const unknownFailedTask = {
  ...failedTask,
  id: "unknown-failed",
  error_source: undefined,
};
const merged = timeline.mergeTasks([oldTask], [failedTask, updatedOldTask], 2);
const trimmed = timeline.mergeTasks([oldTask, failedTask], [{
  ...oldTask,
  id: "newest",
  created_at: "2026-08-15T10:00:00Z",
}], 2);
process.stdout.write(JSON.stringify({
  merged: merged.map((task) => ({ id: task.id, result: task.result })),
  trimmed: trimmed.map((task) => task.id),
      old: timeline.buildTaskState(updatedOldTask),
      failed: timeline.buildTaskState(failedTask),
      timedOut: timeline.buildTaskState(timedOutTask),
      unknownFailed: timeline.buildTaskState(unknownFailedTask),
}));
"""
    result = subprocess.run(
        [NODE, "-e", program, str(TIMELINE_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )

    behavior = json.loads(result.stdout)
    assert behavior["merged"] == [
        {"id": "old", "result": "updated result"},
        {"id": "failed"},
    ]
    assert behavior["trimmed"] == ["failed", "newest"]
    assert behavior["old"]["assistantText"] == "updated result"
    assert behavior["old"]["notification"]["label"] == "已通知"
    assert behavior["failed"]["error"] is True
    assert behavior["failed"]["errorSource"] == "Codex CLI（上游 Runtime）"
    assert behavior["timedOut"]["errorSource"] == ""
    assert behavior["unknownFailed"]["errorSource"] == "来源未确认"
    assert behavior["failed"]["notification"] == {
        "status": "failed",
        "label": "通知失败",
        "error": "recipient unavailable",
    }
    assert behavior["failed"]["restart"]["error"] is True
    assert "旧记录没有保存具体原因" in behavior["failed"]["restart"]["text"]
    assert behavior["failed"]["restart"]["notification"]["label"] == "重启结果通知失败"
