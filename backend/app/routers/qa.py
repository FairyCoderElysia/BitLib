# -*- coding: utf-8 -*-
"""AI 问答路由（spec F6 / §10.7 / S5 修复）：POST /api/qa/ask。

RAG：会话/历史加载 → 寒暄分流 → 混合检索（召回阶段权限过滤）
→ small-to-big 回溯 parent（优先语义命中 parent_ids，keyword-only 按查询词匹配）
→ 组装 prompt（含最近历史窗口）→ LLM → 回答 + 引用来源。
"""
import logging

import jieba
from fastapi import APIRouter, Depends
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .. import llm as llm_mod
from .. import models, schemas
from ..db import get_db
from ..deps import get_current_user
from ..errors import BizError, bad_request
from ..search_service import hybrid_search
from ..visibility import dept_visible

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/qa", tags=["qa"])

SYSTEM_PROMPT = (
    "你是企业资料库智能助手。请仅根据提供的资料回答用户问题；"
    "如果资料中没有相关内容，明确回答\"未在资料库中找到相关内容\"，不要编造。"
    "回答使用简体中文，简洁准确。"
)

GREETING_SYSTEM_PROMPT = (
    "你是企业资料库智能助手。用户在进行寒暄，请简短友好回应，不超过 30 字，"
    "不要检索资料，不要编造资料内容。"
)

# S5：纯寒暄封闭集合与判定前去除的标点/空白字符集合。
# 测试脚本读取同一常量，避免硬编码不一致。
GREETING_PHRASES = frozenset({
    "你好", "您好", "hi", "hello", "嗨", "在吗", "谢谢", "多谢", "感谢",
    "再见", "拜拜", "早上好", "中午好", "下午好", "晚上好", "晚安",
})
GREETING_STRIP_CHARS = " \t\n，。！？!?~～"

# S5：上下文/历史窗口常量（模块级，便于按模型上下文调整）。
QA_HISTORY_WINDOW = 10          # 最近 10 条 QAMessage
QA_HISTORY_CHAR_PER_MSG = 600   # 每条历史截断 600 字符
CONTEXT_MAX_BLOCKS = 8          # 全局上下文 parent 块上限（沿用现状）
MAX_PARENTS_PER_DOC = 2         # 每文档最多 parent 块（沿用现状）
CONTEXT_CHAR_PER_PARENT = 1500  # 每块资料截断字符（沿用现状）
CITATION_SNIPPET_CHARS = 150    # 引用 snippet 截断字符（沿用现状）
MAX_KEYWORD_PARENT_MATCHES = 200  # keyword-only 选择器 SQL 预筛行数上限


def _is_greeting(question: str) -> bool:
    """纯寒暄整句精确匹配：去空白/标点后命中封闭集合；不做子串匹配。"""
    normalized = question.translate(str.maketrans("", "", GREETING_STRIP_CHARS))
    return normalized in GREETING_PHRASES


def _load_session_context(db: Session, user: models.User, session_id: int | None):
    """加载会话/历史窗口/最近一条带 citations 的 assistant 消息。

    返回 (session, history, last_citation_msg)。session_id 非空时校验存在且本人，
    否则按现状 400。历史取最近 QA_HISTORY_WINDOW 条（id 升序），
    citations 不嵌入 content。
    """
    if session_id is None:
        return None, [], None

    session = db.get(models.QASession, session_id)
    if session is None or session.user_id != user.id:
        raise bad_request("会话不存在或无权访问")

    recent = (db.query(models.QAMessage)
              .filter(models.QAMessage.session_id == session_id)
              .order_by(models.QAMessage.id.desc())
              .limit(QA_HISTORY_WINDOW).all())
    recent_asc = list(reversed(recent))  # 旧 → 新

    history = [{"role": m.role,
                "content": (m.content or "")[:QA_HISTORY_CHAR_PER_MSG]}
               for m in recent_asc]

    last_citation_msg = None
    for m in reversed(recent_asc):  # 窗口内新 → 旧
        if m.role == "assistant" and m.citations:
            last_citation_msg = m
            break
    return session, history, last_citation_msg


