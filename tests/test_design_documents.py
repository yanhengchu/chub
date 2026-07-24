import json
from pathlib import Path

import app.services.design_documents as service
from app.services.design_documents import DesignDocument, DesignDocumentIndexError


def configure_document(monkeypatch, root: Path, content: str) -> None:
    path = root / "design.md"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(service, "DOCUMENTS_ROOT", root)
    index = root / "design_documents.json"
    index.write_text(
        json.dumps(
            {
                "version": 1,
                "documents": [
                    {
                        "id": "design",
                        "title": "测试方案",
                        "summary": "测试摘要",
                        "status": "讨论中",
                        "path": "design.md",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(service, "DOCUMENTS_INDEX", index)


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


def test_design_document_index_is_reloaded(monkeypatch, tmp_path: Path) -> None:
    configure_document(monkeypatch, tmp_path, "# 初始内容")

    assert [item.id for item in service.list_design_documents()] == ["design"]

    (tmp_path / "new.md").write_text("# 新文档", encoding="utf-8")
    service.DOCUMENTS_INDEX.write_text(
        json.dumps(
            {
                "version": 1,
                "documents": [
                    {
                        "id": "new-design",
                        "title": "新增方案",
                        "summary": "刷新后可见",
                        "status": "调研中",
                        "path": "new.md",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    documents = service.list_design_documents()

    assert [item.id for item in documents] == ["new-design"]


def test_design_document_index_rejects_unsafe_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    docs_root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# 外部文件", encoding="utf-8")
    index = docs_root / "design_documents.json"
    index.write_text(
        json.dumps(
            {
                "version": 1,
                "documents": [
                    {
                        "id": "unsafe",
                        "title": "越界文档",
                        "summary": "不应读取",
                        "status": "调研中",
                        "path": "../outside.md",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(service, "DOCUMENTS_ROOT", docs_root)
    monkeypatch.setattr(service, "DOCUMENTS_INDEX", index)

    assert service.list_design_documents() == []


def test_missing_design_document_index_is_an_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(service, "DOCUMENTS_ROOT", tmp_path)
    monkeypatch.setattr(service, "DOCUMENTS_INDEX", tmp_path / "missing.json")

    try:
        service.list_design_documents()
    except DesignDocumentIndexError:
        pass
    else:
        raise AssertionError("missing index should be reported as an error")


def test_registered_project_documents_exist() -> None:
    documents = service._load_documents()

    assert documents
    assert all(service._document_path(document).is_file() for document in documents)
