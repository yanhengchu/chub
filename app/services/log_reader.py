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
