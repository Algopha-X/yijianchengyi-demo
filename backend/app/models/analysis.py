"""Contract models for requirement analysis payloads."""

from typing import Annotated, Literal

from pydantic import Field, field_validator

from app.models._base import ContractModel, ensure_unique

FactKind = Literal["time", "location", "person", "amount", "contact"]
QuestionField = Literal["title", "subtitle", "body", "time", "location", "organizer"]
IntentType = Literal["event", "announcement", "record"]


class Fact(ContractModel):
    id: Annotated[str, Field(min_length=1, max_length=80)]
    kind: FactKind
    value: Annotated[str, Field(min_length=1, max_length=500)]


class Question(ContractModel):
    field: QuestionField
    question: Annotated[str, Field(max_length=300)]


class Fields(ContractModel):
    title: Annotated[str, Field(max_length=200)]
    subtitle: Annotated[str, Field(max_length=200)]
    body: Annotated[str, Field(max_length=5000)]
    time: Annotated[str, Field(max_length=200)]
    location: Annotated[str, Field(max_length=200)]
    organizer: Annotated[str, Field(max_length=200)]


class Analysis(ContractModel):
    intent_type: IntentType
    summary: Annotated[str, Field(max_length=500)]
    recommended_template_ids: Annotated[
        list[Annotated[str, Field(max_length=100)]], Field(max_length=3)
    ]
    fields: Fields
    missing_fields: list[QuestionField]
    questions: Annotated[list[Question], Field(max_length=6)]
    facts: Annotated[list[Fact], Field(max_length=50)]
    warnings: Annotated[list[Annotated[str, Field(max_length=500)]], Field(max_length=20)]

    @field_validator("recommended_template_ids", "missing_fields")
    @classmethod
    def _items_unique(cls, value):
        return ensure_unique(value)