# -*- coding: utf-8 -*-
"""S7 多部门可见自检（TestClient + mock 语义/LLM，覆盖 A/B/C 可自动化条目）。

运行：cd backend && HF_HUB_OFFLINE=1 python scripts/test_s7_multidept.py
依赖：FTS5（真实 SQLite）+ mock embed / Chroma / LLM，不加载本地模型。
"""
import json
import os
import shutil
import sys
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_ROOT = BACKEND_DIR / "data" / "test_s7_multidept"
TEST_DB = (TEST_ROOT / "app.db").as_posix()

os.environ["ADMIN_INITIAL_PASSWORD"] = "Admin@123456"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
os.environ["CHROMA_DIR"] = str(TEST_ROOT / "chroma")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["MAX_UPLOAD_MB"] = "5"
os.environ["EMBEDDING_MODE"] = "local"
os.environ["RERANKER_ENABLED"] = "false"
os.environ["LLM_MODE"] = "api"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"

from fastapi.testclient import TestClient  # noqa: E402

from app import document_departments as dd  # noqa: E402
from app import models, vector_store  # noqa: E402
from app.db import (SessionLocal, _migrate_crawl_task_departments,  # noqa: E402
                    _migrate_document_departments,
                    _migrate_push_notification_departments)
from app.main import app  # noqa: E402
from app import crawler as crawler_mod  # noqa: E402
from app.routers import qa as qa_mod  # noqa: E402
from app.search_service import semantic_recall  # noqa: E402
from app.visibility import dept_managed, dept_visible  # noqa: E402

ADMIN_PASSWORD = "Admin@123456"
NEW_ADMIN_PASSWORD = "Admin@654321"
EMBED_DIM = 512

upload_dir = Path(os.environ["UPLOAD_DIR"])


def fake_embed(texts):
    return [[0.01] * EMBED_DIM for _ in texts]


def H(token):
    return {"Authorization": f"Bearer {token}"}


