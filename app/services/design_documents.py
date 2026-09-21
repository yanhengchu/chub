from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
from threading import Lock
from urllib.parse import unquote, urlsplit, urlunsplit

import bleach
import markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS_ROOT = PROJECT_ROOT / "docs"
DOCUMENTS_INDEX = DOCUMENTS_ROOT / "design_documents.json"
MAX_DOCUMENT_BYTES = 512 * 1024
MAX_DOCUMENT_SOURCE_CHARS = 6_000
DOCUMENT_INDEX_VERSION = 3
DOCUMENT_STATUSES = (
    "调研中",
    "待实现",
    "进行中",
    "待验收",
    "第一阶段已验收",
    "已验收",
    "持续维护",
)
DOCUMENT_CORE_STATUSES = (
    "进行中",
    "待验收",
    "已验收",
    "持续维护",
)
ALLOWED_DOCUMENT_STATUSES = frozenset(DOCUMENT_STATUSES)
DOCUMENT_CATEGORIES = (
    ("project_baseline", "项目基线", "定义项目定位、全局边界和当前能力。"),
    ("delivery_requirement", "专项需求与设计", "定义可独立讨论、实施和验收的专题能力。"),
    ("independent_learning", "独立学习资料", "记录独立于当前主交付链路的探索与学习。"),
    ("historical_archive", "历史归档", "保留已结束阶段的冻结资料，仅用于追溯。"),
)
DOCUMENT_CATEGORY_LABELS = {
    category: label for category, label, _description in DOCUMENT_CATEGORIES
}
ALLOWED_DOCUMENT_CATEGORIES = frozenset(DOCUMENT_CATEGORY_LABELS)
DOCUMENT_GROUPS = (
    (
        "project-core-documents",
        "项目核心文档",
        "项目说明、总体架构与当前能力契约。",
        frozenset({"project_baseline"}),
    ),
    (
        "deployment-interface",
        "部署与界面规范",
        "正式部署、安装方式与工作台界面规范。",
        frozenset({"delivery_requirement"}),
    ),
    (
        "delivery-automation",
        "交付与自动化",
        "Deliveryline 需求交付与周报自动化。",
        frozenset({"delivery_requirement"}),
    ),
    (
        "ai-runtime",
        "AI Runtime",
        "Runtime 架构、插件与 Codex 专属能力。",
        frozenset({"delivery_requirement"}),
    ),
    (
        "task-orchestration",
        "任务编排",
        "Session 状态、Quick Worker 与通用/微信任务编排。",
        frozenset({"delivery_requirement"}),
    ),
    (
        "external-integration-learning",
        "外部集成与独立学习",
        "OpenClaw 定制集成与本机大模型学习资料。",
        frozenset({"delivery_requirement", "independent_learning"}),
    ),
)
DOCUMENT_GROUP_LABELS = {
    group: label for group, label, _description, _categories in DOCUMENT_GROUPS
}
DOCUMENT_GROUP_CATEGORIES = {
    group: categories
    for group, _label, _description, categories in DOCUMENT_GROUPS
}
ALLOWED_DOCUMENT_GROUPS = frozenset(DOCUMENT_GROUP_LABELS)
LOGGER = logging.getLogger("hub.project_documents")
_STATE_LOCK = Lock()
_DOCUMENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PROJECT_DOCUMENT_PATHS = {
    "@project/README.md": PROJECT_ROOT / "README.md",
}
_HOME_DOCUMENTS_PER_GROUP = 5


@dataclass(frozen=True)
class DesignDocument:
    id: str
    title: str
    summary: str
    status: str
    relative_path: str
    category: str = "project_baseline"
    group: str = ""


@dataclass(frozen=True)
class DesignDocumentView:
    id: str
    title: str
    summary: str
    status: str
    updated_at: datetime
    category: str = "project_baseline"
    category_label: str = "项目基线"
    group: str = ""
    group_label: str = ""
    archived: bool = False
    html: str | None = None


@dataclass(frozen=True)
class DesignDocumentSource:
    """A bounded plaintext source for a registered project document."""

    id: str
    title: str
    content: str
    truncated: bool


class DesignDocumentIndexError(RuntimeError):
    """Raised when the project document registry cannot be loaded."""


ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "del",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}
ALLOWED_ATTRIBUTES = {
    "a": ["href", "title"],
    "code": ["class"],
    "h1": ["id"],
    "h2": ["id"],
    "h3": ["id"],
    "h4": ["id"],
    "h5": ["id"],
    "h6": ["id"],
}


