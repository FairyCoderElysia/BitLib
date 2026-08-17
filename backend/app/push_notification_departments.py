# -*- coding: utf-8 -*-
"""推送目标部门集合的单一读写入口（S7 F3）。

所有修改推送目标部门集合的路径必须经 set_push_departments 落库（主列
department_id 与连接表 push_notification_department 同事务同步）；读取统一经
get_push_dept_ids / get_push_dept_pairs / attach_* 收敛口径。

依赖方向：仅依赖 models，不依赖任何 router（避免环）。
"""
from . import models


def _primary_of(ids):
    return min(ids) if ids else None


def get_push_dept_ids(n) -> list:
    """按 id 升序返回推送目标部门 id 列表（兼容未迁移/直接改列的旧数据）。"""
    cached = getattr(n, "_s7_dept_ids", None)
    if cached is not None:
        return list(cached)

    depts = None
    try:
        depts = list(n.departments)
    except Exception:
        depts = None

    if depts:
        ids = sorted({d.id for d in depts if d is not None and d.id is not None})
        if n.department_id is None or n.department_id in ids:
            return ids
        return [n.department_id]
    if n.department_id is not None:
        return [n.department_id]
    return []


def get_push_dept_pairs(n) -> list:
    """返回 [(dept_id, dept_name)]（按 id 升序），名称缺失时置 None。"""
    cached = getattr(n, "_s7_dept_pairs", None)
    if cached is not None:
        return list(cached)
    ids = get_push_dept_ids(n)
    if not ids:
        return []
    name_by_id = {}
    try:
        for d in n.departments:
            if d is not None and d.id is not None:
                name_by_id[d.id] = d.name
    except Exception:
        pass
    return [(did, name_by_id.get(did)) for did in ids]


def attach_push_department_sets(db, notifs) -> None:
    """批量预取目标部门集合与名称，写入 n._s7_dept_ids / n._s7_dept_pairs。"""
    notifs = [x for x in (notifs or []) if x is not None]
    if not notifs:
        return
    nids = sorted({x.id for x in notifs if x.id is not None})
    by_n = {}
    ids_pool = set()
    if nids:
        rows = db.query(models.PushNotificationDepartment).filter(
            models.PushNotificationDepartment.notification_id.in_(nids)).all()
        for row in rows:
            by_n.setdefault(row.notification_id, []).append(row.department_id)
            ids_pool.add(row.department_id)
    name_by_id = {}
    if ids_pool:
        name_by_id = {d.id: d.name for d in db.query(models.Department).filter(
            models.Department.id.in_(list(ids_pool))).all()}

    for n in notifs:
        ids = sorted(set(by_n.get(n.id, [])))
        if n.department_id is None:
            if ids:
                ids = []
        elif n.department_id not in ids:
            ids = [n.department_id]
            name_by_id.setdefault(n.department_id, None)
        n._s7_dept_ids = ids
        n._s7_dept_pairs = [(i, name_by_id.get(i)) for i in ids]


def set_push_departments(db, n, department_ids) -> list:
    """单一写入口：以给定集合整体替换推送目标部门。

    - 删除旧连接行 → 插入新连接行 → 同步主列 department_id（=最小 id；空=None）。
    - 参数应为已去重校验的合法部门 id（调用方先 validate）。
    - 调用方负责 commit。
    """
    ids = sorted(set(int(x) for x in (department_ids or [])))
    db.query(models.PushNotificationDepartment).filter(
        models.PushNotificationDepartment.notification_id == n.id).delete(
        synchronize_session=False)
    for did in ids:
        db.add(models.PushNotificationDepartment(notification_id=n.id, department_id=did))
    n.department_id = _primary_of(ids)
    n._s7_dept_ids = ids
    n._s7_dept_pairs = None
    return ids
