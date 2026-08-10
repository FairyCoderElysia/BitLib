# -*- coding: utf-8 -*-
"""解析入库管线入口（M3 实现）。

真实管线：解析(txt/docx/pdf/md) → 清洗 → 父子分片 → Embedding → Chroma 入库
→ ChunkParent 写 SQLite → DocumentFTS 同步 → 状态置 approved。
任一步失败 → 置 failed + error_message（重新入库时先清理旧分块）。
"""
import logging
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from . import fts, models, vector_store
from .chunker import chunk_document
from .cleaning import MIN_TEXT_LEN, clean_text
from .config import settings
from .embeddings import embed
from .parsers import parse_file
from .summary import generate_summary

logger = logging.getLogger(__name__)


def ingest_document(db: Session, doc: models.Document) -> models.Document:
    """对已置 processing 的文档执行完整入库管线（文件来源）。返回更新后的 doc（已 commit）。"""
    try:
        path = Path(settings.upload_dir) / doc.file_path
        raw = parse_file(path)
        _run_pipeline(db, doc, raw)
    except Exception as exc:
        _mark_failed(db, doc, exc)
    return doc


def ingest_text(db: Session, doc: models.Document, raw_text: str) -> models.Document:
    """文本直接入库（爬虫等无文件来源）。返回更新后的 doc（已 commit）。"""
    try:
        _run_pipeline(db, doc, raw_text)
    except Exception as exc:
        _mark_failed(db, doc, exc)
    return doc


def _mark_failed(db: Session, doc: models.Document, exc: Exception) -> None:
    db.rollback()
    doc.status = models.STATUS_FAILED
    doc.error_message = str(exc)[:500]
    db.add(doc)
    db.commit()
    logger.error("文档 %s 入库失败: %s", doc.id, exc)


def _run_pipeline(db: Session, doc: models.Document, raw: str) -> None:

    # 2. 清洗 + 过短拦截
    text = clean_text(raw)
    if len(text) < MIN_TEXT_LEN:
        raise ValueError(f"清洗后有效文本过短（{len(text)} 字符 < {MIN_TEXT_LEN}），不入库")

    # 3. 父子分片
    chunks = chunk_document(text)

    # 4. 清理旧分块（重新入库场景）
    vector_store.delete_by_document(doc.id)
    db.query(models.ChunkParent).filter(models.ChunkParent.document_id == doc.id).delete()

    # 5. 全部 child 向量化
    child_texts = [c["text"] for p in chunks for c in p["children"]]
    embeddings = embed(child_texts) if child_texts else []
    if not embeddings:
        raise ValueError("分片结果为空，无法入库")

    # 6. Chroma 写入（metadata 冗余可见性字段）
    dept_val = "" if doc.department_id is None else str(doc.department_id)
    items = []
    idx = 0
    for p in chunks:
        for c in p["children"]:
            items.append({
                "id": f"{doc.id}-{idx}",
                "embedding": embeddings[idx],
                "text": c["text"],
                "document_id": doc.id,
                "parent_id": p["parent"]["index"],
                "chunk_index": c["index"],
                "department_id": dept_val,
                "status": models.STATUS_APPROVED,
            })
            idx += 1
    vector_store.add_children(items)

    # 7. ChunkParent 写 SQLite
    for p in chunks:
        db.add(models.ChunkParent(
            document_id=doc.id,
            chunk_index=p["parent"]["index"],
            title=p["parent"]["title"],
            text=p["parent"]["text"],
        ))

    # 8. 更新文档 + FTS 同步
    doc.content_text = text
    # 摘要生成（F17）：LLM 失败内部已降级截取，绝不抛错、不影响入库状态
    doc.summary = generate_summary(db, doc)
    doc.status = models.STATUS_APPROVED
    doc.error_message = None
    doc.approved_at = doc.approved_at or datetime.utcnow()
    db.add(doc)
    fts.sync_document(db, doc)
    db.commit()
    logger.info("文档 %s 入库完成：%d child / %d parent", doc.id, len(items), len(chunks))
