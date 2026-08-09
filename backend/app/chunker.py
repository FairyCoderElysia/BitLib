# -*- coding: utf-8 -*-
"""父子分片（small-to-big，spec §8 / DESIGN.md §8）。

- parent：上下文单元（~PARENT_CHARS 字符，≈1200 token），按段落聚合，存 SQLite
- child ：检索单元（~CHUNK_CHARS 字符，≈250 token），滑动重叠，存 Chroma
- token 估算：中文近似 len(text) // 1.5
"""
import re

CHUNK_CHARS = 375    # ≈250 token
OVERLAP_CHARS = 45   # ≈30 token
PARENT_CHARS = 1800  # ≈1200 token

_SENT_END = re.compile(r"(?<=[。！？!?；;])")
_PARA_SPLIT = re.compile(r"\n\s*\n")


def _split_sentences(text: str) -> list[str]:
    """按句末标点切句（保留标点）。"""
    parts = _SENT_END.split(text)
    return [p.strip() for p in parts if p.strip()]


def _make_children(parent_text: str) -> list[str]:
    """parent 内句子累积成 child 块，跨块尾部保留 overlap 字符。"""
    children: list[str] = []
    buf = ""
    for sent in _split_sentences(parent_text):
        if buf and len(buf) + len(sent) > CHUNK_CHARS:
            children.append(buf.strip())
            buf = buf[-OVERLAP_CHARS:] if len(buf) > OVERLAP_CHARS else buf
        buf += sent
    if buf.strip():
        children.append(buf.strip())
    return children or [parent_text.strip()]


def _parent_title(parent_text: str) -> str:
    """parent 标题：首行前 50 字符。"""
    first = parent_text.strip().splitlines()[0] if parent_text.strip() else ""
    return first[:50]


def chunk_document(text: str) -> list[dict]:
    """整篇文档 → [{parent:{index,title,text}, children:[{index,text}...]}...]

    按段落累积成 parent（≤PARENT_CHARS），再对每个 parent 切 child。
    """
    paras = [p.strip() for p in _PARA_SPLIT.split(text or "") if p.strip()]
    if not paras:
        paras = [text.strip()]

    parent_texts: list[str] = []
    buf = ""
    for p in paras:
        # 段落超长（如无换行的长文本）：按 PARENT_CHARS 字符窗口切分
        if len(p) > PARENT_CHARS:
            if buf:
                parent_texts.append(buf.strip())
                buf = ""
            for i in range(0, len(p), PARENT_CHARS):
                parent_texts.append(p[i:i + PARENT_CHARS].strip())
            continue
        if buf and len(buf) + len(p) > PARENT_CHARS:
            parent_texts.append(buf.strip())
            buf = p
        else:
            buf += p + "\n"
    if buf.strip():
        parent_texts.append(buf.strip())

    result = []
    for i, pt in enumerate(parent_texts):
        children = _make_children(pt)
        result.append({
            "parent": {"index": i, "title": _parent_title(pt), "text": pt},
            "children": [{"index": j, "text": c} for j, c in enumerate(children)],
        })
    return result


def count_tokens(text: str) -> int:
    """中文近似 token 数。"""
    return len(text) // 1.5
