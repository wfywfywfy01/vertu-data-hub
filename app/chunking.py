"""文档解析 + 切片（source-agnostic，不含入库逻辑，入库见 app/ingestion/doc_ingest.py）。

- PDF/DOCX 等用 Docling 解析为 Markdown；MD/TXT 直接读取
- 切片目标 300~700 tokens（估算），块间重叠 50~100 tokens，表格不拆散
"""
import re
from dataclasses import dataclass
from pathlib import Path

DOCLING_SUFFIXES = {".pdf", ".docx", ".pptx", ".html", ".xlsx"}
PLAIN_SUFFIXES = {".md", ".txt"}

MAX_TOKENS = 700
MIN_TOKENS = 300
OVERLAP_TOKENS = 80


# ---------- 解析 ----------

def convert_to_markdown(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in PLAIN_SUFFIXES:
        return path.read_text(encoding="utf-8")
    if suffix in DOCLING_SUFFIXES:
        from docling.document_converter import DocumentConverter

        result = DocumentConverter().convert(str(path))
        return result.document.export_to_markdown()
    raise ValueError(f"unsupported file type: {path.name}")


# ---------- 切片 ----------

_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")


def estimate_tokens(text: str) -> int:
    """粗略估算：CJK 每字 1 token，其余每 4 字符 1 token。"""
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + max(other // 4, 0)


@dataclass
class Block:
    text: str
    heading: str | None = None  # 本块若是标题则为标题文本
    is_table: bool = False


def split_blocks(markdown: str) -> list[Block]:
    """按 标题 / 表格（连续 | 行）/ 段落 分块，表格保持完整。"""
    blocks: list[Block] = []
    paragraph: list[str] = []
    table: list[str] = []

    def flush_paragraph():
        if paragraph:
            blocks.append(Block("\n".join(paragraph)))
            paragraph.clear()

    def flush_table():
        if table:
            blocks.append(Block("\n".join(table), is_table=True))
            table.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            flush_paragraph()
            table.append(line)
            continue
        flush_table()
        if stripped.startswith("#"):
            flush_paragraph()
            blocks.append(Block(line, heading=stripped.lstrip("#").strip()))
        elif stripped == "":
            flush_paragraph()
        else:
            paragraph.append(line)
    flush_paragraph()
    flush_table()
    return blocks


@dataclass
class Chunk:
    text: str
    section: str | None


def chunk_blocks(blocks: list[Block]) -> list[Chunk]:
    chunks: list[Chunk] = []
    current: list[Block] = []
    section: str | None = None
    chunk_section: str | None = None

    def tokens(blist: list[Block]) -> int:
        return sum(estimate_tokens(b.text) for b in blist)

    def emit(with_overlap: bool) -> None:
        nonlocal current, chunk_section
        if current:
            chunks.append(Chunk("\n\n".join(b.text for b in current), chunk_section))
            tail: list[Block] = []
            if with_overlap:
                # 片内截断时保留尾部 ~OVERLAP_TOKENS 的块作为下一片开头（表格不重复带入）
                for b in reversed(current):
                    if b.is_table or tokens(tail) >= OVERLAP_TOKENS:
                        break
                    tail.insert(0, b)
            current = tail
        chunk_section = section

    for block in blocks:
        if block.heading is not None:
            # 章节边界：当前片达到最小片长即收片（不带重叠，保持章节对齐）
            should_emit = tokens(current) >= MIN_TOKENS
            section = block.heading  # 先更新，emit 里 chunk_section 取的是"下一片"的章节
            if should_emit:
                emit(with_overlap=False)
        if tokens(current) + estimate_tokens(block.text) > MAX_TOKENS and current:
            emit(with_overlap=True)
        if chunk_section is None:
            chunk_section = section
        current.append(block)
    if current:
        chunks.append(Chunk("\n\n".join(b.text for b in current), chunk_section))
    return [c for c in chunks if c.text.strip()]


def chunk_markdown(markdown: str) -> list[Chunk]:
    return chunk_blocks(split_blocks(markdown))
