# -*- coding: utf-8 -*-
"""AI 问答路由（spec F6 / §10.7）：POST /api/qa/ask。

RAG：混合检索（召回阶段权限过滤）→ RRF → 重排 → small-to-big 回溯 parent → 兜底权限过滤
→ 组装 prompt → LLM → 回答 + 引用来源。
"""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import llm as llm_mod
from .. import models, schemas
from ..db import get_db
from ..deps import get_current_user
from ..errors import bad_request
from ..search_service import hybrid_search

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/qa", tags=["qa"])

SYSTEM_PROMPT = (
    "你是企业资料库智能助手。请仅根据提供的资料回答用户问题；"
    "如果资料中没有相关内容，明确回答\"未在资料库中找到相关内容\"，不要编造。"
    "回答使用简体中文，简洁准确。"
)


@router.post("/ask")
def ask_question(body: schemas.QARequest,
                 db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    """RAG 问答。返回回答 + 引用来源（document_id/title/snippet/parent_id）。"""
    question = (body.question or "").strip()
    if not question:
        raise bad_request("问题不能为空")

    # 1. 混合检索（召回阶段权限过滤已内置），多取候选供回溯
    hits = hybrid_search(db, user, question, top_k=20, limit=8)
    if not hits:
        return schemas.ok({
            "session_id": None,
            "answer": "未在资料库中找到相关内容",
            "citations": [],
            "confidence": 0.0,
        })

    # 2. small-to-big：回溯 parent 块（每文档最多 2 块，控制 prompt 长度）
    contexts = []
    citations = []
    for h in hits:
        doc = h["document"]
        parents = (db.query(models.ChunkParent)
                   .filter(models.ChunkParent.document_id == doc.id)
                   .order_by(models.ChunkParent.chunk_index)
                   .limit(2).all())
        if not parents:
            continue
        for p in parents:
            if p.text:
                contexts.append(f"[{doc.title}] {p.text[:1500]}")
        first = parents[0]
        citations.append({
            "document_id": doc.id,
            "title": doc.title,
            "snippet": first.text[:150],
            "parent_id": first.id,
            "chunk_index": first.chunk_index,
        })
        if len(contexts) >= 8:  # 上下文预算
            break

    if not contexts:
        return schemas.ok({
            "session_id": None,
            "answer": "未在资料库中找到相关内容",
            "citations": [],
            "confidence": 0.0,
        })

    # 3. 组装 prompt → LLM
    material = "\n\n".join(contexts)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"资料：\n{material}\n\n问题：{question}"},
    ]
    try:
        answer = llm_mod.chat(messages)
    except RuntimeError as exc:
        from ..errors import BizError
        raise BizError(503, 50300, str(exc)) from exc

    # 4. 记录会话（修复#4：有 session_id 则续接，无则新建）
    try:
        session = None
        if body.session_id:
            session = db.get(models.QASession, body.session_id)
            if session is None or session.user_id != user.id:
                raise bad_request("会话不存在或无权访问")
        if session is None:
            session = models.QASession(user_id=user.id, title=question[:50])
            db.add(session)
            db.flush()
        db.add(models.QAMessage(session_id=session.id, role="user", content=question))
        db.add(models.QAMessage(session_id=session.id, role="assistant",
                                content=answer, citations=citations))
        db.commit()
        session_id = session.id
    except bad_request:
        raise
    except Exception as exc:
        logger.warning("QA 会话写入失败: %s", exc)
        db.rollback()
        session_id = None

    return schemas.ok({
        "session_id": session_id,
        "answer": answer,
        "citations": citations,
        "confidence": round(hits[0]["score"], 4) if hits else 0.0,
    })


@router.get("/sessions")
def list_sessions(page: int = 1, page_size: int = 20,
                  db: Session = Depends(get_db),
                  user: models.User = Depends(get_current_user)):
    """当前用户的问答会话列表（修复#4）：按更新时间倒序。"""
    sessions = (db.query(models.QASession)
                .filter(models.QASession.user_id == user.id)
                .order_by(models.QASession.created_at.desc())
                .offset((page - 1) * page_size).limit(page_size)
                .all())
    total = db.query(models.QASession).filter(
        models.QASession.user_id == user.id).count()
    items = []
    for s in sessions:
        last_msg = (db.query(models.QAMessage)
                    .filter(models.QAMessage.session_id == s.id)
                    .order_by(models.QAMessage.id.desc()).first())
        items.append({
            "id": s.id,
            "title": s.title,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "last_preview": (last_msg.content[:60] if last_msg and last_msg.role == "assistant"
                             else last_msg.content[:60] if last_msg else ""),
        })
    return schemas.ok({"items": items, "total": total, "page": page, "page_size": page_size})


@router.get("/sessions/{session_id}/messages")
def session_messages(session_id: int,
                     db: Session = Depends(get_db),
                     user: models.User = Depends(get_current_user)):
    """会话全部消息（修复#4）：含引用来源。"""
    session = db.get(models.QASession, session_id)
    if session is None or session.user_id != user.id:
        raise bad_request("会话不存在或无权访问")
    msgs = (db.query(models.QAMessage)
            .filter(models.QAMessage.session_id == session_id)
            .order_by(models.QAMessage.id).all())
    return schemas.ok({
        "session_id": session_id,
        "messages": [{
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "citations": m.citations or [],
        } for m in msgs],
    })


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int,
                   db: Session = Depends(get_db),
                   user: models.User = Depends(get_current_user)):
    """删除单条会话（F21 收尾）：级联删除其消息，仅本人。"""
    session = db.get(models.QASession, session_id)
    if session is None or session.user_id != user.id:
        raise bad_request("会话不存在或无权访问")
    db.query(models.QAMessage).filter(models.QAMessage.session_id == session_id).delete()
    db.delete(session)
    db.commit()
    return schemas.ok({"deleted": session_id})


@router.delete("/sessions")
def clear_sessions(db: Session = Depends(get_db),
                   user: models.User = Depends(get_current_user)):
    """清空本人全部会话（F21 收尾）。"""
    ids = [s.id for s in db.query(models.QASession).filter(
        models.QASession.user_id == user.id).all()]
    if ids:
        db.query(models.QAMessage).filter(models.QAMessage.session_id.in_(ids)).delete(
            synchronize_session=False)
        db.query(models.QASession).filter(models.QASession.user_id == user.id).delete(
            synchronize_session=False)
        db.commit()
    return schemas.ok({"deleted": len(ids)})
