# -*- coding: utf-8 -*-
"""热点搜索缓存（spec F16）：配置 redis_url 用 Redis，未配置降级进程内 TTL 缓存。

对业务层透明（同一 get/set 接口）。TTL 默认 600s。
"""
import json
import time
import logging

from .config import settings

logger = logging.getLogger(__name__)


class Cache:
    def __init__(self):
        self._redis = None
        if settings.redis_url:
            try:
                import redis
                self._redis = redis.from_url(settings.redis_url, decode_responses=True)
                logger.info("缓存后端：Redis (%s)", settings.redis_url)
            except Exception as exc:
                logger.warning("Redis 连接失败，降级内存缓存: %s", exc)
                self._redis = None
        if self._redis is None:
            self._mem: dict[str, tuple[float, object]] = {}
            logger.info("缓存后端：进程内 TTL 缓存（未配置 REDIS_URL）")

    def get(self, key: str):
        if self._redis is not None:
            raw = self._redis.get(key)
            return json.loads(raw) if raw else None
        item = self._mem.get(key)
        if item is not None and item[0] > time.time():
            return item[1]
        self._mem.pop(key, None)
        return None

    def set(self, key: str, value, ttl: int = 600) -> None:
        if self._redis is not None:
            try:
                self._redis.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
            except Exception as exc:
                logger.warning("Redis 写入失败: %s", exc)
            return
        self._mem[key] = (time.time() + ttl, value)


cache = Cache()
