from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, urlunsplit

from app.automations.models import LinkedDocumentsTemplate


MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_LINES = 20_000
MAX_SOURCE_LINE_LENGTH = 16_384
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
MARKDOWN_LINK_PATTERN = re.compile(
    r"\[([^\]]+)\]\((https://[^\s)]+)(?:\s+[\"'][^)]*[\"'])?\)"
)
UNSAFE_FILENAME_PATTERN = re.compile(r'[\x00-\x1f<>:"/\\|?*＜＞：＂／＼uFF3C｜？＊]')


class ExtensionFailed(Exception):
    pass


@dataclass(frozen=True)
class LinkedDocument:
    name: str
    url: str


def _source_lines(path: Path) -> list[str]:
    try:
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ExtensionFailed("主周报超过关联链接解析大小限制")
        content = path.read_text(encoding="utf-8-sig")
    except ExtensionFailed:
        raise
    except (OSError, UnicodeError) as exc:
        raise ExtensionFailed("主周报无法读取关联链接") from exc
    lines = content.splitlines()
    if len(lines) > MAX_SOURCE_LINES:
        raise ExtensionFailed("主周报超过关联链接解析行数限制")
    if any(len(line) > MAX_SOURCE_LINE_LENGTH for line in lines):
        raise ExtensionFailed("主周报包含过长行，无法安全解析关联链接")
    return lines


def _section_lines(lines: list[str], section: str) -> list[str]:
    start = None
    level = None
    for index, line in enumerate(lines):
        match = HEADING_PATTERN.match(line.strip())
        if not match:
            continue
        if start is None:
            if match.group(2).strip() == section:
                start = index + 1
                level = len(match.group(1))
            continue
        if len(match.group(1)) <= (level or 0):
            return lines[start:index]
    if start is None:
        raise ExtensionFailed(f"主周报中未找到“{section}”章节")
    return lines[start:]


def extract_linked_documents(
    path: Path,
    source_url: str,
    template: LinkedDocumentsTemplate,
) -> list[LinkedDocument]:
    source_host = (urlparse(source_url).hostname or "").lower().rstrip(".")
    section = _section_lines(_source_lines(path), template.source.section)
    documents = []
    seen_urls = set()
    for line in section:
        for match in MARKDOWN_LINK_PATTERN.finditer(line):
            raw_url = html.unescape(match.group(2)).replace("\\.", ".")
            parsed = urlparse(raw_url)
            host = (parsed.hostname or "").lower().rstrip(".")
            if (
                parsed.scheme != "https"
                or host != source_host
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port is not None
                or not any(
                    parsed.path.startswith(prefix)
                    for prefix in template.source.allowed_paths
                )
            ):
                continue
            normalized_url = urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
            )
            if normalized_url in seen_urls:
                continue
            seen_urls.add(normalized_url)
            name = re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
            documents.append(
                LinkedDocument(name=name or f"document-{len(documents) + 1:02d}", url=normalized_url)
            )
            if len(documents) > template.source.max_documents:
                raise ExtensionFailed("关联文档数量超过配置上限")
    if not documents:
        raise ExtensionFailed("“各端周报”章节中没有可下载的同租户飞书文档")
    return documents


def linked_filename(name: str, index: int, used: set[str]) -> str:
    safe_name = UNSAFE_FILENAME_PATTERN.sub("-", name)
    safe_name = re.sub(r"\s+", " ", safe_name).strip(" .-")[:80].rstrip(" .-")
    if not safe_name:
        safe_name = f"document-{index:02d}"
    candidate = f"{index:02d}-{safe_name}.md"
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{index:02d}-{safe_name}-{suffix}.md"
        suffix += 1
    used.add(candidate.casefold())
    return candidate
