"""Contract models for generation job payloads."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.models._base import ContractModel
from app.models.common import Error

JobKind = Literal["analyze", "render"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "superseded"]
JobStage = Literal[
    "queued",
    "classifying",
    "recommending",
    "checking_fields",
    "polishing",
    "validating",
    "adapting_images",
    "rendering",
    "done",
    "failed",
    "superseded",
]


class JobResult(ContractModel):
    applied_revision: Annotated[int, Field(ge=1)] | None = None
    preview_asset_id: UUID | None = None


class Job(ContractModel):
    id: UUID
    task_id: UUID
    kind: JobKind
    revision: Annotated[int, Field(ge=1)]
    status: JobStatus
    stage: JobStage
    progress: Annotated[int, Field(ge=0, le=100)]
    result: JobResult | None
    error: Error | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None