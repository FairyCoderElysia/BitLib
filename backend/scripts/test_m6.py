# -*- coding: utf-8 -*-
"""M6 测试：搜索热词榜 + 输入联想（spec §3 F20）。

覆盖验收项（编号对应 contract-6.md §4）：
  1. 数据准备：admin 直入库 技术部/市场部/公开 文档 + 建 zhangsan（技术部）
  2. 空态：SearchLog 无记录时 hot-words 返回 {"items": []}
  3. 热词生成：多次搜索"机器学习""网络"后 hot-words 返回这些词
  4. 热词不泄露文档内容：直接搜索完整标题后，热词榜不含该完整标题字符串
  5. 热词缓存幂等：二次 hot-words cached=true 且 items 一致
  6. 联想可见性：admin 搜"市"含"市场部产品推广方案"；zhangsan 搜"市"不含
  7. 联想前缀命中：admin 搜"机"返回"机器学习实践指南"；zhangsan（技术部）可见该文档
  8. 联想缓存幂等：同用户同 q 二次请求 cached=true 且 items 一致
  9. 空前缀：suggest?q=（空）与纯空格返回 {"items": []}，HTTP 200

运行：cd backend && python scripts/test_m6.py
依赖本地 embedding 模型（bge-small-zh-v1.5，M3 已下载）；RERANKER_ENABLED=false 避免下载重排模型。
"""
import os
import shutil
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_ROOT = BACKEND_DIR / "data" / "test_m6"
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

