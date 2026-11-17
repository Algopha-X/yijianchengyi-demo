"""Revision-bound per-task typography and placement, validated on the server."""

import copy
import json

from app.repositories.db import encode, owned_row, save_task, task_payload
from app.services.management import view
from app.services.studio import now, require, revision_matches
from app.services.templates import TemplateValidationError, validate_template_payload


def initialize(database):
    with database.transaction() as connection:
        connection.execute("""CREATE TABLE IF NOT EXISTS task_layouts (
            task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
            template_id TEXT NOT NULL, document_json TEXT NOT NULL CHECK(json_valid(document_json))
        )""")


def effective(connection, task, original):
    row = connection.execute("SELECT * FROM task_layouts WHERE task_id=? AND template_id=?",
                             (task["id"], task["templateId"])).fetchone()
    return json.loads(row["document_json"]) if row else original


def get_layout(service, owner, identifier):
    with service.db.transaction() as connection:
        task = task_payload(owned_row(connection, "tasks", identifier, owner))
        require(bool(task["templateId"]), 409, "TEMPLATE_REQUIRED", "请先选择模板")
        original = service.template(task["templateId"])
        document = effective(connection, task, original)
        return {"revision": task["revision"], "document": document, "customized": document != original}


def patch(service, owner, identifier, payload):
    with service.db.transaction() as connection:
        task = task_payload(owned_row(connection, "tasks", identifier, owner))
        revision_matches(task, payload.expected_revision)
        require(bool(task["templateId"]), 409, "TEMPLATE_REQUIRED", "请先选择模板")
        require(not connection.execute("SELECT 1 FROM jobs WHERE task_id=? AND status IN ('queued','running')", (identifier,)).fetchone(),
                409, "TASK_BUSY", "正在生成，请等待结束后修改排版")
        original = service.template(task["templateId"])
        document = copy.deepcopy(original if payload.reset else effective(connection, task, original))
        if not payload.reset:
            for key, value in payload.fields.items():
                document["fields"][key].update(value.model_dump(mode="json", by_alias=True, exclude_unset=True))
                if value.box is not None:
                    # Explicit placement applies with and without a picture.
                    document["withoutImage"][key] = value.box.model_dump()
            if payload.image_slot is not None:
                document["imageSlot"].update(payload.image_slot.model_dump(mode="json", by_alias=True, exclude_unset=True))
        try:
            document = validate_template_payload(document)
        except TemplateValidationError as exc:
            require(False, 422, "VALIDATION_ERROR", str(exc))
        current = effective(connection, task, original)
        if document != current:
            connection.execute("INSERT INTO task_layouts VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET template_id=excluded.template_id,document_json=excluded.document_json",
                               (identifier, task["templateId"], encode(document)))
            task.update(revision=task["revision"] + 1, confirmedRevision=None, previewAssetId=None,
                        previewRevision=None, phase="needs_confirmation" if task["analysis"] else "draft",
                        lastError=None, updatedAt=now())
            save_task(connection, task, owner)
        return {"task": view(connection, task), "document": document, "customized": document != original}
