from __future__ import annotations

import hashlib
import json
import logging
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from app.codex.models import (
    CodexDailyTokenUsage,
    CodexQuotaData,
    CodexQuotaWindow,
    CodexTokenUsageData,
    utc_now,
)


LOGGER = logging.getLogger("hub.codex.rate_limits")


@dataclass(frozen=True)
class CodexAccountCollection:
    auth_type: Literal["chatgpt", "apiKey"] | None
    quota: CodexQuotaData | None = None
    usage: CodexTokenUsageData | None = None
    message: str | None = None
    identity_key: str | None = None


class CodexRateLimitService:
    """Read the signed-in Codex account's quota and usage through fixed RPC APIs."""

    CACHE_SECONDS = 5 * 60
    REQUEST_TIMEOUT_SECONDS = 8
    MAX_RESPONSE_LINES = 100
    MAX_RESPONSE_LINE_BYTES = 64 * 1024

    def __init__(self) -> None:
        self._cached: CodexQuotaData | None = None
        self._cached_at = 0.0
        self._usage_cached: CodexTokenUsageData | None = None
        self._usage_cached_at = 0.0
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

    def read_usage(self, *, force: bool = False) -> CodexTokenUsageData:
        with self._lock:
            if (
                not force
                and self._usage_cached is not None
                and time.monotonic() - self._usage_cached_at < self.CACHE_SECONDS
            ):
                return self._usage_cached

            result = self._read_usage_from_codex()
            if result.status == "available":
                self._usage_cached = result
                self._usage_cached_at = time.monotonic()
                return result
            if self._usage_cached is not None:
                return self._usage_cached.model_copy(update={"message": result.message})
            return result

    def read_account_status(
        self,
        *,
        force: bool = False,
    ) -> tuple[CodexQuotaData, CodexTokenUsageData]:
        with self._lock:
            cache_fresh = (
                self._cached is not None
                and self._usage_cached is not None
                and time.monotonic() - self._cached_at < self.CACHE_SECONDS
                and time.monotonic() - self._usage_cached_at < self.CACHE_SECONDS
            )
            if not force and cache_fresh:
                return self._cached, self._usage_cached

            codex_available = shutil.which("codex") is not None
            responses = (
                self._request_codex_many(
                    {
                        2: "account/rateLimits/read",
                        3: "account/usage/read",
                    }
                )
                if codex_available
                else {}
            )
            unavailable_message = (
                "额度信息暂时不可用。"
                if codex_available
                else "未检测到 Codex CLI。"
            )
            usage_unavailable_message = (
                "Token 用量暂时不可用。"
                if codex_available
                else "未检测到 Codex CLI。"
            )
            quota = (
                self._parse_response(responses[2])
                if 2 in responses
                else self._unavailable(unavailable_message)
            )
            usage = (
                self._parse_usage_response(responses[3])
                if 3 in responses
                else self._usage_unavailable(usage_unavailable_message)
            )
            if quota.status == "available":
                self._cached = quota
                self._cached_at = time.monotonic()
            elif self._cached is not None:
                quota = self._cached.model_copy(update={"message": quota.message})
            if usage.status == "available":
                self._usage_cached = usage
                self._usage_cached_at = time.monotonic()
            elif self._usage_cached is not None:
                usage = self._usage_cached.model_copy(update={"message": usage.message})
            return quota, usage

    def collect_ai_account_status(
        self,
        *,
        timeout_seconds: float,
    ) -> CodexAccountCollection:
        """Detect auth and only read account usage for ChatGPT login."""
        if shutil.which("codex") is None:
            return CodexAccountCollection(None, message="未检测到 Codex CLI。")

        process: subprocess.Popen[str] | None = None
        try:
            process = self._start_process()
            assert process.stdin is not None
            lines = self._response_queue(process)
            deadline = time.monotonic() + max(0.1, timeout_seconds)
            self._write_messages(
                process,
                [
                    *self._initialization_messages(),
                    {"method": "account/read", "id": 2, "params": {}},
                ],
            )
            account_response = self._read_response_queue(
                lines,
                response_ids={2},
                deadline=deadline,
            ).get(2)
            auth_type = self._parse_auth_type(account_response)
            identity_key = self._parse_account_identity(account_response)
            if auth_type != "chatgpt":
                return CodexAccountCollection(
                    auth_type,
                    message=(
                        None
                        if auth_type == "apiKey"
                        else "无法确认 Codex 认证类型。"
                    ),
                    identity_key=identity_key,
                )

            self._write_messages(
                process,
                [
                    {"method": "account/rateLimits/read", "id": 3, "params": {}},
                    {"method": "account/usage/read", "id": 4, "params": {}},
                ],
            )
            responses = self._read_response_queue(
                lines,
                response_ids={3, 4},
                deadline=deadline,
            )
            quota = (
                self._parse_response(responses[3])
                if 3 in responses
                else self._unavailable("额度信息暂时不可用。")
            )
            usage = (
                self._parse_usage_response(responses[4])
                if 4 in responses
                else self._usage_unavailable("Token 用量暂时不可用。")
            )
            return CodexAccountCollection(
                "chatgpt",
                quota=quota,
                usage=usage,
                identity_key=identity_key,
            )
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            LOGGER.info("Codex authentication request was unavailable", exc_info=True)
            return CodexAccountCollection(None, message="Codex 认证状态暂时不可用。")
        finally:
            if process is not None:
                self._stop_process(process)

    def _read_from_codex(self) -> CodexQuotaData:
        if shutil.which("codex") is None:
            return self._unavailable("未检测到 Codex CLI。")
        response = self._request_codex("account/rateLimits/read")
        if response is None:
            return self._unavailable("额度信息暂时不可用。")
        return self._parse_response(response)

    def _read_usage_from_codex(self) -> CodexTokenUsageData:
        if shutil.which("codex") is None:
            return self._usage_unavailable("未检测到 Codex CLI。")
        response = self._request_codex("account/usage/read")
        if response is None:
            return self._usage_unavailable("Token 用量暂时不可用。")
        return self._parse_usage_response(response)

    def _request_codex(self, method: str) -> dict[str, Any] | None:
        return self._request_codex_many({2: method}).get(2)

    def _request_codex_many(
        self,
        methods: dict[int, str],
    ) -> dict[int, dict[str, Any]]:
        if shutil.which("codex") is None:
            return {}

        process: subprocess.Popen[str] | None = None
        try:
            process = self._start_process()
            messages = self._initialization_messages()
            messages.extend(
                {"method": method, "id": response_id, "params": {}}
                for response_id, method in methods.items()
            )
            self._write_messages(process, messages)
            return self._read_responses(process, response_ids=set(methods))
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
            LOGGER.info("Codex account request was unavailable", exc_info=True)
            return {}
        finally:
            if process is not None:
                self._stop_process(process)

    def _read_responses(
        self,
        process: subprocess.Popen[str],
        *,
        response_ids: set[int],
    ) -> dict[int, dict[str, Any]]:
        return self._read_response_queue(
            self._response_queue(process),
            response_ids=response_ids,
            deadline=time.monotonic() + self.REQUEST_TIMEOUT_SECONDS,
        )

    def _read_response_queue(
        self,
        lines: queue.Queue[str | None],
        *,
        response_ids: set[int],
        deadline: float,
    ) -> dict[int, dict[str, Any]]:
        responses: dict[int, dict[str, Any]] = {}
        for _ in range(self.MAX_RESPONSE_LINES):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if responses:
                    return responses
                raise TimeoutError("Codex rate limit request timed out")
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty as error:
                if responses:
                    return responses
                raise TimeoutError("Codex rate limit request timed out") from error
            if line is None:
                break
            if len(line.encode("utf-8")) > self.MAX_RESPONSE_LINE_BYTES:
                raise ValueError("Codex app-server response line is too large")
            message = json.loads(line)
            if not isinstance(message, dict):
                continue
            response_id = message.get("id")
            if response_id in response_ids:
                responses[response_id] = message
                if response_ids <= responses.keys():
                    return responses
        if responses:
            return responses
        raise ValueError("Codex app-server did not return account data")

    @staticmethod
    def _start_process() -> subprocess.Popen[str]:
        return subprocess.Popen(
            ["codex", "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )

    @staticmethod
    def _initialization_messages() -> list[dict[str, Any]]:
        return [
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
        ]

    @staticmethod
    def _write_messages(
        process: subprocess.Popen[str],
        messages: list[dict[str, Any]],
    ) -> None:
        assert process.stdin is not None
        for message in messages:
            process.stdin.write(f"{json.dumps(message)}\n")
        process.stdin.flush()

    @staticmethod
    def _response_queue(
        process: subprocess.Popen[str],
    ) -> queue.Queue[str | None]:
        assert process.stdout is not None
        lines: queue.Queue[str | None] = queue.Queue()

        def consume_output() -> None:
            assert process.stdout is not None
            for line in process.stdout:
                lines.put(line)
            lines.put(None)

        threading.Thread(target=consume_output, daemon=True).start()
        return lines

    @staticmethod
    def _parse_auth_type(
        response: dict[str, Any] | None,
    ) -> Literal["chatgpt", "apiKey"] | None:
        if not isinstance(response, dict):
            return None
        result = response.get("result")
        account = result.get("account") if isinstance(result, dict) else None
        auth_type = account.get("type") if isinstance(account, dict) else None
        if auth_type in {"chatgpt", "apiKey"}:
            return auth_type
        if (
            isinstance(result, dict)
            and account is None
            and result.get("requiresOpenaiAuth") is False
        ):
            return "apiKey"
        return None

    @staticmethod
    def _parse_account_identity(response: dict[str, Any] | None) -> str | None:
        if not isinstance(response, dict):
            return None
        result = response.get("result")
        account = result.get("account") if isinstance(result, dict) else None
        if not isinstance(account, dict):
            return None
        identifiers = [account.get("id"), account.get("email")]
        normalized = [
            value.strip().lower()
            for value in identifiers
            if isinstance(value, str) and value.strip()
        ]
        if not normalized:
            return None
        return hashlib.sha256("\0".join(normalized).encode("utf-8")).hexdigest()

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

    def _parse_usage_response(
        self,
        response: dict[str, Any],
    ) -> CodexTokenUsageData:
        result = response.get("result")
        if not isinstance(result, dict):
            return self._usage_unavailable("当前 Codex 账户未提供 Token 用量。")
        values = result.get("dailyUsageBuckets")
        if not isinstance(values, list):
            return self._usage_unavailable("当前 Codex 账户未提供每日 Token 用量。")

        buckets: list[CodexDailyTokenUsage] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            try:
                bucket = CodexDailyTokenUsage.model_validate(
                    {
                        "start_date": value.get("startDate"),
                        "tokens": value.get("tokens"),
                    }
                )
            except ValueError:
                continue
            buckets.append(bucket)
        return CodexTokenUsageData(status="available", daily_usage=buckets)

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
    def _usage_unavailable(message: str) -> CodexTokenUsageData:
        return CodexTokenUsageData(status="unavailable", message=message)

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
