from pathlib import Path

import app.services.design_documents as service
from app.services.design_documents import DesignDocument


def configure_document(monkeypatch, root: Path, content: str) -> None:
    path = root / "design.md"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(service, "DOCUMENTS_ROOT", root)
    monkeypatch.setattr(
        service,
        "DOCUMENTS",
        (
            DesignDocument(
                id="design",
                title="测试方案",
                summary="测试摘要",
                status="讨论中",
                relative_path="design.md",
            ),
        ),
    )


def test_design_document_markdown_is_sanitized(monkeypatch, tmp_path: Path) -> None:
    configure_document(
        monkeypatch,
        tmp_path,
        "# 标题\n\n<script>alert('unsafe')</script>\n\n[危险链接](javascript:alert(1))",
    )

    document = service.get_design_document("design")

    assert document is not None
    assert document.html is not None
    assert "<h1" in document.html
    assert "<script" not in document.html
    assert "javascript:" not in document.html


def test_design_document_rejects_unregistered_and_oversized_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    configure_document(monkeypatch, tmp_path, "content")
    monkeypatch.setattr(service, "MAX_DOCUMENT_BYTES", 1)

    assert service.get_design_document("missing") is None
    assert service.get_design_document("design") is None


def test_design_document_rejects_path_outside_docs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("content", encoding="utf-8")
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    monkeypatch.setattr(service, "DOCUMENTS_ROOT", docs_root)
    document = DesignDocument(
        id="outside",
        title="越界文档",
        summary="测试",
        status="讨论中",
        relative_path="../outside.md",
    )

    try:
        service._document_path(document)
    except ValueError:
        pass
    else:
        raise AssertionError("outside path should be rejected")
