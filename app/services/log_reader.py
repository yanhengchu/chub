from __future__ import annotations

import re
from pathlib import Path


MAX_READ_BYTES = 256 * 1024
MAX_LINE_BYTES = 8 * 1024
TRUNCATED_SUFFIX = " [truncated]"
BEARER_PATTERN = re.compile(
    r"\bBearer\s+[A-Za-z0-9._~+/=-]+",
    re.IGNORECASE,
)


class LogReadError(Exception):
    pass


def redact_log_line(
    line: str,
    sensitive_values: tuple[str, ...],
    *,
    max_line_bytes: int | None = MAX_LINE_BYTES,
) -> str:
    redacted = _redact(line, sensitive_values)
    return (
        _truncate_line(redacted, max_line_bytes)
        if max_line_bytes is not None
        else redacted
    )


def _truncate_line(value: str, max_line_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_line_bytes:
        return value
    suffix = TRUNCATED_SUFFIX.encode("utf-8")
    if max_line_bytes <= len(suffix):
        return suffix[:max_line_bytes].decode("ascii")
    content_limit = max(0, max_line_bytes - len(suffix))
    content = encoded[:content_limit].decode("utf-8", errors="ignore")
    return content + TRUNCATED_SUFFIX


def _redact(line: str, sensitive_values: tuple[str, ...]) -> str:
    redacted = line
    for value in sensitive_values:
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    return BEARER_PATTERN.sub("Bearer [REDACTED]", redacted)


def tail_log(
    path: Path,
    lines: int,
    *,
    sensitive_values: tuple[str, ...] = (),
    max_read_bytes: int = MAX_READ_BYTES,
    max_line_bytes: int = MAX_LINE_BYTES,
) -> list[str]:
    try:
        with path.open("rb") as file:
            file.seek(0, 2)
            size = file.tell()
            read_size = min(size, max_read_bytes)
            start = size - read_size
            preceding = b""
            if start > 0:
                file.seek(start - 1)
                preceding = file.read(1)
            else:
                file.seek(0)
            content = file.read(read_size)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise LogReadError("Unable to read Hub log") from exc

    if size > read_size and preceding != b"\n":
        first_newline = content.find(b"\n")
        content = (
            content[first_newline + 1 :]
            if first_newline >= 0
            else content[-max_line_bytes:]
        )

    selected = content.splitlines()[-lines:]
    return [
        _truncate_line(
            _redact(
                line.decode("utf-8", errors="replace"),
                sensitive_values,
            ),
            max_line_bytes,
        )
        for line in selected
    ]


def read_log_page(
    path: Path,
    lines: int,
    *,
    before: int | None = None,
    sensitive_values: tuple[str, ...] = (),
) -> tuple[list[str], int | None]:
    try:
        with path.open("rb") as file:
            file.seek(0, 2)
            size = file.tell()
            end = size if before is None else min(max(before, 0), size)
            start = max(0, end - MAX_READ_BYTES)
            file.seek(start)
            content = file.read(end - start)
    except FileNotFoundError:
        return [], None
    except OSError as exc:
        raise LogReadError("Unable to read Hub log") from exc

    chunks = content.splitlines(keepends=True)
    if start > 0 and chunks:
        partial = chunks.pop(0)
        if not chunks:
            return (
                [
                    redact_log_line(
                        partial.decode("utf-8", errors="replace"),
                        sensitive_values,
                    )
                ],
                start,
            )
    selected = chunks[-lines:]
    selected_start = end - sum(len(chunk) for chunk in selected)
    next_cursor = selected_start if selected_start > 0 else None
    values = [
        redact_log_line(
            chunk.rstrip(b"\r\n").decode("utf-8", errors="replace"),
            sensitive_values,
        )
        for chunk in selected
    ]
    return values, next_cursor
