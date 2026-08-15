import json
import shutil
import subprocess
from pathlib import Path

import pytest


NODE = shutil.which("node")
AI_USAGE_CORE_SCRIPT = (
    Path(__file__).parents[1]
    / "app"
    / "web"
    / "static"
    / "js"
    / "core"
    / "ai-usage.js"
)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_ai_usage_core_owns_cache_requests_refresh_and_clear() -> None:
    program = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const cached = {
  status: "available",
  checked_at: new Date().toISOString(),
  display: { long: "Weekly cached" },
};
const storageValues = new Map([
  ["hub.sessionToken", "session-token"],
  ["hub.aiUsageCache", JSON.stringify(cached)],
]);
const storage = {
  getItem: (key) => storageValues.get(key) || null,
  setItem: (key, value) => storageValues.set(key, value),
  removeItem: (key) => storageValues.delete(key),
};
const pending = [];
const fetchCalls = [];
globalThis.window = globalThis;
globalThis.sessionStorage = storage;
globalThis.localStorage = storage;
globalThis.fetch = (path, options) => {
  fetchCalls.push({ path, options });
  return new Promise((resolve) => pending.push(resolve));
};
eval(source);

function respond(data, { status = 200, success = true, error = null } = {}) {
  const resolve = pending.shift();
  resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => success
      ? { success: true, data }
      : { success: false, error },
  });
}

