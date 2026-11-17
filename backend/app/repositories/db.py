"""SQLite storage with short transactions and the reviewed database contract."""

import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.config import PROJECT_ROOT
from app.errors import ApiError

TASK_COLUMNS = {
    "id": "id",
    "ownerSessionId": "owner_session_id",
    "revision": "revision",
    "scenario": "scenario",
    "style": "style",
    "rawText": "raw_text",
    "fields": "fields_json",
    "userEditedFields": "user_edited_fields_json",
    "assetIds": "asset_ids_json",
    "templateId": "template_id",
    "templateVersion": "template_version",
    "analysis": "analysis_json",
    "analysisMode": "analysis_mode",
    "confirmedRevision": "confirmed_revision",
    "previewAssetId": "preview_asset_id",
    "previewRevision": "preview_revision",
    "phase": "phase",
    "lastError": "last_error_json",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
}
JSON_KEYS = {key for key, column in TASK_COLUMNS.items() if column.endswith("_json")}


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def task_payload(row):
    return {
        key: json.loads(row[column])
        if key in JSON_KEYS and row[column] is not None
        else row[column]
        for key, column in TASK_COLUMNS.items()
        if key != "ownerSessionId"
    }


def save_task(connection, task, owner, *, insert=False):
    values = dict(task, ownerSessionId=owner)
    columns = list(TASK_COLUMNS.values())
    params = [
        encode(values[key]) if key in JSON_KEYS and values[key] is not None else values[key]
        for key in TASK_COLUMNS
    ]
    if insert:
        connection.execute(
            f"INSERT INTO tasks ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            params,
        )
    else:
        connection.execute(
            f"UPDATE tasks SET {','.join(column + '=?' for column in columns)} WHERE id=? AND owner_session_id=?",
            [*params, task["id"], owner],
        )


def owned_row(connection, table, identifier, owner):
    # Table names are internal constants, never request parameters.
    if table not in {"tasks", "assets", "jobs"}:
        raise ValueError("Unknown internal table")
    row = connection.execute(
        f"SELECT * FROM {table} WHERE id=? AND owner_session_id=?", (identifier, owner)
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "未找到当前会话的资源")
    return row


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            if not connection.execute(
                "SELECT name FROM sqlite_master WHERE name='sessions'"
            ).fetchone():
                schema = (PROJECT_ROOT / "backend/migrations/001_init.sql").read_text(
                    encoding="utf-8"
                )
                connection.executescript(schema)
                connection.execute("PRAGMA user_version=1")

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
