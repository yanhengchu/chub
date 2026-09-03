from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
from socket import AF_INET, SOCK_STREAM, SOL_SOCKET, SO_REUSEADDR, socket
from threading import Thread
import time
from urllib.parse import parse_qs, unquote, urlsplit

import pytest
import uvicorn
from playwright.async_api import expect

from app.application import create_app
from app.automations.browser import session_factory
from app.core.config import Settings


RUN_BROWSER_TESTS = os.getenv("CHUB_BROWSER_TESTS") == "1"

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.browser,
    pytest.mark.skipif(
        not RUN_BROWSER_TESTS,
        reason="set CHUB_BROWSER_TESTS=1 to run managed Chrome regression tests",
    ),
]


def _browser_settings(root: Path) -> Settings:
    return Settings.model_validate(
        {
            "app": {"name": "Chub", "version": "0.1.0"},
            "node": {
                "id": "conversation-browser-test",
                "name": "Conversation Browser Test",
                "type": "ubuntu",
            },
            "server": {"port": 8080},
            "security": {"allow_tailscale": False},
            "logs": {
                "file": root / "hub.log",
                "operations_file": root / "operations.log",
                "worker_operations_file": root / "worker-operations.log",
                "level": "ERROR",
                "max_lines": 100,
            },
            "codex_pty": {
                "enabled": True,
                "workspace": root / "workspace",
                "data_file": root / "codex-sessions.json",
                "runtime_dir": root / "codex-runtime",
            },
            "automations": {
                "shared_config_file": root / "automations.yaml",
                "local_config_file": root / "automations.local.yaml",
                "state_dir": root / "automation-state",
                "runtime_dir": root / "automation-runtime",
                "artifacts_dir": root / "automation-artifacts",
            },
            "project_documents": {"state_file": root / "project-documents.json"},
            "requests": {"state_file": root / "requests.json"},
            "notifications": {
                "enabled": False,
                "registry_file": root / "notifications.yaml",
                "secrets_dir": root / "notification-secrets",
            },
            "openclaw": {
                "quick_interaction_completion": {"enabled": False},
                "weixin_chub_mode": {"state_file": root / "weixin-chub-mode.json"},
            },
        }
    )


