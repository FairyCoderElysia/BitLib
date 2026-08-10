# -*- coding: utf-8 -*-
"""检索路由（spec §10.6 / F3 / F20）：GET /api/search、热词榜、输入联想。"""
import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

import jieba
from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import client_ip
from ..cache import cache
from ..db import get_db
from ..deps import get_current_user
from ..errors import bad_request
from ..search_service import hybrid_search
from ..visibility import dept_visible

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])

CACHE_TTL = 600  # 热点搜索缓存秒数

# 热词榜 / 联想缓存（F20）
HOT_WORDS_CACHE_KEY = "hotwords:all"   # 全局（查询词不绑定部门）
HOT_WORDS_TTL = 600
SUGGEST_TTL = 300
SUGGEST_PAGE_SIZE = 8
HOT_WORDS_WINDOW_DAYS = 30             # 近 30 天搜索日志窗口

# 常用中文停用词（过滤热词榜噪声，spec §3 F20）
STOP_WORDS = frozenset(
    "的了是在我有和就都而及与或一个上也很到说要去你会着没有看好不那等"
)


def _document_snapshot(doc: models.Document) -> dict:
    return {
        "id": doc.id, "title": doc.title, "file_name": doc.file_name,
        "file_type": doc.file_type, "file_size": doc.file_size,
        "status": doc.status, "is_featured": doc.is_featured,
        "source": doc.source, "department_id": doc.department_id,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


@router.get("")
def search(
    q: str,
    request: Request,
    page: int = 1,
    page_size: int = 20,
    department_id: Optional[int] = None,
    file_type: Optional[str] = None,
    source: Optional[str] = None,
    is_featured: Optional[bool] = None,
    sort: str = "relevance",  # relevance / time
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """关键词+语义混合检索（权限过滤在召回阶段，重排后兜底校验）。"""
    q = q.strip()
    if not q:
        raise bad_request("查询词不能为空")
    if page < 1 or page_size < 1 or page_size > 100:
        raise bad_request("分页参数非法")

    # 跨部门筛选仅 admin 可用（spec §10.6）
    if department_id is not None and user.role != models.ROLE_ADMIN:
        raise bad_request("仅管理员可按部门筛选")

    cache_key = (f"search:{user.id}:{q}:{department_id}:{file_type}:{source}:"
                 f"{is_featured}:{sort}:{page}:{page_size}")
    cached = cache.get(cache_key)
    if cached is not None:
        items = _filter_cache(db, user, cached["items"])
        if len(items) == len(cached["items"]) and items:
            return schemas.ok({
                "total": cached["total"], "page": page, "page_size": page_size,
                "items": items, "cached": True,
            })

    hits = hybrid_search(db, user, q, limit=20)
    results = []
    for h in hits:
        doc = h["document"]
        # 筛选参数
        if file_type and doc.file_type != file_type:
            continue
        if source and doc.source != source:
            continue
        if is_featured is not None and doc.is_featured != is_featured:
            continue
        results.append({"doc": doc, "snippet": h["snippet"], "score": h["score"]})

    if sort == "time":
        results.sort(key=lambda r: r["doc"].created_at, reverse=True)

    total = len(results)
    start = (page - 1) * page_size
    items = [_document_snapshot(r["doc"]) | {"snippet": r["snippet"],
                                             "score": round(r["score"], 4)}
             for r in results[start:start + page_size]]

    # 记录 SearchLog（热词数据源，P2）
    try:
        db.add(models.SearchLog(user_id=user.id, query=q, hit_count=total))
        db.commit()
    except Exception as exc:
        logger.warning("SearchLog 写入失败: %s", exc)
        db.rollback()

    if page == 1 and items:
        cache.set(cache_key, {"total": total, "items": items}, CACHE_TTL)
    return schemas.ok({"total": total, "page": page, "page_size": page_size,
                       "items": items, "cached": False})


def _filter_cache(db: Session, user: models.User, items: list[dict]) -> list[dict]:
    """缓存有效性校验（spec F16：删除/下架后缓存不返回过期结果）。

    对 items 的文档 id 批量校验仍 approved 且当前用户可见，失效项剔除；
    全部失效返回空（触发回源重算）。
    """
    if not items:
        return []
    ids = [it["id"] for it in items]
    dept_cond = "1=1" if user.role == models.ROLE_ADMIN else \
        "(department_id IS NULL OR department_id = :dept)"
    # ids 来自缓存快照（int），内联避免 SQLite text() expanding IN 问题
    ids_str = ",".join(str(i) for i in ids)
    rows = db.execute(
        text(f"SELECT id FROM document WHERE id IN ({ids_str}) "
             f"AND status='approved' AND {dept_cond}"),
        {"dept": user.department_id},
    ).fetchall()
    valid = {r[0] for r in rows}
    return [it for it in items if it["id"] in valid]


def _is_junk_word(word: str) -> bool:
    """热词过滤：单字 / 停用词 / 纯数字或纯符号丢弃（保留中文/字母词条）。"""
    if len(word) < 2:
        return True
    if word in STOP_WORDS:
        return True
    # 不含任何字母或汉字（纯数字、纯符号、纯空白）视为噪声
    if not any(ch.isalpha() for ch in word):
        return True
    return False


@router.get("/hot-words")
def hot_words(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """热词榜（spec F20）：聚合近 30 天 SearchLog.query → jieba 分词。

    全局聚合（查询词本身不绑定部门，admin 与普通用户同一榜单）；
    仅返回分词词条（≥2 字符、滤停用词/纯数字符号），不返回完整查询词，
    避免泄露文档内容。SearchLog 为空返回空数组。
    """
    cached = cache.get(HOT_WORDS_CACHE_KEY)
    if cached is not None:
        return schemas.ok({"items": cached["items"], "cached": True})

    since = datetime.utcnow() - timedelta(days=HOT_WORDS_WINDOW_DAYS)
    rows = db.execute(
        text("SELECT query FROM search_log WHERE created_at >= :since"),
        {"since": since},
    ).fetchall()

    counter: Counter = Counter()
    for (query,) in rows:
        if not query:
            continue
        for word in jieba.cut_for_search(query):
            word = word.strip()
            if _is_junk_word(word):
                continue
            counter[word] += 1

    items = [w for w, _ in counter.most_common(10)]
    # 空结果不缓存：避免 SearchLog 由空转非空后仍命中空缓存（缓存污染）
    if items:
        cache.set(HOT_WORDS_CACHE_KEY, {"items": items}, HOT_WORDS_TTL)
    return schemas.ok({"items": items, "cached": False})


@router.get("/suggest")
def suggest(
    q: str = "",
    page_size: int = SUGGEST_PAGE_SIZE,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """输入联想（spec F20）：按当前用户可见性过滤 approved 文档标题。

    前缀命中（title LIKE 'q%'）优先，不足时用标题包含 q（LIKE '%q%'）补齐，
    去重后最多返回 SUGGEST_PAGE_SIZE 条；q 为空返回空数组。
    缓存按用户隔离（key 带 user.id），避免不同部门用户复用彼此标题。
    """
    q = q.strip()
    if not q:
        return schemas.ok({"items": []})
    page_size = max(1, min(int(page_size), SUGGEST_PAGE_SIZE))

    cache_key = f"suggest:{user.id}:{q}"
    cached = cache.get(cache_key)
    if cached is not None:
        items = cached["items"]
        # 可见性兜底（Evaluator 发现：文档下架/改部门后有 300s 泄露窗口）
        if items:
            ids = [it["id"] for it in items]
            dept_cond = "1=1" if user.role == models.ROLE_ADMIN else \
                "(department_id IS NULL OR department_id = :dept)"
            ids_str = ",".join(str(i) for i in ids)
            rows = db.execute(
                text(f"SELECT id FROM document WHERE id IN ({ids_str}) "
                     f"AND status='approved' AND {dept_cond}"),
                {"dept": user.department_id},
            ).fetchall()
            valid = {r[0] for r in rows}
            items = [it for it in items if it["id"] in valid]
        return schemas.ok({"items": items, "cached": True})

    base = db.query(models.Document).filter(
        models.Document.status == models.STATUS_APPROVED)
    if user.role != models.ROLE_ADMIN:
        base = base.filter(or_(
            models.Document.department_id.is_(None),
            models.Document.department_id == user.department_id,
        ))

    items: list[dict] = []
    seen: set[str] = set()

    def _append(docs) -> None:
        """追加候选：去重 + 二次可见性校验（与 dept_visible 口径一致）。"""
        for doc in docs:
            if len(items) >= page_size:
                return
            if doc.title in seen or not dept_visible(user, doc):
                continue
            seen.add(doc.title)
            items.append({"id": doc.id, "title": doc.title})

    # 前缀命中优先，不足用包含命中补齐
    _append(base.filter(models.Document.title.like(f"{q}%")).all())
    if len(items) < page_size:
        _append(base.filter(models.Document.title.like(f"%{q}%")).all())

    # 空结果不缓存：避免新增/可见文档后联想仍命中空缓存（缓存污染）
    if items:
        cache.set(cache_key, {"items": items}, SUGGEST_TTL)
    return schemas.ok({"items": items, "cached": False})
