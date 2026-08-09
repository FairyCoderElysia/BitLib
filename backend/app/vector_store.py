# -*- coding: utf-8 -*-
"""Chroma 向量库封装（spec §5.1 ChunkChild）。

- 集合 "chunks"：child 检索块
- metadata 冗余可见性字段：document_id / parent_id / chunk_index / department_id / status
  （支撑召回阶段权限过滤，spec §7.2）
- department_id 约定：公开文档存 ""（空串），部门文档存 str(department_id)
  —— Chroma where 对 None 过滤不可靠，用空串表示公开
- 维度切换（embedding_dim）时需全量重建：删除集合后重新入库
"""
import logging

from .config import settings

logger = logging.getLogger(__name__)

PUBLIC_DEPT = ""  # metadata 中公开文档的 department_id 值

_client = None
_collection = None

STATUS_APPROVED = "approved"


def _get_collection():
    """懒加载持久化 client 与集合。"""
    global _client, _collection
    if _collection is None:
        import chromadb
        _client = chromadb.PersistentClient(path=settings.chroma_dir)
        _collection = _client.get_or_create_collection(
            "chunks", metadata={"hnsw:space": "cosine"})
    return _collection


def add_children(items: list[dict]) -> None:
    """批量写入 child 块。items: [{id, embedding, text, document_id, parent_id,
    chunk_index, department_id(空串=公开), status}]。重复 id 覆盖（upsert）。"""
    col = _get_collection()
    col.upsert(
        ids=[i["id"] for i in items],
        embeddings=[i["embedding"] for i in items],
        documents=[i["text"] for i in items],
        metadatas=[{
            "document_id": i["document_id"],
            "parent_id": i["parent_id"],
            "chunk_index": i["chunk_index"],
            "department_id": i.get("department_id", PUBLIC_DEPT),
            "status": i.get("status", STATUS_APPROVED),
        } for i in items],
    )


def delete_by_document(document_id: int) -> None:
    """删除某文档全部 child（重新入库/删除文档时清理）。"""
    col = _get_collection()
    col.delete(where={"document_id": document_id})


def query(embedding: list[float], top_k: int,
          user_department_id: int | None = None, is_admin: bool = False) -> list[dict]:
    """按可见性过滤的向量召回（召回阶段权限过滤）。

    仅取 status=approved；非管理员限 公开(department_id="") 或 本部门。
    返回 [{document_id, parent_id, chunk_index, text, distance}]（distance 升序）。
    """
    col = _get_collection()
    if is_admin:
        where = {"status": STATUS_APPROVED}
    else:
        depts = [PUBLIC_DEPT, str(user_department_id)] if user_department_id else [PUBLIC_DEPT]
        # Chroma where 顶层多 key 不合法，需显式 $and
        where = {"$and": [{"status": STATUS_APPROVED},
                          {"department_id": {"$in": depts}}]}
    res = col.query(query_embeddings=[embedding], n_results=top_k, where=where)
    hits = []
    if res and res.get("ids") and res["ids"][0]:
        metas = res["metadatas"][0]
        for i, _id in enumerate(res["ids"][0]):
            m = metas[i]
            hits.append({
                "document_id": m["document_id"],
                "parent_id": m["parent_id"],
                "chunk_index": m["chunk_index"],
                "text": res["documents"][0][i],
                "distance": res["distances"][0][i],
                "status": m.get("status"),
            })
    return hits


def count_by_document(document_id: int) -> int:
    """某文档的 child 数量（测试/校验用）。"""
    col = _get_collection()
    try:
        res = col.get(where={"document_id": document_id})
        return len(res["ids"])
    except Exception:
        return 0
