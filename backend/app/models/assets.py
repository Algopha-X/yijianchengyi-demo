"""Contract models for uploaded assets."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.models._base import ContractModel

AssetKind = Literal["upload", "preview"]
AssetMimeType = Literal["image/png", "image/jpeg"]


class Asset(ContractModel):
    id: UUID
    kind: AssetKind
    mime_type: AssetMimeType
    bytes: Annotated[int, Field(ge=1)]
    width: Annotated[int, Field(ge=1)]
    height: Annotated[int, Field(ge=1)]
    file_url: Annotated[str, Field(max_length=500)]
    created_at: datetime