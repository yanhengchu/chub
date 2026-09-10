from __future__ import annotations

from pathlib import Path

import pytest

from app.automations import codex_auth_switch as auth_switch
from app.core.config import Settings


def _write_configs(home: Path) -> None:
    home.mkdir()
    (home / "config.toml").write_text('model = "current"\n', encoding="utf-8")
    (home / "config_account.toml").write_text(
        'model = "account"\n', encoding="utf-8"
    )
    (home / "config_api.toml").write_text('model = "api"\n', encoding="utf-8")


def test_account_switch_syncs_current_config_to_api_backup(tmp_path: Path) -> None:
    home = tmp_path / ".codex"
    _write_configs(home)

    auth_switch._sync_configuration("account", home)

    assert (home / "config.toml").read_text(encoding="utf-8") == 'model = "account"\n'
    assert (home / "config_api.toml").read_text(encoding="utf-8") == 'model = "current"\n'


def test_api_switch_syncs_current_config_to_account_backup(tmp_path: Path) -> None:
    home = tmp_path / ".codex"
    _write_configs(home)

    auth_switch._sync_configuration("api", home)

    assert (home / "config.toml").read_text(encoding="utf-8") == 'model = "api"\n'
    assert (home / "config_account.toml").read_text(encoding="utf-8") == 'model = "current"\n'


@pytest.mark.parametrize(
    ("url", "input_count", "expected"),
    [
        ("https://auth.openai.com/codex/device", 0, "account"),
        ("https://auth.openai.com/sign-in-with-chatgpt/codex/consent", 0, "consent"),
        ("https://auth.openai.com/deviceauth/callback", 9, "code"),
        ("https://auth.openai.com/unknown", 0, "unknown"),
    ],
)
def test_device_auth_page_state_is_explicit(
    url: str,
    input_count: int,
    expected: str,
) -> None:
    assert auth_switch._device_auth_page_state(url, input_count, 9) == expected


def test_device_auth_page_state_rejects_unexpected_login_redirect() -> None:
    with pytest.raises(auth_switch.CodexAuthSwitchError, match="人工处理"):
        auth_switch._device_auth_page_state("https://chatgpt.com/auth/login", 0, 9)


def test_device_auth_page_state_rejects_unexpected_input_shape() -> None:
    with pytest.raises(auth_switch.CodexAuthSwitchError, match="格式已变化"):
        auth_switch._device_auth_page_state("https://auth.openai.com/codex/device", 1, 9)


def test_device_auth_page_state_recognizes_account_controls_on_dynamic_path() -> None:
    assert auth_switch._device_auth_page_state(
        "https://auth.openai.com/account/select",
        0,
        9,
        control_count=1,
    ) == "account"


def test_device_code_values_must_match_each_character_before_confirmation() -> None:
    assert auth_switch._device_code_values_match(
        ["A", "B", "C", "D", "E", "F", "G", "H", "I"],
        "ABCDEFGHI",
    )
    assert not auth_switch._device_code_values_match(
        ["A", "B", "C", "D", "E", "F", "G", "H", "0"],
        "ABCDEFGHI",
    )
    assert not auth_switch._device_code_values_match(
        ["A", "B", "C"],
        "ABCDEFGHI",
    )


@pytest.mark.anyio
async def test_device_auth_completes_each_page_stage_before_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    values = [""] * 9

    class FakeInput:
        def __init__(self, index: int) -> None:
            self.index = index

        async def fill(self, value: str) -> None:
            values[self.index] = value
            events.append(f"fill:{self.index}:{value}")

        async def input_value(self) -> str:
            return values[self.index]

        async def blur(self) -> None:
            return None

        async def press(self, _key: str) -> None:
            return None

    class FakeLocator:
        def __init__(self, page: "FakePage", selector: str) -> None:
            self.page = page
            self.selector = selector

        @property
        def first(self) -> "FakeLocator":
            return self

        async def count(self) -> int:
            if self.selector == "input:visible":
                return 9 if self.page.stage == "code" else 0
            if self.selector == auth_switch.ACCOUNT_CONTROL_SELECTOR:
                return 1 if self.page.stage == "account" else 0
            if self.selector == 'button[value="grant"]:visible':
                return 1 if self.page.stage in {"consent", "code"} else 0
            return 0

        def nth(self, index: int) -> FakeInput:
            return FakeInput(index)

        async def click(self, **_kwargs) -> None:
            if self.selector == auth_switch.ACCOUNT_CONTROL_SELECTOR:
                events.append("select-account")
                self.page.stage = "consent"
                return
            if self.page.stage == "consent":
                events.append("confirm-consent")
                self.page.stage = "code"
                return
            events.append("confirm-device-code")
            self.page.stage = "complete"

        async def is_enabled(self) -> bool:
            return self.page.stage == "code" and all(values)

    class FakePage:
        def __init__(self) -> None:
            self.stage = "account"
            self.closed = False

        @property
        def url(self) -> str:
            return {
                "account": auth_switch.DEVICE_URL,
                "consent": "https://auth.openai.com/sign-in-with-chatgpt/codex/consent",
                "code": "https://auth.openai.com/deviceauth/callback",
                "complete": "https://auth.openai.com/deviceauth/success",
            }[self.stage]

        async def goto(self, url: str, **_kwargs) -> None:
            assert url == auth_switch.DEVICE_URL
            events.append("open-device-page")

        def locator(self, selector: str) -> FakeLocator:
            return FakeLocator(self, selector)

        async def wait_for_timeout(self, _milliseconds: int) -> None:
            return None

        def is_closed(self) -> bool:
            return self.closed

        async def close(self) -> None:
            self.closed = True
            events.append("close-page")

    page = FakePage()

    class FakeChrome:
        class Context:
            async def new_page(self) -> FakePage:
                return page

        context = Context()

    class FakeSession:
        async def __aenter__(self) -> FakeChrome:
            return FakeChrome()

        async def __aexit__(self, *_args) -> None:
            return None

    monkeypatch.setattr(
        auth_switch,
        "session_factory",
        lambda: lambda: FakeSession(),
    )

    await auth_switch._complete_device_auth("ABCD-EFGHI")

    assert values == list("ABCDEFGHI")
    assert events == [
        "open-device-page",
        "select-account",
        "confirm-consent",
        *[f"fill:{index}:{value}" for index, value in enumerate("ABCDEFGHI")],
        "confirm-device-code",
        "close-page",
    ]


