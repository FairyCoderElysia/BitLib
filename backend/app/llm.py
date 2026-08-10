# -*- coding: utf-8 -*-
"""LLM 双模式（spec F6）：local=Ollama / api=OpenAI 兼容，同一 chat() 接口。

- local：POST {llm_base_url}/chat/completions（Ollama 兼容 OpenAI 端点）
- api：同构请求 + Authorization Bearer
- 调用失败抛 RuntimeError，由上层转友好错误（不影响检索功能）
"""
import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def chat(messages: list[dict], temperature: float = 0.3, timeout: float = 180) -> str:
    """messages: [{"role": "system"|"user"|"assistant", "content": str}]。返回回答文本。

    timeout：请求超时秒数（默认 180 保持向后兼容；摘要生成等短任务可传更小值）。
    """
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {}
    if settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "stream": False,
        "temperature": temperature,
    }
    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
    except Exception as exc:
        logger.error("LLM 调用失败（mode=%s, url=%s）: %s", settings.llm_mode, url, exc)
        raise RuntimeError(
            f"AI 服务不可用（{settings.llm_mode} 模式，{url}）。"
            f"请确认 Ollama 已启动（ollama serve）或配置 LLM_API_URL/KEY"
        ) from exc
    try:
        return resp.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        logger.error("LLM 响应格式异常: %s", resp.text[:300])
        raise RuntimeError("AI 服务响应格式异常") from exc
