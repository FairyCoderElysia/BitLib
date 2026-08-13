# -*- coding: utf-8 -*-
"""Sprint 11 测试：后端 7 项评估优化验收（contract-11 §2）。

覆盖验收项：
  S1. FTS 失败态同步：mock embed 抛异常 → 直入库 failed → document_fts 行 status='failed'；
      正常重入库成功 → FTS 行 status='approved' 且内容重写（自愈）
  S2. 死参数：generate_summary(doc) 单参调用返回 str；源码无 "generate_summary(db" 残留
  S3. 422→400：batch-download 非数组/非整数/bool → 统一 400（code=40000，非 422）；
      空列表 → 400；合法 id → 200
  S4. 文档向量缓存：同 (document_id, updated_at) 两次调用底层 get 仅 1 次；
      updated_at 变化 → 重算；结果 L2 范数 ≈ 1
  S5. zip manifest：zip 含 manifest.txt（表头 + 每文档一行），条目数 = 文档数 + 1；
      file_name == "manifest.txt" 的文档被重命名（manifest (2).txt）不冲突
  S6. 断句：降级摘要以句末标点结尾且 ≤120；无标点文本保持 [:120]；get_display_summary 同口径
  S7. 清理统一/幂等：脚本运行后 data/test_m11 目录不存在（finally 清理生效）

运行：cd backend && python scripts/test_m11.py
依赖：TestClient + mock embed（不真实加载本地模型）；RERANKER_ENABLED=false 避免下载重排模型。
LLM_BASE_URL 指向不可达端口 → 强制摘要降级（与"未配置 Ollama"等价）。
"""
import io
import math
import os
import shutil
import sys
import uuid
import zipfile
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_ROOT = BACKEND_DIR / "data" / "test_m11"
TEST_DB = (TEST_ROOT / "app.db").as_posix()

os.environ["ADMIN_INITIAL_PASSWORD"] = "Admin@123456"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
os.environ["CHROMA_DIR"] = str(TEST_ROOT / "chroma")
os.environ["MAX_UPLOAD_MB"] = "5"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["EMBEDDING_MODE"] = "local"
os.environ["RERANKER_ENABLED"] = "false"  # 跳过 cross-encoder，走 RRF + featured 加权
# LLM 强制不可达 → 摘要走降级截取；S1 重入库的向量化由 mock 提供，不加载真实模型
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"
os.environ["LLM_MODE"] = "api"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app import models, vector_store  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.summary import _fallback_cut, generate_summary, get_display_summary  # noqa: E402

ADMIN_PASSWORD = "Admin@123456"
EMBED_DIM = 512  # 与 settings.embedding_dim 一致，mock 向量维度


def H(token):
    return {"Authorization": f"Bearer {token}"}


def read_zip(res):
    """TestClient 响应内容 → (names, manifest_lines)；完整消费 StreamingResponse。"""
    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        names = zf.namelist()
        manifest = zf.read("manifest.txt").decode("utf-8") if "manifest.txt" in names else ""
    return names, manifest.strip().splitlines() if manifest else []


