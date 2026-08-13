# -*- coding: utf-8 -*-
"""管理端路由（M2）：审批中心 / 直入库 / 用户管理 / 审计日志。

权限（spec §2.2）：
- 审批：admin 全部，dept_admin 仅本部门 pending
- 直入库：admin/dept_admin（dept_admin 仅本部门或公开）
- 用户管理：admin 专属
- 审计日志查看：admin 专属
"""
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import client_ip, log_action
from ..db import get_db
from ..deps import require_admin, require_dept_admin
from ..errors import bad_request, conflict, forbidden, not_found
from ..ingest import ingest_document
from ..routers.documents import _build_document, delete_stored_file
from ..security import hash_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def _pending_query(db: Session, current_user: models.User):
    """审批中心可见 pending：admin 全部 / dept_admin 仅本部门。"""
    q = db.query(models.Document).filter(models.Document.status == models.STATUS_PENDING)
    if current_user.role == models.ROLE_DEPT_ADMIN:
        q = q.filter(models.Document.department_id == current_user.department_id)
    return q


def _assert_approvable(db: Session, document_id: int, current_user: models.User) -> models.Document:
    """取待审批文档并做权限/状态校验。"""
    doc = db.get(models.Document, document_id)
    if doc is None:
        raise not_found("文档不存在")
    if current_user.role == models.ROLE_DEPT_ADMIN and \
            doc.department_id != current_user.department_id:
        raise forbidden("无权审批其他部门的文档")
    if doc.status != models.STATUS_PENDING:
        raise bad_request(f"当前状态 {doc.status} 不可审批")
    return doc


# ---------------- 审批中心 ----------------