class _ProjectDocumentLinkTreeprocessor(Treeprocessor):
    def __init__(
        self,
        md: markdown.Markdown,
        *,
        source_path: Path,
        registered_paths: dict[Path, str],
    ) -> None:
        super().__init__(md)
        self.source_path = source_path
        self.registered_paths = registered_paths

    def run(self, root: object) -> None:
        for element in root.iter("a"):
            href = element.get("href")
            if not href:
                continue
            parsed = urlsplit(href)
            if parsed.scheme or parsed.netloc or parsed.path.startswith("/"):
                continue
            relative_path = unquote(parsed.path)
            if not relative_path.lower().endswith(".md"):
                continue
            target_path = (self.source_path.parent / relative_path).resolve()
            document_id = self.registered_paths.get(target_path)
            if document_id is None:
                element.attrib.pop("href", None)
                continue
            element.set(
                "href",
                urlunsplit(("", "", f"/project-docs/{document_id}", "", parsed.fragment)),
            )


class _ProjectDocumentLinkExtension(Extension):
    def __init__(
        self,
        *,
        source_path: Path,
        registered_paths: dict[Path, str],
    ) -> None:
        super().__init__()
        self.source_path = source_path
        self.registered_paths = registered_paths

    def extendMarkdown(self, md: markdown.Markdown) -> None:
        md.treeprocessors.register(
            _ProjectDocumentLinkTreeprocessor(
                md,
                source_path=self.source_path,
                registered_paths=self.registered_paths,
            ),
            "project_document_links",
            5,
        )


def _load_documents() -> tuple[DesignDocument, ...]:
    try:
        payload = json.loads(DOCUMENTS_INDEX.read_text(encoding="utf-8"))
    except FileNotFoundError:
        LOGGER.error("Project document index does not exist: %s", DOCUMENTS_INDEX)
        raise DesignDocumentIndexError(
            "Project document index does not exist"
        ) from None
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.error("Unable to read project document index: %s", DOCUMENTS_INDEX)
        raise DesignDocumentIndexError(
            "Unable to read project document index"
        ) from exc

    values = payload.get("documents") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("version") != DOCUMENT_INDEX_VERSION
        or not isinstance(values, list)
    ):
        LOGGER.error("Project document index has an unsupported format")
        raise DesignDocumentIndexError(
            "Project document index has an unsupported format"
        )

    documents = []
    document_ids: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            LOGGER.warning("Ignoring invalid project document index entry")
            continue
        document_id = value.get("id")
        title = value.get("title")
        summary = value.get("summary")
        status = value.get("status")
        category = value.get("category")
        group = value.get("group", "")
        relative_path = value.get("path")
        if (
            not isinstance(document_id, str)
            or not _DOCUMENT_ID_PATTERN.fullmatch(document_id)
            or document_id in document_ids
            or not isinstance(title, str)
            or not title.strip()
            or len(title) > 120
            or not isinstance(summary, str)
            or not summary.strip()
            or len(summary) > 300
            or not isinstance(status, str)
            or status.strip() not in ALLOWED_DOCUMENT_STATUSES
            or not isinstance(category, str)
            or category.strip() not in ALLOWED_DOCUMENT_CATEGORIES
            or not isinstance(group, str)
            or group.strip() not in ALLOWED_DOCUMENT_GROUPS
            or category.strip() not in DOCUMENT_GROUP_CATEGORIES[group.strip()]
            or not isinstance(relative_path, str)
            or not relative_path
        ):
            LOGGER.warning("Ignoring invalid project document index entry")
            continue
        document = DesignDocument(
            id=document_id,
            title=title.strip(),
            summary=summary.strip(),
            status=status.strip(),
            relative_path=relative_path,
            category=category.strip(),
            group=group.strip(),
        )
        try:
            _document_path(document)
        except ValueError:
            LOGGER.warning("Ignoring unsafe project document path: %s", relative_path)
            continue
        documents.append(document)
        document_ids.add(document_id)
    return tuple(documents)


def _document_path(document: DesignDocument) -> Path:
    project_document = _PROJECT_DOCUMENT_PATHS.get(document.relative_path)
    if project_document is not None:
        return project_document.resolve()
    if document.relative_path.startswith("@project/"):
        raise ValueError("Unknown project document path alias")
    root = DOCUMENTS_ROOT.resolve()
    path = (root / document.relative_path).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != ".md":
        raise ValueError("Design document path is outside the allowed directory")
    return path


def _archived_document_ids(
    state_file: Path,
    documents: tuple[DesignDocument, ...],
) -> set[str]:
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return set()
    except (OSError, json.JSONDecodeError, TypeError):
        LOGGER.warning("Unable to read project document archive state")
        return set()

    values = payload.get("archived_document_ids", []) if isinstance(payload, dict) else []
    if not isinstance(values, list):
        return set()
    registered_ids = {document.id for document in documents}
    return {
        value
        for value in values
        if isinstance(value, str) and value in registered_ids
    }


