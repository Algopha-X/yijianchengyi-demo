"""Bounded template object edits; no user HTML, CSS or executable code."""

from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import Field, StrictInt

from app.api.studio import Owner, Service, TaskId
from app.models._base import ContractModel
from app.models.tasks import RevisionRequest
from app.services import layouts
from app.services.templates import FIELD_KEYS, BoxModel

router = APIRouter(prefix="/api/v2/tasks", tags=["SRS 排版编辑"])


class FieldEdit(ContractModel):
    font_size: Annotated[StrictInt, Field(ge=12, le=200)] | None = None
    min_font_size: Annotated[StrictInt, Field(ge=12, le=200)] | None = None
    font_weight: Literal[400, 700] | None = None
    color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None
    align: Literal["left", "center", "right"] | None = None
    box: BoxModel | None = None


class ImageEdit(ContractModel):
    fit: Literal["cover", "contain"] | None = None
    box: BoxModel | None = None


class LayoutRequest(RevisionRequest):
    fields: dict[Literal["title", "subtitle", "body", "time", "location", "organizer"], FieldEdit] = Field(default_factory=dict, max_length=len(FIELD_KEYS))
    image_slot: ImageEdit | None = None
    reset: bool = False


@router.get("/{taskId}/layout")
def get_layout(task_id: TaskId, service: Service, owner: Owner):
    return layouts.get_layout(service, owner, str(task_id))


@router.patch("/{taskId}/layout")
def edit_layout(task_id: TaskId, payload: LayoutRequest, service: Service, owner: Owner):
    return layouts.patch(service, owner, str(task_id), payload)
