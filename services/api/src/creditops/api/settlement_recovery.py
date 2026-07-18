"""Stage-14 settlement (14A) + recovery-preparation (14B) API.

Master design section 5 giai đoạn 14.  Two mutually exclusive post-repayment
branches, both case-scoped and fail-closed (an unassigned actor gets the same
indistinguishable 404 as a missing case):

- POST ``/settlement/check`` -- the ``OPS_CHECKER`` submits the ledger snapshot
  inputs; the server DERIVES eligibility (``derive_settlement_eligible``: zero
  outstanding balance AND zero open exceptions).  Ineligible -> 409
  ``SETTLEMENT_NOT_ELIGIBLE`` with the derived details; eligible -> the settlement
  check is recorded (201).
- POST ``/settlement/confirm`` -- the ``OPS_CHECKER`` confirms.  It fails closed
  unless an eligible (zero-balance) check exists for the version (409
  ``SETTLEMENT_NOT_ELIGIBLE``), then writes the LABELLED MOCK closure / release
  receipts, satisfies ``HG_SETTLEMENT_CONFIRMED`` through the orchestration
  repository, audits, and re-ticks.
- POST ``/recovery`` -- the ``OPS_CHECKER`` opens a recovery case.  It requires
  BOTH the deterministic trigger (``derive_recovery_trigger``: a supplied
  snapshot showing sustained shortfall) to be True (else 409
  ``RECOVERY_NOT_TRIGGERED``) AND an explicit human escalation rationale (a
  mandatory request field; 422 if blank).  Never opened from a model score.
- POST ``/recovery/{id}/approve-strategy`` -- a DIFFERENT human authority (not
  the escalator; 409 ``SAME_ACTOR_FORBIDDEN``) approves the recovery strategy,
  moving the case ``PREPARING -> STRATEGY_APPROVED``, satisfying
  ``HG_RECOVERY_STRATEGY_APPROVED``, auditing and re-ticking.  Separate gates for
  future security / legal / write-off actions are OUT OF SCOPE.
- GET ``/settlement`` / GET ``/recovery`` -- any case participant reads.

Real closure, registry release, enforcement, litigation and write-off are OUT OF
SCOPE.  This module is exported as ``router`` and is NOT registered in
``main.py`` here; production wiring is a separate change (tests include the
router directly).

All customer data in this project is synthetic and created solely for
demonstration.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.orchestration.roles import CASE_PARTICIPANT_ROLES
from creditops.application.ports.orchestration import (
    OrchestrationAuditEvent,
    OrchestrationRepository,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.application.ports.settlement_recovery import (
    RecordedRecoveryCase,
    RecordedSettlementCheck,
    RecordedSettlementReceipt,
    RecoveryCaseNotFound,
    RecoveryStrategyConflict,
    SettlementRecoveryRepository,
)
from creditops.application.unit_of_work import ActorContext
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.domain.orchestration import GateStatus, GateType
from creditops.domain.settlement_recovery import (
    MOCK_SETTLEMENT_RECEIPTS,
    RecoveryCase,
    RecoveryOption,
    RecoveryStatus,
    RecoveryTriggerInputs,
    SettlementCheck,
    SettlementLedgerInputs,
    SettlementReceiptKind,
    derive_recovery_trigger,
    derive_settlement_eligible,
)
from creditops.observability import log_event

router = APIRouter(prefix="/api/v1/cases/{case_id}", tags=["settlement-recovery"])

_logger = logging.getLogger(__name__)

#: PROPOSED synthetic JWT authority role for the independent settlement /
#: recovery authority (design giai đoạn 14: "Settlement/recovery/legal
#: authority").  No official SHB role exists; this dedicated role is the
#: API-layer authority for recording the settlement check, confirming settlement,
#: opening a recovery case, and approving a recovery strategy.  Row access is
#: still enforced by the case-assignment filter.
OPS_CHECKER_ROLE = "OPS_CHECKER"

#: Roles allowed to READ the settlement / recovery surfaces.
_READ_ROLES = CASE_PARTICIPANT_ROLES | {OPS_CHECKER_ROLE}

#: PROPOSED synthetic default notes for the two LABELLED MOCK receipts.
_MOCK_RECEIPT_NOTES: dict[SettlementReceiptKind, str] = {
    SettlementReceiptKind.MOCK_CLOSURE: "Tất toán khoản vay (chứng từ mô phỏng).",
    SettlementReceiptKind.MOCK_RELEASE: (
        "Giải chấp và xóa đăng ký biện pháp bảo đảm (chứng từ mô phỏng)."
    ),
}


# -- request / response models ------------------------------------------------


class SettlementCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    outstanding_principal: str = Field(alias="outstandingPrincipal", max_length=64)
    outstanding_interest: str = Field(alias="outstandingInterest", max_length=64)
    outstanding_fees: str = Field(alias="outstandingFees", max_length=64)
    open_exception_count: int = Field(alias="openExceptionCount", ge=0)


class RecoveryOptionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label_vi: str = Field(alias="label", min_length=1, max_length=400)
    description_vi: str = Field(alias="description", min_length=1, max_length=4000)
    consequences_vi: str = Field(alias="consequences", min_length=1, max_length=4000)
    dependencies_vi: str | None = Field(
        default=None, alias="dependencies", min_length=1, max_length=4000
    )


class OpenRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    outstanding_total: str = Field(alias="outstandingTotal", max_length=64)
    periods_in_shortfall: int = Field(alias="periodsInShortfall", ge=0)
    trigger_summary_vi: str = Field(alias="triggerSummary", min_length=1, max_length=4000)
    escalation_rationale_vi: str = Field(
        alias="escalationRationale", min_length=1, max_length=4000
    )
    evidence_refs: tuple[str, ...] = Field(alias="evidenceRefs", min_length=1)
    options: tuple[RecoveryOptionRequest, ...] = Field(min_length=1)


class SettlementCheckResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    outstanding_principal: str = Field(serialization_alias="outstandingPrincipal")
    outstanding_interest: str = Field(serialization_alias="outstandingInterest")
    outstanding_fees: str = Field(serialization_alias="outstandingFees")
    open_exception_count: int = Field(serialization_alias="openExceptionCount")
    zero_balance_confirmed: bool = Field(serialization_alias="zeroBalanceConfirmed")
    created_at: datetime = Field(serialization_alias="createdAt")


class SettlementReceiptResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    settlement_check_id: UUID = Field(serialization_alias="settlementCheckId")
    kind: str
    note_vi: str | None = Field(serialization_alias="note")
    created_at: datetime = Field(serialization_alias="createdAt")


class SettlementViewResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    checks: list[SettlementCheckResponse]
    receipts: list[SettlementReceiptResponse]
    case_version: int = Field(serialization_alias="caseVersion")
    confirmable: bool


class SettlementConfirmationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    gate_type: str = Field(serialization_alias="gateType")
    status: str
    case_version: int = Field(serialization_alias="caseVersion")
    disposition_ref: str = Field(serialization_alias="dispositionRef")
    receipts: list[SettlementReceiptResponse]


class RecoveryOptionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    label_vi: str = Field(serialization_alias="label")
    description_vi: str = Field(serialization_alias="description")
    consequences_vi: str = Field(serialization_alias="consequences")
    dependencies_vi: str | None = Field(serialization_alias="dependencies")


class RecoveryCaseResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    trigger_summary_vi: str = Field(serialization_alias="triggerSummary")
    escalated_by: UUID = Field(serialization_alias="escalatedBy")
    escalation_rationale_vi: str = Field(serialization_alias="escalationRationale")
    status: str
    evidence_refs: list[str] = Field(serialization_alias="evidenceRefs")
    options: list[RecoveryOptionResponse]
    approved_by: UUID | None = Field(serialization_alias="approvedBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class RecoveryCasesResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    recovery_cases: list[RecoveryCaseResponse] = Field(serialization_alias="recoveryCases")
    case_version: int = Field(serialization_alias="caseVersion")


class RecoveryApprovalResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    gate_type: str = Field(serialization_alias="gateType")
    status: str
    case_version: int = Field(serialization_alias="caseVersion")
    disposition_ref: str = Field(serialization_alias="dispositionRef")
    recovery_case: RecoveryCaseResponse = Field(serialization_alias="recoveryCase")


Actor = Annotated[ActorContext, Depends(require_actor)]


# -- dependencies / helpers ---------------------------------------------------


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


def _repository(request: Request) -> SettlementRecoveryRepository:
    repository = getattr(request.app.state, "settlement_recovery_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="SETTLEMENT_RECOVERY_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ tất toán / xử lý nợ chưa sẵn sàng.",
            retryable=True,
        )
    return cast(SettlementRecoveryRepository, repository)


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


def _check_response(check: RecordedSettlementCheck) -> SettlementCheckResponse:
    return SettlementCheckResponse(
        id=check.id,
        case_id=check.case_id,
        case_version=check.case_version,
        outstanding_principal=check.outstanding_principal,
        outstanding_interest=check.outstanding_interest,
        outstanding_fees=check.outstanding_fees,
        open_exception_count=check.open_exception_count,
        zero_balance_confirmed=check.zero_balance_confirmed,
        created_at=check.created_at,
    )


def _receipt_response(receipt: RecordedSettlementReceipt) -> SettlementReceiptResponse:
    return SettlementReceiptResponse(
        id=receipt.id,
        settlement_check_id=receipt.settlement_check_id,
        kind=receipt.kind.value,
        note_vi=receipt.note_vi,
        created_at=receipt.created_at,
    )


def _recovery_response(recovery: RecordedRecoveryCase) -> RecoveryCaseResponse:
    return RecoveryCaseResponse(
        id=recovery.id,
        case_id=recovery.case_id,
        case_version=recovery.case_version,
        trigger_summary_vi=recovery.trigger_summary_vi,
        escalated_by=recovery.escalated_by,
        escalation_rationale_vi=recovery.escalation_rationale_vi,
        status=recovery.status.value,
        evidence_refs=list(recovery.evidence_refs),
        options=[
            RecoveryOptionResponse(
                label_vi=option.label_vi,
                description_vi=option.description_vi,
                consequences_vi=option.consequences_vi,
                dependencies_vi=option.dependencies_vi,
            )
            for option in recovery.options
        ],
        approved_by=recovery.approved_by,
        created_at=recovery.created_at,
    )


# -- settlement (14A) ---------------------------------------------------------


@router.post("/settlement/check", response_model=SettlementCheckResponse, status_code=201)
async def create_settlement_check(
    case_id: UUID,
    body: SettlementCheckRequest,
    actor: Actor,
    request: Request,
) -> SettlementCheckResponse:
    """Record ONE settlement ledger check; ineligible snapshots are rejected."""

    _require_role(actor, OPS_CHECKER_ROLE)
    record = await _assert_case_access(request, actor, case_id)

    try:
        inputs = SettlementLedgerInputs(
            outstanding_principal=body.outstanding_principal,
            outstanding_interest=body.outstanding_interest,
            outstanding_fees=body.outstanding_fees,
            open_exception_count=body.open_exception_count,
        )
    except (ValidationError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_SETTLEMENT_SNAPSHOT",
            message_vi="Số liệu ledger tất toán không hợp lệ.",
        ) from exc

    eligibility = derive_settlement_eligible(inputs)
    if not eligibility.eligible:
        # Fail closed: settlement opens ONLY on a zero balance with no open
        # exceptions.  The derived details say exactly why.
        raise ApiException(
            status_code=409,
            code="SETTLEMENT_NOT_ELIGIBLE",
            message_vi=(
                "Chưa đủ điều kiện tất toán: còn dư nợ hoặc còn exception "
                "chưa xử lý."
            ),
            details={
                "zeroBalance": eligibility.zero_balance,
                "outstandingPrincipal": eligibility.outstanding_principal,
                "outstandingInterest": eligibility.outstanding_interest,
                "outstandingFees": eligibility.outstanding_fees,
                "openExceptionCount": eligibility.open_exception_count,
            },
        )

    try:
        check = SettlementCheck(
            id=uuid4(),
            case_id=case_id,
            case_version=record.version,
            outstanding_principal=eligibility.outstanding_principal,
            outstanding_interest=eligibility.outstanding_interest,
            outstanding_fees=eligibility.outstanding_fees,
            open_exception_count=eligibility.open_exception_count,
            zero_balance_confirmed=eligibility.zero_balance,
            recorded_by=actor.actor_id,
        )
    except (ValidationError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_SETTLEMENT_SNAPSHOT",
            message_vi="Số liệu ledger tất toán không hợp lệ.",
        ) from exc

    recorded = await _repository(request).record_settlement_check(
        check=check, actor_id=actor.actor_id, actor_role=OPS_CHECKER_ROLE
    )
    return _check_response(recorded)


@router.post("/settlement/confirm", response_model=SettlementConfirmationResponse)
async def confirm_settlement(
    case_id: UUID,
    actor: Actor,
    request: Request,
    response: Response,
) -> SettlementConfirmationResponse:
    """Confirm settlement: write the MOCK receipts and satisfy the gate."""

    _require_role(actor, OPS_CHECKER_ROLE)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)

    check = await repository.load_latest_settlement_check(case_id, record.version)
    if check is None or not check.zero_balance_confirmed:
        raise ApiException(
            status_code=409,
            code="SETTLEMENT_NOT_ELIGIBLE",
            message_vi=(
                "Chưa có settlement check đủ điều kiện (zero balance) cho phiên "
                "bản hồ sơ hiện tại."
            ),
            details={"hasEligibleCheck": check is not None and check.zero_balance_confirmed},
        )

    orchestration = _orchestration_repository(request)

    receipts = await repository.record_settlement_receipts(
        settlement_check_id=check.id,
        case_id=case_id,
        case_version=record.version,
        receipts=[(kind, _MOCK_RECEIPT_NOTES[kind]) for kind in MOCK_SETTLEMENT_RECEIPTS],
        actor_id=actor.actor_id,
        actor_role=OPS_CHECKER_ROLE,
    )

    disposition_ref = f"settlement:{record.version}"
    await orchestration.ensure_gate(
        case_id=case_id,
        case_version=record.version,
        gate_type=GateType.HG_SETTLEMENT_CONFIRMED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=disposition_ref,
    )
    await repository.append_audit(
        OrchestrationAuditEvent(
            case_id=case_id,
            case_version=record.version,
            event_type="SETTLEMENT_CONFIRMED",
            execution_id=uuid4(),
            artifact_type="SETTLEMENT_CHECK",
            artifact_id=check.id,
            event_data={
                "actorId": str(actor.actor_id),
                "caseVersion": record.version,
                "settlementCheckId": str(check.id),
                "receiptKinds": [r.kind.value for r in receipts],
            },
        )
    )
    await _retick_orchestration(
        request, orchestration, case_id=case_id, trigger_ref=f"HG_SETTLE:{record.version}"
    )
    response.status_code = 200
    return SettlementConfirmationResponse(
        gate_type=GateType.HG_SETTLEMENT_CONFIRMED.value,
        status=GateStatus.SATISFIED.value,
        case_version=record.version,
        disposition_ref=disposition_ref,
        receipts=[_receipt_response(r) for r in receipts],
    )


@router.get("/settlement", response_model=SettlementViewResponse)
async def get_settlement(
    case_id: UUID, actor: Actor, request: Request
) -> SettlementViewResponse:
    _require_reader(actor)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    checks = await repository.list_settlement_checks(case_id, record.version)
    latest = checks[0] if checks else None
    receipts = (
        await repository.list_settlement_receipts(latest.id) if latest is not None else ()
    )
    return SettlementViewResponse(
        checks=[_check_response(c) for c in checks],
        receipts=[_receipt_response(r) for r in receipts],
        case_version=record.version,
        confirmable=latest is not None and latest.zero_balance_confirmed,
    )


# -- recovery (14B) -----------------------------------------------------------


@router.post("/recovery", response_model=RecoveryCaseResponse, status_code=201)
async def open_recovery(
    case_id: UUID,
    body: OpenRecoveryRequest,
    actor: Actor,
    request: Request,
) -> RecoveryCaseResponse:
    """Open ONE recovery case from a deterministic trigger + human escalation."""

    _require_role(actor, OPS_CHECKER_ROLE)
    record = await _assert_case_access(request, actor, case_id)

    try:
        inputs = RecoveryTriggerInputs(
            outstanding_total=body.outstanding_total,
            periods_in_shortfall=body.periods_in_shortfall,
        )
    except (ValidationError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_RECOVERY_SNAPSHOT",
            message_vi="Số liệu ledger cho trigger xử lý nợ không hợp lệ.",
        ) from exc

    assessment = derive_recovery_trigger(inputs)
    if not assessment.triggered:
        # Fail closed: recovery opens ONLY from a deterministic sustained-shortfall
        # trigger PLUS human escalation -- never a model score, never one late
        # period.
        raise ApiException(
            status_code=409,
            code="RECOVERY_NOT_TRIGGERED",
            message_vi=(
                "Chưa đủ điều kiện mở hồ sơ xử lý nợ: shortfall chưa kéo dài đủ "
                "số kỳ theo quy tắc."
            ),
            details={
                "outstandingTotal": assessment.outstanding_total,
                "periodsInShortfall": assessment.periods_in_shortfall,
                "thresholdPeriods": assessment.threshold_periods,
            },
        )

    try:
        recovery = RecoveryCase(
            id=uuid4(),
            case_id=case_id,
            case_version=record.version,
            trigger_summary_vi=body.trigger_summary_vi,
            escalated_by=actor.actor_id,
            escalation_rationale_vi=body.escalation_rationale_vi,
            status=RecoveryStatus.PREPARING,
            evidence_refs=body.evidence_refs,
            options=tuple(
                RecoveryOption(
                    label_vi=option.label_vi,
                    description_vi=option.description_vi,
                    consequences_vi=option.consequences_vi,
                    dependencies_vi=option.dependencies_vi,
                )
                for option in body.options
            ),
        )
    except (ValidationError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_RECOVERY_CASE",
            message_vi="Hồ sơ xử lý nợ không hợp lệ.",
        ) from exc

    recorded = await _repository(request).record_recovery_case(
        recovery=recovery, actor_id=actor.actor_id, actor_role=OPS_CHECKER_ROLE
    )
    return _recovery_response(recorded)


@router.get("/recovery", response_model=RecoveryCasesResponse)
async def list_recovery(
    case_id: UUID, actor: Actor, request: Request
) -> RecoveryCasesResponse:
    _require_reader(actor)
    record = await _assert_case_access(request, actor, case_id)
    cases = await _repository(request).list_recovery_cases(case_id, record.version)
    return RecoveryCasesResponse(
        recovery_cases=[_recovery_response(c) for c in cases],
        case_version=record.version,
    )


@router.post(
    "/recovery/{recovery_id}/approve-strategy", response_model=RecoveryApprovalResponse
)
async def approve_recovery_strategy(
    case_id: UUID,
    recovery_id: UUID,
    actor: Actor,
    request: Request,
    response: Response,
) -> RecoveryApprovalResponse:
    """A DIFFERENT human authority approves the recovery strategy + the gate."""

    _require_role(actor, OPS_CHECKER_ROLE)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)

    current = await repository.load_recovery_case(recovery_id, case_id, record.version)
    if current is None:
        raise ApiException(
            status_code=404,
            code="RECOVERY_CASE_NOT_FOUND",
            message_vi="Không tìm thấy hồ sơ xử lý nợ trong hồ sơ này.",
        )
    if current.status is not RecoveryStatus.PREPARING:
        raise ApiException(
            status_code=409,
            code="RECOVERY_ALREADY_APPROVED",
            message_vi="Chiến lược xử lý nợ đã được phê duyệt trước đó.",
            details={"status": current.status.value},
        )
    if current.escalated_by == actor.actor_id:
        # Separation of duty: the strategy approver must differ from the escalator.
        raise ApiException(
            status_code=409,
            code="SAME_ACTOR_FORBIDDEN",
            message_vi=(
                "Người phê duyệt chiến lược phải khác với người đã escalate hồ sơ."
            ),
            details={"actorId": str(actor.actor_id)},
        )

    orchestration = _orchestration_repository(request)

    try:
        approved = await repository.approve_recovery_strategy(
            recovery_id=recovery_id,
            case_id=case_id,
            case_version=record.version,
            approved_by=actor.actor_id,
            actor_role=OPS_CHECKER_ROLE,
        )
    except RecoveryCaseNotFound as exc:
        raise ApiException(
            status_code=404,
            code="RECOVERY_CASE_NOT_FOUND",
            message_vi="Không tìm thấy hồ sơ xử lý nợ trong hồ sơ này.",
        ) from exc
    except RecoveryStrategyConflict as exc:
        # Lost race: the case was approved between the pre-check and the write.
        raise ApiException(
            status_code=409,
            code="RECOVERY_ALREADY_APPROVED",
            message_vi="Chiến lược xử lý nợ đã được phê duyệt trước đó.",
        ) from exc

    disposition_ref = f"recovery-strategy:{recovery_id}"
    await orchestration.ensure_gate(
        case_id=case_id,
        case_version=record.version,
        gate_type=GateType.HG_RECOVERY_STRATEGY_APPROVED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=disposition_ref,
    )
    await repository.append_audit(
        OrchestrationAuditEvent(
            case_id=case_id,
            case_version=record.version,
            event_type="RECOVERY_STRATEGY_APPROVED",
            execution_id=uuid4(),
            artifact_type="RECOVERY_CASE",
            artifact_id=recovery_id,
            event_data={
                "actorId": str(actor.actor_id),
                "caseVersion": record.version,
                "recoveryCaseId": str(recovery_id),
            },
        )
    )
    await _retick_orchestration(
        request, orchestration, case_id=case_id, trigger_ref=f"HG_RECOVERY:{recovery_id}"
    )
    response.status_code = 200
    return RecoveryApprovalResponse(
        gate_type=GateType.HG_RECOVERY_STRATEGY_APPROVED.value,
        status=GateStatus.SATISFIED.value,
        case_version=record.version,
        disposition_ref=disposition_ref,
        recovery_case=_recovery_response(approved),
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