@pytest.fixture(scope="module")
def conversation_browser_server(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = tmp_path_factory.mktemp("conversation-browser")
    application = create_app(_browser_settings(root))
    # The browser fixture supplies all Session/API data through ConversationApi;
    # allow its synthetic IDs through the HTML page gate as well.
    application.state.codex_pty_manager.require_quick_access = lambda _session_id: None
    listener = socket(AF_INET, SOCK_STREAM)
    listener.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(application, log_level="critical", lifespan="off")
    )
    thread = Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name="conversation-browser-test-server",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        pytest.fail("isolated Chub conversation browser test server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        if thread.is_alive():
            pytest.fail("isolated Chub conversation browser test server did not stop")


def _session(
    session_id: str,
    title: str,
    created_at: str,
    slot: int | None,
    *,
    can_archive: bool = True,
) -> dict:
    return {
        "id": session_id,
        "title": title,
        "created_at": created_at,
        "codex_session_id": f"native-{session_id}",
        "can_archive": can_archive,
        "session_mode": "quick",
        "workspace_id": "chub",
        "status": "stopped",
        "activity": "idle",
        "activity_source": None,
        "permission_mode": "full-access",
        "quick_interaction_running": False,
        "weixin_session_slot": slot,
    }


def _task(
    task_id: str,
    prompt: str,
    result: str,
    created_at: str,
    *,
    notification_status: str = "sent",
) -> dict:
    return {
        "id": task_id,
        "status": "succeeded",
        "prompt": prompt,
        "result": result,
        "error": None,
        "created_at": created_at,
        "updated_at": created_at,
        "notification_status": notification_status,
        "notification_error": None,
        "deferred_restart_status": None,
        "deferred_restart_error": None,
        "deferred_restart_updated_at": None,
        "deferred_restart_notification_status": None,
        "deferred_restart_notification_error": None,
        "deferred_restart_notification_updated_at": None,
    }


class ConversationApi:
    def __init__(self) -> None:
        self.sessions = [
            _session("session-2", "Second Session", "2026-08-15T09:00:00Z", 2),
            _session("session-1", "Main Session", "2026-08-15T08:00:00Z", 1),
        ]
        self.tasks = {
            "session-1": [
                _task("task-1", "Earlier question", "Earlier answer", "2026-08-15T08:00:00Z"),
                _task("task-2", "Recent question", "Recent answer", "2026-08-15T08:10:00Z"),
                _task(
                    "task-3",
                    "Latest question",
                    "Latest answer",
                    "2026-08-15T08:20:00Z",
                    notification_status="failed",
                ),
            ],
            "session-2": [
                _task("task-4", "Second question", "Second answer", "2026-08-15T09:00:00Z")
            ],
        }
        self.requested_paths: list[str] = []
        self.fail_submission_recovery = False
        self.abort_load_requests = 0
        self.invalid_json_load_requests = 0
        self.recovery_started = asyncio.Event()
        self.recovery_release = asyncio.Event()
        self._recovery_failure_pending = False

    def _find_session(self, session_id: str) -> dict | None:
        return next(
            (session for session in self.sessions if session["id"] == session_id),
            None,
        )

    async def handle(self, route) -> None:
        request = route.request
        parsed = urlsplit(request.url)
        path = parsed.path
        self.requested_paths.append(f"{request.method} {path}")
        if request.method == "GET" and self.abort_load_requests > 0:
            self.abort_load_requests -= 1
            await route.abort("failed")
            return
        if request.method == "GET" and self.invalid_json_load_requests > 0:
            self.invalid_json_load_requests -= 1
            await route.fulfill(
                status=503,
                content_type="text/plain",
                body="Web is restarting",
            )
            return
        status = 200
        data: dict | None = None

        if (
            path == "/api/codex/sessions"
            and request.method == "GET"
            and self._recovery_failure_pending
        ):
            self._recovery_failure_pending = False
            self.recovery_started.set()
            await self.recovery_release.wait()
            await route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {
                        "success": False,
                        "error": {
                            "code": "browser_test_recovery_failed",
                            "message": "Old Session recovery failed",
                        },
                    }
                ),
            )
            return
        if path == "/api/codex/sessions" and request.method == "GET":
            data = {
                "available": True,
                "unavailable_reason": None,
                "quick_creation": {"available": True, "reason": None},
                "workspaces": [
                    {
                        "id": "chub",
                        "name": "Chub",
                        "path": "/workspace/chub",
                        "available": True,
                    }
                ],
                "sessions": deepcopy(self.sessions),
            }
        elif path == "/api/codex/models" and request.method == "GET":
            data = {
                "models": [
                    {
                        "id": "gpt-test",
                        "name": "GPT Test",
                        "description": "Browser test model",
                        "default_level": "medium",
                        "levels": [
                            {"id": "low", "description": "Light"},
                            {"id": "medium", "description": "Balanced"},
                        ],
                    }
                ],
                "default_model": "gpt-test",
                "default_reasoning_effort": "medium",
            }
        elif path == "/api/codex/sessions" and request.method == "POST":
            session = _session(
                "session-3",
                "New Session",
                "2026-08-15T10:00:00Z",
                None,
                can_archive=False,
            )
            session["codex_session_id"] = None
            self.sessions.insert(0, session)
            self.tasks[session["id"]] = []
            data = deepcopy(session)
        elif path.startswith("/api/codex/sessions/"):
            remainder = path.removeprefix("/api/codex/sessions/")
            encoded_session_id, _, action = remainder.partition("/")
            session_id = unquote(encoded_session_id)
            session = self._find_session(session_id)
            if action == "title" and request.method == "PATCH" and session:
                session["title"] = request.post_data_json["title"]
                data = deepcopy(session)
            elif action == "configuration" and request.method == "PATCH" and session:
                payload = request.post_data_json
                session["permission_mode"] = payload["permission_mode"]
                session["model"] = payload.get("model")
                session["reasoning_effort"] = payload.get("reasoning_effort")
                data = deepcopy(session)
            elif action == "archive" and request.method == "POST" and session:
                self.sessions = [item for item in self.sessions if item["id"] != session_id]
                data = {"session_id": session_id}
            elif action == "stop" and request.method == "POST" and session:
                session["status"] = "stopped"
                session["activity"] = "idle"
                session["quick_interaction_running"] = False
                session["usage"] = {"owner": "none", "phase": "idle"}
                data = deepcopy(session)
            elif not action and request.method == "DELETE" and session:
                self.sessions = [item for item in self.sessions if item["id"] != session_id]
                self.tasks.pop(session_id, None)
                data = {}
            elif action == "quick-interactions" and request.method == "GET":
                tasks = self.tasks.get(session_id, [])
                query = parse_qs(parsed.query)
                selected = tasks[:-2] if "before_created_at" in query else tasks[-2:]
                data = {
                    "tasks": deepcopy(selected),
                    "total": len(tasks),
                    "has_more": "before_created_at" not in query and len(tasks) > 2,
                }
            elif action == "quick-interactions" and request.method == "POST":
                if self.fail_submission_recovery:
                    self._recovery_failure_pending = True
                    await route.fulfill(
                        status=503,
                        content_type="application/json",
                        body=json.dumps(
                            {
                                "success": False,
                                "error": {
                                    "code": "browser_test_submission_failed",
                                    "message": "Submission failed",
                                },
                            }
                        ),
                    )
                    return
                prompt = request.post_data_json["prompt"]
                tasks = self.tasks.setdefault(session_id, [])
                task = _task(
                    f"task-{len(tasks) + 10}",
                    prompt,
                    "Submitted answer",
                    "2026-08-15T10:10:00Z",
                )
                tasks.append(task)
                data = {"task": deepcopy(task)}
            elif not action and request.method == "GET" and session:
                data = deepcopy(session)

        if data is None:
            status = 404
            payload = {
                "success": False,
                "error": {"code": "browser_test_missing", "message": "Not mocked"},
            }
        else:
            payload = {"success": True, "data": data}
        await route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(payload),
        )


