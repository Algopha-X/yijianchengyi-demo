"""Contract models for task lifecycle payloads."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictInt, field_validator, model_validator

from app.models._base import ContractModel, ensure_unique
from app.models.analysis import Analysis, Fields, QuestionField
from app.models.common import Error, LlmMode
from app.models.templates import Scenario, Style

TaskPhase = Literal[
    "draft",
    "needs_input",
    "needs_confirmation",
    "ready_to_render",
    "rendering",
    "preview_ready",
    "failed",
]


class TaskCreate(ContractModel):
    scenario: Scenario
    style: Style | None = None
    raw_text: Annotated[str, Field(min_length=1, max_length=10000)]
    asset_ids: Annotated[list[UUID], Field(max_length=1)]

    @field_validator("asset_ids")
    @classmethod
    def _assets_unique(cls, value):
        return ensure_unique(value)


class TaskPatch(ContractModel):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    scenario: Scenario | None = None
    style: Style | None = None
    raw_text: Annotated[str, Field(min_length=1, max_length=10000)] | None = None
    fields: Fields | None = None
    template_id: Annotated[str, Field(max_length=100)] | None = None
    asset_ids: Annotated[list[UUID], Field(max_length=1)] | None = None

    @field_validator("asset_ids")
    @classmethod
    def _assets_unique(cls, value):
        return ensure_unique(value)

    @model_validator(mode="after")
    def _require_one_updatable_field(self):
        if len(self.model_dump(exclude_unset=True)) < 2:
            raise ValueError("expectedRevision 之外至少需要一个待更新字段")
        return self


class RevisionRequest(ContractModel):
    expected_revision: Annotated[StrictInt, Field(ge=1)]


class ConfirmRequest(ContractModel):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    confirmed_fact_ids: Annotated[list[Annotated[str, Field(max_length=80)]], Field(max_length=50)]

    @field_validator("confirmed_fact_ids")
    @classmethod
    def _facts_unique(cls, value):
        return ensure_unique(value)


class Task(ContractModel):
    id: UUID
    revision: Annotated[int, Field(ge=1)]
    scenario: Scenario
    style: Style | None
    raw_text: Annotated[str, Field(max_length=10000)]
    fields: Fields
    asset_ids: Annotated[list[UUID], Field(max_length=1)]
    template_id: Annotated[str, Field(max_length=100)] | None
    template_version: Annotated[int, Field(ge=1)] | None
    analysis: Analysis | None
    analysis_mode: LlmMode | None
    confirmed_revision: Annotated[int, Field(ge=1)] | None
    preview_asset_id: UUID | None
    preview_revision: Annotated[int, Field(ge=1)] | None
    phase: TaskPhase
    last_error: Error | None
    created_at: datetime
    updated_at: datetime
    user_edited_fields: list[QuestionField]

    @field_validator("asset_ids", "user_edited_fields")
    @classmethod
    def _values_unique(cls, value):
        return ensure_unique(value)


class TaskList(ContractModel):
    items: Annotated[list[Task], Field(max_length=50)]
