# -*- coding: utf-8 -*-
"""S5 问答回归测试（TestClient + mock，确定性覆盖 Q1-Q15 中可自动化断言）。

运行：cd backend && HF_HUB_OFFLINE=1 python scripts/test_s5_qa.py

测试库独立（backend/data/test_s5_qa/），运行结束清理；不加载本地 embedding/reranker，
不落真实 Chroma；QA 路径通过 mock llm.chat 与 hybrid_search 获得确定性。
"""
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from unittest import mock

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

TEST_ROOT = BACKEND_DIR / "data" / "test_s5_qa"
TEST_DB = (TEST_ROOT / "app.db").as_posix()

# 必须在导入 app 之前设置环境变量（pydantic-settings：环境变量优先于 .env）
os.environ["ADMIN_INITIAL_PASSWORD"] = "Admin@123456"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB}"
os.environ["UPLOAD_DIR"] = str(TEST_ROOT / "uploads")
os.environ["CHROMA_DIR"] = str(TEST_ROOT / "chroma")
os.environ["MAX_UPLOAD_MB"] = "5"
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ["EMBEDDING_MODE"] = "local"
os.environ["RERANKER_ENABLED"] = "false"
os.environ["LLM_MODE"] = "api"
os.environ["LLM_BASE_URL"] = "http://127.0.0.1:1/v1"

from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402
from app import search_service  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.routers import qa as qa_mod  # noqa: E402
from app.security import hash_password  # noqa: E402

USER_PASSWORD = "User@123456"


def H(token):
    return {"Authorization": f"Bearer {token}"}


def login(c, username, password=USER_PASSWORD):
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200 and r.json()["code"] == 0, r.text
    return r.json()["data"]["token"]


def make_user(username, department_id, role="user"):
    """直接在测试库创建已改密状态的用户，避免 A7 首登改密流程干扰。"""
    with SessionLocal() as db:
        user = models.User(
            username=username,
            password_hash=hash_password(USER_PASSWORD),
            role=role,
            department_id=department_id,
            must_change_password=False,
            created_at=datetime.utcnow(),
        )
        db.add(user)
        db.commit()
        return user.id


def make_doc(title, parent_texts, department_id=None, status="approved"):
    """直接创建 approved 文档及其 ChunkParent（不经过解析入库，不触 Chroma）。"""
    now = datetime.utcnow()
    with SessionLocal() as db:
        doc = models.Document(
            title=title,
            file_name=f"{title}.txt",
            file_path="",
            file_type="txt",
            file_size=0,
            file_hash=f"sha256:{title}",
            content_text="\n".join(parent_texts),
            status=status,
            department_id=department_id,
            source=models.SOURCE_UPLOAD,
            is_featured=False,
            created_at=now,
            updated_at=now,
        )
        db.add(doc)
        db.flush()
        for idx, text in enumerate(parent_texts):
            db.add(models.ChunkParent(
                document_id=doc.id,
                chunk_index=idx,
                title=title,
                text=text,
            ))
        db.commit()
        return doc.id, doc


def get_parents(doc_id):
    """按 chunk_index 升序返回该文档全部 ChunkParent。"""
    with SessionLocal() as db:
        return (db.query(models.ChunkParent)
                .filter(models.ChunkParent.document_id == doc_id)
                .order_by(models.ChunkParent.chunk_index).all())


