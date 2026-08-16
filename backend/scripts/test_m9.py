# -*- coding: utf-8 -*-
"""Sprint 9 测试：相似文档推荐（F18）。

覆盖（对应 contract-9 §4 验收）：
  1. 主题相近互推：D1/D2 机器学习（公开）互推，D2 的 distance < D3（财务）的 distance
  2. 不推荐自身：任意文档 related 不含自身 id
  3. 权限过滤：zhangsan（技术部）请求 D1 related 不含 D4（市场部）；
     admin 同接口含 D4 → 同一文档推荐因用户而不同
  4. 无相关文档返回空：库中仅 1 篇文档时 related 返回 data: []
  5. 非 approved 返回空：D_pending（pending、无向量）→ related data: [] 且 HTTP 200
  6. 无权访问返回 404：zhangsan 请求 D4（市场部）related → 404（不泄露存在性）
  7. 性能：小库（≤5 文档）related 端到端 < 1s

运行：cd backend && python scripts/test_m9.py
依赖本地 embedding 模型（bge-small-zh-v1.5，M3 已下载，同 test_m5）。
"""
import os
import shutil
import sys
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_ROOT = BACKEND_DIR / "data" / "test_m9"
TEST_DB = (TEST_ROOT / "app.db").as_posix()

os.environ["ADMIN_INITIAL_PASSWORD"] = "Admin@123456"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
os.environ["CHROMA_DIR"] = str(TEST_ROOT / "chroma")
os.environ["MAX_UPLOAD_MB"] = "5"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["EMBEDDING_MODE"] = "local"

from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.ingest import ingest_text  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_PASSWORD = "Admin@123456"
NEW_ADMIN_PASSWORD = "Admin@123456!"  # A7 硬拦截适配：首登 admin 改密用

# ---------------- 测试文档内容 ----------------
# D1/D2：机器学习/深度学习主题（公开）；D3：财务/报销主题（公开）；D4：机器学习主题（市场部）

ML_TEXT = ("机器学习与深度学习模型训练实践：本文介绍数据预处理、特征工程、模型选择与"
           "超参数调优等关键步骤，并对比卷积神经网络与循环神经网络在不同业务场景下的"
           "适用性。掌握梯度下降、反向传播与模型评估指标，有助于构建稳定可靠的智能系统，"
           "提升企业预测与决策效率。")
FIN_TEXT = ("财务报销管理制度：本文规范员工差旅报销、采购付款与费用分摊流程，"
            "涵盖发票审核、预算控制与月末结算等环节。财务人员应核对报销单据的真实性与"
            "合规性，严格执行审批权限，确保账目清晰并按时完成税务申报与审计归档。")
