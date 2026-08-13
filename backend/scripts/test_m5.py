# -*- coding: utf-8 -*-
"""M5 测试：爬虫抓取入库 / 去重 / 域名白名单(SSRF) / 部门推送 / 工作台统计 / 审计筛选。

覆盖任务 6 项（编号对应任务清单）：
  1. 本地 HTTP 服务器模拟站点（127.0.0.1 随机端口，page1/page2 两页）
     - 创建爬虫任务 → 手动 run → CrawlRunLog success、两页入库（source=crawl、
       department_id=目标部门、approved）、Chroma/FTS 可查
  2. 重复 run → 去重跳过（ingested=0、Document 数不增）
  3. 非白名单外链（example.com/x）不被抓取（SSRF 白名单生效）
  4. 推送：admin 全员+部门推送 → 普通用户可见全员+本部门、未读数正确 → 已读 → 未读减少；
     他部门用户不见本部门定向推送；dept_admin 越权 403
  5. 统计：GET /admin/stats 各字段齐全；dept_admin 仅本部门数字
  6. 审计筛选：audit-logs?action=crawl_run / push_create / crawl_task_create

运行：cd backend && python scripts/test_m5.py
依赖本地 embedding 模型（bge-small-zh-v1.5，M3 已下载）。
"""
import http.server
import os
import shutil
import sys
import threading
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_ROOT = BACKEND_DIR / "data" / "test_m5"
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
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_PASSWORD = "Admin@123456"

# ---------------- 本地模拟站点（两页 + 一个外链） ----------------

PAGE1_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>机器学习模型训练方法</title></head>
<body><h1>机器学习模型训练方法</h1>
<p>机器学习模型训练方法包括数据准备、特征工程、模型选择与超参数调优等关键步骤，
本文详细介绍梯度下降与随机森林等常用算法的训练流程与评估指标。</p>
<p>掌握这些方法有助于企业构建稳定可靠的智能预测系统，提升业务决策效率，减少人工统计成本。</p>
<a href="/page2">数据分析实战案例</a>
<a href="http://example.com/x">外部站点链接</a>
</body></html>"""

PAGE2_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>数据分析实战案例</title></head>
<body><h1>数据分析实战案例</h1>
<p>数据分析实战案例演示数据清洗、探索性分析与可视化报告生成的全过程，
通过真实业务数据样本讲解统计方法的应用场景与结论解读。</p>
<p>本文提供从数据接入到最终报告输出的完整参考流程，适合数据分析初学者学习实践与复盘。</p>
</body></html>"""


