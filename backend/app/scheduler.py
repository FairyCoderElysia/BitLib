# -*- coding: utf-8 -*-
"""定时任务调度（M5，spec §6.2 / F9）。

APScheduler BackgroundScheduler：settings.enable_scheduler=True 时启动，
加载所有 enabled 且 schedule 非空的 CrawlTask 并按 cron 注册。
crawl 路由通过 _schedule_task / _remove_task 对单个任务动态增删调度。
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import models
from .config import settings
from .crawler import run_crawl_task
from .db import SessionLocal

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler()


def _job_id(task_id: int) -> str:
    return f"crawl_task_{task_id}"


def _job_func(task_id: int) -> None:
    """调度任务函数：独立会话执行爬虫任务（定时触发无请求上下文）。"""
    db = SessionLocal()
    try:
        run_crawl_task(db, task_id)
    except Exception as exc:
        logger.exception("定时爬虫任务 %s 异常：%s", task_id, exc)
    finally:
        db.close()


def _schedule_task(sched, task) -> None:
    """为单个任务注册 cron job（幂等：先移除旧 job）。未启用或 schedule 空则移除。"""
    if task is None or not getattr(task, "enabled", False) or not (task.schedule or "").strip():
        _remove_task(sched, task.id if task is not None else 0)
        return
    try:
        trigger = CronTrigger.from_crontab(task.schedule.strip())
    except Exception as exc:
        logger.warning("任务 %s cron 表达式 %r 非法，跳过调度：%s",
                       task.id, task.schedule, exc)
        return
    _remove_task(sched, task.id)
    sched.add_job(_job_func, trigger, args=[task.id], id=_job_id(task.id),
                  replace_existing=True, coalesce=True, max_instances=1,
                  misfire_grace_time=60)
    logger.info("已注册任务 %s 的 cron 调度：%s", task.id, task.schedule)


def _remove_task(sched, task_id: int) -> None:
    """移除任务的 cron job（job 不存在时静默）。"""
    try:
        sched.remove_job(_job_id(task_id))
    except Exception:
        pass


def load_tasks(db) -> int:
    """加载所有 enabled 且 schedule 非空的 CrawlTask 并注册。返回注册任务数。"""
    count = 0
    for task in db.query(models.CrawlTask).filter(
            models.CrawlTask.enabled.is_(True)).all():
        if not (task.schedule or "").strip():
            continue
        _schedule_task(scheduler, task)
        count += 1
    return count


def start_scheduler() -> bool:
    """settings.enable_scheduler=True 时启动调度器并加载任务。返回调度器是否在运行。"""
    if not settings.enable_scheduler:
        logger.info("爬虫定时调度未启用（ENABLE_SCHEDULER=false）")
        return False
    if scheduler.running:
        return True
    db = SessionLocal()
    try:
        loaded = load_tasks(db)
    finally:
        db.close()
    scheduler.start()
    logger.info("APScheduler 已启动，加载 %d 个爬虫定时任务", loaded)
    return True


def shutdown_scheduler() -> None:
    """应用关闭时停止调度器。"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("APScheduler 已停止")
