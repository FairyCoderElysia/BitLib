# -*- coding: utf-8 -*-
"""FastAPI 应用入口：lifespan（建表播种）+ CORS + 路由注册 + 统一异常处理。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import random_secret_generated, settings
from .db import init_db
from .errors import (CODE_BAD_REQUEST, CODE_FORBIDDEN, CODE_INTERNAL,
                     CODE_NOT_FOUND, CODE_UNAUTHORIZED, BizError)
from .routers import (admin, auth, crawl, documents, favorites, notifications,
                      qa, search)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动：建表 + 播种 + 爬虫调度器；打印首次 admin 初始密码 / 随机 secret_key 提示。"""
    admin_password = init_db()
    if admin_password:
        logger.warning("首次播种内置 admin 账号，初始密码：%s（请登录后尽快修改）",
                       admin_password)
    if random_secret_generated:
        logger.warning("未配置 SECRET_KEY，已随机生成（仅本次运行有效，重启后旧 token 失效）")
    from .scheduler import shutdown_scheduler, start_scheduler
    start_scheduler()
    _prewarm()  # 后台预热 embedding 模型，避免首请求加载竞态（历史 500 根因）
    _ensure_vector_health()  # 后台检测 Chroma HNSW 可加载，损坏时自动重建（自愈）
    logger.info("%s 已启动", settings.app_name)
    yield
    shutdown_scheduler()


def _ensure_vector_health() -> None:
    """后台线程：验证 Chroma 向量索引可查询；HNSW 跨进程加载失败（Windows 上
    chromadb 持久化脆弱，重启后偶发 "Error loading hnsw index"）时自动重建。

    重建在服务进程内执行（reset 集合 → 全部 approved 文档重新入库 → 预热查询），
    完成后检索自动恢复；重建期间检索可能短暂 500（自愈窗口）。
    """

    def _check():
        try:
            import time
            time.sleep(3)  # 等服务完全就绪
            from . import vector_store
            vector_store.query([0.0] * settings.embedding_dim, 1,
                               user_department_id=None, is_admin=True)
            logger.info("Chroma 向量索引健康，无需重建")
            return
        except Exception as exc:
            logger.warning("Chroma HNSW 加载失败，自动重建向量索引: %s", exc)
        try:
            from .db import SessionLocal
            from . import models
            from .ingest import ingest_document, ingest_text
            from . import vector_store
            vector_store.reset_collection()
            db = SessionLocal()
            try:
                docs = db.query(models.Document).filter(
                    models.Document.status == models.STATUS_APPROVED).all()
                ok = 0
                for doc in docs:
                    if doc.file_path:
                        ingest_document(db, doc, regen_summary=False)
                    else:
                        ingest_text(db, doc, doc.content_text or "", regen_summary=False)
                    db.refresh(doc)
                    if doc.status == models.STATUS_APPROVED:
                        ok += 1
                vector_store.query([0.0] * settings.embedding_dim, 1,
                                   user_department_id=None, is_admin=True)
                logger.info("向量索引自动重建完成：%s 个文档", ok)
            finally:
                db.close()
        except Exception as exc:
            logger.exception("向量索引自动重建失败: %s", exc)

    import threading
    threading.Thread(target=_check, daemon=True).start()


def _prewarm() -> None:
    """后台线程预热 embedding 与 reranker 模型；失败不阻塞启动（首次使用时再加载）。"""

    def _load():
        # embedding 预热：与 S3 之前行为一致，失败不阻塞启动
        try:
            from .embeddings import embed
            embed(["预热"])
            logger.info("embedding 模型预热完成")
        except Exception as exc:
            logger.warning("embedding 模型预热失败（首次使用时再加载）: %s", exc)

        # reranker 预热：仅 reranker_enabled=true 时加载。
        # warm_up() 只有 CrossEncoder 真正加载成功才返回 True；
        # 加载失败会抛异常，由本处 warning（与 embedding 预热语义一致）。
        try:
            if settings.reranker_enabled:
                from .rerank import warm_up
                warm_up()
                logger.info("reranker 模型预热完成")
        except Exception as exc:
            logger.warning("reranker 模型预热失败（首次使用时再加载）: %s", exc)

    import threading
    threading.Thread(target=_load, daemon=True).start()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# CORS：私有化部署默认放开，生产可按需收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Skipped-Count"],  # 批量下载剔除计数（生产跨域可读）
)

# 路由注册：统一前缀 /api（spec §10.1）
app.include_router(auth.router, prefix="/api")
app.include_router(documents.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(qa.router, prefix="/api")
app.include_router(favorites.router, prefix="/api")
app.include_router(crawl.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")

# 统一错误响应结构（spec §10.1）
_HTTP_TO_CODE = {
    400: CODE_BAD_REQUEST,
    401: CODE_UNAUTHORIZED,
    403: CODE_FORBIDDEN,
    404: CODE_NOT_FOUND,
}


@app.exception_handler(BizError)
async def _biz_error_handler(request: Request, exc: BizError):
    return JSONResponse(status_code=exc.status_code, content={
        "code": exc.code, "message": exc.message, "detail": exc.detail})


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    code = _HTTP_TO_CODE.get(exc.status_code, CODE_INTERNAL)
    return JSONResponse(status_code=exc.status_code, content={
        "code": code, "message": str(exc.detail), "detail": {}})


@app.exception_handler(RequestValidationError)
async def _request_validation_error_handler(request: Request, exc: RequestValidationError):
    """D2：malformed JSON 等 FastAPI 默认 422 统一为 400 + 业务码结构。

    仅处理 FastAPI 请求校验错误（JSON 解析失败 / 路径与查询参数校验失败等），
    不影响 BizError 与 HTTPException 的既有业务错误响应。
    """
    return JSONResponse(status_code=400, content={
        "code": CODE_BAD_REQUEST,
        "message": "请求参数校验失败",
        "detail": {"errors": jsonable_encoder(exc.errors())},
    })


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("未处理异常: %s", exc)
    return JSONResponse(status_code=500, content={
        "code": CODE_INTERNAL, "message": "服务器内部错误", "detail": {}})


@app.get("/api/health")
def health():
    """健康检查（部署冒烟）。"""
    return {"code": 0, "message": "ok", "data": {"status": "up"}}