@router.get("/pending")
def pending_documents(page: int = 1, page_size: int = 20,
                      db: Session = Depends(get_db),
                      current_user: models.User = Depends(require_dept_admin)):
    """待审批列表：admin 全部 / dept_admin 仅本部门。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = _pending_query(db, current_user)
    items, total = schemas.paginate(
        q.order_by(models.Document.created_at.desc()), page, page_size)
    return schemas.ok({
        "total": total, "page": page, "page_size": page_size,
        "items": [schemas.document_to_dict(d) for d in items],
    })


@router.post("/pending/{document_id}/approve")
def approve_document(document_id: int,
                     request: Request,
                     db: Session = Depends(get_db),
                     current_user: models.User = Depends(require_dept_admin)):
    """审批通过：pending → processing → ingest_document（占位）→ approved。"""
    doc = _assert_approvable(db, document_id, current_user)
    doc.status = models.STATUS_PROCESSING
    doc.approver_id = current_user.id
    doc.approved_at = datetime.utcnow()
    db.add(doc)
    db.commit()
    # 触发解析入库管线（本轮为占位：置 approved + 打印提示）
    ingest_document(db, doc)
    log_action(db, current_user, "approve", "document", doc.id,
               {"file_name": doc.file_name, "status": models.STATUS_APPROVED},
               client_ip(request))
    return schemas.ok({"id": doc.id, "status": doc.status})


@router.post("/pending/{document_id}/reject")
def reject_document(document_id: int,
                    body: schemas.RejectRequest,
                    request: Request,
                    db: Session = Depends(get_db),
                    current_user: models.User = Depends(require_dept_admin)):
    """审批拒绝：pending → rejected，附拒绝原因（上传者可见）。"""
    doc = _assert_approvable(db, document_id, current_user)
    doc.status = models.STATUS_REJECTED
    doc.reject_reason = body.reason.strip()
    doc.approver_id = current_user.id
    db.add(doc)
    db.commit()
    log_action(db, current_user, "reject", "document", doc.id,
               {"file_name": doc.file_name, "reason": doc.reject_reason},
               client_ip(request))
    return schemas.ok({"id": doc.id, "status": doc.status})


# ---------------- 管理端直入库上传 ----------------


@router.post("/documents/upload")
def direct_upload(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    department_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(require_dept_admin),
):
    """管理端直入库上传（跳过审批，直接走解析入库管线，本轮占位）。

    - admin：可指定任意部门或公开（department_id 空）
    - dept_admin：仅本部门或公开
    """
    if current_user.role == models.ROLE_DEPT_ADMIN and \
            department_id is not None and department_id != current_user.department_id:
        raise forbidden("部门管理员仅可向本部门或公开直入库")
    if department_id is not None and db.get(models.Department, department_id) is None:
        raise bad_request("部门不存在")

    doc = _build_document(db, file, title, department_id=department_id,
                          source=models.SOURCE_UPLOAD,
                          uploaded_by=current_user.id)
    doc.status = models.STATUS_PROCESSING  # 直入库先置 processing，随后占位入库
    db.add(doc)
    db.commit()
    ingest_document(db, doc)
    log_action(db, current_user, "direct_upload", "document", doc.id,
               {"file_name": doc.file_name, "status": models.STATUS_APPROVED,
                "department_id": department_id}, client_ip(request))
    return schemas.ok(schemas.document_to_dict(doc))


@router.post("/documents/{document_id}/reprocess")
def reprocess_document(document_id: int,
                       request: Request,
                       db: Session = Depends(get_db),
                       current_user: models.User = Depends(require_dept_admin)):
    """重新入库（spec §10.5 / F8）：对 failed / offline 文档重跑解析入库管线。

    - admin 任意文档；dept_admin 仅本部门
    """
    doc = db.get(models.Document, document_id)
    if doc is None:
        raise not_found("文档不存在")
    if current_user.role == models.ROLE_DEPT_ADMIN and \
            doc.department_id != current_user.department_id:
        raise forbidden("无权操作其他部门的文档")
    if doc.status not in (models.STATUS_FAILED, models.STATUS_OFFLINE):
        raise bad_request(f"仅 failed/offline 文档可重新入库，当前状态 {doc.status}")
    doc.status = models.STATUS_PROCESSING
    doc.error_message = None
    db.add(doc)
    db.commit()
    ingest_document(db, doc)
    log_action(db, current_user, "reprocess", "document", doc.id,
               {"file_name": doc.file_name, "status": doc.status}, client_ip(request))
    return schemas.ok({"id": doc.id, "status": doc.status,
                       "error_message": doc.error_message})


@router.post("/documents/{document_id}/regenerate-summary")
def regenerate_summary(document_id: int,
                       request: Request,
                       db: Session = Depends(get_db),
                       current_user: models.User = Depends(require_dept_admin)):
    """重新生成摘要（F17 补充）：对 approved 文档单独重跑 generate_summary，不动入库管线。

    - admin 任意文档；dept_admin 仅本部门
    - LLM 不可用时自动降级为片段截取（generate_summary 内部兜底）
    """
    doc = db.get(models.Document, document_id)
    if doc is None:
        raise not_found("文档不存在")
    if current_user.role == models.ROLE_DEPT_ADMIN and \
            doc.department_id != current_user.department_id:
        raise forbidden("无权操作其他部门的文档")
    if doc.status != models.STATUS_APPROVED or not doc.content_text:
        raise bad_request("仅已入库（approved）且有正文的文档可重新生成摘要")
    from ..summary import generate_summary
    doc.summary = generate_summary(doc)
    db.add(doc)
    db.commit()
    log_action(db, current_user, "regenerate_summary", "document", doc.id,
               {"file_name": doc.file_name, "summary_len": len(doc.summary or "")},
               client_ip(request))
    return schemas.ok({"id": doc.id, "summary": doc.summary})


@router.post("/rebuild-index")
def rebuild_vector_index(request: Request,
                         db: Session = Depends(get_db),
                         current_user: models.User = Depends(require_admin)):
    """重建向量索引（运维）：服务进程内删除并重建 Chroma 集合，对全部 approved
    文档重新入库（上传文档读文件、爬虫文档用 content_text）。

    背景：Windows 上跨进程写入的 Chroma HNSW 状态不可靠（"Error loading hnsw
    index"），故重建必须在服务进程内执行（本接口），完成后服务继续运行即可
    正常检索；请勿重建后立即强杀服务（会中断 flush）。
    """
    from .. import vector_store
    from ..ingest import ingest_document, ingest_text
    vector_store.reset_collection()
    docs = db.query(models.Document).filter(
        models.Document.status == models.STATUS_APPROVED).all()
    ok = fail = 0
    for doc in docs:
        if doc.file_path:
            ingest_document(db, doc, regen_summary=False)
        else:
            ingest_text(db, doc, doc.content_text or "", regen_summary=False)
        db.refresh(doc)
        if doc.status == models.STATUS_APPROVED:
            ok += 1
        else:
            fail += 1
    # 触发 compactor 构建 HNSW 索引（同进程查询预热）
    try:
        vector_store.query([0.0] * settings.embedding_dim, 1,
                           user_department_id=None, is_admin=True)
    except Exception:
        pass
    log_action(db, current_user, "rebuild_index", "system", None,
               {"rebuilt": ok, "failed": fail}, client_ip(request))
    return schemas.ok({"rebuilt": ok, "failed": fail})


class DocumentPatch(BaseModel):
    """文档管理操作（F8）：标记重点 / 下架 / 重新上架 / 改部门。"""
    is_featured: Optional[bool] = None
    status: Optional[str] = None          # offline（下架）或 approved（上架）
    department_id: Optional[int] = None


@router.patch("/documents/{document_id}")
def patch_document(document_id: int,
                   body: DocumentPatch,
                   request: Request,
                   db: Session = Depends(get_db),
                   current_user: models.User = Depends(require_dept_admin)):
    """文档管理（spec F8）：重点标记 / 下架 / 重新上架 / 改部门。

    - admin 任意文档；dept_admin 仅本部门
    - 下架/改部门后可见性即时切换（检索/问答/下载按状态+部门过滤，天然生效）
    """
    doc = db.get(models.Document, document_id)
    if doc is None:
        raise not_found("文档不存在")
    if current_user.role == models.ROLE_DEPT_ADMIN and \
            doc.department_id != current_user.department_id:
        raise forbidden("无权操作其他部门的文档")

    changes = []
    if body.is_featured is not None:
        doc.is_featured = body.is_featured
        changes.append(f"featured={body.is_featured}")
    if body.status is not None:
        if body.status not in (models.STATUS_OFFLINE, models.STATUS_APPROVED):
            raise bad_request("status 仅支持 offline（下架）/ approved（上架）")
        if body.status == models.STATUS_APPROVED and doc.status == models.STATUS_OFFLINE:
            # 上架前确认已有入库内容（曾 approved）
            if not doc.content_text:
                raise bad_request("该文档从未入库成功，请先重新入库（reprocess）")
        doc.status = body.status
        changes.append(f"status={body.status}")
    if body.department_id is not None:
        if db.get(models.Department, body.department_id) is None:
            raise bad_request("部门不存在")
        doc.department_id = body.department_id
        changes.append(f"department_id={body.department_id}")
    db.add(doc)
    db.commit()
    if changes:
        log_action(db, current_user, "patch_document", "document", doc.id,
                   {"file_name": doc.file_name, "changes": changes},
                   client_ip(request))
    return schemas.ok({"id": doc.id, "status": doc.status,
                       "is_featured": doc.is_featured,
                       "department_id": doc.department_id})


# ---------------- 文档管理列表 / 删除 ----------------

DOC_STATUS_FILTERS = list(models.ALL_DOC_STATUS)


@router.get("/documents")
def list_documents(status: Optional[str] = None,
                   department_id: Optional[int] = None,
                   source: Optional[str] = None,
                   file_type: Optional[str] = None,
                   is_featured: Optional[bool] = None,
                   page: int = 1, page_size: int = 20,
                   db: Session = Depends(get_db),
                   current_user: models.User = Depends(require_dept_admin)):
    """文档管理列表（spec §8.2 / F8）：admin 全部 / dept_admin 仅本部门。

    筛选：状态 / 部门 / 来源 / 格式 / 重点；分页 page/page_size（spec §10.1）。
    """
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = db.query(models.Document)
    if current_user.role == models.ROLE_DEPT_ADMIN:
        q = q.filter(models.Document.department_id == current_user.department_id)
    if status:
        if status not in DOC_STATUS_FILTERS:
            raise bad_request(f"非法状态值：{status}")
        q = q.filter(models.Document.status == status)
    if department_id is not None:
        q = q.filter(models.Document.department_id == department_id)
    if source:
        if source not in (models.SOURCE_UPLOAD, models.SOURCE_CRAWL):
            raise bad_request(f"非法来源：{source}")
        q = q.filter(models.Document.source == source)
    if file_type:
        q = q.filter(models.Document.file_type == file_type)
    if is_featured is not None:
        q = q.filter(models.Document.is_featured.is_(is_featured))
    items, total = schemas.paginate(
        q.order_by(models.Document.created_at.desc()), page, page_size)
    return schemas.ok({
        "total": total, "page": page, "page_size": page_size,
        "items": [schemas.document_to_dict(d) for d in items],
    })


@router.delete("/documents/{document_id}")
def delete_document(document_id: int,
                    request: Request,
                    db: Session = Depends(get_db),
                    current_user: models.User = Depends(require_dept_admin)):
    """删除文档（spec §8.2 文档管理）：admin 任意 / dept_admin 仅本部门。

    删除私有文件 + 分片（ChunkParent）+ 记录，写审计日志。
    """
    doc = db.get(models.Document, document_id)
    if doc is None:
        raise not_found("文档不存在")
    if current_user.role == models.ROLE_DEPT_ADMIN and \
            doc.department_id != current_user.department_id:
        raise forbidden("无权操作其他部门的文档")
    delete_stored_file(doc)
    db.query(models.ChunkParent).filter(
        models.ChunkParent.document_id == doc.id).delete(synchronize_session=False)
    log_action(db, current_user, "document_delete", "document", doc.id,
               {"file_name": doc.file_name, "title": doc.title}, client_ip(request))
    db.delete(doc)
    db.commit()
    return schemas.ok({"id": document_id, "deleted": True})


# ---------------- 用户管理（admin 专属） ----------------


@router.get("/users")
def list_users(page: int = 1, page_size: int = 20,
               db: Session = Depends(get_db),
               current_user: models.User = Depends(require_admin)):
    """用户列表（分页）。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = db.query(models.User)
    items, total = schemas.paginate(q.order_by(models.User.id), page, page_size)
    return schemas.ok({
        "total": total, "page": page, "page_size": page_size,
        "items": [schemas.user_to_dict(u) for u in items],
    })