MARKET_ML_TEXT = ("机器学习在市场营销中的应用：通过用户行为分析与购买预测模型，"
                  "实现精准推荐与客户流失预警。本文结合深度学习与自然语言处理技术，"
                  "介绍用户画像构建、点击率预估与营销活动效果评估等方法，"
                  "帮助市场团队优化投放策略并提升转化率。")


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
        data = r.json()["data"]
        # A7 硬拦截适配：首登 admin 未改密前业务 API 均 403；先改密再继续。
        if data["user"].get("must_change_password"):
            rr = c.post("/api/auth/change-password", headers=H(data["token"]),
                        json={"old_password": password, "new_password": NEW_ADMIN_PASSWORD})
            assert rr.status_code == 200 and rr.json()["code"] == 0, rr.text
        return data["token"]

    def create_doc(db, title, text, dept_id, uploaded_by, ingest=True):
        """建 Document 记录；ingest=True 走 ingest_text 置 approved。"""
        doc = models.Document(
            title=title,
            file_name=f"{title}.txt",
            file_path="",
            file_type="txt",
            file_size=len(text),
            file_hash=f"m9-{title}",
            status=models.STATUS_PENDING,
            department_id=dept_id,
            source=models.SOURCE_UPLOAD,
            uploaded_by=uploaded_by,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        if ingest:
            ingest_text(db, doc, text)
            db.refresh(doc)
        return doc.id

    try:
        with TestClient(app) as c:
            token_admin = login(c, "admin", ADMIN_PASSWORD)

            # 默认部门：技术部（部门 A）/ 市场部（部门 B）
            with SessionLocal() as db:
                dept_tech = db.query(models.Department).filter(
                    models.Department.name == "技术部").first()
                dept_market = db.query(models.Department).filter(
                    models.Department.name == "市场部").first()
                assert dept_tech is not None and dept_market is not None, "默认部门未播种"

            # 建 zhangsan（技术部普通用户）
            r = c.post("/api/admin/users", headers=H(token_admin),
                       json={"username": "zhangsan", "password": "zs123456",
                             "role": "user", "department_id": dept_tech.id})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            zhangsan_id = r.json()["data"]["id"]
            token_zs = login(c, "zhangsan", "zs123456")

            # ---------- 1. 库中仅 1 篇 → 无相关文档返回空 ----------
            with SessionLocal() as db:
                d1 = create_doc(db, "机器学习实践指南", ML_TEXT, None, zhangsan_id)
            r = c.get(f"/api/documents/{d1}/related", headers=H(token_zs))
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            assert r.json()["data"] == [], f"仅 1 篇文档应返回空推荐: {r.json()}"
            check("1. 无相关文档：库中仅自身时 related 返回空数组")

            # ---------- 2. 入库其余 3 篇（D2 同主题公开 / D3 财务公开 / D4 同主题市场部） ----------
            with SessionLocal() as db:
                d2 = create_doc(db, "深度学习模型训练", ML_TEXT, None, zhangsan_id)
                d3 = create_doc(db, "财务报销管理制度", FIN_TEXT, None, zhangsan_id)
                d4 = create_doc(db, "市场营销机器学习", MARKET_ML_TEXT, dept_market.id, zhangsan_id)

            # ---------- 3. 主题相近互推 + 排序 ----------
            r = c.get(f"/api/documents/{d1}/related", headers=H(token_zs))
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            items = r.json()["data"]
            ids = [it["id"] for it in items]
            assert d2 in ids, f"D1 推荐应含同主题 D2: {ids}"
            # 若 D3 与 D2 同时出现，D2 的 distance 应更小（相关度排序正确）
            if d2 in ids and d3 in ids:
                dist_d2 = next(it["distance"] for it in items if it["id"] == d2)
                dist_d3 = next(it["distance"] for it in items if it["id"] == d3)
                assert dist_d2 < dist_d3, f"D2 应比 D3 更相似: {items}"
            check("3. 主题相近互推：D1 推荐含 D2 且 D2.distance < D3.distance")

            # ---------- 4. 不推荐自身 ----------
            for did in (d1, d2, d3):
                r = c.get(f"/api/documents/{did}/related", headers=H(token_zs))
                ids = [it["id"] for it in r.json()["data"]]
                assert did not in ids, f"文档 {did} 不应推荐自身: {ids}"
            check("4. 不推荐自身：各文档 related 不含自身 id")

            # ---------- 5. 权限过滤：zhangsan 不见市场部 D4；admin 可见 ----------
            r = c.get(f"/api/documents/{d1}/related", headers=H(token_zs))
            ids_zs = [it["id"] for it in r.json()["data"]]
            assert d4 not in ids_zs, f"zhangsan 推荐不应含市场部 D4: {ids_zs}"
            r = c.get(f"/api/documents/{d1}/related", headers=H(token_admin))
            ids_admin = [it["id"] for it in r.json()["data"]]
            assert d4 in ids_admin, f"admin 推荐应含市场部 D4: {ids_admin}"
            check("5. 权限过滤：zhangsan 不见 D4、admin 见 D4（同一文档推荐因用户而异）")

            # ---------- 6. 非 approved 返回空（pending、无向量） ----------
            with SessionLocal() as db:
                d_pending = create_doc(db, "待审批文档", ML_TEXT, dept_tech.id,
                                       zhangsan_id, ingest=False)
            r = c.get(f"/api/documents/{d_pending}/related", headers=H(token_zs))
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            assert r.json()["data"] == [], f"pending 文档应返回空推荐: {r.json()}"
            check("6. 非 approved：pending 文档 related 返回空数组且 HTTP 200")

            # ---------- 7. 无权访问返回 404 ----------
            r = c.get(f"/api/documents/{d4}/related", headers=H(token_zs))
            assert r.status_code == 404, f"zhangsan 请求市场部 D4 应 404: {r.text}"
            check("7. 无权访问：zhangsan 请求市场部 D4 related → 404")

            # ---------- 8. 性能：小库端到端 < 1s ----------
            t0 = time.perf_counter()
            r = c.get(f"/api/documents/{d1}/related", headers=H(token_zs))
            elapsed = time.perf_counter() - t0
            assert r.status_code == 200, r.text
            assert elapsed < 1.0, f"related 耗时 {elapsed:.3f}s 应 < 1s"
            check(f"8. 性能：小库 related 端到端 {elapsed:.3f}s < 1s")

        print(f"\n=== ALL {len(passed)} M9 TESTS PASSED ===")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
