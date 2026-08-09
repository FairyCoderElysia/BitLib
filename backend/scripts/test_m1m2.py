# -*- coding: utf-8 -*-
"""M1+M2 冒烟测试（TestClient + assert 全绿）——对应 contract-1 验收清单。

运行方式：
    cd backend && python scripts/test_m1m2.py

使用独立的测试数据库（data/test_m1m2/app.db）与上传目录，不污染正式数据；
默认播种密码 ADMIN_INITIAL_PASSWORD=Admin@123456（由本脚本注入环境变量）。
"""
import os
import shutil
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_ROOT = BACKEND_DIR / "data" / "test_m1m2"
TEST_DB = (TEST_ROOT / "app.db").as_posix()

# 必须在导入 app 之前设置环境变量（pydantic-settings：环境变量优先于 .env）
os.environ["ADMIN_INITIAL_PASSWORD"] = "Admin@123456"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
os.environ["MAX_UPLOAD_MB"] = "2"  # 便于验证大小上限
os.environ.setdefault("SECRET_KEY", "test-secret-key")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.db import engine  # noqa: E402

ADMIN_PASSWORD = "Admin@123456"


def H(token):
    return {"Authorization": f"Bearer {token}"}


def main():
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    passed: list = []

    def check(name):
        passed.append(name)
        print(f"  PASS: {name}")

    try:
        with TestClient(app) as c:
            # ---------- 1. 认证 ----------
            r = c.post("/api/auth/login",
                       json={"username": "admin", "password": ADMIN_PASSWORD})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            data = r.json()["data"]
            admin_token = data["token"]
            assert data["user"]["username"] == "admin"
            assert data["user"]["role"] == "admin"
            check("1. 登录成功返回 token + 用户信息(角色/用户名)")

            r = c.post("/api/auth/login",
                       json={"username": "admin", "password": "wrong-password"})
            assert r.status_code == 401, r.text
            check("1. 密码错误返回 401（不泄露账号存在性）")

            r = c.get("/api/auth/me")
            assert r.status_code == 401, r.text
            check("1. 无 token 访问受保护接口返回 401")

            r = c.post("/api/auth/register", json={"username": "x", "password": "y"})
            assert r.status_code == 404, r.text  # 无开放注册入口
            check("1. 无开放注册（/auth/register 不存在）")

            # 部门列表（登录可用）
            r = c.get("/api/auth/departments", headers=H(admin_token))
            assert r.status_code == 200, r.text
            depts = {d["name"]: d["id"] for d in r.json()["data"]}
            tech_id, prod_id = depts["技术部"], depts["产品部"]

            # 建号 helper
            def create_user(username, password, role="user", department_id=None):
                payload = {"username": username, "password": password, "role": role}
                if department_id is not None:
                    payload["department_id"] = department_id
                return c.post("/api/admin/users", json=payload, headers=H(admin_token))

            r = create_user("user1", "user123", "user", tech_id)
            assert r.status_code == 200, r.text
            r = create_user("dpa1", "dpa123", "dept_admin", tech_id)
            assert r.status_code == 200, r.text
            r = create_user("userB", "userb123", "user", prod_id)
            assert r.status_code == 200, r.text
            r = create_user("user4", "user4123", "user", None)  # 无部门用户
            assert r.status_code == 200, r.text

            def login(u, p):
                rr = c.post("/api/auth/login", json={"username": u, "password": p})
                assert rr.status_code == 200, rr.text
                return rr.json()["data"]["token"]

            t_user1 = login("user1", "user123")
            t_dpa = login("dpa1", "dpa123")
            t_userB = login("userB", "userb123")
            t_user4 = login("user4", "user4123")

            # ---------- 2. 普通用户访问管理端 403 ----------
            r = c.get("/api/admin/pending", headers=H(t_user1))
            assert r.status_code == 403, r.text
            check("2. 普通用户访问 /api/admin/* 返回 403")

            # ---------- 3. 用户上传（pending / 归属 / 校验 / 去重） ----------
            r = c.post("/api/documents/upload", headers=H(t_user1),
                       files={"file": ("notes.txt",
                                       b"hello world document content", "text/plain")})
            assert r.status_code == 200, r.text
            d = r.json()["data"]
            assert d["status"] == "pending", d
            assert d["department_id"] == tech_id, d
            check("3. 用户上传成功 → pending，部门归属 = 上传者部门")

            r = c.post("/api/documents/upload", headers=H(t_user1),
                       files={"file": ("bad.exe", b"MZ...", "application/octet-stream")})
            assert r.status_code == 400, r.text
            check("3. 不支持格式返回 400")

            r = c.post("/api/documents/upload", headers=H(t_user1),
                       files={"file": ("big.txt", b"x" * (2 * 1024 * 1024 + 1),
                                       "text/plain")})
            assert r.status_code == 400, r.text
            check("3. 超过大小上限返回 400")

            r = c.post("/api/documents/upload", headers=H(t_user1),
                       files={"file": ("notes.txt",
                                       b"hello world document content", "text/plain")})
            assert r.status_code == 409, r.text
            assert "该文件已存在" in r.json()["message"]
            check("3. sha256 重复上传返回 409「该文件已存在」")

            r = c.post("/api/documents/upload", headers=H(t_user4),
                       files={"file": ("public.md", b"# public doc\ncontent",
                                       "text/markdown")})
            assert r.status_code == 200, r.text
            assert r.json()["data"]["department_id"] is None, r.text
            check("3. 无部门用户上传 → 公开（department_id 为空）")

            # ---------- 4. 审批通过 / 拒绝 ----------
            r = c.post("/api/documents/upload", headers=H(t_user1),
                       files={"file": ("approve.txt",
                                       b"text to approve with enough length for the ingestion pipeline to pass the minimum text check " * 3, "text/plain")})
            doc_approve = r.json()["data"]["id"]
            r = c.post(f"/api/admin/pending/{doc_approve}/approve",
                       headers=H(admin_token))
            assert r.status_code == 200, r.text
            assert r.json()["data"]["status"] == "approved", r.text
            check("4. admin 审批通过 → processing→approved")

            r = c.post("/api/documents/upload", headers=H(t_user1),
                       files={"file": ("reject.txt", b"text to reject", "text/plain")})
            doc_reject = r.json()["data"]["id"]
            r = c.post(f"/api/admin/pending/{doc_reject}/reject",
                       headers=H(admin_token), json={"reason": "内容与主题不符"})
            assert r.status_code == 200, r.text
            r = c.get("/api/documents/mine", headers=H(t_user1))
            assert r.status_code == 200, r.text
            mine = {m["id"]: m for m in r.json()["data"]["items"]}
            assert mine[doc_reject]["status"] == "rejected", mine
            assert mine[doc_reject]["reject_reason"] == "内容与主题不符"
            check("4. admin 拒绝 → rejected + 原因（我的上传可见）")

            # ---------- 5. dept_admin 仅见本部门 pending ----------
            r = c.post("/api/documents/upload", headers=H(t_user1),
                       files={"file": ("a.txt", b"dept a doc", "text/plain")})
            doc_a = r.json()["data"]["id"]
            r = c.post("/api/documents/upload", headers=H(t_userB),
                       files={"file": ("b.txt", b"dept b doc", "text/plain")})
            doc_b = r.json()["data"]["id"]

            r = c.get("/api/admin/pending", headers=H(t_dpa))
            assert r.status_code == 200, r.text
            ids = {i["id"] for i in r.json()["data"]["items"]}
            assert doc_a in ids and doc_b not in ids, (doc_a, doc_b, ids)
            check("5. dept_admin 仅见本部门 pending")

            r = c.get("/api/admin/pending", headers=H(admin_token))
            assert r.status_code == 200, r.text
            ids = {i["id"] for i in r.json()["data"]["items"]}
            assert doc_a in ids and doc_b in ids, ids
            check("5. admin 审批中心见全部 pending")

            # ---------- 6. 撤回 ----------
            r = c.delete(f"/api/documents/{doc_a}", headers=H(t_userB))
            assert r.status_code == 403, r.text
            check("6. 撤回他人文档返回 403")

            r = c.delete(f"/api/documents/{doc_a}", headers=H(t_user1))
            assert r.status_code == 200, r.text
            r = c.post(f"/api/admin/pending/{doc_a}/approve", headers=H(admin_token))
            assert r.status_code in (404, 400), r.text
            check("6. 本人撤回 pending 成功，撤回后不可再审批")

            # ---------- 7. 管理端直入库 ----------
            r = c.post("/api/admin/documents/upload", headers=H(admin_token),
                       files={"file": ("direct.txt",
                                       b"direct upload document with enough content for the ingestion pipeline to pass " * 4,
                                       "text/plain")},
                       data={"department_id": str(tech_id)})
            assert r.status_code == 200, r.text
            assert r.json()["data"]["status"] == "approved", r.text
            check("7. 管理端直入库上传 → 直接 approved（指定部门）")

            r = c.post("/api/admin/documents/upload", headers=H(t_dpa),
                       files={"file": ("d2.txt", b"direct dept", "text/plain")},
                       data={"department_id": str(prod_id)})
            assert r.status_code == 403, r.text
            check("7. dept_admin 向其他部门直入库返回 403")

            # ---------- 8. 审计日志 ----------
            r = c.get("/api/admin/audit-logs", headers=H(admin_token))
            assert r.status_code == 200, r.text
            actions = {a["action"] for a in r.json()["data"]["items"]}
            need = {"upload", "approve", "reject", "withdraw",
                    "direct_upload", "user_create"}
            assert need.issubset(actions), (need, actions)
            check("8. 审计日志包含上传/审批/拒绝/撤回/直入库/建号")

            r = c.get("/api/admin/audit-logs?action=approve", headers=H(admin_token))
            assert r.status_code == 200, r.text
            assert all(a["action"] == "approve" for a in r.json()["data"]["items"])
            check("8. 审计日志按动作筛选生效")

            # ---------- 9. 用户管理 ----------
            r = create_user("user3", "user3123", "user", tech_id)
            assert r.status_code == 200, r.text
            user3_id = r.json()["data"]["id"]
            t_user3 = login("user3", "user3123")

            r = c.get("/api/admin/pending", headers=H(t_user3))
            assert r.status_code == 403, r.text
            r = c.patch(f"/api/admin/users/{user3_id}", headers=H(admin_token),
                        json={"role": "dept_admin"})
            assert r.status_code == 200, r.text
            # 角色从数据库实时读取：旧 token 立即生效
            r = c.get("/api/admin/pending", headers=H(t_user3))
            assert r.status_code == 200, r.text
            r = c.patch(f"/api/admin/users/{user3_id}", headers=H(admin_token),
                        json={"password": "newpass123"})
            assert r.status_code == 200, r.text
            r = c.post("/api/auth/login",
                       json={"username": "user3", "password": "newpass123"})
            assert r.status_code == 200, r.text
            check("9. admin 建号 / 改角色（旧 token 生效）/ 重置密码")

        print(f"\n=== ALL {len(passed)} M1+M2 TESTS PASSED ===")
    finally:
        engine.dispose()
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
