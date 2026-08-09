# -*- coding: utf-8 -*-
"""爬虫通用模块（M5，spec §6.2 爬虫入库流 / F9 / §7.2 SSRF 防护）。

- fetch_page：Scrapling Fetcher.get 优先，StealthyFetcher 兜底（反爬）
- extract_text：配置 selector 时取 CSS 选中元素文本，否则 BeautifulSoup 整页正文
- extract_links：提取页面链接并按域名白名单过滤（SSRF 防护）
- run_crawl_task：任务主流程——BFS 按 max_depth 抓取 → 清洗 → sha256 去重
  → 直接入库（ingest_text，不走审批）→ CrawlRunLog + 审计
"""
import hashlib
import logging
import threading
from collections import deque
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import models
from .audit import log_action
from .cleaning import MIN_TEXT_LEN, clean_text
from .ingest import ingest_text

logger = logging.getLogger(__name__)

# 每域名最多抓取页数（防失控）
MAX_PAGES_PER_DOMAIN = 50

try:
    from scrapling import Fetcher, StealthyFetcher
except Exception:  # pragma: no cover - 依赖缺失时爬虫功能不可用
    Fetcher = None
    StealthyFetcher = None

# 任务级互斥锁：防止手动 run 与定时调度同时执行同一任务
_LOCKS: dict[int, threading.Lock] = {}
_LOCK_GUARD = threading.Lock()


def _task_lock(task_id: int) -> threading.Lock:
    with _LOCK_GUARD:
        if task_id not in _LOCKS:
            _LOCKS[task_id] = threading.Lock()
        return _LOCKS[task_id]


def fetch_page(url: str, timeout: int = 15):
    """抓取单页。Fetcher.get 优先，异常时 StealthyFetcher 兜底（反爬）。

    返回 scrapling Response 或 None（网络/状态码异常）。
    """
    if Fetcher is None:
        logger.warning("scrapling 未安装，无法抓取 %s", url)
        return None
    try:
        resp = Fetcher.get(url, timeout=timeout)
    except Exception as exc:
        logger.info("Fetcher.get 失败 %s（尝试 Stealthy 兜底）: %s", url, exc)
        resp = _stealthy_fetch(url, timeout)
    if resp is None:
        return None
    status = getattr(resp, "status", None)
    if status is not None and status >= 400:
        logger.warning("抓取返回非 2xx 状态 %s: %s", status, url)
        return None
    return resp


def _stealthy_fetch(url: str, timeout: int):
    """StealthyFetcher 兜底：0.4.x 提供 fetch（timeout 单位毫秒）；早期版本为 get（秒）。"""
    if StealthyFetcher is None:
        return None
    try:
        fetch = getattr(StealthyFetcher, "fetch", None)
        if fetch is not None:
            return fetch(url, timeout=timeout * 1000, headless=True)
        get = getattr(StealthyFetcher, "get", None)
        if get is not None:
            return get(url, timeout=timeout)
    except Exception as exc:
        logger.warning("Stealthy 兜底抓取失败 %s: %s", url, exc)
    return None


def extract_text(resp, selector: str = "") -> str:
    """提取页面正文。

    selector 配置时取 CSS 选中元素的全部文本；否则 BeautifulSoup 整页正文提取。
    提取失败返回空串（由调用方按过短拦截处理）。
    """
    if resp is None:
        return ""
    if selector:
        try:
            el = resp.css(selector).first
            if el is not None:
                return el.get_all_text(separator="\n")
        except Exception as exc:
            logger.warning("CSS 选择器 %r 提取失败：%s", selector, exc)
    try:
        soup = BeautifulSoup(resp.body, "html.parser")
        return soup.get_text("\n", strip=True)
    except Exception as exc:
        logger.warning("BeautifulSoup 正文提取失败：%s", exc)
        return ""


def extract_links(resp, base_url: str, allowed_domains) -> list:
    """提取页面 a[href] 并做域名白名单过滤（SSRF 防护，spec §7.2）。

    仅保留 http/https 且 host 命中 allowed_domains（含子域名）的链接；
    起始 URL 域名由 run_crawl_task 并入白名单。
    """
    if resp is None or not allowed_domains:
        return []
    allowed = {str(d).strip().lower().lstrip(".")
               for d in allowed_domains if d and str(d).strip()}
    links = set()
    try:
        for a in resp.css("a[href]"):
            href = (a.attrib.get("href") or "").strip()
            if not href or href.lower().startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https"):
                continue
            host = (parsed.hostname or "").lower()
            if _host_in_domains(host, allowed):
                links.add(absolute)
    except Exception as exc:
        logger.warning("链接提取失败 %s: %s", base_url, exc)
    return sorted(links)


