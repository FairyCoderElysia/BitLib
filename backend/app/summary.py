# -*- coding: utf-8 -*-
"""文档智能摘要（spec §3 F17 / Sprint 7）。

- generate_summary：入库管线内同步生成 doc.summary。LLM 可用则生成 1-3 句摘要；
  LLM 未配置/超时/任何异常一律降级为开头片段截取，绝不抛错、绝不阻塞入库。
- get_display_summary：检索与详情共用的展示口径（有 summary 用 summary，否则截取）。
"""
import logging

from . import llm

logger = logging.getLogger(__name__)

# 摘要 prompt：要求简洁 1-3 句，直接输出摘要
SYSTEM_PROMPT = (
    "请用 1-3 句中文概括以下文档的核心内容，"
    "直接输出摘要，不要解释，不要加标题，不要换行。"
)
LLM_TIMEOUT = 20          # 秒：LLM 慢/挂起时放弃，避免拖住入库接口
LLM_PREFIX_CHARS = 2000   # 送入 LLM 的前缀长度（防超长 token）
SUMMARY_MAX_CHARS = 200   # LLM 成功输出清理后截断上限
FALLBACK_CHARS = 120      # 降级截取长度


def generate_summary(db, doc) -> str:
    """生成文档摘要（入库调用）。任何异常都降级为开头片段，绝不 raise。"""
    text = doc.content_text or ""
    try:
        prefix = text[:LLM_PREFIX_CHARS]
        if not prefix.strip():
            return text[:FALLBACK_CHARS]  # 空文档直接返回空片段
        answer = llm.chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prefix},
            ],
            temperature=0.3,
            timeout=LLM_TIMEOUT,
        )
        # 清理：压缩多余空白/换行后截断上限
        cleaned = " ".join(str(answer).split()).strip()
        if cleaned:
            return cleaned[:SUMMARY_MAX_CHARS]
        return text[:FALLBACK_CHARS]
    except Exception as exc:
        # LLM 未配置（RuntimeError）/ 超时 / 网络异常 → 降级截取，日志告警
        logger.warning("摘要生成失败，降级为开头片段（doc=%s）: %s",
                       getattr(doc, "id", None), exc)
        return text[:FALLBACK_CHARS]


def get_display_summary(doc) -> str:
    """展示口径：优先 doc.summary，否则截取 content_text 开头；两者皆空返回空串。"""
    if doc.summary:
        return doc.summary
    return (doc.content_text or "")[:FALLBACK_CHARS]
