"""Contract models for published template listings."""

from typing import Annotated, Literal

from pydantic import Field

from app.models._base import ContractModel

Scenario = Literal["poster", "article_image"]
Style = Literal["fresh", "asymmetric", "lively", "elegant", "modern", "retro"]


class TemplateSummary(ContractModel):
    id: Annotated[str, Field(max_length=100)]
    version: Annotated[int, Field(ge=1)]
    name: Annotated[str, Field(max_length=60)]
    scenario: Scenario
    style: Style
    thumbnail_url: Annotated[str, Field(max_length=500)]


class TemplateList(ContractModel):
    items: list[TemplateSummary]