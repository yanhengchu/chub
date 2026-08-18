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
ALLOWED_DOCUMENT_STATUSES = frozenset(
    {"调研中", "待实现", "进行中", "待验收", "已验收", "持续维护"}
)
LOGGER = logging.getLogger("hub.project_documents")
_STATE_LOCK = Lock()
_DOCUMENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_PROJECT_DOCUMENT_PATHS = {
    "@project/README.md": PROJECT_ROOT / "README.md",
}
_CORE_DOCUMENT_IDS = ("project-readme", "chub-architecture")
_HOME_DOCUMENT_LIMIT = 5


@dataclass(frozen=True)
class DesignDocument:
    id: str
    title: str
    summary: str
    status: str
    relative_path: str


@dataclass(frozen=True)
class DesignDocumentView:
    id: str
    title: str
    summary: str
    status: str
    updated_at: datetime
    archived: bool = False
    html: str | None = None


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
        or payload.get("version") != 1
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
    limit: int = _HOME_DOCUMENT_LIMIT,
) -> list[DesignDocumentView]:
    if limit <= 0:
        return []
    by_id = {document.id: document for document in documents}
    core_documents = [
        by_id[document_id]
        for document_id in _CORE_DOCUMENT_IDS
        if document_id in by_id
    ]
    recent_documents = sorted(
        (
            document
            for document in documents
            if document.id not in _CORE_DOCUMENT_IDS
        ),
        key=lambda item: item.updated_at,
        reverse=True,
    )
    return (core_documents + recent_documents)[:limit]


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
        archived=metadata.archived,
        html=cleaned,
    )
