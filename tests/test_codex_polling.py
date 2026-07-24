import json
import shutil
import subprocess
from pathlib import Path

import pytest


NODE = shutil.which("node")
POLLING_SCRIPT = (
    Path(__file__).parents[1] / "app" / "web" / "static" / "codex_polling.js"
)


@pytest.mark.skipif(NODE is None, reason="Node.js is required for JavaScript behavior tests")
def test_codex_poll_plan_controls_lifecycle_and_backoff() -> None:
    program = """
const { codexPollPlan } = require(process.argv[1]);
const base = {
  stateChanged: false,
  unchangedSince: 1000,
  now: 2000,
  visible: true,
  authenticated: true,
  mutating: false,
  fastDelay: 2000,
  slowDelay: 8000,
  slowAfter: 120000,
};
const working = [{ status: "running", activity: "working" }];
const idle = [{ status: "running", activity: "idle" }];
const unknown = [{ status: "running", activity: "unknown" }];
const plans = {
  stopped: codexPollPlan({ ...base, sessions: idle }),
  fast: codexPollPlan({ ...base, sessions: working }),
  slow: codexPollPlan({
    ...base,
    sessions: working,
    now: 122000,
  }),
  unknown: codexPollPlan({ ...base, sessions: unknown }),
  changed: codexPollPlan({
    ...base,
    sessions: working,
    stateChanged: true,
    now: 122000,
  }),
  hidden: codexPollPlan({
    ...base,
    sessions: working,
    visible: false,
  }),
  signedOut: codexPollPlan({
    ...base,
    sessions: working,
    authenticated: false,
  }),
  mutating: codexPollPlan({
    ...base,
    sessions: working,
    mutating: true,
  }),
};
process.stdout.write(JSON.stringify(plans));
"""
    result = subprocess.run(
        [NODE, "-e", program, str(POLLING_SCRIPT)],
        check=True,
        capture_output=True,
        text=True,
    )
    plans = json.loads(result.stdout)

    assert plans["stopped"] == {
        "shouldPoll": False,
        "unchangedSince": 0,
        "delay": None,
    }
    assert plans["fast"]["delay"] == 2000
    assert plans["slow"]["delay"] == 8000
    assert plans["unknown"] == {
        "shouldPoll": True,
        "unchangedSince": 1000,
        "delay": 8000,
    }
    assert plans["changed"] == {
        "shouldPoll": True,
        "unchangedSince": 122000,
        "delay": 2000,
    }
    assert plans["hidden"]["delay"] is None
    assert plans["signedOut"]["delay"] is None
    assert plans["mutating"]["delay"] is None
