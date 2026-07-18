from __future__ import annotations

from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.kickoff import (
    KickoffCaseNotFound,
    KickoffOrchestration,
)
from creditops.application.orchestration.roles import CASE_PARTICIPANT_ROLES
from creditops.application.orchestration.status import build_status
from creditops.application.ports.orchestration import OrchestrationRepository
from creditops.application.ports.queue import QueuePort
from creditops.application.unit_of_work import ActorContext

router = APIRouter(prefix="/api/v1/cases/{case_id}/orchestration", tags=["orchestration"])


class AdvanceAcceptedResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    task_id: UUID = Field(serialization_alias="taskId")
    case_version: int = Field(serialization_alias="caseVersion")
    status: str
    created: bool


class GateResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    gate_type: str = Field(serialization_alias="gateType")
    status: str
    disposition_ref: str | None = Field(serialization_alias="dispositionRef")
    satisfied_at: datetime | None = Field(serialization_alias="satisfiedAt")


class PlanStepResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    task_type: str = Field(serialization_alias="taskType")
    priority: int


class TaskStateResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    task_id: UUID = Field(serialization_alias="taskId")
    task_type: str = Field(serialization_alias="taskType")
    case_version: int = Field(serialization_alias="caseVersion")
    status: str


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    task_type: str = Field(serialization_alias="taskType")
    readiness: str
    reason: str


class DeadlockResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    reasons: list[str]


class OrchestrationStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    has_intake_handoff: bool = Field(serialization_alias="hasIntakeHandoff")
    plan_source: str = Field(serialization_alias="planSource")
    plan: list[PlanStepResponse]
    readiness: list[ReadinessResponse]
    tasks: list[TaskStateResponse]
    gates: list[GateResponse]
    superseded_task_ids: list[str] = Field(serialization_alias="supersededTaskIds")
    deadlock: DeadlockResponse | None


Actor = Annotated[ActorContext, Depends(require_actor)]


def _require_participant(actor: ActorContext) -> None:
    if not (CASE_PARTICIPANT_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _repository(request: Request) -> OrchestrationRepository:
    repository = getattr(request.app.state, "orchestration_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="ORCHESTRATION_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ điều phối hồ sơ chưa sẵn sàng.",
            retryable=True,
        )
    return cast(OrchestrationRepository, repository)


def _queue(request: Request) -> QueuePort:
    # Agent tasks travel on their own queue, separate from document ingestion.
    queue = getattr(request.app.state, "agent_task_queue", None)
    if queue is None:
        raise ApiException(
            status_code=503,
            code="TASK_QUEUE_UNAVAILABLE",
            message_vi="Hàng đợi xử lý chưa sẵn sàng.",
            retryable=True,
        )
    return cast(QueuePort, queue)


async def _assert_case_access(
    request: Request, actor: ActorContext, case_id: UUID
) -> None:
    """Fail closed with an indistinguishable 404 for unassigned actors."""
    uow_factory = getattr(request.app.state, "uow_factory", None)
    if uow_factory is None:
        raise ApiException(
            status_code=503,
            code="CASE_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ hồ sơ chưa sẵn sàng.",
            retryable=True,
        )
    async with uow_factory(actor) as uow:
        record = await uow.cases.get_assigned(case_id, actor.actor_id)
    if record is None:
        raise ApiException(
            status_code=404,
            code="CASE_NOT_ACCESSIBLE",
            message_vi="Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.",
        )


@router.post("/advance", response_model=AdvanceAcceptedResponse, status_code=202)
async def advance_orchestration(
    case_id: UUID,
    actor: Actor,
    request: Request,
) -> AdvanceAcceptedResponse:
    _require_participant(actor)
    await _assert_case_access(request, actor, case_id)
    kickoff = KickoffOrchestration(_repository(request), _queue(request))
    try:
        result = await kickoff.execute(case_id)
    except KickoffCaseNotFound as exc:
        raise ApiException(
            status_code=404,
            code="CASE_NOT_ACCESSIBLE",
            message_vi="Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.",
        ) from exc
    return AdvanceAcceptedResponse(
        task_id=result.task_id,
        case_version=result.case_version,
        status=result.status.value,
        created=result.created,
    )


@router.get("", response_model=OrchestrationStatusResponse)
async def get_orchestration(
    case_id: UUID,
    actor: Actor,
    request: Request,
) -> OrchestrationStatusResponse:
    _require_participant(actor)
    await _assert_case_access(request, actor, case_id)
    snapshot = await _repository(request).load_snapshot(case_id)
    if snapshot is None:
        raise ApiException(
            status_code=404,
            code="CASE_NOT_ACCESSIBLE",
            message_vi="Không tìm thấy hồ sơ hoặc bạn không có quyền truy cập.",
        )
    status = build_status(snapshot)
    return OrchestrationStatusResponse(
        case_id=case_id,
        case_version=status.case_version,
        has_intake_handoff=status.has_intake_handoff,
        plan_source=status.plan.source,
        plan=[
            PlanStepResponse(task_type=step.task_type.value, priority=step.priority)
            for step in status.plan.steps
        ],
        readiness=[
            ReadinessResponse(
                task_type=assessment.task_type.value,
                readiness=assessment.readiness.value,
                reason=assessment.reason,
            )
            for assessment in status.readiness.assessments
        ],
        tasks=[
            TaskStateResponse(
                task_id=task.task_id,
                task_type=task.task_type.value,
                case_version=task.case_version,
                status=task.status.value,
            )
            for task in status.tasks
        ],
        gates=[
            GateResponse(
                gate_type=gate.gate_type.value,
                status=gate.status.value,
                disposition_ref=gate.disposition_ref,
                satisfied_at=gate.satisfied_at,
            )
            for gate in status.gates
        ],
        superseded_task_ids=list(status.readiness.superseded_task_ids),
        deadlock=(
            DeadlockResponse(reasons=list(status.deadlock.reasons))
            if status.deadlock is not None
            else None
        ),
    )
