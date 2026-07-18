"""Proposed disbursement API: dual human gates + labelled-mock execution.

Master design section 5 giai đoạn 11 ("Giải ngân vốn vay").  Credit Operations
only PREPARES the action; execution runs through a labelled deterministic mock
after TWO separate human gates satisfied by DIFFERENT actors.  Six surfaces, all
case-scoped and fail-closed (an unassigned actor gets the same indistinguishable
404 as a missing case):

- ``POST ""`` -- the ``OPS_OFFICER`` (maker) creates ONE proposed action for the
  current case version.  The action derives ONLY from an approval
  ``HumanCreditDecision`` (else 409) AND a SATISFIED
  ``HG_DISBURSEMENT_CONDITIONS_CONFIRMED`` gate for the version (else 409).  The
  amount is an EXACT decimal, currency-aware and cap-aware validated against the
  ``ApprovedTermSnapshot`` (mismatch / over-cap / malformed -> 422).  Idempotent
  on the case version.
- ``POST "/{id}/validate"`` -- an ``OPS_CHECKER`` satisfies
  ``HG_DISBURSEMENT_VALIDATED`` (gate 1) and reticks.
- ``POST "/{id}/authorize"`` -- an ``OPS_CHECKER`` satisfies
  ``HG_DISBURSEMENT_AUTHORIZED`` (gate 2) and reticks.  It requires gate 1 already
  SATISFIED (else 409 ``VALIDATION_REQUIRED``) and the authorizer MUST differ from
  the validator (else 409 ``SAME_ACTOR_FORBIDDEN``).
- ``POST "/{id}/execute"`` -- an ``OPS_CHECKER`` runs the labelled mock adapter.
  Requires BOTH gates SATISFIED (else 409 ``DISBURSEMENT_NOT_AUTHORIZED``) and the
  executor to DIFFER from the action creator (else 409 ``SAME_ACTOR_FORBIDDEN``).
  A timeout / ambiguous result records ``EXECUTION_UNKNOWN`` and is NEVER blindly
  retried -- a second execute on an unresolved action is 409
  ``RECONCILIATION_REQUIRED``; an already-executed action is 409
  ``ALREADY_EXECUTED``.
- ``POST "/{id}/reconcile"`` -- an ``OPS_CHECKER`` resolves an unresolved
  execution to ``CONFIRMED_EXECUTED`` / ``CONFIRMED_NOT_EXECUTED`` with a
  mandatory rationale; only ``CONFIRMED_NOT_EXECUTED`` re-opens a new attempt.
- ``GET ""`` -- any case participant reads the actions, their receipts, and the
  two gate statuses.

No agent path exists to any surface here.  This module is exported as ``router``
and is NOT registered in ``main.py`` (production wiring is a separate change).

All customer data in this project is synthetic and created solely for
demonstration.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from creditops.api.auth import require_actor
from creditops.api.conditions import OPS_CHECKER_ROLE
from creditops.api.errors import ApiException
from creditops.application.orchestration.kickoff import KickoffOrchestration
from creditops.application.orchestration.roles import (
    CASE_PARTICIPANT_ROLES,
    OPS_OFFICER_ROLE,
)
from creditops.application.ports.credit_decisions import CreditDecisionRepository
from creditops.application.ports.disbursements import (
    AlreadyExecutedError,
    DisbursementActionNotFound,
    DisbursementExecutionAdapter,
    DisbursementRepository,
    NotReconcilableError,
    ReconciliationRequiredError,
    RecordedDisbursementAction,
    RecordedExecutionReceipt,
)
from creditops.application.ports.orchestration import (
    OrchestrationAuditEvent,
    OrchestrationRepository,
    OrchestrationSnapshot,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.application.unit_of_work import ActorContext
from creditops.application.use_cases.dispatch_outbox import DispatchOutbox
from creditops.domain.credit_decisions import APPROVAL_DECISIONS
from creditops.domain.disbursements import (
    AmountExceedsApprovedError,
    CurrencyMismatchError,
    ExecutionStatus,
    ProposedDisbursementAction,
    parse_exact_amount,
    validate_amount_against_terms,
)
from creditops.domain.orchestration import GateStatus, GateType
from creditops.observability import log_event

router = APIRouter(
    prefix="/api/v1/cases/{case_id}/proposed-disbursements", tags=["disbursements"]
)

_logger = logging.getLogger(__name__)

#: Roles allowed to READ: any case participant plus the ops checker.
_READ_ROLES = CASE_PARTICIPANT_ROLES | {OPS_CHECKER_ROLE}

#: Decision outcomes that PERMIT a disbursement (an approval).
_PERMITTING_DECISIONS: frozenset[str] = frozenset(d.value for d in APPROVAL_DECISIONS)

_VALIDATED_REF_PREFIX = "proposed-disbursement-validated"
_AUTHORIZED_REF_PREFIX = "proposed-disbursement-authorized"


# -- request / response models ------------------------------------------------


class CreateDisbursementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    #: Optional partial amount (exact decimal string); defaults to the approved
    #: amount.  Never a float.
    amount: str | None = Field(default=None, min_length=1, max_length=40)
    currency: str | None = Field(default=None, min_length=1, max_length=8)
    beneficiary_ref_vi: str = Field(alias="beneficiaryRef", min_length=1, max_length=400)
    account_ref_vi: str = Field(alias="accountRef", min_length=1, max_length=400)


class ReconcileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    outcome: str = Field(min_length=1, max_length=64)
    rationale_vi: str = Field(alias="rationale", min_length=1, max_length=4000)


class DisbursementActionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    decision_id: UUID = Field(serialization_alias="decisionId")
    amount: str
    currency: str
    beneficiary_ref_vi: str = Field(serialization_alias="beneficiaryRef")
    account_ref_vi: str = Field(serialization_alias="accountRef")
    status: str
    created_by: UUID = Field(serialization_alias="createdBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class ExecutionReceiptResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    action_id: UUID = Field(serialization_alias="actionId")
    idempotency_key: str = Field(serialization_alias="idempotencyKey")
    adapter_label: str = Field(serialization_alias="adapterLabel")
    result_status: str = Field(serialization_alias="resultStatus")
    receipt_ref: str | None = Field(serialization_alias="receiptRef")
    recorded_by: UUID = Field(serialization_alias="recordedBy")
    created_at: datetime = Field(serialization_alias="createdAt")


class GateWriteResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    gate_type: str = Field(serialization_alias="gateType")
    status: str
    action_id: UUID = Field(serialization_alias="actionId")
    disposition_ref: str = Field(serialization_alias="dispositionRef")


class ExecutionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    action: DisbursementActionResponse
    receipt: ExecutionReceiptResponse


class DisbursementActionDetail(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    action: DisbursementActionResponse
    receipts: list[ExecutionReceiptResponse]
    validated_gate_status: str = Field(serialization_alias="validatedGateStatus")
    authorized_gate_status: str = Field(serialization_alias="authorizedGateStatus")


class DisbursementListResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    actions: list[DisbursementActionDetail]
    case_version: int = Field(serialization_alias="caseVersion")


Actor = Annotated[ActorContext, Depends(require_actor)]


# -- role / dependency helpers ------------------------------------------------


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


def _repository(request: Request) -> DisbursementRepository:
    repository = getattr(request.app.state, "disbursement_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="DISBURSEMENT_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ giải ngân chưa sẵn sàng.",
            retryable=True,
        )
    return cast(DisbursementRepository, repository)


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


def _adapter(request: Request) -> DisbursementExecutionAdapter:
    adapter = getattr(request.app.state, "disbursement_execution_adapter", None)
    if adapter is None:
        raise ApiException(
            status_code=503,
            code="DISBURSEMENT_ADAPTER_UNAVAILABLE",
            message_vi="Bộ thực thi giải ngân (mock) chưa sẵn sàng.",
            retryable=True,
        )
    return cast(DisbursementExecutionAdapter, adapter)


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


async def _load_action_or_404(
    request: Request, action_id: UUID, case_id: UUID, case_version: int
) -> RecordedDisbursementAction:
    action = await _repository(request).load_action(action_id, case_id, case_version)
    if action is None:
        raise ApiException(
            status_code=404,
            code="DISBURSEMENT_ACTION_NOT_FOUND",
            message_vi="Không tìm thấy hành động giải ngân trong phiên bản hồ sơ này.",
        )
    return action


def _gate_for(
    snapshot: OrchestrationSnapshot | None,
    gate_type: GateType,
    case_version: int,
) -> Any:
    if snapshot is None:
        return None
    for gate in snapshot.gates:
        if (
            gate.gate_type is gate_type
            and gate.case_version == case_version
            and gate.status is GateStatus.SATISFIED
        ):
            return gate
    return None


# -- create -------------------------------------------------------------------


@router.post("", response_model=DisbursementActionResponse, status_code=201)
async def create_disbursement(
    case_id: UUID,
    body: CreateDisbursementRequest,
    actor: Actor,
    request: Request,
    response: Response,
) -> DisbursementActionResponse:
    """Create ONE proposed disbursement action derived from approved terms."""

    _require_role(actor, OPS_OFFICER_ROLE)
    record = await _assert_case_access(request, actor, case_id)

    decision = await _credit_decision_repository(request).load_decision(
        case_id, record.version
    )
    if decision is None or decision.decision not in _PERMITTING_DECISIONS:
        raise ApiException(
            status_code=409,
            code="DISBURSEMENT_REQUIRES_APPROVAL_DECISION",
            message_vi=(
                "Chưa có quyết định phê duyệt tín dụng cho phiên bản hồ sơ hiện "
                "tại để đề xuất giải ngân."
            ),
        )

    # The action derives ONLY from a satisfied conditions gate for the version.
    snapshot = await _orchestration_repository(request).load_snapshot(case_id)
    if _gate_for(
        snapshot, GateType.HG_DISBURSEMENT_CONDITIONS_CONFIRMED, record.version
    ) is None:
        raise ApiException(
            status_code=409,
            code="DISBURSEMENT_CONDITIONS_NOT_CONFIRMED",
            message_vi=(
                "Chưa xác nhận điều kiện giải ngân "
                "(HG_DISBURSEMENT_CONDITIONS_CONFIRMED) cho phiên bản này."
            ),
        )

    approved_amount, approved_currency = _approved_terms(decision)
    amount = _resolve_amount(body.amount, approved_amount)
    currency = body.currency or approved_currency
    if currency is None:
        raise ApiException(
            status_code=422,
            code="CURRENCY_REQUIRED",
            message_vi="Thiếu loại tiền giải ngân và điều khoản duyệt không có loại tiền.",
        )
    try:
        validate_amount_against_terms(
            amount=amount,
            currency=currency,
            approved_amount=approved_amount,
            approved_currency=approved_currency,
        )
    except CurrencyMismatchError as exc:
        raise ApiException(
            status_code=422,
            code="CURRENCY_MISMATCH",
            message_vi="Loại tiền giải ngân khác với loại tiền đã được phê duyệt.",
        ) from exc
    except AmountExceedsApprovedError as exc:
        raise ApiException(
            status_code=422,
            code="AMOUNT_EXCEEDS_APPROVED",
            message_vi="Số tiền giải ngân vượt quá số tiền đã được phê duyệt.",
        ) from exc

    try:
        action = ProposedDisbursementAction(
            id=uuid4(),
            case_id=case_id,
            case_version=record.version,
            decision_id=decision.id,
            amount=amount,
            currency=currency,
            beneficiary_ref_vi=body.beneficiary_ref_vi,
            account_ref_vi=body.account_ref_vi,
            status=ExecutionStatus.PROPOSED,
            created_by=actor.actor_id,
        )
    except (ValidationError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_DISBURSEMENT",
            message_vi="Hành động giải ngân đề xuất không hợp lệ.",
        ) from exc

    recorded = await _repository(request).create_action(action=action)
    if not recorded.created:
        response.status_code = 200
    return _action_response(recorded)


def _approved_terms(decision: Any) -> tuple[Decimal | None, str | None]:
    """Extract the approved amount + currency from the decision's snapshot."""

    snapshot = getattr(decision, "snapshot", None)
    if snapshot is None:
        return None, None
    terms = cast("dict[str, object]", dict(snapshot.terms))
    raw_amount = terms.get("amount")
    raw_currency = terms.get("currency")
    approved_amount = Decimal(str(raw_amount)) if raw_amount is not None else None
    approved_currency = str(raw_currency) if raw_currency is not None else None
    return approved_amount, approved_currency


