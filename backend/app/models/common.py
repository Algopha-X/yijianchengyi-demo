"""Contract models shared by every endpoint."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field

from app.models._base import ContractModel

LlmMode = Literal["mock", "real"]


class Error(ContractModel):
    code: Annotated[str, Field(max_length=100)]
    message: Annotated[str, Field(max_length=1000)]
    details: dict[str, Any]
    retryable: bool | None = None


class ErrorResponse(ContractModel):
    error: Error
    request_id: UUID


class Health(ContractModel):
    status: Literal["ok"]
    mode: LlmMode
    version: Annotated[str, Field(max_length=50)]


class Session(ContractModel):
    expires_at: datetime