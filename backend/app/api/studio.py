"""Contract-facing routes for sessions, assets, tasks and jobs."""

import asyncio
import hashlib
import io
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, UploadFile
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from PIL import Image

from app.errors import ApiError
from app.models.assets import Asset
from app.models.common import Session
from app.models.jobs import Job
from app.models.tasks import ConfirmRequest, RevisionRequest, Task, TaskCreate, TaskList, TaskPatch
from app.services.studio import MAX_UPLOAD, Studio, require, revision_matches

router = APIRouter(prefix="/api")


def studio(request: Request) -> Studio:
    return request.app.state.studio


def owner(request: Request) -> str:
    return studio(request).session(request.cookies.get("ycy_session"))


Service = Annotated[Studio, Depends(studio)]
Owner = Annotated[str, Depends(owner)]
IdempotencyKey = Annotated[UUID, Header(alias="Idempotency-Key")]
TaskId = Annotated[UUID, PathParam(alias="taskId")]
AssetId = Annotated[UUID, PathParam(alias="assetId")]
JobId = Annotated[UUID, PathParam(alias="jobId")]
IMAGE_RESPONSE = {
    200: {
        "description": "图像二进制",
        "headers": {"Content-Disposition": {"schema": {"type": "string"}}},
        "content": {
            "image/png": {"schema": {"type": "string", "format": "binary"}},
            "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
        },
    },
}


@router.post("/session", response_model=Session, operation_id="createOrRenewSession")
async def session(request: Request, response: Response, service: Service):
    token, expires = service.create_session(request.cookies.get("ycy_session"))
    response.set_cookie(
        "ycy_session",
        token,
        max_age=service.settings.session_ttl_days * 86400,
        httponly=True,
        secure=service.settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Session-Scope"] = hashlib.sha256(
        service.session(token).encode()
    ).hexdigest()
    return {"expiresAt": expires}


@router.get("/tasks", response_model=TaskList, operation_id="listTasks")
async def list_tasks(service: Service, account: Owner):
    return service.list_tasks(account)


@router.post("/tasks", response_model=Task, status_code=201, operation_id="createTask")
async def create_task(payload: TaskCreate, service: Service, account: Owner):
    return service.create_task(account, payload)


@router.get("/tasks/{taskId}", response_model=Task, operation_id="getTask")
async def get_task(task_id: TaskId, service: Service, account: Owner):
    return service.get_task(account, str(task_id))


@router.patch("/tasks/{taskId}", response_model=Task, operation_id="updateTask")
async def patch_task(task_id: TaskId, payload: TaskPatch, service: Service, account: Owner):
    return service.patch_task(account, str(task_id), payload)


@router.post("/tasks/{taskId}/confirm", response_model=Task, operation_id="confirmTaskFacts")
async def confirm(task_id: TaskId, payload: ConfirmRequest, service: Service, account: Owner):
    return service.confirm(account, str(task_id), payload)


@router.post(
    "/tasks/{taskId}/analyze",
    response_model=Job,
    response_model_exclude_unset=True,
    status_code=202,
    operation_id="analyzeTask",
)
async def analyze(
    task_id: TaskId,
    payload: RevisionRequest,
    service: Service,
    account: Owner,
    idempotency_key: IdempotencyKey,
):
    return service.enqueue(
        account, str(task_id), "analyze", payload.expected_revision, str(idempotency_key)
    )


@router.post(
    "/tasks/{taskId}/render",
    response_model=Job,
    response_model_exclude_unset=True,
    status_code=202,
    operation_id="renderTask",
)
async def render(
    task_id: TaskId,
    payload: RevisionRequest,
    service: Service,
    account: Owner,
    idempotency_key: IdempotencyKey,
):
    return service.enqueue(
        account, str(task_id), "render", payload.expected_revision, str(idempotency_key)
    )


@router.get(
    "/jobs/{jobId}", response_model=Job, response_model_exclude_unset=True, operation_id="getJob"
)
async def get_job(job_id: JobId, service: Service, account: Owner):
    return service.get_job(account, str(job_id))


@router.post("/assets", response_model=Asset, status_code=201, operation_id="uploadAsset")
async def upload(file: UploadFile, service: Service, account: Owner):
    try:
        content = await file.read(MAX_UPLOAD + 1)
        return await asyncio.to_thread(service.upload, account, content, file.filename)
    finally:
        await file.close()


@router.get("/assets/{assetId}/file", operation_id="getAssetFile", response_class=Response,
            responses=IMAGE_RESPONSE)
async def asset_file(asset_id: AssetId, service: Service, account: Owner):
    row, path = service.get_asset(account, str(asset_id))
    return FileResponse(
        path,
        media_type=row["mime_type"],
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        content_disposition_type="inline",
    )


@router.get("/tasks/{taskId}/export", operation_id="exportTask", response_class=Response,
            responses=IMAGE_RESPONSE)
async def export(
    task_id: TaskId,
    expected_revision: Annotated[int, Query(alias="expectedRevision", ge=1)],
    service: Service,
    account: Owner,
    format: Annotated[Literal["png", "jpg"], Query()],
):
    task = service.get_task(account, str(task_id))
    revision_matches(task, expected_revision)
    require(
        task["previewAssetId"] is not None and task["previewRevision"] == task["revision"],
        409,
        "PREVIEW_STALE",
        "当前版本尚无可下载预览，请重新确认并生成",
    )
    _, path = service.get_asset(account, task["previewAssetId"])
    filename = f"ycy-{task['id']}.{format}"
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    if format == "png":
        return FileResponse(path, media_type="image/png", headers=headers)

    def convert():
        with Image.open(path) as source:
            output = io.BytesIO()
            background = Image.new(
                "RGB", source.size, service.template(task["templateId"])["canvas"]["background"]
            )
            if source.mode == "RGBA":
                background.paste(source, mask=source.getchannel("A"))
            else:
                background.paste(source.convert("RGB"))
            background.save(output, "JPEG", quality=92)
            return output.getvalue()

    try:
        content = await asyncio.to_thread(convert)
    except OSError:
        raise ApiError(503, "RENDER_FAILED", "导出暂时失败，请重试，原始预览已保留") from None
    return Response(content, media_type="image/jpeg", headers=headers)