async def _open_conversation(
    browser,
    server: str,
    api: ConversationApi,
    *,
    theme: str,
    viewport: tuple[int, int],
):
    context = await browser.new_context(
        viewport={"width": viewport[0], "height": viewport[1]},
        reduced_motion="reduce",
    )
    await context.route(f"{server}/api/**", api.handle)
    page = await context.new_page()
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    await page.add_init_script(
        script=(
            "localStorage.clear();"
            "sessionStorage.clear();"
            f"localStorage.setItem('hub.uiStyle.v1', {json.dumps(theme)});"
        )
    )
    response = await page.goto(
        f"{server}/codex/session-1/quick-interactions/conversation",
        wait_until="domcontentloaded",
    )
    assert response is not None and response.status == 200
    await expect(page.locator("#conversation-session-title")).to_have_text("Main Session")
    await expect(page.locator("[data-task-id]")).to_have_count(2)
    return context, page, page_errors


async def test_terminal_page_returns_home_after_connection_takeover(
    conversation_browser_server: str,
) -> None:
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context = await chrome.browser.new_context()
        page = await context.new_page()
        try:
            await context.route(
                f"{conversation_browser_server}/terminal-takeover-fixture",
                lambda route: route.fulfill(
                    content_type="text/html",
                    body=(
                        '<body data-session-id="session-1" data-page-id="page-1">'
                        '<script src="/static/terminal.js" defer></script>'
                    ),
                ),
            )
            await context.route(
                f"{conversation_browser_server}/codex/session-1/connection/page-1",
                lambda route: route.fulfill(
                    content_type="application/json",
                    body='{"state":"displaced"}',
                ),
            )
            response = await page.goto(
                f"{conversation_browser_server}/terminal-takeover-fixture",
                wait_until="domcontentloaded",
            )
            assert response is not None and response.status == 200
            await expect(page).to_have_url(
                f"{conversation_browser_server}/",
                timeout=3_000,
            )
        finally:
            await context.close()


