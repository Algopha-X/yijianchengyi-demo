"""Private image library, public review, bounded crops and explicit image mocks."""

import asyncio
import base64
import binascii
import io
import json
from collections import Counter
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from app.config import PROJECT_ROOT
from app.errors import ApiError
from app.repositories.db import encode, owned_row
from app.services.studio import MAX_UPLOAD, asset_payload, require


def initialize(database):
    with database.transaction() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS material_details (
                asset_id TEXT PRIMARY KEY REFERENCES assets(id),
                name TEXT NOT NULL, tags_json TEXT NOT NULL DEFAULT '[]',
                visibility TEXT NOT NULL DEFAULT 'private'
                    CHECK(visibility IN ('private','pending','public','rejected')),
                review_note TEXT, reviewer_id TEXT, generated_mode TEXT
            );
            CREATE TABLE IF NOT EXISTS material_favorites (
                owner_id TEXT NOT NULL REFERENCES sessions(id),
                asset_id TEXT NOT NULL REFERENCES assets(id), PRIMARY KEY(owner_id,asset_id)
            );
            CREATE TABLE IF NOT EXISTS notification_reads (
                owner_id TEXT NOT NULL REFERENCES sessions(id),
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                PRIMARY KEY(owner_id,job_id)
            );
        """)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(material_details)")}
        if "revision" not in columns:
            connection.execute(
                "ALTER TABLE material_details ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
            )


def material_view(connection, row, owner):
    metadata = connection.execute(
        "SELECT * FROM material_details WHERE asset_id=?", (row["id"],)
    ).fetchone()
    favorite = connection.execute(
        "SELECT 1 FROM material_favorites WHERE owner_id=? AND asset_id=?", (owner, row["id"])
    ).fetchone()
    return {
        **asset_payload(row),
        "name": metadata["name"] if metadata else "未命名图片",
        "tags": json.loads(metadata["tags_json"]) if metadata else [],
        "visibility": metadata["visibility"] if metadata else "private",
        "reviewNote": metadata["review_note"] if metadata else None,
        "revision": metadata["revision"] if metadata else 0,
        "generatedMode": metadata["generated_mode"] if metadata else None,
        "favorite": bool(favorite),
        "owned": row["owner_session_id"] == owner,
        "thumbnailUrl": f"/api/v2/materials/{row['id']}/thumbnail",
    }


def accessible(connection, identifier, owner, allow_pending=False):
    row = connection.execute(
        """SELECT a.* FROM assets a LEFT JOIN material_details m ON m.asset_id=a.id
        WHERE a.id=? AND a.kind='upload' AND (a.owner_session_id=? OR m.visibility='public'
        OR (? AND m.visibility='pending'))""",
        (identifier, owner, int(allow_pending)),
    ).fetchone()
    require(row is not None, 404, "NOT_FOUND", "未找到可访问的素材")
    return row


def list_materials(service, owner, search="", scope="private", favorite=False):
    with service.db.transaction() as connection:
        rows = connection.execute(
            """SELECT a.* FROM assets a LEFT JOIN material_details m ON m.asset_id=a.id
            WHERE a.kind='upload' AND (a.owner_session_id=? OR m.visibility='public')
            ORDER BY a.created_at DESC LIMIT 500""",
            (owner,),
        ).fetchall()
        items = [material_view(connection, row, owner) for row in rows]
    needle = search.casefold().strip()
    return {
        "items": [
            item
            for item in items
            if (
                scope == "all"
                or (scope == "public" and item["visibility"] == "public")
                or (scope == "private" and item["owned"])
            )
            and (not favorite or item["favorite"])
            and (not needle or needle in (item["name"] + " " + " ".join(item["tags"])).casefold())
        ][:100]
    }


def metadata(service, owner, identifier, payload):
    with service.db.transaction() as connection:
        asset = owned_row(connection, "assets", identifier, owner)
        require(asset["kind"] == "upload", 422, "VALIDATION_ERROR", "请先将图片保存至素材库")
        row = connection.execute(
            "SELECT generated_mode FROM material_details WHERE asset_id=?", (identifier,)
        ).fetchone()
        connection.execute(
            """INSERT INTO material_details (asset_id,name,tags_json,visibility,generated_mode)
            VALUES (?,?,?,?,?) ON CONFLICT(asset_id) DO UPDATE SET name=excluded.name,
            tags_json=excluded.tags_json,visibility=excluded.visibility,review_note=NULL,reviewer_id=NULL,
            revision=material_details.revision+1""",
            (
                identifier,
                payload.name.strip(),
                encode(payload.tags),
                "pending" if payload.request_public else "private",
                row[0] if row else None,
            ),
        )
        return material_view(connection, owned_row(connection, "assets", identifier, owner), owner)


def thumbnail(service, owner, identifier, allow_pending=False):
    with service.db.transaction() as connection:
        row = accessible(connection, identifier, owner, allow_pending)
    _, path = service.get_asset(row["owner_session_id"], identifier)
    with Image.open(path) as image:
        image.thumbnail((480, 480))
        output = io.BytesIO()
        image.convert("RGB").save(output, format="JPEG", quality=78)
    return output.getvalue()


def import_material(service, owner, identifier):
    with service.db.transaction() as connection:
        row = accessible(connection, identifier, owner)
    if row["owner_session_id"] == owner:
        return asset_payload(row)
    _, path = service.get_asset(row["owner_session_id"], identifier)
    return service.upload(
        owner, path.read_bytes(), "import.png" if row["mime_type"] == "image/png" else "import.jpg"
    )


def crop(service, owner, identifier, payload):
    _, path = service.get_asset(owner, identifier)
    with Image.open(path) as image:
        require(
            payload.x + payload.width <= image.width and payload.y + payload.height <= image.height,
            422,
            "VALIDATION_ERROR",
            "裁剪区域超出原图范围",
        )
        result = image.crop(
            (payload.x, payload.y, payload.x + payload.width, payload.y + payload.height)
        )
        output = io.BytesIO()
        result.save(output, format="PNG")
    return service.upload(owner, output.getvalue(), "crop.png")


def generation_busy(service, owner):
    state = getattr(service, "_image_generation_state", None)
    return bool(state and state["owners"][owner])


@asynccontextmanager
async def generation_slot(service, owner):
    # The server runs one process. Requests are bounded in memory and are not
    # durable jobs; cancelled requests release their slot in all failure paths.
    state = getattr(service, "_image_generation_state", None)
    if state is None:
        state = {
            "semaphore": asyncio.Semaphore(service.settings.job_concurrency),
            "count": 0,
            "owners": Counter(),
        }
        service._image_generation_state = state
    require(
        state["count"] < service.settings.job_concurrency + service.settings.job_queue_limit,
        429,
        "IMAGE_QUEUE_FULL",
        "图片生成等待队列已满，请稍后重试",
    )
    state["count"] += 1
    state["owners"][owner] += 1
    acquired = False
    try:
        try:
            async with asyncio.timeout(65):
                await state["semaphore"].acquire()
                acquired = True
        except TimeoutError:
            raise ApiError(
                503, "IMAGE_QUEUE_TIMEOUT", "图片生成排队超时，素材已保留，请重试"
            ) from None
        yield
    finally:
        if acquired:
            state["semaphore"].release()
        state["count"] -= 1
        state["owners"][owner] -= 1
        if not state["owners"][owner]:
            del state["owners"][owner]


def mock_image(service, owner, payload):
    if payload.source_asset_id:
        _, path = service.get_asset(owner, str(payload.source_asset_id))
        with Image.open(path) as original:
            image = ImageEnhance.Color(original.convert("RGB")).enhance(0.65)
            image.thumbnail((payload.width, payload.height))
    else:
        seed = sum(payload.prompt.encode())
        image = Image.new("RGB", (payload.width, payload.height), (225, 236, 228))
        draw = ImageDraw.Draw(image)
        for index in range(6):
            x, y = (seed + index * 131) % image.width, (seed * 3 + index * 97) % image.height
            radius = min(image.size) // (4 + index)
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=(80 + index * 18, 130 + index * 12, 135 + index * 10),
            )
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, image.width, 36), fill="white")
    font = ImageFont.truetype(str(PROJECT_ROOT / "assets/fonts/NotoSansSC-VF.ttf"), 18)
    draw.text((12, 6), "Mock 占位图片 · 未调用图像模型", fill="black", font=font)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def save_generated(service, owner, content, mode):
    # The authoritative upload decoder enforces type, decoded pixels and bytes.
    try:
        require(len(content) <= MAX_UPLOAD, 502, "IMAGE_OUTPUT_INVALID", "图像服务返回图片过大")
        with Image.open(io.BytesIO(content)) as decoded:
            require(
                decoded.format in {"PNG", "JPEG"},
                502,
                "IMAGE_OUTPUT_INVALID",
                "图像服务须返回 JPG 或 PNG",
            )
            filename = "generated.jpg" if decoded.format == "JPEG" else "generated.png"
        asset = service.upload(owner, content, filename)
    except ApiError as error:
        if error.status_code not in {413, 415}:
            raise
        raise ApiError(
            502, "IMAGE_OUTPUT_INVALID", "图像服务返回图片未通过格式、大小或像素检查"
        ) from None
    except (OSError, ValueError, Image.DecompressionBombError):
        raise ApiError(502, "IMAGE_OUTPUT_INVALID", "图像服务未返回可解码图片") from None
    with service.db.transaction() as connection:
        connection.execute(
            "INSERT INTO material_details (asset_id,name,tags_json,generated_mode) VALUES (?,?,?,?)",
            (
                asset["id"],
                "模拟生成图片" if mode == "mock" else "模型生成图片",
                encode(["生成图片"]),
                mode,
            ),
        )
    return {
        "asset": asset,
        "mode": mode,
        "message": "Mock 图片：本地规则生成，未调用图像模型"
        if mode == "mock"
        else "真实图像接口返回，已完成解码校验",
    }


async def image_work(function, *args):
    # Python cannot stop a running worker thread. Hold the generation slot until
    # bounded decoding/saving finishes, even if the HTTP client disconnects.
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if worker.done() and not worker.cancelled():
            worker.exception()  # Retrieve any worker failure without exposing it.
        raise


async def generate(service, owner, payload):
    require(bool(payload.prompt.strip()), 422, "VALIDATION_ERROR", "请填写图片描述")
    # Check ownership even when the selected real editing protocol is unavailable.
    if payload.source_asset_id:
        service.get_asset(owner, str(payload.source_asset_id))
    async with generation_slot(service, owner):
        # A request may have resolved its owner just before another request
        # imported that anonymous workspace. Do not write into its old owner.
        with service.db.transaction() as connection:
            relocated = connection.execute(
                "SELECT a.workspace_id FROM accounts a JOIN account_sessions s ON s.account_id=a.id WHERE s.session_id=?",
                (owner,),
            ).fetchone()
            require(
                not relocated or relocated["workspace_id"] == owner,
                409,
                "WORKSPACE_CHANGED",
                "工作区已切换，请重新提交图片生成请求",
            )
        if service.settings.image_mode == "mock":
            content = await image_work(mock_image, service, owner, payload)
            mode = "mock"
        else:
            settings = service.settings
            require(
                all([settings.image_base_url, settings.image_api_key, settings.image_model]),
                503,
                "IMAGE_NOT_CONFIGURED",
                "真实图像服务配置不完整，未使用 Mock 替代",
            )
            try:
                parts = urlsplit(settings.image_base_url)
                valid_url = (
                    parts.scheme == "https"
                    and bool(parts.hostname)
                    and not parts.username
                    and not parts.password
                    and not parts.query
                    and not parts.fragment
                    and (parts.port is None or 1 <= parts.port <= 65535)
                )
            except ValueError:
                valid_url = False
            require(valid_url, 503, "IMAGE_NOT_CONFIGURED", "图像服务地址须为 HTTPS API 基路径")
            require(
                payload.source_asset_id is None,
                503,
                "IMAGE_EDIT_NOT_CONFIGURED",
                "图生图供应商协议尚未配置，未执行模拟替代",
            )
            content = await real_image(settings, payload)
            mode = "real"
        return await image_work(save_generated, service, owner, content, mode)


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重复 JSON 字段")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("JSON 数值必须有限")


async def real_image(settings, payload):
    try:
        async with asyncio.timeout(60):
            for attempt in range(2):
                try:
                    async with httpx.AsyncClient(
                        timeout=30, trust_env=False, follow_redirects=False
                    ) as client:
                        async with client.stream(
                            "POST",
                            settings.image_base_url.rstrip("/") + "/images/generations",
                            headers={
                                "Authorization": f"Bearer {settings.image_api_key.get_secret_value()}"
                            },
                            json={
                                "model": settings.image_model,
                                "prompt": payload.prompt,
                                "size": f"{payload.width}x{payload.height}",
                                "response_format": "b64_json",
                                "n": 1,
                            },
                        ) as response:
                            if 300 <= response.status_code < 500 and response.status_code != 429:
                                raise ApiError(
                                    502,
                                    "IMAGE_PROVIDER_FAILED",
                                    "图像服务拒绝请求，请检查服务配置和权限",
                                )
                            response.raise_for_status()
                            chunks, length = [], 0
                            async for chunk in response.aiter_bytes():
                                length += len(chunk)
                                require(
                                    length <= MAX_UPLOAD * 4 // 3 + 65536,
                                    502,
                                    "IMAGE_OUTPUT_INVALID",
                                    "图像服务返回内容过大",
                                )
                                chunks.append(chunk)
                    document = json.loads(
                        b"".join(chunks),
                        object_pairs_hook=unique_json_object,
                        parse_constant=reject_constant,
                    )
                    data = document["data"]
                    require(
                        isinstance(data, list)
                        and len(data) == 1
                        and isinstance(data[0], dict)
                        and "url" not in data[0],
                        502,
                        "IMAGE_OUTPUT_INVALID",
                        "图像服务须返回单张 Base64 图片，不接收远程图片 URL",
                    )
                    encoded = data[0]["b64_json"]
                    require(
                        isinstance(encoded, str) and len(encoded) <= MAX_UPLOAD * 4 // 3 + 8,
                        502,
                        "IMAGE_OUTPUT_INVALID",
                        "图像服务返回格式无效",
                    )
                    content = base64.b64decode(encoded, validate=True)
                    require(
                        bool(content) and len(content) <= MAX_UPLOAD,
                        502,
                        "IMAGE_OUTPUT_INVALID",
                        "图像服务返回图片过大或为空",
                    )
                    return content
                except httpx.HTTPError:
                    if attempt == 1:
                        raise ApiError(
                            502, "IMAGE_PROVIDER_FAILED", "真实图像服务失败，草稿已保留，可重试"
                        ) from None
                except (ValueError, KeyError, TypeError, IndexError, binascii.Error):
                    if attempt == 1:
                        raise ApiError(
                            502, "IMAGE_OUTPUT_INVALID", "图像服务返回格式无效，未生成任何素材"
                        ) from None
    except TimeoutError:
        raise ApiError(
            504, "IMAGE_PROVIDER_TIMEOUT", "真实图像服务超时，草稿已保留，可重试"
        ) from None
