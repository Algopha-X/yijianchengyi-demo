"""Administrator diagnostics and explicit runtime provider-mode selection."""

from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from pydantic import ValidationError

from app.api.accounts_v2 import RawSession
from app.api.studio import Service
from app.config import Settings
from app.models._base import ContractModel
from app.services import accounts
from app.services.studio import require

router = APIRouter(prefix="/api/v2/admin", tags=["SRS 运行管理"])


class ModeRequest(ContractModel):
    llm_mode: Literal["mock", "real"]
    image_mode: Literal["mock", "real"]


def status(service, current):
    with service.db.transaction() as connection:
        accounts.require_account(connection, current, {"admin"})
        counts = {row[0]: row[1] for row in connection.execute("SELECT status,count(*) FROM jobs GROUP BY status")}
        jobs = connection.execute("SELECT id,kind,status,stage,created_at,finished_at,error_json FROM jobs ORDER BY created_at DESC LIMIT 50").fetchall()
        user_count = connection.execute("SELECT count(*) FROM accounts").fetchone()[0]
        storage = connection.execute("SELECT coalesce(sum(bytes),0) FROM assets").fetchone()[0]
    settings = service.settings
    return {"llmMode": settings.llm_mode, "imageMode": settings.image_mode,
            "llmConfigured": bool(settings.llm_base_url and settings.llm_api_key and settings.llm_model),
            "imageConfigured": bool(settings.image_base_url and settings.image_api_key and settings.image_model),
            "verificationMode": settings.verification_mode,
            "mailConfigured": bool(settings.smtp_host and settings.smtp_user and settings.smtp_password and settings.smtp_from),
            "jobCounts": counts, "userCount": user_count, "storageBytes": storage,
            "imageRequests": getattr(service, "_image_generation_state", {}).get("count", 0),
            "recentJobs": [{"id": row["id"], "kind": row["kind"], "status": row["status"], "stage": row["stage"],
                            "createdAt": row["created_at"], "finishedAt": row["finished_at"], "failed": row["error_json"] is not None} for row in jobs]}


@router.get("/status")
def diagnostics(service: Service, current: RawSession):
    return status(service, current)


@router.post("/services")
async def select_services(payload: ModeRequest, request: Request, service: Service, current: RawSession):
    with service.db.transaction() as connection:
        accounts.require_account(connection, current, {"admin"})
        require(not connection.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running')").fetchone(),
                409, "SERVICE_BUSY", "仍有生成任务，完成后才能切换服务模式")
        require(not getattr(service, "_image_generation_state", {}).get("count", 0),
                409, "SERVICE_BUSY", "仍有图片生成请求，完成后才能切换服务模式")
    values = service.settings.model_dump()
    values.update(llm_mode=payload.llm_mode, image_mode=payload.image_mode)
    try:
        updated = Settings(_env_file=None, **values)
        if updated.image_mode == "real":
            require(all([updated.image_base_url, updated.image_api_key, updated.image_model]), 503,
                    "IMAGE_NOT_CONFIGURED", "真实图像服务配置不完整，请先在后端配置")
            url = urlsplit(updated.image_base_url)
            require(url.scheme == "https" and url.hostname and not url.username and not url.password
                    and not url.query and not url.fragment and (url.port is None or 1 <= url.port <= 65535),
                    503, "IMAGE_NOT_CONFIGURED", "真实图像服务地址配置无效")
    except (ValidationError, ValueError):
        require(False, 503, "PROVIDER_NOT_CONFIGURED", "真实服务配置未通过检查，请先在后端配置，当前模式未改变")
    service.settings = updated
    request.app.state.settings = updated
    return {**status(service, current), "message": "运行模式已更新；服务重启后恢复后端环境变量中的配置"}