@router.post("/users")
def create_user(body: schemas.UserCreate,
                request: Request,
                db: Session = Depends(get_db),
                current_user: models.User = Depends(require_admin)):
    """创建账号（仅管理员）。"""
    if db.query(models.User).filter(models.User.username == body.username).first():
        raise conflict("用户名已存在")
    if body.department_id is not None and db.get(models.Department, body.department_id) is None:
        raise bad_request("部门不存在")
    user = models.User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
        department_id=body.department_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_action(db, current_user, "user_create", "user", user.id,
               {"username": user.username, "role": user.role,
                "department_id": user.department_id}, client_ip(request))
    return schemas.ok(schemas.user_to_dict(user))


@router.patch("/users/{user_id}")
def update_user(user_id: int,
                body: schemas.UserUpdate,
                request: Request,
                db: Session = Depends(get_db),
                current_user: models.User = Depends(require_admin)):
    """修改用户角色 / 部门 / 重置密码。"""
    user = db.get(models.User, user_id)
    if user is None:
        raise not_found("用户不存在")

    changes = {}
    if body.role is not None and body.role != user.role:
        user.role = body.role
        changes["role"] = body.role
    # 显式传 null 表示清空部门（转为公开/无部门）
    if "department_id" in body.model_fields_set and body.department_id != user.department_id:
        if body.department_id is not None and db.get(models.Department, body.department_id) is None:
            raise bad_request("部门不存在")
        user.department_id = body.department_id
        changes["department_id"] = body.department_id
    if body.password:
        user.password_hash = hash_password(body.password)
        changes["password"] = "重置"
    if not changes:
        raise bad_request("没有需要修改的字段")

    db.add(user)
    db.commit()
    db.refresh(user)
    log_action(db, current_user, "user_update", "user", user.id,
               {"username": user.username, "changes": changes}, client_ip(request))
    return schemas.ok(schemas.user_to_dict(user))


