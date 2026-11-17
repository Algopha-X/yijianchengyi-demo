"""SRS export extension; the original frozen PNG/JPG endpoint stays compatible."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.api.studio import Owner, Service, TaskId
from app.services.exports import ExportFormat, export_preview
from app.services.studio import require, revision_matches

router = APIRouter(prefix="/api/v2")


@router.get(
    "/tasks/{taskId}/export",
    operation_id="exportTaskV2",
    response_class=Response,
    responses={
        200: {
            "description": "当前预览导出；2 倍为普通像素缩放，PDF 为单页栅格图",
            "content": {
                "image/png": {"schema": {"type": "string", "format": "binary"}},
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
            },
        },
    },
)
async def export(
    task_id: TaskId,
    expected_revision: Annotated[int, Query(alias="expectedRevision", ge=1)],
    service: Service,
    account: Owner,
    format: Annotated[ExportFormat, Query()],
    scale: Annotated[int, Query(ge=1, le=2)] = 1,
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
    exported = await asyncio.to_thread(
        export_preview,
        path,
        format,
        scale=scale,
        background=service.template(task["templateId"])["canvas"]["background"],
    )
    filename = f"ycy-{task['id']}-{scale}x.{format}"
    return Response(
        exported.content,
        media_type=exported.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Scale": str(scale),
            "X-Export-Width": str(exported.width),
            "X-Export-Height": str(exported.height),
            "X-Export-Resize": "native" if scale == 1 else "raster-upscale",
            "X-Export-Content": "raster-preview",
        },
    )