def _host_in_domains(host: str, domains) -> bool:
    """host 是否命中域名白名单（host 本身或子域名）。"""
    for d in domains:
        if host == d or host.endswith("." + d):
            return True
    return False


def _page_title(resp) -> str:
    """取页面 <title> 文本，失败返回空串。"""
    try:
        el = resp.css("title").first
        if el is not None:
            return el.get_all_text().strip()[:255]
    except Exception:
        pass
    return ""


def run_crawl_task(db, task_id: int, user=None):
    """执行爬虫任务主流程（BFS，同步执行）。

    :param db: 数据库会话
    :param task_id: 爬虫任务 ID
    :param user: 触发人（定时任务为 None；手动执行传当前用户，用于审计）
    :return: CrawlRunLog 记录；任务不存在或正在运行返回 None
    """
    task = db.get(models.CrawlTask, task_id)
    if task is None or task.status == "running":
        return None
    with _task_lock(task_id):
        return _do_run(db, task, user)


def _do_run(db, task, user):
    """实际执行（已在任务锁内）。"""
    task.status = "running"
    db.add(task)
    db.commit()

    run_log = models.CrawlRunLog(task_id=task.id,
                                 started_at=datetime.utcnow(), status="running")
    db.add(run_log)
    db.commit()
    db.refresh(run_log)

    fetched = ingested = skipped = 0
    errors = []
    try:
        start_urls = [str(u) for u in (task.start_urls or []) if u]
        allowed = [str(d) for d in (task.allowed_domains or [])]
        # 起始 URL 域名自动并入白名单（SSRF：仅任务配置域名 + 起始 URL 域名可抓）
        for su in start_urls:
            host = urlparse(su).hostname
            if host and host not in allowed:
                allowed.append(host)
        max_depth = max(0, int(task.max_depth or 1))
        selector = task.selector or ""
        target_dept = task.target_department_id

        visited = set()
        queue = deque((u, 0) for u in start_urls)
        per_domain = {}

        while queue:
            url, depth = queue.popleft()
            if url in visited:
                continue
            visited.add(url)
            host = (urlparse(url).hostname or "").lower()
            if per_domain.get(host, 0) >= MAX_PAGES_PER_DOMAIN:
                continue

            resp = fetch_page(url)
            if resp is None:
                errors.append(f"{url}: 抓取失败")
                continue
            fetched += 1
            per_domain[host] = per_domain.get(host, 0) + 1

            text = clean_text(extract_text(resp, selector))
            if len(text) < MIN_TEXT_LEN:
                skipped += 1  # 过短拦截（spec §7 F7：清洗后 <50 字符不入库）
            else:
                sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if db.query(models.Document).filter(
                        models.Document.file_hash == sha).first() is not None:
                    skipped += 1  # sha256 去重跳过（已入库内容）
                else:
                    title = _page_title(resp) or url
                    doc = models.Document(
                        title=title[:255],
                        file_name=title[:255] or url,
                        file_path="",                      # 爬虫无私有文件，正文存 content_text
                        file_type="txt",
                        file_size=len(text.encode("utf-8")),
                        file_hash=sha,
                        status=models.STATUS_PROCESSING,
                        source=models.SOURCE_CRAWL,
                        department_id=target_dept,         # 继承任务目标部门（空=公开，§2.5）
                        uploaded_by=task.created_by,
                    )
                    db.add(doc)
                    db.commit()
                    db.refresh(doc)
                    # 直接入库（爬虫为管理员授权行为，不走审批，spec §6.2）
                    ingest_text(db, doc, text)
                    if doc.status == models.STATUS_APPROVED:
                        ingested += 1
                    else:
                        errors.append(f"{url}: 入库失败 {doc.error_message}")

            # 深度内继续 BFS（无论本页是否入库/去重跳过，链接都需继续爬取）
            if depth < max_depth:
                for link in extract_links(resp, url, allowed):
                    if link not in visited:
                        queue.append((link, depth + 1))

        run_log.status = "success"
        if errors:
            run_log.error = "\n".join(errors[:20])[:2000]
    except Exception as exc:
        run_log.status = "failed"
        run_log.error = str(exc)[:2000]
        logger.exception("爬虫任务 %s 执行异常", task.id)
    finally:
        run_log.fetched_count = fetched
        run_log.ingested_count = ingested
        run_log.skipped_count = skipped
        run_log.finished_at = datetime.utcnow()
        db.add(run_log)
        task.status = "idle"
        task.last_run_at = run_log.finished_at
        db.add(task)
        db.commit()
        db.refresh(run_log)

    # 任务执行结果写审计（spec F9：任务创建/启停/执行均审计）
    log_action(db, user, "crawl_run", "crawl_task", task.id, {
        "name": task.name,
        "fetched": fetched, "ingested": ingested, "skipped": skipped,
        "status": run_log.status, "error": run_log.error,
    })
    return run_log
