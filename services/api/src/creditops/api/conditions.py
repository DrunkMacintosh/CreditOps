"""Disbursement ConditionLedger API: human-only condition lifecycle + the gate.

Master design section 5 giai đoạn 10.  Independent credit operations verify the
signed contract, perfected security, customer own-funds participation, purpose
documents, licences and other bound conditions BEFORE disbursement.  Four
surfaces, all case-scoped and fail-closed (an unassigned actor gets the same
indistinguishable 404 as a missing case):

- POST ``/conditions`` -- the ``OPS_OFFICER`` opens ONE condition.  A condition
  may only be opened once a PERMITTING human credit decision exists for the
  current case version (an approval: ``APPROVED_AS_PROPOSED`` /
  ``APPROVED_WITH_CONDITIONS``), loaded through the credit-decision repository.
  The condition binds that decision as its source.
- GET ``/conditions`` -- any case participant reads the ledger for the current
  version.
- POST ``/conditions/{id}/transition`` -- move ONE condition along a VALIDATED
  edge (``domain/conditions.py::ALLOWED_TRANSITIONS``).  AUTHORITY SPLIT
  (PROPOSED synthetic; no official SHB matrix): ``VERIFIED`` and the human
  ``WAIVED_BY_HUMAN`` / ``NOT_APPLICABLE_BY_HUMAN`` rulings require the
  independent ``OPS_CHECKER`` role; the waiver / not-applicable rulings
  additionally require an explicit authority rationale.  Every other move is an
  ordinary ``OPS_OFFICER`` workflow step.
- POST ``/conditions/confirm`` -- the independent ``OPS_CHECKER`` confirms the
  ledger.  It fails closed unless ``derive_conditions_confirmable`` is True (409
  ``CONDITIONS_NOT_SATISFIED`` listing the blocking ids -- an EMPTY ledger is
  never confirmable) AND the confirming actor is DIFFERENT from every actor who
  drove a ``VERIFIED`` transition (409 ``SAME_ACTOR_FORBIDDEN`` -- separation of
  duty).  On success it satisfies ``HG_DISBURSEMENT_CONDITIONS_CONFIRMED``
  through the orchestration repository (exactly as ``api/financing.py`` records
  its gate -- the gate-writing authority stays out of the condition port),
  audits, and re-ticks the orchestrator.

WAIVER is HUMAN-only with an authority record: a waiver is recorded as a status
transition to ``WAIVED_BY_HUMAN`` by the ``OPS_CHECKER`` role with a mandatory
rationale, captured on the append-only status-event trail.  No agent path exists
to any surface here.

This module is exported as ``router`` and is NOT registered in ``main.py`` here;
production wiring is a separate change (tests include the router directly).

All customer data in this project is synthetic and created solely for
demonstration.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.orchestration.roles import (
    CASE_PARTICIPANT_ROLES,
    OPS_OFFICER_ROLE,
)
from creditops.application.ports.conditions import (
    ConditionLedgerRepository,
    ConditionNotFound,
    ForbiddenConditionTransition,
    RecordedCondition,
)
from creditops.application.ports.credit_decisions import CreditDecisionRepository
from creditops.application.ports.orchestration import (
    OrchestrationAuditEvent,
    OrchestrationRepository,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.application.unit_of_work import ActorContext
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.domain.conditions import (
    CHECKER_AUTHORITY_TARGETS,
    CONFIRMABLE_STATUSES,
    RATIONALE_REQUIRED_TARGETS,
    ConditionStatus,
    DisbursementCondition,
    derive_conditions_confirmable,
    is_transition_allowed,
)
from creditops.domain.credit_decisions import APPROVAL_DECISIONS
from creditops.domain.orchestration import GateStatus, GateType
from creditops.observability import log_event

router = APIRouter(prefix="/api/v1/cases/{case_id}/conditions", tags=["conditions"])

_logger = logging.getLogger(__name__)

#: PROPOSED synthetic JWT authority role for the INDEPENDENT credit-operations
#: checker (design giai đoạn 10: "do ops checker độc lập thực hiện").  No
#: official SHB role exists; this dedicated role is the API-layer authority for
#: verification, human waiver / not-applicable rulings, and the final
#: confirmation, alongside a case assignment for row access.
OPS_CHECKER_ROLE = "OPS_CHECKER"

#: Roles allowed to READ the ledger: any case participant plus the ops checker.
_READ_ROLES = CASE_PARTICIPANT_ROLES | {OPS_CHECKER_ROLE}

#: Decision outcomes that PERMIT opening disbursement conditions (an approval).
_PERMITTING_DECISIONS: frozenset[str] = frozenset(
    decision.value for decision in APPROVAL_DECISIONS
)

#: PROPOSED synthetic disposition-reference prefix bound to the confirmed case
#: version (no official SHB mapping).
_DISPOSITION_REF_PREFIX = "disbursement-conditions"


class CreateConditionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    condition_text_vi: str = Field(
        alias="conditionText", min_length=1, max_length=4000
    )
    owner_vi: str | None = Field(default=None, alias="owner", min_length=1, max_length=400)
    due_date: date | None = Field(default=None, alias="dueDate")


class TransitionConditionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    to_status: str = Field(alias="toStatus", min_length=1, max_length=64)
    rationale_vi: str | None = Field(
        default=None, alias="rationale", min_length=1, max_length=4000
    )
    evidence_refs: tuple[str, ...] | None = Field(default=None, alias="evidenceRefs")


class ConditionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    decision_id: UUID = Field(serialization_alias="decisionId")
    condition_text_vi: str = Field(serialization_alias="conditionText")
    owner_vi: str | None = Field(serialization_alias="owner")
    due_date: date | None = Field(serialization_alias="dueDate")
    status: str
    evidence_refs: list[str] = Field(serialization_alias="evidenceRefs")
    created_at: datetime = Field(serialization_alias="createdAt")


class ConditionsResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    conditions: list[ConditionResponse]
    case_version: int = Field(serialization_alias="caseVersion")
    confirmable: bool


class ConfirmationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    gate_type: str = Field(serialization_alias="gateType")
    status: str
    case_version: int = Field(serialization_alias="caseVersion")
    disposition_ref: str = Field(serialization_alias="dispositionRef")


Actor = Annotated[ActorContext, Depends(require_actor)]


def _require_role(actor: ActorContext, role: str) -> None:
    if role not in actor.roles:
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò được yêu cầu cho thao tác này.",
        )


def _require_reader(actor: ActorContext) -> None:
    if not (_READ_ROLES & actor.roles):
        raise ApiException(
            status_code=403,
            code="INSUFFICIENT_ROLE",
            message_vi="Bạn không có vai trò tham gia hồ sơ được yêu cầu.",
        )


def _repository(request: Request) -> ConditionLedgerRepository:
    repository = getattr(request.app.state, "condition_ledger_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="CONDITION_LEDGER_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ điều kiện giải ngân chưa sẵn sàng.",
            retryable=True,
        )
    return cast(ConditionLedgerRepository, repository)


def _credit_decision_repository(request: Request) -> CreditDecisionRepository:
    repository = getattr(request.app.state, "credit_decision_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="CREDIT_DECISION_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ quyết định tín dụng chưa sẵn sàng.",
            retryable=True,
        )
    return cast(CreditDecisionRepository, repository)


def _orchestration_repository(request: Request) -> OrchestrationRepository | None:
    repository = getattr(request.app.state, "orchestration_repository", None)
    return cast("OrchestrationRepository | None", repository)


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


def _condition_response(condition: RecordedCondition) -> ConditionResponse:
    return ConditionResponse(
        id=condition.id,
        case_id=condition.case_id,
        case_version=condition.case_version,
        decision_id=condition.decision_id,
        condition_text_vi=condition.condition_text_vi,
        owner_vi=condition.owner_vi,
        due_date=condition.due_date,
        status=condition.status.value,
        evidence_refs=list(condition.evidence_refs),
        created_at=condition.created_at,
    )


@router.post("", response_model=ConditionResponse, status_code=201)
async def create_condition(
    case_id: UUID,
    body: CreateConditionRequest,
    actor: Actor,
    request: Request,
) -> ConditionResponse:
    """Open ONE disbursement condition bound to a permitting credit decision."""

    _require_role(actor, OPS_OFFICER_ROLE)
    record = await _assert_case_access(request, actor, case_id)

    decision = await _credit_decision_repository(request).load_decision(
        case_id, record.version
    )
    if decision is None or decision.decision not in _PERMITTING_DECISIONS:
        # No approval decision authorises a disbursement condition yet: fail
        # closed.  Conditions exist only downstream of a permitting decision.
        raise ApiException(
            status_code=409,
            code="CONDITIONS_REQUIRE_APPROVAL_DECISION",
            message_vi=(
                "Chưa có quyết định phê duyệt tín dụng cho phiên bản hồ sơ hiện "
                "tại để mở điều kiện giải ngân."
            ),
        )

    try:
        condition = DisbursementCondition(
            id=uuid4(),
            case_id=case_id,
            case_version=record.version,
            decision_id=decision.id,
            condition_text_vi=body.condition_text_vi,
            owner_vi=body.owner_vi,
            due_date=body.due_date,
            status=ConditionStatus.PENDING,
        )
    except (ValidationError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_CONDITION",
            message_vi="Điều kiện giải ngân không hợp lệ.",
        ) from exc

    created = await _repository(request).create_condition(
        condition=condition, actor_id=actor.actor_id, actor_role=OPS_OFFICER_ROLE
    )
    return _condition_response(created)


@router.get("", response_model=ConditionsResponse)
async def list_conditions(
    case_id: UUID, actor: Actor, request: Request
) -> ConditionsResponse:
    _require_reader(actor)
    record = await _assert_case_access(request, actor, case_id)
    conditions = await _repository(request).list_conditions(case_id, record.version)
    return ConditionsResponse(
        conditions=[_condition_response(c) for c in conditions],
        case_version=record.version,
        confirmable=derive_conditions_confirmable(c.status for c in conditions),
    )


@router.post("/{condition_id}/transition", response_model=ConditionResponse)
async def transition_condition(
    case_id: UUID,
    condition_id: UUID,
    body: TransitionConditionRequest,
    actor: Actor,
    request: Request,
) -> ConditionResponse:
    """Move ONE condition along a validated edge under the correct authority."""

    try:
        to_status = ConditionStatus(body.to_status)
    except ValueError as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_CONDITION_STATUS",
            message_vi="Trạng thái điều kiện không hợp lệ.",
        ) from exc

    # The authority required depends on the target: VERIFIED and the human
    # waiver / not-applicable rulings are independent-checker acts; every other
    # move is an ordinary ops-officer step.
    required_role = (
        OPS_CHECKER_ROLE if to_status in CHECKER_AUTHORITY_TARGETS else OPS_OFFICER_ROLE
    )
    _require_role(actor, required_role)
    record = await _assert_case_access(request, actor, case_id)

    if to_status in RATIONALE_REQUIRED_TARGETS and not body.rationale_vi:
        # Waiver / not-applicable are human authority acts: the rationale IS the
        # authority record and is mandatory.
        raise ApiException(
            status_code=422,
            code="RATIONALE_REQUIRED",
            message_vi="Cần lý do (authority record) cho quyết định miễn trừ / không áp dụng.",
        )

    repository = _repository(request)
    current = await repository.load_condition(condition_id, case_id, record.version)
    if current is None:
        raise ApiException(
            status_code=404,
            code="CONDITION_NOT_FOUND",
            message_vi="Không tìm thấy điều kiện giải ngân trong hồ sơ này.",
        )
    if not is_transition_allowed(current.status, to_status):
        raise ApiException(
            status_code=422,
            code="FORBIDDEN_CONDITION_TRANSITION",
            message_vi="Chuyển trạng thái điều kiện không được phép.",
            details={"fromStatus": current.status.value, "toStatus": to_status.value},
        )

    try:
        updated = await repository.transition_condition(
            condition_id=condition_id,
            case_id=case_id,
            case_version=record.version,
            to_status=to_status,
            actor_id=actor.actor_id,
            actor_role=required_role,
            rationale_vi=body.rationale_vi,
            evidence_refs=body.evidence_refs,
        )
    except ConditionNotFound as exc:
        raise ApiException(
            status_code=404,
            code="CONDITION_NOT_FOUND",
            message_vi="Không tìm thấy điều kiện giải ngân trong hồ sơ này.",
        ) from exc
    except ForbiddenConditionTransition as exc:
        # Lost race: the condition moved between the pre-check and the write.
        raise ApiException(
            status_code=422,
            code="FORBIDDEN_CONDITION_TRANSITION",
            message_vi="Chuyển trạng thái điều kiện không được phép.",
        ) from exc
    return _condition_response(updated)


@router.post("/confirm", response_model=ConfirmationResponse)
async def confirm_conditions(
    case_id: UUID,
    actor: Actor,
    request: Request,
    response: Response,
) -> ConfirmationResponse:
    """Independent OPS-checker confirmation of the disbursement-condition ledger."""

    _require_role(actor, OPS_CHECKER_ROLE)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)

    conditions = await repository.list_conditions(case_id, record.version)
    if not derive_conditions_confirmable(c.status for c in conditions):
        blocking = [
            str(c.id) for c in conditions if c.status not in CONFIRMABLE_STATUSES
        ]
        raise ApiException(
            status_code=409,
            code="CONDITIONS_NOT_SATISFIED",
            message_vi=(
                "Còn điều kiện giải ngân chưa được xác minh / miễn trừ / xác định "
                "không áp dụng; không thể xác nhận."
            ),
            details={"blockingConditionIds": blocking, "empty": not conditions},
        )

    # Separation of duty: the confirming checker must not be any actor who drove
    # a VERIFIED transition (the maker/checker cannot self-confirm their own
    # verification).  PROPOSED: exclusion is scoped to VERIFIED actors per the
    # design ("confirming actor must differ from every actor who VERIFIED").
    verifying_actors = await repository.list_verifying_actor_ids(
        case_id, record.version
    )
    if actor.actor_id in verifying_actors:
        raise ApiException(
            status_code=409,
            code="SAME_ACTOR_FORBIDDEN",
            message_vi=(
                "Người xác nhận độc lập phải khác với người đã xác minh điều kiện."
            ),
            details={"actorId": str(actor.actor_id)},
        )

    orchestration = _orchestration_repository(request)
    if orchestration is None:
        raise ApiException(
            status_code=503,
            code="ORCHESTRATION_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ điều phối chưa sẵn sàng.",
            retryable=True,
        )

    disposition_ref = f"{_DISPOSITION_REF_PREFIX}:{record.version}"
    await orchestration.ensure_gate(
        case_id=case_id,
        case_version=record.version,
        gate_type=GateType.HG_DISBURSEMENT_CONDITIONS_CONFIRMED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=disposition_ref,
    )
    await repository.append_audit(
        OrchestrationAuditEvent(
            case_id=case_id,
            case_version=record.version,
            event_type="DISBURSEMENT_CONDITIONS_CONFIRMED",
            execution_id=uuid4(),
            artifact_type="DISBURSEMENT_CONDITION_LEDGER",
            artifact_id=case_id,
            event_data={
                "actorId": str(actor.actor_id),
                "caseVersion": record.version,
                "conditionCount": len(conditions),
            },
        )
    )
    await _retick_orchestration(
        request, orchestration, case_id=case_id, trigger_ref=f"HG_COND:{record.version}"
    )
    response.status_code = 200
    return ConfirmationResponse(
        gate_type=GateType.HG_DISBURSEMENT_CONDITIONS_CONFIRMED.value,
        status=GateStatus.SATISFIED.value,
        case_version=record.version,
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
    best-effort (the recovery dispatch picks up anything left).  A tick failure
    must never fail the human's already-recorded confirmation, but it is logged,
    never silent.
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
                worker_dispatcher=getattr(request.app.state, "worker_dispatcher", None),
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
            "Orchestration retick failed; the confirmation is durable and the "
            "case can be advanced manually",
            {"event": "orchestration_retick_failed", "trigger": trigger_ref},
        )
