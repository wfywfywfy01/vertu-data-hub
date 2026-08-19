from pathlib import Path

import pytest

from app.processing import documents


def test_csv_is_extracted_as_plain_text(tmp_path):
    path = tmp_path / "sales.csv"
    path.write_text("sku,qty\nV01,2\n", encoding="utf-8")

    extracted = documents.extract_document(path)

    assert "V01" in extracted.markdown
    assert extracted.chunks[0].page_start is None


def test_docling_pages_keep_page_citations(tmp_path, monkeypatch):
    path = tmp_path / "policy.pdf"
    path.write_bytes(b"fake")

    class FakeDocument:
        pages = {1: object(), 2: object()}

        def export_to_markdown(self, page_no=None):
            return f"# Page {page_no}\n\nEvidence on page {page_no}."

    monkeypatch.setattr(documents, "_convert_with_docling", lambda _path: FakeDocument())

    extracted = documents.extract_document(path)

    assert [chunk.page_start for chunk in extracted.chunks] == [1, 2]
    assert [chunk.page_end for chunk in extracted.chunks] == [1, 2]
    assert "<!-- page:1 -->" in extracted.markdown
    assert "<!-- page:2 -->" in extracted.markdown


def test_pdf_falls_back_to_pdfium_and_keeps_page_citations(tmp_path, monkeypatch):
    path = tmp_path / "policy.pdf"
    path.write_bytes(b"fake")
    monkeypatch.setattr(
        documents,
        "_convert_with_docling",
        lambda _path: (_ for _ in ()).throw(RuntimeError("docling unavailable")),
    )
    monkeypatch.setattr(
        documents,
        "_extract_pdf_pages",
        lambda _path: [(1, "First page"), (2, "Second page")],
    )

    extracted = documents.extract_document(path)

    assert [chunk.page_start for chunk in extracted.chunks] == [1, 2]
    assert "<!-- page:2 -->" in extracted.markdown


def test_office_parse_failure_is_permanent(tmp_path, monkeypatch):
    path = tmp_path / "broken.docx"
    path.write_bytes(b"broken")
    monkeypatch.setattr(
        documents,
        "_convert_with_docling",
        lambda _path: (_ for _ in ()).throw(RuntimeError("invalid package")),
    )

    with pytest.raises(ValueError, match=r"\.docx parsing failed"):
        documents.extract_document(path)


def test_scanned_pdf_uses_ocr_and_keeps_page_citation(tmp_path, monkeypatch):
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"fake")
    monkeypatch.setattr(
        documents,
        "_convert_with_docling",
        lambda _path: (_ for _ in ()).throw(RuntimeError("docling unavailable")),
    )
    monkeypatch.setattr(documents, "_extract_pdf_pages", lambda _path: [(1, "")])
    monkeypatch.setattr(
        documents,
        "_extract_pdf_ocr_pages",
        lambda _path, language: [(1, f"OCR {language} Safiran Hamrah")],
    )

    extracted = documents.extract_document(path, "fa")

    assert extracted.chunks[0].page_start == 1
    assert "OCR fa Safiran Hamrah" in extracted.markdown
