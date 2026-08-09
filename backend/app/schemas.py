# -*- coding: utf-8 -*-
"""Pydantic 请求/响应 schema 与 dict 序列化辅助。"""
from datetime import datetime
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")

# ---------------- 请求体 ----------------


class LoginRequest(BaseModel):
    """登录请求。"""
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class RejectRequest(BaseModel):
    """审批拒绝请求：必须附原因。"""
    reason: str = Field(..., min_length=1, max_length=500)


class UserCreate(BaseModel):
    """管理员建号请求。"""
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    role: str = Field(default="user", pattern="^(admin|dept_admin|user)$")
    department_id: Optional[int] = None


class UserUpdate(BaseModel):
    """管理员修改用户：角色 / 部门 / 重置密码（均可选，显式传 null 可清空部门）。"""
    role: Optional[str] = Field(None, pattern="^(admin|dept_admin|user)$")
    department_id: Optional[int] = None
    password: Optional[str] = Field(None, min_length=6, max_length=128)


# ---------------- 分页响应 ----------------


class Page(BaseModel, Generic[T]):
    """统一分页结构：total / page / page_size / items。"""
    total: int
    page: int
    page_size: int
    items: List[T]


# ---------------- 序列化辅助（返回统一 data 结构） ----------------


def ok(data: Any = None) -> dict:
    """统一成功响应：{"code": 0, "message": "ok", "data": ...}"""
    return {"code": 0, "message": "ok", "data": data}


def user_to_dict(user) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "department_id": user.department_id,
        "department_name": user.department.name if user.department else None,
        "created_at": user.created_at,
    }


def document_to_dict(doc) -> dict:
    return {
        "id": doc.id,
        "title": doc.title,
        "file_name": doc.file_name,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "status": doc.status,
        "department_id": doc.department_id,
        "department_name": doc.department.name if doc.department else None,
        "source": doc.source,
        "is_featured": doc.is_featured,
        "reject_reason": doc.reject_reason,
        "error_message": doc.error_message,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
    }


def audit_log_to_dict(log) -> dict:
    return {
        "id": log.id,
        "user_id": log.user_id,
        "username": log.user.username if log.user else None,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "detail": log.detail,
        "ip": log.ip,
        "created_at": log.created_at,
    }


def paginate(query, page: int, page_size: int):
    """通用分页：返回 (items, total)。"""
    total = query.count()
    items = query.offset((page - 1) * page_size).limit(page_size).all()
    return items, total


class QARequest(BaseModel):
    """AI 问答请求（spec §10.7）。"""
    question: str = Field(..., min_length=1, max_length=2000)
    session_id: Optional[int] = None  # 续接会话（修复#4）
