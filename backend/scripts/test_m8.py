# -*- coding: utf-8 -*-
"""Sprint 8 测试：爬虫增量更新（F22：新增/更新/跳过三分支 + 老库迁移 + FTS/搜索清理）。

覆盖（对应 contract-8 §4 验收）：
  1. 迁移幂等：用无 source_url/updated_count 列的老表启动 init_db() 不报错；
     PRAGMA 校验两列已补；重复 init_db() 不抛异常
  2. 新增：新 URL 首次 run → ingested=1、updated=0，doc.source_url==url、status=approved
  3. 跳过：同 URL 同内容再次 run → skipped=1、updated=0、文档数不变
  4. 更新：页面内容变化再 run → updated=1、doc id 不变、content_text/file_hash 更新、
     Chroma 新向量可查、FTS 新词命中、旧内容独有词不再命中、GET /api/search 命中新词
  5. 运行记录：logs/run 返回含 updated_count；任务列表 last_run 亦含
  6. 更新失败：embedding 抛异常 → doc 置 failed + error_message、计数进 errors 非 updated

运行：cd backend && python scripts/test_m8.py
依赖本地 embedding 模型（bge-small-zh-v1.5，M3 已下载，同 test_m5）。
"""
import http.server
import os
import shutil
import sys
import threading
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_ROOT = BACKEND_DIR / "data" / "test_m8"
TEST_DB = (TEST_ROOT / "app.db").as_posix()

os.environ["ADMIN_INITIAL_PASSWORD"] = "Admin@123456"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
os.environ["CHROMA_DIR"] = str(TEST_ROOT / "chroma")
os.environ["MAX_UPLOAD_MB"] = "5"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["EMBEDDING_MODE"] = "local"

from fastapi.testclient import TestClient  # noqa: E402

from app import models, vector_store  # noqa: E402
from app.cleaning import clean_text  # noqa: E402
from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.main import app  # noqa: E402
from sqlalchemy import text  # noqa: E402

ADMIN_PASSWORD = "Admin@123456"

# ---------------- 页面内容（v1 含"区块链/共识"，v2 改为"量子计算/超导"） ----------------

V1_TEXT = ("本文研究区块链共识机制的演进脉络，从工作量证明到权益证明的对比分析，"
           "以及分片技术与侧链方案在性能与安全之间的权衡取舍。"
           "共识协议决定了分布式账本的最终一致性，是区块链系统可靠运行的核心基础组件。")

V2_TEXT = ("本文介绍超导量子计算芯片的最新进展，包括量子比特的相干时间提升、"
           "纠错码的工程实现，以及低温控制系统对整体架构的关键支撑作用。"
           "量子计算在密码学与材料模拟等领域具有潜在优势，仍需持续投入研发突破瓶颈。")

CURRENT_BODY = V1_TEXT  # 测试中修改以模拟页面内容变化


def _page_html(body: str) -> str:
    return (f"<!DOCTYPE html><html><head><meta charset=\"utf-8\"><title>测试页面</title></head>"
            f"<body>{body}</body></html>")


class SiteHandler(http.server.BaseHTTPRequestHandler):
    """本地测试站点：返回 CURRENT_BODY 渲染的页面。"""

    def do_GET(self):
        body = _page_html(CURRENT_BODY).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默访问日志
        pass


# ---------------- 老库结构（无 Sprint8 新列） ----------------

_LEGACY_DOCUMENT_DDL = """
CREATE TABLE document (
    id INTEGER PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    file_name VARCHAR(255) NOT NULL,
    file_path VARCHAR(512) NOT NULL,
    file_type VARCHAR(16) NOT NULL,
    file_size INTEGER DEFAULT 0,
    file_hash VARCHAR(64) NOT NULL,
    content_text TEXT,
    summary TEXT,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    error_message TEXT,
    is_featured BOOLEAN,
    department_id INTEGER,
    source VARCHAR(16) NOT NULL DEFAULT 'upload',
    uploaded_by INTEGER,
    approver_id INTEGER,
    approved_at DATETIME,
    reject_reason TEXT,
    created_at DATETIME,
    updated_at DATETIME
)
"""

_LEGACY_RUN_LOG_DDL = """
CREATE TABLE crawl_run_log (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL,
    started_at DATETIME,
    finished_at DATETIME,
    fetched_count INTEGER DEFAULT 0,
    ingested_count INTEGER DEFAULT 0,
    skipped_count INTEGER DEFAULT 0,
    status VARCHAR(16) DEFAULT 'running',
    error TEXT,
    created_at DATETIME
)
"""


