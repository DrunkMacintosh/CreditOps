"""Independent Risk Review API: read-only status + human disposition writes.

GET mirrors the read-only maker-output APIs (``api/underwriting.py``,
``api/legal.py``): a case-participant role is required, row access is the
case-assignment check, and an unassigned actor receives an indistinguishable
404.  There is no way to write an assessment or a challenge here -- both are
append-only and written exclusively by the worker
(application/risk_review/processor.py).

POST is the ONLY human write surface for a checker's output: recording a
disposition on one challenge, or an assessment-level disposition when the
checker raised nothing severe.  A disposition never deletes or edits the
challenge/assessment row it disposes (challenge_dispositions is append-only,
DB-enforced).  Restricted to the RISK_REVIEWER human role.  After recording a
disposition, this handler re-derives G3_RISK_DISPOSITION
(application/orchestration/gates.py::derive_g3_status) and, only if that pure
derivation says SATISFIED, calls the orchestration repository to record it --
the checker/agent code never calls this; only a human disposition can trigger
it, and only through this deterministic derivation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.gates import derive_g3_status
from creditops.application.orchestration.roles import (
    CASE_PARTICIPANT_ROLES,
    RISK_REVIEWER_ROLE,
)
from creditops.application.ports.orchestration import OrchestrationRepository
from creditops.application.ports.risk_review import RiskReviewRepository
from creditops.application.unit_of_work import ActorContext
from creditops.domain.orchestration import GateStatus, GateType
from creditops.domain.risk_review import ChallengeSeverity

router = APIRouter(prefix="/api/v1/cases/{case_id}/risk-review", tags=["risk-review"])

_DISPOSITION_TYPES = frozenset({"ACCEPTED_RISK", "MAKER_MUST_REVISE", "ESCALATED", "NOTED"})


class HandoffStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    handoff_id: UUID = Field(serialization_alias="handoffId")
    state: str
    created_at: datetime = Field(serialization_alias="createdAt")


class DispositionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    disposition_type: str = Field(serialization_alias="dispositionType")
    rationale_vi: str = Field(serialization_alias="rationale")
    actor_id: UUID = Field(serialization_alias="actorId")
    actor_role: str = Field(serialization_alias="actorRole")
    created_at: datetime = Field(serialization_alias="createdAt")


class ChallengeStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    target: dict[str, object]
    challenge_type: str = Field(serialization_alias="challengeType")
    statement_vi: str = Field(serialization_alias="statement")
    citations: list[dict[str, object]]
    severity: str
    confidence: str
    raised_by: str = Field(serialization_alias="raisedBy")
    dispositions: list[DispositionResponse]


class RiskReviewStatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    assessment_id: UUID = Field(serialization_alias="assessmentId")
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    agent_role: str = Field(serialization_alias="agentRole")
    execution_id: UUID = Field(serialization_alias="executionId")
    prompt_version: str = Field(serialization_alias="promptVersion")
    created_at: datetime = Field(serialization_alias="createdAt")
    handoff: HandoffStatusResponse | None
    challenges: list[ChallengeStatusResponse]
    assessment_level_dispositions: list[DispositionResponse] = Field(
        serialization_alias="assessmentLevelDispositions"
    )
    unresolved_challenge_count: int = Field(serialization_alias="unresolvedChallengeCount")
    gate_status: str = Field(serialization_alias="gateStatus")


class RecordDispositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    disposition_type: str = Field(alias="dispositionType", min_length=1, max_length=50)
    rationale_vi: str = Field(alias="rationale", min_length=1, max_length=4000)


Actor = Annotated[ActorContext, Depends(require_actor)]


def _require_participant(actor: ActorContext) -> None:
    if not (CASE_PARTICIPANT_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _require_risk_reviewer(actor: ActorContext) -> None:
    if RISK_REVIEWER_ROLE not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò rà soát rủi ro độc lập được yêu cầu.",
        )


def _repository(request: Request) -> RiskReviewRepository:
    repository = getattr(request.app.state, "risk_review_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="RISK_REVIEW_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ rà soát rủi ro độc lập chưa sẵn sàng.",
            retryable=True,
        )
    return cast(RiskReviewRepository, repository)


def _orchestration_repository(request: Request) -> OrchestrationRepository | None:
    repository = getattr(request.app.state, "orchestration_repository", None)
    return cast("OrchestrationRepository | None", repository)


async def _assert_case_access(request: Request, actor: ActorContext, case_id: UUID) -> None:
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


@router.get("", response_model=RiskReviewStatusResponse)
async def get_risk_review(
    case_id: UUID, actor: Actor, request: Request
) -> RiskReviewStatusResponse:
    _require_participant(actor)
    await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    record = await repository.load_latest_assessment(case_id)
    if record is None:
        raise ApiException(
            status_code=404,
            code="RISK_REVIEW_NOT_AVAILABLE",
            message_vi="Chưa có bản rà soát rủi ro độc lập cho hồ sơ này.",
        )
    dispositions = await repository.load_dispositions(case_id, record.case_version)
    by_challenge: dict[UUID, list[Any]] = {}
    assessment_level: list[Any] = []
    for disposition in dispositions:
        if disposition.challenge_id is None:
            assessment_level.append(disposition)
        else:
            by_challenge.setdefault(disposition.challenge_id, []).append(disposition)

    raw_challenges = record.assessment.get("challenges", [])
    challenges: list[ChallengeStatusResponse] = []
    severities: dict[UUID, ChallengeSeverity] = {}
    if isinstance(raw_challenges, list):
        for item in raw_challenges:
            if not isinstance(item, dict):
                continue
            challenge_id = UUID(str(item["id"]))
            severities[challenge_id] = ChallengeSeverity(str(item["severity"]))
            bound = by_challenge.get(challenge_id, [])
            challenges.append(
                ChallengeStatusResponse(
                    id=challenge_id,
                    target=dict(item.get("target", {})),
                    challenge_type=str(item.get("challenge_type", "")),
                    statement_vi=str(item.get("statement_vi", "")),
                    citations=[dict(c) for c in item.get("citations", [])],
                    severity=str(item.get("severity", "")),
                    confidence=str(item.get("confidence", "")),
                    raised_by=str(item.get("raised_by", "")),
                    dispositions=[_disposition_response(d) for d in bound],
                )
            )

    unresolved = sum(1 for challenge in challenges if not challenge.dispositions)
    gate_status = derive_g3_status(
        assessment_exists=True,
        challenge_severities=severities,
        disposed_challenge_ids=set(by_challenge.keys()),
        has_assessment_level_disposition=bool(assessment_level),
    )

    return RiskReviewStatusResponse(
        assessment_id=record.assessment_id,
        case_id=record.case_id,
        case_version=record.case_version,
        agent_role=record.agent_role,
        execution_id=record.execution_id,
        prompt_version=record.prompt_version,
        created_at=record.created_at,
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
        challenges=challenges,
        assessment_level_dispositions=[_disposition_response(d) for d in assessment_level],
        unresolved_challenge_count=unresolved,
        gate_status=gate_status.value,
    )


@router.post(
    "/challenges/{challenge_id}/disposition",
    response_model=DispositionResponse,
    status_code=201,
)
async def record_challenge_disposition(
    case_id: UUID,
    challenge_id: UUID,
    body: RecordDispositionRequest,
    actor: Actor,
    request: Request,
) -> DispositionResponse:
    _require_risk_reviewer(actor)
    await _assert_case_access(request, actor, case_id)
    return await _record_disposition(
        request, actor, case_id, challenge_id=challenge_id, body=body
    )


@router.post("/disposition", response_model=DispositionResponse, status_code=201)
async def record_assessment_level_disposition(
    case_id: UUID,
    body: RecordDispositionRequest,
    actor: Actor,
    request: Request,
) -> DispositionResponse:
    """The explicit human NOTED disposition required when the checker raised
    no severe challenge at all -- G3 must never derive SATISFIED from
    silence (deliverable 4/(f))."""

    _require_risk_reviewer(actor)
    if body.disposition_type != "NOTED":
        raise ApiException(
            status_code=422,
            code="INVALID_DISPOSITION_TYPE",
            message_vi="Quyết định ở cấp bản đánh giá chỉ được phép là NOTED.",
        )
    await _assert_case_access(request, actor, case_id)
    return await _record_disposition(request, actor, case_id, challenge_id=None, body=body)


async def _record_disposition(
    request: Request,
    actor: ActorContext,
    case_id: UUID,
    *,
    challenge_id: UUID | None,
    body: RecordDispositionRequest,
) -> DispositionResponse:
    if body.disposition_type not in _DISPOSITION_TYPES:
        raise ApiException(
            status_code=422,
            code="INVALID_DISPOSITION_TYPE",
            message_vi="Loại quyết định không hợp lệ.",
        )
    repository = _repository(request)
    record = await repository.load_latest_assessment(case_id)
    if record is None:
        raise ApiException(
            status_code=404,
            code="RISK_REVIEW_NOT_AVAILABLE",
            message_vi="Chưa có bản rà soát rủi ro độc lập cho hồ sơ này.",
        )
    if challenge_id is not None:
        raw_challenges = record.assessment.get("challenges", [])
        known_ids = (
            {str(item["id"]) for item in raw_challenges if isinstance(item, dict)}
            if isinstance(raw_challenges, list)
            else set()
        )
        if str(challenge_id) not in known_ids:
            raise ApiException(
                status_code=404,
                code="CHALLENGE_NOT_FOUND",
                message_vi="Không tìm thấy thách thức trong bản rà soát này.",
            )

    disposition = await repository.record_disposition(
        disposition_id=uuid4(),
        assessment_id=record.assessment_id,
        challenge_id=challenge_id,
        disposition_type=body.disposition_type,
        rationale_vi=body.rationale_vi,
        actor_id=actor.actor_id,
        actor_role=RISK_REVIEWER_ROLE,
    )

    await _maybe_satisfy_g3(request, record, actor)
    return _disposition_response(disposition)


async def _maybe_satisfy_g3(
    request: Request, record: Any, actor: ActorContext
) -> None:
    """Re-derive G3 after a disposition and record it ONLY if now SATISFIED.

    This is the human-triggered write path described in
    ``application/orchestration/gates.py::derive_g3_status``; the checker
    processor never calls this.
    """

    orchestration_repository = _orchestration_repository(request)
    if orchestration_repository is None:
        return
    repository = _repository(request)
    dispositions = await repository.load_dispositions(record.case_id, record.case_version)
    disposed_ids = {d.challenge_id for d in dispositions if d.challenge_id is not None}
    has_assessment_level = any(d.challenge_id is None for d in dispositions)
    raw_challenges = record.assessment.get("challenges", [])
    severities = {
        UUID(str(item["id"])): ChallengeSeverity(str(item["severity"]))
        for item in raw_challenges
        if isinstance(item, dict)
    }
    status = derive_g3_status(
        assessment_exists=True,
        challenge_severities=severities,
        disposed_challenge_ids=disposed_ids,
        has_assessment_level_disposition=has_assessment_level,
    )
    if status is not GateStatus.SATISFIED:
        return
    await orchestration_repository.ensure_gate(
        case_id=record.case_id,
        case_version=record.case_version,
        gate_type=GateType.G3_RISK_DISPOSITION,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=f"risk-review-assessment:{record.assessment_id}",
    )


def _disposition_response(disposition: Any) -> DispositionResponse:
    return DispositionResponse(
        id=disposition.id,
        disposition_type=disposition.disposition_type,
        rationale_vi=disposition.rationale_vi,
        actor_id=disposition.actor_id,
        actor_role=disposition.actor_role,
        created_at=disposition.created_at,
    )