def main():
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    passed = []

    def check(name):
        passed.append(name)
        print(f"  PASS: {name}")

    q14_sid = None
    q14_expected = None

    try:
        # 第一段 TestClient：跑主要 QA 测试；patch 启动预热与向量健康检查，避免加载模型
        with mock.patch("app.main._prewarm"), \
                mock.patch("app.main._ensure_vector_health"):
            with TestClient(app) as c:
                # ---------- 准备：部门 / 用户 ----------
                with SessionLocal() as db:
                    dept_tech = db.query(models.Department).filter(
                        models.Department.name == "技术部").first()
                    dept_prod = db.query(models.Department).filter(
                        models.Department.name == "产品部").first()
                    assert dept_tech is not None and dept_prod is not None
                u_tech_id = make_user("s5_tech", dept_tech.id)
                u_prod_id = make_user("s5_prod", dept_prod.id)
                token_tech = login(c, "s5_tech")
                token_prod = login(c, "s5_prod")
                check("准备. 测试库独立，部门/用户就绪")

                # ---------- Q1 ----------
                # Q1a: search_service.hybrid_search 结果项新增 parent_ids（按语义命中顺序去重）
                q1_doc_id, q1_doc = make_doc("Q1文档", ["Q1 文档正文内容，用于验证 parent_ids 透传。"])
                q1_sem = [
                    {"document_id": q1_doc_id, "parent_id": 3, "chunk_index": 0,
                     "text": "命中块", "distance": 0.20},
                    {"document_id": q1_doc_id, "parent_id": 5, "chunk_index": 1,
                     "text": "命中块", "distance": 0.25},
                    {"document_id": q1_doc_id, "parent_id": 3, "chunk_index": 0,
                     "text": "命中块", "distance": 0.30},
                ]
                with SessionLocal() as db:
                    q1_user = db.get(models.User, u_tech_id)
                with mock.patch.object(search_service, "semantic_recall",
                                       return_value=q1_sem), \
                        mock.patch.object(search_service, "keyword_recall",
                                          return_value=[q1_doc_id]):
                    with SessionLocal() as db:
                        q1_user2 = db.get(models.User, u_tech_id)
                        q1_hits = search_service.hybrid_search(db, q1_user2, "查询",
                                                               top_k=30, limit=8)
                assert q1_hits, "hybrid_search 应返回结果"
                assert q1_hits[0]["parent_ids"] == [3, 5], q1_hits[0]
                assert "parent_ids" in q1_hits[0]
                # Q1b: /api/search 响应结构不受影响（多余键不进入 items）
                with mock.patch("app.routers.search.hybrid_search",
                                return_value=[{"document": q1_doc, "snippet": "片段",
                                               "score": 0.91, "matched": None,
                                               "parent_ids": [3, 5]}]):
                    r = c.get("/api/search", params={"q": "查询"}, headers=H(token_tech))
                assert r.status_code == 200, r.text
                q1_item = r.json()["data"]["items"][0]
                assert q1_item["id"] == q1_doc_id
                assert "parent_ids" not in q1_item, q1_item
                assert q1_item["snippet"] == "片段" and q1_item["score"] == 0.91, q1_item
                check("Q1. hybrid_search 新增 parent_ids（语义顺序去重），/api/search 响应结构不变")

                # ---------- Q2 + Q4 + Q6 ----------
                q2_texts = [
                    "这是文档开头无关内容，介绍企业资料管理系统的历史背景与适用范围，不包含模型训练优化器的答案。",
                    "第一章导读：说明本手册的结构和阅读对象，不包含任何具体技术知识点。",
                    "第二章部署准备：服务器需要至少 4 核 CPU 和 8GB 内存，不包含模型训练优化器。",
                    "第三章常见问题：VPN 接入密码重置流程说明，不包含模型训练优化器。",
                    "目标知识点章节：深度学习模型训练需要大量标注数据，使用 Adam 优化器可加快收敛。",
                ]
                q2_doc_id, q2_doc = make_doc("Q2长文档", q2_texts)
                q2_parents = get_parents(q2_doc_id)
                q2_p4 = q2_parents[4]
                q2_p3 = q2_parents[3]
                q2_captured = []

                def q2_fake_chat(messages, temperature=0.3):
                    q2_captured.extend(messages)
                    return "使用 Adam 优化器可加快收敛。"

                with mock.patch.object(qa_mod.llm_mod, "chat",
                                       side_effect=q2_fake_chat), \
                        mock.patch.object(qa_mod, "hybrid_search",
                                          return_value=[{"document": q2_doc,
                                                         "score": 0.91,
                                                         "snippet": "后部命中",
                                                         "matched": None,
                                                         "parent_ids": [4, 3]}]) as q2_hs:
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "模型训练应该使用什么优化器？"})
                assert r.status_code == 200, r.text
                q2_data = r.json()["data"]
                assert q2_hs.call_args.kwargs.get("top_k") == 30, q2_hs.call_args
                assert q2_hs.call_args.kwargs.get("limit") == 8, q2_hs.call_args
                assert "未在资料库中找到相关内容" not in q2_data["answer"], q2_data
                assert q2_data["answer"] == "使用 Adam 优化器可加快收敛。", q2_data
                assert q2_data["confidence"] == 0.91, q2_data
                q2_cits = q2_data["citations"]
                assert len(q2_cits) == 2, q2_cits
                assert q2_cits[0]["chunk_index"] == 4, q2_cits
                assert q2_cits[0]["parent_id"] == q2_p4.id, q2_cits
                assert q2_cits[0]["snippet"] == q2_p4.text[:150], q2_cits
                assert q2_cits[1]["chunk_index"] == 3, q2_cits
                assert q2_cits[1]["parent_id"] == q2_p3.id, q2_cits
                # 最终 user 消息资料块包含后部 parent，不包含开头前 2 个 parent 的无关文本
                q2_final = [m for m in q2_captured if m["role"] == "user"][-1]["content"]
                assert "资料：" in q2_final and "问题：" in q2_final, q2_final
                assert "Adam 优化器" in q2_final, q2_final
                assert "历史背景与适用范围" not in q2_final, q2_final
                assert "第一章导读" not in q2_final, q2_final
                check("Q2/Q4/Q6. parent_ids 优先回溯后部 parent，citations 一一对应，QA top_k=30/limit=8")

                # ---------- Q3 ----------
                q3_texts = [
                    "公司年度旅游计划安排在秋季，包括行程安排和预算说明。",
                    "每周五下午召开部门例会，会议纪要将在会后统一发布。",
                    "该系统支持 Windows 和 Linux 操作系统，用户可根据需要选择版本。",
                    "所有员工应按时提交月度工作报告，逾期将影响绩效评估。",
                ]
                q3_doc_id, q3_doc = make_doc("Q3操作系统文档", q3_texts)
                q3_parents = get_parents(q3_doc_id)
                q3_captured = []

                def q3_fake_chat(messages, temperature=0.3):
                    q3_captured.extend(messages)
                    return "该系统支持 Windows 和 Linux。"

                with mock.patch.object(qa_mod.llm_mod, "chat",
                                       side_effect=q3_fake_chat), \
                        mock.patch.object(qa_mod, "hybrid_search",
                                          return_value=[{"document": q3_doc,
                                                         "score": 0.77,
                                                         "snippet": "keyword命中",
                                                         "matched": None,
                                                         "parent_ids": []}]):
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "支持哪些操作系统？"})
                assert r.status_code == 200, r.text
                q3_data = r.json()["data"]
                q3_cits = q3_data["citations"]
                assert len(q3_cits) == 1, q3_cits
                assert q3_cits[0]["chunk_index"] == 2, q3_cits
                assert q3_cits[0]["parent_id"] == q3_parents[2].id, q3_cits
                q3_final = [m for m in q3_captured if m["role"] == "user"][-1]["content"]
                assert "支持 Windows 和 Linux 操作系统" in q3_final, q3_final
                assert "年度旅游计划" not in q3_final, q3_final
                check("Q3. keyword-only 命中按查询词匹配选最相关 parent，不取文档开头")

                # ---------- Q4 补充：全局 8 块 / 每文档 2 块，citations 与上下文一一对应 ----------
                q4_docs = []
                for i in range(5):
                    q4_did, q4_doc = make_doc(f"Q4文档{i}", [f"Q4文档{i}第0块内容", f"Q4文档{i}第1块内容"])
                    q4_docs.append(q4_doc)
                with SessionLocal() as db:
                    q4_hits = [{"document": d, "parent_ids": [0, 1]} for d in q4_docs]
                    q4_contexts, q4_citations = qa_mod._build_context_and_citations(
                        db, q4_hits, "测试问题")
                assert len(q4_contexts) == 8, len(q4_contexts)
                assert len(q4_citations) == 8, len(q4_citations)
                for _i, _ci in enumerate(q4_citations):
                    assert _ci["parent_id"] > 0 and _ci["chunk_index"] in (0, 1), _ci
                assert q4_citations[0]["snippet"] == "Q4文档0第0块内容"[:150], q4_citations[0]
                check("Q4. 上下文全局上限 8 块/每文档 2 块，citations 与上下文一一对应")

                # ---------- Q5 ----------
                q5_texts = [
                    "该文档的唯一有效 parent 块，但不在 semantic parent_ids 中。",
                    "另一个有效 parent 块，也不在 semantic parent_ids 中。",
                ]
                q5_doc_id, q5_doc = make_doc("Q5无效parent文档", q5_texts)
                with mock.patch.object(qa_mod.llm_mod, "chat") as q5_chat, \
                        mock.patch.object(qa_mod, "hybrid_search",
                                          return_value=[
                                              {"document": q5_doc, "score": 0.61,
                                               "snippet": "无效", "matched": None,
                                               "parent_ids": [99, 98]},
                                              {"document": q5_doc, "score": 0.60,
                                               "snippet": "无匹配", "matched": None,
                                               "parent_ids": []}]):
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "支持哪些操作系统？"})
                assert r.status_code == 200, r.text
                q5_data = r.json()["data"]
                assert q5_data["answer"] == "未在资料库中找到相关内容", q5_data
                assert q5_data["citations"] == [], q5_data
                assert q5_chat.call_count == 0, "无有效上下文时不得调用 LLM"
                check("Q5. 语义 parent 全无效且 keyword 无匹配时返回未找到且 citations 空")

                # ---------- Q7 ----------
                q7_texts = [
                    "这是用于历史窗口测试的资料文档，内容包含机器学习算法在企业分析中的应用说明。",
                    "该资料文档的第二段，补充说明模型评估方法与特征工程实践。",
                ]
                q7_doc_id, q7_doc = make_doc("Q7历史资料文档", q7_texts)
                q7_long_content = "长" * 650
                with SessionLocal() as db:
                    q7_session = models.QASession(user_id=u_tech_id, title="历史测试")
                    db.add(q7_session)
                    db.flush()
                    q7_rounds = [
                        ("历史用户1", "历史助手1"),
                        ("历史用户2", q7_long_content),
                        ("历史用户3", "历史助手3"),
                    ]
                    for idx, (u, a) in enumerate(q7_rounds):
                        db.add(models.QAMessage(session_id=q7_session.id,
                                                role="user", content=u))
                        citations = [{"document_id": 999, "title": "不应出现在历史内容中",
                                      "snippet": "x", "parent_id": 1,
                                      "chunk_index": 0}] if idx == 1 else None
                        db.add(models.QAMessage(session_id=q7_session.id,
                                                role="assistant", content=a,
                                                citations=citations))
                    db.commit()
                    q7_sid = q7_session.id
                q7_captured = []

                def q7_fake_chat(messages, temperature=0.3):
                    q7_captured.extend(messages)
                    return "根据资料，机器学习算法可用于企业分析。"

                with mock.patch.object(qa_mod.llm_mod, "chat",
                                       side_effect=q7_fake_chat), \
                        mock.patch.object(qa_mod, "hybrid_search",
                                          return_value=[{"document": q7_doc,
                                                         "score": 0.83,
                                                         "snippet": "命中",
                                                         "matched": None,
                                                         "parent_ids": [0]}]):
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "第四轮问题？", "session_id": q7_sid})
                assert r.status_code == 200, r.text
                q7_roles = [m["role"] for m in q7_captured if m["role"] != "system"]
                assert q7_roles == ["user", "assistant", "user", "assistant",
                                    "user", "assistant", "user"], q7_roles
                q7_history = q7_captured[1:7]
                assert q7_history[0]["content"] == "历史用户1", q7_history
                assert q7_history[1]["content"] == "历史助手1", q7_history
                assert q7_history[2]["content"] == "历史用户2", q7_history
                assert q7_history[3]["content"] == q7_long_content[:600], "历史应截断到 600 字符"
                assert q7_history[4]["content"] == "历史用户3", q7_history
                assert q7_history[5]["content"] == "历史助手3", q7_history
                assert "不应出现在历史内容中" not in q7_history[3]["content"]
                q7_final = q7_captured[-1]["content"]
                assert "资料：" in q7_final and "问题：第四轮问题？" in q7_final, q7_final
                check("Q7. LLM messages 携带最近历史（id 升序/截断 600/citations 不嵌入）")


                # ---------- Q8 ----------
                # 读取模块级常量：封闭集合内每个寒暄整句均应判定为 True，且不做子串匹配
                for _g in qa_mod.GREETING_PHRASES:
                    assert qa_mod._is_greeting(_g) is True, _g
                assert qa_mod._is_greeting("你好，请问 VPN 怎么接入") is False

                q8_captured = []

                def q8_fake_chat(messages, temperature=0.3):
                    q8_captured.append(messages)
                    return "你好，有什么可以帮助您的？"

                with mock.patch.object(qa_mod.llm_mod, "chat",
                                       side_effect=q8_fake_chat), \
                        mock.patch.object(qa_mod, "hybrid_search") as q8_hs:
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "你好"})
                assert r.status_code == 200, r.text
                q8_data = r.json()["data"]
                assert q8_hs.call_count == 0, "寒暄不应走检索"
                assert q8_captured and q8_captured[0][0]["content"] == qa_mod.GREETING_SYSTEM_PROMPT
                assert not any("资料：" in m["content"] for m in q8_captured[0])
                assert q8_data["answer"] == "你好，有什么可以帮助您的？", q8_data
                assert q8_data["citations"] == [], q8_data
                assert q8_data["confidence"] == 0.0, q8_data
                q8_sid = q8_data["session_id"]
                assert q8_sid, "寒暄消息应正常落库"
                r = c.get(f"/api/qa/sessions/{q8_sid}/messages", headers=H(token_tech))
                assert r.status_code == 200, r.text
                q8_msgs = r.json()["data"]["messages"]
                assert len(q8_msgs) == 2 and q8_msgs[0]["role"] == "user", q8_msgs
                assert q8_msgs[1]["role"] == "assistant" and q8_msgs[1]["citations"] == [], q8_msgs

                # 寒暄回答超长截断到 100 字符内
                with mock.patch.object(qa_mod.llm_mod, "chat",
                                       return_value="感" * 120), \
                        mock.patch.object(qa_mod, "hybrid_search") as q8_hs2:
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "谢谢"})
                assert r.status_code == 200, r.text
                assert q8_hs2.call_count == 0
                assert len(r.json()["data"]["answer"]) == 100, r.json()["data"]
                # 带寒暄前缀的知识问题不按寒暄处理（进入检索路径）
                with mock.patch.object(qa_mod.llm_mod, "chat") as q8_chat3, \
                        mock.patch.object(qa_mod, "hybrid_search",
                                          return_value=[]) as q8_hs3:
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "你好，请问 VPN 怎么接入"})
                assert q8_hs3.call_count == 1, "带知识内容的句子应进入检索路径"
                assert q8_chat3.call_count == 0, "无命中且无历史 citations 时不应调用 LLM"
                assert "未在资料库中找到相关内容" in r.json()["data"]["answer"], r.text
                check("Q8. 纯寒暄独立 prompt 不检索/不带资料，回复截断 100 字，消息落库")

                # ---------- Q9 ----------
                with mock.patch.object(qa_mod.llm_mod, "chat") as q9_chat, \
                        mock.patch.object(qa_mod, "hybrid_search",
                                          return_value=[]) as q9_hs:
                    for q9_q in ["绝对不存在的知识问题xyzabc", "你是谁", "你能做什么"]:
                        r = c.post("/api/qa/ask", headers=H(token_tech),
                                   json={"question": q9_q})
                        assert r.status_code == 200, r.text
                        q9_data = r.json()["data"]
                        assert "未在资料库中找到相关内容" in q9_data["answer"], q9_data
                        assert q9_data["citations"] == [], q9_data
                assert q9_hs.call_count == 3, q9_hs.call_count
                assert q9_chat.call_count == 0, "无命中且无历史 citations 时不得调用 LLM"
                check("Q9. 知识性问题无命中（含你是谁/你能做什么）返回未找到且不调用 LLM")

                # ---------- Q10 ----------
                q10_texts = [
                    "该系统的安装步骤：第一步下载安装包，第二步运行安装程序，第三步完成初始配置。",
                    "该系统支持 Windows 和 Linux 操作系统，用户可根据需要选择版本。",
                ]
                q10_doc_id, q10_doc = make_doc("Q10系统文档", q10_texts)
                with mock.patch.object(qa_mod.llm_mod, "chat",
                                       return_value="根据资料，该系统安装步骤为下载安装包并运行安装程序。"), \
                        mock.patch.object(qa_mod, "hybrid_search",
                                          return_value=[{"document": q10_doc,
                                                         "score": 0.88,
                                                         "snippet": "安装命中",
                                                         "matched": None,
                                                         "parent_ids": [0]}]):
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "该系统如何安装？"})
                assert r.status_code == 200, r.text
                q10_sid = r.json()["data"]["session_id"]
                assert q10_sid, r.text
                assert r.json()["data"]["citations"], r.text
                q10_captured = []

                def q10_fake_chat(messages, temperature=0.3):
                    q10_captured.extend(messages)
                    return "根据资料，该系统支持 Windows 和 Linux。"

                with mock.patch.object(qa_mod.llm_mod, "chat",
                                       side_effect=q10_fake_chat), \
                        mock.patch.object(qa_mod, "hybrid_search",
                                          return_value=[]) as q10_hs:
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "那它支持哪些操作系统？",
                                     "session_id": q10_sid})
                assert r.status_code == 200, r.text
                q10_data = r.json()["data"]
                assert q10_data["session_id"] == q10_sid, q10_data
                assert "Windows" in q10_data["answer"] and "Linux" in q10_data["answer"], q10_data
                assert q10_data["confidence"] == 0.0, q10_data
                assert q10_data["citations"], "复用路径应产生 citations"
                assert q10_data["citations"][0]["document_id"] == q10_doc_id, q10_data
                q10_final = [m for m in q10_captured if m["role"] == "user"][-1]["content"]
                assert "支持 Windows 和 Linux 操作系统" in q10_final, q10_final
                check("Q10. 无命中时复用上一轮 citations，按当前问题选 parent，confidence=0.0")

                # ---------- Q11 ----------
                q11_texts = ["用于 21 条历史消息测试的资料文档，包含机器学习算法与预测建模内容。"]
                q11_doc_id, q11_doc = make_doc("Q11历史压力文档", q11_texts)
                with SessionLocal() as db:
                    q11_session = models.QASession(user_id=u_tech_id, title="压力测试")
                    db.add(q11_session)
                    db.flush()
                    for i in range(21):
                        role = "user" if i % 2 == 0 else "assistant"
                        db.add(models.QAMessage(session_id=q11_session.id,
                                                role=role, content=f"历史消息{i}"))
                    db.commit()
                    q11_sid = q11_session.id
                q11_captured = []

                def q11_fake_chat(messages, temperature=0.3):
                    q11_captured.extend(messages)
                    return "根据资料回答。"

                with mock.patch.object(qa_mod.llm_mod, "chat",
                                       side_effect=q11_fake_chat), \
                        mock.patch.object(qa_mod, "hybrid_search",
                                          return_value=[{"document": q11_doc,
                                                         "score": 0.69,
                                                         "snippet": "命中",
                                                         "matched": None,
                                                         "parent_ids": [0]}]):
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "继续提问？", "session_id": q11_sid})
                assert r.status_code == 200, r.text
                q11_data = r.json()["data"]
                assert q11_data["session_id"] == q11_sid, q11_data
                q11_hist = [m for m in q11_captured
                            if m["role"] != "system" and "资料：" not in m["content"]]
                q11_hist_count = len(q11_hist)
                assert q11_hist_count == 10, f"历史条数应恰好为 10，实际 {q11_hist_count}"
                assert q11_hist[0]["content"] == "历史消息11", q11_hist
                with SessionLocal() as db:
                    q11_count = db.query(models.QAMessage).filter(
                        models.QAMessage.session_id == q11_sid).count()
                assert q11_count == 23, q11_count
                check("Q11. 21 条历史时仅取最近 10 条，新消息正常追加")

                # ---------- Q12 ----------
                r = c.post("/api/qa/ask", headers=H(token_tech),
                           json={"question": "   "})
                assert r.status_code == 400 and r.json()["code"] == 40000, r.text
                # session 不存在：校验提前到检索之前
                with mock.patch.object(qa_mod, "hybrid_search") as q12_hs:
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "问题", "session_id": 999999})
                assert r.status_code == 400 and r.json()["code"] == 40000, r.text
                assert "会话不存在或无权访问" in r.json()["message"], r.text
                assert q12_hs.call_count == 0, "session 校验应在检索之前"
                # session 属于他人
                with mock.patch.object(qa_mod, "hybrid_search") as q12_hs2:
                    r = c.post("/api/qa/ask", headers=H(token_prod),
                               json={"question": "问题", "session_id": q7_sid})
                assert r.status_code == 400 and r.json()["code"] == 40000, r.text
                assert "会话不存在或无权访问" in r.json()["message"], r.text
                assert q12_hs2.call_count == 0
                # LLM 失败 -> 503 + code=50300
                q12_texts = ["LLM 失败测试文档内容，包含企业资料管理系统使用指南。"]
                q12_doc_id, q12_doc = make_doc("Q12LLM失败文档", q12_texts)
                with mock.patch.object(qa_mod.llm_mod, "chat",
                                       side_effect=RuntimeError("AI 服务不可用（测试注入）")), \
                        mock.patch.object(qa_mod, "hybrid_search",
                                          return_value=[{"document": q12_doc,
                                                         "score": 0.8,
                                                         "snippet": "命中",
                                                         "matched": None,
                                                         "parent_ids": [0]}]):
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "企业资料管理系统怎么使用？"})
                assert r.status_code == 503, r.text
                assert r.json()["code"] == 50300, r.text
                check("Q12. question 空 400 / session 不存在或越权 400 / LLM 失败 503+50300")

                # ---------- Q15 ----------
                q15_texts = ["机器学习算法用于数据建模与预测分析，支持特征工程与模型评估。"]
                q15_doc_id, q15_doc = make_doc("Q15机器学习文档", q15_texts)
                q15_parents = get_parents(q15_doc_id)
                with SessionLocal() as db:
                    q15_session = models.QASession(user_id=u_tech_id, title="Q15串扰测试")
                    db.add(q15_session)
                    db.flush()
                    db.add(models.QAMessage(session_id=q15_session.id, role="user",
                                            content="机器学习如何用于预测？"))
                    db.add(models.QAMessage(session_id=q15_session.id, role="assistant",
                                            content="根据资料，机器学习可用于预测分析。",
                                            citations=[{"document_id": q15_doc_id,
                                                        "title": "Q15机器学习文档",
                                                        "snippet": q15_parents[0].text[:150],
                                                        "parent_id": q15_parents[0].id,
                                                        "chunk_index": 0}]))
                    db.commit()
                    q15_sid = q15_session.id
                with mock.patch.object(qa_mod.llm_mod, "chat") as q15_chat, \
                        mock.patch.object(qa_mod, "hybrid_search",
                                          return_value=[]) as q15_hs:
                    r = c.post("/api/qa/ask", headers=H(token_tech),
                               json={"question": "量子力学中的薛定谔方程如何推导？",
                                     "session_id": q15_sid})
                assert r.status_code == 200, r.text
                q15_data = r.json()["data"]
                assert "未在资料库中找到相关内容" in q15_data["answer"], q15_data
                assert q15_data["citations"] == [], q15_data
                assert q15_chat.call_count == 0, "候选文档无 token 匹配时不得调用 LLM"
                assert q15_hs.call_count == 1
                check("Q15. 历史 citations 与当前问题无 token 匹配时返回未找到且不串扰")

                # ---------- Q14 准备：保存多轮会话消息快照 ----------
                q14_sid = q10_sid
                r = c.get(f"/api/qa/sessions/{q14_sid}/messages", headers=H(token_tech))
                assert r.status_code == 200, r.text
                q14_expected = r.json()["data"]["messages"]
                assert len(q14_expected) == 4, q14_expected  # Q10 两轮：user+assistant 各两条
                assert all(m["role"] in ("user", "assistant") for m in q14_expected)
                assert all("citations" in m for m in q14_expected)
                check("Q14a. 会话消息按 id 升序返回完整 user/assistant 与 citations")

        # 第二段 TestClient：同一测试库重启后再次查询消息接口，记录应完全一致
        with mock.patch("app.main._prewarm"), \
                mock.patch("app.main._ensure_vector_health"):
            with TestClient(app) as c2:
                token_tech2 = login(c2, "s5_tech")
                r = c2.get(f"/api/qa/sessions/{q14_sid}/messages", headers=H(token_tech2))
                assert r.status_code == 200, r.text
                q14_after = r.json()["data"]["messages"]
                assert q14_after == q14_expected, "重启后消息记录应完全一致"
                check("Q14b. 服务重启后再次查询会话消息返回相同记录")

        print(f"\n=== ALL {len(passed)} S5 QA TESTS PASSED ===")
    finally:
        try:
            from app.db import engine
            engine.dispose()
        except Exception:
            pass
        try:
            import app.vector_store as vector_store
            vector_store._client = None
            vector_store._collection = None
        except Exception:
            pass
        shutil.rmtree(TEST_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
