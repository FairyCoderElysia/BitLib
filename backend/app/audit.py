# -*- coding: utf-8 -*-
"""审计日志写入（spec §3.6）：上传/撤回/审批/直入库/用户变更等关键操作。"""
from typing import Optional

from sqlalchemy.orm import Session

from . import models


def log_action(db: Session, user: Optional[models.User], action: str,
               target_type: str = "", target_id: Optional[int] = None,
               detail: Optional[dict] = None, ip: str = "") -> models.AuditLog:
    """写入一条审计日志并立即提交（独立事务，只增不删，不随业务回滚丢失）。

    :param db: 数据库会话
    :param user: 操作人（可空）
    :param action: 动作，如 upload / approve / reject / withdraw / direct_upload / user_create
    :param target_type: 对象类型，如 document / user
    :param target_id: 对象 ID
    :param detail: 详情 JSON
    :param ip: 操作人 IP
    """
    log = models.AuditLog(
        user_id=user.id if user else None,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip=ip,
    )
    db.add(log)
    db.commit()
    return log


def client_ip(request) -> str:
    """从 Request 提取客户端 IP。"""
    return request.client.host if request and request.client else ""