class SiteHandler(http.server.BaseHTTPRequestHandler):
    """本地测试站点：/page1、/page2，其余 404。"""

    def do_GET(self):
        if self.path in ("/", "/page1"):
            html = PAGE1_HTML
        elif self.path == "/page2":
            html = PAGE2_HTML
        else:
            self.send_error(404)
            return
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默访问日志
        pass


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

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SiteHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    try:
        with TestClient(app) as c:
            token_admin = login(c, "admin", ADMIN_PASSWORD)

            with SessionLocal() as db:
                dept_a = db.query(models.Department).filter(
                    models.Department.name == "技术部").first()
                dept_b = db.query(models.Department).filter(
                    models.Department.name == "产品部").first()
                assert dept_a is not None and dept_b is not None, "默认部门未播种"

            # 建账号：技术部普通用户 + 产品部普通用户 + 产品部 dept_admin
            for u, pwd, role, dept in (("user_a", "User@123456", "user", dept_a.id),
                                       ("user_b", "User@123456", "user", dept_b.id),
                                       ("dept_b_admin", "Dept@123456", "dept_admin", dept_b.id)):
                r = c.post("/api/admin/users", headers=H(token_admin),
                           json={"username": u, "password": pwd, "role": role,
                                 "department_id": dept})
                assert r.status_code == 200 and r.json()["code"] == 0, r.text
            token_a = login(c, "user_a", "User@123456")
            token_b = login(c, "user_b", "User@123456")
            token_db = login(c, "dept_b_admin", "Dept@123456")

            # ---------- 1. 创建爬虫任务 + 手动 run + 入库断言 ----------
            r = c.post("/api/admin/crawl-tasks", headers=H(token_admin), json={
                "name": "技术博客抓取",
                "start_urls": [f"http://127.0.0.1:{port}/page1"],
                "allowed_domains": ["127.0.0.1"],
                "max_depth": 1,
                "target_department_id": dept_a.id,
                "enabled": False,
            })
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            task_id = r.json()["data"]["id"]

            r = c.post(f"/api/admin/crawl-tasks/{task_id}/run", headers=H(token_admin))
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            run1 = r.json()["data"]
            assert run1["status"] == "success", run1
            assert run1["fetched_count"] == 2, f"应抓取 page1+page2: {run1}"
            assert run1["ingested_count"] == 2, f"两页均应入库: {run1}"

            with SessionLocal() as db:
                docs = db.query(models.Document).filter(
                    models.Document.source == models.SOURCE_CRAWL).all()
                assert len(docs) == 2, f"应入库 2 个爬虫文档: {len(docs)}"
                titles = {d.title for d in docs}
                assert "机器学习模型训练方法" in titles, titles
                assert "数据分析实战案例" in titles, titles
                for d in docs:
                    assert d.status == models.STATUS_APPROVED, d.status
                    assert d.department_id == dept_a.id, "爬虫文档应继承任务目标部门"
                    assert d.file_path == "" and d.file_type == "txt"
                    assert vector_store.count_by_document(d.id) > 0, f"文档 {d.id} 无 Chroma child"
                # FTS 可查（查询侧 jieba 分词，与检索实现一致）
                from sqlalchemy import text as _text
                import jieba as _jieba
                kw = " ".join(_jieba.cut("机器学习"))
                row = db.execute(
                    _text("SELECT rowid FROM document_fts WHERE document_fts MATCH :kw"),
                    {"kw": kw}).fetchone()
                assert row is not None, f"FTS 查询未命中（kw={kw}）"
            check("1. 手动 run 成功：两页入库（source=crawl/部门继承/approved），Chroma+FTS 可查")

            # ---------- 2. 重复 run → 去重跳过 ----------
            r = c.post(f"/api/admin/crawl-tasks/{task_id}/run", headers=H(token_admin))
            assert r.status_code == 200, r.text
            run2 = r.json()["data"]
            assert run2["status"] == "success", run2
            assert run2["fetched_count"] == 2, run2
            assert run2["ingested_count"] == 0, f"重复内容应全部去重跳过: {run2}"
            assert run2["skipped_count"] == 2, run2
            with SessionLocal() as db:
                n = db.query(models.Document).filter(
                    models.Document.source == models.SOURCE_CRAWL).count()
                assert n == 2, f"去重后文档数不应增加: {n}"
            check("2. 重复 run 全部去重跳过（ingested=0、文档数不增）")

            # ---------- 3. 非白名单外链不被抓取（SSRF） ----------
            with SessionLocal() as db:
                docs = db.query(models.Document).filter(
                    models.Document.source == models.SOURCE_CRAWL).all()
                assert all("example.com" not in d.file_name for d in docs), \
                    "外链 example.com/x 不应入库"
                # 两次 run 均未抓取到白名单外地址（fetched 恒为 2）
            check("3. 非白名单域名链接不被抓取（SSRF 白名单生效）")

            # 任务列表带最近运行记录
            r = c.get("/api/admin/crawl-tasks", headers=H(token_admin))
            items = r.json()["data"]["items"]
            assert len(items) == 1, items
            assert items[0]["id"] == task_id and items[0]["last_run"]["ingested_count"] == 0, \
                items[0]
            r = c.get(f"/api/admin/crawl-tasks/{task_id}/logs", headers=H(token_admin))
            assert r.json()["data"]["total"] == 2, "应有 2 条运行记录"

            # ---------- 4. 部门定向推送 + 通知中心 ----------
            r = c.post("/api/admin/push", headers=H(token_admin), json={
                "title": "系统升级公告", "content": "本周五晚间系统升级维护。",
                "document_id": None, "department_id": None})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            all_push_id = r.json()["data"]["id"]
            r = c.post("/api/admin/push", headers=H(token_admin), json={
                "title": "技术部内部资料", "content": "新入库的技术规范文档请查阅。",
                "department_id": dept_a.id})
            assert r.status_code == 200, r.text
            dept_push_id = r.json()["data"]["id"]

            # 技术部普通用户：见全员+本部门，未读 2
            r = c.get("/api/notifications", headers=H(token_a))
            data = r.json()["data"]
            assert data["total"] == 2 and data["unread_count"] == 2, data
            ids_a = {it["id"] for it in data["items"]}
            assert all_push_id in ids_a and dept_push_id in ids_a, ids_a
            # 产品部用户：仅见全员推送，未见本部门定向
            r = c.get("/api/notifications", headers=H(token_b))
            data = r.json()["data"]
            assert data["total"] == 1 and data["unread_count"] == 1, data
            ids_b = {it["id"] for it in data["items"]}
            assert all_push_id in ids_b and dept_push_id not in ids_b, ids_b

            # 已读单条 → 未读数减少
            r = c.post(f"/api/notifications/{dept_push_id}/read", headers=H(token_a))
            assert r.status_code == 200, r.text
            r = c.get("/api/notifications", headers=H(token_a))
            assert r.json()["data"]["unread_count"] == 1, r.json()["data"]
            # 全部已读
            r = c.post("/api/notifications/read-all", headers=H(token_a))
            assert r.status_code == 200 and r.json()["data"]["marked"] == 1, r.text
            r = c.get("/api/notifications", headers=H(token_a))
            assert r.json()["data"]["unread_count"] == 0, r.json()["data"]

            # dept_admin 越权向其他部门推送 → 403
            r = c.post("/api/admin/push", headers=H(token_db), json={
                "title": "越权推送", "department_id": dept_a.id})
            assert r.status_code == 403, r.text
            check("4. 推送可见性/未读数/已读/全部已读/越权403 全部正确")

            # ---------- 5. 工作台统计 ----------
            r = c.get("/api/admin/stats", headers=H(token_admin))
            data = r.json()["data"]
            assert data["document_total"] == 2, data
            assert data["document_by_status"].get("approved") == 2, data
            assert data["pending_count"] == 0, data
            assert data["crawl_task_count"] == {"enabled": 0, "disabled": 1}, data
            assert data["department_count"] >= 3, data
            assert data["user_count"] >= 4, data  # admin + user_a + user_b + dept_b_admin
            assert isinstance(data["trend_7d"], list), data
            # dept_admin（产品部）仅本部门数字：无本部门文档
            r = c.get("/api/admin/stats", headers=H(token_db))
            data = r.json()["data"]
            assert data["document_total"] == 0, data
            assert data["user_count"] == 2, f"产品部应有 user_b+dept_b_admin: {data}"
            assert data["department_count"] == 1, data
            check("5. /admin/stats 各字段齐全；dept_admin 仅本部门数字")

            # ---------- 6. 审计筛选 ----------
            r = c.get("/api/admin/audit-logs", params={"action": "crawl_run"},
                      headers=H(token_admin))
            assert r.json()["data"]["total"] >= 2, "crawl_run 审计应 ≥2 条"
            r = c.get("/api/admin/audit-logs", params={"action": "push_create"},
                      headers=H(token_admin))
            assert r.json()["data"]["total"] >= 2, "push_create 审计应 ≥2 条"
            r = c.get("/api/admin/audit-logs", params={"action": "crawl_task_create"},
                      headers=H(token_admin))
            assert r.json()["data"]["total"] == 1, "crawl_task_create 审计应 1 条"
            check("6. 审计日志按 action 筛选命中 crawl_run/push_create/crawl_task_create")

        print(f"\n=== ALL {len(passed)} M5 TESTS PASSED ===")
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
