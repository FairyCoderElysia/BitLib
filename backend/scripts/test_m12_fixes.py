# -*- coding: utf-8 -*-
"""Sprint 1 差异修复测试（F1 首登改密 / F2+F8 重复文件更新 / F15 批量审批）。

运行：cd backend && python scripts/test_m12_fixes.py
依赖：TestClient + mock embed（不加载本地模型）+ mock Chroma 写删（不落真实向量库）。
"""
import os
import shutil
import sys
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_ROOT = BACKEND_DIR / "data" / "test_m12"
TEST_DB = (TEST_ROOT / "app.db").as_posix()

os.environ["ADMIN_INITIAL_PASSWORD"] = "Admin@123456"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
os.environ["CHROMA_DIR"] = str(TEST_ROOT / "chroma")
os.environ["MAX_UPLOAD_MB"] = "5"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["EMBEDDING_MODE"] = "local"
os.environ["RERANKER_ENABLED"] = "false"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"
os.environ["LLM_MODE"] = "api"

from fastapi.testclient import TestClient  # noqa: E402

from app import models, vector_store  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_PASSWORD = "Admin@123456"
NEW_ADMIN_PASSWORD = "Admin@654321"
EMBED_DIM = 512

delete_calls = []
add_calls = []


def fake_embed(texts):
    return [[0.01] * EMBED_DIM for _ in texts]


def fake_delete_by_document(doc_id):
    delete_calls.append(doc_id)


def fake_add_children(items):
    add_calls.extend(items)


def fake_query(*args, **kwargs):
    return []


def H(token):
    return {"Authorization": f"Bearer {token}"}


