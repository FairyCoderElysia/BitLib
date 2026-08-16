# -*- coding: utf-8 -*-
"""Sprint 10 测试：批量下载（F19）。

覆盖（对应 contract-10 §4 验收）：
  1. 正常批量：D1/D2/D3（approved 可见）→ 200、application/zip、X-Skipped-Count=0，
     zip 恰 3 文档 + manifest.txt（共 4 条目）且 arcname 与 file_name 一致、内容非空
  2. 混入不可见：加市场部 D4 → 200 + X-Skipped-Count=1，zip 3 文档 + manifest（不含 D4）
  3. 混入文件丢失：D1 + D5（物理文件缺失）→ 200 + X-Skipped-Count=1，zip 1 文档 + manifest
  4. 超过 50（重复填充）→ 400；空列表 → 400；全不可见 [D4] → 400
  5. 审计：D1-D3 有 download(batch=true)；被剔除 D4 无下载审计
  6. 重名：D6/D7 file_name 相同 → zip 内 arcname 不重复（含 " (2)" 后缀）
  7. admin 兜底：admin 批量 [D1, D4] → 200 + X-Skipped-Count=0（全量可见）
  8. 回归：单文件下载 GET /documents/{id}/download 仍 200
  9. S11 联动：zip 含 manifest.txt 清单（表头 + 每文档一行），条目数 = 文档数 + 1

运行：cd backend && python scripts/test_m10.py
"""
import io
import os
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_ROOT = BACKEND_DIR / "data" / "test_m10"
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
from app.main import app  # noqa: E402

ADMIN_PASSWORD = "Admin@123456"
NEW_ADMIN_PASSWORD = "Admin@123456!"  # A7 硬拦截适配：首登 admin 改密用


def H(token):
    return {"Authorization": f"Bearer {token}"}