def _resolve_amount(raw: str | None, approved_amount: Decimal | None) -> Decimal:
    if raw is not None:
        try:
            return parse_exact_amount(raw)
        except ValueError as exc:
            raise ApiException(
                status_code=422,
                code="INVALID_AMOUNT",
                message_vi="Số tiền giải ngân không phải số thập phân hợp lệ (> 0).",
            ) from exc
    if approved_amount is None:
        raise ApiException(
            status_code=422,
            code="AMOUNT_REQUIRED",
            message_vi="Thiếu số tiền giải ngân và điều khoản duyệt không có số tiền.",
        )
    return approved_amount


# -- validate / authorize (the two separate human gates) ----------------------


@router.post("/{action_id}/validate", response_model=GateWriteResponse)
async def validate_disbursement(
    case_id: UUID,
    action_id: UUID,
    actor: Actor,
    request: Request,
) -> GateWriteResponse:
    """Satisfy ``HG_DISBURSEMENT_VALIDATED`` (gate 1) for the action."""

    _require_role(actor, OPS_CHECKER_ROLE)
    record = await _assert_case_access(request, actor, case_id)
    await _load_action_or_404(request, action_id, case_id, record.version)

    orchestration = _orchestration_repository(request)
    disposition_ref = f"{_VALIDATED_REF_PREFIX}:{action_id}"
    await orchestration.ensure_gate(
        case_id=case_id,
        case_version=record.version,
        gate_type=GateType.HG_DISBURSEMENT_VALIDATED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=disposition_ref,
    )
    await orchestration.append_audit(
        _audit(
            case_id,
            record.version,
            "DISBURSEMENT_VALIDATED",
            action_id,
            {"actorId": str(actor.actor_id)},
        )
    )
    await _retick_orchestration(
        request, orchestration, case_id=case_id, trigger_ref=f"HG_DISB_V:{action_id}"
    )
    return GateWriteResponse(
        gate_type=GateType.HG_DISBURSEMENT_VALIDATED.value,
        status=GateStatus.SATISFIED.value,
        action_id=action_id,
        disposition_ref=disposition_ref,
    )


