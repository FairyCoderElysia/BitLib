# -*- coding: utf-8 -*-
"""检索路由（spec §10.6 / F3）：GET /api/search。"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import client_ip
from ..cache import cache
from ..db import get_db
from ..deps import get_current_user
from ..errors import bad_request
from ..search_service import hybrid_search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])

CACHE_TTL = 600  # 热点搜索缓存秒数


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
