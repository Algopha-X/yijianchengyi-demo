"""Public template catalog endpoints."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi import Path as PathParam
from fastapi.responses import FileResponse

from app.errors import ApiError
from app.models.templates import Scenario, Style, TemplateList
from app.services.templates import (
    TemplateCatalog,
    TemplateNotFoundError,
    TemplateValidationError,
    ThumbnailNotFoundError,
    get_template_catalog,
)

router = APIRouter(prefix="/api/templates", tags=["templates"])


def _catalog_unavailable(error: TemplateValidationError) -> ApiError:
    return ApiError(
        503,
        "SERVICE_UNAVAILABLE",
        "模板目录暂时不可用，请联系维护者",
        {"reason": str(error)},
    )


def _template_not_found(template_id: str) -> ApiError:
    return ApiError(404, "NOT_FOUND", "未找到模板", {"templateId": template_id})


def catalog_dependency() -> TemplateCatalog:
    try:
        return get_template_catalog()
    except TemplateValidationError as exc:
        raise _catalog_unavailable(exc) from exc


Catalog = Annotated[TemplateCatalog, Depends(catalog_dependency)]


@router.get("", response_model=TemplateList, operation_id="listTemplates")
def list_templates(
    catalog: Catalog,
    scenario: Scenario | None = None,
    style: Style | None = None,
) -> TemplateList:
    try:
        return catalog.list_templates(scenario=scenario, style=style)
    except TemplateValidationError as exc:
        raise _catalog_unavailable(exc) from exc


@router.get("/{templateId}", operation_id="getTemplate")
def get_template(
    template_id: Annotated[str, PathParam(alias="templateId")],
    catalog: Catalog,
) -> dict[str, Any]:
    try:
        return catalog.get_template(template_id)
    except TemplateNotFoundError as exc:
        raise _template_not_found(template_id) from exc


@router.get(
    "/{templateId}/thumbnail",
    operation_id="getTemplateThumbnail",
    responses={
        200: {
            "content": {
                "image/png": {},
                "image/jpeg": {},
            }
        }
    },
)
def get_template_thumbnail(
    template_id: Annotated[str, PathParam(alias="templateId")],
    catalog: Catalog,
) -> FileResponse:
    try:
        path, media_type = catalog.get_thumbnail(template_id)
    except TemplateNotFoundError as exc:
        raise _template_not_found(template_id) from exc
    except ThumbnailNotFoundError as exc:
        raise ApiError(
            404,
            "NOT_FOUND",
            "模板缩略图尚未生成，请稍后重试",
            {"templateId": template_id},
        ) from exc
    return FileResponse(
        path,
        media_type=media_type,
        content_disposition_type="inline",
    )
