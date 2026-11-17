"""Local proposed v2 task endpoints; the reviewed v1 schema stays unchanged."""

from typing import Annotated

from fastapi import APIRouter, Query, Response
from pydantic import Field, StrictInt

from app.api.studio import Owner, Service, TaskId
from app.models.tasks import RevisionRequest
from app.services import management

router = APIRouter(prefix="/api/v2/tasks", tags=["SRS 本地扩展"])


class RenameRequest(RevisionRequest):
    expected_management_revision: Annotated[StrictInt, Field(ge=0)]
    display_name: Annotated[str, Field(min_length=1, max_length=80)]


@router.get("")
async def list_tasks(service: Service, account: Owner):
    return management.list_tasks(service, account)


@router.get("/{taskId}")
async def get_task(task_id: TaskId, service: Service, account: Owner):
    return management.get_task(service, account, str(task_id))


@router.post("/{taskId}/rename")
async def rename(task_id: TaskId, payload: RenameRequest, service: Service, account: Owner):
    return management.rename(service, account, str(task_id), payload)


@router.post("/{taskId}/copy", status_code=201)
async def copy_task(task_id: TaskId, payload: RevisionRequest, service: Service, account: Owner):
    return management.copy_task(service, account, str(task_id), payload.expected_revision)


@router.delete("/{taskId}", status_code=204)
async def delete_task(
    task_id: TaskId, service: Service, account: Owner,
    expected_revision: Annotated[int, Query(alias="expectedRevision", ge=1)],
):
    management.delete_task(service, account, str(task_id), expected_revision)
    return Response(status_code=204)