def login(c, u, p="User@123456"):
    r = c.post("/api/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]["token"]


def admin_login_and_changepw(c):
    r = c.post("/api/auth/login", json={"username": "admin", "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    token = data["token"]
    if data["user"].get("must_change_password"):
        rr = c.post("/api/auth/change-password", headers=H(token),
                    json={"old_password": ADMIN_PASSWORD,
                          "new_password": NEW_ADMIN_PASSWORD})
        assert rr.status_code == 200, rr.text
    return token


def create_user(c, admin_token, username, role="user", department_id=None):
    payload = {"username": username, "password": "User@123456", "role": role}
    if department_id is not None:
        payload["department_id"] = department_id
    r = c.post("/api/admin/users", json=payload, headers=H(admin_token))
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]["id"]


def admin_upload(c, token, content, filename, title, department_ids=None, department_id=None):
    fp = TEST_ROOT / filename
    fp.write_text(content, encoding="utf-8")
    data = {"title": title}
    if department_ids is not None:
        data["department_ids"] = json.dumps(department_ids)
    elif department_id is not None:
        data["department_id"] = str(department_id)
    with fp.open("rb") as f:
        return c.post("/api/admin/documents/upload", headers=H(token),
                      files={"file": (filename, f, "text/plain")}, data=data)


def main():
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    passed = []

    def check(name):
        passed.append(name)
        print(f"  PASS: {name}")

    sem_candidates = []

    def fake_sem_query(embedding, top_k, user_department_id=None, is_admin=False,
                       db=None, user=None):
        return list(sem_candidates)

    with mock.patch("app.ingest.embed", side_effect=fake_embed), \
            mock.patch.object(vector_store, "add_children"), \
            mock.patch.object(vector_store, "delete_by_document"), \
            mock.patch.object(vector_store, "query", side_effect=fake_sem_query), \
            mock.patch("app.main._prewarm"), \
            mock.patch("app.main._ensure_vector_health"):
        with TestClient(app) as c:
            admin_token = admin_login_and_changepw(c)

            r = c.get("/api/auth/departments", headers=H(admin_token))
            depts = {d["name"]: d["id"] for d in r.json()["data"]}
            X, Y, Z = depts["技术部"], depts["产品部"], depts["市场部"]
            uid_x = create_user(c, admin_token, "s7_x", "user", X)
            uid_y = create_user(c, admin_token, "s7_y", "user", Y)
            uid_z = create_user(c, admin_token, "s7_z", "user", Z)
            create_user(c, admin_token, "s7_dax", "dept_admin", X)
            tx = login(c, "s7_x")
            ty = login(c, "s7_y")
            tz = login(c, "s7_z")
            tdax = login(c, "s7_dax")
            check("准备. 部门 X/Y/Z 与用户就绪")

            # ---------- A1：连接表与唯一约束 ----------
            from sqlalchemy import text
            with SessionLocal() as db:
                row = db.execute(text(
                    "SELECT sql FROM sqlite_master WHERE type='table' "
                    "AND name='document_department'")).fetchone()
                assert row and row[0], "document_department 表不存在"
            check("A1. document_department 表已建（含唯一约束）")

            # ---------- A2：迁移幂等 ----------
            with SessionLocal() as db:
                legacy = models.Document(
                    title="历史单部门文档", file_name="legacy.txt", file_path="legacy.txt",
                    file_type="txt", file_size=10, file_hash="s7-legacy-hash",
                    status=models.STATUS_APPROVED, department_id=X, source=models.SOURCE_UPLOAD,
                    uploaded_by=uid_x)
                db.add(legacy)
                db.commit()
                db.refresh(legacy)
                legacy_id = legacy.id
            _migrate_document_departments()
            with SessionLocal() as db:
                rows = db.query(models.DocumentDepartment).filter(
                    models.DocumentDepartment.document_id == legacy_id).all()
                assert [r.department_id for r in rows] == [X]
            _migrate_document_departments()
            with SessionLocal() as db:
                rows = db.query(models.DocumentDepartment).filter(
                    models.DocumentDepartment.document_id == legacy_id).all()
                assert len(rows) == 1
            check("A2. 迁移幂等：单部门旧文档→单元素集合，重复迁移不重复插行")

            # ---------- A3：dept_visible / dept_managed ----------
            with SessionLocal() as db:
                du = db.get(models.User, uid_x)
                dy = db.get(models.User, uid_y)
                dz = db.get(models.User, uid_z)
                da = db.query(models.User).filter(models.User.username == "s7_dax").first()
                dadmin = db.get(models.User, 1)
                doc = db.get(models.Document, legacy_id)
                assert dept_visible(dadmin, doc)
                assert dept_visible(du, doc) and not dept_visible(dy, doc)
                assert dept_managed(dadmin, doc)
                assert dept_managed(da, doc)
                assert not dept_managed(dy, doc)
                pub = models.Document(
                    title="公开文档", file_name="pub.txt", file_path="pub.txt",
                    file_type="txt", file_size=10, file_hash="s7-pub-hash",
                    status=models.STATUS_APPROVED, department_id=None,
                    source=models.SOURCE_UPLOAD, uploaded_by=uid_x)
                db.add(pub)
                db.commit()
                db.refresh(pub)
                pub_id = pub.id
                assert dept_visible(dz, pub) and dept_visible(dy, pub)
                assert not dept_managed(da, pub)
            check("A3. dept_visible/dept_managed 按集合判定（admin/公开/命中/不命中）")

            # ---------- A4 + B3：FTS 关键词路多部门过滤 ----------
            phrase_xy = f"多部门唯一关键词{os.getpid()}xy"
            content_xy = f"{phrase_xy}。本文档同时属于 X 与 Y 部门。" * 12
            r = admin_upload(c, admin_token, content_xy, "multi-xy.txt", "跨部门共享手册",
                             department_ids=[X, Y])
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            d_xy = r.json()["data"]
            assert d_xy["status"] == "approved", d_xy
            assert sorted(d_xy["department_ids"]) == sorted([X, Y]), d_xy
            assert d_xy["department_id"] == min(X, Y), d_xy
            doc_xy = d_xy["id"]
            check("A4a. admin 直入库多部门成功，主部门=min(集合)")

            def search_ids(q, token):
                rr = c.get("/api/search", params={"q": q}, headers=H(token))
                assert rr.status_code == 200 and rr.json()["code"] == 0, rr.text
                return [it["id"] for it in rr.json()["data"]["items"]]

            for tok, expect in ((tx, True), (ty, True), (tz, False)):
                ids = search_ids(phrase_xy, tok)
                assert (doc_xy in ids) is expect, (tok, ids)
            assert doc_xy in search_ids(phrase_xy, admin_token)
            check("A4b. 关键词路：X/Y 召回命中、Z 不命中、admin 命中")

            # dept_admin 直入库权限（B3）
            r = admin_upload(c, tdax, "部门管理员多部门直入库权限边界测试甲。" * 12,
                             "b-denied.txt", "越权直入库", department_ids=[Y])
            assert r.status_code == 403, r.text
            r = admin_upload(c, tdax, "部门管理员多部门直入库权限边界测试乙。" * 12,
                             "b-ok.txt", "本部门直入库", department_ids=[X, Y])
            assert r.status_code == 200, r.text
            assert sorted(r.json()["data"]["department_ids"]) == sorted([X, Y])
            r = admin_upload(c, tdax, "部门管理员多部门直入库权限边界测试丙。" * 12,
                             "b-pub.txt", "公开直入库", department_ids=[])
            assert r.status_code == 200, r.text
            assert r.json()["data"]["department_ids"] == []
            check("B3. dept_admin 直入库仅可空或含本部门，否则 403")

            # ---------- B2：普通用户上传多部门/默认/公开/非法/超限 ----------
            content_u = "普通用户多部门上传测试正文内容足够长。" * 10
            fp = TEST_ROOT / "user-upload.txt"
            fp.write_text(content_u, encoding="utf-8")
            with fp.open("rb") as f:
                r = c.post("/api/documents/upload", headers=H(tx),
                           files={"file": ("user-upload.txt", f, "text/plain")},
                           data={"department_ids": json.dumps([X, Y])})
            assert r.status_code == 200, r.text
            assert sorted(r.json()["data"]["department_ids"]) == sorted([X, Y])
            fp2 = TEST_ROOT / "user-default.txt"
            fp2.write_text("用户默认部门上传测试正文内容足够长。" * 10, encoding="utf-8")
            with fp2.open("rb") as f:
                r = c.post("/api/documents/upload", headers=H(tx),
                           files={"file": ("user-default.txt", f, "text/plain")})
            assert r.status_code == 200 and r.json()["data"]["department_ids"] == [X], r.text
            fp3 = TEST_ROOT / "user-public.txt"
            fp3.write_text("用户清空部门上传公开测试正文。" * 10, encoding="utf-8")
            with fp3.open("rb") as f:
                r = c.post("/api/documents/upload", headers=H(tx),
                           files={"file": ("user-public.txt", f, "text/plain")},
                           data={"department_ids": "[]"})
            assert r.status_code == 200 and r.json()["data"]["department_ids"] == [], r.text
            check("B2a. 用户上传多选/默认本部门/显式[]公开")

            before_files = set(p.name for p in upload_dir.iterdir())
            fp4 = TEST_ROOT / "user-bad.txt"
            fp4.write_text("非法部门测试正文内容。" * 10, encoding="utf-8")
            with fp4.open("rb") as f:
                r = c.post("/api/documents/upload", headers=H(tx),
                           files={"file": ("user-bad.txt", f, "text/plain")},
                           data={"department_ids": json.dumps([99999])})
            assert r.status_code == 400 and r.json()["code"] == 40000, r.text
            after_files = set(p.name for p in upload_dir.iterdir())
            assert after_files == before_files, after_files ^ before_files
            check("B2b. 非法部门 400 且不残留文件")

            with mock.patch.object(dd, "MAX_DOC_DEPARTMENTS", 2):
                r = c.post("/api/documents/upload", headers=H(tx),
                           files={"file": ("user-many.txt",
                                           "多部门超限测试正文内容足够长。".encode("utf-8"),
                                           "text/plain")},
                           data={"department_ids": json.dumps([X, Y, Z])})
            assert r.status_code == 400, r.text
            assert "超出上限" in r.json()["message"], r.text
            check("B2c. monkeypatch 上限后 3 部门上传返回 400")

            # ---------- A8 + C6：dept_admin 管理边界 ----------
            r = c.get("/api/admin/documents", headers=H(tdax))
            ids_dax = [i["id"] for i in r.json()["data"]["items"]]
            assert doc_xy in ids_dax, ids_dax
            assert pub_id not in ids_dax, "公开不纳入 dept_admin"
            r = c.get("/api/admin/stats", headers=H(tdax))
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            r = c.get("/api/admin/stats", headers=H(admin_token))
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            check("A8/C6. dept_admin 列表/统计按 dept_managed 口径，公开不纳入")

            # ---------- B4 / B4-2：改部门多部门 + 权限边界 + 即时生效 ----------
            r = c.patch(f"/api/admin/documents/{doc_xy}", headers=H(admin_token),
                        json={"department_ids": [X, Z]})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            assert sorted(r.json()["data"]["department_ids"]) == sorted([X, Z]), r.text
            with SessionLocal() as db:
                d = db.get(models.Document, doc_xy)
                assert d.department_id == min(X, Z), d.department_id
                rows = db.query(models.DocumentDepartment).filter(
                    models.DocumentDepartment.document_id == doc_xy).all()
                assert sorted(r.department_id for r in rows) == sorted([X, Z])
            assert doc_xy not in search_ids(phrase_xy, ty)
            assert doc_xy in search_ids(phrase_xy, tz)
            check("B4. admin 改部门多部门即时生效（主部门列+连接表+检索一致）")

            r = c.patch(f"/api/admin/documents/{doc_xy}", headers=H(tdax),
                        json={"department_ids": [Y]})
            assert r.status_code == 403, r.text
            with SessionLocal() as db:
                rows = db.query(models.DocumentDepartment).filter(
                    models.DocumentDepartment.document_id == doc_xy).all()
                assert sorted(r.department_id for r in rows) == sorted([X, Z])
            r = c.patch(f"/api/admin/documents/{doc_xy}", headers=H(tdax),
                        json={"department_ids": []})
            assert r.status_code == 200, r.text
            assert r.json()["data"]["department_ids"] == []
            assert doc_xy in search_ids(phrase_xy, tz)
            assert doc_xy in search_ids(phrase_xy, ty)
            check("B4-2. 改部门权限边界：dept_admin 403 且原集不变；改公开允许")

            # ---------- A7：document_to_dict 字段 ----------
            r = c.patch(f"/api/admin/documents/{doc_xy}", headers=H(admin_token),
                        json={"department_ids": [X, Y]})
            assert r.status_code == 200, r.text
            r = c.get(f"/api/documents/{doc_xy}", headers=H(tx))
            data = r.json()["data"]
            assert sorted(data["department_ids"]) == sorted([X, Y])
            assert sorted(d["id"] for d in data["departments"]) == sorted([X, Y])
            assert data["department_id"] == min(X, Y)
            assert data["department_name"], data
            check("A7. document_to_dict 含 departments/department_ids 且保留旧字段")

            # ---------- C1：收藏权限口径 ----------
            r = c.post("/api/favorites", headers=H(tx), json={"document_id": doc_xy})
            assert r.status_code == 200, r.text
            r = c.post("/api/favorites", headers=H(tz), json={"document_id": doc_xy})
            assert r.status_code == 403, r.text
            r = c.patch(f"/api/admin/documents/{doc_xy}", headers=H(admin_token),
                        json={"department_ids": [Y]})
            assert r.status_code == 200, r.text
            r = c.get("/api/favorites", headers=H(tx))
            assert r.status_code == 200, r.text
            hit = [i for i in r.json()["data"]["items"] if i.get("document_id") == doc_xy]
            assert hit and hit[0]["is_valid"] is False and hit[0]["document"] is None
            check("C1. X 收藏成功、Z 收藏 403；改部门后 X 收藏隐藏且不报错")

            # ---------- C2/C3：QA 引用权限口径（直测 _reuse_last_citations） ----------
            with SessionLocal() as db:
                qdoc = models.Document(
                    title="QA专用多部门文档", file_name="q.txt", file_path="",
                    file_type="txt", file_size=0, file_hash="s7-qa-doc",
                    content_text="", status=models.STATUS_APPROVED, department_id=X,
                    source=models.SOURCE_UPLOAD, uploaded_by=uid_x)
                db.add(qdoc)
                db.flush()
                dd.set_doc_departments(db, qdoc, [X, Y])
                db.commit()
                qdoc_id = qdoc.id
            q_parent_text = "唯一知识点：使用蓝鲸优化器可以提升模型收敛速度。"
            with SessionLocal() as db:
                db.add(models.ChunkParent(document_id=qdoc_id, chunk_index=0,
                                          title="QA", text=q_parent_text))
                db.commit()

            class FakeMsg:
                citations = [{"document_id": qdoc_id}]

            with SessionLocal() as db:
                du = db.get(models.User, uid_x)
                dz = db.get(models.User, uid_z)
                ctx_x, cit_x = qa_mod._reuse_last_citations(db, du, "优化器", FakeMsg())
                ctx_z, cit_z = qa_mod._reuse_last_citations(db, dz, "优化器", FakeMsg())
            assert cit_x and cit_x[0]["document_id"] == qdoc_id
            assert not cit_z
            assert not any(q_parent_text in t for t in ctx_z)
            check("C2/C3. 问答/上一轮 citations 复用按集合过滤（X 引用、Z 不越权）")

            # ---------- A5：语义路后置过滤（真实 vector_store.query + mock 集合） ----------
            def make_stub_collection(cands):
                class StubCol:
                    def query(self, query_embeddings=None, n_results=None, where=None):
                        n = min(n_results or len(cands), len(cands))
                        chosen = cands[:n]
                        return {
                            "ids": [[str(i) for i in range(len(chosen))]],
                            "metadatas": [[{
                                "document_id": c["document_id"],
                                "parent_id": c["parent_id"],
                                "chunk_index": c["chunk_index"],
                                "department_id": c.get("department_id", ""),
                                "status": c.get("status", "approved"),
                            } for c in chosen]],
                            "documents": [[c["text"] for c in chosen]],
                            "distances": [[c["distance"] for c in chosen]],
                        }
                return StubCol()

            cands = [
                {"document_id": qdoc_id, "parent_id": 0, "chunk_index": 0,
                 "text": "x", "distance": 0.1, "status": "approved",
                 "department_id": str(Y)},
                {"document_id": 999999, "parent_id": 0, "chunk_index": 0,
                 "text": "x", "distance": 0.2, "status": "approved",
                 "department_id": ""},
            ]
            with SessionLocal() as db:
                du = db.get(models.User, uid_x)
                dz = db.get(models.User, uid_z)
            with mock.patch("app.search_service.embed", side_effect=fake_embed),                     mock.patch("app.vector_store._get_collection",
                               return_value=make_stub_collection(cands)):
                with SessionLocal() as db:
                    hits = semantic_recall(db, du, "查询", top_k=10)
                    hits_z = semantic_recall(db, dz, "查询", top_k=10)
            assert all(h["document_id"] != 999999 for h in hits), hits
            assert qdoc_id in [h["document_id"] for h in hits], hits  # X 可见 qdoc
            assert all(h["document_id"] != qdoc_id for h in hits_z), hits_z
            sem_candidates[:] = []
            check("A5. 语义路多取候选+回表 dept_visible 后置过滤（X 见、Z 不见、999999 被滤）")

            # C4：Z 以不可见 qdoc 为入口 → 404
            r = c.get(f"/api/documents/{qdoc_id}/related", headers=H(tz))
            assert r.status_code == 404, r.text
            check("C4. 相似推荐权限口径（不可见入口 404）")

            # ---------- C5：联想 + 缓存兜底 ----------
            r = c.get("/api/search/suggest", params={"q": "跨部门共享"}, headers=H(ty))
            assert r.status_code == 200
            assert any(i["id"] == doc_xy for i in r.json()["data"]["items"])
            c.get("/api/search/suggest", params={"q": "跨部门共享"}, headers=H(tx))  # 预热 X 缓存
            r = c.patch(f"/api/admin/documents/{doc_xy}", headers=H(admin_token),
                        json={"department_ids": [Y]})
            assert r.status_code == 200
            r2 = c.get("/api/search/suggest", params={"q": "跨部门共享"}, headers=H(tx))
            assert not any(i["id"] == doc_xy for i in r2.json()["data"]["items"])
            check("C5. 联想可见性 + 改部门后缓存兜底不向无权限用户返回标题")

            # ---------- C8：写操作后主表/连接表/FTS 一致 ----------
            with SessionLocal() as db:
                d = db.get(models.Document, doc_xy)
                rows = db.query(models.DocumentDepartment).filter(
                    models.DocumentDepartment.document_id == doc_xy).all()
                ids = sorted(r.department_id for r in rows)
                assert ids == [Y], ids
                assert d.department_id == Y == dd._primary_of(ids)
                from sqlalchemy import text as _t
                fts_row = db.execute(_t(
                    "SELECT department_id FROM document_fts WHERE rowid=:i"),
                    {"i": doc_xy}).fetchone()
                assert fts_row and str(fts_row[0]) == str(Y), fts_row
            check("C8. 主表/连接表/FTS 主部门冗余一致")

            # ============================================================
            # D1-D8：爬虫任务与通知推送多部门（用户新增需求）
            # ============================================================
            # ---------- D1：两张新连接表 + 唯一约束 ----------
            with SessionLocal() as db:
                for name in ("crawl_task_department", "push_notification_department"):
                    row = db.execute(text(
                        "SELECT sql FROM sqlite_master WHERE type='table' AND name=:n"),
                        {"n": name}).fetchone()
                    assert row and row[0], f"{name} 表不存在"
                    assert "UNIQUE" in row[0].upper() or "unique" in row[0], row[0]
            check("D1. crawl_task_department/push_notification_department 建表（含唯一约束）")

            # ---------- D2：F2/F3 启动迁移幂等 ----------
            with SessionLocal() as db:
                legacy_task = models.CrawlTask(
                    name="历史单部门爬虫任务", start_urls=["http://old.test/page"],
                    allowed_domains=["old.test"], max_depth=0,
                    target_department_id=X, enabled=False, status="disabled",
                    created_by=1)
                db.add(legacy_task)
                legacy_push = models.PushNotification(
                    title="历史单部门推送", content="旧数据", document_id=None,
                    department_id=Y, created_by=1)
                db.add(legacy_push)
                db.commit()
                legacy_task_id = legacy_task.id
                legacy_push_id = legacy_push.id
            _migrate_crawl_task_departments()
            _migrate_push_notification_departments()
            with SessionLocal() as db:
                r1 = db.query(models.CrawlTaskDepartment).filter(
                    models.CrawlTaskDepartment.task_id == legacy_task_id).all()
                assert [x.department_id for x in r1] == [X]
                r2 = db.query(models.PushNotificationDepartment).filter(
                    models.PushNotificationDepartment.notification_id == legacy_push_id).all()
                assert [x.department_id for x in r2] == [Y]
            _migrate_crawl_task_departments()
            _migrate_push_notification_departments()
            with SessionLocal() as db:
                assert db.query(models.CrawlTaskDepartment).filter(
                    models.CrawlTaskDepartment.task_id == legacy_task_id).count() == 1
                assert db.query(models.PushNotificationDepartment).filter(
                    models.PushNotificationDepartment.notification_id == legacy_push_id
                ).count() == 1
            with SessionLocal() as db:
                db.query(models.CrawlTaskDepartment).filter(
                    models.CrawlTaskDepartment.task_id == legacy_task_id).delete()
                db.query(models.PushNotificationDepartment).filter(
                    models.PushNotificationDepartment.notification_id == legacy_push_id).delete()
                db.delete(db.get(models.CrawlTask, legacy_task_id))
                db.delete(db.get(models.PushNotification, legacy_push_id))
                db.commit()
            check("D2. 旧单部门任务/推送幂等迁移为单元素集合，重复迁移不重复插行")
            # ---------- D3：dept_admin 对爬虫创建/编辑/删除/执行均 403 ----------
            r = c.post("/api/admin/crawl-tasks", headers=H(tdax), json={
                "name": "越权建任务", "start_urls": ["http://d3.test/a"],
                "allowed_domains": ["d3.test"], "max_depth": 0,
                "target_department_ids": [X]})
            assert r.status_code == 403, r.text
            r = c.post("/api/admin/crawl-tasks", headers=H(admin_token), json={
                "name": "D3权限任务", "start_urls": ["http://d3.test/b"],
                "allowed_domains": ["d3.test"], "max_depth": 0,
                "target_department_ids": [X, Y]})
            assert r.status_code == 200, r.text
            d3_task_id = r.json()["data"]["id"]
            r = c.patch(f"/api/admin/crawl-tasks/{d3_task_id}", headers=H(tdax), json={"name": "x"})
            assert r.status_code == 403, r.text
            r = c.delete(f"/api/admin/crawl-tasks/{d3_task_id}", headers=H(tdax))
            assert r.status_code == 403, r.text
            r = c.post(f"/api/admin/crawl-tasks/{d3_task_id}/run", headers=H(tdax))
            assert r.status_code == 403, r.text
            check("D3. dept_admin 对爬虫 create/patch/delete/run 均 403")

            # ---------- D4：爬虫入库继承集合 + 同 URL 更新保留原集合（mock fetch） ----------
            crawl_url = "http://mock.test/s7-page"
            phrase_crawl = f"爬虫多部门继承关键词{os.getpid()}"
            seq = (f"{phrase_crawl}。这是很长的一段正文。{phrase_crawl} 再次出现。" * 30)
            html1 = ("<html><head><title>爬虫多部门文档</title></head><body>"
                     + seq + "</body></html>")

            class _FakeNode:
                def __init__(self, text=""):
                    self._text = text
                    self.attrib = {}
                def get_all_text(self, separator="-"):
                    return self._text

            class _FakeCSS:
                def __init__(self, first=None, items=()):
                    self.first = first
                    self._items = items
                def __iter__(self):
                    return iter(self._items)

            class _FakeResp:
                def __init__(self, body, title=""):
                    self.body = body
                    self._title = title
                def css(self, sel):
                    if sel == "title":
                        node = _FakeNode(self._title)
                        return _FakeCSS(first=node if self._title else None)
                    return _FakeCSS(first=None, items=())

            r = c.post("/api/admin/crawl-tasks", headers=H(admin_token), json={
                "name": "D4多部门爬虫", "start_urls": [crawl_url],
                "allowed_domains": ["mock.test"], "max_depth": 0, "enabled": False,
                "target_department_ids": [X, Y]})
            assert r.status_code == 200, r.text
            d4_task = r.json()["data"]
            assert sorted(d4_task["target_department_ids"]) == sorted([X, Y]), d4_task
            assert d4_task["target_department_id"] == min(X, Y), d4_task
            assert len(d4_task["target_departments"]) == 2, d4_task
            d4_task_id = d4_task["id"]
            with mock.patch.object(crawler_mod, "fetch_page",
                                   return_value=_FakeResp(html1, "爬虫多部门文档")):
                r = c.post(f"/api/admin/crawl-tasks/{d4_task_id}/run", headers=H(admin_token))
            assert r.status_code == 200, r.text
            assert r.json()["data"]["status"] == "success", r.json()["data"]
            assert r.json()["data"]["ingested_count"] == 1, r.json()["data"]
            with SessionLocal() as db:
                cdoc = db.query(models.Document).filter(
                    models.Document.source_url == crawl_url,
                    models.Document.source == models.SOURCE_CRAWL).first()
                assert cdoc is not None, "爬虫应入库新文档"
                cdoc_id = cdoc.id
                ids_rows = db.query(models.DocumentDepartment).filter(
                    models.DocumentDepartment.document_id == cdoc_id).all()
                assert sorted(x.department_id for x in ids_rows) == sorted([X, Y])
                assert cdoc.department_id == min(X, Y)
            for tok, expect in ((tx, True), (ty, True), (tz, False)):
                assert (cdoc_id in search_ids(phrase_crawl, tok)) is expect, (tok,)
            check("D4a. 新入库文档继承任务目标部门集合（X/Y 可检索、Z 不可）")

            phrase_upd = f"爬虫更新后唯一关键词{os.getpid()}"
            seq2 = (f"{phrase_upd}。更新后的正文发生了内容变化。" * 30)
            html2 = ("<html><head><title>爬虫多部门文档v2</title></head><body>"
                     + seq2 + "</body></html>")
            with SessionLocal() as db:
                cdoc = db.get(models.Document, cdoc_id)
                dd.set_doc_departments(db, cdoc, [Z])
                db.commit()
            with mock.patch.object(crawler_mod, "fetch_page",
                                   return_value=_FakeResp(html2, "爬虫多部门文档v2")):
                r = c.post(f"/api/admin/crawl-tasks/{d4_task_id}/run", headers=H(admin_token))
            assert r.status_code == 200, r.text
            assert r.json()["data"]["updated_count"] == 1, r.json()["data"]
            with SessionLocal() as db:
                cdoc = db.get(models.Document, cdoc_id)
                ids_rows = db.query(models.DocumentDepartment).filter(
                    models.DocumentDepartment.document_id == cdoc_id).all()
                assert sorted(x.department_id for x in ids_rows) == [Z], ids_rows
                assert cdoc.title == "爬虫多部门文档v2"
            assert cdoc_id in search_ids(phrase_upd, tz)
            assert cdoc_id not in search_ids(phrase_upd, tx)
            check("D4b. 同 URL 更新分支保留文档既有部门集合不变")
            # ---------- D5：推送创建支持 department_ids（空=全员） + dept_admin 边界 ----------
            r = c.post("/api/admin/push", headers=H(admin_token), json={
                "title": "D5多部门推送", "content": "面向 X/Y",
                "department_ids": [X, Y]})
            assert r.status_code == 200, r.text
            push_xy = r.json()["data"]
            assert sorted(push_xy["department_ids"]) == sorted([X, Y]), push_xy
            assert push_xy["department_id"] == min(X, Y), push_xy
            assert len(push_xy["departments"]) == 2, push_xy
            push_xy_id = push_xy["id"]
            r = c.post("/api/admin/push", headers=H(admin_token), json={
                "title": "D5全员推送", "content": "全员", "department_ids": []})
            assert r.status_code == 200, r.text
            push_all_id = r.json()["data"]["id"]
            assert r.json()["data"]["department_ids"] == []
            r = c.post("/api/admin/push", headers=H(tdax), json={
                "title": "越权推送", "department_ids": [Y]})
            assert r.status_code == 403, r.text
            r = c.post("/api/admin/push", headers=H(tdax), json={
                "title": "本部门全员工推送", "department_ids": []})
            assert r.status_code == 200, r.text
            r = c.post("/api/admin/push", headers=H(tdax), json={
                "title": "本部门含X推送", "department_ids": [X, Y]})
            assert r.status_code == 200, r.text
            check("D5. 推送多部门保存/空=全员/保留旧字段；dept_admin 边界 403")

            # ---------- D6：五个收口点按集合口径统一（admin 不特殊放权） ----------
            r = c.get("/api/notifications", params={"page_size": 100}, headers=H(tx))
            xd = r.json()["data"]
            assert push_xy_id in {i["id"] for i in xd["items"]}
            assert push_all_id in {i["id"] for i in xd["items"]}
            assert xd["unread_count"] == xd["total"] >= 2
            r = c.get("/api/notifications", params={"page_size": 100}, headers=H(tz))
            zd = r.json()["data"]
            zids = {i["id"] for i in zd["items"]}
            assert push_all_id in zids and push_xy_id not in zids, zids
            assert zd["unread_count"] == zd["total"]
            r = c.get(f"/api/notifications/{push_xy_id}", headers=H(tx))
            assert r.status_code == 200 and r.json()["data"]["is_read"] is False, r.text
            r = c.get(f"/api/notifications/{push_xy_id}", headers=H(tz))
            assert r.status_code == 404, r.text
            r = c.post(f"/api/notifications/{push_xy_id}/read", headers=H(tx))
            assert r.status_code == 200, r.text
            r = c.get(f"/api/notifications/{push_xy_id}", headers=H(tx))
            assert r.json()["data"]["is_read"] is True, r.json()
            r = c.post(f"/api/notifications/{push_xy_id}/read", headers=H(tz))
            assert r.status_code == 404, r.text
            r = c.get("/api/notifications", params={"page_size": 100}, headers=H(tx))
            xd2 = r.json()["data"]
            assert xd2["unread_count"] == xd["total"] - 1, (xd2, xd)
            r = c.get("/api/notifications", params={"page_size": 100}, headers=H(admin_token))
            ad = r.json()["data"]
            a_ids = {i["id"] for i in ad["items"]}
            assert push_all_id in a_ids and push_xy_id not in a_ids, a_ids
            check("D6. 通知列表/未读/已读统计/mark_read/详情直读按集合统一，admin 不特殊放权")

            # ---------- D7：删除级联清理 + 部门删除引用检查纳入新连接表 ----------
            r = c.post("/api/admin/departments", headers=H(admin_token), json={"name": "S7D7临时部"})
            assert r.status_code == 200, r.text
            temp_dept_id = r.json()["data"]["id"]
            r = c.post("/api/admin/crawl-tasks", headers=H(admin_token), json={
                "name": "D7临时任务", "start_urls": ["http://d7.test/a"],
                "allowed_domains": ["d7.test"], "max_depth": 0,
                "target_department_ids": [temp_dept_id]})
            assert r.status_code == 200, r.text
            t7_id = r.json()["data"]["id"]
            r = c.post("/api/admin/push", headers=H(admin_token), json={
                "title": "D7临时推送", "department_ids": [temp_dept_id]})
            assert r.status_code == 200, r.text
            p7_id = r.json()["data"]["id"]
            r = c.delete(f"/api/admin/departments/{temp_dept_id}", headers=H(admin_token))
            assert r.status_code == 409, r.text
            r = c.delete(f"/api/admin/crawl-tasks/{t7_id}", headers=H(admin_token))
            assert r.status_code == 200, r.text
            with SessionLocal() as db:
                assert db.query(models.CrawlTaskDepartment).filter(
                    models.CrawlTaskDepartment.task_id == t7_id).count() == 0
            r = c.delete(f"/api/admin/departments/{temp_dept_id}", headers=H(admin_token))
            assert r.status_code == 409, r.text
            r = c.delete(f"/api/admin/push/{p7_id}", headers=H(admin_token))
            assert r.status_code == 200, r.text
            with SessionLocal() as db:
                assert db.query(models.PushNotificationDepartment).filter(
                    models.PushNotificationDepartment.notification_id == p7_id).count() == 0
                assert db.query(models.PushRead).filter(
                    models.PushRead.notification_id == p7_id).count() == 0
            r = c.delete(f"/api/admin/departments/{temp_dept_id}", headers=H(admin_token))
            assert r.status_code == 200, r.text
            r = c.delete(f"/api/admin/documents/{cdoc_id}", headers=H(admin_token))
            assert r.status_code == 200, r.text
            with SessionLocal() as db:
                assert db.query(models.DocumentDepartment).filter(
                    models.DocumentDepartment.document_id == cdoc_id).count() == 0
            check("D7. 删除任务/推送/文档显式清理连接表；部门删除引用检查纳入新连接表")

        # ---------- 第二次 TestClient：重启后一致（C8） ----------
        with mock.patch("app.ingest.embed", side_effect=fake_embed), \
                mock.patch.object(vector_store, "add_children"), \
                mock.patch.object(vector_store, "delete_by_document"), \
                mock.patch.object(vector_store, "query", side_effect=fake_sem_query), \
                mock.patch("app.main._prewarm"), \
                mock.patch("app.main._ensure_vector_health"):
            with TestClient(app) as c2:
                r = c2.post("/api/auth/login", json={"username": "admin",
                                                     "password": NEW_ADMIN_PASSWORD})
                assert r.status_code == 200 and r.json()["code"] == 0, r.text
                admin_token2 = r.json()["data"]["token"]
                ty2 = login(c2, "s7_y")
                tx2 = login(c2, "s7_x")
                rr = c2.get("/api/search", params={"q": phrase_xy}, headers=H(ty2))
                assert doc_xy in [i["id"] for i in rr.json()["data"]["items"]]
                rr = c2.get("/api/search", params={"q": phrase_xy}, headers=H(tx2))
                assert doc_xy not in [i["id"] for i in rr.json()["data"]["items"]]
            check("C8b. 服务重启后多部门可见性仍一致")

    # ---------- A9：删除文档级联清理连接行（按路由口径先清分块再删文档） ----------
    with SessionLocal() as db:
        db.query(models.ChunkParent).filter(
            models.ChunkParent.document_id == doc_xy).delete()
        db.query(models.Favorite).filter(
            models.Favorite.document_id == doc_xy).delete()
        db.query(models.DocumentDepartment).filter(
            models.DocumentDepartment.document_id == doc_xy).delete()
        d = db.get(models.Document, doc_xy)
        if d is not None:
            db.delete(d)
        db.commit()
    with SessionLocal() as db:
        assert db.query(models.DocumentDepartment).filter(
            models.DocumentDepartment.document_id == doc_xy).count() == 0
    check("A9. 删除文档可级联清理连接行")

    print(f"\n=== ALL {len(passed)} S7 MULTIDEPT TESTS PASSED ===")
    try:
        from app.db import engine
        engine.dispose()
    except Exception:
        pass
    shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
