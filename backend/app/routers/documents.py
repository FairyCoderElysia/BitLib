# -*- coding: utf-8 -*-
"""用户端文档路由（M2）：上传 → pending / 我的上传 / 撤回。

上传流程（spec §6.1）：格式白名单 + 大小上限 → sha256 去重 → pending
（文件私有存储，不解析不向量化）；department_id = 上传者部门（无部门=公开）。
"""
import hashlib
import logging
import uuid
from pathlib import Path
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import models, schemas, vector_store
from ..audit import client_ip, log_action
from ..config import settings
from ..db import get_db
from ..deps import get_current_user
from ..errors import bad_request, conflict, not_found, forbidden
from ..summary import get_display_summary
from ..visibility import can_access, can_preview, dept_visible

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

# 上传格式白名单（F2）
ALLOWED_FILE_TYPES = {"txt", "docx", "pdf", "md"}


def file_ext(file_name: str) -> str:
    """提取扩展名（小写，不含点）。"""
    return Path(file_name).suffix.lstrip(".").lower()


def save_upload(upload: UploadFile) -> Tuple[str, str, int, str]:
    """校验格式/大小并写入私有 upload_dir，返回 (ext, 存储名, 字节数, sha256)。

    超限或异常时清理已写文件并抛 BizError。
    """
    file_name = upload.filename or "未命名"
    ext = file_ext(file_name)
    if ext not in ALLOWED_FILE_TYPES:
        raise bad_request(f"不支持的格式：.{ext}，仅支持 txt/docx/pdf/md")

    target_dir = Path(settings.upload_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    store_name = f"{uuid.uuid4().hex}.{ext}"
    target = target_dir / store_name

    max_bytes = settings.max_upload_mb * 1024 * 1024
    sha = hashlib.sha256()
    size = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise bad_request(f"文件超过大小上限 {settings.max_upload_mb}MB")
                sha.update(chunk)
                out.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return ext, store_name, size, sha.hexdigest()


def delete_stored_file(doc) -> None:
    """删除文档对应的私有文件（存在则删）。"""
    target = Path(settings.upload_dir) / doc.file_path
    target.unlink(missing_ok=True)


def find_duplicate(db: Session, sha256_hex: str) -> Optional[models.Document]:
    """sha256 文件级去重：库内已存在相同 hash 的文档。"""
    return db.query(models.Document).filter(
        models.Document.file_hash == sha256_hex
    ).first()


def _build_document(db: Session, upload: UploadFile, title: str,
                    department_id: Optional[int], source: str,
                    uploaded_by: int) -> models.Document:
    """公共上传逻辑：落盘 + 去重 + 建 pending/processing 记录。"""
    ext, store_name, size, sha256_hex = save_upload(upload)

    dup = find_duplicate(db, sha256_hex)
    if dup is not None:
        Path(settings.upload_dir, store_name).unlink(missing_ok=True)
        raise conflict("该文件已存在", detail={"document_id": dup.id,
                                              "hint": "可改用直入库/更新为新版本（F8）"})

    file_name = upload.filename or store_name
    doc_title = (title or "").strip() or Path(file_name).stem
    doc = models.Document(
        title=doc_title,
        file_name=file_name,
        file_path=store_name,
        file_type=ext,
        file_size=size,
        file_hash=sha256_hex,
        status=models.STATUS_PENDING,
        department_id=department_id,
        source=source,
        uploaded_by=uploaded_by,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


@router.post("/upload")
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """普通用户上传：→ pending，归属上传者部门（无部门=公开）。"""
    doc = _build_document(db, file, title,
                          department_id=current_user.department_id,
                          source=models.SOURCE_UPLOAD,
                          uploaded_by=current_user.id)
    log_action(db, current_user, "upload", "document", doc.id,
               {"file_name": doc.file_name, "file_size": doc.file_size,
                "status": models.STATUS_PENDING}, client_ip(request))
    return schemas.ok(schemas.document_to_dict(doc))


@router.get("/mine")
def my_documents(page: int = 1, page_size: int = 20,
                 db: Session = Depends(get_db),
                 current_user: models.User = Depends(get_current_user)):
    """我的上传记录（含状态/拒绝原因/撤回）。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = db.query(models.Document).filter(models.Document.uploaded_by == current_user.id)
    items, total = schemas.paginate(
        q.order_by(models.Document.created_at.desc()), page, page_size)
    return schemas.ok({
        "total": total, "page": page, "page_size": page_size,
        "items": [schemas.document_to_dict(d) for d in items],
    })


@router.delete("/{document_id}")
def withdraw_document(document_id: int,
                      request: Request,
                      db: Session = Depends(get_db),
                      current_user: models.User = Depends(get_current_user)):
    """撤回：仅限本人 pending 文档（删除文件 + 记录）。他人 → 403。"""
    doc = db.get(models.Document, document_id)
    if doc is None:
        raise not_found("文档不存在")
    if doc.uploaded_by != current_user.id:
        raise forbidden("只能撤回自己上传的文档")
    if doc.status != models.STATUS_PENDING:
        raise bad_request(f"仅 pending 状态文档可撤回，当前状态：{doc.status}")
    delete_stored_file(doc)
    log_action(db, current_user, "withdraw", "document", doc.id,
               {"file_name": doc.file_name, "status": models.STATUS_PENDING},
               client_ip(request))
    db.delete(doc)
    db.commit()
    return schemas.ok({"id": document_id, "status": "withdrawn"})


@router.get("/{document_id}")
def document_detail(document_id: int,
                    db: Session = Depends(get_db),
                    current_user: models.User = Depends(get_current_user)):
    """文档详情（spec §2.4 状态-可见性矩阵）：approved 可见者取全文；非 approved 仅上传者/审批者。"""
    doc = db.get(models.Document, document_id)
    if doc is None or not can_access(current_user, doc):
        raise not_found("文档不存在或无权访问")
    data = schemas.document_to_dict(doc)
    if doc.status == models.STATUS_APPROVED:
        data["content_text"] = doc.content_text
    return schemas.ok(data)


@router.get("/{document_id}/related")
def related_documents(document_id: int,
                      db: Session = Depends(get_db),
                      current_user: models.User = Depends(get_current_user)):
    """相似文档推荐（spec §3 F18）：文档级向量最近邻 + 双层权限过滤。

    - 权限口径与详情一致：不可访问 → 404（不泄露存在性）。
    - 仅 approved 有推荐；无 child 向量 / 非 approved → 空数组。
    - 召回阶段复用 vector_store.query()（status=approved + 公开/本部门），
      DB 阶段再以 approved + dept_visible 兜底（防脏数据，口径同 F3）。
    - distance 为 cosine 距离（越小越相似），供前端排序/调试。
    """
    doc = db.get(models.Document, document_id)
    if doc is None or not can_access(current_user, doc):
        raise not_found("文档不存在或无权访问")
    if doc.status != models.STATUS_APPROVED:
        return schemas.ok([])
    vec = vector_store.get_document_vector(document_id)
    if vec is None:
        return schemas.ok([])
    cands = vector_store.query_similar_documents(
        vec, exclude_document_id=document_id, top_k=5,
        user_department_id=current_user.department_id,
        is_admin=current_user.role == models.ROLE_ADMIN)
    ids = [c["document_id"] for c in cands]
    docs = {}
    if ids:
        docs = {d.id: d for d in db.query(models.Document).filter(
            models.Document.id.in_(ids)).all()}
    items = []
    for c in cands:
        d = docs.get(c["document_id"])
        if d is None or d.status != models.STATUS_APPROVED or not dept_visible(current_user, d):
            continue
        items.append({
            "id": d.id,
            "title": d.title,
            "file_type": d.file_type,
            "summary": get_display_summary(d),
            "source": d.source,
            "department_name": d.department.name if d.department else None,
            "distance": c["distance"],
        })
    return schemas.ok(items)


@router.get("/{document_id}/preview")
def preview_document(document_id: int,
                     db: Session = Depends(get_db),
                     current_user: models.User = Depends(get_current_user)):
    """原文在线预览（spec F4）：返回原文件流 inline。offline 全角色不可访问。"""
    doc = db.get(models.Document, document_id)
    if doc is None or not can_preview(current_user, doc):
        raise not_found("文档不存在或无权访问")
    target = Path(settings.upload_dir) / doc.file_path
    if not target.exists():
        raise not_found("文件已丢失")
    return FileResponse(str(target), filename=doc.file_name,
                        media_type=_media_type(doc.file_type))


@router.get("/{document_id}/download")
def download_document(document_id: int,
                      request: Request,
                      db: Session = Depends(get_db),
                      current_user: models.User = Depends(get_current_user)):
    """下载（spec F4）：仅 approved 且部门可见；记录审计。"""
    doc = db.get(models.Document, document_id)
    if doc is None or doc.status != models.STATUS_APPROVED or not dept_visible(current_user, doc):
        raise not_found("文档不存在或无权访问")
    target = Path(settings.upload_dir) / doc.file_path
    if not target.exists():
        raise not_found("文件已丢失")
    log_action(db, current_user, "download", "document", doc.id,
               {"file_name": doc.file_name}, client_ip(request))
    return FileResponse(str(target), filename=doc.file_name,
                        media_type=_media_type(doc.file_type))


def _media_type(ext: str) -> str:
    return {
        "txt": "text/plain; charset=utf-8",
        "md": "text/markdown; charset=utf-8",
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(ext, "application/octet-stream")
