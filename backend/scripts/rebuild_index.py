# -*- coding: utf-8 -*-
"""重建正式库向量索引（运维工具）。

适用场景：Chroma HNSW 索引损坏（如并发写竞态导致 "Error loading hnsw index"，
检索 500）。流程：停服务 → 删 data/chroma → 跑本脚本（对全部 approved 文档
重新走入库管线重建向量/分块/FTS）→ 重启服务。

用法：python scripts/rebuild_index.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from app import models
from app.db import SessionLocal
from app.ingest import ingest_document, ingest_text


def main():
    db: Session = SessionLocal()
    try:
        docs = db.query(models.Document).filter(
            models.Document.status == models.STATUS_APPROVED).all()
        print(f"待重建 {len(docs)} 个 approved 文档")
        ok = fail = 0
        for doc in docs:
            db.expunge(doc)
            if doc.file_path:
                ingest_document(db, doc)          # 上传文档：读文件重入库
            else:
                ingest_text(db, doc, doc.content_text or "")  # 爬虫文档：文本重入库
            db.refresh(doc)
            if doc.status == models.STATUS_APPROVED:
                ok += 1
            else:
                fail += 1
                print(f"  失败 id={doc.id} title={doc.title!r}: {doc.error_message}")
        print(f"重建完成：成功 {ok}，失败 {fail}")
        # 关键：Chroma 的 HNSW 索引由 compactor 在查询时异步构建——只写不查
        # 不会生成 HNSW 文件（目录仅 index_metadata.pickle），新进程加载即报
        # "Error loading hnsw index"。写入后执行一次查询强制触发构建，再 close。
        try:
            import app.vector_store as vs
            vs.query([0.0] * 512, 1, user_department_id=None, is_admin=True)
            print("已触发 HNSW 索引构建（查询预热）")
        except Exception as exc:
            print(f"预热查询失败（不影响已入库数据，服务进程查询时会再触发）: {exc}")
    finally:
        db.close()
        # 关键：显式关闭 Chroma client，等待 compactor 异步任务 flush 完成，
        # 否则 HNSW 索引未落盘即退出，下次加载报 "Error loading hnsw index"
        try:
            import app.vector_store as vs
            if vs._client is not None:
                vs._client.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
