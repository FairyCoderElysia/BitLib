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


def ingest_document(db: Session, doc: models.Document,
                    regen_summary: bool = True) -> models.Document:
    """对已置 processing 的文档执行完整入库管线（文件来源）。返回更新后的 doc（已 commit）。

    regen_summary=False：保留已有 summary，不重调 LLM（向量索引重建时提速）。
    """
    try:
        path = Path(settings.upload_dir) / doc.file_path
        raw = parse_file(path)
        _run_pipeline(db, doc, raw, regen_summary)
    except Exception as exc:
        _mark_failed(db, doc, exc)
    return doc


def ingest_text(db: Session, doc: models.Document, raw_text: str,
                regen_summary: bool = True) -> models.Document:
    """文本直接入库（爬虫等无文件来源）。返回更新后的 doc（已 commit）。"""
    try:
        _run_pipeline(db, doc, raw_text, regen_summary)
    except Exception as exc:
        _mark_failed(db, doc, exc)
    return doc


def ingest_document_update(db: Session, doc: models.Document,
                           new_file_path: str, new_file_size: int,
                           new_file_hash: str,
                           regen_summary: bool = True) -> models.Document:
    """更新为新版本（F2/F8 修复）专用入口。

    先完成解析/清洗/分片/embedding 全部可失败计算，成功后才进入破坏性替换阶段
    （清理旧 ChunkParent/旧 Chroma child → 写新 Chroma → 写新 ChunkParent →
    更新 doc 文件字段与 FTS → commit）。任一步可失败计算失败：删除新落盘文件，
    恢复 doc 为 approved，旧文件/旧分块/旧向量/旧 FTS 全部不动。
    破坏性替换阶段失败：置 failed 并记录 error_message（可 reprocess 恢复），
    并删除新落盘文件。
    """
    new_path = Path(settings.upload_dir) / new_file_path
    old_file_path = doc.file_path
    old_file_size = doc.file_size
    old_file_hash = doc.file_hash

    # ---- 可失败计算阶段：绝不触碰旧数据 ----
    try:
        raw = parse_file(new_path)
        text, chunks, embeddings = _compute_pipeline(doc, raw)
    except Exception:
        db.rollback()
        # 调用方可能已把 doc 置为 processing；可失败阶段失败要恢复 approved
        doc.status = models.STATUS_APPROVED
        doc.error_message = None
        db.add(doc)
        db.commit()
        new_path.unlink(missing_ok=True)
        raise

    # ---- 破坏性替换阶段 ----
    try:
        _replace_document_data(db, doc, text, chunks, embeddings, regen_summary)
        doc.file_path = new_file_path
        doc.file_size = new_file_size
        doc.file_hash = new_file_hash
        db.add(doc)
        db.commit()
    except Exception as exc:
        db.rollback()
        new_path.unlink(missing_ok=True)
        _mark_failed(db, doc, exc)
        raise

    # 成功后删除旧物理文件。D1：删除失败不得让已提交的更新表现为失败/半更新，
    # 只记录日志（旧文件残留可后续清理，不影响新文件与文档记录）。
    if old_file_path and old_file_path != new_file_path:
        try:
            Path(settings.upload_dir, old_file_path).unlink(missing_ok=True)
        except Exception:
            logger.exception("旧文件删除失败（doc_id=%s, old_file=%s），更新本身已提交",
                             doc.id, old_file_path)
    logger.info("文档 %s 已更新为新版本：%s -> %s（%s -> %s）",
                doc.id, old_file_hash, new_file_hash,
                old_file_size, new_file_size)
    return doc


def _mark_failed(db: Session, doc: models.Document, exc: Exception) -> None:
    db.rollback()
    doc.status = models.STATUS_FAILED
    doc.error_message = str(exc)[:500]
    db.add(doc)
    # FTS 冗余列同步（S11 优化 1）：failed 态也刷新 document_fts 行，避免与主表
    # status/内容不一致；同步失败仅告警，绝不阻断 failed 状态写入
    try:
        fts.sync_document(db, doc)
    except Exception:
        logger.exception("FTS 失败态同步失败（doc=%s），主表状态不受影响", doc.id)
    db.commit()
    logger.error("文档 %s 入库失败: %s", doc.id, exc)


def _compute_pipeline(doc: models.Document, raw: str):
    """可失败计算阶段：清洗 → 分片 → 全部 child embedding。不触碰旧数据。"""
    text = clean_text(raw)
    if len(text) < MIN_TEXT_LEN:
        raise ValueError(f"清洗后有效文本过短（{len(text)} 字符 < {MIN_TEXT_LEN}），不入库")

    chunks = chunk_document(text)

    child_texts = [c["text"] for p in chunks for c in p["children"]]
    embeddings = embed(child_texts) if child_texts else []
    if not embeddings:
        raise ValueError("分片结果为空，无法入库")
    return text, chunks, embeddings


def _replace_document_data(db: Session, doc: models.Document, text: str,
                           chunks: list, embeddings: list,
                           regen_summary: bool = True) -> None:
    """破坏性替换阶段：清理旧分块/旧向量 → 写新 Chroma → 写新 ChunkParent → 更新 doc/FTS → commit。"""
    # 清理旧分块（仅重新入库场景需要）
    # 首次入库集合中无该文档，跳过 Chroma delete——空集合上 delete(where=...)
    # 会触发 Chroma compactor 异步竞态，偶发 "Error loading hnsw index" 致入库失败
    # （实测 M1M2 回归偶现）。以 SQLite 侧 ChunkParent 是否存在判断是否重入库。
    has_old = db.query(models.ChunkParent).filter(
        models.ChunkParent.document_id == doc.id).first() is not None
    if has_old:
        vector_store.delete_by_document(doc.id)
    db.query(models.ChunkParent).filter(
        models.ChunkParent.document_id == doc.id).delete(
        synchronize_session=False)

    # Chroma 写入（metadata 冗余可见性字段）
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

    # ChunkParent 写 SQLite
    for p in chunks:
        db.add(models.ChunkParent(
            document_id=doc.id,
            chunk_index=p["parent"]["index"],
            title=p["parent"]["title"],
            text=p["parent"]["text"],
        ))

    # 更新文档 + FTS 同步
    doc.content_text = text
    # 摘要生成（F17）：LLM 失败内部已降级截取，绝不抛错、不影响入库状态；
    # 重建场景（regen_summary=False）保留已有 summary，跳过 LLM 调用以提速
    if regen_summary:
        doc.summary = generate_summary(doc)
    doc.status = models.STATUS_APPROVED
    doc.error_message = None
    doc.approved_at = doc.approved_at or datetime.utcnow()
    db.add(doc)
    fts.sync_document(db, doc)
    db.commit()
    logger.info("文档 %s 入库完成：%d child / %d parent", doc.id, len(items), len(chunks))


def _run_pipeline(db: Session, doc: models.Document, raw: str,
                  regen_summary: bool = True) -> None:
    """完整入库管线：可失败计算 → 破坏性替换。"""
    text, chunks, embeddings = _compute_pipeline(doc, raw)
    _replace_document_data(db, doc, text, chunks, embeddings, regen_summary)
