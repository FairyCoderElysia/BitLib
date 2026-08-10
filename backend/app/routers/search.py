# -*- coding: utf-8 -*-
"""检索路由（spec §10.6 / F3 / F20）：GET /api/search、热词榜、输入联想。"""
import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Optional

import jieba
from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from .. import models, schemas
from ..audit import client_ip
from ..cache import cache
from ..db import get_db
from ..deps import get_current_user
from ..errors import bad_request
from ..search_service import hybrid_search
from ..summary import get_display_summary
from ..visibility import dept_visible

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/search", tags=["search"])

CACHE_TTL = 600  # 热点搜索缓存秒数

# 热词榜 / 联想缓存（F20）
HOT_WORDS_CACHE_KEY = "hotwords:all"   # 全局（查询词不绑定部门）
HOT_WORDS_TTL = 600
SUGGEST_TTL = 300
SUGGEST_PAGE_SIZE = 8
HOT_WORDS_WINDOW_DAYS = 30             # 近 30 天搜索日志窗口

# 常用中文停用词（过滤热词榜噪声，spec §3 F20）
STOP_WORDS = frozenset(
    "的了是在我有和就都而及与或一个上也很到说要去你会着没有看好不那等"
)
# 常见双字噪声词（F20 热词质量优化，eval-6 残留建议）
STOP_WORDS_2 = frozenset(
    "如何 我们 你们 他们 这个 那个 什么 怎么 为什么 请问 介绍 需要 可以 应该 是否 "
    "已经 还是 不过 但是 因为 所以 如果 虽然 然后 之后 之前 关于 对于 以及 通过 "
    "进行 使用 一个 一些 一种 可能 没有 不是 不会 不能 知道 了解 查看 查找 怎样 "
    "哪些 哪个 里面 上面 下面 时候 地方 问题 情况 方法 方式 步骤 流程 内容 信息 "
    "功能 管理 搜索 查询 下载 上传 查看 编辑 删除 添加 设置 配置 提供 支持 包括 "
    "需要 要求 说明 处理 操作 进入 打开 显示 返回 当前 全部 所有 相关 其他 各种 "
    "不同 主要 基本 具体 建议 方案 计划 目标 结果 效果 影响 数据 模型 算法 服务 "
    "平台 版本 状态 类型 名称 地址 时间 日期 数量 大小 范围 部分 方面 领域 方向 "
    "列表 页面 界面 按钮 窗口 提示 错误 成功 失败 正常 异常 有效 无效 能够 必须 "
    "非常 比较 相当 更加 特别 完全 很多 大量 少数 个别 相同 相似 另外 此外 同时 "
    "现在 目前 未来 过去 今天 明天 昨天 今年 明年 去年 每周 每天 每年 一次 两次 "
    "多次 首次 再次 开头 中间 结尾 前面 后面 左边 右边 内部 外部 本地 远程 线上 "
    "线下 正式 临时 永久 默认 自定义 手动 自动 批量 单个 新增 更新 统计 分析 导出 "
    "导入 打印 保存 取消 确认 提交 刷新 加载 等待 完成 开始 结束 暂停 继续 停止 "
    "启动 关闭 连接 断开 登录 注册 退出 密码 账号 用户名 邮箱 手机 验证码 忘记 "
    "记住 同意 拒绝 接受 忽略 跳过 下一步 上一步 首页 上一页 下一页 最后 第一 "
    "第二 第三 每个 任意 某个 别的 更多 更少 更大 更小 更高 更低 更快 更慢 更好 "
    "更差 尤其 特别 甚至 几乎 大约 大概 左右 上下 前后 附近 周围 旁边 中间 中心 "
    "边缘 角落 顶部 底部 左侧 右侧 中部 里面 外面 以后 以前 最近 最新 最早 首先 "
    "其次 还有 以及 或者 就是 只是 可是 然而 因此 由于 如果 假如 假设 即使 尽管 "
    "无论 不管 除非 只要 只有 既然 与其 宁可 宁愿 只好 只能 不必 无需 不用 谢谢 "
    "抱歉 没关系 好的 嗯 啊 哦 唉 呀 呢 吧 嘛 啦"
)


