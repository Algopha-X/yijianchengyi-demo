"""Shared configuration for contract-facing Pydantic models."""

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=to_camel,
    )


def ensure_unique(values):
    if len(set(values)) != len(values):
        raise ValueError("数组元素必须唯一")
    return values