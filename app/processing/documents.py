"""Document extraction with page-preserving chunks."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.chunking import chunk_markdown


PLAIN_SUFFIXES = {".md", ".txt", ".csv"}
DOCLING_SUFFIXES = {".pdf", ".docx", ".pptx", ".xlsx"}


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


def _extract_pdf_pages(path: Path) -> list[tuple[int, str]]:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        pages = []
        for page_no, page in enumerate(document, start=1):
            text_page = page.get_textpage()
            try:
                pages.append((page_no, text_page.get_text_range()))
            finally:
                text_page.close()
                page.close()
        return pages
    finally:
        document.close()


def _chunks(markdown: str, page_no: int | None) -> list[CitedChunk]:
    return [
        CitedChunk(chunk.text, chunk.section, page_no, page_no)
        for chunk in chunk_markdown(markdown)
    ]


def extract_document(path: Path) -> ExtractedDocument:
    suffix = path.suffix.lower()
    if suffix in PLAIN_SUFFIXES:
        markdown = path.read_text(encoding="utf-8-sig")
        chunks = _chunks(markdown, None)
    elif suffix in DOCLING_SUFFIXES:
        document = None
        try:
            document = _convert_with_docling(path)
        except Exception as docling_error:
            if suffix != ".pdf":
                raise ValueError(f"{suffix} parsing failed") from docling_error
            try:
                pages = _extract_pdf_pages(path)
            except Exception as pdfium_error:
                raise ValueError("PDF parsing failed with Docling and PDFium") from pdfium_error
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
    return ExtractedDocument(markdown=markdown, chunks=chunks)
