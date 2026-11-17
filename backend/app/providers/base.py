"""The common asynchronous analysis provider interface."""

from typing import Any, Protocol

from app.config import Settings


class AnalysisProvider(Protocol):
    async def analyze(
        self,
        task: dict[str, Any],
        templates: list[dict[str, Any]],
        selected_template: dict[str, Any] | None,
        settings: Settings,
    ) -> dict[str, Any]: ...