@router.post("/{action_id}/authorize", response_model=GateWriteResponse)
async def authorize_disbursement(
    case_id: UUID,
    action_id: UUID,
    actor: Actor,
    request: Request,
) -> GateWriteResponse:
    """Satisfy ``HG_DISBURSEMENT_AUTHORIZED`` (gate 2) -- a DIFFERENT actor."""

    _require_role(actor, OPS_CHECKER_ROLE)
    record = await _assert_case_access(request, actor, case_id)
    await _load_action_or_404(request, action_id, case_id, record.version)

    orchestration = _orchestration_repository(request)
    snapshot = await orchestration.load_snapshot(case_id)
    validated = _gate_for(
        snapshot, GateType.HG_DISBURSEMENT_VALIDATED, record.version
    )
    if validated is None:
        raise ApiException(
            status_code=409,
            code="VALIDATION_REQUIRED",
            message_vi=(
                "Chưa thể phê chuẩn: cổng HG_DISBURSEMENT_VALIDATED chưa được "
                "thỏa mãn trước."
            ),
        )
    # Maker-checker: the authorizer must DIFFER from the validator.
    if validated.satisfied_by_actor_id == actor.actor_id:
        raise ApiException(
            status_code=409,
            code="SAME_ACTOR_FORBIDDEN",
            message_vi=(
                "Người phê chuẩn phải khác với người đã xác nhận (tách biệt "
                "nhiệm vụ)."
            ),
        )

    disposition_ref = f"{_AUTHORIZED_REF_PREFIX}:{action_id}"
    await orchestration.ensure_gate(
        case_id=case_id,
        case_version=record.version,
        gate_type=GateType.HG_DISBURSEMENT_AUTHORIZED,
        status=GateStatus.SATISFIED,
        satisfied_by_actor_id=actor.actor_id,
        disposition_ref=disposition_ref,
    )
    await orchestration.append_audit(
        _audit(
            case_id,
            record.version,
            "DISBURSEMENT_AUTHORIZED",
            action_id,
            {"actorId": str(actor.actor_id)},
        )
    )
    await _retick_orchestration(
        request, orchestration, case_id=case_id, trigger_ref=f"HG_DISB_A:{action_id}"
    )
    return GateWriteResponse(
        gate_type=GateType.HG_DISBURSEMENT_AUTHORIZED.value,
        status=GateStatus.SATISFIED.value,
        action_id=action_id,
        disposition_ref=disposition_ref,
    )


