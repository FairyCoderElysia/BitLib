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
import math

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


def get_document_vector(document_id: int) -> list[float] | None:
    """文档级向量（spec F18）：该文档全部 child embedding 逐维平均 + L2 归一化。

    无 child（未入库/非 approved）返回 None。文档多时全量拉取开销见
    contract-9 §6（本 sprint 不加缓存，保持最简单）。
    """
    col = _get_collection()
    res = col.get(where={"document_id": document_id}, include=["embeddings"])
    vecs = res.get("embeddings")
    if vecs is None or len(vecs) == 0:  # numpy 数组不能用 `or` 判真（ambiguous）
        return None
    n = len(vecs)
    avg = [sum(float(v[i]) for v in vecs) / n for i in range(len(vecs[0]))]
    norm = math.sqrt(sum(x * x for x in avg)) or 1.0
    return [x / norm for x in avg]


def query_similar_documents(document_vector: list[float], exclude_document_id: int,
                            top_k: int = 5, user_department_id: int | None = None,
                            is_admin: bool = False) -> list[dict]:
    """按可见性过滤的文档级相似查询（spec F18）。

    1) 用现有 query()（已按 status=approved + 公开/本部门过滤）多取候选：
       n_results = max(top_k * 4, 20)（去重后保证足量，超库内数量时 Chroma 自动截断）；
    2) 按 document_id 去重，每文档取最小 distance（该文档与目标最近的 child 距离），
       排除 exclude_document_id（自身）；
    3) 升序取 top_k。返回 [{document_id, distance}]。
    排除自身在应用层做（Chroma where 组合 $ne 与现有 $and 复杂且易错）。
    """
    hits = query(document_vector, max(top_k * 4, 20),
                 user_department_id=user_department_id, is_admin=is_admin)
    best: dict[int, float] = {}
    for h in hits:
        did = h["document_id"]
        if did == exclude_document_id:
            continue
        dist = h["distance"]
        if did not in best or dist < best[did]:
            best[did] = dist
    ordered = sorted(best, key=lambda k: best[k])[:top_k]
    return [{"document_id": did, "distance": best[did]} for did in ordered]
