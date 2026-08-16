# -*- coding: utf-8 -*-
"""混合检索（spec §6.3 / F3）：关键词(FTS bm25) + 语义(Chroma) 双路召回 → RRF 融合 → 重排。

- 双路均在召回阶段按可见性过滤（FTS 冗余列 / Chroma metadata），不可见文档不进入后续计算
- 重排后做兜底权限校验（防脏数据）
"""
import logging
from typing import Optional

import jieba
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .embeddings import embed
from .rerank import rerank
from .vector_store import query as vector_query

logger = logging.getLogger(__name__)

RRF_K = 60
RECALL_TOP_K = 30


def keyword_recall(db: Session, user: models.User, query: str, top_k: int = RECALL_TOP_K) -> list[int]:
    """关键词路：jieba 分词 → FTS5 MATCH → bm25 排序 → 可见性过滤（冗余列）。

    admin 角色不限部门（spec：管理员可见全部文档）；普通用户限 公开+本部门。
    """
    tokens = [t for t in jieba.cut_for_search(query) if t.strip() and len(t.strip()) > 1]
    if not tokens:
        return []
    # FTS5 前缀通配：jieba 常把复合词作整体 token（如"知识库"），用户搜子词（"知识"）
    # 时精确匹配会漏。每个 token 加 `*` 前缀（"知识*" 命中"知识库"），
    # 由 RRF + 重排负责排序与过滤（用户反馈：搜"知识"无结果）。
    match = " OR ".join(f"{t}*" for t in tokens)
    # 部门过滤：admin 跳过（历史 bug：admin department_id 为 None 时被限成仅公开文档）
    if user.role == models.ROLE_ADMIN:
        dept_clause = ""
    else:
        dept_list = [""]
        if user.department_id is not None:
            dept_list.append(str(user.department_id))
        placeholders = ",".join(f"'{d}'" for d in dept_list)  # 可信值（int/常量）
        dept_clause = f"AND department_id IN ({placeholders}) "
    sql = (
        "SELECT rowid FROM document_fts "
        "WHERE document_fts MATCH :kw AND status = 'approved' " +
        dept_clause +
        "ORDER BY bm25(document_fts) LIMIT :lim"
    )
    params = {"kw": match, "lim": top_k}
    try:
        rows = db.execute(text(sql), params).fetchall()
    except Exception as exc:  # FTS 特殊字符等导致 MATCH 语法错误 → 降级空召回
        logger.warning("FTS 查询失败（query=%r）: %s", query, exc)
        return []
    return [r[0] for r in rows]


def semantic_recall(db: Session, user: models.User, query: str, top_k: int = RECALL_TOP_K) -> list[dict]:
    """语义路：Embedding → Chroma 召回（metadata 可见性过滤）。返回含 parent_id 的命中。

    Chroma 不可用（HNSW 重建窗口/损坏）时降级返回空，检索回退关键词路而非 500
    （spec §7.2：重建期间检索降级为关键词路）。
    """
    try:
        emb = embed([query])[0]
        return vector_query(emb, top_k,
                            user_department_id=user.department_id,
                            is_admin=user.role == models.ROLE_ADMIN)
    except Exception as exc:
        logger.warning("语义召回失败，降级关键词路（Chroma 不可用/索引重建中）: %s", exc)
        return []