def _tokens(s: str) -> set:
    """jieba 分词集合（过滤标点/单字，用于 FTS 新旧词断言）。"""
    import jieba
    punct = set("，。！？、；：''""（）《》·—…")
    return {t for t in jieba.lcut(s) if len(t) >= 2 and not all(c in punct for c in t)}


def H(token):
    return {"Authorization": f"Bearer {token}"}


def main():
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    passed: list = []

    def check(name):
        passed.append(name)
        print(f"  PASS: {name}")

    def login(c, username, password):
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200 and r.json()["code"] == 0, r.text
        return r.json()["data"]["token"]

    def cols_of(table):
        with engine.connect() as conn:
            return [r[1] for r in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()]

    # ---------- 1. 老库迁移（先建无新列的老表，再启动 init_db） ----------
    with engine.begin() as conn:
        conn.execute(text(_LEGACY_DOCUMENT_DDL))
        conn.execute(text(_LEGACY_RUN_LOG_DDL))
    assert "source_url" not in cols_of("document"), "前置：老库 document 不应有 source_url"
    assert "updated_count" not in cols_of("crawl_run_log"), "前置：老库 crawl_run_log 不应有 updated_count"

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SiteHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        # TestClient 启动触发 lifespan → init_db → create_all + _migrate_columns（补列）+ FTS + 播种
        with TestClient(app) as c:
            # 迁移后：两列已补
            assert "source_url" in cols_of("document"), cols_of("document")
            assert "updated_count" in cols_of("crawl_run_log"), cols_of("crawl_run_log")
            # 幂等：再次 init_db 不抛异常（重复调用，列已存在跳过）
            init_db()
            assert "source_url" in cols_of("document") and \
                "updated_count" in cols_of("crawl_run_log")
            check("1. 老库迁移幂等：init_db 补列成功且重复调用不报错")

            token_admin = login(c, "admin", ADMIN_PASSWORD)

            # 创建爬虫任务（公开，无目标部门）
            r = c.post("/api/admin/crawl-tasks", headers=H(token_admin), json={
                "name": "增量抓取测试",
                "start_urls": [f"http://127.0.0.1:{port}/page"],
                "allowed_domains": ["127.0.0.1"],
                "max_depth": 0,
                "enabled": False,
            })
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            task_id = r.json()["data"]["id"]

            # ---------- 2. 首次 run：新增 ----------
            r = c.post(f"/api/admin/crawl-tasks/{task_id}/run", headers=H(token_admin))
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            run1 = r.json()["data"]
            assert run1["status"] == "success", run1
            assert run1["fetched_count"] == 1 and run1["ingested_count"] == 1, run1
            assert run1["updated_count"] == 0 and run1["skipped_count"] == 0, run1
            assert "updated_count" in run1, "run 返回应含 updated_count"

            with SessionLocal() as db:
                doc = db.query(models.Document).filter(
                    models.Document.source == models.SOURCE_CRAWL).first()
                assert doc is not None, "应有 1 个爬虫文档"
                doc_id = doc.id
                assert doc.source_url == f"http://127.0.0.1:{port}/page", doc.source_url
                import hashlib as _hl
                assert doc.file_hash == _hl.sha256(
                    clean_text(doc.content_text).encode("utf-8")).hexdigest(), \
                    "file_hash 应为清洗后入库文本 sha256（自洽）"
                assert doc.status == models.STATUS_APPROVED
                assert vector_store.count_by_document(doc.id) > 0, "应有 Chroma child"
            check("2. 首次 run 新增：ingested=1、updated=0、source_url 记录、approved")

            # ---------- 3. 重复 run：内容不变 → 跳过 ----------
            r = c.post(f"/api/admin/crawl-tasks/{task_id}/run", headers=H(token_admin))
            run2 = r.json()["data"]
            assert run2["status"] == "success", run2
            assert run2["ingested_count"] == 0 and run2["updated_count"] == 0, run2
            assert run2["skipped_count"] == 1, run2
            with SessionLocal() as db:
                n = db.query(models.Document).filter(
                    models.Document.source == models.SOURCE_CRAWL).count()
                assert n == 1, f"文档数不应增加: {n}"
            check("3. 重复 run 内容不变：skipped=1、updated=0、文档数不变")

            # ---------- 4. 页面内容变化 → 更新 ----------
            global CURRENT_BODY
            CURRENT_BODY = V2_TEXT
            r = c.post(f"/api/admin/crawl-tasks/{task_id}/run", headers=H(token_admin))
            run3 = r.json()["data"]
            assert run3["status"] == "success", run3
            assert run3["updated_count"] == 1, run3
            assert run3["ingested_count"] == 0 and run3["skipped_count"] == 0, run3

            with SessionLocal() as db:
                doc = db.get(models.Document, doc_id)
                assert doc is not None, "更新应复用同一 doc 记录"
                assert doc.id == doc_id, "doc id 不应变化"
                assert V2_TEXT in doc.content_text, "content_text 应包含新内容（页面含标题前缀）"
                assert doc.file_hash == _hl.sha256(
                    clean_text(doc.content_text).encode("utf-8")).hexdigest(), "更新后 file_hash 自洽"
                assert doc.status == models.STATUS_APPROVED
                assert doc.source_url == f"http://127.0.0.1:{port}/page", doc.source_url
                assert vector_store.count_by_document(doc.id) > 0, "更新后应有新 Chroma child"
                # FTS：新内容独有词命中、旧内容独有词不再命中（查询侧 jieba 分词，与写入一致）
                v1_clean, v2_clean = clean_text(V1_TEXT), clean_text(V2_TEXT)
                old_only = _tokens(v1_clean) - _tokens(v2_clean)
                new_only = _tokens(v2_clean) - _tokens(v1_clean)
                assert old_only and new_only, f"页面差异 token 缺失: old={old_only} new={new_only}"
                old_kw = sorted(old_only)[0]
                new_kw = sorted(new_only)[0]
                hit_new = db.execute(
                    text("SELECT rowid FROM document_fts WHERE document_fts MATCH :kw"),
                    {"kw": new_kw}).fetchone()
                assert hit_new is not None and hit_new[0] == doc.id, \
                    f"FTS 新词 {new_kw} 未命中 doc {doc_id}"
                hit_old = db.execute(
                    text("SELECT rowid FROM document_fts WHERE document_fts MATCH :kw"),
                    {"kw": old_kw}).fetchone()
                assert hit_old is None, f"FTS 旧词 {old_kw} 应不再命中: {hit_old}"

            # GET /api/search 命中新词（管理员可见公开文档）
            r = c.get("/api/search", params={"q": "量子计算"}, headers=H(token_admin))
            data = r.json()["data"]
            assert data["total"] >= 1 and any(
                it["id"] == doc_id for it in data["items"]), \
                f"搜索应命中更新后文档: {data}"
            check("4. 内容变化再 run：updated=1、doc id 不变、新内容可检索、旧词不再命中")

            # ---------- 5. 运行记录含 updated_count（logs 分页 / 任务列表 last_run） ----------
            r = c.get(f"/api/admin/crawl-tasks/{task_id}/logs", headers=H(token_admin))
            logs = r.json()["data"]["items"]
            assert len(logs) == 3, f"应有 3 条运行记录: {len(logs)}"
            by_updated = {l["updated_count"] for l in logs}
            assert by_updated == {0, 1}, f"logs 应含 updated_count 0/1: {by_updated}"
            r = c.get("/api/admin/crawl-tasks", headers=H(token_admin))
            last_run = r.json()["data"]["items"][0]["last_run"]
            assert last_run["updated_count"] == 1, f"任务列表 last_run 应含 updated_count: {last_run}"
            check("5. 运行记录含 updated_count（logs 分页 / 任务列表 last_run）")

            # ---------- 6. 更新失败：embedding 抛异常 → doc failed、计数进 errors ----------
            from unittest import mock
            CURRENT_BODY = V2_TEXT + "补充段落以触发内容变化更新分支。"
            with mock.patch("app.ingest.embed", side_effect=RuntimeError("mock embed 故障")):
                r = c.post(f"/api/admin/crawl-tasks/{task_id}/run", headers=H(token_admin))
                run4 = r.json()["data"]
            assert run4["status"] == "success", f"单页失败不应整体 failed: {run4}"
            assert run4["updated_count"] == 0 and run4["ingested_count"] == 0, run4
            assert run4["error"] and "更新失败" in run4["error"], f"errors 应含更新失败: {run4}"
            with SessionLocal() as db:
                doc = db.get(models.Document, doc_id)
                assert doc.status == models.STATUS_FAILED, doc.status
                assert doc.error_message and "mock embed 故障" in doc.error_message, \
                    doc.error_message
            check("6. 更新失败：doc 置 failed + error_message、计数进 errors 非 updated")

        print(f"\n=== ALL {len(passed)} M8 TESTS PASSED ===")
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
