import pytest

from app.core import platform
from app.core.platform import detect_platform


def test_detect_macos() -> None:
    assert detect_platform("Darwin") == "macos"


def test_detect_windows() -> None:
    assert detect_platform("Windows") == "windows"


def test_detect_ubuntu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "_read_os_release", lambda: {"ID": "ubuntu"})

    assert detect_platform("Linux") == "ubuntu"


def test_detect_unknown() -> None:
    assert detect_platform("Plan9") == "unknown"