@pytest.mark.parametrize("theme", ["standard", "code-dark"])
@pytest.mark.parametrize(
    "viewport",
    [(360, 800), (412, 915), (1280, 900)],
    ids=["android-compact", "android-large", "desktop"],
)
async def test_conversation_layout_in_managed_chrome(
    conversation_browser_server: str,
    theme: str,
    viewport: tuple[int, int],
) -> None:
    api = ConversationApi()
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context, page, page_errors = await _open_conversation(
            chrome.browser,
            conversation_browser_server,
            api,
            theme=theme,
            viewport=viewport,
        )
        try:
            layout = await page.evaluate(
                """() => {
                    const composer = document.querySelector("#conversation-form");
                    const navigation = document.querySelector("#conversation-session-navigation");
                    const input = document.querySelector("#conversation-prompt");
                    const submit = document.querySelector("#conversation-submit");
                    const inputShell = document.querySelector(".conversation-input-row");
                    const settingControls = [
                        ...document.querySelectorAll(".conversation-setting-trigger"),
                        submit,
                    ];
                    const inputRect = input.getBoundingClientRect();
                    const submitRect = submit.getBoundingClientRect();
                    const touchTargets = [
                        navigation.querySelector("#conversation-session-create"),
                        ...navigation.querySelectorAll(".conversation-session-switch"),
                        ...document.querySelectorAll(
                            "#conversation-session-rename, "
                            + "#conversation-session-stop, "
                            + "#conversation-session-archive, "
                            + "#conversation-session-delete"
                        ),
                    ].map(node => {
                        const rect = node.getBoundingClientRect();
                        return {
                            id: node.id || node.dataset.sessionId,
                            isSwitch: node.classList.contains("conversation-session-switch"),
                            isFixed: !node.classList.contains("conversation-session-switch"),
                            width: rect.width,
                            height: rect.height,
                        };
                    });
                    return {
                        theme: document.documentElement.dataset.uiStyle,
                        overflow: document.documentElement.scrollWidth - innerWidth,
                        composerWidth: composer.getBoundingClientRect().width,
                        navigationWidth: navigation.getBoundingClientRect().width,
                        inputShellWidth: inputShell.getBoundingClientRect().width,
                        settingControls: settingControls.map(node => {
                            const rect = node.getBoundingClientRect();
                            return {
                                id: node.id,
                                width: rect.width,
                                height: rect.height,
                            };
                        }),
                        composerMenusAbove: Array.from(
                            document.querySelectorAll(".conversation-setting-menu")
                        ).every(menu => getComputedStyle(menu).bottom !== "auto"),
                        inputSubmitOverlap: Math.max(
                            0,
                            Math.min(inputRect.right, submitRect.right)
                                - Math.max(inputRect.left, submitRect.left),
                        ) * Math.max(
                            0,
                            Math.min(inputRect.bottom, submitRect.bottom)
                                - Math.max(inputRect.top, submitRect.top),
                        ),
                        touchTargets,
                        titleVisible: !document.querySelector(
                            "#conversation-session-title-row"
                        ).hidden,
                    };
                }"""
            )
        finally:
            await context.close()

    assert layout["theme"] == theme
    assert layout["overflow"] <= 1
    assert layout["composerWidth"] > 0
    assert layout["navigationWidth"] <= layout["composerWidth"] + 1
    assert layout["inputShellWidth"] <= layout["composerWidth"] + 1
    assert all(
        target["width"] == (30 if target["id"] == "conversation-submit" else target["width"])
        and target["height"] == 30
        for target in layout["settingControls"]
    )
    assert layout["composerMenusAbove"] is True
    assert layout["inputSubmitOverlap"] == 0
    assert all(
        (target["width"] == 30 if target["isFixed"] else target["width"] > 0)
        and target["height"] == 30
        for target in layout["touchTargets"]
    )
    assert layout["titleVisible"] is True
    assert page_errors == []


