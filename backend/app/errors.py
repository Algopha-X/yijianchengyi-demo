"""Translate application and framework failures into the public error contract."""

import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Use only public messages and details here, never upstream error bodies."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details if details is not None else {}


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or str(uuid4())
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": message, "details": details or {}},
            "requestId": request_id,
        },
        headers={**(headers or {}), "X-Request-ID": request_id},
    )


async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "请求执行失败 requestId=%s exceptionType=%s",
        getattr(request.state, "request_id", "unknown"),
        type(exc).__name__,
    )
    return error_response(request, 500, "INTERNAL_ERROR", "服务暂时出现问题，请稍后重试")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def application_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        code, message = {
            401: ("SESSION_REQUIRED", "请先建立或续期会话"),
            403: ("FORBIDDEN", "无权执行此操作"),
            404: ("NOT_FOUND", "未找到请求的资源"),
            405: ("METHOD_NOT_ALLOWED", "此接口不支持该请求方法"),
            413: ("FILE_TOO_LARGE", "上传文件过大"),
            415: ("UNSUPPORTED_IMAGE", "不支持此图片格式"),
            422: ("VALIDATION_ERROR", "请求参数不符合要求"),
            429: ("TOO_MANY_REQUESTS", "请求过于频繁，请稍后重试"),
            503: ("SERVICE_UNAVAILABLE", "服务暂时不可用，请稍后重试"),
        }.get(exc.status_code, ("HTTP_ERROR", "请求未能完成"))
        return error_response(request, exc.status_code, code, message, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "field": ".".join(str(part) for part in error["loc"]),
                "message": "此项输入不符合要求",
                "type": error["type"],
            }
            for error in exc.errors()
        ]
        return error_response(
            request, 422, "VALIDATION_ERROR", "请求参数不符合要求", {"fields": fields}
        )