def read_zip_names(res):
    """TestClient 响应内容 → zip 内条目列表（完整消费 StreamingResponse）。"""
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        return zf.namelist()


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

    upload_dir = Path(os.environ["UPLOAD_DIR"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    def create_approved(db, title, file_name, text, dept_id, uploaded_by, with_file=True):
        """直接入库 approved 文档；with_file=True 写物理文件，返回 doc.id。"""
        store_name = f"{uuid.uuid4().hex}.txt"
        if with_file:
            (upload_dir / store_name).write_text(text, encoding="utf-8")
        doc = models.Document(
            title=title,
            file_name=file_name,
            file_path=store_name,
            file_type="txt",
            file_size=len(text.encode("utf-8")),
            file_hash=f"m10-{title}",
            status=models.STATUS_APPROVED,
            department_id=dept_id,
            source=models.SOURCE_UPLOAD,
            uploaded_by=uploaded_by,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc.id

    def batch(c, token, ids):
        return c.post("/api/documents/batch-download", headers=H(token),
                      json={"document_ids": ids})

    try:
        with TestClient(app) as c:
            token_admin = login(c, "admin", ADMIN_PASSWORD)

            with SessionLocal() as db:
                dept_tech = db.query(models.Department).filter(
                    models.Department.name == "技术部").first()
                dept_market = db.query(models.Department).filter(
                    models.Department.name == "市场部").first()
                assert dept_tech is not None and dept_market is not None, "默认部门未播种"

            r = c.post("/api/admin/users", headers=H(token_admin),
                       json={"username": "zhangsan", "password": "zs123456",
                             "role": "user", "department_id": dept_tech.id})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            zhangsan_id = r.json()["data"]["id"]
            token_zs = login(c, "zhangsan", "zs123456")

            # ---------- 造数：D1-D3 可见；D4 市场部不可见；D5 物理文件缺失 ----------
            with SessionLocal() as db:
                d1 = create_approved(db, "批量文档A", "文档A.txt", "内容A", None, zhangsan_id)
                d2 = create_approved(db, "批量文档B", "文档B.txt", "内容B", None, zhangsan_id)
                d3 = create_approved(db, "批量文档C", "文档C.txt", "内容C", None, zhangsan_id)
                d4 = create_approved(db, "市场部文档", "市场部.txt", "内容D",
                                     dept_market.id, zhangsan_id)
                d5 = create_approved(db, "文件丢失文档", "丢失.txt", "内容E",
                                     None, zhangsan_id, with_file=False)

            # ---------- 1. 正常批量 ----------
            r = batch(c, token_zs, [d1, d2, d3])
            assert r.status_code == 200, r.text
            assert "application/zip" in r.headers.get("content-type", ""), r.headers
            assert r.headers.get("x-skipped-count") == "0", r.headers
            names = read_zip_names(r)
            assert len(names) == 4, f"zip 应 3 文档 + manifest.txt: {names}"
            assert set(names) == {"文档A.txt", "文档B.txt", "文档C.txt", "manifest.txt"}, names
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                for n in zf.namelist():
                    assert zf.read(n), f"条目 {n} 内容不应为空"
            # S11 联动：manifest 含表头 + 3 数据行，首列 id 对应打包文档
            with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
                mlines = zf.read("manifest.txt").decode("utf-8").strip().splitlines()
            assert mlines[0] == "id\ttitle\tsource", mlines
            assert len(mlines) == 4, mlines
            assert {int(l.split("\t")[0]) for l in mlines[1:]} == {d1, d2, d3}, mlines
            check("1. 正常批量：3 文档 + manifest.txt → 200 / zip 4 条目 / X-Skipped-Count=0")

            # ---------- 2. 混入不可见（市场部 D4） ----------
            r = batch(c, token_zs, [d1, d2, d3, d4])
            assert r.status_code == 200, r.text
            assert r.headers.get("x-skipped-count") == "1", r.headers
            names = read_zip_names(r)
            assert len(names) == 4, f"zip 应 3 文档 + manifest.txt（不含 D4）: {names}"
            assert "市场部.txt" not in names, names
            check("2. 混入不可见：加 D4 → 200 + X-Skipped-Count=1，zip 不含 D4")

            # ---------- 3. 混入文件丢失（D5 无物理文件） ----------
            r = batch(c, token_zs, [d1, d5])
            assert r.status_code == 200, r.text
            assert r.headers.get("x-skipped-count") == "1", r.headers
            names = read_zip_names(r)
            assert set(names) == {"文档A.txt", "manifest.txt"}, names
            check("3. 混入文件丢失：D1+D5 → 200 + X-Skipped-Count=1，zip 仅 D1 + manifest")

            # ---------- 4. 超过 50 / 空列表 / 全不可见 → 400 ----------
            r = batch(c, token_zs, [d1] * 51)
            assert r.status_code == 400, r.text
            assert "50" in r.json()["message"], r.json()
            check("4a. 超过 50（重复填充 51 个）→ 400")

            r = batch(c, token_zs, [])
            assert r.status_code == 400, r.text
            assert "document_ids 不能为空" in r.json()["message"], r.json()
            check("4b. 空列表 → 400")

            r = batch(c, token_zs, [d4])
            assert r.status_code == 400, r.text
            assert "均不可下载" in r.json()["message"], r.json()
            check("4c. 全部不可见（D4）→ 400，不产生空 zip")

            # ---------- 5. 审计：D1-D3 有 batch download；被剔除 D4 无记录 ----------
            with SessionLocal() as db:
                logs = db.query(models.AuditLog).filter(
                    models.AuditLog.action == "download",
                    models.AuditLog.target_type == "document",
                ).all()
                batch_ids = {lg.target_id for lg in logs
                             if lg.detail and lg.detail.get("batch") is True}
                assert {d1, d2, d3} <= batch_ids, f"D1-D3 应有 batch 下载审计: {batch_ids}"
                d4_logs = [lg for lg in logs if lg.target_id == d4]
                assert not d4_logs, f"被剔除的 D4 不应有下载审计: {d4_logs}"
            check("5. 审计：D1-D3 有 download(batch=true)，被剔除 D4 无审计")

            # ---------- 6. 重名：zip 内 arcname 不重复 ----------
            with SessionLocal() as db:
                d6 = create_approved(db, "重名文档一", "重名.txt", "内容六", None, zhangsan_id)
                d7 = create_approved(db, "重名文档二", "重名.txt", "内容七", None, zhangsan_id)
            r = batch(c, token_zs, [d6, d7])
            assert r.status_code == 200, r.text
            names = read_zip_names(r)
            assert len(names) == len(set(names)), f"arcname 不应重复: {names}"
            assert "重名.txt" in names and "重名 (2).txt" in names, names
            check('6. 重名：同 file_name 打包 → arcname 唯一（含 " (2)" 后缀）')

            # ---------- 7. admin 兜底：全量可见 ----------
            r = batch(c, token_admin, [d1, d4])
            assert r.status_code == 200, r.text
            assert r.headers.get("x-skipped-count") == "0", r.headers
            names = read_zip_names(r)
            assert "市场部.txt" in names, names
            check("7. admin 兜底：admin 批量 [D1,D4] → 200 + X-Skipped-Count=0")

            # ---------- 8. 回归：单文件下载不受影响 ----------
            r = c.get(f"/api/documents/{d1}/download", headers=H(token_zs))
            assert r.status_code == 200, r.text
            # 非 ASCII 文件名被 Starlette 编码为 filename*=utf-8''%E6...（RFC 5987），解码后应含原名
            cd = r.headers.get("content-disposition", "")
            assert "attachment" in cd and (
                "文档A.txt" in cd or "filename*=utf-8''" in cd), cd
            check("8. 回归：单文件下载 GET /documents/{id}/download 仍 200")

        print(f"\n=== ALL {len(passed)} M10 TESTS PASSED ===")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