@pytest.mark.parametrize("theme", ["standard", "code-dark"])
async def test_conversation_composer_options_show_current_values_and_disable_approval(
    conversation_browser_server: str,
    theme: str,
) -> None:
    api = ConversationApi()
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context, page, page_errors = await _open_conversation(
            chrome.browser,
            conversation_browser_server,
            api,
            theme=theme,
            viewport=(1280, 900),
        )
        try:
            await expect(page.locator("#conversation-permission-value")).to_have_text(
                "Full access"
            )
            await expect(page.locator("#conversation-model-value")).to_have_text("GPT Test")
            await expect(page.locator("#conversation-reasoning-value")).to_have_text("Medium")
            await expect(page.locator("#conversation-submit")).to_be_disabled()
            await expect(page.locator("#conversation-submit")).to_have_text("")
            assert await page.locator("#conversation-submit svg").count() == 1
            await expect(page.locator("#conversation-permission-menu [data-value='ask']")).to_be_disabled()
            before_hover = await page.locator(".conversation-setting-trigger").evaluate_all(
                """nodes => nodes.map(node => ({
                    color: getComputedStyle(node).color,
                    shadow: getComputedStyle(node).boxShadow,
                }))"""
            )
            assert all(item["shadow"] == "none" for item in before_hover)
            assert all(item["color"] == before_hover[0]["color"] for item in before_hover)
            await page.locator("#conversation-permission-trigger").hover()
            after_hover = await page.locator("#conversation-permission-trigger").evaluate(
                """node => ({
                    color: getComputedStyle(node).color,
                    shadow: getComputedStyle(node).boxShadow,
                })"""
            )
            assert after_hover["color"] == before_hover[0]["color"]
            assert after_hover["shadow"] == "none"
            await page.locator("#conversation-model-trigger").click()
            await expect(page.locator("#conversation-model-menu")).to_be_visible()
            await page.locator("#conversation-model-menu [data-value='']").click()
            await expect(page.locator("#conversation-model-menu")).not_to_be_visible()
            await page.locator("#conversation-model-trigger").click()
            await page.locator("#conversation-model-menu [data-value='gpt-test']").click()
            await expect(page.locator("#conversation-model-value")).to_have_text("GPT Test")
            await expect(page.locator("#conversation-model-trigger")).to_be_enabled()
            assert "PATCH /api/codex/sessions/session-1/configuration" in api.requested_paths
            await page.locator("#conversation-permission-trigger").click()
            await expect(page.locator("#conversation-permission-menu")).to_be_visible()
        finally:
            await context.close()

    assert page_errors == []


async def test_conversation_suppresses_transient_connection_errors(
    conversation_browser_server: str,
) -> None:
    api = ConversationApi()
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context, page, page_errors = await _open_conversation(
            chrome.browser,
            conversation_browser_server,
            api,
            theme="standard",
            viewport=(1280, 900),
        )
        try:
            api.abort_load_requests = 2
            await page.evaluate("loadConversation()")
            await expect(page.locator("#conversation-submit-message")).to_have_text("")
            await expect(page.locator("#conversation-history-message")).to_have_text("")

            api.invalid_json_load_requests = 1
            await page.evaluate("loadConversation()")
            await expect(page.locator("#conversation-submit-message")).to_have_text("")
            await expect(page.locator("#conversation-history-message")).to_have_text("")

            await page.evaluate("loadConversation()")
            await expect(page.locator("#conversation-session-title")).to_have_text(
                "Main Session"
            )
            await expect(page.locator("#conversation-submit-message")).to_have_text("")
        finally:
            await context.close()

    assert page_errors == []


