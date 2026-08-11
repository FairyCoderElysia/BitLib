# -*- coding: utf-8 -*-
"""FastAPI 应用入口：lifespan（建表播种）+ CORS + 路由注册 + 统一异常处理。"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
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
    logger.info("%s 已启动", settings.app_name)
    yield
    shutdown_scheduler()


def _prewarm() -> None:
    """后台线程预热 embedding 模型；失败不阻塞启动（首次使用时再加载）。"""

    def _load():
        try:
            from .embeddings import embed
            embed(["预热"])
            logger.info("embedding 模型预热完成")
        except Exception as exc:
            logger.warning("embedding 模型预热失败（首次使用时再加载）: %s", exc)

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


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("未处理异常: %s", exc)
    return JSONResponse(status_code=500, content={
        "code": CODE_INTERNAL, "message": "服务器内部错误", "detail": {}})


@app.get("/api/health")
def health():
    """健康检查（部署冒烟）。"""
    return {"code": 0, "message": "ok", "data": {"status": "up"}}
