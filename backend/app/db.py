# -*- coding: utf-8 -*-
"""数据库：引擎 / 会话 / 初始化建表与播种。

- SQLite 默认 data/app.db，启用 WAL + busy_timeout + 外键约束
- init_db()：建全量表 + FTS5 external content 虚拟表 + 播种默认部门与 admin
"""
import logging
import secrets
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATA_DIR, settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明基类。"""


def _make_engine():
    """创建引擎；SQLite 启用 WAL / busy_timeout（支撑并发读写）。"""
    url = settings.database_url
    kwargs = {"future": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    eng = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):  # pragma: no cover - 事件回调
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return eng


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db():
    """FastAPI 请求级会话依赖。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_dirs() -> None:
    """确保 data/ 与 uploads/ 目录存在，且数据库文件所在目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    # 解析 SQLite URL 中的数据库文件路径并确保其父目录存在
    url = settings.database_url
    if url.startswith("sqlite:///"):
        db_path = url[len("sqlite:///"):]
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def _create_fts_table() -> None:
    """创建 DocumentFTS（FTS5 独立表，tokenize='unicode61'）。

    - 应用层同步：jieba 分词后写入 title/content_text（见 app/fts.py）
    - 冗余 department_id / status 列（UNINDEXED）支撑召回阶段权限过滤（M4 使用）
    - 历史库若为 external content 表（content='document'）则一次性迁移重建：
      external content 表 DELETE 时校验 content 表原文，与分词写入不一致会报
      "database disk image is malformed"，故改用独立表（自存副本，删除不受校验约束）
    """
    try:
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='document_fts'"
            )).fetchone()
        if row and row[0] and "content='document'" in row[0]:
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE document_fts"))
            logger.info("迁移：document_fts 由 external content 重建为独立 FTS5 表")
        ddl = text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5("
            "  title, content_text,"
            "  department_id UNINDEXED,"
            "  status UNINDEXED,"
            "  tokenize='unicode61'"
            ")"
        )
        with engine.begin() as conn:
            conn.execute(ddl)
        logger.info("DocumentFTS 表已就绪")
    except Exception as exc:  # FTS5 编译缺失等场景：M3 才使用，先降级警告
        logger.warning("DocumentFTS 建表失败（M3 使用，可暂缓）：%s", exc)


def _seed_defaults():
    """首次启动播种：3 个默认部门 + admin 账号。

    admin 密码取 settings.admin_initial_password，缺省随机生成。
    返回首次生成的 admin 初始密码（admin 已存在则返回 None）。幂等。
    """
    from . import models
    from .security import hash_password

    with SessionLocal() as db:
        # 默认部门（幂等）
        if db.query(models.Department).count() == 0:
            for name in ("技术部", "产品部", "市场部"):
                db.add(models.Department(name=name))
            db.commit()
            logger.info("已播种默认部门：技术部 / 产品部 / 市场部")

        # admin 账号（幂等，已存在不重置密码）
        admin = db.query(models.User).filter(models.User.username == "admin").first()
        if admin is not None:
            return None
        password = settings.admin_initial_password.strip() or secrets.token_urlsafe(12)
        db.add(models.User(
            username="admin",
            password_hash=hash_password(password),
            role="admin",
            department_id=None,
        ))
        db.commit()
        logger.info("已播种内置 admin 账号")
        return password


def _migrate_columns() -> None:
    """对已存在的表执行幂等 ALTER（Sprint 8 新增列，兼容老库）。

    - document.source_url（VARCHAR(512)，可空）：SQLite ALTER 仅允许追加可空/带默认列，满足；
      索引不随 ALTER 创建，需单独 CREATE INDEX IF NOT EXISTS（与 models 声明的 index 同名幂等）。
    - crawl_run_log.updated_count（INTEGER DEFAULT 0）：同上。
    - 全新库由 create_all 直接建列，PRAGMA 检查命中即跳过；异常降级警告，不阻塞启动。
    """
    try:
        with engine.begin() as conn:
            cols = [r[1] for r in conn.execute(text("PRAGMA table_info(document)")).fetchall()]
            if "source_url" not in cols:
                conn.execute(text("ALTER TABLE document ADD COLUMN source_url VARCHAR(512)"))
                logger.info("迁移：document 增加 source_url 列")
            cols2 = [r[1] for r in conn.execute(text("PRAGMA table_info(crawl_run_log)")).fetchall()]
            if "updated_count" not in cols2:
                conn.execute(text("ALTER TABLE crawl_run_log ADD COLUMN updated_count INTEGER DEFAULT 0"))
                logger.info("迁移：crawl_run_log 增加 updated_count 列")
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_document_source_url ON document(source_url)"))
    except Exception as exc:  # 表缺失等场景：降级警告，不阻塞启动
        logger.warning("Sprint8 迁移列失败（降级继续）：%s", exc)


def init_db() -> str | None:
    """初始化：建目录 → 建表（含 FTS）→ 老库补列 → 播种。返回首次 admin 初始密码。"""
    from . import models  # noqa: F401  确保模型已注册到 Base.metadata

    _ensure_dirs()
    Base.metadata.create_all(bind=engine)
    _migrate_columns()
    _create_fts_table()
    return _seed_defaults()
