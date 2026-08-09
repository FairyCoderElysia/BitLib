# -*- coding: utf-8 -*-
"""用户端通知中心（M5，spec F10 / §6.5 推送流 / §10.3）。

可见规则（spec §2.3 部门维度）：推送 department_id 空 = 全员，非空 = 仅本部门成员可见；
无部门用户（含 admin）仅见全员推送。已读状态存 PushRead。
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..deps import get_current_user
from ..errors import not_found

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _visible_query(db: Session, user: models.User):
    """当前用户可见推送：全员（department_id 空）或 本部门定向。"""
    q = db.query(models.PushNotification)
    if user.department_id is not None:
        q = q.filter((models.PushNotification.department_id.is_(None)) |
                     (models.PushNotification.department_id == user.department_id))
    else:
        q = q.filter(models.PushNotification.department_id.is_(None))
    return q


def _visible_ids_query(db: Session, user: models.User):
    """当前用户可见推送 ID 子查询（供已读统计 IN 使用）。"""
    q = db.query(models.PushNotification.id)
    if user.department_id is not None:
        return q.filter((models.PushNotification.department_id.is_(None)) |
                        (models.PushNotification.department_id == user.department_id))
    return q.filter(models.PushNotification.department_id.is_(None))


def _push_to_dict(n, is_read: bool = False) -> dict:
    return {
        "id": n.id,
        "title": n.title,
        "content": n.content,
        "document_id": n.document_id,
        "department_id": n.department_id,
        "created_by": n.created_by,
        "created_at": n.created_at,
        "is_read": is_read,
    }


@router.get("")
def list_notifications(page: int = 1, page_size: int = 20,
                       db: Session = Depends(get_db),
                       current_user: models.User = Depends(get_current_user)):
    """通知列表（分页）+ 未读数。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = _visible_query(db, current_user)
    items, total = schemas.paginate(
        q.order_by(models.PushNotification.created_at.desc()), page, page_size)
    # 未读数 = 可见通知总数 - 可见通知中已读数
    read_count = 0
    if total:
        read_count = db.query(models.PushRead).filter(
            models.PushRead.user_id == current_user.id,
            models.PushRead.notification_id.in_(_visible_ids_query(db, current_user)),
        ).count()
    unread_count = max(0, total - read_count)

    read_ids = set()
    if items:
        ids = [n.id for n in items]
        read_ids = {r.notification_id for r in db.query(models.PushRead).filter(
            models.PushRead.user_id == current_user.id,
            models.PushRead.notification_id.in_(ids)).all()}
    return schemas.ok({
        "total": total, "page": page, "page_size": page_size,
        "unread_count": unread_count,
        "items": [_push_to_dict(n, n.id in read_ids) for n in items],
    })


@router.post("/{notification_id}/read")
def mark_read(notification_id: int,
              db: Session = Depends(get_db),
              current_user: models.User = Depends(get_current_user)):
    """标记单条已读（幂等）。"""
    n = db.get(models.PushNotification, notification_id)
    if n is None or _visible_query(db, current_user).filter(
            models.PushNotification.id == notification_id).first() is None:
        raise not_found("通知不存在或无权访问")
    existing = db.query(models.PushRead).filter(
        models.PushRead.notification_id == notification_id,
        models.PushRead.user_id == current_user.id).first()
    if existing is None:
        db.add(models.PushRead(notification_id=notification_id, user_id=current_user.id))
        db.commit()
    return schemas.ok({"id": notification_id, "is_read": True})


@router.post("/read-all")
def mark_all_read(db: Session = Depends(get_db),
                  current_user: models.User = Depends(get_current_user)):
    """全部已读（spec §10.3）：对当前用户全部可见推送标记已读。"""
    ids = [r[0] for r in _visible_query(db, current_user)
           .with_entities(models.PushNotification.id).all()]
    existing = set()
    if ids:
        existing = {r[0] for r in db.query(models.PushRead.notification_id).filter(
            models.PushRead.user_id == current_user.id,
            models.PushRead.notification_id.in_(ids)).all()}
    for nid in ids:
        if nid not in existing:
            db.add(models.PushRead(notification_id=nid, user_id=current_user.id))
    db.commit()
    return schemas.ok({"marked": len(ids) - len(existing), "is_read": True})
