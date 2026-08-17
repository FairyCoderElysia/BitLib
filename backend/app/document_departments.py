# -*- coding: utf-8 -*-
"""文档多部门可见性的单一读写入口（S7）。

所有修改文档可见部门集合的路径必须经 set_doc_departments 落库（主表 department_id
与连接表 document_department、FTS 主部门快照同事务同步）；所有读取统一经
visible_document_cond / visible_document_filter / get_doc_dept_pairs 收敛口径，
禁止直接信任 Document.department_id 做授权判断。

依赖方向：仅依赖 models / fts / errors，不依赖任何 router（避免环）。
"""
import json
import logging

from . import fts, models
from .errors import bad_request

logger = logging.getLogger(__name__)

# 单文档可见部门数量上限。契约约定：MAX_DOC_DEPARTMENTS = min(当前已存在部门总数, 100)。
# 此处保存 100 的硬上限；生效值经 get_max_doc_departments 动态取 min。
# 测试可通过 monkeypatch 修改本模块的 MAX_DOC_DEPARTMENTS 以验证超限 400。
MAX_DOC_DEPARTMENTS = 100

# 搜索/联想缓存代际标记：任何改部门写操作 bump 后，旧缓存键失效，
# 保证 C8「新部门立即可召回、旧部门立即不可召回」对缓存命中路径同样成立。
# 进程内 int，无需额外依赖。
_DEPT_EPOCH = 0


def get_dept_epoch() -> int:
    """返回当前部门可见性代际。搜索/联想缓存键必须拼入此值。"""
    return _DEPT_EPOCH


def bump_dept_epoch() -> int:
    """部门可见性发生变化时递增代际（改部门写路径调用）。"""
    global _DEPT_EPOCH
    _DEPT_EPOCH += 1
    return _DEPT_EPOCH


def get_max_doc_departments(db) -> int:
    """返回当前生效的部门数量上限 = min(部门总数, MAX_DOC_DEPARTMENTS)。"""
    total = db.query(models.Department).count()
    return min(total, MAX_DOC_DEPARTMENTS)


def parse_department_ids(value):
    """解析 Form 字段 department_ids（JSON 数组字符串）。

    - None → None（调用方按“未提供”走回退）；
    - 非法 JSON / 非数组 / 非整数元素 → 抛 400 BizError（统一业务错误响应）。
    """
    if value is None:
        return None
    if isinstance(value, str):
        raw = value.strip()
        if raw == "":
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise bad_request("department_ids 必须为 JSON 数组字符串")
    else:
        data = value
    if not isinstance(data, list):
        raise bad_request("department_ids 必须为数组")
    ids = []
    for item in data:
        if type(item) is not int:
            raise bad_request("department_ids 必须为整数数组")
        ids.append(item)
    return ids


def validate_department_ids(db, department_ids) -> list:
    """去重、校验部门存在性与数量上限；抛 400（部门不存在 / 超出上限）。返回保序去重列表。"""
    ids = list(dict.fromkeys([int(x) for x in (department_ids or [])]))
    if not ids:
        return []
    limit = get_max_doc_departments(db)
    if len(ids) > limit:
        raise bad_request(f"可见部门数量超出上限（最多 {limit} 个）")
    for did in ids:
        if db.get(models.Department, did) is None:
            raise bad_request("部门不存在")
    return ids


def _primary_of(ids) -> int | None:
    """集合的最小 id = 主部门；空集合 = None（公开）。"""
    return min(ids) if ids else None


def get_doc_dept_ids(doc) -> list:
    """按 id 升序返回文档可见部门 id 列表（兼容未迁移/直接改列的旧数据）。"""
    cached = getattr(doc, "_s7_dept_ids", None)
    if cached is not None:
        return list(cached)

    depts = None
    try:
        depts = list(doc.departments)
    except Exception:
        depts = None

    if depts:
        ids = sorted({d.id for d in depts if d is not None and d.id is not None})
        if doc.department_id is None or doc.department_id in ids:
            return ids
        return [doc.department_id]
    if doc.department_id is not None:
        return [doc.department_id]
    return []


def _dept_name(doc, dept_id: int) -> str | None:
    """尽量从 department 关系获取单部门名（多部门名称由 attach 批量预取提供）。"""
    dep = getattr(doc, "department", None)
    if dep is not None and dep.id == dept_id:
        return dep.name
    return None


def get_doc_dept_pairs(doc) -> list:
    """返回 [(dept_id, dept_name)]（按 id 升序），名称缺失时置 None。"""
    cached = getattr(doc, "_s7_dept_pairs", None)
    if cached is not None:
        return list(cached)
    ids = get_doc_dept_ids(doc)
    if not ids:
        return []
    return [(did, _dept_name(doc, did)) for did in ids]


