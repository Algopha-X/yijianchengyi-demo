"""Material management with ownership checks and separate public review."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response
from pydantic import Field, StrictBool, StrictInt, field_validator

from app.api.accounts_v2 import RawSession
from app.api.studio import AssetId, Owner, Service
from app.models._base import ContractModel, ensure_unique
from app.services import accounts, materials
from app.services.studio import require

router = APIRouter(prefix="/api/v2/materials", tags=["SRS 素材扩展"])


class MetadataRequest(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    tags: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=20)]], Field(max_length=10)
    ] = []
    request_public: StrictBool = False

    @field_validator("tags")
    @classmethod
    def unique(cls, value):
        return ensure_unique(value)


class FavoriteRequest(ContractModel):
    favorite: StrictBool


class CropRequest(ContractModel):
    x: Annotated[StrictInt, Field(ge=0)]
    y: Annotated[StrictInt, Field(ge=0)]
    width: Annotated[StrictInt, Field(ge=1, le=10000)]
    height: Annotated[StrictInt, Field(ge=1, le=10000)]


class GenerateRequest(ContractModel):
    prompt: Annotated[str, Field(min_length=1, max_length=2000)]
    source_asset_id: UUID | None = None
    width: Literal[512, 1024] = 1024
    height: Literal[512, 1024] = 1024


class ReviewRequest(ContractModel):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    decision: Literal["approve", "reject"]
    note: Annotated[str, Field(max_length=500)] = ""


def reviewer(connection, current):
    account = accounts.current_account(connection, current)
    require(
        account is not None and not account["banned"] and account["role"] in {"admin", "reviewer"},
        403,
        "REVIEW_FORBIDDEN",
        "需要审核员或管理员权限",
    )
    return account


@router.get("")
def listing(
    service: Service,
    owner: Owner,
    search: Annotated[str, Query(max_length=100)] = "",
    scope: Literal["private", "public", "all"] = "private",
    favorite: bool = False,
):
    return materials.list_materials(service, owner, search, scope, favorite)


@router.post("/generate", status_code=201)
async def generate(payload: GenerateRequest, service: Service, owner: Owner):
    return await materials.generate(service, owner, payload)


@router.get("/review-queue")
def review_queue(service: Service, current: RawSession, owner: Owner):
    with service.db.transaction() as connection:
        reviewer(connection, current)
        rows = connection.execute(
            "SELECT a.* FROM assets a JOIN material_details m ON m.asset_id=a.id WHERE m.visibility='pending' ORDER BY a.created_at LIMIT 100"
        ).fetchall()
        return {"items": [materials.material_view(connection, row, owner) for row in rows]}


@router.patch("/{assetId}")
def metadata(asset_id: AssetId, payload: MetadataRequest, service: Service, owner: Owner):
    require(
        bool(payload.name.strip()) and all(tag.strip() for tag in payload.tags),
        422,
        "VALIDATION_ERROR",
        "名称和标签不能为空白",
    )
    return materials.metadata(service, owner, str(asset_id), payload)


@router.post("/{assetId}/favorite")
def favorite(asset_id: AssetId, payload: FavoriteRequest, service: Service, owner: Owner):
    with service.db.transaction() as connection:
        row = materials.accessible(connection, str(asset_id), owner)
        if payload.favorite:
            connection.execute(
                "INSERT OR IGNORE INTO material_favorites VALUES (?,?)", (owner, str(asset_id))
            )
        else:
            connection.execute(
                "DELETE FROM material_favorites WHERE owner_id=? AND asset_id=?",
                (owner, str(asset_id)),
            )
        return materials.material_view(connection, row, owner)


@router.get("/{assetId}/thumbnail")
def thumbnail(asset_id: AssetId, service: Service, owner: Owner, current: RawSession):
    with service.db.transaction() as connection:
        account = accounts.current_account(connection, current)
        can_review = bool(
            account and not account["banned"] and account["role"] in {"admin", "reviewer"}
        )
    return Response(
        materials.thumbnail(service, owner, str(asset_id), can_review),
        media_type="image/jpeg",
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/{assetId}/import", status_code=201)
def import_material(asset_id: AssetId, service: Service, owner: Owner):
    return materials.import_material(service, owner, str(asset_id))


@router.post("/{assetId}/crop", status_code=201)
def crop(asset_id: AssetId, payload: CropRequest, service: Service, owner: Owner):
    return materials.crop(service, owner, str(asset_id), payload)


@router.post("/{assetId}/review")
def review(
    asset_id: AssetId, payload: ReviewRequest, service: Service, current: RawSession, owner: Owner
):
    require(
        payload.decision != "reject" or bool(payload.note.strip()),
        422,
        "VALIDATION_ERROR",
        "拒绝时请填写审核意见",
    )
    with service.db.transaction() as connection:
        account = reviewer(connection, current)
        row = connection.execute(
            "SELECT a.* FROM assets a JOIN material_details m ON m.asset_id=a.id WHERE a.id=? AND m.visibility='pending'",
            (str(asset_id),),
        ).fetchone()
        require(row is not None, 404, "NOT_FOUND", "未找到待审素材")
        revision = connection.execute(
            "SELECT revision FROM material_details WHERE asset_id=?", (str(asset_id),)
        ).fetchone()[0]
        require(
            revision == payload.expected_revision,
            409,
            "MATERIAL_REVISION_CONFLICT",
            "素材已被修改，请重新载入后审核",
        )
        require(
            row["owner_session_id"] != account["workspace_id"],
            403,
            "SELF_REVIEW_FORBIDDEN",
            "不能审核自己提交的素材",
        )
        connection.execute(
            "UPDATE material_details SET visibility=?,review_note=?,reviewer_id=?,revision=revision+1 WHERE asset_id=?",
            (
                "public" if payload.decision == "approve" else "rejected",
                payload.note,
                account["id"],
                str(asset_id),
            ),
        )
        return materials.material_view(connection, row, owner)
