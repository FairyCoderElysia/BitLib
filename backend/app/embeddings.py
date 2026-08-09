# -*- coding: utf-8 -*-
"""Embedding 双模式（spec §7.2 / F6）。

- local：sentence-transformers（默认 BAAI/bge-small-zh-v1.5，512 维），懒加载，首次调用下载模型
- api  ：OpenAI 兼容接口（POST {base}/embeddings），base 缺省回退 llm_base_url
- 维度校验：与 settings.embedding_dim 不一致抛错（提示切换维度需重建向量索引）
"""
import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_model = None  # 本地模型单例


def _get_local_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        logger.info("加载本地 embedding 模型：%s（首次需下载，约 95MB）", settings.embedding_model)
        _model = SentenceTransformer(settings.embedding_model)
    return _model


def _check_dim(vectors: list[list[float]]) -> None:
    dim = len(vectors[0]) if vectors else 0
    if dim != settings.embedding_dim:
        raise RuntimeError(
            f"embedding 维度 {dim} 与配置 embedding_dim={settings.embedding_dim} 不一致，"
            f"请切换配置并重建向量索引"
        )


def _embed_local(texts: list[str]) -> list[list[float]]:
    model = _get_local_model()
    vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()
    _check_dim(vecs)
    return vecs


def _embed_api(texts: list[str]) -> list[list[float]]:
    base = (settings.embedding_api_base_url or settings.llm_base_url).rstrip("/")
    model = settings.embedding_api_model or settings.embedding_model
    headers = {}
    if settings.embedding_api_key or settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.embedding_api_key or settings.llm_api_key}"
    resp = httpx.post(f"{base}/embeddings", headers=headers,
                      json={"model": model, "input": texts}, timeout=120)
    resp.raise_for_status()
    data = resp.json()["data"]
    vecs = [item["embedding"] for item in sorted(data, key=lambda x: x["index"])]
    _check_dim(vecs)
    return vecs


def embed(texts: list[str]) -> list[list[float]]:
    """批量向量化。texts 非空。"""
    if not texts:
        return []
    if settings.embedding_mode == "api":
        return _embed_api(texts)
    return _embed_local(texts)
