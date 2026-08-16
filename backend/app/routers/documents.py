# -*- coding: utf-8 -*-
"""用户端文档路由（M2）：上传 → pending / 我的上传 / 撤回。

上传流程（spec §6.1）：格式白名单 + 大小上限 → sha256 去重 → pending
（文件私有存储，不解析不向量化）；department_id = 上传者部门（无部门=公开）。
"""
import hashlib
import logging
import uuid
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Any, List, Optional, Tuple
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import document_update, models, schemas, vector_store
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

# 单次批量下载上限（spec §3 F19 / §10.8）
BATCH_DOWNLOAD_MAX = 50


class BatchDownloadRequest(BaseModel):
    """批量下载请求体：document_ids 不启用 Pydantic 强类型校验（S11 优化 3），
    非数组/非整数统一由路由内手动校验返回 400（替代 FastAPI 默认 422）。"""
    document_ids: Any = Field(default_factory=list)


def _unique_arcname(used: set, file_name: str) -> str:
    """zip 内 arcname 唯一：重名追加 " (2)"、" (3)"…（"报告.pdf" → "报告 (2).pdf"）。

    Evaluator eval-10：arcname 先做 basename 清洗防 zip slip（file_name 含 ../ 时截取末段）。
    """
    base = Path(file_name).name  # 防 zip slip：剥离任何路径成分
    if base not in used:
        used.add(base)
        return base
    stem = Path(base).stem
    suffix = Path(base).suffix
    i = 2
    while True:
        cand = f"{stem} ({i}){suffix}"
        if cand not in used:
            used.add(cand)
            return cand
        i += 1


def file_ext(file_name: str) -> str:
    """提取扩展名（小写，不含点）。"""
    return Path(file_name).suffix.lstrip(".").lower()


def _manifest_title(doc) -> str:
    """manifest 用标题：制表符/换行替换为空格，防错行。"""
    return "".join(" " if ch in "\t\r\n" else ch for ch in (doc.title or ""))


def _manifest_source(doc) -> str:
    """manifest 来源列：优先 source_url（爬虫），否则 source（upload 等）。"""
    return doc.source_url or doc.source or ""


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


def _prepare_upload(db: Session, upload: UploadFile, title: str,
                    department_id: Optional[int], source: str,
                    uploaded_by: int,
                    update_if_duplicate: bool = False,
                    current_user: Optional[models.User] = None,
                    upload_source_label: str = "user_upload",
                    request: Optional[Request] = None) -> Tuple[models.Document, bool]:
    """公共上传逻辑：落盘 + sha256 去重 + 建 pending 记录或更新为新版本。

    返回 (doc, is_update)：
    - is_update=False：新建文档记录（pending），由调用方继续处理与审计。
    - is_update=True：已通过 document_update 更新为新版本（已入库、已审计），调用方勿重复审计。
    """
    ext, store_name, size, sha256_hex = save_upload(upload)

    dup = find_duplicate(db, sha256_hex)
    if dup is not None:
        if not update_if_duplicate:
            Path(settings.upload_dir, store_name).unlink(missing_ok=True)
            raise conflict("该文件已存在", detail={
                "document_id": dup.id,
                "title": dup.title,
                "status": dup.status,
                "can_update": document_update.can_update_document(current_user, dup)
                if current_user else False,
                "hint": "如具备更新权限，可带 update_if_duplicate=true 更新为新版本",
            })
        # 注意：更新通道保留新落盘文件；成功时 ingest_document_update 会把 doc.file_path
        # 切到新文件并删除旧文件，失败时由它清理新文件；权限/状态校验失败（403/400）此处兜底删除新文件。
        # D1：只应在“文档记录尚未切换到新文件”的失败路径清理新文件；一旦
        # ingest_document_update 已成功提交（dup.file_path == store_name），
        # 后续审计失败/旧文件删除失败等不得删除已生效的新文件。
        try:
            doc = document_update.update_document_from_upload(
                db, current_user, dup, store_name, size, sha256_hex,
                source_label=upload_source_label, request=request)
        except Exception:
            if dup.file_path != store_name:
                Path(settings.upload_dir, store_name).unlink(missing_ok=True)
            raise
        return doc, True

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
    return doc, False