(async () => {
  const events = [];
  window.ChubAiUsage.subscribe((value) => {
    events.push(value?.display?.long || null);
  });
  const restored = window.ChubAiUsage.current();
  const fromCache = await window.ChubAiUsage.load();

  const first = window.ChubAiUsage.load({ force: true });
  const concurrent = window.ChubAiUsage.load({ force: true });
  await Promise.resolve();
  const fresh = {
    status: "available",
    checked_at: new Date().toISOString(),
    display: { long: "Weekly fresh" },
  };
  respond(fresh);
  const firstResult = await first;
  const concurrentResult = await concurrent;

  window.ChubAiUsage.clear();
  const normal = window.ChubAiUsage.load();
  const forcedAfterNormal = window.ChubAiUsage.load({ force: true });
  await Promise.resolve();
  respond({
    status: "available",
    checked_at: new Date().toISOString(),
    display: { long: "Weekly normal" },
  });
  const normalResult = await normal;
  await Promise.resolve();
  respond({
    status: "available",
    checked_at: new Date().toISOString(),
    display: { long: "Weekly forced" },
  });
  const forcedAfterNormalResult = await forcedAfterNormal;

  const staleRequest = window.ChubAiUsage.load({ force: true });
  await Promise.resolve();
  window.ChubAiUsage.clear();
  respond({
    status: "available",
    checked_at: new Date().toISOString(),
    display: { long: "Weekly stale" },
  });
  const staleResult = await staleRequest;

  storageValues.set("hub.aiUsageCache", JSON.stringify(fresh));
  const unauthorized = window.ChubAiUsage.load({ force: true });
  await Promise.resolve();
  respond(null, {
    status: 401,
    success: false,
    error: { code: "authentication_required", message: "Token expired" },
  });
  let unauthorizedCode = null;
  try {
    await unauthorized;
  } catch (error) {
    unauthorizedCode = error.code;
  }

  process.stdout.write(JSON.stringify({
    restored,
    fromCache,
    fetchCalls,
    firstResult,
    concurrentResult,
    normalResult,
    forcedAfterNormalResult,
    staleResult,
    currentAfterClear: window.ChubAiUsage.current(),
    unauthorizedCode,
    cachedAfterUnauthorized: storageValues.get("hub.aiUsageCache") || null,
    events,
  }));
})().catch((error) => {
  process.stderr.write(String(error?.stack || error));
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        [NODE, "-e", program, str(AI_USAGE_CORE_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
    behavior = json.loads(result.stdout)

    assert behavior["restored"]["display"]["long"] == "Weekly cached"
    assert behavior["fromCache"] == behavior["restored"]
    assert [call["path"] for call in behavior["fetchCalls"]] == [
        "/api/ai/usage?refresh=true",
        "/api/ai/usage",
        "/api/ai/usage?refresh=true",
        "/api/ai/usage?refresh=true",
        "/api/ai/usage?refresh=true",
    ]
    assert all(
        call["options"]["headers"]["Authorization"] == "Bearer session-token"
        for call in behavior["fetchCalls"]
    )
    assert behavior["firstResult"] == behavior["concurrentResult"]
    assert behavior["firstResult"]["display"]["long"] == "Weekly fresh"
    assert behavior["normalResult"]["display"]["long"] == "Weekly normal"
    assert behavior["forcedAfterNormalResult"]["display"]["long"] == "Weekly forced"
    assert behavior["staleResult"] is None
    assert behavior["currentAfterClear"] is None
    assert behavior["unauthorizedCode"] == "invalid_credentials"
    assert behavior["cachedAfterUnauthorized"] is None
    assert behavior["events"] == [
        "Weekly cached",
        "Weekly fresh",
        None,
        "Weekly normal",
        "Weekly forced",
        None,
        None,
    ]


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_ai_usage_core_keeps_live_requests_working_when_storage_is_blocked() -> None:
    program = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const blockedStorage = {
  getItem() { throw new Error("blocked"); },
  setItem() { throw new Error("blocked"); },
  removeItem() { throw new Error("blocked"); },
};
const usage = {
  status: "available",
  checked_at: new Date().toISOString(),
  display: { long: "Weekly live" },
};
globalThis.window = globalThis;
globalThis.sessionStorage = blockedStorage;
globalThis.localStorage = blockedStorage;
globalThis.fetch = async (_path, options) => ({
  ok: true,
  status: 200,
  json: async () => ({ success: true, data: usage, options }),
});
eval(source);

(async () => {
  const loaded = await window.ChubAiUsage.load();
  window.ChubAiUsage.clear();
  process.stdout.write(JSON.stringify({
    loaded,
    current: window.ChubAiUsage.current(),
  }));
})().catch((error) => {
  process.stderr.write(String(error?.stack || error));
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        [NODE, "-e", program, str(AI_USAGE_CORE_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
    behavior = json.loads(result.stdout)

    assert behavior["loaded"]["display"]["long"] == "Weekly live"
    assert behavior["current"] is None


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
@pytest.mark.parametrize(
    ("cached_value", "token"),
    [
        ("invalid-json", "saved-token"),
        ("123", "saved-token"),
        (
            json.dumps(
                {
                    "status": "available",
                    "checked_at": "2026-08-15T10:00:00+08:00",
                }
            ),
            "",
        ),
    ],
)
def test_ai_usage_core_removes_invalid_or_unauthenticated_cache(
    cached_value: str,
    token: str,
) -> None:
    program = r"""
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8");
const cachedValue = process.argv[2];
const token = process.argv[3];
let removed = false;
globalThis.window = globalThis;
globalThis.sessionStorage = {
  getItem: (key) => {
    if (key === "hub.aiUsageCache") return cachedValue;
    if (key === "hub.sessionToken") return token || null;
    return null;
  },
  setItem() {},
  removeItem: (key) => {
    if (key === "hub.aiUsageCache") removed = true;
  },
};
globalThis.localStorage = { getItem: () => null };
globalThis.fetch = async () => { throw new Error("not expected"); };
eval(source);
process.stdout.write(JSON.stringify({
  current: window.ChubAiUsage.current(),
  removed,
}));
"""
    result = subprocess.run(
        [NODE, "-e", program, str(AI_USAGE_CORE_SCRIPT), cached_value, token],
        check=True,
        capture_output=True,
        text=True,
    )
    behavior = json.loads(result.stdout)

    assert behavior == {"current": None, "removed": True}
