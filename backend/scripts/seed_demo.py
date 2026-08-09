# -*- coding: utf-8 -*-
"""演示数据种子（Evaluator E2E 用）：admin 直入库 3 文档 + 用户上传 1 待审批 + 部门推送。

用法：python scripts/seed_demo.py（需先存在 backend/.env 配置 ADMIN_INITIAL_PASSWORD）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

PW = "admin123"


def _upload(c, token, name: str, content: str, title: str, department_id=None):
    from io import BytesIO
    files = {"file": (name, BytesIO(content.encode("utf-8")), "text/plain")}
    data = {"title": title}
    if department_id is not None:
        data["department_id"] = str(department_id)
    r = c.post("/api/admin/documents/upload", headers={"Authorization": f"Bearer {token}"},
               files=files, data=data)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def main():
    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": PW})
        assert r.status_code == 200, f"admin 登录失败: {r.text}（请先配置 .env 的 ADMIN_INITIAL_PASSWORD）"
        token = r.json()["data"]["token"]

        depts = {d["name"]: d["id"] for d in c.get(
            "/api/auth/departments",
            headers={"Authorization": f"Bearer {token}"}).json()["data"]}

        # 3 个直入库文档（技术部 2 + 公开 1）
        _upload(c, token, "ai_guide.txt",
                "机器学习模型训练方法与实践：数据预处理、特征工程、模型选择与评估、"
                "超参数调优。企业落地机器学习项目需要完整的数据治理流程。" * 3,
                "机器学习实践指南", depts.get("技术部"))
        _upload(c, token, "network_doc.txt",
                "企业内部网络接入指南：VPN 配置、Wi-Fi 连接、防火墙策略、"
                "远程办公安全注意事项与常见问题排查。" * 3,
                "网络接入指南", depts.get("技术部"))
        _upload(c, token, "culture.txt",
                "新员工入职手册：企业文化、考勤制度、报销流程、"
                "办公环境与员工福利介绍。" * 3,
                "新员工入职手册", None)

        # 建普通用户（技术部）
        r = c.post("/api/admin/users", headers={"Authorization": f"Bearer {token}"},
                   json={"username": "zhangsan", "password": "zs123456",
                         "role": "user", "department_id": depts.get("技术部")})
        assert r.status_code == 200, r.text

        # 普通用户上传 1 个待审批文档
        r = c.post("/api/auth/login", json={"username": "zhangsan", "password": "zs123456"})
        ut = r.json()["data"]["token"]
        from io import BytesIO
        files = {"file": ("my_report.txt", BytesIO(
            ("季度工作汇报：项目进展、风险分析与下季度计划。" * 5).encode("utf-8")), "text/plain")}
        r = c.post("/api/documents/upload", headers={"Authorization": f"Bearer {ut}"},
                   files=files, data={"title": "季度工作汇报"})
        assert r.status_code == 200, r.text

        # 部门推送
        r = c.post("/api/admin/push", headers={"Authorization": f"Bearer {token}"},
                   json={"title": "技术部资料更新通知",
                         "content": "已上线《机器学习实践指南》，请查阅。",
                         "department_id": depts.get("技术部")})
        assert r.status_code == 200, r.text

        print("OK: 演示数据就绪（admin/zhangsan；3 已入库文档 + 1 待审批 + 1 部门推送）")


if __name__ == "__main__":
    main()
