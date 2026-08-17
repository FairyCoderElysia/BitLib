# -*- coding: utf-8 -*-
"""DocumentFTS 同步（spec §5.1 / §6.3）。

- FTS5 external content 表（document_fts）不自动同步行，应用层维护
- jieba 分词（空格 join）后写入 title / content_text，配合 unicode61 tokenizer 实现中文词匹配
- 冗余 department_id / status 列：department_id 仅存「主部门」快照（观测），
"""
import logging

import jieba
from sqlalchemy import text

logger = logging.getLogger(__name__)

_FTS_TABLE = "document_fts"


def _tokenize(s: str | None) -> str:
    return " ".join(jieba.cut(s or "", cut_all=False))


def _dept_str(department_id: int | None) -> str:
    return str(department_id) if department_id is not None else ""


def sync_document(db, doc) -> None:
    """插入/更新文档的 FTS 行（先删后插）。调用方负责 commit。

    department_id 冗余列写主部门快照；授权一律不回读该列（S7 连接表为准）。
    """
    db.execute(text(f"DELETE FROM {_FTS_TABLE} WHERE rowid=:id"), {"id": doc.id})
    db.execute(
        text(f"INSERT INTO {_FTS_TABLE}(rowid, title, content_text, department_id, status) "
             f"VALUES(:id, :t, :c, :d, :s)"),
        {"id": doc.id, "t": _tokenize(doc.title), "c": _tokenize(doc.content_text),
         "d": _dept_str(doc.department_id), "s": doc.status},
    )


def delete_document(db, doc_id: int) -> None:
    """删除文档的 FTS 行。调用方负责 commit。"""
    db.execute(text(f"DELETE FROM {_FTS_TABLE} WHERE rowid=:id"), {"id": doc_id})