@router.delete("/users/{user_id}")
def delete_user(user_id: int,
                request: Request,
                db: Session = Depends(get_db),
                current_user: models.User = Depends(require_admin)):
    """删除用户（spec §10.4）：admin 自身与内置 admin 除外。

    级联处理其上传文档（删除文件+分片+记录）、收藏夹/收藏，并将其审批过的
    文档 approver 解绑；写审计日志。
    """
    user = db.get(models.User, user_id)
    if user is None:
        raise not_found("用户不存在")
    if user.id == current_user.id:
        raise bad_request("不能删除当前登录的管理员账号")
    if user.id == 1:
        raise bad_request("内置管理员账号不可删除")

    # 级联：收藏夹与收藏
    folder_ids = [f.id for f in db.query(models.FavoriteFolder).filter(
        models.FavoriteFolder.user_id == user_id).all()]
    if folder_ids:
        db.query(models.Favorite).filter(
            models.Favorite.folder_id.in_(folder_ids)).delete(synchronize_session=False)
    db.query(models.Favorite).filter(
        models.Favorite.user_id == user_id).delete(synchronize_session=False)
    db.query(models.FavoriteFolder).filter(
        models.FavoriteFolder.user_id == user_id).delete(synchronize_session=False)

    # 级联：其上传的文档（删除私有文件 + 分片 + 记录）
    docs = db.query(models.Document).filter(
        models.Document.uploaded_by == user_id).all()
    for doc in docs:
        delete_stored_file(doc)
        db.query(models.ChunkParent).filter(
            models.ChunkParent.document_id == doc.id).delete(synchronize_session=False)
        db.delete(doc)

    # 其审批过的文档解绑审批人
    db.query(models.Document).filter(models.Document.approver_id == user_id).update(
        {models.Document.approver_id: None}, synchronize_session=False)

    log_action(db, current_user, "user_delete", "user", user.id,
               {"username": user.username}, client_ip(request))
    db.delete(user)
    db.commit()
    return schemas.ok({"id": user_id, "deleted": True})


