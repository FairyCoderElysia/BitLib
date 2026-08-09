# -*- coding: utf-8 -*-
"""FastAPI 依赖：当前用户与角色校验（deps.py）。

- get_current_user：解析 Bearer token → 从数据库加载用户（角色实时生效）
- require_admin：仅 admin
- require_dept_admin：admin 或 dept_admin（管理端接口共用）
"""
from fastapi import Depends, Request
from sqlalchemy.orm import Session

from . import models
from .db import get_db
from .errors import unauthorized, forbidden
from .security import decode_token


def get_current_user(request: Request, db: Session = Depends(get_db)) -> models.User:
    """解析 Authorization: Bearer <token> 并返回当前用户。无效/过期 → 401。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise unauthorized()
    token = auth[len("Bearer "):].strip()
    payload = decode_token(token)
    if not payload:
        raise unauthorized()
    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise unauthorized()
    user = db.get(models.User, user_id)
    if user is None:
        raise unauthorized()
    return user


def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    """仅管理员可访问。"""
    if current_user.role != models.ROLE_ADMIN:
        raise forbidden()
    return current_user


def require_dept_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    """admin 或 dept_admin 可访问（审批 / 直入库等管理操作）。"""
    if current_user.role not in (models.ROLE_ADMIN, models.ROLE_DEPT_ADMIN):
        raise forbidden()
    return current_user
