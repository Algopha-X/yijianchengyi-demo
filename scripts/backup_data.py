"""Create a new consistent SQLite snapshot and copy only its referenced images."""

import argparse
import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def backup(database: Path, data_directory: Path, destination: Path):
    database, data_directory, destination = database.resolve(), data_directory.resolve(), destination.resolve()
    if not database.is_file():
        raise ValueError("数据库不存在，未创建空备份")
    if destination.exists():
        raise ValueError("备份目标已存在，请指定新的目录；不会覆盖旧备份")
    destination.mkdir(parents=True, exist_ok=False)
    snapshot = destination / "app.db"
    # SQLite's backup API includes committed WAL pages without copying a live db file blindly.
    with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(snapshot)) as target:
        source.backup(target)
    manifest = {"createdAt": datetime.now(UTC).isoformat(), "status": "incomplete", "files": []}
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with closing(sqlite3.connect(snapshot)) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite 完整性检查失败")
        paths = [row[0] for row in connection.execute("SELECT path FROM assets ORDER BY id")]
    for relative in paths:
        source_file, target_file = (data_directory / relative).resolve(), (destination / relative).resolve()
        if not source_file.is_relative_to(data_directory) or not target_file.is_relative_to(destination):
            raise ValueError("图片路径超出数据目录，备份已停止")
        if not source_file.is_file():
            raise ValueError("数据库引用的图片缺失，备份未完成")
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_file, target_file)
        manifest["files"].append({"path": relative, "sha256": hashlib.sha256(target_file.read_bytes()).hexdigest()})
    manifest["databaseSha256"] = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    manifest["status"] = "complete"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="备份 SQLite 和其引用的图片，不覆盖既有目录")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data/app.db")
    parser.add_argument("--data-dir", type=Path, default=PROJECT_ROOT / "data")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = backup(args.database, args.data_dir, args.output)
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"备份失败：{error}\n")
    print(f"备份完成：{len(result['files'])} 张图片，SQLite 完整性检查通过。请妥善保管此目录。")


if __name__ == "__main__":
    main()