# ---------------- 部门管理（admin 专属，修复#1） ----------------
class DepartmentIn(BaseModel):
    """部门创建/重命名。"""
    name: str = Field(..., min_length=1, max_length=64)


@router.get("/departments")
def list_departments_admin(db: Session = Depends(get_db),
                           current_user: models.User = Depends(require_admin)):
    """部门列表（管理端）：含 用户数/文档数/爬虫任务数 统计。"""
    depts = db.query(models.Department).order_by(models.Department.id).all()
    items = []
    for d in depts:
        items.append({
            "id": d.id,
            "name": d.name,
            "user_count": db.query(models.User).filter(
                models.User.department_id == d.id).count(),
            "doc_count": db.query(models.Document).filter(
                models.Document.department_id == d.id).count(),
            "crawl_task_count": db.query(models.CrawlTask).filter(
                models.CrawlTask.target_department_id == d.id).count(),
        })
    return schemas.ok({"items": items})


@router.post("/departments")
def create_department(body: DepartmentIn,
                      request: Request,
                      db: Session = Depends(get_db),
                      current_user: models.User = Depends(require_admin)):
    """创建部门（spec §2.2 角色；管理端可维护部门）。"""
    name = body.name.strip()
    if db.query(models.Department).filter(models.Department.name == name).first():
        raise conflict("部门已存在")
    d = models.Department(name=name)
    db.add(d)
    db.commit()
    db.refresh(d)
    log_action(db, current_user, "department_create", "department", d.id,
               {"name": d.name}, client_ip(request))
    return schemas.ok({"id": d.id, "name": d.name})


