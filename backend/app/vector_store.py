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
import threading
import time

from .config import settings

logger = logging.getLogger(__name__)

PUBLIC_DEPT = ""  # metadata 中公开文档的 department_id 值

_client = None
_collection = None

STATUS_APPROVED = "approved"

# 文档级向量 TTL 缓存（S11 优化 4）：key=(document_id, updated_at)
# updated_at 变化即失效（Document.updated_at 有 onupdate，任何 UPDATE 自动刷新）；
# 进程内内存态（本服务单进程运行），TTL 600s + 容量上限 2048 防无界增长。
VEC_CACHE_TTL = 600          # 秒
VEC_CACHE_MAX = 2048         # 容量上限
_vec_cache: dict[tuple[int, str], tuple[float, list[float] | None]] = {}
_vec_cache_lock = threading.Lock()


def _cache_put(key: tuple[int, str], vec: list[float] | None) -> None:
    """写缓存：惰性删除过期条目；超容量上限时整体清空（保持最简）。"""
    with _vec_cache_lock:
        now = time.monotonic()
        if len(_vec_cache) >= VEC_CACHE_MAX:
            _vec_cache.clear()
        else:
            expired = [k for k, (ts, _) in _vec_cache.items() if now - ts > VEC_CACHE_TTL]
            for k in expired:
                _vec_cache.pop(k, None)
        _vec_cache[key] = (now, vec)


def _cache_get(key: tuple[int, str]):
    """读缓存；未命中返回哨兵（None 结果也缓存，需区分）。"""
    with _vec_cache_lock:
        entry = _vec_cache.get(key)
    if entry is None:
        return _MISS
    written_at, vec = entry
    if time.monotonic() - written_at > VEC_CACHE_TTL:
        return _MISS  # 过期：视为未命中，后续重算覆盖
    return vec


class _Miss:
    """缓存未命中哨兵（区别于缓存值为 None 的结果）。"""


_MISS = _Miss()


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


def _to_hits(res) -> list[dict]:
    """把 Chroma query 结果整理为命中字典列表（保持原始 distance 顺序）。"""
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
                "department_id": m.get("department_id", PUBLIC_DEPT),
            })
    return hits


def query(embedding: list[float], top_k: int,
          user_department_id: int | None = None, is_admin: bool = False,
          db=None, user=None) -> list[dict]:
    """向量召回（S7：多取候选 + 回表 dept_visible 后置过滤）。

    - fetch_k = max(top_k * 4, 60)：只按 status=approved 召回更多候选，
      部门维度不再信任 Chroma 单值 metadata（其 department_id 仅为主部门快照）；
    - 提供 db/user 时：按 candidate 的 document_id 回表 SQLite，逐个 dept_visible
      校验后按 distance 升序截断 top_k（以连接表为权威，改部门即时生效）；
    - 未提供 db/user（启动健康检查 / 旧内部调用）：回退 metadata 部门过滤，
      保持旧签名兼容。
    返回 [{document_id, parent_id, chunk_index, text, distance, status, department_id}]。
    """
    col = _get_collection()
    fetch_k = max(top_k * 4, 60)
    where = {"status": STATUS_APPROVED}
    res = col.query(query_embeddings=[embedding], n_results=fetch_k, where=where)
    hits = _to_hits(res)

    if is_admin:
        return hits[:top_k]
    if db is not None and user is not None:
        return _post_filter(db, user, hits)[:top_k]

    # 兼容旧调用（无 db）：仍按 metadata 单值 department_id 过滤（公开 + 本部门）
    depts = [PUBLIC_DEPT]
    if user_department_id is not None:
        depts.append(str(user_department_id))
    return [h for h in hits if h.get("department_id") in depts][:top_k]


