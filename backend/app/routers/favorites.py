# -*- coding: utf-8 -*-
"""收藏夹与收藏路由（spec F5 / §10.2）：创建/重命名/删除收藏夹，收藏/取消/列表。

仅可收藏当前用户可见（approved + 部门可见）文档；不可见 403。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..deps import get_current_user
from ..errors import bad_request, conflict, forbidden, not_found
from ..visibility import dept_visible

router = APIRouter(prefix="/favorites", tags=["favorites"])


class FolderIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class FavoriteIn(BaseModel):
    document_id: int
    folder_id: int | None = None


def _assert_visible_doc(db: Session, user: models.User, document_id: int) -> models.Document:
    """收藏目标校验：approved 且当前用户可见。"""
    doc = db.get(models.Document, document_id)
    if doc is None or doc.status != models.STATUS_APPROVED or not dept_visible(user, doc):
        raise forbidden("仅可收藏可见文档")
    return doc


@router.get("/folders")
def list_folders(db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    folders = db.query(models.FavoriteFolder).filter(
        models.FavoriteFolder.user_id == user.id).all()
    result = []
    for f in folders:
        cnt = db.query(models.Favorite).filter(
            models.Favorite.folder_id == f.id).count()
        result.append({"id": f.id, "name": f.name, "count": cnt,
                       "created_at": f.created_at.isoformat() if f.created_at else None})
    return schemas.ok({"items": result})


@router.post("/folders")
def create_folder(body: FolderIn, db: Session = Depends(get_db),
                  user: models.User = Depends(get_current_user)):
    f = models.FavoriteFolder(user_id=user.id, name=body.name.strip())
    db.add(f)
    db.commit()
    db.refresh(f)
    return schemas.ok({"id": f.id, "name": f.name, "count": 0})


@router.patch("/folders/{folder_id}")
def rename_folder(folder_id: int, body: FolderIn, db: Session = Depends(get_db),
                  user: models.User = Depends(get_current_user)):
    f = _own_folder(db, user, folder_id)
    f.name = body.name.strip()
    db.commit()
    return schemas.ok({"id": f.id, "name": f.name})


@router.delete("/folders/{folder_id}")
def delete_folder(folder_id: int, db: Session = Depends(get_db),
                  user: models.User = Depends(get_current_user)):
    f = _own_folder(db, user, folder_id)
    db.query(models.Favorite).filter(models.Favorite.folder_id == f.id).delete()
    db.delete(f)
    db.commit()
    return schemas.ok({"deleted": folder_id})


@router.get("")
def list_favorites(db: Session = Depends(get_db),
                   user: models.User = Depends(get_current_user)):
    favs = (db.query(models.Favorite)
            .filter(models.Favorite.user_id == user.id)
            .order_by(models.Favorite.created_at.desc())
            .all())
    ids = [f.document_id for f in favs if f.document_id is not None]
    docs = {}
    if ids:
        docs = {d.id: d for d in db.query(models.Document).filter(
            models.Document.id.in_(ids)).all()}
    from ..document_departments import attach_department_sets
    attach_department_sets(db, list(docs.values()))
    items = []
    for f in favs:
        doc = docs.get(f.document_id)
        if doc is None:
            # 文档已删除：条目标记"已失效"（spec F5），不报错
            items.append({"id": f.id, "folder_id": f.folder_id,
                          "document_id": f.document_id,
                          "is_valid": False, "document": None})
            continue
        # F1-C1：文档被改到本用户不可见集合/下架后，不泄露、不报错，按无权限处理
        valid = (doc.status == models.STATUS_APPROVED and dept_visible(user, doc))
        items.append({"id": f.id, "folder_id": f.folder_id,
                      "document_id": f.document_id,
                      "is_valid": valid,
                      "document": schemas.document_to_dict(doc) if valid else None})
    return schemas.ok({"items": items})


@router.post("")
def add_favorite(body: FavoriteIn, db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    _assert_visible_doc(db, user, body.document_id)
    if body.folder_id is not None:
        _own_folder(db, user, body.folder_id)
    exists = db.query(models.Favorite).filter(
        models.Favorite.user_id == user.id,
        models.Favorite.document_id == body.document_id).first()
    if exists is not None:
        raise conflict("该文档已在收藏中", detail={"favorite_id": exists.id})
    fav = models.Favorite(user_id=user.id, folder_id=body.folder_id,
                          document_id=body.document_id)
    db.add(fav)
    db.commit()
    db.refresh(fav)
    return schemas.ok({"id": fav.id, "folder_id": fav.folder_id,
                       "document_id": fav.document_id})


@router.delete("/{document_id}")
def remove_favorite(document_id: int, db: Session = Depends(get_db),
                    user: models.User = Depends(get_current_user)):
    fav = db.query(models.Favorite).filter(
        models.Favorite.user_id == user.id,
        models.Favorite.document_id == document_id).first()
    if fav is None:
        raise not_found("收藏不存在")
    db.delete(fav)
    db.commit()
    return schemas.ok({"removed": document_id})


def _own_folder(db: Session, user: models.User, folder_id: int) -> models.FavoriteFolder:
    f = db.get(models.FavoriteFolder, folder_id)
    if f is None or f.user_id != user.id:
        raise not_found("收藏夹不存在")
    return f