@router.patch("/departments/{department_id}")
def rename_department(department_id: int,
                      body: DepartmentIn,
                      request: Request,
                      db: Session = Depends(get_db),
                      current_user: models.User = Depends(require_admin)):
    """重命名部门。"""
    d = db.get(models.Department, department_id)
    if d is None:
        raise not_found("部门不存在")
    name = body.name.strip()
    if db.query(models.Department).filter(models.Department.name == name,
                                          models.Department.id != department_id).first():
        raise conflict("部门已存在")
    old = d.name
    d.name = name
    db.commit()
    log_action(db, current_user, "department_rename", "department", d.id,
               {"old": old, "new": name}, client_ip(request))
    return schemas.ok({"id": d.id, "name": d.name})


@router.delete("/departments/{department_id}")
def delete_department(department_id: int,
                      request: Request,
                      db: Session = Depends(get_db),
                      current_user: models.User = Depends(require_admin)):
    """删除部门：存在 用户/文档/爬虫任务 引用时阻止（防级联破坏）。"""
    d = db.get(models.Department, department_id)
    if d is None:
        raise not_found("部门不存在")
    refs = []
    if db.query(models.User).filter(models.User.department_id == department_id).first():
        refs.append("用户")
    if db.query(models.Document).filter(models.Document.department_id == department_id).first():
        refs.append("文档")
    if db.query(models.CrawlTask).filter(
            models.CrawlTask.target_department_id == department_id).first():
        refs.append("爬虫任务")
    if refs:
        raise conflict("该部门下存在" + "、".join(refs) + "，无法删除")
    db.delete(d)
    db.commit()
    log_action(db, current_user, "department_delete", "department", department_id,
               {"name": d.name}, client_ip(request))
    return schemas.ok({"deleted": department_id})


# ---------------- 审计日志（admin 专属） ----------------


