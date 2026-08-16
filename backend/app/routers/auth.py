# -*- coding: utf-8 -*-
"""认证路由（F1）：登录 / 当前用户 / 部门列表。无开放注册。"""
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import client_ip, log_action
from ..db import get_db
from ..deps import get_current_user
from ..errors import bad_request, unauthorized
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login")
def login(body: schemas.LoginRequest, db: Session = Depends(get_db)):
    """账号密码登录，返回 JWT + 用户信息（角色/部门/用户名）。

    密码错误统一提示"用户名或密码错误"，不泄露账号是否存在（F1）。
    """
    user = db.query(models.User).filter(models.User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise unauthorized("用户名或密码错误")
    token = create_access_token(user.id, user.username, user.role)
    return schemas.ok({
        "token": token,
        "user": schemas.user_to_dict(user),
    })


@router.get("/me")
def me(current_user: models.User = Depends(get_current_user)):
    """当前登录用户信息。"""
    return schemas.ok(schemas.user_to_dict(current_user))


@router.post("/change-password")
def change_password(body: Any = Body(None),
                    request: Request = None,
                    db: Session = Depends(get_db),
                    current_user: models.User = Depends(get_current_user)):
    """自助修改密码（F1 修复）：已登录用户可修改自己的密码。

    手动校验（统一 400 业务码，避免 FastAPI 默认 422）：
    - old_password 必须为 1-128 字符串；校验失败返回 400「原密码错误」
    - new_password 必须为 6-128 字符串；长度不足返回 400
    - new_password 与 old_password 相同返回 400
    成功后更新 password_hash、清除 must_change_password，并写审计 change_password。
    """
    if not isinstance(body, dict):
        raise bad_request("请求体必须为 JSON 对象")
    old_password = body.get("old_password")
    new_password = body.get("new_password")
    if not isinstance(old_password, str) or not old_password:
        raise bad_request("old_password 不能为空")
    if not isinstance(new_password, str) or not new_password:
        raise bad_request("new_password 不能为空")
    if len(old_password) > 128:
        raise bad_request("old_password 长度不能超过 128")
    if len(new_password) > 128:
        raise bad_request("new_password 长度不能超过 128")
    if len(new_password) < 6:
        raise bad_request("新密码长度至少 6 位")
    if not verify_password(old_password, current_user.password_hash):
        raise bad_request("原密码错误")
    if new_password == old_password:
        raise bad_request("新密码不能与旧密码相同")

    current_user.password_hash = hash_password(new_password)
    current_user.must_change_password = False
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    log_action(db, current_user, "change_password", "user", current_user.id, {},
               client_ip(request))
    return schemas.ok(schemas.user_to_dict(current_user))


@router.get("/departments")
def departments(db: Session = Depends(get_db),
                current_user: models.User = Depends(get_current_user)):
    """部门列表（登录可用）。"""
    rows = db.query(models.Department).order_by(models.Department.id).all()
    return schemas.ok([{"id": d.id, "name": d.name} for d in rows])
