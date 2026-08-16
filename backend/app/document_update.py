# -*- coding: utf-8 -*-
"""重复文件更新为新版本（F2/F8 修复）：统一权限判定与更新执行入口。

供 routers/documents.py（用户端上传）与 routers/admin.py（管理端直入库）复用，
避免 router 间循环依赖。更新不新增 Document 记录，替换原文/分块/向量，审计 action="update"。
"""
import logging

from sqlalchemy.orm import Session

from . import models
from .audit import client_ip, log_action
from .errors import bad_request, forbidden
from .ingest import ingest_document_update

logger = logging.getLogger(__name__)


def has_update_permission(user: models.User, doc: models.Document) -> bool:
    """更新权限判定（tech-design 4.2.3，用户已确认边界）。

    - admin：任意文档
    - dept_admin：本部门文档（公开文档 department_id=null 不在此列，仅 admin 可更新）
    - user：本人上传的文档
    """
    if user is None or doc is None:
        return False
    if user.role == models.ROLE_ADMIN:
        return True
    if user.role == models.ROLE_DEPT_ADMIN:
        return doc.department_id is not None and doc.department_id == user.department_id
    if user.role == models.ROLE_USER:
        return doc.uploaded_by is not None and doc.uploaded_by == user.id
    return False


def can_update_document(user: models.User, doc: models.Document) -> bool:
    """409 detail.can_update：具备权限且目标状态为 approved。"""
    return (doc is not None
            and doc.status == models.STATUS_APPROVED
            and has_update_permission(user, doc))


def assert_update_allowed(user: models.User, doc: models.Document) -> None:
    """更新前校验：先权限（403）后状态（400），与 tech-design 4.2.2 一致。"""
    if not has_update_permission(user, doc):
        raise forbidden("无权更新该文档")
    if doc.status != models.STATUS_APPROVED:
        raise bad_request(f"目标文档状态不可更新，当前状态 {doc.status}")


def update_document_from_upload(db: Session, user: models.User, doc: models.Document,
                                new_store_name: str, new_file_size: int,
                                new_file_hash: str,
                                source_label: str = "user_upload",
                                request=None) -> models.Document:
    """执行更新为新版本：状态 approved → processing → ingest_document_update → approved。

    - 可失败计算阶段失败：ingest_document_update 恢复 approved 并删除新文件；
      这里转成 400 可读错误，不写审计。
    - 破坏性替换阶段失败：ingest_document_update 置 failed 并记录 error_message；
      这里转成 400 可读错误（可 reprocess 恢复），不写审计。
    - 成功后写审计 action="update"。
    """
    old_file_hash = doc.file_hash
    old_file_size = doc.file_size
    old_file_name = doc.file_name

    assert_update_allowed(user, doc)

    doc.status = models.STATUS_PROCESSING
    doc.error_message = None
    db.add(doc)
    db.commit()

    try:
        ingest_document_update(db, doc, new_store_name, new_file_size, new_file_hash)
    except Exception as exc:
        db.rollback()
        raise bad_request(str(exc)[:500])

    # D1：审计失败不得让已成功提交的更新表现为 500/半更新，只记录日志。
    try:
        log_action(db, user, "update", "document", doc.id,
                   {"source": source_label,
                    "file_name": old_file_name,
                    "old_file_hash": old_file_hash,
                    "new_file_hash": new_file_hash,
                    "old_file_size": old_file_size,
                    "new_file_size": new_file_size},
                   client_ip(request))
    except Exception:
        logger.exception("更新审计写入失败（doc_id=%s），更新本身已提交", doc.id)
    return doc
