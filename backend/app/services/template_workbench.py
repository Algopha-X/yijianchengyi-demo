"""Private template drafts, bounded grids and independently reviewed publications."""

import json
from contextlib import closing
from uuid import uuid4

from app.errors import ApiError
from app.rendering.renderer import render_template
from app.services import accounts
from app.services.studio import now, require
from app.services.templates import (
    FIELD_KEYS,
    TemplateValidationError,
    field_capacity_errors,
    validate_template_payload,
)

SAMPLE_FIELDS = {
    "title": "示例标题", "subtitle": "副标题", "body": "示例正文，请替换为自己的内容。",
    "time": "活动时间", "location": "活动地点", "organizer": "主办方",
}


def initialize(database):
    with database.transaction() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS template_drafts (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES sessions(id),
                author_account_id TEXT REFERENCES accounts(id),
                version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL CHECK(status IN ('draft','pending','rejected','approved')),
                source_template_id TEXT NOT NULL, document_json TEXT NOT NULL,
                grid_json TEXT, review_comment TEXT NOT NULL DEFAULT '',
                reviewer_account_id TEXT REFERENCES accounts(id),
                publication_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS template_drafts_owner ON template_drafts(owner_id);
            CREATE TABLE IF NOT EXISTS template_publications (
                id TEXT PRIMARY KEY, draft_id TEXT NOT NULL REFERENCES template_drafts(id),
                draft_version INTEGER NOT NULL, document_json TEXT NOT NULL,
                thumbnail_png BLOB NOT NULL, author_account_id TEXT NOT NULL REFERENCES accounts(id),
                reviewer_account_id TEXT NOT NULL REFERENCES accounts(id), published_at TEXT NOT NULL,
                UNIQUE(draft_id,draft_version)
            );
            CREATE TRIGGER IF NOT EXISTS template_publications_no_update
            BEFORE UPDATE ON template_publications BEGIN
                SELECT RAISE(ABORT,'published templates are immutable');
            END;
            CREATE TRIGGER IF NOT EXISTS template_publications_no_delete
            BEFORE DELETE ON template_publications BEGIN
                SELECT RAISE(ABORT,'published templates are immutable');
            END;
        """)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def validated(document):
    try:
        result = validate_template_payload(document)
    except TemplateValidationError:
        raise ApiError(422, "VALIDATION_ERROR", "模板内容不符合模板字段、尺寸或字体规范") from None
    require(len(encode(result).encode("utf-8")) <= 262144,
            422, "VALIDATION_ERROR", "模板 JSON 不能超过 256 KiB")
    require(all(len(field["label"]) <= 60 and len(field["question"]) <= 300
                for field in result["fields"].values()),
            422, "VALIDATION_ERROR", "字段名称最多 60 字，问题最多 300 字")
    return result


def view(row):
    return {
        "id": row["id"], "version": row["version"], "status": row["status"],
        "sourceTemplateId": row["source_template_id"],
        "document": json.loads(row["document_json"]),
        "grid": json.loads(row["grid_json"]) if row["grid_json"] else None,
        "reviewComment": row["review_comment"], "publicationId": row["publication_id"],
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
    }


def owned(connection, owner, identifier):
    row = connection.execute(
        "SELECT * FROM template_drafts WHERE id=? AND owner_id=?", (identifier, owner)
    ).fetchone()
    require(row is not None, 404, "NOT_FOUND", "未找到当前工作区的模板草稿")
    return row


def version_matches(row, version):
    require(row["version"] == version, 409, "TEMPLATE_VERSION_CONFLICT",
            "模板草稿已更新，请重新载入后再操作", {"currentVersion": row["version"]})


def create(service, owner, current, source_id, name):
    document = validated(service.template(source_id))
    document["name"] = name.strip()
    require(bool(document["name"]), 422, "VALIDATION_ERROR", "模板名称不能为空")
    identifier, stamp = str(uuid4()), now()
    with service.db.transaction() as connection:
        account = accounts.current_account(connection, current)
        connection.execute(
            "INSERT INTO template_drafts(id,owner_id,author_account_id,status,source_template_id,"
            "document_json,created_at,updated_at) VALUES (?,?,?,'draft',?,?,?,?)",
            (identifier, owner, account["id"] if account else None, source_id,
             encode(document), stamp, stamp),
        )
        return view(owned(connection, owner, identifier))


def grid_document(document, grid):
    """Convert finite cells/spans into trusted boxes; no HTML or nested code is accepted."""
    result = validated(document)
    canvas = result["canvas"]
    columns, rows, margin, gap = (grid[key] for key in ("columns", "rows", "margin", "gap"))
    cell_width = (canvas["width"] - 2 * margin - (columns - 1) * gap) // columns
    cell_height = (canvas["height"] - 2 * margin - (rows - 1) * gap) // rows
    require(cell_width >= 1 and cell_height >= 1, 422, "GRID_INVALID", "网格边距和间距过大")
    placements = grid["placements"]
    require(len(placements) == 7 and {item["target"] for item in placements} == {*FIELD_KEYS, "image"},
            422, "GRID_INVALID", "网格须恰好包含六个文字区域和一个图片区域")
    occupied = set()
    boxes = {}
    for item in placements:
        col, row, col_span, row_span = (item[key] for key in ("column", "row", "columnSpan", "rowSpan"))
        require(col + col_span <= columns and row + row_span <= rows,
                422, "GRID_INVALID", "合并单元格不能超出网格范围")
        cells = {(x, y) for x in range(col, col + col_span) for y in range(row, row + row_span)}
        require(not (occupied & cells), 422, "GRID_OVERLAP", "文字或图片区块不能占用重叠单元格")
        occupied.update(cells)
        boxes[item["target"]] = {
            "x": margin + col * (cell_width + gap), "y": margin + row * (cell_height + gap),
            "width": col_span * cell_width + (col_span - 1) * gap,
            "height": row_span * cell_height + (row_span - 1) * gap,
        }
    for key in FIELD_KEYS:
        result["fields"][key]["box"] = boxes[key]
    result["imageSlot"]["box"] = boxes["image"]
    result["withoutImage"] = {key: boxes[key] for key in FIELD_KEYS}
    return validated(result)


def update(service, owner, identifier, expected_version, document, grid):
    document = validated(document)
    if grid is not None:
        document = grid_document(document, grid)
    with service.db.transaction() as connection:
        row = owned(connection, owner, identifier)
        version_matches(row, expected_version)
        require(row["status"] in {"draft", "rejected"}, 409, "TEMPLATE_LOCKED",
                "待审或已发布模板不可修改，请等待审核或复制为新草稿")
        connection.execute(
            "UPDATE template_drafts SET document_json=?,grid_json=?,version=version+1,status='draft',"
            "review_comment='',reviewer_account_id=NULL,updated_at=? WHERE id=? AND version=?",
            (encode(document), encode(grid) if grid is not None else None, now(), identifier, expected_version),
        )
        return view(owned(connection, owner, identifier))


def submit(service, owner, current, identifier, expected_version):
    with service.db.transaction() as connection:
        account = accounts.require_account(connection, current)
        row = owned(connection, owner, identifier)
        version_matches(row, expected_version)
        require(row["status"] in {"draft", "rejected"}, 409, "TEMPLATE_LOCKED", "该模板已提交或已发布")
        validated(json.loads(row["document_json"]))
        connection.execute(
            "UPDATE template_drafts SET status='pending',version=version+1,author_account_id=?,updated_at=? WHERE id=?",
            (account["id"], now(), identifier),
        )
        return view(owned(connection, owner, identifier))


def reviewable(connection, current, identifier, expected_version):
    account = accounts.require_account(connection, current, {"admin", "reviewer"})
    row = connection.execute("SELECT * FROM template_drafts WHERE id=?", (identifier,)).fetchone()
    require(row is not None, 404, "NOT_FOUND", "未找到模板草稿")
    version_matches(row, expected_version)
    require(row["status"] == "pending", 409, "TEMPLATE_NOT_PENDING", "该模板当前不在待审状态")
    require(row["author_account_id"] != account["id"] and row["owner_id"] != account["workspace_id"],
            403, "SELF_REVIEW_FORBIDDEN", "不能审核自己提交的模板")
    return account, row


async def review(service, current, identifier, expected_version, decision, comment):
    with service.db.transaction() as connection:
        _, row = reviewable(connection, current, identifier, expected_version)
        document = validated(json.loads(row["document_json"]))
    thumbnail = None
    if decision == "approve":
        fields = {key: value[:document["fields"][key]["maxChars"]] for key, value in SAMPLE_FIELDS.items()}
        thumbnail = await render_template(document, fields)
    # Recheck after Chromium returns: the reviewed document/version must be identical.
    with service.db.transaction() as connection:
        account, row = reviewable(connection, current, identifier, expected_version)
        publication = f"draft-{identifier}-v{expected_version}" if decision == "approve" else None
        if publication is not None:
            connection.execute(
                "INSERT INTO template_publications VALUES (?,?,?,?,?,?,?,?)",
                (publication, identifier, expected_version, row["document_json"], thumbnail,
                 row["author_account_id"], account["id"], now()),
            )
        connection.execute(
            "UPDATE template_drafts SET status=?,version=version+1,review_comment=?,"
            "reviewer_account_id=?,publication_id=?,updated_at=? WHERE id=?",
            ("approved" if decision == "approve" else "rejected", comment,
             account["id"], publication, now(), identifier),
        )
        return view(connection.execute("SELECT * FROM template_drafts WHERE id=?", (identifier,)).fetchone())


async def review_preview(service, current, identifier, expected_version):
    with service.db.transaction() as connection:
        _, row = reviewable(connection, current, identifier, expected_version)
        document = validated(json.loads(row["document_json"]))
    fields = {key: value[:document["fields"][key]["maxChars"]] for key, value in SAMPLE_FIELDS.items()}
    content = await render_template(document, fields)
    with service.db.transaction() as connection:
        reviewable(connection, current, identifier, expected_version)
    return content


async def preview(service, owner, identifier, expected_version, fields, asset_id=None):
    with service.db.transaction() as connection:
        row = owned(connection, owner, identifier)
        version_matches(row, expected_version)
        document = validated(json.loads(row["document_json"]))
    errors = field_capacity_errors(document, fields)
    require(not errors, 422, "TEXT_OVERFLOW", "预览内容超过模板字段字数上限", {"fields": errors})
    image_path = service.get_asset(owner, asset_id)[1] if asset_id else None
    content = await render_template(document, fields, image_path)
    with service.db.transaction() as connection:
        version_matches(owned(connection, owner, identifier), expected_version)
    return content


def published_row(database, identifier):
    # Studio.template may be called inside an existing write transaction. A
    # separate WAL read must not acquire a second BEGIN IMMEDIATE writer lock.
    with closing(database.connect()) as connection:
        row = connection.execute("SELECT * FROM template_publications WHERE id=?", (identifier,)).fetchone()
    require(row is not None, 404, "NOT_FOUND", "未找到已发布模板")
    return row


def get_published(database, identifier):
    return validated(json.loads(published_row(database, identifier)["document_json"]))


def publication_view(row):
    document = json.loads(row["document_json"])
    return {
        "id": row["id"], "version": document["version"], "name": document["name"],
        "scenario": document["scenario"], "style": document["style"],
        "thumbnailUrl": f"/api/v2/published-templates/{row['id']}/thumbnail",
        "publishedAt": row["published_at"],
    }
