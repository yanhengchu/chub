from __future__ import annotations

import unicodedata
from collections.abc import Iterator

from wcwidth import wcswidth


ELLIPSIS = "…"
ELLIPSIS_WIDTH = 2
ZERO_WIDTH_JOINER = "\u200d"


def _is_regional_indicator(char: str) -> bool:
    return "\U0001f1e6" <= char <= "\U0001f1ff"


def _is_cluster_extension(char: str) -> bool:
    codepoint = ord(char)
    return (
        unicodedata.category(char) in {"Mn", "Mc", "Me"}
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0x1F3FB <= codepoint <= 0x1F3FF
        or 0xE0020 <= codepoint <= 0xE007F
        or 0xE0100 <= codepoint <= 0xE01EF
    )


def _text_clusters(value: str) -> Iterator[str]:
    index = 0
    while index < len(value):
        end = index + 1
        if (
            _is_regional_indicator(value[index])
            and end < len(value)
            and _is_regional_indicator(value[end])
        ):
            end += 1
        while end < len(value) and _is_cluster_extension(value[end]):
            end += 1
        while end + 1 < len(value) and value[end] == ZERO_WIDTH_JOINER:
            end += 2
            while end < len(value) and _is_cluster_extension(value[end]):
                end += 1
        yield value[index:end]
        index = end


def display_width(value: str) -> int:
    width = 0
    for cluster in _text_clusters(value):
        if cluster == ELLIPSIS:
            width += ELLIPSIS_WIDTH
            continue
        width += max(0, wcswidth(cluster))
    return width


def truncate_display_width(value: str, max_width: int) -> str:
    if display_width(value) <= max_width:
        return value

    available_width = max(0, max_width - ELLIPSIS_WIDTH)
    kept: list[str] = []
    used_width = 0
    for cluster in _text_clusters(value):
        cluster_width = display_width(cluster)
        if used_width + cluster_width > available_width:
            break
        kept.append(cluster)
        used_width += cluster_width
    return "".join(kept).rstrip() + ELLIPSIS
