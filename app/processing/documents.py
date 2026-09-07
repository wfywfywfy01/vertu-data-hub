"""Document extraction with page-preserving chunks."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import math

from app.chunking import chunk_markdown


PLAIN_SUFFIXES = {".md", ".txt", ".csv"}
DOCLING_SUFFIXES = {".docx", ".pptx", ".xlsx"}
MAX_PDF_PAGES = 200
MAX_OCR_EDGE = 2000
MAX_DOCUMENT_CHARACTERS = 2_000_000


@dataclass(frozen=True)
class CitedChunk:
    text: str
    section: str | None
    page_start: int | None
    page_end: int | None


@dataclass(frozen=True)
class ExtractedDocument:
    markdown: str
    chunks: list[CitedChunk]


def _convert_with_docling(path: Path):
    from docling.document_converter import DocumentConverter

    return DocumentConverter().convert(str(path)).document


def _extract_pdf_pages(path: Path) -> tuple[list[tuple[int, str]], set[int]]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        if len(document) > MAX_PDF_PAGES:
            raise ValueError("PDF exceeds 200-page extraction limit")
        pages = []
        ocr_pages = set()
        characters = 0
        for page_no, page in enumerate(document, start=1):
            text_page = page.get_textpage()
            try:
                text = text_page.get_text_range()
                characters += len(text)
                if characters > MAX_DOCUMENT_CHARACTERS:
                    raise ValueError("document exceeds text extraction limit")
                pages.append((page_no, text))
                if not text.strip() or (
                    len(text.strip()) < 100
                    and next(page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]), None) is not None
                ):
                    ocr_pages.add(page_no)
            finally:
                text_page.close()
                page.close()
        return pages, ocr_pages
    finally:
        document.close()


def _extract_pdf_ocr_pages(
    path: Path, language_code: str | None = None, page_numbers: set[int] | None = None
) -> list[tuple[int, str]]:
    import pypdfium2 as pdfium
    from app.processing.images import extract_image

    document = pdfium.PdfDocument(str(path))
    try:
        if len(document) > MAX_PDF_PAGES:
            raise ValueError("scanned PDF exceeds 200-page OCR limit")
        pages = []
        for page_no in sorted(page_numbers if page_numbers is not None else range(1, len(document) + 1)):
            page = document[page_no - 1]
            try:
                edge = max(page.get_size())
                if not math.isfinite(edge) or edge <= 0:
                    raise ValueError("invalid PDF page dimensions")
                bitmap = page.render(scale=min(2, MAX_OCR_EDGE / edge))
                try:
                    with bitmap.to_pil() as image, BytesIO() as output:
                        image.save(output, format="PNG")
                        pages.append((page_no, extract_image(output.getvalue(), language_code).text))
                finally:
                    bitmap.close()
            finally:
                page.close()
        return pages
    finally:
        document.close()


def _chunks(markdown: str, page_no: int | None) -> list[CitedChunk]:
    return [
        CitedChunk(chunk.text, chunk.section, page_no, page_no)
        for chunk in chunk_markdown(markdown)
    ]


def extract_document(path: Path, language_code: str | None = None) -> ExtractedDocument:
    suffix = path.suffix.lower()
    if suffix in PLAIN_SUFFIXES:
        if path.stat().st_size > MAX_DOCUMENT_CHARACTERS * 4:
            raise ValueError("document exceeds text extraction limit")
        markdown = path.read_text(encoding="utf-8-sig")
        chunks = _chunks(markdown, None)
    elif suffix == ".pdf":
        try:
            pages, ocr_pages = _extract_pdf_pages(path)
            if ocr_pages:
                ocr = dict(_extract_pdf_ocr_pages(path, language_code, ocr_pages))
                pages = [(number, "\n\n".join(part for part in (text, ocr.get(number, "")) if part.strip()))
                         for number, text in pages]
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("PDF extraction failed") from exc
        markdown = "\n\n".join(
            f"<!-- page:{number} -->\n\n{text}" for number, text in pages if text.strip()
        )
        if len(markdown) > MAX_DOCUMENT_CHARACTERS:
            raise ValueError("document exceeds text extraction limit")
        chunks = [chunk for number, text in pages for chunk in _chunks(text, number)]
    elif suffix in DOCLING_SUFFIXES:
        document = None
        try:
            document = _convert_with_docling(path)
        except Exception as docling_error:
            raise ValueError(f"{suffix} parsing failed") from docling_error
        else:
            page_numbers = sorted(document.pages)
            pages = [
                (page_no, document.export_to_markdown(page_no=page_no))
                for page_no in page_numbers
            ]
        if pages:
            markdown = "\n\n".join(
                f"<!-- page:{page_no} -->\n\n{text}" for page_no, text in pages if text.strip()
            )
            chunks = [chunk for page_no, text in pages for chunk in _chunks(text, page_no)]
        elif document is not None:
            markdown = document.export_to_markdown()
            chunks = _chunks(markdown, None)
        else:
            markdown = ""
            chunks = []
    else:
        raise ValueError(f"unsupported document type: {suffix or '<none>'}")
    if not chunks:
        raise ValueError("document has no extractable text")
    if len(markdown) > MAX_DOCUMENT_CHARACTERS:
        raise ValueError("document exceeds text extraction limit")
    return ExtractedDocument(markdown=markdown, chunks=chunks)