def rrf_fuse(ranked_lists: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion：多路召回按秩融合。"""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])


def _snippet(text: str, query: str, width: int = 120) -> str:
    """命中摘要片段：定位第一个查询词附近，否则取开头。"""
    q = query.strip()
    if q:
        idx = text.find(q)
        if idx >= 0:
            start = max(0, idx - width // 2)
            return text[start:start + width * 2].replace("\n", " ")
    return text[:width * 2].replace("\n", " ")


def hybrid_search(db: Session, user: models.User, query: str,
                  top_k: int = RECALL_TOP_K, limit: int = 5,
                  scorer=None) -> list[dict]:
    """混合检索主流程。返回 [{document, snippet, score, matched, parent_ids}] 按相关度排序。

    parent_ids 为该文档在语义路命中的 parent chunk_index 去重列表（按语义命中顺序），
    供 QA 上下文装配做 small-to-big 回溯；不影响排序/RRF/重排/阈值。
    """
    if not query.strip():
        return []

    query_tokens = [t for t in jieba.cut_for_search(query) if t.strip() and len(t.strip()) > 1]
    kw_ids = keyword_recall(db, user, query, top_k)
    sem_hits = semantic_recall(db, user, query, top_k)
    sem_ids = [h["document_id"] for h in sem_hits]

    # 语义相似度阈值（spec F3）：关键词 0 命中且语义最高相似度低于阈值 → 未找到
    max_sem = max((1.0 - h["distance"] for h in sem_hits), default=-1.0)
    if not kw_ids and max_sem < settings.search_threshold:
        return []

    # RRF 融合
    fused = rrf_fuse([kw_ids, sem_ids])

    # 组装候选（重排文本用 content_text 前段；parent 回溯信息来自语义命中）
    # S5：保留原有 parent_id（首个语义命中）不动，同时按语义命中顺序收集去重的
    # parent_ids 列表，供 QA 上下文装配做 small-to-big 回溯；排序/RRF/重排/阈值均不改。
    parent_by_doc: dict[int, int] = {}
    parent_ids_by_doc: dict[int, list[int]] = {}
    for h in sem_hits:
        parent_by_doc.setdefault(h["document_id"], h["parent_id"])
        lst = parent_ids_by_doc.setdefault(h["document_id"], [])
        pid = h.get("parent_id")
        if pid not in lst:
            lst.append(pid)

    candidates = []
    for doc_id, rrf in fused[: max(limit * 4, 20)]:
        doc = db.get(models.Document, doc_id)
        if doc is None or doc.status != models.STATUS_APPROVED:
            continue
        if not _dept_visible_after(user, doc):  # 兜底权限校验
            continue
        candidates.append({
            "document_id": doc.id,
            "title": doc.title,
            "text": (doc.content_text or "")[:800],
            "rrf": rrf,
            "is_featured": doc.is_featured,
            "doc": doc,
            "parent_id": parent_by_doc.get(doc.id, 0),
            # 查询词覆盖度：文档正文中出现的查询 token 数 / 查询 token 总数。
            # 前缀通配会让泛词（如"企业"）命中大量弱相关文档，仅凭 FTS 命中
            # 强制保留会混入噪音；覆盖 >=50% 查询词才算强关键词相关
            # （单 token 查询天然 1.0，如"知识"→"知识库"）。
            "kw_cov": (sum(1 for t in query_tokens if t in (doc.content_text or ""))
                       / max(len(query_tokens), 1)),
        })

    ranked = rerank(query, candidates, limit=limit, scorer=scorer)

    # 重排分数下限过滤（用户反馈：0.5 中性分噪音文档不应返回）
    # 仅当实际使用 cross-encoder（reranker_enabled）时分数是 sigmoid 概率，可套阈值；
    # RRF 模式（分数为秩融合值）不适用。
    # 关键词强相关（kw_cov >= 0.5，即覆盖半数以上查询词，如"知识"→"知识库"）无条件
    # 保留——短查询时相关文档分数可能整体偏低（~0.53），绝对阈值会误伤；
    # 仅覆盖少量泛词的弱命中（如多词查询只沾"企业"）仍按分数过滤。
    if settings.reranker_enabled:
        ranked = [c for c in ranked
                  if c.get("kw_cov", 0.0) >= 0.5 or c["score"] >= settings.rerank_threshold]
        ranked = ranked[:limit]

    results = []
    for c in ranked:
        results.append({
            "document": c["doc"],
            "score": c["score"],
            "snippet": _snippet(c["text"], query),
            "matched": None,
            "parent_ids": parent_ids_by_doc.get(c["doc"].id, []),
        })
    return results


def _dept_visible_after(user: models.User, doc: models.Document) -> bool:
    """兜底可见性校验（等价 dept_visible，避免循环 import）。"""
    if user.role == models.ROLE_ADMIN:
        return True
    if doc.department_id is None:
        return True
    return doc.department_id == user.department_id
