# -*- coding: utf-8 -*-
"""文档可见性（spec §2.3 / §2.4 状态-可见性矩阵）。

统一在后端强制过滤：详情/预览/下载/检索/收藏/问答全部走此判定。
"""
from . import models


def dept_visible(user: models.User, doc: models.Document) -> bool:
    """部门维度：admin 全量；公开(department_id 空)全员；非空仅本部门。"""
    if user.role == models.ROLE_ADMIN:
        return True
    if doc.department_id is None:
        return True
    return doc.department_id == user.department_id


def can_access(user: models.User, doc: models.Document) -> bool:
    """下载/详情完整内容权限（spec §2.4）。

    offline → 全角色不可访问；approved → 部门可见者；非 approved → 仅上传者/审批者。
    """
    if doc.status == models.STATUS_OFFLINE:
        return False
    if doc.status == models.STATUS_APPROVED:
        return dept_visible(user, doc)
    if user.role == models.ROLE_ADMIN:
        return True
    if doc.uploaded_by == user.id:
        return True
    if user.role == models.ROLE_DEPT_ADMIN and doc.department_id == user.department_id:
        return True
    return False


def can_preview(user: models.User, doc: models.Document) -> bool:
    """预览权限：approved 部门可见者；pending/processing/failed 上传者+审批者（审阅用途）。

    offline 全角色不可访问。
    """
    if doc.status == models.STATUS_OFFLINE:
        return False
    return can_access(user, doc)