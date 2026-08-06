from __future__ import annotations

import json
import logging
import queue
import shutil
import subprocess
import threading
import time
from datetime import UTC, datetime
from typing import Any

from app.codex.models import CodexQuotaData, CodexQuotaWindow, utc_now


LOGGER = logging.getLogger("hub.codex.rate_limits")


class CodexRateLimitService:
    """Read the signed-in Codex account's rate limits through its fixed RPC API."""

    CACHE_SECONDS = 5 * 60
    REQUEST_TIMEOUT_SECONDS = 8
    MAX_RESPONSE_LINES = 100
    MAX_RESPONSE_LINE_BYTES = 64 * 1024

    def __init__(self) -> None:
        self._cached: CodexQuotaData | None = None
        self._cached_at = 0.0
        self._lock = threading.Lock()

    def read(self, *, force: bool = False) -> CodexQuotaData:
        with self._lock:
            if (
                not force
                and self._cached is not None
                and time.monotonic() - self._cached_at < self.CACHE_SECONDS
            ):
                return self._cached

            result = self._read_from_codex()
            if result.status == "available":
                self._cached = result
                self._cached_at = time.monotonic()
                return result
            if self._cached is not None:
                return self._cached.model_copy(update={"message": result.message})
            return result

    def _read_from_codex(self) -> CodexQuotaData:
        if shutil.which("codex") is None:
            return self._unavailable("未检测到 Codex CLI。")

        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                ["codex", "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                start_new_session=True,
            )
            assert process.stdin is not None
            for message in (
                {
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "clientInfo": {
                            "name": "chub",
                            "title": "Chub",
                            "version": "1",
                        }
                    },
                },
                {"method": "initialized", "params": {}},
                {"method": "account/rateLimits/read", "id": 2},
            ):
                process.stdin.write(f"{json.dumps(message)}\n")
            process.stdin.flush()
            response = self._read_response(process, response_id=2)
            return self._parse_response(response)
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            LOGGER.info("Codex rate limit request was unavailable", exc_info=True)
            return self._unavailable("额度信息暂时不可用。")
        finally:
            if process is not None:
                self._stop_process(process)

    def _read_response(
        self,
        process: subprocess.Popen[str],
        *,
        response_id: int,
    ) -> dict[str, Any]:
        assert process.stdout is not None
        lines: queue.Queue[str | None] = queue.Queue()

        def consume_output() -> None:
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=consume_output, daemon=True).start()
        deadline = time.monotonic() + self.REQUEST_TIMEOUT_SECONDS
        for _ in range(self.MAX_RESPONSE_LINES):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Codex rate limit request timed out")
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty as error:
                raise TimeoutError("Codex rate limit request timed out") from error
            if line is None:
                break
            if len(line.encode("utf-8")) > self.MAX_RESPONSE_LINE_BYTES:
                raise ValueError("Codex app-server response line is too large")
            message = json.loads(line)
            if isinstance(message, dict) and message.get("id") == response_id:
                return message
        raise ValueError("Codex app-server did not return rate limits")

    def _parse_response(self, response: dict[str, Any]) -> CodexQuotaData:
        result = response.get("result")
        if not isinstance(result, dict):
            return self._unavailable("当前 Codex 账户未提供额度信息。")
        rate_limits = result.get("rateLimits")
        if not isinstance(rate_limits, dict):
            return self._unavailable("当前 Codex 账户未提供额度信息。")

        windows: list[CodexQuotaWindow] = []
        for key in ("primary", "secondary"):
            window = rate_limits.get(key)
            if not isinstance(window, dict):
                continue
            parsed = self._parse_window(window)
            if parsed is not None:
                windows.append(parsed)
        if not windows:
            return self._unavailable("当前 Codex 账户未提供额度信息。")
        return CodexQuotaData(status="available", windows=windows)

    @staticmethod
    def _parse_window(value: dict[str, Any]) -> CodexQuotaWindow | None:
        used = value.get("usedPercent")
        duration = value.get("windowDurationMins")
        resets_at = value.get("resetsAt")
        if (
            isinstance(used, bool)
            or not isinstance(used, (int, float))
            or isinstance(duration, bool)
            or not isinstance(duration, int)
            or isinstance(resets_at, bool)
            or not isinstance(resets_at, (int, float))
            or duration < 1
        ):
            return None
        try:
            reset_time = datetime.fromtimestamp(resets_at, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
        return CodexQuotaWindow(
            remaining_percent=max(0, min(100, round(100 - used))),
            window_duration_minutes=duration,
            resets_at=reset_time,
        )

    @staticmethod
    def _unavailable(message: str) -> CodexQuotaData:
        return CodexQuotaData(status="unavailable", message=message)

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)
