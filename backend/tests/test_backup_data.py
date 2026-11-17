"""A backup must restore referenced images and reject unsafe or partial inputs."""

import importlib.util
import json
import sqlite3
from contextlib import closing

import pytest

from app.config import PROJECT_ROOT

spec = importlib.util.spec_from_file_location("backup_data", PROJECT_ROOT / "scripts/backup_data.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def database(tmp_path, relative="assets/test.png"):
    path = tmp_path / "source.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE assets(id TEXT,path TEXT)")
        connection.execute("INSERT INTO assets VALUES ('id',?)", (relative,))
        connection.commit()
    return path


def test_consistent_snapshot_referenced_files_and_no_overwrite(tmp_path):
    source = database(tmp_path)
    data = tmp_path / "data"
    (data / "assets").mkdir(parents=True)
    (data / "assets/test.png").write_bytes(b"immutable-image-content")
    result = module.backup(source, data, tmp_path / "backup")
    assert result["status"] == "complete" and len(result["files"]) == 1
    assert (tmp_path / "backup/assets/test.png").read_bytes() == b"immutable-image-content"
    with closing(sqlite3.connect(tmp_path / "backup/app.db")) as snapshot:
        assert snapshot.execute("SELECT path FROM assets").fetchone()[0] == "assets/test.png"
    with pytest.raises(ValueError, match="已存在"):
        module.backup(source, data, tmp_path / "backup")


def test_missing_file_leaves_explicit_incomplete_backup(tmp_path):
    with pytest.raises(ValueError, match="缺失"):
        module.backup(database(tmp_path), tmp_path / "data", tmp_path / "backup")
    assert json.loads((tmp_path / "backup/manifest.json").read_text())["status"] == "incomplete"


def test_unsafe_database_path_does_not_copy_outside_directory(tmp_path):
    source = database(tmp_path, "../private.txt")
    (tmp_path / "private.txt").write_text("private")
    with pytest.raises(ValueError, match="超出"):
        module.backup(source, tmp_path / "data", tmp_path / "backup")
    assert not (tmp_path / "backup/private.txt").exists()