def _document_snapshot(doc: models.Document) -> dict:
    return {
        "id": doc.id, "title": doc.title, "file_name": doc.file_name,
        "file_type": doc.file_type, "file_size": doc.file_size,
        "status": doc.status, "is_featured": doc.is_featured,
        "source": doc.source, "department_id": doc.department_id,
        "summary": get_display_summary(doc),  # F17：文档摘要（有则用，否则截取）
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


@router.get("")
def search(
    q: str,
    request: Request,
    page: int = 1,
    page_size: int = 20,
    department_id: Optional[int] = None,
    file_type: Optional[str] = None,
    source: Optional[str] = None,
    is_featured: Optional[bool] = None,
    sort: str = "relevance",  # relevance / time
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """关键词+语义混合检索（权限过滤在召回阶段，重排后兜底校验）。"""
    q = q.strip()
    if not q:
        raise bad_request("查询词不能为空")
    if page < 1 or page_size < 1 or page_size > 100:
        raise bad_request("分页参数非法")

    # 跨部门筛选仅 admin 可用（spec §10.6）
    if department_id is not None and user.role != models.ROLE_ADMIN:
        raise bad_request("仅管理员可按部门筛选")

    cache_key = (f"search:{user.id}:{q}:{department_id}:{file_type}:{source}:"
                 f"{is_featured}:{sort}:{page}:{page_size}")
    cached = cache.get(cache_key)
    if cached is not None:
        items = _filter_cache(db, user, cached["items"])
        if len(items) == len(cached["items"]) and items:
            return schemas.ok({
                "total": cached["total"], "page": page, "page_size": page_size,
                "items": items, "cached": True,
            })

    hits = hybrid_search(db, user, q, limit=20)
    results = []
    for h in hits:
        doc = h["document"]
        # 筛选参数
        if file_type and doc.file_type != file_type:
            continue
        if source and doc.source != source:
            continue
        if is_featured is not None and doc.is_featured != is_featured:
            continue
        results.append({"doc": doc, "snippet": h["snippet"], "score": h["score"]})

    if sort == "time":
        results.sort(key=lambda r: r["doc"].created_at, reverse=True)

    total = len(results)
    start = (page - 1) * page_size
    items = [_document_snapshot(r["doc"]) | {"snippet": r["snippet"],
                                             "score": round(r["score"], 4)}
             for r in results[start:start + page_size]]

    # 记录 SearchLog（热词数据源，P2）
    try:
        db.add(models.SearchLog(user_id=user.id, query=q, hit_count=total))
        db.commit()
    except Exception as exc:
        logger.warning("SearchLog 写入失败: %s", exc)
        db.rollback()

    if page == 1 and items:
        cache.set(cache_key, {"total": total, "items": items}, CACHE_TTL)
    return schemas.ok({"total": total, "page": page, "page_size": page_size,
                       "items": items, "cached": False})


def _filter_cache(db: Session, user: models.User, items: list[dict]) -> list[dict]:
    """缓存有效性校验（spec F16：删除/下架后缓存不返回过期结果）。

    对 items 的文档 id 批量校验仍 approved 且当前用户可见，失效项剔除；
    全部失效返回空（触发回源重算）。
    """
    if not items:
        return []
    ids = [it["id"] for it in items]
    dept_cond = "1=1" if user.role == models.ROLE_ADMIN else \
        "(department_id IS NULL OR department_id = :dept)"
    # ids 来自缓存快照（int），内联避免 SQLite text() expanding IN 问题
    ids_str = ",".join(str(i) for i in ids)
    rows = db.execute(
        text(f"SELECT id FROM document WHERE id IN ({ids_str}) "
             f"AND status='approved' AND {dept_cond}"),
        {"dept": user.department_id},
    ).fetchall()
    valid = {r[0] for r in rows}
    return [it for it in items if it["id"] in valid]


def _is_junk_word(word: str) -> bool:
    """热词过滤：单字 / 停用词 / 纯数字或纯符号丢弃（保留中文/字母词条）。"""
    if len(word) < 2:
        return True
    if word in STOP_WORDS or word in STOP_WORDS_2:
        return True
    # 不含任何字母或汉字（纯数字、纯符号、纯空白）视为噪声
    if not any(ch.isalpha() for ch in word):
        return True
    return False


@router.get("/hot-words")
def hot_words(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """热词榜（spec F20）：聚合近 30 天 SearchLog.query → jieba 分词。

    全局聚合（查询词本身不绑定部门，admin 与普通用户同一榜单）；
    仅返回分词词条（≥2 字符、滤停用词/纯数字符号），不返回完整查询词，
    避免泄露文档内容。SearchLog 为空返回空数组。
    """
    cached = cache.get(HOT_WORDS_CACHE_KEY)
    if cached is not None:
        return schemas.ok({"items": cached["items"], "cached": True})

    since = datetime.utcnow() - timedelta(days=HOT_WORDS_WINDOW_DAYS)
    rows = db.execute(
        text("SELECT query FROM search_log WHERE created_at >= :since"),
        {"since": since},
    ).fetchall()

    counter: Counter = Counter()
    for (query,) in rows:
        if not query:
            continue
        for word in jieba.cut_for_search(query):
            word = word.strip()
            if _is_junk_word(word):
                continue
            counter[word] += 1

    items = [w for w, _ in counter.most_common(10)]
    # 空结果不缓存：避免 SearchLog 由空转非空后仍命中空缓存（缓存污染）
    if items:
        cache.set(HOT_WORDS_CACHE_KEY, {"items": items}, HOT_WORDS_TTL)
    return schemas.ok({"items": items, "cached": False})


@router.get("/suggest")
def suggest(
    q: str = "",
    page_size: int = SUGGEST_PAGE_SIZE,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """输入联想（spec F20）：按当前用户可见性过滤 approved 文档标题。

    前缀命中（title LIKE 'q%'）优先，不足时用标题包含 q（LIKE '%q%'）补齐，
    去重后最多返回 SUGGEST_PAGE_SIZE 条；q 为空返回空数组。
    缓存按用户隔离（key 带 user.id），避免不同部门用户复用彼此标题。
    """
    q = q.strip()
    if not q:
        return schemas.ok({"items": []})
    page_size = max(1, min(int(page_size), SUGGEST_PAGE_SIZE))

    cache_key = f"suggest:{user.id}:{q}"
    cached = cache.get(cache_key)
    if cached is not None:
        items = cached["items"]
        # 可见性兜底（Evaluator 发现：文档下架/改部门后有 300s 泄露窗口）
        if items:
            ids = [it["id"] for it in items]
            dept_cond = "1=1" if user.role == models.ROLE_ADMIN else \
                "(department_id IS NULL OR department_id = :dept)"
            ids_str = ",".join(str(i) for i in ids)
            rows = db.execute(
                text(f"SELECT id FROM document WHERE id IN ({ids_str}) "
                     f"AND status='approved' AND {dept_cond}"),
                {"dept": user.department_id},
            ).fetchall()
            valid = {r[0] for r in rows}
            items = [it for it in items if it["id"] in valid]
        return schemas.ok({"items": items, "cached": True})

    base = db.query(models.Document).filter(
        models.Document.status == models.STATUS_APPROVED)
    if user.role != models.ROLE_ADMIN:
        base = base.filter(or_(
            models.Document.department_id.is_(None),
            models.Document.department_id == user.department_id,
        ))

    items: list[dict] = []
    seen: set[str] = set()

    def _append(docs) -> None:
        """追加候选：去重 + 二次可见性校验（与 dept_visible 口径一致）。"""
        for doc in docs:
            if len(items) >= page_size:
                return
            if doc.title in seen or not dept_visible(user, doc):
                continue
            seen.add(doc.title)
            items.append({"id": doc.id, "title": doc.title})

    # 前缀命中优先，不足用包含命中补齐（转义 %/_ 通配符，eval-6 残留建议）
    escaped_q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    _append(base.filter(models.Document.title.like(f"{escaped_q}%", escape="\\")).all())
    if len(items) < page_size:
        _append(base.filter(models.Document.title.like(f"%{escaped_q}%", escape="\\")).all())

    # 空结果不缓存：避免新增/可见文档后联想仍命中空缓存（缓存污染）
    if items:
        cache.set(cache_key, {"items": items}, SUGGEST_TTL)
    return schemas.ok({"items": items, "cached": False})
