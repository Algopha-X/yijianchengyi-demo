"""Environment configuration; paths are always relative to the repository root."""

from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigurationError(RuntimeError):
    """A startup error whose message is safe to show without configuration values."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_allowed_origins: str = "http://127.0.0.1:5173,http://127.0.0.1:8000"
    data_dir: Path = Path("data")
    database_path: Path = Path("data/app.db")
    llm_mode: Literal["mock", "real"] = "mock"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=60, gt=0, le=60)
    llm_max_retries: int = Field(default=1, ge=0, le=1)
    job_concurrency: int = Field(default=2, ge=1, le=2)
    job_queue_limit: int = Field(default=20, ge=1, le=20)
    session_ttl_days: int = Field(default=7, ge=1, le=7)
    cookie_secure: bool = False
    verification_mode: Literal["development", "smtp"] = "development"
    smtp_host: str | None = None
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    image_mode: Literal["mock", "real"] = "mock"
    image_base_url: str | None = None
    image_api_key: SecretStr | None = None
    image_model: str | None = None

    @field_validator("data_dir", "database_path")
    @classmethod
    def resolve_project_path(cls, value: Path) -> Path:
        return (PROJECT_ROOT / value).resolve()

    @field_validator("llm_base_url", "llm_api_key", "llm_model", mode="before")
    @classmethod
    def normalize_optional_values(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def validate_real_configuration(self) -> Self:
        if self.llm_mode != "real":
            return self
        missing = [
            name
            for name in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL")
            if getattr(self, name.lower()) is None
        ]
        if missing:
            raise ValueError("real 模式缺少必填配置：" + "、".join(missing))
        try:
            url = urlsplit(self.llm_base_url)
            valid = (
                url.scheme == "https"
                and bool(url.hostname)
                and url.username is None
                and url.password is None
                and not url.query
                and not url.fragment
                and (url.port is None or 1 <= url.port <= 65535)
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("LLM_BASE_URL 必须是无用户凭据、查询参数和片段的 HTTPS API 基路径")
        return self


def load_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        issues = []
        for error in exc.errors(include_input=False, include_context=False, include_url=False):
            location = ".".join(str(part).upper() for part in error["loc"])
            issues.append(f"{location}: {error['msg']}" if location else error["msg"])
        raise ConfigurationError("配置无效：" + "；".join(issues)) from None