def attach_department_sets(db, docs) -> None:
    """批量预取可见部门集合与名称，写入 doc._s7_dept_ids / doc._s7_dept_pairs。"""
    docs = [d for d in (docs or []) if d is not None]
    if not docs:
        return
    doc_ids = sorted({d.id for d in docs if d.id is not None})
    by_doc = {}
    ids_pool = set()
    if doc_ids:
        rows = db.query(models.DocumentDepartment).filter(
            models.DocumentDepartment.document_id.in_(doc_ids)).all()
        for row in rows:
            by_doc.setdefault(row.document_id, []).append(row.department_id)
            ids_pool.add(row.department_id)
    name_by_id = {}
    if ids_pool:
        name_by_id = {d.id: d.name for d in db.query(models.Department).filter(
            models.Department.id.in_(list(ids_pool))).all()}

    for d in docs:
        ids = sorted(set(by_doc.get(d.id, [])))
        # 快照一致性自愈：主部门列与连接表集合不符 → 以最近写入主列为准
        if d.department_id is None:
            if ids:
                ids = []
        elif d.department_id not in ids:
            ids = [d.department_id]
            name_by_id.setdefault(d.department_id, _dept_name(d, d.department_id))
        d._s7_dept_ids = ids
        d._s7_dept_pairs = [(i, name_by_id.get(i)) for i in ids]


def set_doc_departments(db, doc, department_ids, sync_fts=True,
                        invalidate_cache=False):
    """单一写入口：以给定集合整体替换文档可见部门。

    - 删除旧连接行 → 插入新连接行 → 同步主部门列（=最小 id；空=None）
      → 可选同步 FTS 主部门快照；调用方负责 commit。
    - invalidate_cache=True 时递增缓存代际（改部门即时生效用）。
    - 参数应为已去重校验的合法部门 id（调用方先 validate_department_ids）。
    """
    ids = sorted(set(int(x) for x in (department_ids or [])))
    db.query(models.DocumentDepartment).filter(
        models.DocumentDepartment.document_id == doc.id).delete(
        synchronize_session=False)
    for did in ids:
        db.add(models.DocumentDepartment(document_id=doc.id, department_id=did))
    doc.department_id = _primary_of(ids)
    if sync_fts:
        try:
            fts.sync_document(db, doc)
        except Exception:
            logger.exception("set_doc_departments：FTS 主部门快照同步失败（doc=%s）", doc.id)
    doc._s7_dept_ids = ids
    if invalidate_cache:
        bump_dept_epoch()
    return ids


def sync_primary_department(db, doc) -> int | None:
    """仅按连接表重算主部门列（迁移/自愈用），返回新 department_id。调用方 commit。"""
    rows = db.query(models.DocumentDepartment.department_id).filter(
        models.DocumentDepartment.document_id == doc.id).all()
    ids = sorted({r[0] for r in rows if r[0] is not None})
    doc.department_id = _primary_of(ids)
    return doc.department_id


def visible_document_cond(user, doc_id_col="rowid"):
    """返回 (SQL 片段, 参数 dict)，供裸 SQL 查询复用文档可见性。

    - admin → (1=1, {})；
    - 其余 → 公开（主部门列 IS NULL 等价集合空，由写路径保证）或集合含本部门。
    """
    if user.role == models.ROLE_ADMIN:
        return "1=1", {}
    clause = (f"{doc_id_col} IN "
              f"(SELECT id FROM document WHERE department_id IS NULL)")
    params = {}
    if user.department_id is not None:
        clause = ("(" + clause +
                  f" OR {doc_id_col} IN (SELECT document_id FROM document_department "
                  f"WHERE department_id = :s7_dept))")
        params = {"s7_dept": int(user.department_id)}
    return clause, params


def visible_document_filter(query, user):
    """对 Document ORM 查询追加可见性过滤（admin 不加，等价旧“全量”口径）。"""
    from sqlalchemy import and_, exists

    if user.role == models.ROLE_ADMIN:
        return query
    cond = models.Document.department_id.is_(None)
    if user.department_id is not None:
        dd = models.DocumentDepartment
        cond = cond | exists().where(and_(
            dd.document_id == models.Document.id,
            dd.department_id == user.department_id,
        ))
    return query.filter(cond)


def managed_document_filter(query, user):
    """对 Document ORM 查询追加 dept_admin 管理边界（集合非空且含本部门）。

    admin 全量；非 dept_admin 恒空；公开文档（集合空）不纳入 dept_admin。
    """
    from sqlalchemy import and_, exists

    if user.role == models.ROLE_ADMIN:
        return query
    if user.role != models.ROLE_DEPT_ADMIN:
        from sqlalchemy import false
        return query.filter(false())
    dd = models.DocumentDepartment
    return query.filter(exists().where(and_(
        dd.document_id == models.Document.id,
        dd.department_id == user.department_id,
    )))
