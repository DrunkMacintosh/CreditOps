"""Operations-reporting read API: GET /api/v1/reporting/operations.

The ``/bao-cao-van-hanh`` surface (master design section 17.1): a role-gated,
strictly read-only aggregate dashboard.  It exposes ONLY grouped counts of
operational health -- tasks by status, queue-age bands, human gates by
type/status, the outbox backlog, documents by stage, alerts by status.  By
construction the payload carries NO per-case identifier, document body, or
secret: it can neither reach a single case nor mutate anything.

Access requires the synthetic ``REPORTING_VIEWER`` role (fail closed 403).  This
router is deliberately NOT wired into ``main.py`` here; it exports ``router`` for
the lead to mount.
"""

from __future__ import annotations

from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.ports.reporting import (
    OperationsMetrics,
    ReportingRepository,
)
from creditops.application.unit_of_work import ActorContext

router = APIRouter(prefix="/api/v1/reporting", tags=["reporting"])

# PROPOSED synthetic JWT role for the read-only operations-reporting surface.
# Defined LOCALLY: it is NOT part of the closed case-assignment role set
# (migration 202607180008) because reporting is an aggregate, non-case-scoped
# view, not a case participant role.  The official SHB reporting/RBAC mapping is
# an OPEN QUESTION (design section 24); this identifier is synthetic and the
# surface fails closed until a token carries it.
REPORTING_VIEWER_ROLE = "REPORTING_VIEWER"


class StatusCountResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    status: str
    count: int


class QueueAgeBucketResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    status: str
    bucket: str
    count: int


class GateStatusCountResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    gate_type: str = Field(serialization_alias="gateType")
    status: str
    count: int


class StageCountResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    stage: str
    count: int


class OutboxBacklogResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    undispatched_count: int = Field(serialization_alias="undispatchedCount")
    max_attempts: int = Field(serialization_alias="maxAttempts")


class OperationsReportResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    #: Synthetic-prototype marker (design section 2): every metric family below
    #: is a PROPOSED synthetic aggregate, not an official SHB operational report.
    label: str = "SYNTHETIC"
    tasks_by_status: list[StatusCountResponse] = Field(serialization_alias="tasksByStatus")
    queue_age_buckets: list[QueueAgeBucketResponse] = Field(
        serialization_alias="queueAgeBuckets"
    )
    human_gates_by_type_status: list[GateStatusCountResponse] = Field(
        serialization_alias="humanGatesByTypeStatus"
    )
    outbox: OutboxBacklogResponse
    documents_by_stage: list[StageCountResponse] = Field(
        serialization_alias="documentsByStage"
    )
    alerts_by_status: list[StatusCountResponse] = Field(
        serialization_alias="alertsByStatus"
    )


Actor = Annotated[ActorContext, Depends(require_actor)]


def _require_reporting_viewer(actor: ActorContext) -> None:
    """Fail closed unless the actor holds the synthetic REPORTING_VIEWER role."""
    if REPORTING_VIEWER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò xem báo cáo vận hành được yêu cầu.",
        )


def _reporting_repository(request: Request) -> ReportingRepository:
    repository = getattr(request.app.state, "reporting_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="REPORTING_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ báo cáo vận hành chưa sẵn sàng.",
            retryable=True,
        )
    return cast(ReportingRepository, repository)


def _report_response(metrics: OperationsMetrics) -> OperationsReportResponse:
    return OperationsReportResponse(
        tasks_by_status=[
            StatusCountResponse(status=row.status, count=row.count)
            for row in metrics.tasks_by_status
        ],
        queue_age_buckets=[
            QueueAgeBucketResponse(status=row.status, bucket=row.bucket, count=row.count)
            for row in metrics.queue_age_buckets
        ],
        human_gates_by_type_status=[
            GateStatusCountResponse(
                gate_type=row.gate_type, status=row.status, count=row.count
            )
            for row in metrics.human_gates
        ],
        outbox=OutboxBacklogResponse(
            undispatched_count=metrics.outbox.undispatched_count,
            max_attempts=metrics.outbox.max_attempts,
        ),
        documents_by_stage=[
            StageCountResponse(stage=row.stage, count=row.count)
            for row in metrics.documents_by_stage
        ],
        alerts_by_status=[
            StatusCountResponse(status=row.status, count=row.count)
            for row in metrics.alerts_by_status
        ],
    )


@router.get("/operations", response_model=OperationsReportResponse)
async def operations_report(actor: Actor, request: Request) -> OperationsReportResponse:
    _require_reporting_viewer(actor)
    repository = _reporting_repository(request)
    metrics = await repository.load_operations_metrics()
    return _report_response(metrics)
