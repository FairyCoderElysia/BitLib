# -*- coding: utf-8 -*-
"""管理端爬虫路由（M5，spec F9 / §5.1 CrawlTask）：任务 CRUD / 启停 / 手动执行 / 运行记录。

权限：admin 专属（spec §2.2：爬虫任务配置与定时抓取仅管理员）。
调度联动：创建/编辑/删除时同步注册或移除 APScheduler job（见 app/scheduler.py）。
"""
import logging
from typing import Optional
from urllib.parse import urlparse

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import crawl_task_departments, document_departments, models, schemas
from ..audit import client_ip, log_action
from ..crawler import run_crawl_task
from ..db import get_db
from ..deps import require_admin
from ..errors import bad_request, not_found
from ..scheduler import _remove_task, _schedule_task, scheduler

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/crawl-tasks", tags=["crawl"])


class CrawlTaskIn(BaseModel):
    """爬虫任务创建请求（spec §5.1 CrawlTask 字段）。"""
    name: str = Field(..., min_length=1, max_length=128)
    start_urls: list = Field(default_factory=list, description="起始 URL 列表")
    allowed_domains: list = Field(default_factory=list, description="域名白名单（SSRF）")
    selector: str = Field(default="", description="正文 CSS 选择器，留空=智能提取")
    max_depth: int = Field(default=1, ge=0, le=5)
    schedule: str = Field(default="", description="cron 表达式，空=不定时")
    enabled: bool = False
    target_department_id: Optional[int] = Field(default=None, description="目标部门（单值兼容），空=公开")
    target_department_ids: Optional[list[int]] = Field(
        default=None, description="目标部门集合（空数组=公开）")


class CrawlTaskPatch(BaseModel):
    """爬虫任务部分更新（全部可选）。"""
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    start_urls: Optional[list] = None
    allowed_domains: Optional[list] = None
    selector: Optional[str] = None
    max_depth: Optional[int] = Field(None, ge=0, le=5)
    schedule: Optional[str] = None
    enabled: Optional[bool] = None
    target_department_id: Optional[int] = None
    target_department_ids: Optional[list[int]] = Field(
        default=None, description="目标部门集合（空数组=公开）")


def _validate_input(db: Session, body: BaseModel) -> None:
    """创建/更新前的公共校验：URL 合法性 / 白名单 / cron / 目标部门存在性。"""
    start_urls = body.start_urls or []
    allowed = body.allowed_domains or []
    if not start_urls:
        raise bad_request("start_urls 至少需要一个起始 URL")
    if not allowed:
        raise bad_request("allowed_domains 至少需要一个允许域名（SSRF 白名单）")
    for u in start_urls:
        parsed = urlparse(str(u))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise bad_request(f"起始 URL 非法：{u}")
    if body.schedule and body.schedule.strip():
        try:
            CronTrigger.from_crontab(body.schedule.strip())
        except Exception:
            raise bad_request(f"cron 表达式非法：{body.schedule}")
    # 目标部门集合校验：target_department_ids 提供值（含 []）优先；
    # 否则回退旧单值 target_department_id。逐项校验存在性/上限。
    if getattr(body, "target_department_ids", None) is not None:
        document_departments.validate_department_ids(db, list(body.target_department_ids))
    elif getattr(body, "target_department_id", None) is not None:
        document_departments.validate_department_ids(db, [body.target_department_id])


def crawl_task_to_dict(task, last_log=None) -> dict:
    data = {
        "id": task.id,
        "name": task.name,
        "start_urls": task.start_urls or [],
        "allowed_domains": task.allowed_domains or [],
        "selector": task.selector or "",
        "max_depth": task.max_depth,
        "schedule": task.schedule or "",
        "enabled": task.enabled,
        "status": task.status,
        "last_run_at": task.last_run_at,
        "target_department_id": task.target_department_id,
        "target_department_ids": [did for did, _ in
                                  crawl_task_departments.get_crawl_task_dept_pairs(task)],
        "target_departments": [{"id": did, "name": nm} for did, nm in
                               crawl_task_departments.get_crawl_task_dept_pairs(task)],
        "created_by": task.created_by,
        "created_at": task.created_at,
    }
    if last_log is not None:
        data["last_run"] = crawl_run_log_to_dict(last_log)
    return data