from app import models  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_PASSWORD = "Admin@123456"


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

    def hot_items(c, token):
        r = c.get("/api/search/hot-words", headers=H(token))
        assert r.status_code == 200 and r.json()["code"] == 0, r.text
        return r.json()["data"]

    def suggest_items(c, token, q):
        r = c.get("/api/search/suggest", params={"q": q}, headers=H(token))
        assert r.status_code == 200 and r.json()["code"] == 0, r.text
        return r.json()["data"]

    try:
        with TestClient(app) as c:
            token_admin = login(c, "admin", ADMIN_PASSWORD)

            # ---------- 1. 数据准备 ----------
            with SessionLocal() as db:
                dept_tech = db.query(models.Department).filter(
                    models.Department.name == "技术部").first()
                dept_mkt = db.query(models.Department).filter(
                    models.Department.name == "市场部").first()
                assert dept_tech is not None and dept_mkt is not None, "默认部门未播种"

            def _write(name: str, text: str) -> Path:
                p = TEST_ROOT / name
                p.write_text(text, encoding="utf-8")
                return p

            # 技术部文档（zhangsan 可见）、市场部文档（zhangsan 不可见）、公开文档
            t_tech = ("机器学习实践指南：机器学习算法用于数据建模、预测与特征工程，"
                      "覆盖监督学习与无监督学习两类方法，是企业智能分析的核心技术。")
            t_mkt = ("市场部产品推广方案：围绕市场调研、竞品分析与客户画像制定推广策略，"
                     "面向目标客户群体开展精准营销活动。")
            t_pub = ("网络安全基础：介绍网络攻击类型、安全防护策略与应急预案，"
                     "帮助员工提升信息安全意识。常见威胁包括钓鱼邮件、恶意软件与"
                     "社会工程学攻击，需要定期开展安全培训与应急演练。")
            tech_id = direct_upload(c, token_admin, _write("d_tech.txt", t_tech),
                                    "机器学习实践指南", dept_tech.id)
            mkt_id = direct_upload(c, token_admin, _write("d_mkt.txt", t_mkt),
                                   "市场部产品推广方案", dept_mkt.id)
            pub_id = direct_upload(c, token_admin, _write("d_pub.txt", t_pub),
                                   "网络安全基础")

            # 建技术部普通用户 zhangsan
            r = c.post("/api/admin/users", headers=H(token_admin),
                       json={"username": "zhangsan", "password": "User@123456",
                             "role": "user", "department_id": dept_tech.id})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            token_zs = login(c, "zhangsan", "User@123456")
            check("1. 直入库 技术部/市场部/公开 文档 + zhangsan（技术部）就绪")

            # ---------- 2. 空态：SearchLog 无记录时热词空 ----------
            data = hot_items(c, token_admin)
            assert data["items"] == [], data
            check("2. SearchLog 空时 hot-words 返回空数组")

            # ---------- 3. 热词生成：多次搜索后出现对应词条 ----------
            for _ in range(3):
                c.get("/api/search", params={"q": "机器学习"}, headers=H(token_admin))
            for _ in range(2):
                c.get("/api/search", params={"q": "网络"}, headers=H(token_admin))
            # 直接搜索完整标题，验证热词不泄露文档内容
            c.get("/api/search", params={"q": "市场部产品推广方案"}, headers=H(token_admin))

            data = hot_items(c, token_admin)
            items = data["items"]
            assert "机器" in items, f"热词应含「机器」: {items}"
            assert "学习" in items, f"热词应含「学习」: {items}"
            assert "网络" in items, f"热词应含「网络」: {items}"
            check("3. 多次搜索后 hot-words 返回「机器学习」「网络」")

            # ---------- 4. 热词不泄露完整文档标题 ----------
            assert "市场部产品推广方案" not in items, f"热词泄露完整标题: {items}"
            for w in items:
                assert len(w) >= 2, f"热词含单字: {w}"
            check("4. 热词均为分词词条，不含完整文档标题")

            # ---------- 5. 热词缓存幂等 ----------
            data2 = hot_items(c, token_admin)
            assert data2["cached"] is True, data2
            assert data2["items"] == items, "缓存后 items 应一致"
            check("5. 二次 hot-words cached=true 且 items 一致")

            # ---------- 6. 联想可见性隔离 ----------
            d_admin_mkt = suggest_items(c, token_admin, "市")
            titles_admin_mkt = [it["title"] for it in d_admin_mkt["items"]]
            assert "市场部产品推广方案" in titles_admin_mkt, titles_admin_mkt
            assert d_admin_mkt["items"][0]["title"] == "市场部产品推广方案", \
                "前缀命中应排最前"
            d_zs_mkt = suggest_items(c, token_zs, "市")
            titles_zs_mkt = [it["title"] for it in d_zs_mkt["items"]]
            assert "市场部产品推广方案" not in titles_zs_mkt, \
                f"zhangsan 不应看到市场部文档: {titles_zs_mkt}"
            check("6. admin 搜「市」含市场部文档且前缀置顶；zhangsan 不可见")

            # ---------- 7. 联想前缀命中 + 本部门可见 ----------
            d_admin_ji = suggest_items(c, token_admin, "机")
            titles_admin_ji = [it["title"] for it in d_admin_ji["items"]]
            assert "机器学习实践指南" in titles_admin_ji, titles_admin_ji
            assert len(d_admin_ji["items"]) <= 8, "联想条数应 ≤ 8"
            d_zs_ji = suggest_items(c, token_zs, "机")
            titles_zs_ji = [it["title"] for it in d_zs_ji["items"]]
            assert "机器学习实践指南" in titles_zs_ji, \
                f"zhangsan 应看到本部门文档: {titles_zs_ji}"
            check("7. 前缀联想命中「机器学习实践指南」，本部门用户可见")

            # ---------- 8. 联想缓存幂等（同用户同 q） ----------
            d_zs_ji2 = suggest_items(c, token_zs, "机")
            assert d_zs_ji2["cached"] is True, d_zs_ji2
            assert d_zs_ji2["items"] == d_zs_ji["items"], "缓存后联想 items 应一致"
            check("8. 二次 suggest cached=true 且 items 一致")

            # ---------- 9. 空前缀 ----------
            for empty_q in ("", "   "):
                r = c.get("/api/search/suggest", params={"q": empty_q}, headers=H(token_admin))
                assert r.status_code == 200, r.text
                assert r.json()["data"]["items"] == [], r.text
            check("9. suggest 空 q / 纯空格返回空数组，HTTP 200")

            # ---------- 10. 改部门/下架后 suggest 缓存失效（eval-6 残留建议） ----------
            # 先让 mkt 标题进入缓存（admin 搜"市"）
            r = c.get("/api/search/suggest", params={"q": "市"}, headers=H(token_admin))
            assert r.status_code == 200 and any(
                i["title"] == "市场部产品推广方案" for i in r.json()["data"]["items"]), r.text
            # 第二次命中缓存
            r = c.get("/api/search/suggest", params={"q": "市"}, headers=H(token_admin))
            assert r.json()["data"].get("cached") is True, r.text
            # 下架市场部文档
            r = c.patch(f"/api/admin/documents/{mkt_id}", headers=H(token_admin),
                        json={"status": "offline"})
            assert r.status_code == 200, r.text
            # 再次联想：缓存命中路径应剔除已下架文档
            r = c.get("/api/search/suggest", params={"q": "市"}, headers=H(token_admin))
            assert r.status_code == 200, r.text
            titles = [i["title"] for i in r.json()["data"]["items"]]
            assert "市场部产品推广方案" not in titles, f"缓存未失效: {titles}"
            check("10. 文档下架后 suggest 缓存命中不再返回该标题（可见性兜底生效）")

        print(f"\n=== ALL {len(passed)} M6 TESTS PASSED ===")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
