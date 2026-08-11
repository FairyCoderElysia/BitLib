# -*- coding: utf-8 -*-
"""应用配置：pydantic-settings + .env 加载。

配置优先级：显式传入 > 环境变量 > .env 文件 > 内置默认值。
LLM / Embedding / Reranker 相关字段仅为占位默认值，供 M3/M4 使用。
"""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（backend/）
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ENV_FILE = BASE_DIR / ".env"

# 标记：secret_key 是否由系统随机生成（启动日志提示）
random_secret_generated = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "企业资料管理系统"
    debug: bool = False

    # ---------- 数据库 ----------
    # 默认 SQLite：data/app.db（WAL + busy_timeout 在 db.py 中启用）
    database_url: str = f"sqlite:///{(DATA_DIR / 'app.db').as_posix()}"

    # ---------- JWT ----------
    secret_key: str = ""          # 缺省随机生成并打印启动日志
    jwt_expire_minutes: int = 720  # 默认 12h

    # ---------- 上传 ----------
    upload_dir: str = str(DATA_DIR / "uploads")  # 文件私有存储目录
    max_upload_mb: int = 200

    # ---------- 向量库（M3） ----------
    chroma_dir: str = str(DATA_DIR / "chroma")   # Chroma 持久化目录

    # ---------- 爬虫定时调度（M5，spec §6.2 / F9） ----------
    # 总开关：False 时不启动 APScheduler（手动 run 仍可用）；.env: ENABLE_SCHEDULER=true
    enable_scheduler: bool = False

    # ---------- admin 初始账号 ----------
    # 首次启动播种 admin；缺省随机生成并打印启动日志；仅首启生效
    admin_initial_password: str = ""

    # ---------- 可选 Redis（未配置时 M4 降级为内存 TTL 缓存） ----------
    redis_url: str = ""

    # ---------- LLM / Embedding / Reranker（占位，M3/M4 使用） ----------
    llm_mode: str = "local"                 # local=Ollama / api=OpenAI 兼容
    llm_model: str = "qwen2.5"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = ""
    embedding_mode: str = "local"           # local / api
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dim: int = 512                # 与向量库维度一致；切换维度需重建索引
    embedding_api_base_url: str = ""        # 缺省回退 llm_base_url
    embedding_api_key: str = ""
    embedding_api_model: str = ""
    reranker_model: str = "BAAI/bge-reranker-base"
    reranker_enabled: bool = True
    rerank_threshold: float = 0.55        # 重排分数下限：低于视为不相关剔除（sigmoid 中性基准 0.5）
    search_threshold: float = 0.5          # 语义相似度阈值：低于则视为未找到（spec F3/F6）


@lru_cache
def get_settings() -> Settings:
    """返回配置单例；secret_key 缺省时随机生成（仅内存，不写回文件）。"""
    global random_secret_generated
    s = Settings()
    if not s.secret_key:
        import secrets
        s.secret_key = secrets.token_hex(32)
        random_secret_generated = True
    return s


settings = get_settings()
