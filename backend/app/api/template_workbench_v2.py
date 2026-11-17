"""Strict JSON template editor, finite grid preview and public review endpoints."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response
from fastapi import Path as PathParam
from pydantic import Field, StrictInt, model_validator

from app.api.accounts_v2 import RawSession
from app.api.studio import Owner, Service
from app.models._base import ContractModel
from app.models.analysis import Fields
from app.services import accounts
from app.services import template_workbench as workbench
from app.services.templates import TemplateDocumentModel

router = APIRouter(prefix="/api/v2", tags=["SRS 模板工作台"])
DraftId = Annotated[UUID, PathParam(alias="draftId")]
PublicationId = Annotated[str, PathParam(alias="publicationId", pattern=r"^draft-[0-9a-f-]{36}-v[1-9][0-9]*$")]


class CreateDraft(ContractModel):
    source_template_id: Annotated[str, Field(min_length=1, max_length=100)]
    name: Annotated[str, Field(min_length=1, max_length=60)]


class VersionRequest(ContractModel):
    expected_version: Annotated[StrictInt, Field(ge=1)]


class Placement(ContractModel):
    target: Literal["title", "subtitle", "body", "time", "location", "organizer", "image"]
    column: Annotated[StrictInt, Field(ge=0, le=11)]
    row: Annotated[StrictInt, Field(ge=0, le=15)]
    column_span: Annotated[StrictInt, Field(ge=1, le=12)] = 1
    row_span: Annotated[StrictInt, Field(ge=1, le=16)] = 1


class Grid(ContractModel):
    columns: Annotated[StrictInt, Field(ge=1, le=12)]
    rows: Annotated[StrictInt, Field(ge=1, le=16)]
    margin: Annotated[StrictInt, Field(ge=0, le=200)] = 40
    gap: Annotated[StrictInt, Field(ge=0, le=100)] = 16
    placements: Annotated[list[Placement], Field(min_length=7, max_length=7)]


class UpdateDraft(VersionRequest):
    document: TemplateDocumentModel
    grid: Grid | None = None


class PreviewDraft(VersionRequest):
    fields: Fields
    asset_id: UUID | None = None


class ReviewDraft(VersionRequest):
    decision: Literal["approve", "reject"]
    comment: Annotated[str, Field(max_length=500)] = ""

    @model_validator(mode="after")
    def rejection_reason(self):
        if self.decision == "reject" and not self.comment.strip():
            raise ValueError("拒绝时请提供审核意见")
        return self


@router.get("/template-drafts")
def drafts(service: Service, owner: Owner):
    with service.db.transaction() as connection:
        rows = connection.execute("SELECT * FROM template_drafts WHERE owner_id=? ORDER BY updated_at DESC LIMIT 100", (owner,)).fetchall()
    return {"items": [workbench.view(row) for row in rows]}


@router.post("/template-drafts", status_code=201)
def create(payload: CreateDraft, service: Service, owner: Owner, current: RawSession):
    return workbench.create(service, owner, current, payload.source_template_id, payload.name)


@router.get("/template-drafts/{draftId}")
def draft(draft_id: DraftId, service: Service, owner: Owner):
    with service.db.transaction() as connection:
        return workbench.view(workbench.owned(connection, owner, str(draft_id)))


@router.patch("/template-drafts/{draftId}")
def update(draft_id: DraftId, payload: UpdateDraft, service: Service, owner: Owner):
    return workbench.update(service, owner, str(draft_id), payload.expected_version,
                            payload.document.model_dump(mode="json", by_alias=True, exclude_none=True),
                            payload.grid.model_dump(mode="json", by_alias=True) if payload.grid else None)


@router.post("/template-drafts/{draftId}/preview", response_class=Response)
async def preview(draft_id: DraftId, payload: PreviewDraft, service: Service, owner: Owner):
    content = await workbench.preview(service, owner, str(draft_id), payload.expected_version,
                                       payload.fields.model_dump(), str(payload.asset_id) if payload.asset_id else None)
    return Response(content, media_type="image/png", headers={"Cache-Control": "private, no-store", "X-Template-Version": str(payload.expected_version)})


@router.post("/template-drafts/{draftId}/submit")
def submit(draft_id: DraftId, payload: VersionRequest, service: Service, owner: Owner, current: RawSession):
    return workbench.submit(service, owner, current, str(draft_id), payload.expected_version)


@router.get("/template-reviews")
def queue(service: Service, current: RawSession):
    with service.db.transaction() as connection:
        accounts.require_account(connection, current, {"admin", "reviewer"})
        rows = connection.execute("SELECT * FROM template_drafts WHERE status='pending' ORDER BY updated_at LIMIT 100").fetchall()
    return {"items": [workbench.view(row) for row in rows]}


@router.post("/template-drafts/{draftId}/review")
async def review(draft_id: DraftId, payload: ReviewDraft, service: Service, current: RawSession):
    return await workbench.review(service, current, str(draft_id), payload.expected_version, payload.decision, payload.comment.strip())


@router.post("/template-drafts/{draftId}/review-preview", response_class=Response)
async def review_preview(draft_id: DraftId, payload: VersionRequest, service: Service, current: RawSession):
    content = await workbench.review_preview(service, current, str(draft_id), payload.expected_version)
    return Response(content, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@router.get("/published-templates")
def published(service: Service, limit: Annotated[int, Query(ge=1, le=100)] = 50,
              offset: Annotated[int, Query(ge=0)] = 0):
    with service.db.transaction() as connection:
        rows = connection.execute("SELECT * FROM template_publications ORDER BY published_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        total = connection.execute("SELECT count(*) FROM template_publications").fetchone()[0]
    return {"items": [workbench.publication_view(row) for row in rows], "total": total}


@router.get("/published-templates/{publicationId}")
def published_detail(publication_id: PublicationId, service: Service):
    row = workbench.published_row(service.db, publication_id)
    return {**workbench.publication_view(row), "document": workbench.get_published(service.db, publication_id)}


@router.get("/published-templates/{publicationId}/thumbnail", response_class=Response)
def published_thumbnail(publication_id: PublicationId, service: Service):
    row = workbench.published_row(service.db, publication_id)
    return Response(row["thumbnail_png"], media_type="image/png", headers={"Cache-Control": "public, max-age=86400, immutable"})
