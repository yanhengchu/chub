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
def test_codex_model_preference_restores_cached_value_before_refresh() -> None:
    program = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const sessionValues = new Map();
const localValues = new Map([
  ["hub.codexDefaultModel.v1", "gpt-cached"],
  ["hub.codexDefaultReasoningEffort.v1", "high"],
]);
const cached = {
  models: [{
    id: "gpt-cached",
    name: "GPT Cached",
    default_level: "medium",
    levels: [{ id: "medium" }, { id: "high" }],
  }],
  default_model: "gpt-cached",
  default_reasoning_effort: "medium",
};
sessionValues.set("hub.codexModelPreferenceCache", JSON.stringify(cached));
Object.defineProperty(globalThis, "sessionStorage", {
  value: {
    getItem: (key) => sessionValues.get(key) || null,
    setItem: (key, value) => sessionValues.set(key, value),
    removeItem: (key) => sessionValues.delete(key),
  },
});
Object.defineProperty(globalThis, "localStorage", {
  value: { getItem: (key) => localValues.get(key) || null },
});
const modelPreference = { textContent: "新建默认：正在读取…", dataset: {} };
const elements = { codexModelPreference: modelPreference };
eval(`${source}\n
restoreCodexModelPreferenceCache();
const restored = {
  text: modelPreference.textContent,
  hasValue: modelPreference.dataset.hasValue,
};
const fresh = {
  models: [{
    id: "gpt-cached",
    name: "GPT Fresh",
    default_level: "medium",
    levels: [{ id: "medium" }, { id: "high" }],
  }],
  default_model: "gpt-cached",
  default_reasoning_effort: "medium",
};
renderCodexModelPreference(fresh);
storeCodexModelPreferenceCache(fresh);
process.stdout.write(JSON.stringify({
  restored,
  refreshedText: modelPreference.textContent,
  cachedName: JSON.parse(sessionValues.get(CODEX_MODEL_PREFERENCE_CACHE_KEY)).models[0].name,
}));
`);
"""
    result = subprocess.run(
        [NODE, "-e", program, str(SESSIONS_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "restored": {
            "text": "新建默认：GPT Cached · High",
            "hasValue": "true",
        },
        "refreshedText": "新建默认：GPT Fresh · High",
        "cachedName": "GPT Fresh",
    }

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
