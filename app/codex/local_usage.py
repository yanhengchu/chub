from __future__ import annotations

import json
import os
import stat
import time
from datetime import date, datetime, time as datetime_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


class CodexLocalUsageUnavailable(Exception):
    """Raised when local Codex usage cannot be collected completely and safely."""


class CodexLocalUsageReader:
    """Sum structured token counters from this user's local Codex sessions."""

    MAX_DISCOVERED_FILES = 10_000
    MAX_CANDIDATE_FILES = 512
    MAX_FILE_BYTES = 32 * 1024 * 1024
    MAX_TOTAL_BYTES = 64 * 1024 * 1024
    MAX_LINE_BYTES = 2 * 1024 * 1024

    def __init__(self, codex_home: Path | None = None) -> None:
        if codex_home is None:
            configured_home = os.environ.get("CODEX_HOME")
            codex_home = (
                Path(configured_home) if configured_home else Path.home() / ".codex"
            )
        self._codex_home = codex_home.expanduser()

    def read_today(
        self,
        *,
        today: date,
        timezone: ZoneInfo,
        timeout_seconds: float,
    ) -> int:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        if timeout_seconds <= 0:
            raise CodexLocalUsageUnavailable("timeout")

        day_start = datetime.combine(
            today,
            datetime_time.min,
            tzinfo=timezone,
        ).timestamp()
        candidates = self._candidate_files(day_start=day_start, deadline=deadline)

        usage_by_session: dict[str, int] = {}
        for path, size in candidates:
            self._check_deadline(deadline)
            parsed = self._read_file(
                path,
                size=size,
                today=today,
                timezone=timezone,
                deadline=deadline,
            )
            if parsed is None:
                continue
            session_id, tokens = parsed
            usage_by_session[session_id] = max(
                tokens,
                usage_by_session.get(session_id, 0),
            )
        return sum(usage_by_session.values())

    def _candidate_files(
        self,
        *,
        day_start: float,
        deadline: float,
    ) -> list[tuple[Path, int]]:
        candidates: list[tuple[Path, int]] = []
        discovered = 0
        total_bytes = 0
        roots_found = False

        def fail_walk(_error: OSError) -> None:
            raise CodexLocalUsageUnavailable("directory_read_failed")

        for root in (
            self._codex_home / "sessions",
            self._codex_home / "archived_sessions",
        ):
            self._check_deadline(deadline)
            try:
                root_stat = root.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise CodexLocalUsageUnavailable("directory_read_failed") from exc
            if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
                raise CodexLocalUsageUnavailable("session_root_invalid")
            roots_found = True

            for directory, directory_names, file_names in os.walk(
                root,
                topdown=True,
                followlinks=False,
                onerror=fail_walk,
            ):
                self._check_deadline(deadline)
                directory_path = Path(directory)
                directory_names[:] = [
                    name
                    for name in directory_names
                    if not (directory_path / name).is_symlink()
                ]
                for file_name in file_names:
                    if not file_name.endswith(".jsonl"):
                        continue
                    discovered += 1
                    if discovered > self.MAX_DISCOVERED_FILES:
                        raise CodexLocalUsageUnavailable("too_many_session_files")

                    path = directory_path / file_name
                    try:
                        file_stat = path.lstat()
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        raise CodexLocalUsageUnavailable("session_stat_failed") from exc
                    if not stat.S_ISREG(file_stat.st_mode):
                        continue
                    if file_stat.st_mtime < day_start:
                        continue
                    if file_stat.st_size > self.MAX_FILE_BYTES:
                        raise CodexLocalUsageUnavailable("session_file_too_large")

                    total_bytes += file_stat.st_size
                    if total_bytes > self.MAX_TOTAL_BYTES:
                        raise CodexLocalUsageUnavailable("session_data_too_large")
                    candidates.append((path, file_stat.st_size))
                    if len(candidates) > self.MAX_CANDIDATE_FILES:
                        raise CodexLocalUsageUnavailable("too_many_recent_sessions")

        if not roots_found:
            raise CodexLocalUsageUnavailable("session_root_unavailable")
        return candidates

    def _read_file(
        self,
        path: Path,
        *,
        size: int,
        today: date,
        timezone: ZoneInfo,
        deadline: float,
    ) -> tuple[str, int] | None:
        session_id: str | None = None
        model_provider: str | None = None
        previous_total: int | None = None
        today_tokens = 0
        saw_token_count = False

        try:
            with path.open("rb") as handle:
                consumed = 0
                while consumed < size:
                    self._check_deadline(deadline)
                    remaining = size - consumed
                    line = handle.readline(min(self.MAX_LINE_BYTES + 1, remaining))
                    if not line:
                        break
                    consumed += len(line)

                    if len(line) > self.MAX_LINE_BYTES:
                        consumed += self._discard_line(
                            handle,
                            remaining=size - consumed,
                            deadline=deadline,
                            already_complete=line.endswith(b"\n"),
                        )
                        continue
                    if not line.endswith(b"\n") and consumed < size:
                        consumed += self._discard_line(
                            handle,
                            remaining=size - consumed,
                            deadline=deadline,
                            already_complete=False,
                        )
                        continue
                    if b'"session_meta"' not in line and b'"token_count"' not in line:
                        continue

                    try:
                        value = json.loads(line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        if consumed >= size and not line.endswith(b"\n"):
                            break
                        raise CodexLocalUsageUnavailable(
                            "session_line_invalid"
                        ) from exc
                    if not isinstance(value, dict):
                        continue

                    value_type = value.get("type")
                    payload = value.get("payload")
                    if value_type == "session_meta" and isinstance(payload, dict):
                        candidate_id = payload.get("id")
                        candidate_provider = payload.get("model_provider")
                        if isinstance(candidate_id, str) and candidate_id:
                            session_id = candidate_id
                        if isinstance(candidate_provider, str):
                            model_provider = candidate_provider
                        continue
                    if (
                        value_type != "event_msg"
                        or not isinstance(payload, dict)
                        or payload.get("type") != "token_count"
                    ):
                        continue

                    total_tokens = self._total_tokens(payload)
                    if total_tokens is None:
                        continue
                    event_time = self._event_time(value.get("timestamp"))
                    if event_time is None:
                        raise CodexLocalUsageUnavailable("token_timestamp_invalid")

                    delta = (
                        total_tokens
                        if previous_total is None or total_tokens < previous_total
                        else total_tokens - previous_total
                    )
                    if event_time.astimezone(timezone).date() == today:
                        today_tokens += delta
                    previous_total = total_tokens
                    saw_token_count = True
        except FileNotFoundError as exc:
            raise CodexLocalUsageUnavailable("session_moved_during_read") from exc
        except OSError as exc:
            raise CodexLocalUsageUnavailable("session_read_failed") from exc

        if not saw_token_count:
            return None
        if session_id is None:
            raise CodexLocalUsageUnavailable("session_identity_missing")
        if model_provider != "openai":
            return None
        return session_id, today_tokens

    def _discard_line(
        self,
        handle: Any,
        *,
        remaining: int,
        deadline: float,
        already_complete: bool,
    ) -> int:
        if already_complete:
            return 0
        discarded = 0
        while discarded < remaining:
            self._check_deadline(deadline)
            chunk = handle.readline(min(64 * 1024, remaining - discarded))
            if not chunk:
                break
            discarded += len(chunk)
            if chunk.endswith(b"\n"):
                break
        return discarded

    @staticmethod
    def _total_tokens(payload: dict[str, Any]) -> int | None:
        info = payload.get("info")
        total = info.get("total_token_usage") if isinstance(info, dict) else None
        tokens = total.get("total_tokens") if isinstance(total, dict) else None
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0:
            return None
        return tokens

    @staticmethod
    def _event_time(value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise CodexLocalUsageUnavailable("timeout")