# -- execute (the labelled mock adapter, after BOTH gates) --------------------


@router.post("/{action_id}/execute", response_model=ExecutionResponse)
async def execute_disbursement(
    case_id: UUID,
    action_id: UUID,
    actor: Actor,
    request: Request,
) -> ExecutionResponse:
    """Run the labelled mock adapter after BOTH gates; different-from-creator."""

    _require_role(actor, OPS_CHECKER_ROLE)
    record = await _assert_case_access(request, actor, case_id)
    action = await _load_action_or_404(request, action_id, case_id, record.version)

    snapshot = await _orchestration_repository(request).load_snapshot(case_id)
    both_satisfied = _gate_for(
        snapshot, GateType.HG_DISBURSEMENT_VALIDATED, record.version
    ) is not None and _gate_for(
        snapshot, GateType.HG_DISBURSEMENT_AUTHORIZED, record.version
    ) is not None
    if not both_satisfied:
        raise ApiException(
            status_code=409,
            code="DISBURSEMENT_NOT_AUTHORIZED",
            message_vi=(
                "Chưa thể giải ngân: cần cả hai cổng HG_DISBURSEMENT_VALIDATED và "
                "HG_DISBURSEMENT_AUTHORIZED được thỏa mãn."
            ),
        )
    # The executor must DIFFER from the maker who created the action.
    if action.created_by == actor.actor_id:
        raise ApiException(
            status_code=409,
            code="SAME_ACTOR_FORBIDDEN",
            message_vi=(
                "Người thực thi giải ngân phải khác với người tạo hành động "
                "(tách biệt nhiệm vụ)."
            ),
        )

    try:
        recorded_action, receipt = await _repository(request).execute_action(
            action_id=action_id,
            case_id=case_id,
            case_version=record.version,
            adapter=_adapter(request),
            idempotency_key=uuid4().hex,
            actor_id=actor.actor_id,
            actor_role=OPS_CHECKER_ROLE,
        )
    except ReconciliationRequiredError as exc:
        raise ApiException(
            status_code=409,
            code="RECONCILIATION_REQUIRED",
            message_vi=(
                "Lần thực thi trước chưa xác định (EXECUTION_UNKNOWN); cần đối "
                "soát thủ công, không tự động thực thi lại."
            ),
        ) from exc
    except AlreadyExecutedError as exc:
        raise ApiException(
            status_code=409,
            code="ALREADY_EXECUTED",
            message_vi="Hành động giải ngân đã được xác nhận thực thi.",
        ) from exc
    except DisbursementActionNotFound as exc:
        raise ApiException(
            status_code=404,
            code="DISBURSEMENT_ACTION_NOT_FOUND",
            message_vi="Không tìm thấy hành động giải ngân trong phiên bản hồ sơ này.",
        ) from exc
    return ExecutionResponse(
        action=_action_response(recorded_action),
        receipt=_receipt_response(receipt),
    )


