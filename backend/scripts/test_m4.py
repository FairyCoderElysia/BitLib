# -*- coding: utf-8 -*-
"""M4 测试：混合检索 / 权限过滤 / featured 加权 / 详情预览下载 / 收藏夹 / 缓存 / QA / SearchLog。

覆盖任务 9 项（编号对应任务清单）：
  1. 数据准备：直入库 3 部门文档 + featured 文档 + 普通用户 + dept_admin
  2. GET /api/search 关键词命中 / 部门权限过滤 / admin 全量
  3. featured 加权置顶（同相关度下排前）
  4. 空结果 total=0 不报错
  5. 详情/预览/下载 状态-可见性矩阵（approved/不可见/pending/offline）
  6. 收藏夹 CRUD + 收藏/取消/409/403 + count
  7. QA 问答（monkeypatch llm.chat）+ citations 引用来源 + 不可见文档不入引用
  8. 热点搜索缓存：同查询二次 cached=true；下架后缓存失效且结果不含该文档
  9. SearchLog 有记录

运行：cd backend && python scripts/test_m4.py
依赖本地 embedding 模型（bge-small-zh-v1.5，M3 已下载）；RERANKER_ENABLED=false 避免下载重排模型。
"""
import os
import shutil
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_ROOT = BACKEND_DIR / "data" / "test_m4"
TEST_DB = (TEST_ROOT / "app.db").as_posix()

os.environ["ADMIN_INITIAL_PASSWORD"] = "Admin@123456"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
os.environ["CHROMA_DIR"] = str(TEST_ROOT / "chroma")
os.environ["MAX_UPLOAD_MB"] = "5"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["EMBEDDING_MODE"] = "local"
os.environ["RERANKER_ENABLED"] = "false"  # 跳过 cross-encoder，走 RRF + featured 加权

from fastapi.testclient import TestClient  # noqa: E402