def login(c, u, p):
    r = c.post("/api/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return r.json()["data"]["token"]


def create_user(c, token, username, password, role="user", department_id=None):
    payload = {"username": username, "password": password, "role": role}
    if department_id is not None:
        payload["department_id"] = department_id
    return c.post("/api/admin/users", json=payload, headers=H(token))


def upload(c, token, text, filename, title="", update=False):
    files = {"file": (filename, text.encode("utf-8"), "text/plain")}
    data = {}
    if title:
        data["title"] = title
    if update:
        data["update_if_duplicate"] = "true"
    return c.post("/api/documents/upload", headers=H(token), files=files, data=data)


def main():
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    passed = []

    def check(name):
        passed.append(name)
        print(f"  PASS: {name}")

    with mock.patch("app.ingest.embed", side_effect=fake_embed), \
            mock.patch.object(vector_store, "delete_by_document",
                              side_effect=fake_delete_by_document), \
            mock.patch.object(vector_store, "add_children",
                              side_effect=fake_add_children), \
            mock.patch.object(vector_store, "query", side_effect=fake_query):
        with TestClient(app) as c:
            # ================= A. F1 首登强制改密 =================
            r = c.post("/api/auth/login", json={"username": "admin",
                                                "password": ADMIN_PASSWORD})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            admin_token = r.json()["data"]["token"]
            assert r.json()["data"]["user"]["must_change_password"] is True, r.text
            check("A1a. 全新库播种 admin 登录返回 must_change_password=true")

            r = c.get("/api/auth/me", headers=H(admin_token))
            assert r.status_code == 200, r.text
            assert r.json()["data"]["must_change_password"] is True, r.text
            check("A1b. /auth/me 返回 must_change_password=true")

            r = c.post("/api/auth/change-password", headers=H(admin_token),
                       json={"old_password": "wrong-old", "new_password": "abc123"})
            assert r.status_code == 400, r.text
            assert "原密码错误" in r.json()["message"], r.text
            check("A2a. 原密码错误返回 400（不 401）")

            r = c.post("/api/auth/change-password", headers=H(admin_token),
                       json={"old_password": ADMIN_PASSWORD,
                             "new_password": ADMIN_PASSWORD})
            assert r.status_code == 400, r.text
            assert "新密码不能与旧密码相同" in r.json()["message"], r.text
            check("A2b. 新旧密码相同返回 400")

            r = c.post("/api/auth/change-password", headers=H(admin_token),
                       json={"old_password": ADMIN_PASSWORD, "new_password": "123"})
            assert r.status_code == 400, r.text
            assert "新密码长度至少 6 位" in r.json()["message"], r.text
            check("A2c. 新密码长度 <6 返回 400")

            r = c.post("/api/auth/change-password", headers=H(admin_token),
                       json={"old_password": ADMIN_PASSWORD,
                             "new_password": NEW_ADMIN_PASSWORD})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            assert r.json()["data"]["must_change_password"] is False, r.text
            check("A2d. 改密成功返回 must_change_password=false")

            r = c.post("/api/auth/login", json={"username": "admin",
                                                "password": ADMIN_PASSWORD})
            assert r.status_code == 401, r.text
            admin_token = login(c, "admin", NEW_ADMIN_PASSWORD)
            r = c.get("/api/auth/me", headers=H(admin_token))
            assert r.json()["data"]["must_change_password"] is False, r.text
            check("A3. 新密码登录成功且不再强制改密；旧密码登录失败")

            # ================= B. F2/F8 重复文件更新 =================
            r = c.get("/api/auth/departments", headers=H(admin_token))
            depts = {d["name"]: d["id"] for d in r.json()["data"]}
            tech_id = depts["技术部"]
            prod_id = depts["产品部"]

            r = create_user(c, admin_token, "user1", "user123", "user", tech_id)
            assert r.status_code == 200, r.text
            r = create_user(c, admin_token, "user2", "user2123", "user", prod_id)
            assert r.status_code == 200, r.text
            r = create_user(c, admin_token, "dpa1", "dpa123", "dept_admin", tech_id)
            assert r.status_code == 200, r.text
            u1 = login(c, "user1", "user123")
            u2 = login(c, "user2", "user2123")
            dpa = login(c, "dpa1", "dpa123")

            long_text = "这是一份用于测试更新为新版本的文档正文。" * 20
            r = upload(c, u1, long_text, "v1.txt", title="版本文档")
            assert r.status_code == 200 and r.json()["data"]["status"] == "pending", r.text
            doc_id = r.json()["data"]["id"]
            r = c.post(f"/api/admin/pending/{doc_id}/approve", headers=H(admin_token))
            assert r.status_code == 200, r.text
            assert r.json()["data"]["status"] == "approved", r.text

            with SessionLocal() as db:
                doc = db.get(models.Document, doc_id)
                old_hash = doc.file_hash
                old_path = doc.file_path
                parent_count_before = db.query(models.ChunkParent).filter(
                    models.ChunkParent.document_id == doc_id).count()
            assert parent_count_before > 0, "审批通过后应有 ChunkParent"
            check("B0. 准备 approved 文档（含 ChunkParent）")

            r = upload(c, u1, long_text, "v1.txt", title="版本文档")
            assert r.status_code == 409, r.text
            detail = r.json()["detail"]
            assert detail["document_id"] == doc_id, r.text
            assert detail["can_update"] is True, r.text
            assert detail["status"] == "approved", r.text
            check("B1a. 默认重复上传仍 409，detail 含 document_id/can_update")

            r = upload(c, u2, long_text, "v1.txt", update=True)
            assert r.status_code == 403, r.text
            with SessionLocal() as db:
                doc = db.get(models.Document, doc_id)
                assert doc.status == "approved" and doc.file_hash == old_hash
            check("B5. 无权限用户重复上传 update=true 返回 403 且原文档不变")

            delete_calls.clear()
            add_calls.clear()
            r = upload(c, u1, long_text, "v1.txt", title="版本文档", update=True)
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            data = r.json()["data"]
            assert data["id"] == doc_id, r.text
            assert data["status"] == "approved", r.text
            with SessionLocal() as db:
                doc = db.get(models.Document, doc_id)
                assert doc.file_hash == old_hash
                assert doc.file_path != old_path
                parent_count_after = db.query(models.ChunkParent).filter(
                    models.ChunkParent.document_id == doc_id).count()
                assert parent_count_after > 0
            assert delete_calls and delete_calls[-1] == doc_id, "更新应清理旧向量"
            assert any(i["document_id"] == doc_id for i in add_calls), "更新应写入新向量"
            check("B2. 本人重复上传 update=true 更新成功且重建分块/向量")

            with SessionLocal() as db:
                doc = db.get(models.Document, doc_id)
                doc.department_id = tech_id
                db.add(doc)
                db.commit()
            r = c.post("/api/admin/documents/upload", headers=H(dpa),
                       files={"file": ("v1.txt", long_text.encode("utf-8"), "text/plain")},
                       data={"department_id": str(tech_id), "update_if_duplicate": "true"})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            check("B3. 部门管理员可更新本部门 approved 文档")

            with SessionLocal() as db:
                doc = db.get(models.Document, doc_id)
                doc.department_id = prod_id
                db.add(doc)
                db.commit()
            r = c.post("/api/admin/documents/upload", headers=H(dpa),
                       files={"file": ("v1.txt", long_text.encode("utf-8"), "text/plain")},
                       data={"update_if_duplicate": "true"})
            assert r.status_code == 403, r.text
            r = c.post("/api/admin/documents/upload", headers=H(admin_token),
                       files={"file": ("v1.txt", long_text.encode("utf-8"), "text/plain")},
                       data={"update_if_duplicate": "true"})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            check("B4. 部门管理员不可更新非本部门；管理员可更新任意 approved")

            r = c.get("/api/admin/audit-logs?action=update", headers=H(admin_token))
            assert r.status_code == 200, r.text
            items = r.json()["data"]["items"]
            assert items and any(i["target_id"] == doc_id for i in items), r.text
            check("B6. 更新成功写审计 action=update")

            with SessionLocal() as db:
                doc = db.get(models.Document, doc_id)
                old_hash = doc.file_hash
                old_file_path = doc.file_path
                parent_before_fail = db.query(models.ChunkParent).filter(
                    models.ChunkParent.document_id == doc_id).count()
            with mock.patch("app.ingest.parse_file",
                            side_effect=ValueError("解析失败（测试注入）")):
                r = c.post("/api/admin/documents/upload", headers=H(admin_token),
                           files={"file": ("v1.txt", long_text.encode("utf-8"), "text/plain")},
                           data={"update_if_duplicate": "true"})
            assert r.status_code == 400, r.text
            assert "解析失败" in r.json()["message"], r.text
            with SessionLocal() as db:
                doc = db.get(models.Document, doc_id)
                assert doc.status == "approved", r.text
                assert doc.file_hash == old_hash, r.text
                assert doc.file_path == old_file_path, r.text
                parent_after_fail = db.query(models.ChunkParent).filter(
                    models.ChunkParent.document_id == doc_id).count()
                assert parent_after_fail == parent_before_fail
            check("B7. 可失败阶段失败不半更新：doc 保持 approved，旧分块不动")

            # ================= C. F15 批量审批 =================
            pending_ids = []
            for i, title in enumerate(["批量A", "批量B", "批量C", "批量D"]):
                r = upload(c, u1, f"{title} 批量审批测试文档内容。" * 12, f"{title}.txt")
                assert r.status_code == 200, r.text
                pending_ids.append(r.json()["data"]["id"])

            ids = [pending_ids[0], pending_ids[1], 999999]
            r = c.post("/api/admin/pending/batch", headers=H(admin_token),
                       json={"action": "approve", "document_ids": ids})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            data = r.json()["data"]
            assert data["total"] == 3 and data["succeeded"] == 2 and data["failed"] == 1, r.text
            by_id = {x["id"]: x for x in data["results"]}
            assert by_id[pending_ids[0]]["success"] is True
            assert by_id[pending_ids[1]]["success"] is True
            assert by_id[999999]["success"] is False and by_id[999999]["code"] == 40400
            check("C1/C3a. 批量通过成功+失败明细（部分成功 200）")

            reject_ids = [pending_ids[2], pending_ids[3]]
            r = c.post("/api/admin/pending/batch", headers=H(admin_token),
                       json={"action": "reject", "document_ids": reject_ids,
                             "reason": "批量测试拒绝"})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            data = r.json()["data"]
            assert data["succeeded"] == 2, r.text
            with SessionLocal() as db:
                for did in reject_ids:
                    doc = db.get(models.Document, did)
                    assert doc.status == "rejected"
                    assert doc.reject_reason == "批量测试拒绝"
            check("C2. 批量拒绝成功且统一原因生效")

            r = c.post("/api/admin/pending/batch", headers=H(admin_token),
                       json={"action": "bad", "document_ids": [1]})
            assert r.status_code == 400 and r.json()["code"] == 40000, r.text
            r = c.post("/api/admin/pending/batch", headers=H(admin_token),
                       json={"action": "approve", "document_ids": []})
            assert r.status_code == 400, r.text
            r = c.post("/api/admin/pending/batch", headers=H(admin_token),
                       json={"action": "approve", "document_ids": "1"})
            assert r.status_code == 400, r.text
            r = c.post("/api/admin/pending/batch", headers=H(admin_token),
                       json={"action": "reject", "document_ids": [1]})
            assert r.status_code == 400, r.text
            check("C5. 参数校验统一 400（action 非法/空数组/非数组/reject 缺 reason）")

            r = upload(c, u2, "产品部批量测试文档内容。" * 12, "prod-batch.txt")
            prod_pending = r.json()["data"]["id"]
            r = c.post("/api/admin/pending/batch", headers=H(dpa),
                       json={"action": "approve", "document_ids": [prod_pending]})
            assert r.status_code == 200, r.text
            data = r.json()["data"]
            assert data["failed"] == 1 and data["results"][0]["success"] is False, r.text
            assert data["results"][0]["code"] == 40300, r.text
            with SessionLocal() as db:
                assert db.get(models.Document, prod_pending).status == "pending"
            check("C4. 部门管理员批量处理非本部门条目失败且不被处理")

            r = c.get("/api/admin/audit-logs?action=approve", headers=H(admin_token))
            assert r.status_code == 200, r.text
            batch_approves = [a for a in r.json()["data"]["items"]
                              if a["detail"] and a["detail"].get("batch") is True]
            assert batch_approves, "批量通过应写 batch=true 审计"
            check("C1b. 批量通过逐条写审计且 detail.batch=true")

    print(f"\n=== ALL {len(passed)} S1 FIX TESTS PASSED ===")
    try:
        from app.db import engine
        engine.dispose()
    except Exception:
        pass
    shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