def _select_parents_from_semantic(db: Session, doc: models.Document,
                                  parent_ids: list[int], limit: int):
    """优先路径：按语义命中 parent_ids 顺序取文本非空的前 limit 个 ChunkParent。"""
    if not parent_ids:
        return []
    rows = (db.query(models.ChunkParent)
            .filter(models.ChunkParent.document_id == doc.id,
                    models.ChunkParent.chunk_index.in_(parent_ids))
            .all())
    by_idx = {p.chunk_index: p for p in rows}
    selected = []
    seen: set[int] = set()
    for idx in parent_ids:
        p = by_idx.get(idx)
        if p is None or idx in seen:
            continue
        if not (p.text or "").strip():
            continue
        seen.add(idx)
        selected.append(p)
        if len(selected) >= limit:
            break
    return selected


def _select_parents_by_keyword(db: Session, doc: models.Document,
                               question: str, limit: int):
    """keyword-only 回退：按查询词 token 在 ChunkParent.text 匹配打分取前 limit。

    score = 命中不同 token 数；标题命中任一 token 仅作同分附加小分。
    排序 (-score, chunk_index)。无任何 token 命中则返回空（跳过该文档）。
    """
    tokens = [t for t in jieba.cut_for_search(question) if t.strip() and len(t.strip()) > 1]
    tokens = list(dict.fromkeys(tokens))  # 去重保序：score 统计不同 token 数
    if not tokens:
        token = question.strip()
        if not token:
            return []
        tokens = [token]

    conds = [func.instr(models.ChunkParent.text, t) > 0 for t in tokens]
    rows = (db.query(models.ChunkParent)
            .filter(models.ChunkParent.document_id == doc.id, or_(*conds))
            .order_by(models.ChunkParent.chunk_index)
            .limit(MAX_KEYWORD_PARENT_MATCHES).all())

    scored = []
    for p in rows:
        text = p.text or ""
        score = float(sum(1 for t in tokens if t in text))
        if any(t in (p.title or "") for t in tokens):
            score += 0.01
        scored.append((score, p.chunk_index, p))
    scored.sort(key=lambda x: (-x[0], x[1]))

    selected = []
    for _, _, p in scored:
        if not (p.text or "").strip():
            continue
        selected.append(p)
        if len(selected) >= limit:
            break
    return selected


def _append_parent_context(contexts: list, citations: list,
                           doc: models.Document, parent: models.ChunkParent) -> None:
    """追加一个 parent 对应的上下文块与引用项（一一对应）。"""
    text = parent.text or ""
    contexts.append(f"[{doc.title}] {text[:CONTEXT_CHAR_PER_PARENT]}")
    citations.append({
        "document_id": doc.id,
        "title": doc.title,
        "snippet": text[:CITATION_SNIPPET_CHARS],
        "parent_id": parent.id,
        "chunk_index": parent.chunk_index,
    })


def _build_context_and_citations(db: Session, hits: list, question: str):
    """检索命中 → 上下文 + citations。优先 parent_ids；无则 keyword 回退。"""
    contexts = []
    citations = []
    for h in hits or []:
        doc = h.get("document")
        if doc is None:
            continue
        parent_ids = h.get("parent_ids") or []
        if parent_ids:
            parents = _select_parents_from_semantic(db, doc, parent_ids,
                                                    MAX_PARENTS_PER_DOC)
        else:
            parents = _select_parents_by_keyword(db, doc, question,
                                                 MAX_PARENTS_PER_DOC)
        for p in parents:
            if len(contexts) >= CONTEXT_MAX_BLOCKS:
                return contexts, citations
            _append_parent_context(contexts, citations, doc, p)
    return contexts, citations


def _reuse_last_citations(db: Session, user: models.User, question: str,
                          last_citation_msg):
    """无命中时复用上一轮 citations：候选文档校验 approved + 部门可见，
    用当前问题按 keyword 选择器选最相关 parent（每文档最多 2 块/全局 8 块）。"""
    if last_citation_msg is None:
        return [], []

    doc_ids = []
    for ci in (last_citation_msg.citations or []):
        if isinstance(ci, dict):
            did = ci.get("document_id")
            if did is not None and did not in doc_ids:
                doc_ids.append(did)

    contexts = []
    citations = []
    for did in doc_ids:
        doc = db.get(models.Document, did)
        if doc is None or doc.status != models.STATUS_APPROVED:
            continue
        if not dept_visible(user, doc):
            continue
        parents = _select_parents_by_keyword(db, doc, question, MAX_PARENTS_PER_DOC)
        for p in parents:
            if len(contexts) >= CONTEXT_MAX_BLOCKS:
                return contexts, citations
            _append_parent_context(contexts, citations, doc, p)
    return contexts, citations