@router.get("/audit-logs")
def audit_logs(action: Optional[str] = None,
               target_type: Optional[str] = None,
               user_id: Optional[int] = None,
               created_from: Optional[datetime] = None,
               created_to: Optional[datetime] = None,
               page: int = 1, page_size: int = 20,
               db: Session = Depends(get_db),
               current_user: models.User = Depends(require_admin)):
    """审计日志查询：分页 + 筛选（动作 / 对象类型 / 操作人 / 时间范围）。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = db.query(models.AuditLog)
    if action:
        q = q.filter(models.AuditLog.action == action)
    if target_type:
        q = q.filter(models.AuditLog.target_type == target_type)
    if user_id is not None:
        q = q.filter(models.AuditLog.user_id == user_id)
    if created_from is not None:
        q = q.filter(models.AuditLog.created_at >= created_from)
    if created_to is not None:
        q = q.filter(models.AuditLog.created_at <= created_to)
    items, total = schemas.paginate(q.order_by(models.AuditLog.created_at.desc()),
                                    page, page_size)
    return schemas.ok({
        "total": total, "page": page, "page_size": page_size,
        "items": [schemas.audit_log_to_dict(l) for l in items],
    })


# ---------------- 部门定向推送（spec F10） ----------------


class PushCreate(BaseModel):
    """部门推送请求：标题必填；内容/关联文档/目标部门可选（空=全员）。"""
    title: str = Field(..., min_length=1, max_length=128)
    content: Optional[str] = ""
    document_id: Optional[int] = None
    department_id: Optional[int] = None


@router.post("/push")
def create_push(body: PushCreate,
                request: Request,
                db: Session = Depends(get_db),
                current_user: models.User = Depends(require_dept_admin)):
    """创建推送（spec F10 / §6.5 推送流）。

    - admin：任意部门或空（全员）
    - dept_admin：department_id 必须等于其部门或空（全员）
    创建写审计日志。
    """
    if current_user.role == models.ROLE_DEPT_ADMIN and \
            body.department_id not in (None, current_user.department_id):
        raise forbidden("部门管理员仅可向本部门或全员推送")
    if body.department_id is not None and db.get(models.Department, body.department_id) is None:
        raise bad_request("部门不存在")
    if body.document_id is not None and db.get(models.Document, body.document_id) is None:
        raise bad_request("关联文档不存在")

    n = models.PushNotification(
        title=body.title.strip(),
        content=body.content or "",
        document_id=body.document_id,
        department_id=body.department_id,
        created_by=current_user.id,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    log_action(db, current_user, "push_create", "push_notification", n.id,
               {"title": n.title, "department_id": n.department_id,
                "document_id": n.document_id}, client_ip(request))
    return schemas.ok({
        "id": n.id, "title": n.title, "content": n.content,
        "document_id": n.document_id, "department_id": n.department_id,
        "created_by": n.created_by, "created_at": n.created_at,
    })


# ---------------- 工作台统计（spec F13） ----------------


@router.get("/stats")
def stats(db: Session = Depends(get_db),
          current_user: models.User = Depends(require_dept_admin)):
    """工作台统计：admin 全量 / dept_admin 仅本部门（spec F13）。

    返回文档总数（按状态分组）、待审批数、爬虫任务数（enabled/disabled）、
    部门数、用户数、近 7 日操作趋势（AuditLog 按日期分组计数）。
    """
    dept_id = None
    if current_user.role == models.ROLE_DEPT_ADMIN:
        dept_id = current_user.department_id

    # 文档按状态分组（本部门过滤）
    dq = db.query(models.Document)
    if dept_id is not None:
        dq = dq.filter(models.Document.department_id == dept_id)
    doc_rows = dq.with_entities(models.Document.status,
                                func.count(models.Document.id)).group_by(
        models.Document.status).all()
    doc_by_status = {s: c for s, c in doc_rows}
    doc_total = sum(doc_by_status.values())

    # 爬虫任务（dept_admin 仅统计 target_department_id=本部门的任务）
    cq = db.query(models.CrawlTask)
    if dept_id is not None:
        cq = cq.filter(models.CrawlTask.target_department_id == dept_id)
    crawl_enabled = cq.filter(models.CrawlTask.enabled.is_(True)).count()
    crawl_disabled = cq.filter(models.CrawlTask.enabled.is_(False)).count()

    # 部门 / 用户数
    if dept_id is not None:
        dept_count = 1  # 本部门视图
        user_count = db.query(models.User).filter(
            models.User.department_id == dept_id).count()
    else:
        dept_count = db.query(models.Department).count()
        user_count = db.query(models.User).count()

    # 近 7 日操作趋势（AuditLog 按日期分组；dept_admin 仅统计本部门操作人的操作）
    since = datetime.utcnow() - timedelta(days=7)
    aq = db.query(func.date(models.AuditLog.created_at).label("day"),
                  func.count(models.AuditLog.id))
    aq = aq.filter(models.AuditLog.created_at >= since)
    if dept_id is not None:
        aq = aq.join(models.User, models.AuditLog.user_id == models.User.id).filter(
            models.User.department_id == dept_id)
    trend_rows = aq.group_by("day").order_by("day").all()
    trend = [{"date": str(day), "count": cnt} for day, cnt in trend_rows]

    return schemas.ok({
        "document_total": doc_total,
        "document_by_status": doc_by_status,
        "pending_count": doc_by_status.get(models.STATUS_PENDING, 0),
        "crawl_task_count": {"enabled": crawl_enabled, "disabled": crawl_disabled},
        "department_count": dept_count,
        "user_count": user_count,
        "trend_7d": trend,
    })
