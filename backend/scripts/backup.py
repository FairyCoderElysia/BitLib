# -*- coding: utf-8 -*-
"""企业资料管理系统 · 数据备份脚本。

将 backend/data/（app.db + uploads/ 私有文件 + chroma/ 向量库）整体打包为
zip（按日期命名），输出到 backend/backup/ 目录，并打印完成信息。

用法：
    cd backend
    python scripts/backup.py            # 默认备份
    python scripts/backup.py "D:\\bak"  # 指定输出目录

恢复（备份恢复）：
    1) 停服（停止 uvicorn / docker compose down）
    2) 将备份 zip 解压回 backend/data/（覆盖 app.db、uploads/、chroma/）
    3) 重启服务

说明：
    - data/test_* 目录为各里程碑测试脚本使用的独立测试库，不参与备份；
    - 建议停服后备份，或至少保持 SQLite WAL 一致性（WAL/SHM 文件一并打包）；
    - 可将本脚本加入计划任务（Windows 任务计划 / cron）实现每日自动备份。
"""
import datetime
import sys
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent      # backend/
DATA_DIR = BASE_DIR / "data"                            # 备份源


def collect_files(root: Path) -> list:
    """收集 data/ 下待备份文件（排除 test_* 测试目录）。"""
    files = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        # 跳过测试脚本独立测试库目录（data/test_m1m2 / test_m3 / ...）
        if rel.parts and rel.parts[0].startswith("test_"):
            continue
        files.append(p)
    return files


def backup(output_dir: str | None = None) -> Path:
    if not DATA_DIR.exists():
        print(f"[backup] 数据目录不存在: {DATA_DIR}")
        sys.exit(1)

    out_dir = Path(output_dir) if output_dir else BASE_DIR / "backup"
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = out_dir / f"backup_{stamp}.zip"

    files = collect_files(DATA_DIR)
    if not files:
        print("[backup] data/ 下没有可备份的文件")
        sys.exit(1)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, f.relative_to(DATA_DIR).as_posix())

    total_mb = sum(f.stat().st_size for f in files) / 1024 / 1024
    print(f"[backup] 完成：{zip_path}")
    print(f"[backup] 文件数 {len(files)}，原始大小 {total_mb:.1f} MB")
    print(f"[backup] 恢复：停服后解压该 zip 回 {DATA_DIR} 并重启服务")
    return zip_path


if __name__ == "__main__":
    backup(sys.argv[1] if len(sys.argv) > 1 else None)