def _build_knowledge_messages(question: str, contexts: list, history: list) -> list:
    """知识性问答 messages：system + 最近历史（升序，已截断）+ 最终 user（资料+问题）。"""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history or []:
        messages.append({"role": h["role"], "content": h["content"]})
    material = "\n\n".join(contexts)
    messages.append({
        "role": "user",
        "content": f"资料：\n{material}\n\n问题：{question}",
    })
    return messages


def _build_greeting_messages(question: str) -> list:
    """纯寒暄 messages：独立 prompt，不带历史/资料/检索。"""
    return [
        {"role": "system", "content": GREETING_SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]


def _persist_qa(db: Session, user: models.User, session: models.QASession | None,
                question: str, answer: str, citations: list):
    """记录会话与 user/assistant 消息；写入失败沿用现状：session_id 为 None。"""
    try:
        if session is None:
            session = models.QASession(user_id=user.id, title=question[:50])
            db.add(session)
            db.flush()
        db.add(models.QAMessage(session_id=session.id, role="user", content=question))
        db.add(models.QAMessage(session_id=session.id, role="assistant",
                                content=answer, citations=citations))
        db.commit()
        return session.id
    except Exception as exc:
        logger.warning("QA 会话写入失败: %s", exc)
        db.rollback()
        return None


@router.post("/ask")
def ask_question(body: schemas.QARequest,
                 db: Session = Depends(get_db),
                 user: models.User = Depends(get_current_user)):
    """RAG 问答。返回回答 + 引用来源（document_id/title/snippet/parent_id/chunk_index）。"""
    question = (body.question or "").strip()
    if not question:
        raise bad_request("问题不能为空")

    # 1. 会话/历史加载提前到检索之前（错误语义与原先一致）
    session, history, last_citation_msg = _load_session_context(db, user, body.session_id)

    # 2. 纯寒暄分流：整句精确匹配，不检索、不带资料
    if _is_greeting(question):
        try:
            answer = llm_mod.chat(_build_greeting_messages(question))
        except RuntimeError as exc:
            raise BizError(503, 50300, str(exc)) from exc
        answer = answer[:100]
        citations: list = []
        session_id = _persist_qa(db, user, session, question, answer, citations)
        return schemas.ok({
            "session_id": session_id,
            "answer": answer,
            "citations": citations,
            "confidence": 0.0,
        })

    # 3. 知识路径：QA 召回 top_k=30（仅此处，不改 search_service 默认值）
    hits = hybrid_search(db, user, question, top_k=30, limit=8)
    contexts, citations = _build_context_and_citations(db, hits, question)
    confidence = round(hits[0]["score"], 4) if hits and contexts else 0.0

    if not contexts:
        # 4. 无有效上下文：历史窗口内最近 citations 复用；再失败才返回未找到
        contexts, citations = _reuse_last_citations(db, user, question,
                                                    last_citation_msg)
        confidence = 0.0
        if not contexts:
            return schemas.ok({
                "session_id": None,
                "answer": "未在资料库中找到相关内容",
                "citations": [],
                "confidence": 0.0,
            })

    # 5. 组装 LLM messages（system + 最近 10 条历史 + 最终 user）
    try:
        answer = llm_mod.chat(_build_knowledge_messages(question, contexts, history))
    except RuntimeError as exc:
        raise BizError(503, 50300, str(exc)) from exc

    # 保护性收敛：LLM 明确未找到时，返回与落库的 citations 均为空
    if "未在资料库中找到相关内容" in answer:
        citations = []

    session_id = _persist_qa(db, user, session, question, answer, citations)
    return schemas.ok({
        "session_id": session_id,
        "answer": answer,
        "citations": citations,
        "confidence": confidence,
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
