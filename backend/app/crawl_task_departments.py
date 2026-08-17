# -*- coding: utf-8 -*-
"""爬虫任务目标部门集合的单一读写入口（S7 F2）。

所有修改爬虫任务目标部门集合的路径必须经 set_crawl_task_departments 落库
（主列 target_department_id 与连接表 crawl_task_department 同事务同步）；读取统一
经 get_crawl_task_dept_ids / get_crawl_task_dept_pairs / attach_* 收敛口径。

依赖方向：仅依赖 models，不依赖任何 router（避免环）。部门合法性校验复用
document_departments.validate_department_ids（同“去重/存在/上限”语义）。
"""
from . import models


def _primary_of(ids):
    """集合的最小 id = 主部门；空集合 = None（公开）。"""
    return min(ids) if ids else None


def get_crawl_task_dept_ids(task) -> list:
    """按 id 升序返回任务目标部门 id 列表（兼容未迁移/直接改列的旧数据）。"""
    cached = getattr(task, "_s7_dept_ids", None)
    if cached is not None:
        return list(cached)

    depts = None
    try:
        depts = list(task.target_departments)
    except Exception:
        depts = None

    if depts:
        ids = sorted({d.id for d in depts if d is not None and d.id is not None})
        if task.target_department_id is None or task.target_department_id in ids:
            return ids
        # 快照一致性自愈：主列与连接表不符（仅历史/直接改列会出现）→ 回退主列单值
        return [task.target_department_id]
    if task.target_department_id is not None:
        return [task.target_department_id]
    return []


def get_crawl_task_dept_pairs(task) -> list:
    """返回 [(dept_id, dept_name)]（按 id 升序），名称缺失时置 None。"""
    cached = getattr(task, "_s7_dept_pairs", None)
    if cached is not None:
        return list(cached)
    ids = get_crawl_task_dept_ids(task)
    if not ids:
        return []
    name_by_id = {}
    try:
        for d in task.target_departments:
            if d is not None and d.id is not None:
                name_by_id[d.id] = d.name
    except Exception:
        pass
    return [(did, name_by_id.get(did)) for did in ids]


def attach_crawl_task_department_sets(db, tasks) -> None:
    """批量预取目标部门集合与名称，写入 task._s7_dept_ids / task._s7_dept_pairs。"""
    tasks = [x for x in (tasks or []) if x is not None]
    if not tasks:
        return
    task_ids = sorted({x.id for x in tasks if x.id is not None})
    by_task = {}
    ids_pool = set()
    if task_ids:
        rows = db.query(models.CrawlTaskDepartment).filter(
            models.CrawlTaskDepartment.task_id.in_(task_ids)).all()
        for row in rows:
            by_task.setdefault(row.task_id, []).append(row.department_id)
            ids_pool.add(row.department_id)
    name_by_id = {}
    if ids_pool:
        name_by_id = {d.id: d.name for d in db.query(models.Department).filter(
            models.Department.id.in_(list(ids_pool))).all()}

    for task in tasks:
        ids = sorted(set(by_task.get(task.id, [])))
        # 快照一致性自愈：主列与连接表不符 → 以最近写入主列为准
        if task.target_department_id is None:
            if ids:
                ids = []
        elif task.target_department_id not in ids:
            ids = [task.target_department_id]
            name_by_id.setdefault(task.target_department_id, None)
        task._s7_dept_ids = ids
        task._s7_dept_pairs = [(i, name_by_id.get(i)) for i in ids]


def set_crawl_task_departments(db, task, department_ids) -> list:
    """单一写入口：以给定集合整体替换任务目标部门。

    - 删除旧连接行 → 插入新连接行 → 同步主列 target_department_id（=最小 id；空=None）。
    - 参数应为已去重校验的合法部门 id（调用方先 validate）。
    - 调用方负责 commit。
    """
    ids = sorted(set(int(x) for x in (department_ids or [])))
    db.query(models.CrawlTaskDepartment).filter(
        models.CrawlTaskDepartment.task_id == task.id).delete(
        synchronize_session=False)
    for did in ids:
        db.add(models.CrawlTaskDepartment(task_id=task.id, department_id=did))
    task.target_department_id = _primary_of(ids)
    task._s7_dept_ids = ids
    task._s7_dept_pairs = None
    return ids
