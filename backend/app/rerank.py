# -*- coding: utf-8 -*-
"""重排序（spec F3/F6）：cross-encoder 精排，featured 加权 +1.0。

- 本地模式：sentence-transformers CrossEncoder（默认 BAAI/bge-reranker-base，懒加载）
- API 模式 / reranker_enabled=False：跳过重排，按 RRF 分排序 + featured 加权
- scorer 可注入（测试/替换用）
"""
import logging
from typing import Callable, Optional

from .config import settings

logger = logging.getLogger(__name__)

_scorer_cache = None


def _default_scorer() -> Callable[[list[tuple[str, str]]], list[float]]:
    """懒加载 CrossEncoder；模型首次使用下载（~1.1GB）。

    CrossEncoder.predict 返回 logits，sigmoid 转 [0,1] 概率，与 search_threshold 对齐。
    """
    global _scorer_cache
    if _scorer_cache is None:
        import math
        from sentence_transformers import CrossEncoder
        logger.info("加载重排模型：%s（首次需下载，约 1.1GB）", settings.reranker_model)
        model = CrossEncoder(settings.reranker_model)
        _scorer_cache = lambda pairs: [1.0 / (1.0 + math.exp(-s))  # noqa: E731
                                       for s in model.predict(pairs)]
    return _scorer_cache


def rerank(query: str, candidates: list[dict], limit: int = 5,
           scorer: Optional[Callable[[list[tuple[str, str]]], list[float]]] = None) -> list[dict]:
    """对候选精排取 topN。candidates: [{document_id, text, rrf, is_featured, ...}]。

    返回 candidates 的前 limit（已按 score 降序；score 含 featured +1.0 加权）。
    """
    if not candidates:
        return []

    if not settings.reranker_enabled:
        # 跳过 cross-encoder：按 RRF + featured 排序
        for c in candidates:
            c["score"] = c.get("rrf", 0.0) + (1.0 if c.get("is_featured") else 0.0)
        candidates.sort(key=lambda c: -c["score"])
        return candidates[:limit]

    pairs = [(query, c["text"]) for c in candidates]
    try:
        if scorer is None:
            scorer = _default_scorer()
        scores = scorer(pairs)
    except Exception as exc:
        logger.warning("重排失败，降级 RRF 排序: %s", exc)
        for c in candidates:
            c["score"] = c.get("rrf", 0.0) + (1.0 if c.get("is_featured") else 0.0)
        candidates.sort(key=lambda c: -c["score"])
        return candidates[:limit]
    for c, s in zip(candidates, scores):
        c["score"] = float(s) + (1.0 if c.get("is_featured") else 0.0)
    candidates.sort(key=lambda c: -c["score"])
    return candidates[:limit]