from app import llm as llm_mod  # noqa: E402
from app import models  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_PASSWORD = "Admin@123456"
FAKE_ANSWER = "根据资料：机器学习算法用于数据建模、预测与特征工程，是企业智能分析的核心技术。"


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

    def direct_upload(c, token, path: Path, title: str, department_id=None) -> int:
        """admin 直入库（approved），返回文档 id。"""
        with path.open("rb") as f:
            files = {"file": (path.name, f, "application/octet-stream")}
            data = {"title": title}
            if department_id is not None:
                data["department_id"] = str(department_id)
            r = c.post("/api/admin/documents/upload", headers=H(token), files=files, data=data)
        assert r.status_code == 200 and r.json()["code"] == 0, r.text
        d = r.json()["data"]
        assert d["status"] == "approved", d
        return d["id"]

    try:
        with TestClient(app) as c:
            token_admin = login(c, "admin", ADMIN_PASSWORD)

            # ---------- 1. 数据准备 ----------
            with SessionLocal() as db:
                dept_a = db.query(models.Department).filter(
                    models.Department.name == "技术部").first()
                dept_b = db.query(models.Department).filter(
                    models.Department.name == "产品部").first()
                assert dept_a is not None and dept_b is not None, "默认部门未播种"

            def _write(name: str, text: str) -> Path:
                p = TEST_ROOT / name
                p.write_text(text, encoding="utf-8")
                return p

            t_a = ("本部门技术资料：机器学习算法用于数据挖掘与预测模型构建。"
                   "机器学习算法在业务推荐系统中有广泛应用，涵盖监督学习与无监督学习两类方法。"
                   "项目组持续迭代算法模型，提升推荐准确率与用户满意度。")
            t_feat = ("公开技术资料：机器学习算法用于数据建模与智能预测。"
                      "机器学习算法的核心包括特征工程与模型评估，广泛应用于企业数据分析场景。"
                      "本文档供全员学习，帮助理解算法基本原理与典型应用。")
            t_pub = ("企业文档管理系统使用指南：介绍文档上传、审批、检索与收藏操作流程，"
                     "帮助员工高效管理企业知识资产。全文检索支持关键词与语义混合查询。")
            t_b = ("市场营销策略培训材料：包含市场调研、竞品分析与客户画像等核心营销方法论，"
                   "供产品部门与销售团队参考使用。")

            a1_id = direct_upload(c, token_admin, _write("d_a.txt", t_a), "机器学习算法指南", dept_a.id)
            b2_id = direct_upload(c, token_admin, _write("d_b.txt", t_b), "市场营销培训", dept_b.id)
            pub3_id = direct_upload(c, token_admin, _write("d_pub.txt", t_pub), "文档系统使用指南")
            feat4_id = direct_upload(c, token_admin, _write("d_feat.txt", t_feat), "机器学习公开资料")

            # 建账号：B 部门普通用户 + A 部门 dept_admin
            for u, pwd, role, dept in (("user_b", "User@123456", "user", dept_b.id),
                                       ("dept_a_admin", "Dept@123456", "dept_admin", dept_a.id)):
                r = c.post("/api/admin/users", headers=H(token_admin),
                           json={"username": u, "password": pwd, "role": role,
                                 "department_id": dept})
                assert r.status_code == 200 and r.json()["code"] == 0, r.text
            token_b = login(c, "user_b", "User@123456")
            token_da = login(c, "dept_a_admin", "Dept@123456")

            # featured 标记（当前无管理端接口，直改库；spec F8：标记后检索加权生效）
            with SessionLocal() as db:
                f = db.get(models.Document, feat4_id)
                f.is_featured = True
                db.commit()
            check("1. 直入库 3 部门文档 + featured 文档 + 普通用户/dept_admin 就绪")

            # ---------- 2. 检索与权限过滤 ----------
            r = c.get("/api/search", params={"q": "机器学习"}, headers=H(token_admin))
            ids_admin = [it["id"] for it in r.json()["data"]["items"]]
            assert a1_id in ids_admin and feat4_id in ids_admin, f"admin 应命中 A 部门+公开: {ids_admin}"
            r = c.get("/api/search", params={"q": "机器学习"}, headers=H(token_b))
            ids_b = [it["id"] for it in r.json()["data"]["items"]]
            assert a1_id not in ids_b, f"B 用户不应看到 A 部门文档: {ids_b}"
            assert feat4_id in ids_b, "B 用户应看到公开 featured 文档"
            r = c.get("/api/search", params={"q": "企业文档"}, headers=H(token_admin))
            assert pub3_id in [it["id"] for it in r.json()["data"]["items"]]
            check("2. 关键词命中 + B 用户不可见 A 部门文档 + admin 全量")

            # ---------- 3. featured 加权置顶 ----------
            r = c.get("/api/search", params={"q": "机器学习"}, headers=H(token_admin))
            ids = [it["id"] for it in r.json()["data"]["items"]]
            assert a1_id in ids and feat4_id in ids, f"两文档都应命中: {ids}"
            assert ids.index(feat4_id) < ids.index(a1_id), f"同相关度下 featured 应排前: {ids}"
            check("3. 同相关度下 featured 文档置顶")

            # ---------- 4. 空结果 ----------
            r = c.get("/api/search", params={"q": "绝对不存在的词xyzabc"}, headers=H(token_b))
            assert r.status_code == 200, r.text
            data = r.json()["data"]
            assert data["total"] == 0 and data["items"] == [], data
            check("4. 搜索无结果 total=0 且不报错")

            # ---------- 5. 详情 / 预览 / 下载 ----------
            # 5.1 approved 可见者：detail/preview/download 均 200
            r = c.get(f"/api/documents/{pub3_id}", headers=H(token_b))
            assert r.status_code == 200 and r.json()["data"]["content_text"], r.text
            assert c.get(f"/api/documents/{pub3_id}/preview", headers=H(token_b)).status_code == 200
            assert c.get(f"/api/documents/{pub3_id}/download", headers=H(token_b)).status_code == 200
            # 5.2 不可见用户（A 部门文档对 B 用户）→ 404 不泄露存在性
            for suffix in ("", "/preview", "/download"):
                assert c.get(f"/api/documents/{a1_id}{suffix}",
                             headers=H(token_b)).status_code == 404, suffix
            # 5.3 pending：上传者可预览不可下载；审批者可预览；他部门 dept_admin 不可预览
            fp = TEST_ROOT / "pending.txt"
            fp.write_text("产品需求评审会议纪要草稿，内容待补充完整后正式归档。", encoding="utf-8")
            with fp.open("rb") as f:
                r = c.post("/api/documents/upload", headers=H(token_b),
                           files={"file": (fp.name, f, "text/plain")}, data={"title": "待审纪要"})
            assert r.status_code == 200 and r.json()["data"]["status"] == "pending", r.text
            pid = r.json()["data"]["id"]
            r = c.get(f"/api/documents/{pid}", headers=H(token_b))
            assert r.status_code == 200 and "content_text" not in r.json()["data"], r.text
            assert c.get(f"/api/documents/{pid}/preview", headers=H(token_b)).status_code == 200
            assert c.get(f"/api/documents/{pid}/download", headers=H(token_b)).status_code == 404
            assert c.get(f"/api/documents/{pid}/preview", headers=H(token_admin)).status_code == 200
            assert c.get(f"/api/documents/{pid}/download", headers=H(token_admin)).status_code == 404
            assert c.get(f"/api/documents/{pid}/preview", headers=H(token_da)).status_code == 404
            # 5.4 offline：全角色不可访问（spec §2.4 矩阵）
            with SessionLocal() as db:
                d = db.get(models.Document, b2_id)
                d.status = models.STATUS_OFFLINE
                db.commit()
            for suffix in ("", "/preview", "/download"):
                assert c.get(f"/api/documents/{b2_id}{suffix}",
                             headers=H(token_b)).status_code == 404, f"普通用户 {suffix}"
                assert c.get(f"/api/documents/{b2_id}{suffix}",
                             headers=H(token_admin)).status_code == 404, f"admin {suffix}"
            check("5. 详情/预览/下载按状态-可见性矩阵生效（含 offline 全角色 404）")

            # ---------- 6. 收藏夹 CRUD + 收藏/取消 ----------
            r = c.post("/api/favorites/folders", headers=H(token_b), json={"name": "常用"})
            assert r.status_code == 200, r.text
            fid = r.json()["data"]["id"]
            r = c.patch(f"/api/favorites/folders/{fid}", headers=H(token_b), json={"name": "重要"})
            assert r.status_code == 200 and r.json()["data"]["name"] == "重要", r.text
            r = c.post("/api/favorites", headers=H(token_b),
                       json={"document_id": pub3_id, "folder_id": fid})
            assert r.status_code == 200, r.text
            r = c.post("/api/favorites", headers=H(token_b),
                       json={"document_id": pub3_id, "folder_id": fid})
            assert r.status_code == 409, r.text  # 重复收藏幂等提示
            r = c.post("/api/favorites", headers=H(token_b),
                       json={"document_id": a1_id, "folder_id": fid})
            assert r.status_code == 403, r.text  # 收藏不可见文档
            r = c.get("/api/favorites/folders", headers=H(token_b))
            folder_item = [x for x in r.json()["data"]["items"] if x["id"] == fid][0]
            assert folder_item["count"] == 1, folder_item
            r = c.get("/api/favorites", headers=H(token_b))
            assert pub3_id in [it["document"]["id"] for it in r.json()["data"]["items"]], r.text
            r = c.delete(f"/api/favorites/{pub3_id}", headers=H(token_b))  # 取消收藏
            assert r.status_code == 200, r.text
            r = c.get("/api/favorites/folders", headers=H(token_b))
            folder_item = [x for x in r.json()["data"]["items"] if x["id"] == fid][0]
            assert folder_item["count"] == 0, folder_item
            r = c.delete(f"/api/favorites/folders/{fid}", headers=H(token_b))
            assert r.status_code == 200, r.text
            check("6. 收藏夹新建/重命名/删除 + 收藏/取消/409/403 + count")

            # ---------- 7. QA 问答（monkeypatch llm.chat，citations 权限过滤） ----------
            orig_chat = llm_mod.chat
            llm_mod.chat = lambda messages, temperature=0.3: FAKE_ANSWER  # noqa: E731
            try:
                r = c.post("/api/qa/ask", headers=H(token_admin),
                           json={"question": "机器学习算法是什么？"})
                assert r.status_code == 200, r.text
                data = r.json()["data"]
                assert data["answer"] == FAKE_ANSWER, data
                assert data.get("session_id") is not None, data
                cits = data["citations"]
                assert cits, "citations 为空"
                for ci in cits:
                    assert ci.get("document_id") and ci.get("title") and ci.get("parent_id"), ci
                assert a1_id in {ci["document_id"] for ci in cits}, cits
                r2 = c.post("/api/qa/ask", headers=H(token_b),
                            json={"question": "机器学习算法是什么？"})
                assert r2.status_code == 200, r2.text
                cits2 = r2.json()["data"]["citations"]
                assert a1_id not in {ci["document_id"] for ci in cits2}, \
                    f"A 部门文档不应进入 B 用户引用: {cits2}"
            finally:
                llm_mod.chat = orig_chat
            check("7. QA 返回 answer+citations(document_id/title/parent_id)，不可见文档不入引用")

            # ---------- 8. 热点搜索缓存：命中 + 下架失效 ----------
            r1 = c.get("/api/search", params={"q": "机器学习算法"}, headers=H(token_admin))
            d1 = r1.json()["data"]
            assert d1["cached"] is False, d1
            r2 = c.get("/api/search", params={"q": "机器学习算法"}, headers=H(token_admin))
            d2 = r2.json()["data"]
            assert d2["cached"] is True, "同查询第二次应命中缓存"
            assert feat4_id in [it["id"] for it in d2["items"]], d2
            # 下架 featured 文档 → 缓存失效，结果不得再含该文档
            with SessionLocal() as db:
                d = db.get(models.Document, feat4_id)
                d.status = models.STATUS_OFFLINE
                db.commit()
            r3 = c.get("/api/search", params={"q": "机器学习算法"}, headers=H(token_admin))
            d3 = r3.json()["data"]
            ids3 = [it["id"] for it in d3["items"]]
            assert feat4_id not in ids3, f"下架后检索仍返回: {ids3}"
            assert d3["cached"] is False, "缓存未失效（应回源重算）"
            check("8. 同查询二次命中缓存 cached=true；下架后缓存失效、结果不含下架文档")

            # ---------- 9. SearchLog 落库 ----------
            with SessionLocal() as db:
                n = db.query(models.SearchLog).count()
                assert n >= 1, "SearchLog 无记录"
            check("9. SearchLog 有搜索记录")

        print(f"\n=== ALL {len(passed)} M4 TESTS PASSED ===")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
