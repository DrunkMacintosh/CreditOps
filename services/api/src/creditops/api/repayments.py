"""RepaymentLedger API: deterministic ledger reads + human-only append surfaces.

Master design section 5 giai đoạn 13 ("Thu nợ gốc, lãi và phí").  The bank
collects on schedule; a collections officer surfaces exceptions from the
deterministic ledger and PROPOSES contact / control actions -- but executes
NOTHING.  Four case-scoped, fail-closed surfaces (an unassigned actor gets the
same indistinguishable 404 as a missing case):

- POST ``/repayments`` -- the ``OPS_OFFICER`` opens ONE disbursed facility.  A
  facility may only be opened once a PERMITTING human credit decision exists for
  the current case version (an approval), loaded through the credit-decision
  repository.  The facility binds that decision as its source.
- POST ``/repayments/{facility_id}/events`` -- the collections/operations officer
  appends ONE payment or reversal.  IDEMPOTENT on ``externalReference``: a
  duplicate delivery returns the EXISTING row (200) with no second effect; a new
  event is 201.  A reversal must reference a PAYMENT of the same facility.
- GET ``/repayments/{facility_id}/ledger`` -- any case participant reads the
  RECOMPUTED snapshot: per-period allocation + outstanding + the deterministic
  collections-exception surface, plus the human collection notes.  The ledger
  state is NEVER stored; it is folded on demand as of the ``asOf`` date.
- POST ``/repayments/{facility_id}/notes`` -- the collections officer records ONE
  human FREE-TEXT observation / proposed action.  A PROPOSAL only: no cash-flow
  control, limit freeze, security demand or restructuring is executed anywhere.

PROPOSED / SYNTHETIC AUTHORITY: no official SHB collections role exists.  The
collections/operations officer is mapped to the existing ``OPS_OFFICER`` role
here (documented PROPOSED); reconfigure when an official source exists.

This module is exported as ``router`` and is NOT registered in ``main.py`` here;
production wiring is a separate change (tests include the router directly).

All customer data in this project is synthetic and created solely for
demonstration.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from creditops.api.auth import require_actor
from creditops.api.errors import ApiException
from creditops.application.orchestration.roles import (
    CASE_PARTICIPANT_ROLES,
    OPS_OFFICER_ROLE,
)
from creditops.application.ports.credit_decisions import CreditDecisionRepository
from creditops.application.ports.repayments import (
    RecordedCollectionNote,
    RecordedFacility,
    RepaymentLedgerRepository,
)
from creditops.application.ports.repositories import CaseRecord
from creditops.application.underwriting.calculators import RepaymentStyle
from creditops.application.unit_of_work import ActorContext
from creditops.domain.credit_decisions import APPROVAL_DECISIONS
from creditops.domain.repayments import (
    CollectionsException,
    EventKind,
    Facility,
    LedgerPeriod,
    LedgerSnapshot,
    RepaymentEvent,
    RepaymentLedgerError,
    apply_events,
)

router = APIRouter(
    prefix="/api/v1/cases/{case_id}/repayments", tags=["repayments"]
)

#: PROPOSED synthetic authority: the collections / operations officer analog is
#: the existing OPS_OFFICER role (no official SHB collections role exists).
COLLECTIONS_OFFICER_ROLE = OPS_OFFICER_ROLE

#: Roles allowed to READ the ledger: any case participant.
_READ_ROLES = CASE_PARTICIPANT_ROLES

#: Decision outcomes that PERMIT opening a facility (an approval).
_PERMITTING_DECISIONS: frozenset[str] = frozenset(
    decision.value for decision in APPROVAL_DECISIONS
)

_NOTE_KINDS: frozenset[str] = frozenset({"OBSERVATION", "PROPOSED_ACTION"})


# -- request / response models ------------------------------------------------


class CreateFacilityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    principal: str = Field(min_length=1, max_length=64)
    annual_rate_percent: str = Field(alias="annualRatePercent", min_length=1, max_length=64)
    term_months: int = Field(alias="termMonths", ge=1, le=600)
    repayment_style: str = Field(alias="repaymentStyle", min_length=1, max_length=32)
    first_payment_date: date = Field(alias="firstPaymentDate")
    periodic_fee: str | None = Field(default=None, alias="periodicFee", max_length=64)


class FacilityResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    case_id: UUID = Field(serialization_alias="caseId")
    case_version: int = Field(serialization_alias="caseVersion")
    decision_id: UUID = Field(serialization_alias="decisionId")
    principal: str
    annual_rate_percent: str = Field(serialization_alias="annualRatePercent")
    term_months: int = Field(serialization_alias="termMonths")
    periodic_fee: str = Field(serialization_alias="periodicFee")
    repayment_style: str = Field(serialization_alias="repaymentStyle")
    first_payment_date: date = Field(serialization_alias="firstPaymentDate")


class RecordEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: str = Field(min_length=1, max_length=32)
    amount: str = Field(min_length=1, max_length=64)
    external_reference: str = Field(alias="externalReference", min_length=1, max_length=200)
    effective_date: date = Field(alias="effectiveDate")
    reversed_event_id: UUID | None = Field(default=None, alias="reversedEventId")


class EventResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    facility_id: UUID = Field(serialization_alias="facilityId")
    kind: str
    amount: str
    external_reference: str = Field(serialization_alias="externalReference")
    reversed_event_id: UUID | None = Field(serialization_alias="reversedEventId")
    effective_date: date = Field(serialization_alias="effectiveDate")
    created: bool


class LedgerPeriodResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    period: int
    due_date: date = Field(serialization_alias="dueDate")
    expected_fee: str = Field(serialization_alias="expectedFee")
    expected_interest: str = Field(serialization_alias="expectedInterest")
    expected_principal: str = Field(serialization_alias="expectedPrincipal")
    allocated_fee: str = Field(serialization_alias="allocatedFee")
    allocated_interest: str = Field(serialization_alias="allocatedInterest")
    allocated_principal: str = Field(serialization_alias="allocatedPrincipal")
    outstanding_total: str = Field(serialization_alias="outstandingTotal")
    status: str
    overdue: bool


class ExceptionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    kind: str
    period: int | None
    amount: str
    detail_vi: str = Field(serialization_alias="detailVi")


class CollectionNoteResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    id: UUID
    note_kind: str = Field(serialization_alias="noteKind")
    note_text_vi: str = Field(serialization_alias="noteText")
    proposed_action_vi: str | None = Field(serialization_alias="proposedAction")
    author_role: str = Field(serialization_alias="authorRole")


class LedgerResponse(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    facility_id: UUID = Field(serialization_alias="facilityId")
    as_of: date = Field(serialization_alias="asOf")
    allocation_policy_version: str = Field(serialization_alias="allocationPolicyVersion")
    net_paid: str = Field(serialization_alias="netPaid")
    outstanding_fees: str = Field(serialization_alias="outstandingFees")
    outstanding_interest: str = Field(serialization_alias="outstandingInterest")
    outstanding_principal: str = Field(serialization_alias="outstandingPrincipal")
    outstanding_total: str = Field(serialization_alias="outstandingTotal")
    overpayment: str
    is_settled: bool = Field(serialization_alias="isSettled")
    periods: list[LedgerPeriodResponse]
    exceptions: list[ExceptionResponse]
    notes: list[CollectionNoteResponse]


class CreateNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    note_kind: str = Field(alias="noteKind", min_length=1, max_length=32)
    note_text_vi: str = Field(alias="noteText", min_length=1, max_length=4000)
    proposed_action_vi: str | None = Field(
        default=None, alias="proposedAction", min_length=1, max_length=400
    )


Actor = Annotated[ActorContext, Depends(require_actor)]


# -- helpers ------------------------------------------------------------------


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


def _repository(request: Request) -> RepaymentLedgerRepository:
    repository = getattr(request.app.state, "repayment_ledger_repository", None)
    if repository is None:
        raise ApiException(
            status_code=503,
            code="REPAYMENT_LEDGER_SERVICE_UNAVAILABLE",
            message_vi="Dịch vụ sổ thu nợ chưa sẵn sàng.",
            retryable=True,
        )
    return cast(RepaymentLedgerRepository, repository)


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


async def _load_facility_or_404(
    request: Request, case_id: UUID, facility_id: UUID, case_version: int
) -> RecordedFacility:
    facility = await _repository(request).load_facility(
        facility_id, case_id, case_version
    )
    if facility is None:
        raise ApiException(
            status_code=404,
            code="FACILITY_NOT_FOUND",
            message_vi="Không tìm thấy khoản vay trong hồ sơ này.",
        )
    return facility


def _decimal(value: str, *, code: str, field: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code=code,
            message_vi=f"Giá trị {field} không hợp lệ.",
        ) from exc
    return parsed


def _text(value: Decimal) -> str:
    return format(value, "f")


def _facility_response(facility: RecordedFacility) -> FacilityResponse:
    return FacilityResponse(
        id=facility.id,
        case_id=facility.case_id,
        case_version=facility.case_version,
        decision_id=facility.decision_id,
        principal=_text(facility.principal),
        annual_rate_percent=_text(facility.annual_rate_percent),
        term_months=facility.term_months,
        periodic_fee=_text(facility.periodic_fee),
        repayment_style=facility.repayment_style,
        first_payment_date=facility.first_payment_date,
    )


def _period_response(period: LedgerPeriod) -> LedgerPeriodResponse:
    return LedgerPeriodResponse(
        period=period.period,
        due_date=period.due_date,
        expected_fee=_text(period.expected_fee),
        expected_interest=_text(period.expected_interest),
        expected_principal=_text(period.expected_principal),
        allocated_fee=_text(period.allocated_fee),
        allocated_interest=_text(period.allocated_interest),
        allocated_principal=_text(period.allocated_principal),
        outstanding_total=_text(period.outstanding_total),
        status=period.status.value,
        overdue=period.overdue,
    )


def _exception_response(exception: CollectionsException) -> ExceptionResponse:
    return ExceptionResponse(
        kind=exception.kind.value,
        period=exception.period,
        amount=_text(exception.amount),
        detail_vi=exception.detail_vi,
    )


def _note_response(note: RecordedCollectionNote) -> CollectionNoteResponse:
    return CollectionNoteResponse(
        id=note.id,
        note_kind=note.note_kind,
        note_text_vi=note.note_text_vi,
        proposed_action_vi=note.proposed_action_vi,
        author_role=note.author_role,
    )


# -- facility -----------------------------------------------------------------


@router.post("", response_model=FacilityResponse, status_code=201)
async def create_facility(
    case_id: UUID, body: CreateFacilityRequest, actor: Actor, request: Request
) -> FacilityResponse:
    """Open ONE disbursed facility bound to a permitting credit decision."""

    _require_role(actor, OPS_OFFICER_ROLE)
    record = await _assert_case_access(request, actor, case_id)

    decision = await _credit_decision_repository(request).load_decision(
        case_id, record.version
    )
    if decision is None or decision.decision not in _PERMITTING_DECISIONS:
        raise ApiException(
            status_code=409,
            code="FACILITY_REQUIRES_APPROVAL_DECISION",
            message_vi=(
                "Chưa có quyết định phê duyệt tín dụng cho phiên bản hồ sơ hiện "
                "tại để mở khoản vay."
            ),
        )

    principal = _decimal(body.principal, code="INVALID_FACILITY", field="principal")
    rate = _decimal(
        body.annual_rate_percent, code="INVALID_FACILITY", field="annualRatePercent"
    )
    fee = (
        _decimal(body.periodic_fee, code="INVALID_FACILITY", field="periodicFee")
        if body.periodic_fee is not None
        else Decimal("0")
    )
    try:
        facility = Facility(
            id=uuid4(),
            case_id=case_id,
            case_version=record.version,
            decision_id=decision.id,
            principal=principal,
            annual_rate_percent=rate,
            term_months=body.term_months,
            # Validated by the model: an unknown style raises ValidationError -> 422.
            repayment_style=cast(RepaymentStyle, body.repayment_style),
            first_payment_date=body.first_payment_date,
            periodic_fee=fee,
        )
    except (ValidationError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_FACILITY",
            message_vi="Thông tin khoản vay không hợp lệ.",
        ) from exc

    created = await _repository(request).create_facility(
        facility=facility, actor_id=actor.actor_id, actor_role=OPS_OFFICER_ROLE
    )
    return _facility_response(created)


# -- events -------------------------------------------------------------------


@router.post("/{facility_id}/events", response_model=EventResponse)
async def record_event(
    case_id: UUID,
    facility_id: UUID,
    body: RecordEventRequest,
    actor: Actor,
    request: Request,
    response: Response,
) -> EventResponse:
    """Append ONE payment / reversal idempotently (duplicate delivery -> 200)."""

    _require_role(actor, COLLECTIONS_OFFICER_ROLE)
    record = await _assert_case_access(request, actor, case_id)
    facility = await _load_facility_or_404(
        request, case_id, facility_id, record.version
    )
    repository = _repository(request)

    try:
        kind = EventKind(body.kind)
    except ValueError as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_EVENT_KIND",
            message_vi="Loại sự kiện thu nợ không hợp lệ.",
        ) from exc

    amount = _decimal(body.amount, code="INVALID_EVENT", field="amount")

    if kind is EventKind.REVERSAL:
        # A reversal must reference a PAYMENT of THIS facility (the deterministic
        # fold requires it; enforce it up front so the reference is meaningful).
        existing_events = await repository.list_events(facility_id)
        payment_ids = {
            event.id for event in existing_events if event.kind == EventKind.PAYMENT.value
        }
        if body.reversed_event_id is None or body.reversed_event_id not in payment_ids:
            raise ApiException(
                status_code=422,
                code="INVALID_REVERSAL_REFERENCE",
                message_vi="Bút toán đảo phải tham chiếu một khoản thanh toán của khoản vay này.",
            )

    try:
        event = RepaymentEvent(
            id=uuid4(),
            facility_id=facility.id,
            kind=kind,
            amount=amount,
            external_reference=body.external_reference,
            reversed_event_id=body.reversed_event_id,
            effective_date=body.effective_date,
        )
    except (ValidationError, ValueError) as exc:
        raise ApiException(
            status_code=422,
            code="INVALID_EVENT",
            message_vi="Sự kiện thu nợ không hợp lệ.",
        ) from exc

    recorded, created = await repository.record_event(
        event=event, actor_id=actor.actor_id, actor_role=COLLECTIONS_OFFICER_ROLE
    )
    response.status_code = 201 if created else 200
    return EventResponse(
        id=recorded.id,
        facility_id=recorded.facility_id,
        kind=recorded.kind,
        amount=_text(recorded.amount),
        external_reference=recorded.external_reference,
        reversed_event_id=recorded.reversed_event_id,
        effective_date=recorded.effective_date,
        created=created,
    )


# -- ledger read --------------------------------------------------------------


@router.get("/{facility_id}/ledger", response_model=LedgerResponse)
async def get_ledger(
    case_id: UUID,
    facility_id: UUID,
    actor: Actor,
    request: Request,
    as_of: Annotated[date | None, Query(alias="asOf")] = None,
) -> LedgerResponse:
    """Recompute and return the ledger snapshot + collections exceptions + notes."""

    _require_reader(actor)
    record = await _assert_case_access(request, actor, case_id)
    facility = await _load_facility_or_404(
        request, case_id, facility_id, record.version
    )
    repository = _repository(request)

    events = await repository.list_events(facility_id)
    notes = await repository.list_collection_notes(facility_id)
    observation = as_of if as_of is not None else date.today()

    try:
        snapshot: LedgerSnapshot = apply_events(
            facility.to_facility(),
            [event.to_event() for event in events],
            as_of=observation,
        )
    except RepaymentLedgerError as exc:
        # A structurally impossible history would be a durable-data defect; never
        # a client input error.  Surface it explicitly rather than crashing.
        raise ApiException(
            status_code=500,
            code="LEDGER_RECOMPUTE_FAILED",
            message_vi="Không thể tính lại sổ thu nợ.",
        ) from exc

    return LedgerResponse(
        facility_id=snapshot.facility_id,
        as_of=snapshot.as_of,
        allocation_policy_version=snapshot.allocation_policy_version,
        net_paid=_text(snapshot.net_paid),
        outstanding_fees=_text(snapshot.outstanding_fees),
        outstanding_interest=_text(snapshot.outstanding_interest),
        outstanding_principal=_text(snapshot.outstanding_principal),
        outstanding_total=_text(snapshot.outstanding_total),
        overpayment=_text(snapshot.overpayment),
        is_settled=snapshot.is_settled,
        periods=[_period_response(p) for p in snapshot.periods],
        exceptions=[_exception_response(e) for e in snapshot.exceptions],
        notes=[_note_response(n) for n in notes],
    )


# -- collection notes ---------------------------------------------------------


@router.post(
    "/{facility_id}/notes", response_model=CollectionNoteResponse, status_code=201
)
async def create_note(
    case_id: UUID,
    facility_id: UUID,
    body: CreateNoteRequest,
    actor: Actor,
    request: Request,
) -> CollectionNoteResponse:
    """Record ONE human FREE-TEXT observation / proposed action (no execution)."""

    _require_role(actor, COLLECTIONS_OFFICER_ROLE)
    record = await _assert_case_access(request, actor, case_id)
    facility = await _load_facility_or_404(
        request, case_id, facility_id, record.version
    )

    if body.note_kind not in _NOTE_KINDS:
        raise ApiException(
            status_code=422,
            code="INVALID_NOTE_KIND",
            message_vi="Loại ghi chú thu nợ không hợp lệ.",
        )
    if (body.note_kind == "PROPOSED_ACTION") != (body.proposed_action_vi is not None):
        # A proposed action must name its action; an observation must not carry one.
        raise ApiException(
            status_code=422,
            code="INVALID_NOTE",
            message_vi=(
                "Ghi chú đề xuất hành động phải nêu hành động; ghi chú quan sát "
                "thì không."
            ),
        )

    note = await _repository(request).record_collection_note(
        facility_id=facility.id,
        case_id=case_id,
        case_version=record.version,
        note_kind=body.note_kind,
        note_text_vi=body.note_text_vi,
        proposed_action_vi=body.proposed_action_vi,
        actor_id=actor.actor_id,
        actor_role=COLLECTIONS_OFFICER_ROLE,
    )
    return _note_response(note)