class FakeDoc:
    """generate_summary 单元测试用最小文档桩。"""

    def __init__(self, content_text, summary=None):
        self.id = 1
        self.content_text = content_text
        self.summary = summary


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

    def fts_row(db, doc_id):
        """查 document_fts 行（rowid = document.id）。"""
        return db.execute(text(
            "SELECT status, content_text FROM document_fts WHERE rowid=:id"),
            {"id": doc_id}).fetchone()

    try:
        with TestClient(app) as c:
            token_admin = login(c, "admin", ADMIN_PASSWORD)
            with SessionLocal() as db:
                admin_id = db.query(models.User).filter(
                    models.User.username == "admin").first().id

            # ================= S1. FTS 失败态同步 + 自愈 =================
            fail_text = ("信息安全管理制度：员工账号口令须定期更换，"
                         "敏感数据传输必须加密，离职账号及时注销。"
                         "本制度自发布之日起施行。" * 5)  # 足够长，避免清洗过短拦截
            fp = TEST_ROOT / "fts_fail.txt"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(fail_text, encoding="utf-8")

            # 1a. mock embed 抛异常 → 直入库 → failed + FTS status=failed
            with mock.patch("app.ingest.embed",
                            side_effect=RuntimeError("embed 服务不可用")):
                with fp.open("rb") as f:
                    r = c.post("/api/admin/documents/upload", headers=H(token_admin),
                               files={"file": ("fts_fail.txt", f, "application/octet-stream")},
                               data={"title": "FTS失败态文档"})
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            d = r.json()["data"]
            assert d["status"] == "failed", f"embed 失败应置 failed: {d}"
            doc_id = d["id"]
            with SessionLocal() as db:
                row = fts_row(db, doc_id)
            assert row is not None, "failed 文档应有 FTS 行"
            assert row[0] == "failed", f"FTS 行 status 应同步为 failed: {row}"
            check("S1a. embed 失败 → 文档 failed，FTS 行 status='failed'（冗余列同步）")

            # 1b. 正常重入库 → approved + FTS 行重写为 approved 且内容非空（自愈）
            with mock.patch("app.ingest.embed",
                            side_effect=lambda texts: [[0.01] * EMBED_DIM for _ in texts]):
                r = c.post(f"/api/admin/documents/{doc_id}/reprocess",
                           headers=H(token_admin))
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            assert r.json()["data"]["status"] == "approved", r.text
            with SessionLocal() as db:
                row = fts_row(db, doc_id)
            assert row is not None and row[0] == "approved", \
                f"重入库后 FTS 行应重写为 approved: {row}"
            # FTS content_text 为 jieba 分词（空格分隔），断言单词在分词结果中即可证明内容已重写
            assert row[1] and "信息" in row[1] and "安全" in row[1], \
                f"FTS 行 content_text 应包含新内容（自愈）: {row}"
            check("S1b. 正常重入库 → approved，FTS 行 status='approved' 且内容重写（自愈）")

            # ================= S2. generate_summary 死参数 =================
            with mock.patch("app.llm.chat", side_effect=RuntimeError("AI 不可用")):
                out = generate_summary(FakeDoc(fail_text))
            assert isinstance(out, str) and out, f"单参调用应返回 str: {out!r}"
            for p in (BACKEND_DIR / "app" / "summary.py",
                      BACKEND_DIR / "app" / "ingest.py",
                      BACKEND_DIR / "app" / "routers" / "admin.py"):
                src = p.read_text(encoding="utf-8")
                assert "generate_summary(db" not in src, f"{p.name} 仍有 db 参数残留"
            check("S2. generate_summary(doc) 单参调用正常；源码无 generate_summary(db 残留")

            # ================= S3. batch-download 422 → 400 =================
            def batch(body):
                return c.post("/api/documents/batch-download", headers=H(token_admin),
                              json={"document_ids": body})

            for bad_body in ("abc", [1, "x"], [True]):
                r = batch(bad_body)
                assert r.status_code == 400, f"{bad_body!r} 应 400: {r.status_code} {r.text}"
                body = r.json()
                assert body["code"] == 40000 and body["message"], body
            check('S3a. document_ids 非数组/含非整数/bool → 统一 400（code=40000，非 422）')

            r = batch([])
            assert r.status_code == 400 and "不能为空" in r.json()["message"], r.text
            check("S3b. 空列表 → 400（既有行为不变）")

            r = batch([doc_id])
            assert r.status_code == 200 and "application/zip" in \
                r.headers.get("content-type", ""), r.text
            check("S3c. 合法 id → 200（正常路径回归）")

            # ================= S4. 文档向量缓存 =================
            class StubCol:
                """记录 get 调用次数的 Chroma 集合桩。"""

                def __init__(self):
                    self.get_calls = 0

                def get(self, where=None, include=None):
                    self.get_calls += 1
                    return {"embeddings": [[1.0] * EMBED_DIM, [0.5] * EMBED_DIM]}

            stub = StubCol()
            try:
                with mock.patch("app.vector_store._get_collection", return_value=stub):
                    v1 = vector_store.get_document_vector(999999, "2026-01-01 00:00:00")
                    v2 = vector_store.get_document_vector(999999, "2026-01-01 00:00:00")
                    assert v1 == v2, "同 key 两次应返回相同向量"
                    assert stub.get_calls == 1, f"命中缓存，底层 get 应仅 1 次: {stub.get_calls}"
                    v3 = vector_store.get_document_vector(999999, "2026-01-01 00:00:01")
                    assert stub.get_calls == 2, "updated_at 变化应重算"
                    assert v3 is not None, "updated_at 变化后应返回向量"
                    norm3 = math.sqrt(sum(x * x for x in v3))
                    assert abs(norm3 - 1.0) < 1e-6, f"重算结果应保持 L2 归一化: {norm3}"
                    norm = math.sqrt(sum(x * x for x in v1))
                    assert abs(norm - 1.0) < 1e-6, f"L2 归一化后范数应≈1: {norm}"
            finally:
                vector_store._vec_cache.clear()  # 清理测试缓存，防污染后续
            check("S4. 缓存：同 (document_id, updated_at) 两次 → 底层 get 仅 1 次；"
                  "updated_at 变化重算；结果 L2 范数≈1")

            # ================= S5. zip manifest =================
            upload_dir = Path(os.environ["UPLOAD_DIR"])
            upload_dir.mkdir(parents=True, exist_ok=True)

            def create_approved(db, title, file_name, text, with_file=True):
                store_name = f"{uuid.uuid4().hex}.txt"
                if with_file:
                    (upload_dir / store_name).write_text(text, encoding="utf-8")
                doc = models.Document(
                    title=title,
                    file_name=file_name,
                    file_path=store_name,
                    file_type="txt",
                    file_size=len(text.encode("utf-8")),
                    file_hash=f"m11-{title}",
                    status=models.STATUS_APPROVED,
                    department_id=None,
                    source=models.SOURCE_UPLOAD,
                    uploaded_by=admin_id,
                )
                db.add(doc)
                db.commit()
                db.refresh(doc)
                return doc.id

            with SessionLocal() as db:
                d2 = create_approved(db, "批量文档B", "批量B.txt", "内容B")
                d3 = create_approved(db, "批量文档C", "批量C.txt", "内容C")

            r = batch([doc_id, d2, d3])
            assert r.status_code == 200, r.text
            names, mlines = read_zip(r)
            assert len(names) == 4, f"zip 应 3 文档 + manifest.txt: {names}"
            assert "manifest.txt" in names, names
            assert mlines[0] == "id\ttitle\tsource", mlines
            assert len(mlines) == 4, f"manifest 应表头 + 3 行: {mlines}"
            row_map = {int(l.split("\t")[0]): l.split("\t") for l in mlines[1:]}
            assert set(row_map) == {doc_id, d2, d3}, row_map
            assert row_map[doc_id][1] == "FTS失败态文档", row_map[doc_id]
            assert row_map[doc_id][2] == "upload", row_map[doc_id]
            check("S5a. zip 含 manifest.txt：表头 + 每文档一行（id/title/source）")

            # 重名防护：file_name == "manifest.txt" 的文档被重命名，清单唯一
            with SessionLocal() as db:
                d4 = create_approved(db, "清单文档", "manifest.txt", "内容D")
            r = batch([d4])
            assert r.status_code == 200, r.text
            names2, _ = read_zip(r)
            assert names2.count("manifest.txt") == 1, f"manifest.txt 应唯一（清单）: {names2}"
            assert "manifest (2).txt" in names2, f"用户 manifest.txt 文档应重命名: {names2}"
            check("S5b. 用户文件恰名 manifest.txt → 重命名为 manifest (2).txt，清单唯一")

            # ================= S6. 降级摘要断句 =================
            text_punct = "第一句以句号结束。" + ("无标点填充文本" * 30)  # 长度 > 120
            assert len(text_punct) > 120
            with mock.patch("app.llm.chat", side_effect=RuntimeError("AI 不可用")):
                s1 = generate_summary(FakeDoc(text_punct))
            assert s1.endswith(("。", "！", "？")) and len(s1) <= 120, f"应断句结尾: {s1!r}"
            assert s1 in text_punct, f"摘要应为原文片段: {s1!r}"
            check("S6a. 降级摘要回溯到句末标点结尾，且 ≤120")

            text_nopunct = "无" * 200
            with mock.patch("app.llm.chat", side_effect=RuntimeError("AI 不可用")):
                s2 = generate_summary(FakeDoc(text_nopunct))
            assert s2 == text_nopunct[:120], f"无标点文本应保持 [:120] 截断: {s2!r}"
            assert get_display_summary(FakeDoc(text_punct)) == _fallback_cut(text_punct)
            assert get_display_summary(FakeDoc(text_nopunct)) == _fallback_cut(text_nopunct)
            check("S6b. 无标点保持 [:120]；get_display_summary 同断句口径")

        # ================= S7. 清理统一/幂等（脚本级） =================
        print(f"\n=== ALL {len(passed)} M11 TESTS PASSED ===")
    finally:
        # Windows 句柄问题：SQLite/Chroma 文件句柄未释放会导致 rmtree 残留
        # （这也是历史 test_m3~m10 目录残留的根因）。先释放引擎/Chroma/GC 再删。
        # 注意：用别名 import，避免污染 main() 内已使用的 vector_store 局部名。
        try:
            import app.vector_store as vs_mod
            if vs_mod._client is not None:
                vs_mod._client.close()
            vs_mod._client = None
            vs_mod._collection = None
        except Exception:
            pass
        try:
            from app.db import engine as _eng
            _eng.dispose()
        except Exception:
            pass
        import gc
        gc.collect()
        for _ in range(3):
            shutil.rmtree(TEST_ROOT, ignore_errors=True)
            if not TEST_ROOT.exists():
                break


if __name__ == "__main__":
    main()
    assert not TEST_ROOT.exists(), "S7: finally 清理后 data/test_m11 目录应不存在"
    print("S7 PASS: setup rmtree+mkdir 幂等 / finally 清理生效（目录已删除）")