def test_account_switch_uses_headed_yanheng_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches: list[bool] = []

    class ChromeDebug:
        DEFAULT_USER_DATA_DIR = Path("/tmp/chub-debug-chrome")

        @staticmethod
        def status():
            return type("Status", (), {"state": "stopped"})()

        @staticmethod
        def start(*, headless: bool):
            launches.append(headless)
            return type(
                "Started",
                (),
                {"state": "running", "profile_directory": auth_switch.TARGET_PROFILE},
            )()

    class ProfileStore:
        @staticmethod
        def select_profile(_, profile: str) -> None:
            assert profile == auth_switch.TARGET_PROFILE

    monkeypatch.setattr(auth_switch, "_chrome_debug_module", lambda: ChromeDebug)
    monkeypatch.setattr(
        auth_switch,
        "_profile_modules",
        lambda: (None, None, ProfileStore()),
    )

    auth_switch._switch_browser_for_account(
        auth_switch.BrowserSnapshot(profile="Profile 4", mode="headless"),
    )

    assert launches == [False]


def test_account_switch_restores_browser_after_success(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.automations.runtime_dir = tmp_path / "runtime"
    home = tmp_path / ".codex"
    _write_configs(home)
    events: list[str] = []
    snapshot = auth_switch.BrowserSnapshot(profile="Profile 4", mode="headless")

    monkeypatch.setattr(auth_switch.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(auth_switch, "_capture_browser_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        auth_switch,
        "_switch_browser_for_account",
        lambda value: events.append(f"switch:{value.profile}"),
    )
    monkeypatch.setattr(
        auth_switch,
        "_start_device_auth",
        lambda _: (123, 9, "ABCD-EFGHI"),
    )

    async def authorize(_: str) -> None:
        events.append("authorize")

    monkeypatch.setattr(auth_switch, "_complete_device_auth", authorize)
    monkeypatch.setattr(
        auth_switch,
        "_wait_for_success",
        lambda pid, descriptor: events.append(f"wait:{pid}:{descriptor}"),
    )
    monkeypatch.setattr(
        auth_switch,
        "_assert_status",
        lambda _, mode: events.append(f"status:{mode}"),
    )
    monkeypatch.setattr(
        auth_switch,
        "_restore_browser",
        lambda value: events.append(f"restore:{value.profile}"),
    )

    result = auth_switch.switch("account", codex_home=home, settings=settings)

    assert result.mode == "account"
    assert events == [
        "switch:Profile 4",
        "authorize",
        "wait:123:9",
        "status:account",
        "restore:Profile 4",
    ]
    assert (home / "config.toml").read_text(encoding="utf-8") == 'model = "account"\n'
    assert (home / "config_api.toml").read_text(encoding="utf-8") == 'model = "current"\n'


def test_account_switch_restores_browser_when_activation_fails(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.automations.runtime_dir = tmp_path / "runtime"
    snapshot = auth_switch.BrowserSnapshot(profile="Profile 4", mode="headless")
    restored: list[auth_switch.BrowserSnapshot] = []

    monkeypatch.setattr(auth_switch.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(auth_switch, "_capture_browser_snapshot", lambda: snapshot)
    monkeypatch.setattr(
        auth_switch,
        "_switch_browser_for_account",
        lambda _: (_ for _ in ()).throw(auth_switch.CodexAuthSwitchError("failed")),
    )
    monkeypatch.setattr(auth_switch, "_restore_browser", restored.append)

    with pytest.raises(auth_switch.CodexAuthSwitchError, match="failed"):
        auth_switch.switch("account", settings=settings)

    assert restored == [snapshot]


def test_api_switch_verifies_logout_before_syncing_configuration(
    settings: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings.automations.runtime_dir = tmp_path / "runtime"
    home = tmp_path / ".codex"
    _write_configs(home)
    events: list[str] = []

    monkeypatch.setattr(auth_switch.shutil, "which", lambda _: "/usr/bin/codex")
    monkeypatch.setattr(auth_switch, "_logout", lambda _: events.append("logout"))
    monkeypatch.setattr(
        auth_switch,
        "_assert_status",
        lambda _, mode: events.append(f"status:{mode}"),
    )

    result = auth_switch.switch("api", codex_home=home, settings=settings)

    assert result.mode == "api"
    assert events == ["logout", "status:api"]
    assert (home / "config.toml").read_text(encoding="utf-8") == 'model = "api"\n'
    assert (home / "config_account.toml").read_text(encoding="utf-8") == 'model = "current"\n'
