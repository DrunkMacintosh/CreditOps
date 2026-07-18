"""Reviewer-output API: read-only assessment status + stage-4 human review gate.

GET is read-only, mirroring ``api/underwriting.py``: a case-participant role is
required, row access is the case-assignment check, and an unassigned actor
receives an indistinguishable 404.  The assessment store is append-only and
written exclusively by the worker.

POST ``/review`` is the human write surface for the stage-4 legal review gate
(master design section 5 giai đoạn 4), restricted to the ``LEGAL_REVIEWER``
human role.  It mirrors ``api/underwriting.py``'s review: the reviewed
``assessmentId`` MUST be the CURRENT latest legal assessment for the current case
version or the request is rejected 409 ``STALE_ASSESSMENT`` -- no gate is
written.  On success it satisfies ``HG_LEGAL_ASSESSMENT_REVIEWED`` through the
orchestration repository (the gate-writing authority stays out of the legal
port), re-ticks the orchestrator, and audits.

PROPOSED: the gate is recorded human state surfaced to the legal surface.  It is
NOT required_gate on any task-graph node; whether downstream readiness should
later REQUIRE it is a deferred decision, not wired here.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.orchestration.roles import CASE_PARTICIPANT_ROLES
from creditops.application.ports.legal import (
    LatestLegalAssessmentRecord,
    LegalRepository,
)
from creditops.application.ports.orchestration import (
    OrchestrationAuditEvent,
    OrchestrationRepository,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.application.unit_of_work import ActorContext
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.domain.orchestration import GateStatus, GateType
from creditops.observability import log_event

router = APIRouter(prefix="/api/v1/cases/{case_id}/legal", tags=["legal"])

_logger = logging.getLogger(__name__)

#: PROPOSED synthetic human role permitted to review the legal / compliance /
#: collateral output.  No official SHB role mapping (docs/AGENT_ARCHITECTURE.md).
LEGAL_REVIEWER_ROLE = "LEGAL_REVIEWER"

#: PROPOSED synthetic disposition-reference prefix bound to the reviewed
#: assessment (no official SHB mapping).
_REVIEW_DISPOSITION_REF_PREFIX = "legal-assessment"


class HandoffStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    handoff_id: UUID = Field(serialization_alias="handoffId")
    state: str
    created_at: datetime = Field(serialization_alias="createdAt")


class LegalAssessmentResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    assessment_id: UUID = Field(serialization_alias="assessmentId")
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    agent_role: str = Field(serialization_alias="agentRole")
    execution_id: UUID = Field(serialization_alias="executionId")
    prompt_version: str = Field(serialization_alias="promptVersion")
    created_at: datetime = Field(serialization_alias="createdAt")
    assessment: dict[str, object]
    handoff: HandoffStatusResponse | None


class GateWriteResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    gate_type: str = Field(serialization_alias="gateType")
    status: str
    assessment_id: UUID = Field(serialization_alias="assessmentId")
    disposition_ref: str = Field(serialization_alias="dispositionRef")


class ReviewAssessmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    assessment_id: UUID = Field(alias="assessmentId")
    rationale_vi: str = Field(alias="rationale", min_length=1, max_length=4000)


Actor = Annotated[ActorContext, Depends(require_actor)]


def _require_participant(actor: ActorContext) -> None:
    if not (CASE_PARTICIPANT_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _require_legal_reviewer(actor: ActorContext) -> None:
    if LEGAL_REVIEWER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò rà soát pháp lý được yêu cầu.",
        )


def _repository(request: Request) -> LegalRepository:
    repository = getattr(request.app.state, "legal_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="LEGAL_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ pháp lý, tuân thủ và tài sản bảo đảm chưa sẵn sàng.",
            retryable=True,
        )
    return cast(LegalRepository, repository)


def _orchestration_repository(request: Request) -> OrchestrationRepository:
    repository = getattr(request.app.state, "orchestration_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="ORCHESTRATION_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ điều phối chưa sẵn sàng.",
            retryable=True,
        )
    return cast(OrchestrationRepository, repository)


async def _assert_case_access(
    request: Request, actor: ActorContext, case_id: UUID
) -> CaseRecord:
    """Return the assigned case record, or fail closed with an indistinguishable
    404 for an unassigned actor (assignment membership is never disclosed)."""
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
    return cast(CaseRecord, record)


async def _require_current_assessment(
    repository: LegalRepository,
    case_id: UUID,
    case_version: int,
    assessment_id: UUID,
) -> LatestLegalAssessmentRecord:
    """Load the latest assessment and assert it is the CURRENT one the human is
    acting on: it must exist, be bound to the current case version, and match the
    referenced ``assessment_id``.  A stale reference never writes a gate."""
    record = await repository.load_latest_assessment(case_id)
    if record is None:
        raise ApiException(
            status_code=404,
            code="LEGAL_ASSESSMENT_NOT_AVAILABLE",
            message_vi="Chưa có bản đánh giá pháp lý cho hồ sơ này.",
        )
    if record.case_version != case_version or record.assessment_id != assessment_id:
        raise ApiException(
            status_code=409,
            code="STALE_ASSESSMENT",
            message_vi=(
                "Bản đánh giá pháp lý đã thay đổi; vui lòng xem xét bản mới nhất "
                "của phiên bản hồ sơ hiện tại."
            ),
            details={
                "currentAssessmentId": str(record.assessment_id),
                "expectedCaseVersion": record.case_version,
            },
        )
    return record


@router.get("", response_model=LegalAssessmentResponse)
async def get_legal_assessment(
    case_id: UUID,
    actor: Actor,
    request: Request,
) -> LegalAssessmentResponse:
    _require_participant(actor)
    await _assert_case_access(request, actor, case_id)
    record = await _repository(request).load_latest_assessment(case_id)
    if record is None:
        raise ApiException(
            status_code=404,
            code="LEGAL_ASSESSMENT_NOT_AVAILABLE",
            message_vi="Chưa có bản đánh giá pháp lý cho hồ sơ này.",
        )
    return LegalAssessmentResponse(
        assessment_id=record.assessment_id,
        case_id=record.case_id,
        case_version=record.case_version,
        agent_role=record.agent_role,
        execution_id=record.execution_id,
        prompt_version=record.prompt_version,
        created_at=record.created_at,
        assessment=dict(record.assessment),
        handoff=(
            HandoffStatusResponse(
                handoff_id=record.handoff_id,
                state=record.handoff_state,
                created_at=record.handoff_created_at,
            )
            if record.handoff_id is not None
            and record.handoff_state is not None
            and record.handoff_created_at is not None
            else None
        ),
    )


@router.post("/review", response_model=GateWriteResponse, status_code=200)
async def review_legal_assessment(
    case_id: UUID,
    body: ReviewAssessmentRequest,
    actor: Actor,
    request: Request,
    response: Response,
) -> GateWriteResponse:
    _require_legal_reviewer(actor)
    case = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    record = await _require_current_assessment(
        repository, case_id, case.version, body.assessment_id
    )
    orchestration = _orchestration_repository(request)

    disposition_ref = f"{_REVIEW_DISPOSITION_REF_PREFIX}:{record.assessment_id}"
    await orchestration.ensure_gate(
        case_id=case_id,
        case_version=case.version,
        gate_type=GateType.HG_LEGAL_ASSESSMENT_REVIEWED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=disposition_ref,
    )
    await repository.append_audit(
        OrchestrationAuditEvent(
            case_id=case_id,
            case_version=case.version,
            event_type="LEGAL_ASSESSMENT_REVIEWED",
            execution_id=uuid4(),
            artifact_type="LEGAL_COMPLIANCE_ASSESSMENT",
            artifact_id=record.assessment_id,
            event_data={
                "actorId": str(actor.actor_id),
                "assessmentId": str(record.assessment_id),
                "rationale": body.rationale_vi,
            },
        )
    )
    await _retick_orchestration(
        request,
        orchestration,
        case_id=case_id,
        trigger_ref=f"HG_LGL:{record.assessment_id}",
    )
    response.status_code = 200
    return GateWriteResponse(
        gate_type=GateType.HG_LEGAL_ASSESSMENT_REVIEWED.value,
        status=GateStatus.SATISFIED.value,
        assessment_id=record.assessment_id,
        disposition_ref=disposition_ref,
    )


async def _retick_orchestration(
    request: Request,
    orchestration_repository: Any,
    *,
    case_id: UUID,
    trigger_ref: str,
) -> None:
    """Self-fire an idempotent orchestration tick after a gate satisfaction.

    The plan task + outbox event commit durably; the queue publish is
    best-effort here (the recovery dispatch picks up anything left).  A tick
    failure must never fail the human's already-recorded review, but it is
    logged, never silent.
    """

    try:
        result = await KickoffOrchestration(orchestration_repository).execute(
            case_id, trigger_ref=trigger_ref
        )
        queue = getattr(request.app.state, "agent_task_queue", None)
        if queue is not None:
            await DispatchOutbox(
                orchestration_repository,
                queue,
                worker_dispatcher=getattr(
                    request.app.state, "worker_dispatcher", None
                ),
            ).run()
        log_event(
            _logger,
            logging.INFO,
            "Orchestration retick after gate satisfaction",
            {
                "event": "orchestration_retick",
                "trigger": trigger_ref,
                "created": result.created,
            },
        )
    except Exception:
        log_event(
            _logger,
            logging.ERROR,
            "Orchestration retick failed; the review is durable and the case "
            "can be advanced manually",
            {"event": "orchestration_retick_failed", "trigger": trigger_ref},
        )
