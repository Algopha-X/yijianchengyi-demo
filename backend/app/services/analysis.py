"""Choose the configured provider and expose contract-valid camelCase analysis."""

from typing import Any

from app.config import Settings
from app.providers.base import AnalysisProvider
from app.providers.chat_completions import ChatCompletionsProvider
from app.providers.mock import MockProvider


async def analyze(
    task: dict[str, Any],
    templates: list[dict[str, Any]],
    selected_template: dict[str, Any] | None,
    settings: Settings,
) -> dict[str, Any]:
    provider: AnalysisProvider = (
        MockProvider() if settings.llm_mode == "mock" else ChatCompletionsProvider()
    )
    return await provider.analyze(task, templates, selected_template, settings)
