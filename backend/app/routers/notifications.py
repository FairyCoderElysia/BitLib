# -*- coding: utf-8 -*-
"""用户端通知中心（M5，spec F10 / §6.5 推送流 / §10.3）。

S7 F3 可见规则按「目标部门集合」判定：连接表为空 = 全员；非空 = 仅集合内部门成员可见。
无部门用户（含 admin）仅见全员（空集合）推送；admin 不特殊放权，有部门按本部门判定。
已读状态存 PushRead。
"""
import logging

from fastapi import APIRouter, Depends
from sqlalchemy import exists
from sqlalchemy.orm import Session

from .. import models, push_notification_departments, schemas
from ..db import get_db
from ..deps import get_current_user
from ..errors import not_found

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _link_exists(dept_id=None):
    """返回「存在一条目标部门连接行」的 EXISTS（dept_id 可指定部门）。"""
    from sqlalchemy import and_

    link = models.PushNotificationDepartment
    cond = link.notification_id == models.PushNotification.id
    if dept_id is not None:
        cond = and_(cond, link.department_id == int(dept_id))
    return exists().where(cond)


def _visibility_filter(query, user: models.User):
    """单一可见谓词生成器（F3-2/D6）：
    空集合（无连接行）→ 全员可见；非空 → user.department_id ∈ 集合。
    admin 不特殊放权；无部门用户仅见空集合（全员）推送。
    """
    if user.department_id is None:
        return query.filter(~_link_exists())
    return query.filter(~_link_exists() | _link_exists(user.department_id))


def _visible_query(db: Session, user: models.User):
    """当前用户可见推送查询（列表/total/mark_read/详情共用）。"""
    return _visibility_filter(db.query(models.PushNotification), user)


def _visible_ids_query(db: Session, user: models.User):
    """当前用户可见推送 ID 子查询（供已读统计 IN 使用）。"""
    return _visibility_filter(db.query(models.PushNotification.id), user)


def _push_to_dict(n, is_read: bool = False) -> dict:
    pairs = push_notification_departments.get_push_dept_pairs(n)
    return {
        "id": n.id,
        "title": n.title,
        "content": n.content,
        "document_id": n.document_id,
        "department_id": n.department_id,
        "department_ids": [did for did, _ in pairs],
        "departments": [{"id": did, "name": nm} for did, nm in pairs],
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
    push_notification_departments.attach_push_department_sets(db, items)
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


@router.get("/{notification_id}")
def notification_detail(notification_id: int,
                        db: Session = Depends(get_db),
                        current_user: models.User = Depends(get_current_user)):
    """通知详情直读（S7 D6）：按集合口径校验可见性，不可见返回 404。"""
    n = db.get(models.PushNotification, notification_id)
    if n is None or _visible_query(db, current_user).filter(
            models.PushNotification.id == notification_id).first() is None:
        raise not_found("通知不存在或无权访问")
    is_read = db.query(models.PushRead).filter(
        models.PushRead.notification_id == notification_id,
        models.PushRead.user_id == current_user.id).first() is not None
    return schemas.ok(_push_to_dict(n, is_read))


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