def crawl_run_log_to_dict(log) -> dict:
    return {
        "id": log.id,
        "task_id": log.task_id,
        "started_at": log.started_at,
        "finished_at": log.finished_at,
        "fetched_count": log.fetched_count,
        "ingested_count": log.ingested_count,
        "updated_count": log.updated_count,
        "skipped_count": log.skipped_count,
        "status": log.status,
        "error": log.error,
        "created_at": log.created_at,
    }


@router.get("")
def list_crawl_tasks(page: int = 1, page_size: int = 20,
                     db: Session = Depends(get_db),
                     current_user: models.User = Depends(require_admin)):
    """任务列表（分页），每条附带最近一次运行记录。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = db.query(models.CrawlTask)
    items, total = schemas.paginate(q.order_by(models.CrawlTask.id.desc()), page, page_size)
    crawl_task_departments.attach_crawl_task_department_sets(db, items)
    result = []
    for t in items:
        last_log = db.query(models.CrawlRunLog).filter(
            models.CrawlRunLog.task_id == t.id
        ).order_by(models.CrawlRunLog.started_at.desc()).first()
        result.append(crawl_task_to_dict(t, last_log))
    return schemas.ok({"total": total, "page": page, "page_size": page_size,
                       "items": result})


@router.post("")
def create_crawl_task(body: CrawlTaskIn,
                      request: Request,
                      db: Session = Depends(get_db),
                      current_user: models.User = Depends(require_admin)):
    """创建爬虫任务；enabled 且 schedule 非空时同步注册 cron 调度。"""
    _validate_input(db, body)
    if body.target_department_ids is not None:
        dept_ids = document_departments.validate_department_ids(
            db, list(body.target_department_ids))
    elif body.target_department_id is not None:
        dept_ids = document_departments.validate_department_ids(db, [body.target_department_id])
    else:
        dept_ids = []
    task = models.CrawlTask(
        name=body.name.strip(),
        start_urls=body.start_urls,
        allowed_domains=body.allowed_domains,
        selector=body.selector,
        max_depth=body.max_depth,
        schedule=body.schedule.strip(),
        enabled=body.enabled,
        status="idle" if body.enabled else "disabled",
        target_department_id=(min(dept_ids) if dept_ids else None),
        created_by=current_user.id,
    )
    db.add(task)
    db.flush()
    crawl_task_departments.set_crawl_task_departments(db, task, dept_ids)
    db.commit()
    db.refresh(task)
    if task.enabled and task.schedule:
        _schedule_task(scheduler, task)
    log_action(db, current_user, "crawl_task_create", "crawl_task", task.id,
               {"name": task.name, "enabled": task.enabled, "schedule": task.schedule},
               client_ip(request))
    return schemas.ok(crawl_task_to_dict(task))


@router.patch("/{task_id}")
def update_crawl_task(task_id: int,
                      body: CrawlTaskPatch,
                      request: Request,
                      db: Session = Depends(get_db),
                      current_user: models.User = Depends(require_admin)):
    """编辑任务（含启停）；调度 job 同步注册/移除，写审计。"""
    task = db.get(models.CrawlTask, task_id)
    if task is None:
        raise not_found("爬虫任务不存在")
    # 合并现有值做公共校验（URL/白名单/cron/目标部门集合）
    provided_ids = "target_department_ids" in body.model_fields_set
    provided_single = "target_department_id" in body.model_fields_set
    if provided_ids:
        merged_ids = list(body.target_department_ids or [])
    elif provided_single:
        merged_ids = [body.target_department_id] if body.target_department_id is not None else []
    else:
        merged_ids = crawl_task_departments.get_crawl_task_dept_ids(task)
    merged = CrawlTaskIn(
        name=body.name.strip() if body.name is not None else task.name,
        start_urls=body.start_urls if body.start_urls is not None else (task.start_urls or []),
        allowed_domains=body.allowed_domains if body.allowed_domains is not None
        else (task.allowed_domains or []),
        selector=body.selector if body.selector is not None else (task.selector or ""),
        max_depth=body.max_depth if body.max_depth is not None else task.max_depth,
        schedule=body.schedule if body.schedule is not None else (task.schedule or ""),
        enabled=body.enabled if body.enabled is not None else task.enabled,
        target_department_ids=merged_ids,
    )
    _validate_input(db, merged)

    changes = []
    if body.name is not None and body.name.strip() != task.name:
        task.name = body.name.strip()
        changes.append("name")
    if body.start_urls is not None:
        task.start_urls = body.start_urls
        changes.append("start_urls")
    if body.allowed_domains is not None:
        task.allowed_domains = body.allowed_domains
        changes.append("allowed_domains")
    if body.selector is not None:
        task.selector = body.selector
        changes.append("selector")
    if body.max_depth is not None:
        task.max_depth = body.max_depth
        changes.append("max_depth")
    if body.schedule is not None:
        task.schedule = body.schedule.strip()
        changes.append("schedule")
    if provided_ids or provided_single:
        new_ids = document_departments.validate_department_ids(db, merged_ids)
        crawl_task_departments.set_crawl_task_departments(db, task, new_ids)
        changes.append("target_department_ids")
    if body.enabled is not None:
        task.enabled = body.enabled
        task.status = "idle" if body.enabled else "disabled"
        changes.append("enabled")

    db.add(task)
    db.commit()
    # 调度联动：启用且配置 cron 才注册
    if task.enabled and task.schedule:
        _schedule_task(scheduler, task)
    else:
        _remove_task(scheduler, task.id)
    log_action(db, current_user, "crawl_task_update", "crawl_task", task.id,
               {"name": task.name, "changes": changes}, client_ip(request))
    return schemas.ok(crawl_task_to_dict(task))


@router.delete("/{task_id}")
def delete_crawl_task(task_id: int,
                      request: Request,
                      db: Session = Depends(get_db),
                      current_user: models.User = Depends(require_admin)):
    """删除任务（同步移除 cron job，写审计）。"""
    task = db.get(models.CrawlTask, task_id)
    if task is None:
        raise not_found("爬虫任务不存在")
    _remove_task(scheduler, task.id)
    log_action(db, current_user, "crawl_task_delete", "crawl_task", task.id,
               {"name": task.name}, client_ip(request))
    # S7 D7：连接表关联为 viewonly，必须应用层显式清理后再删任务
    db.query(models.CrawlTaskDepartment).filter(
        models.CrawlTaskDepartment.task_id == task.id).delete(
        synchronize_session=False)
    db.delete(task)
    db.commit()
    return schemas.ok({"id": task_id, "deleted": True})


@router.post("/{task_id}/run")
def run_crawl_task_now(task_id: int,
                       db: Session = Depends(get_db),
                       current_user: models.User = Depends(require_admin)):
    """手动立即执行：同步调用 run_crawl_task，返回本次运行结果摘要。"""
    task = db.get(models.CrawlTask, task_id)
    if task is None:
        raise not_found("爬虫任务不存在")
    run_log = run_crawl_task(db, task_id, user=current_user)
    if run_log is None:
        raise bad_request("任务正在运行中，请稍后再试")
    return schemas.ok(crawl_run_log_to_dict(run_log))


@router.get("/{task_id}/logs")
def crawl_task_logs(task_id: int, page: int = 1, page_size: int = 20,
                    db: Session = Depends(get_db),
                    current_user: models.User = Depends(require_admin)):
    """任务运行日志（分页，倒序）。"""
    if db.get(models.CrawlTask, task_id) is None:
        raise not_found("爬虫任务不存在")
    page = max(1, page)
    page_size = min(max(1, page_size), 100)
    q = db.query(models.CrawlRunLog).filter(models.CrawlRunLog.task_id == task_id)
    items, total = schemas.paginate(
        q.order_by(models.CrawlRunLog.started_at.desc()), page, page_size)
    return schemas.ok({"total": total, "page": page, "page_size": page_size,
                       "items": [crawl_run_log_to_dict(l) for l in items]})