async def test_conversation_workflows_in_managed_chrome(
    conversation_browser_server: str,
) -> None:
    api = ConversationApi()
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context, page, page_errors = await _open_conversation(
            chrome.browser,
            conversation_browser_server,
            api,
            theme="standard",
            viewport=(1280, 900),
        )
        try:
            await page.locator("#conversation-load-earlier").click()
            await expect(page.locator("[data-task-id]")).to_have_count(3)
            assert await page.locator("[data-task-id]").evaluate_all(
                "nodes => nodes.map(node => node.dataset.taskId)"
            ) == ["task-1", "task-2", "task-3"]

            await page.evaluate(
                """() => switchConversationSession(
                    "session-2",
                    "/codex/session-2/quick-interactions/conversation",
                )"""
            )
            switch_state = await page.locator(".conversation-setting-trigger").evaluate_all(
                """nodes => nodes.map(node => ({
                    disabled: node.disabled,
                    color: getComputedStyle(node).color,
                    opacity: getComputedStyle(node).opacity,
                }))"""
            )
            assert all(item["disabled"] for item in switch_state)
            assert all(item["color"] == switch_state[0]["color"] for item in switch_state)
            assert all(item["opacity"] == "1" for item in switch_state)
            await expect(page).to_have_url(
                f"{conversation_browser_server}/codex/session-2/quick-interactions/conversation"
            )
            await expect(page.locator("#conversation-session-title")).to_have_text(
                "Second Session"
            )
            await expect(page.locator("#conversation-feed")).to_contain_text("Second answer")

            await page.locator("#conversation-session-rename").click()
            await page.locator("#conversation-rename-input").fill("Renamed Session")
            await page.locator("#conversation-rename-confirm").click()
            await expect(page.locator("#conversation-session-title")).to_have_text(
                "Renamed Session"
            )

            await page.locator("#conversation-session-archive").click()
            await page.locator("#conversation-archive-confirm").click()
            await expect(page).to_have_url(
                f"{conversation_browser_server}/codex/session-1/quick-interactions/conversation"
            )
            await expect(page.locator("#conversation-session-title")).to_have_text("Main Session")

            await page.locator("#conversation-session-create").click()
            await page.locator("#conversation-create-workspaces .workspace-button").click()
            await expect(page).to_have_url(
                f"{conversation_browser_server}/codex/session-3/quick-interactions/conversation"
            )
            await expect(page.locator("#conversation-session-title")).to_have_text("New Session")

            await page.locator("#conversation-prompt").fill("New browser task")
            await page.locator("#conversation-submit").click()
            await expect(page.locator("#conversation-feed")).to_contain_text("New browser task")
            await expect(page.locator("#conversation-feed")).to_contain_text("Submitted answer")

            await page.locator("#conversation-session-delete").click()
            await page.locator("#conversation-delete-confirm").click()
            await expect(page).to_have_url(
                f"{conversation_browser_server}/codex/session-1/quick-interactions/conversation"
            )
            await expect(page.locator("#conversation-session-title")).to_have_text("Main Session")
        finally:
            await context.close()

    assert page_errors == []
    assert not any(path.endswith("/pin") for path in api.requested_paths)
    assert "PATCH /api/codex/sessions/session-2/title" in api.requested_paths
    assert "POST /api/codex/sessions/session-2/archive" in api.requested_paths
    assert "POST /api/codex/sessions" in api.requested_paths
    assert "POST /api/codex/sessions/session-3/quick-interactions" in api.requested_paths
    assert "DELETE /api/codex/sessions/session-3" in api.requested_paths


