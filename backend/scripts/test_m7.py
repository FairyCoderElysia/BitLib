# -*- coding: utf-8 -*-
"""M7 测试：文档智能摘要（spec §3 F17 / Sprint 7）。

覆盖验收项（对应 contract-7.md §4）：
  1. 数据准备：admin 直入库文档（LLM 未配置 → 走降级截取）
  2. 降级摘要：直入库后 status == approved、error_message is None、
     Document.summary 非空、长度 ≤ 120、等于 content_text 前 120 字符
  3. 不阻塞入库：LLM 失败下整个入库管线成功（approved），无 failed
  4. 检索接口带摘要：GET /api/search 返回 items[].summary 非空
  5. 详情接口带摘要：GET /api/documents/{id} 返回 summary 非空
  6. LLM 成功/异常 mock：generate_summary 返回清理后 LLM 文本（≤200）；
     LLM 抛异常时回退截取

运行：cd backend && python scripts/test_m7.py
依赖本地 embedding 模型（bge-small-zh-v1.5，M3 已下载）；RERANKER_ENABLED=false 避免下载重排模型。
LLM_BASE_URL 指向不可达端口 → 强制 LLM 失败，验证降级路径（与"未配置 Ollama"等价）。
"""
import os
import shutil
import sys
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_ROOT = BACKEND_DIR / "data" / "test_m7"
TEST_DB = (TEST_ROOT / "app.db").as_posix()

os.environ["ADMIN_INITIAL_PASSWORD"] = "Admin@123456"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
os.environ["CHROMA_DIR"] = str(TEST_ROOT / "chroma")
os.environ["MAX_UPLOAD_MB"] = "5"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["EMBEDDING_MODE"] = "local"
os.environ["RERANKER_ENABLED"] = "false"  # 跳过 cross-encoder，走 RRF + featured 加权
# LLM 强制不可达（端口 1 通常拒绝连接）→ 摘要走降级截取，验证容错
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"
os.environ["LLM_MODE"] = "api"

from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app.main import app  # noqa: E402

ADMIN_PASSWORD = "Admin@123456"


def H(token):
    return {"Authorization": f"Bearer {token}"}


def main():
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    passed: list = []

    def check(name):
        passed.append(name)
        print(f"  PASS: {name}")

    def login(c, username, password):
        r = c.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200 and r.json()["code"] == 0, r.text
        return r.json()["data"]["token"]

    def direct_upload(c, token, path: Path, title: str) -> int:
        """admin 直入库（approved），返回文档 id。"""
        with path.open("rb") as f:
            files = {"file": (path.name, f, "application/octet-stream")}
            r = c.post("/api/admin/documents/upload", headers=H(token),
                       files=files, data={"title": title})
        assert r.status_code == 200 and r.json()["code"] == 0, r.text
        d = r.json()["data"]
        assert d["status"] == "approved", d
        return d["id"]

    try:
        with TestClient(app) as c:
            token_admin = login(c, "admin", ADMIN_PASSWORD)

            # ---------- 1. 数据准备：直入库一篇长文档（>120 字符） ----------
            text = ("企业信息安全管理制度：本制度适用于公司全体员工及外包人员，"
                    "涵盖办公终端、网络访问、账号口令、数据备份与应急响应等方面。"
                    "员工应设置高强度密码并定期更换，不得将账号共享给他人使用；"
                    "涉及敏感数据的传输必须经过加密通道，离职时须及时注销相关系统权限。"
                    "信息安全部门每季度组织一次安全培训与应急演练，"
                    "对违反本制度的行为视情节给予警告、通报批评直至解除劳动合同的处理。")
            assert len(text) > 120, "测试文档应超过 120 字符以便验证截取"
            fp = TEST_ROOT / "sec.txt"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(text, encoding="utf-8")
            doc_id = direct_upload(c, token_admin, fp, "企业信息安全管理制度")
            check("1. admin 直入库文档成功（approved）")

            # ---------- 2. 降级摘要：入库未 failed，summary 非空且为前缀截取 ----------
            r = c.get(f"/api/documents/{doc_id}", headers=H(token_admin))
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            detail = r.json()["data"]
            assert detail["status"] == "approved", detail
            assert detail["error_message"] is None, detail
            summary = detail["summary"]
            assert summary, f"summary 应为非空（降级截取）: {detail}"
            assert len(summary) <= 120, f"降级摘要应 ≤120 字符: {len(summary)}"
            assert summary == (detail["content_text"] or "")[:120], \
                f"降级摘要应等于 content_text 前缀: {summary!r}"
            check("2. LLM 未配置时降级摘要 = content_text 前 120 字符")

            # ---------- 3. 入库未因 LLM 失败而 failed ----------
            r = c.get("/api/admin/documents", params={"status": "failed"},
                      headers=H(token_admin))
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            failed_ids = [it["id"] for it in r.json()["data"]["items"]]
            assert doc_id not in failed_ids, f"文档不应 failed: {failed_ids}"
            check("3. LLM 失败不影响入库（approved，无 failed）")

            # ---------- 4. 检索接口带摘要 ----------
            r = c.get("/api/search", params={"q": "信息安全"}, headers=H(token_admin))
            assert r.status_code == 200 and r.json()["code"] == 0, r.text
            items = r.json()["data"]["items"]
            hit = next((it for it in items if it["id"] == doc_id), None)
            assert hit is not None, f"检索应命中文档: {items}"
            assert "summary" in hit and hit["summary"], f"检索项应带非空 summary: {hit}"
            check("4. 检索接口返回 items[].summary 非空")

            # ---------- 5. 详情接口带摘要 ----------
            assert "summary" in detail and detail["summary"], detail
            check("5. 详情接口返回 summary 非空")

        # ---------- 6. LLM 成功 / 异常 mock（单元级） ----------
        from app.summary import generate_summary, get_display_summary  # noqa: E402

        class FakeDoc:
            def __init__(self, content_text, summary=None):
                self.id = 1
                self.content_text = content_text
                self.summary = summary

        # 6a. LLM 成功：返回清理后的 LLM 文本（≤200，压缩空白）
        with mock.patch("app.llm.chat", return_value="  核心内容：\n\n安全制度要点与应急流程。  "):
            out = generate_summary(None, FakeDoc(text))
        assert out == "核心内容： 安全制度要点与应急流程。", f"LLM 文本应清理空白: {out!r}"
        assert len(out) <= 200, f"LLM 摘要应 ≤200 字符: {len(out)}"
        check("6a. LLM 成功时返回清理后摘要（≤200 字符）")

        # 6b. LLM 抛异常：回退截取，绝不抛错
        with mock.patch("app.llm.chat", side_effect=RuntimeError("AI 服务不可用")):
            out = generate_summary(None, FakeDoc(text))
        assert out == text[:120], f"LLM 异常应回退截取: {out!r}"
        check("6b. LLM 抛异常时回退 content_text 前缀截取")

        # 6c. get_display_summary：有 summary 用 summary，否则截取
        assert get_display_summary(FakeDoc(text, summary="已生成摘要")) == "已生成摘要"
        assert get_display_summary(FakeDoc(text)) == text[:120]
        check("6c. get_display_summary 优先 summary，否则截取")

        print(f"\n=== ALL {len(passed)} M7 TESTS PASSED ===")
    finally:
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
