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
    path = tmp_path / "policy.docx"
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
        lambda _path: ([(1, "First page"), (2, "Second page")], set()),
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
    monkeypatch.setattr(documents, "_extract_pdf_pages", lambda _path: ([(1, "")], {1}))
    monkeypatch.setattr(
        documents,
        "_extract_pdf_ocr_pages",
        lambda _path, language, pages: [(1, f"OCR {language} Safiran Hamrah")],
    )

    extracted = documents.extract_document(path, "fa")

    assert extracted.chunks[0].page_start == 1
    assert "OCR fa Safiran Hamrah" in extracted.markdown


def test_mixed_pdf_only_ocrs_blank_pages_without_docling(tmp_path, monkeypatch):
    path = tmp_path / "mixed.pdf"
    path.write_bytes(b"fake")
    monkeypatch.setattr(documents, "_convert_with_docling", lambda _: pytest.fail("PDF must not load Docling"))
    monkeypatch.setattr(documents, "_extract_pdf_pages", lambda _: ([(1, "Cover"), (2, "  "), (3, "Appendix")], {2}))
    calls = []
    def ocr(path, language, pages):
        calls.append(pages)
        return [(2, "Scanned contract")]
    monkeypatch.setattr(documents, "_extract_pdf_ocr_pages", ocr)
    result = documents.extract_document(path)
    assert calls == [{2}]
    assert [chunk.page_start for chunk in result.chunks] == [1, 2, 3]
    assert "Scanned contract" in result.markdown


def test_pdf_page_limit_checked_before_text_or_render(tmp_path):
    import pypdfium2 as pdfium
    path = tmp_path / "long.pdf"
    with pdfium.PdfDocument.new() as pdf:
        for _ in range(documents.MAX_PDF_PAGES + 1):
            pdf.new_page(20, 20).close()
        pdf.save(str(path))
    with pytest.raises(ValueError, match="200-page"):
        documents.extract_document(path)


def test_scanned_body_with_digital_page_number_is_not_omitted(tmp_path, monkeypatch):
    import ctypes
    import pypdfium2 as pdfium
    from PIL import Image
    path = tmp_path / "scan-with-page-number.pdf"
    with pdfium.PdfDocument.new() as pdf:
        page = pdf.new_page(600, 800)
        picture = pdfium.PdfImage.new(pdf)
        with Image.new("RGB", (200, 200), "white") as image:
            bitmap = pdfium.PdfBitmap.from_pil(image)
            picture.set_bitmap(bitmap)
            bitmap.close()
        picture.set_matrix(pdfium.PdfMatrix(a=500, d=700, e=50, f=50))
        page.insert_obj(picture)
        raw_text = pdfium.raw.FPDFPageObj_NewTextObj(pdf, b"Helvetica", 12)
        text = pdfium.PdfObject(raw_text, pdf=pdf)
        number = ctypes.create_string_buffer("1\0".encode("utf-16-le"))
        assert pdfium.raw.FPDFText_SetText(text, ctypes.cast(number, ctypes.POINTER(ctypes.c_ushort)))
        text.set_matrix(pdfium.PdfMatrix(e=280, f=10))
        page.insert_obj(text)
        page.gen_content()
        page.close()
        pdf.save(str(path))
    pages, required = documents._extract_pdf_pages(path)
    assert pages[0][1].strip() == "1"
    assert required == {1}
    monkeypatch.setattr(documents, "_extract_pdf_ocr_pages", lambda _path, language, numbers: [(1, "Scanned contract body")])
    result = documents.extract_document(path)
    assert "Scanned contract body" in result.markdown
    assert result.chunks[0].page_start == 1
