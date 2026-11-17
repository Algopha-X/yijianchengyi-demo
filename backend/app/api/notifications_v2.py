"""Owner-scoped persistent completion notices and truthful queue positions."""

from fastapi import APIRouter

from app.api.studio import JobId, Owner, Service
from app.repositories.db import owned_row
from app.services.studio import job_payload, require

router = APIRouter(prefix="/api/v2", tags=["SRS 通知扩展"])


@router.get("/notifications")
def notifications(service: Service, owner: Owner):
    with service.db.transaction() as connection:
        rows = connection.execute("""SELECT j.*,r.job_id AS read_job FROM jobs j
            LEFT JOIN notification_reads r ON r.job_id=j.id AND r.owner_id=?
            WHERE j.owner_session_id=? AND j.status IN ('succeeded','failed','superseded')
            ORDER BY j.finished_at DESC LIMIT 100""", (owner, owner)).fetchall()
        return {"items": [{"job": job_payload(row), "read": row["read_job"] is not None} for row in rows]}


@router.post("/notifications/{jobId}/read")
def read_notification(job_id: JobId, service: Service, owner: Owner):
    with service.db.transaction() as connection:
        job = owned_row(connection, "jobs", str(job_id), owner)
        require(job["status"] in {"succeeded", "failed", "superseded"}, 409, "JOB_NOT_FINISHED", "任务尚未结束，暂不能标记结果通知")
        connection.execute("INSERT OR IGNORE INTO notification_reads VALUES (?,?)", (owner, str(job_id)))
    return {"read": True}


@router.get("/jobs/{jobId}")
def job_status(job_id: JobId, service: Service, owner: Owner):
    with service.db.transaction() as connection:
        row = owned_row(connection, "jobs", str(job_id), owner)
        position = None
        if row["status"] == "queued":
            position = connection.execute("""SELECT count(*) FROM jobs WHERE status='queued'
                AND (created_at<? OR (created_at=? AND id<=?))""", (row["created_at"], row["created_at"], row["id"])).fetchone()[0]
        return {**job_payload(row), "queuePosition": position}