# -- reconcile ----------------------------------------------------------------


@router.post("/{action_id}/reconcile", response_model=DisbursementActionResponse)
async def reconcile_disbursement(
    case_id: UUID,
    action_id: UUID,
    body: ReconcileRequest,
    actor: Actor,
    request: Request,
) -> DisbursementActionResponse:
    """Human resolution of an unresolved execution (with a mandatory rationale)."""

    _require_role(actor, OPS_CHECKER_ROLE)
    record = await _assert_case_access(request, actor, case_id)
    await _load_action_or_404(request, action_id, case_id, record.version)

    try:
        outcome = ExecutionStatus(body.outcome)
    except ValueError as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_OUTCOME",
            message_vi="Kết quả đối soát không hợp lệ.",
        ) from exc
    if outcome not in {
        ExecutionStatus.CONFIRMED_EXECUTED,
        ExecutionStatus.CONFIRMED_NOT_EXECUTED,
    }:
        raise ApiException(
            status_code=422,
            code="INVALID_OUTCOME",
            message_vi=(
                "Kết quả đối soát phải là CONFIRMED_EXECUTED hoặc "
                "CONFIRMED_NOT_EXECUTED."
            ),
        )

    try:
        recorded = await _repository(request).reconcile_action(
            action_id=action_id,
            case_id=case_id,
            case_version=record.version,
            outcome=outcome,
            rationale_vi=body.rationale_vi,
            actor_id=actor.actor_id,
            actor_role=OPS_CHECKER_ROLE,
        )
    except NotReconcilableError as exc:
        raise ApiException(
            status_code=409,
            code="NOT_RECONCILABLE",
            message_vi=(
                "Hành động không ở trạng thái cần đối soát "
                "(chỉ EXECUTION_REQUESTED / EXECUTION_UNKNOWN)."
            ),
        ) from exc
    except DisbursementActionNotFound as exc:
        raise ApiException(
            status_code=404,
            code="DISBURSEMENT_ACTION_NOT_FOUND",
            message_vi="Không tìm thấy hành động giải ngân trong phiên bản hồ sơ này.",
        ) from exc
    return _action_response(recorded)