def _write_archived_document_ids(state_file: Path, document_ids: set[str]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = state_file.with_name(f".{state_file.name}.tmp")
    descriptor = os.open(
        temporary_file,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(
                {"archived_document_ids": sorted(document_ids)},
                file,
                ensure_ascii=False,
                indent=2,
            )
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary_file, 0o600)
        os.replace(temporary_file, state_file)
        os.chmod(state_file, 0o600)
    except BaseException:
        temporary_file.unlink(missing_ok=True)
        raise


def _metadata(
    document: DesignDocument,
    path: Path,
    archived_document_ids: set[str] | None = None,
) -> DesignDocumentView:
    return DesignDocumentView(
        id=document.id,
        title=document.title,
        summary=document.summary,
        status=document.status,
        updated_at=datetime.fromtimestamp(path.stat().st_mtime),
        category=document.category,
        category_label=DOCUMENT_CATEGORY_LABELS[document.category],
        group=document.group,
        group_label=DOCUMENT_GROUP_LABELS.get(document.group, ""),
        archived=document.id in (archived_document_ids or set()),
    )


def list_design_documents(
    state_file: Path | None = None,
    *,
    include_archived: bool = True,
) -> list[DesignDocumentView]:
    registered_documents = _load_documents()
    archived_document_ids = (
        _archived_document_ids(state_file, registered_documents)
        if state_file is not None
        else set()
    )
    documents = []
    for document in registered_documents:
        path = _document_path(document)
        if path.is_file():
            metadata = _metadata(document, path, archived_document_ids)
            if include_archived or not metadata.archived:
                documents.append(metadata)
        else:
            LOGGER.warning(
                "Registered project document does not exist: id=%s path=%s",
                document.id,
                document.relative_path,
            )
    return documents


def select_home_design_documents(
    documents: list[DesignDocumentView],
    *,
    per_group_limit: int = _HOME_DOCUMENTS_PER_GROUP,
) -> list[DesignDocumentView]:
    if per_group_limit <= 0:
        return []
    selected = []
    for group, _label, _description, _categories in DOCUMENT_GROUPS:
        group_documents = [
            document
            for document in documents
            if document.group == group
        ]
        if group != "project-core-documents":
            group_documents.sort(key=lambda item: item.updated_at, reverse=True)
        selected.extend(group_documents[:per_group_limit])
    return selected


def set_design_document_archived(
    document_id: str,
    archived: bool,
    state_file: Path,
) -> DesignDocumentView | None:
    registered_documents = _load_documents()
    document = next(
        (item for item in registered_documents if item.id == document_id),
        None,
    )
    if document is None:
        return None
    path = _document_path(document)
    if not path.is_file():
        return None

    with _STATE_LOCK:
        archived_document_ids = _archived_document_ids(
            state_file,
            registered_documents,
        )
        if archived:
            archived_document_ids.add(document_id)
        else:
            archived_document_ids.discard(document_id)
        _write_archived_document_ids(state_file, archived_document_ids)
    return _metadata(document, path, archived_document_ids)


def get_design_document(
    document_id: str,
    state_file: Path | None = None,
) -> DesignDocumentView | None:
    registered_documents = _load_documents()
    document = next(
        (item for item in registered_documents if item.id == document_id),
        None,
    )
    if document is None:
        return None

    path = _document_path(document)
    if not path.is_file() or path.stat().st_size > MAX_DOCUMENT_BYTES:
        return None

    source = path.read_text(encoding="utf-8")
    registered_paths = {
        _document_path(item): item.id for item in registered_documents
    }
    rendered = markdown.markdown(
        source,
        extensions=[
            "fenced_code",
            "tables",
            "toc",
            _ProjectDocumentLinkExtension(
                source_path=path,
                registered_paths=registered_paths,
            ),
        ],
        output_format="html",
    )
    cleaned = bleach.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols={"http", "https", "mailto"},
        strip=True,
    )
    archived_document_ids = (
        _archived_document_ids(state_file, registered_documents)
        if state_file is not None
        else set()
    )
    metadata = _metadata(document, path, archived_document_ids)
    return DesignDocumentView(
        id=metadata.id,
        title=metadata.title,
        summary=metadata.summary,
        status=metadata.status,
        updated_at=metadata.updated_at,
        category=metadata.category,
        category_label=metadata.category_label,
        group=metadata.group,
        group_label=metadata.group_label,
        archived=metadata.archived,
        html=cleaned,
    )


def get_design_document_source(
    document_id: str,
    *,
    max_chars: int = MAX_DOCUMENT_SOURCE_CHARS,
) -> DesignDocumentSource | None:
    """Read a registered document only through its fixed registry entry."""

    if not 1 <= max_chars <= MAX_DOCUMENT_SOURCE_CHARS:
        raise ValueError("Project document source length is invalid")
    registered_documents = _load_documents()
    document = next(
        (item for item in registered_documents if item.id == document_id),
        None,
    )
    if document is None:
        return None
    path = _document_path(document)
    if not path.is_file() or path.stat().st_size > MAX_DOCUMENT_BYTES:
        return None
    source = path.read_text(encoding="utf-8")
    content = source[:max_chars]
    return DesignDocumentSource(
        id=document.id,
        title=document.title,
        content=content,
        truncated=len(source) > max_chars,
    )
