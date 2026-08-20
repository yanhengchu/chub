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
THEME_SCRIPT = (
    Path(__file__).parents[1] / "app" / "web" / "static" / "theme.js"
)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_cyber_rain_uses_lowercase_quota_parts_and_clears_them() -> None:
    program = r"""
const fs = require("fs");
const coreSource = fs.readFileSync(process.argv[1], "utf8");
const themeSource = fs.readFileSync(process.argv[2], "utf8");

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName;
    this.children = [];
    this.dataset = {};
    this.className = "";
    this.textContent = "";
    this.listeners = {};
    this.style = {
      setProperty(name, value) { this[name] = value; },
    };
  }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  remove() {
    const index = document.body.children.indexOf(this);
    if (index >= 0) document.body.children.splice(index, 1);
  }
}

const root = new FakeElement("html");
root.dataset.uiStyle = "cyber";
root.clientHeight = 800;
const body = new FakeElement("body");
const meta = new FakeElement("meta");
const documentListeners = {};
const document = {
  documentElement: root,
  body,
  cookie: "",
  createElement: (tagName) => new FakeElement(tagName),
  querySelector(selector) {
    if (selector === ".cyber-matrix") {
      return body.children.find((item) => item.className === "cyber-matrix") || null;
    }
    if (selector === 'meta[name="color-scheme"]') return meta;
    return null;
  },
  addEventListener(name, callback) { documentListeners[name] = callback; },
  dispatchEvent() {},
};
const storageValues = new Map();
const localStorage = {
  getItem: (key) => storageValues.get(key) || null,
  setItem: (key, value) => storageValues.set(key, value),
};
const sessionStorage = {
  getItem: (key) => storageValues.get(key) || null,
  setItem: (key, value) => storageValues.set(key, value),
  removeItem: (key) => storageValues.delete(key),
};
const compactMedia = {
  matches: false,
  addEventListener() {},
};
const window = globalThis;
window.innerHeight = 800;
window.matchMedia = () => compactMedia;
window.getComputedStyle = () => ({ fontSize: "16px" });
globalThis.document = document;
globalThis.localStorage = localStorage;
globalThis.sessionStorage = sessionStorage;
globalThis.CustomEvent = class { constructor(name, options) { this.name = name; this.detail = options?.detail; } };
const usageData = {
  status: "available",
  display: {
    long: "Weekly $781.92 left (78%) · Limit $1,000 · Today $181.02 used 100M tokens · Resets 8/20 15:45",
  },
};
const fetchCalls = [];
globalThis.fetch = async (path, options) => {
  fetchCalls.push({ path, options });
  return {
    ok: true,
    json: async () => ({ success: true, data: usageData }),
  };
};

eval(coreSource);
eval(themeSource);
documentListeners.DOMContentLoaded();

(async () => {
await window.ChubAiUsage.load();

const matrix = document.querySelector(".cyber-matrix");
const dynamics = matrix.children.filter((stream) => stream.dataset.rainDynamic === "true");
const ordinary = matrix.children.filter((stream) => !dynamics.includes(stream));
const visibleText = (stream) => stream.children
  .map((character) => character.textContent === "\u00a0" ? " " : character.textContent)
  .join("")
  .trim();

const weekly = visibleText(dynamics[0]);
const simultaneousToday = visibleText(dynamics[1]);
const weeklyLength = dynamics[0].children.length;
const weeklyDuration = Number.parseFloat(dynamics[0].style.animationDuration);
const startsInProgress = dynamics.every(
  (stream) => Number.parseFloat(stream.style.animationDelay) < 0,
);

dynamics[0].listeners.animationiteration();
const nextWeekly = visibleText(dynamics[0]);
dynamics[1].listeners.animationiteration();
const nextToday = visibleText(dynamics[1]);
const todayLength = dynamics[1].children.length;
const hasFixedSpaces = dynamics[1].children.some((character) => character.dataset.rainSpace === "true");
const cachedUsage = JSON.parse(storageValues.get("hub.aiUsageCache"));

Math.random = () => 0;
window.ChubAiUsage.clear();
const cleared = visibleText(dynamics[0]);
const cacheRemoved = !storageValues.has("hub.aiUsageCache");
document.documentElement.dataset.stylePreview = "cyber";
await window.ChubAiUsage.load({ force: true });
const previewKinds = dynamics.map((stream) => stream.dataset.rainKind);

process.stdout.write(JSON.stringify({
  streamCount: matrix.children.length,
  dynamicCount: dynamics.length,
  ordinaryLowercase: ordinary.every((stream) => /^[a-z01. ]+$/.test(visibleText(stream))),
  phrasesMeaningful: ordinary
    .filter((stream) => stream.dataset.rainKind === "phrase")
    .every((stream) => /^[a-z]+(?:[. ][a-z]+)+$/.test(visibleText(stream))),
  weekly,
  simultaneousToday,
  nextWeekly,
  nextToday,
  weeklyLength,
  todayLength,
  weeklyDuration,
  startsInProgress,
  hasFixedSpaces,
  cleared,
  clearedKind: dynamics[0].dataset.rainKind,
  usagePath: fetchCalls[0].path,
  usageRequestCount: fetchCalls.length,
  cachedUsage,
  cacheRemoved,
  previewKinds,
}));
})().catch((error) => {
  process.stderr.write(String(error));
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        [NODE, "-e", program, str(AI_USAGE_CORE_SCRIPT), str(THEME_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)

    assert data["streamCount"] == 10
    assert data["dynamicCount"] == 2
    assert data["ordinaryLowercase"] is True
    assert data["phrasesMeaningful"] is True
    assert data["weekly"] == (
        "weekly $781.92 left (78%) · limit $1,000 · resets 8/20 15:45"
    )
    assert data["simultaneousToday"] == "today $181.02 used 100m tokens"
    assert data["nextWeekly"] == data["weekly"]
    assert data["nextToday"] == data["simultaneousToday"]
    assert data["weeklyLength"] == data["todayLength"]
    assert data["weeklyDuration"] > 20
    assert data["startsInProgress"] is True
    assert data["hasFixedSpaces"] is True
    assert data["cleared"] == "good.morning"
    assert data["clearedKind"] == "phrase"
    assert data["usagePath"] == "/api/ai/usage"
    assert data["usageRequestCount"] == 2
    assert data["cacheRemoved"] is True
    assert "quota" not in data["previewKinds"]
    assert data["cachedUsage"] == {
        "status": "available",
        "display": {
            "long": (
                "Weekly $781.92 left (78%) · Limit $1,000 · "
                "Today $181.02 used 100M tokens · Resets 8/20 15:45"
            ),
        },
    }
