# -*- coding: utf-8 -*-
"""认证路由（F1）：登录 / 当前用户 / 部门列表。无开放注册。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..deps import get_current_user
from ..errors import unauthorized
from ..security import create_access_token, verify_password

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


@router.get("/departments")
def departments(db: Session = Depends(get_db),
                current_user: models.User = Depends(get_current_user)):
    """部门列表（登录可用）。"""
    rows = db.query(models.Department).order_by(models.Department.id).all()
    return schemas.ok([{"id": d.id, "name": d.name} for d in rows])
