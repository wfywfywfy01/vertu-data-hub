from app.chunking import chunk_markdown, estimate_tokens, split_blocks


def test_estimate_tokens_cjk_vs_ascii():
    assert estimate_tokens("abcd") == 1  # 4 ascii chars -> 1 token
    assert estimate_tokens("中文") == 2  # 2 CJK chars -> 2 tokens


def test_split_blocks_keeps_table_intact():
    md = "# Title\n\npara one\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\npara two\n"
    blocks = split_blocks(md)
    tables = [b for b in blocks if b.is_table]
    assert len(tables) == 1
    assert "| a | b |" in tables[0].text
    assert "| 1 | 2 |" in tables[0].text


def test_chunk_markdown_sections_are_tracked():
    md = "# Sec A\n" + ("word " * 400) + "\n\n# Sec B\n" + ("word " * 400)
    chunks = chunk_markdown(md)
    assert len(chunks) >= 2
    sections = {c.section for c in chunks}
    assert "Sec A" in sections
    assert "Sec B" in sections


def test_chunk_markdown_empty_returns_empty():
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n  ") == []
