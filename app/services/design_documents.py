from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import bleach
import markdown


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS_ROOT = PROJECT_ROOT / "docs"
MAX_DOCUMENT_BYTES = 512 * 1024


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
    html: str | None = None


DOCUMENTS = (
    DesignDocument(
        id="automation-download",
        title="配置驱动的飞书文档下载自动化方案",
        summary="复用 Debug Chrome 飞书登录状态，按配置安全下载 Wiki 文档 Markdown。",
        status="已实现并验收",
        relative_path="AUTOMATION_DOWNLOAD_DESIGN.md",
    ),
)

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


def _document_path(document: DesignDocument) -> Path:
    root = DOCUMENTS_ROOT.resolve()
    path = (root / document.relative_path).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() != ".md":
        raise ValueError("Design document path is outside the allowed directory")
    return path


def _metadata(document: DesignDocument, path: Path) -> DesignDocumentView:
    return DesignDocumentView(
        id=document.id,
        title=document.title,
        summary=document.summary,
        status=document.status,
        updated_at=datetime.fromtimestamp(path.stat().st_mtime),
    )


def list_design_documents() -> list[DesignDocumentView]:
    documents = []
    for document in DOCUMENTS:
        path = _document_path(document)
        if path.is_file():
            documents.append(_metadata(document, path))
    return sorted(documents, key=lambda item: item.updated_at, reverse=True)


def get_design_document(document_id: str) -> DesignDocumentView | None:
    document = next((item for item in DOCUMENTS if item.id == document_id), None)
    if document is None:
        return None

    path = _document_path(document)
    if not path.is_file() or path.stat().st_size > MAX_DOCUMENT_BYTES:
        return None

    source = path.read_text(encoding="utf-8")
    rendered = markdown.markdown(
        source,
        extensions=["fenced_code", "tables", "toc"],
        output_format="html",
    )
    cleaned = bleach.clean(
        rendered,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols={"http", "https", "mailto"},
        strip=True,
    )
    metadata = _metadata(document, path)
    return DesignDocumentView(
        id=metadata.id,
        title=metadata.title,
        summary=metadata.summary,
        status=metadata.status,
        updated_at=metadata.updated_at,
        html=cleaned,
    )