def _post_filter(db, user, hits: list[dict]) -> list[dict]:
    """回表可见性后置过滤：仅保留 status=approved 且 dept_visible(user, doc) 的候选。"""
    if not hits:
        return []
    from . import models
    from .visibility import dept_visible

    ids = sorted({h.get("document_id") for h in hits if h.get("document_id") is not None})
    docs = {}
    if ids:
        rows = db.query(models.Document).filter(models.Document.id.in_(ids)).all()
        docs = {d.id: d for d in rows}
    out = []
    for h in hits:
        d = docs.get(h.get("document_id"))
        if d is None or d.status != STATUS_APPROVED:
            continue
        if not dept_visible(user, d):
            continue
        out.append(h)
    return out


def refresh_document_department_snapshot(document_id: int, department_id: int | None) -> None:
    """best-effort 刷新某文档全部 child 的 department_id 快照（仅观测用途，不参与授权）。

    Chroma 单值 metadata 天然无法表达多部门集合；授权一律回表 SQLite，此处仅为
    让观测快照尽量收敛。失败只告警，不影响主流程。
    """
    try:
        col = _get_collection()
        res = col.get(where={"document_id": document_id})
        ids = res.get("ids") or []
        if not ids:
            return
        col.update(ids=list(ids), metadatas=[{
            "department_id": "" if department_id is None else str(department_id),
        }] * len(ids))
    except Exception as exc:
        logger.warning("刷新文档 %s 的 Chroma 部门快照失败（忽略）: %s", document_id, exc)


def count_by_document(document_id: int) -> int:
    """某文档的 child 数量（测试/校验用）。"""
    col = _get_collection()
    try:
        res = col.get(where={"document_id": document_id})
        return len(res["ids"])
    except Exception:
        return 0


def reset_collection() -> None:
    """删除并重建 "chunks" 集合（运维重建向量索引用，服务进程内调用）。"""
    global _client, _collection
    _get_collection()  # 确保 client 已初始化（否则 _client 为 None）
    try:
        _client.delete_collection("chunks")
    except Exception:
        pass
    _collection = _client.get_or_create_collection(
        "chunks", metadata={"hnsw:space": "cosine"})
    logger.info("Chroma 'chunks' 集合已重建")


def get_document_vector(document_id: int, updated_at=None) -> list[float] | None:
    """文档级向量（spec F18）：该文档全部 child embedding 逐维平均 + L2 归一化。

    - 无 child（未入库/非 approved）返回 None。
    - S11 优化 4：key=(document_id, str(updated_at)) 的 TTL 缓存；updated_at 变化即
      失效（入库/更新/置 failed 均触发 onupdate 刷新）；updated_at 为 None 时跳过
      缓存（调用方无时间戳则每次重算）。None 结果也缓存（key 含 updated_at 天然失效）。
    """
    key = None
    if updated_at is not None:
        key = (document_id, str(updated_at))
        cached = _cache_get(key)
        if cached is not _MISS:
            return cached
    col = _get_collection()
    res = col.get(where={"document_id": document_id}, include=["embeddings"])
    vecs = res.get("embeddings")
    if vecs is None or len(vecs) == 0:  # numpy 数组不能用 `or` 判真（ambiguous）
        if key is not None:
            _cache_put(key, None)
        return None
    n = len(vecs)
    avg = [sum(float(v[i]) for v in vecs) / n for i in range(len(vecs[0]))]
    norm = math.sqrt(sum(x * x for x in avg)) or 1.0
    vec = [x / norm for x in avg]
    if key is not None:
        _cache_put(key, vec)
    return vec


def query_similar_documents(document_vector: list[float], exclude_document_id: int,
                            top_k: int = 5, user_department_id: int | None = None,
                            is_admin: bool = False, db=None, user=None) -> list[dict]:
    """按可见性过滤的文档级相似查询（spec F18）。

    1) 复用 query()（S7：多取候选 + 回表 dept_visible 后置过滤）多取候选；
    2) 按 document_id 去重，每文档取最小 distance，排除 exclude_document_id（自身）；
    3) 升序取 top_k。返回 [{document_id, distance}]。
    """
    hits = query(document_vector, max(top_k * 4, 20),
                 user_department_id=user_department_id, is_admin=is_admin,
                 db=db, user=user)
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