@router.post("/upload")
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    title: str = Form(""),
    update_if_duplicate: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """普通用户上传：→ pending，归属上传者部门（无部门=公开）。

    重复文件且 update_if_duplicate=true 且有权限时走更新为新版本通道（F2/F8 修复）。
    """
    doc, is_update = _prepare_upload(
        db, file, title,
        department_id=current_user.department_id,
        source=models.SOURCE_UPLOAD,
        uploaded_by=current_user.id,
        update_if_duplicate=update_if_duplicate,
        current_user=current_user,
        upload_source_label="user_upload",
        request=request)
    if not is_update:
        log_action(db, current_user, "upload", "document", doc.id,
                   {"file_name": doc.file_name, "file_size": doc.file_size,
                    "status": models.STATUS_PENDING}, client_ip(request))
    return schemas.ok(schemas.document_to_dict(doc))


@router.post("/batch-download")
def batch_download_documents(
    request: Request,
    body: BatchDownloadRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """批量下载（spec §3 F19）：多选文档打包 zip 流式返回。

    - 校验：document_ids 空 → 400；原始长度 >50 → 400（先于去重，防重复填充绕过）。
    - 剔除：非 approved / 部门不可见 / 物理文件丢失 → 计入 skipped（不审计）。
    - 全部剔除 → 400（避免空 zip）；部分剔除 → 200 + 响应头 X-Skipped-Count。
    - 每个实际打包的文档写 download 审计（detail.batch=True）。
    - zip 写入 SpooledTemporaryFile（>8MB 自动落盘），StreamingResponse 分块（64KB）输出。
    """
    # S11 优化 3：非数组 / 非整数 → 统一 400（替代 FastAPI 默认 422）
    if not isinstance(body.document_ids, list):
        raise bad_request("document_ids 必须为数组")
    if any(type(x) is not int for x in body.document_ids):
        raise bad_request("document_ids 必须为整数数组")
    ids_raw = body.document_ids
    if not ids_raw:
        raise bad_request("document_ids 不能为空")
    if len(ids_raw) > BATCH_DOWNLOAD_MAX:
        raise bad_request(f"单次最多下载 {BATCH_DOWNLOAD_MAX} 个文档")
    ids = list(dict.fromkeys(ids_raw))  # 去重保序（防御重复提交）

    docs = db.query(models.Document).filter(models.Document.id.in_(ids)).all()
    docs_by_id = {d.id: d for d in docs}

    packable = []
    skipped = 0
    for doc_id in ids:
        doc = docs_by_id.get(doc_id)
        if doc is None:
            skipped += 1
            continue
        if doc.status != models.STATUS_APPROVED or not dept_visible(current_user, doc):
            skipped += 1
            continue
        if not (Path(settings.upload_dir) / doc.file_path).exists():
            skipped += 1
            continue
        packable.append(doc)

    if not packable:
        raise bad_request("所选文档均不可下载或不存在")

    # 审计：对每个实际打包的文档先写 download 记录（batch 标记），与单文件下载同口径
    ip = client_ip(request)
    for doc in packable:
        log_action(db, current_user, "download", "document", doc.id,
                   {"file_name": doc.file_name, "batch": True}, ip)

    # zip 打包：SpooledTemporaryFile 超 8MB 自动落盘，避免全量进内存
    spool = SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    used_names = {"manifest.txt"}  # 预占清单名，防用户文件恰名 manifest.txt 冲突（S11 优化 5）
    manifest_lines = ["id\ttitle\tsource"]
    with ZipFile(spool, "w", ZIP_DEFLATED) as zf:
        for doc in packable:
            target = Path(settings.upload_dir) / doc.file_path
            zf.write(str(target), arcname=_unique_arcname(used_names, doc.file_name))
            manifest_lines.append(
                f"{doc.id}\t{_manifest_title(doc)}\t{_manifest_source(doc)}")
        # 清单作为普通 zip 条目最后写入（条目数 = 文档数 + 1）
        zf.writestr("manifest.txt", "\n".join(manifest_lines) + "\n")
    spool.seek(0)

    def iter_spool():
        try:
            while True:
                chunk = spool.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            spool.close()

    return StreamingResponse(
        iter_spool(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="documents-batch.zip"',
            "X-Skipped-Count": str(skipped),
        },
    )


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
    vec = vector_store.get_document_vector(document_id, doc.updated_at)
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