# -- list ---------------------------------------------------------------------


@router.get("", response_model=DisbursementListResponse)
async def list_disbursements(
    case_id: UUID, actor: Actor, request: Request
) -> DisbursementListResponse:
    _require_reader(actor)
    record = await _assert_case_access(request, actor, case_id)
    repository = _repository(request)
    actions = await repository.list_actions(case_id)
    snapshot = await _orchestration_repository(request).load_snapshot(case_id)

    details: list[DisbursementActionDetail] = []
    for action in actions:
        receipts = await repository.list_receipts(action.id)
        validated = _gate_for(
            snapshot, GateType.HG_DISBURSEMENT_VALIDATED, action.case_version
        )
        authorized = _gate_for(
            snapshot, GateType.HG_DISBURSEMENT_AUTHORIZED, action.case_version
        )
        details.append(
            DisbursementActionDetail(
                action=_action_response(action),
                receipts=[_receipt_response(r) for r in receipts],
                validated_gate_status=(
                    GateStatus.SATISFIED.value
                    if validated is not None
                    else GateStatus.OPEN.value
                ),
                authorized_gate_status=(
                    GateStatus.SATISFIED.value
                    if authorized is not None
                    else GateStatus.OPEN.value
                ),
            )
        )
    return DisbursementListResponse(actions=details, case_version=record.version)


# -- shared helpers -----------------------------------------------------------


def _audit(
    case_id: UUID,
    case_version: int,
    event_type: str,
    action_id: UUID,
    event_data: dict[str, object],
) -> OrchestrationAuditEvent:
    return OrchestrationAuditEvent(
        case_id=case_id,
        case_version=case_version,
        event_type=event_type,
        execution_id=uuid4(),
        artifact_type="PROPOSED_DISBURSEMENT_ACTION",
        artifact_id=action_id,
        event_data=event_data,
    )


def _action_response(
    action: RecordedDisbursementAction,
) -> DisbursementActionResponse:
    return DisbursementActionResponse(
        id=action.id,
        case_id=action.case_id,
        case_version=action.case_version,
        decision_id=action.decision_id,
        amount=action.amount_text,
        currency=action.currency,
        beneficiary_ref_vi=action.beneficiary_ref_vi,
        account_ref_vi=action.account_ref_vi,
        status=action.status.value,
        created_by=action.created_by,
        created_at=action.created_at,
    )


def _receipt_response(
    receipt: RecordedExecutionReceipt,
) -> ExecutionReceiptResponse:
    return ExecutionReceiptResponse(
        id=receipt.id,
        action_id=receipt.action_id,
        idempotency_key=receipt.idempotency_key,
        adapter_label=receipt.adapter_label,
        result_status=receipt.result_status.value,
        receipt_ref=receipt.receipt_ref,
        recorded_by=receipt.recorded_by,
        created_at=receipt.created_at,
    )


async def _retick_orchestration(
    request: Request,
    orchestration_repository: Any,
    *,
    case_id: UUID,
    trigger_ref: str,
) -> None:
    """Self-fire an idempotent orchestration tick after a gate satisfaction.

    Mirrors ``api/conditions.py``: the plan task + outbox event commit durably,
    the queue publish is best-effort, and a tick failure never fails the human's
    already-recorded gate write -- but it is logged, never silent.
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
            "Orchestration retick after disbursement gate satisfaction",
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
            "Orchestration retick failed; the gate write is durable and the case "
            "can be advanced manually",
            {"event": "orchestration_retick_failed", "trigger": trigger_ref},
        )
