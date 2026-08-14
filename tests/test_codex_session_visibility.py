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
def test_translation_session_visibility_applies_to_all_web_session_lists() -> None:
    program = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const quickCore = require(process.argv[2]);
const values = new Map();
let blocked = false;
Object.defineProperty(globalThis, "localStorage", {
  value: {
    getItem(key) {
      if (blocked) {
        throw new Error("blocked");
      }
      return values.has(key) ? values.get(key) : null;
    },
  },
});
const sessions = [
  { id: "normal", created_at: "2026-08-13T00:00:00Z", workspace_id: "chub" },
  { id: "translation", created_at: "2026-08-15T00:00:00Z", workspace_id: "weixin-translation" },
  { id: "quick-only", created_at: "2026-08-14T00:00:00Z", workspace_id: "other", terminal_access_allowed: false },
];
eval(`${source}\n
const ids = (items) => items.map((item) => item.id);
const visibleIds = () => ({
  home: ids(visibleCodexSessions(sessions)),
  quick: ids(quickCore.sessionSwitcherEntries(sessions)),
});
const result = {};
result.defaultHidden = visibleIds();
values.set(CODEX_SHOW_TRANSLATION_SESSION_KEY, "true");
result.enabled = visibleIds();
values.set(CODEX_SHOW_TRANSLATION_SESSION_KEY, "false");
result.disabled = visibleIds();
blocked = true;
result.blockedStorage = visibleIds();
result.displayTitles = [
  codexSessionDisplayTitle({ title: "项目维护", weixin_session_slot: 3 }),
  codexSessionDisplayTitle({ title: "文本优化与翻译", weixin_session_slot: null }),
  codexSessionDisplayTitle({ title: null, weixin_session_slot: null }),
  codexSessionDisplayTitle({ title: "异常槽位", weixin_session_slot: 10 }),
];
result.slotChangeDetected = codexSessionsSignature([
  { id: "normal", weixin_session_slot: 1 },
]) !== codexSessionsSignature([
  { id: "normal", weixin_session_slot: 2 },
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
        "defaultHidden": {
            "home": ["quick-only", "normal"],
            "quick": ["quick-only", "normal"],
        },
        "enabled": {
            "home": ["translation", "quick-only", "normal"],
            "quick": ["translation", "quick-only", "normal"],
        },
        "disabled": {
            "home": ["quick-only", "normal"],
            "quick": ["quick-only", "normal"],
        },
        "blockedStorage": {
            "home": ["quick-only", "normal"],
            "quick": ["quick-only", "normal"],
        },
        "displayTitles": [
            "S3 · 项目维护",
            "S · 文本优化与翻译",
            "S · 未命名 Session",
            "S · 异常槽位",
        ],
        "slotChangeDetected": True,
    }


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_codex_entry_mode_defaults_to_quick_interaction() -> None:
    program = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const values = new Map();
let blocked = false;
Object.defineProperty(globalThis, "localStorage", {
  value: {
    getItem(key) {
      if (blocked) {
        throw new Error("blocked");
      }
      return values.has(key) ? values.get(key) : null;
    },
  },
});
eval(`${source}\n
const ordinary = { id: "ordinary", terminal_access_allowed: true };
const quickOnly = { id: "quick-only", terminal_access_allowed: false };
const result = {};
result.defaultMode = codexEntryMode(ordinary);
values.set(CODEX_ENTRY_MODE_KEY, JSON.stringify({ ordinary: "terminal" }));
result.savedTerminal = codexEntryMode(ordinary);
values.set(CODEX_ENTRY_MODE_KEY, JSON.stringify({ ordinary: "quick" }));
result.savedQuick = codexEntryMode(ordinary);
values.set(CODEX_ENTRY_MODE_KEY, JSON.stringify({ ordinary: "unexpected" }));
result.unknownValue = codexEntryMode(ordinary);
values.set(CODEX_ENTRY_MODE_KEY, "invalid-json");
result.invalidStorage = codexEntryMode(ordinary);
values.set(CODEX_ENTRY_MODE_KEY, JSON.stringify({ "quick-only": "terminal" }));
result.quickOnly = codexEntryMode(quickOnly);
blocked = true;
result.blockedStorage = codexEntryMode(ordinary);
process.stdout.write(JSON.stringify(result));
`);
"""
    result = subprocess.run(
        [NODE, "-e", program, str(SESSIONS_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "defaultMode": "quick",
        "savedTerminal": "terminal",
        "savedQuick": "quick",
        "unknownValue": "quick",
        "invalidStorage": "quick",
        "quickOnly": "quick",
        "blockedStorage": "quick",
    }