async def test_conversation_stop_requires_confirmation_in_managed_chrome(
    conversation_browser_server: str,
) -> None:
    api = ConversationApi()
    session = api._find_session("session-1")
    assert session is not None
    session.update(
        {
            "status": "running",
            "activity": "working",
            "quick_interaction_running": True,
            "usage": {"owner": "quick_worker", "phase": "running"},
        }
    )
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context, page, page_errors = await _open_conversation(
            chrome.browser,
            conversation_browser_server,
            api,
            theme="standard",
            viewport=(1280, 900),
        )
        try:
            await page.locator("#conversation-session-stop").click()
            await expect(page.locator("#conversation-stop-dialog")).to_be_visible()
            await expect(page.locator("#conversation-stop-description")).to_contain_text(
                "Main Session"
            )
            await page.locator("#conversation-stop-cancel").click()
            await expect(page.locator("#conversation-stop-dialog")).not_to_be_visible()
            assert "POST /api/codex/sessions/session-1/stop" not in api.requested_paths

            await page.locator("#conversation-session-stop").click()
            await page.locator("#conversation-stop-confirm").click()
            await expect(page.locator("#conversation-stop-dialog")).not_to_be_visible()
            await expect(page.locator("#conversation-session-stop")).to_be_disabled()
        finally:
            await context.close()

    assert page_errors == []
    assert "POST /api/codex/sessions/session-1/stop" in api.requested_paths


async def test_conversation_keeps_draft_editable_while_session_is_working(
    conversation_browser_server: str,
) -> None:
    api = ConversationApi()
    session = api._find_session("session-1")
    assert session is not None
    session.update(
        {
            "status": "running",
            "activity": "working",
            "quick_interaction_running": True,
            "usage": {"owner": "quick_worker", "phase": "running"},
        }
    )
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context, page, page_errors = await _open_conversation(
            chrome.browser,
            conversation_browser_server,
            api,
            theme="standard",
            viewport=(1280, 900),
        )
        try:
            await expect(page.locator("#conversation-submit")).to_be_disabled()
            await expect(page.locator("#conversation-prompt")).to_be_enabled()
            await page.locator("#conversation-prompt").fill("Prepare the next task")
            await expect(page.locator("#conversation-prompt")).to_have_value(
                "Prepare the next task"
            )
            await expect(page.locator("#conversation-submit")).to_be_disabled()
        finally:
            await context.close()

    assert page_errors == []


async def test_old_session_recovery_failure_does_not_override_switched_session(
    conversation_browser_server: str,
) -> None:
    api = ConversationApi()
    api.fail_submission_recovery = True
    browser_session = session_factory()
    async with browser_session(ensure_page=False) as chrome:
        context, page, page_errors = await _open_conversation(
            chrome.browser,
            conversation_browser_server,
            api,
            theme="standard",
            viewport=(1280, 900),
        )
        try:
            await page.locator("#conversation-prompt").fill("Fail this task")
            await page.locator("#conversation-submit").click()
            await asyncio.wait_for(api.recovery_started.wait(), timeout=5)

            await page.locator("[data-session-id='session-2']").click()
            await expect(page.locator("#conversation-session-title")).to_have_text(
                "Second Session"
            )
            await expect(page.locator("#conversation-submit")).to_be_disabled()
            await page.locator("#conversation-prompt").fill("Second session task")
            await expect(page.locator("#conversation-submit")).to_be_enabled()

            api.recovery_release.set()
            await page.wait_for_timeout(100)
            await expect(page.locator("#conversation-session-title")).to_have_text(
                "Second Session"
            )
            await expect(page.locator("#conversation-submit")).to_be_enabled()
            await expect(page.locator("#conversation-submit-message")).not_to_contain_text(
                "Old Session recovery failed"
            )
        finally:
            api.recovery_release.set()
            await context.close()

    assert page_errors == []
