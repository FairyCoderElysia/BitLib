# -*- coding: utf-8 -*-
"""Pydantic 请求/响应 schema 与 dict 序列化辅助。"""
from datetime import datetime
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, Field

from .summary import get_display_summary

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


class ChangePasswordRequest(BaseModel):
    """自助修改密码请求。字段手动校验（Any 类型），路由内统一返回 400 业务码。"""
    old_password: Any = None
    new_password: Any = None


class BatchApproveRequest(BaseModel):
    """批量审批请求。字段手动校验（Any 类型），路由内统一返回 400 业务码。"""
    action: Any = None
    document_ids: Any = None
    reason: Any = None


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
        "must_change_password": bool(user.must_change_password),
        "created_at": user.created_at,
    }


def document_to_dict(doc) -> dict:
    # S7：新增 departments / department_ids；保留旧 department_id / department_name
    # 兼容字段（主部门 = 集合最小 id；集合空时两者为 null）。
    from .document_departments import get_doc_dept_pairs
    pairs = get_doc_dept_pairs(doc) or []
    department_ids = [did for did, _ in pairs]
    primary_id = department_ids[0] if department_ids else None
    primary_name = None
    if pairs:
        names = {did: name for did, name in pairs}
        primary_name = names.get(primary_id)
    elif doc.department_id is not None:
        primary_name = doc.department.name if doc.department else None
    return {
        "id": doc.id,
        "title": doc.title,
        "file_name": doc.file_name,
        "file_type": doc.file_type,
        "file_size": doc.file_size,
        "status": doc.status,
        "department_id": primary_id,
        "department_name": primary_name,
        "departments": [{"id": did, "name": name} for did, name in pairs],
        "department_ids": department_ids,
        "source": doc.source,
        "is_featured": doc.is_featured,
        "summary": get_display_summary(doc),  # F17：摘要（pending/rejected 为截取/空）
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
