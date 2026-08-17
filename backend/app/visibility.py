# -*- coding: utf-8 -*-
"""文档可见性（spec §2.3 / §2.4 状态-可见性矩阵）。

统一在后端强制过滤：详情/预览/下载/检索/收藏/问答全部走此判定。
S7：部门维度改为「可见部门集合」口径——admin 全量；集合空=公开全网；非空仅集合内部门。
集合一律经 document_departments.get_doc_dept_ids 读取连接表，不信任 Document.department_id 单值。
"""
from . import models
from .document_departments import get_doc_dept_ids


def dept_visible(user: models.User, doc: models.Document) -> bool:
    """部门维度：admin 全量；公开(集合空)全员；非空仅集合内部门成员。"""
    if user is None or doc is None:
        return False
    if user.role == models.ROLE_ADMIN:
        return True
    ids = set(get_doc_dept_ids(doc))
    if not ids:
        return True  # 公开
    return user.department_id in ids


def dept_managed(user: models.User, doc: models.Document) -> bool:
    """dept_admin 管理边界：admin 全量；非 dept_admin 不可管；否则须「集合非空且含本部门」。

    公开文档（集合空）不纳入 dept_admin 管理口径（保持既有规则）。
    """
    if user is None or doc is None:
        return False
    if user.role == models.ROLE_ADMIN:
        return True
    if user.role != models.ROLE_DEPT_ADMIN:
        return False
    ids = set(get_doc_dept_ids(doc))
    return bool(ids) and user.department_id in ids


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
    return dept_managed(user, doc)


def can_preview(user: models.User, doc: models.Document) -> bool:
    """预览权限：approved 部门可见者；pending/processing/failed 上传者+审批者（审阅用途）。

    offline 全角色不可访问。
    """
    if doc.status == models.STATUS_OFFLINE:
        return False
    return can_access(user, doc)
