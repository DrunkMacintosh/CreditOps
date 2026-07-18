"""Deterministic RepaymentLedger: the stage-13 pure-fold repayment domain.

Master design section 5 giai đoạn 13 ("Thu nợ gốc, lãi và phí").  The bank
collects on schedule; when repayment capacity degrades a collections officer may
PROPOSE tightening cash-flow control, freezing the undrawn limit, demanding
further security or (via stages 4-6) restructuring -- but every such control is a
PROPOSED action awaiting human authority and is executed nowhere here.  This
module is the deterministic, exact-decimal core:

- ``Facility`` is the frozen, immutable disbursed-facility value object.  Its
  amortisation / balloon schedule is DERIVED from (principal, rate, term, style)
  by the SHARED deterministic calculator ``debt_service_schedule`` (never the
  LLM, never re-implemented here) -- ``build_expected_schedule`` delegates to it.
- ``RepaymentEvent`` is one append-only PAYMENT or REVERSAL.  ``amount`` is
  ALWAYS positive; the economic SIGN comes from ``kind`` (a PAYMENT adds, a
  REVERSAL removes).  A REVERSAL REFERENCES the payment it undoes by id and never
  mutates it.
- ``apply_events`` recomputes the whole ledger state as a PURE FOLD over the
  event history ordered by ``(effective_date, recorded_at, id)`` plus the
  schedule.  NOTHING is stored incrementally: outstanding principal / interest /
  fees, per-period allocation and status, and the collections-exception list are
  all DERIVED on demand.  Because the outstanding buckets depend only on the NET
  cash and the schedule, duplicate, partial, late, out-of-order, backdated and
  reversed payments all fall out of the same fold with no special-casing.

ALLOCATION POLICY (``ALLOCATION_ORDER`` / ``ALLOCATION_POLICY_VERSION``): a
PROPOSED synthetic policy -- each installment's obligations are filled
FEES -> INTEREST -> PRINCIPAL, oldest installment first.  No official SHB
allocation rule has been supplied; reconfigure when one exists.

COLLECTIONS EXCEPTIONS are deterministic SURFACE OUTPUTS, never a model: they are
derived by comparing the fold result to the schedule (an overdue installment with
nothing paid, an overdue installment only partially paid, or cash that cannot be
matched to any scheduled obligation).  A human disposes them; the agent only
surfaces and (elsewhere) proposes.

All customer data, policies and banking-system responses in this project are
synthetic and created solely for demonstration.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.application.underwriting.calculators import (
    CalculatorInput,
    DebtServiceSchedule,
    RepaymentStyle,
    debt_service_schedule,
)
from creditops.domain.ids import CaseId, FacilityId, RepaymentEventId

#: The exact money quantum shared with the calculators (2 decimal places).
_MONEY: Final = Decimal("0.01")
#: Fallback ordering timestamp for an event with no recorded_at (pure-domain
#: fixtures); durable events always carry one.
_MIN_DT: Final = datetime.min.replace(tzinfo=UTC)

#: PROPOSED synthetic allocation policy: each installment is filled in this
#: bucket priority, oldest installment first.  No official SHB rule supplied.
ALLOCATION_ORDER: Final[tuple[str, str, str]] = ("FEES", "INTEREST", "PRINCIPAL")
ALLOCATION_POLICY_VERSION: Final = "collections-allocation-v1"


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MONEY, rounding=ROUND_HALF_UP)


class RepaymentLedgerError(ValueError):
    """A structurally invalid event history (e.g. a reversal that references an
    unknown payment or reverses more than was paid).  Well-formed durable data
    never triggers this: the database and adapter guarantee the references."""


class EventKind(StrEnum):
    """The CLOSED set of repayment-event kinds (PROPOSED synthetic taxonomy)."""

    PAYMENT = "PAYMENT"
    REVERSAL = "REVERSAL"


class PeriodStatus(StrEnum):
    """Coverage of one installment, orthogonal to timeliness (see ``overdue``)."""

    PAID = "PAID"
    PARTIALLY_PAID = "PARTIALLY_PAID"
    UNPAID = "UNPAID"


class CollectionsExceptionKind(StrEnum):
    """The deterministic collections-exception surface (PROPOSED synthetic set).

    - ``OVERDUE_INSTALLMENT``: an installment whose due date has passed with
      NOTHING allocated to it.
    - ``UNDERPAID_PERIOD``: an installment whose due date has passed that is only
      PARTIALLY covered.
    - ``UNMATCHED_PAYMENT``: net cash that exceeds every scheduled obligation and
      cannot be matched to any installment (an overpayment carry).
    """

    OVERDUE_INSTALLMENT = "OVERDUE_INSTALLMENT"
    UNDERPAID_PERIOD = "UNDERPAID_PERIOD"
    UNMATCHED_PAYMENT = "UNMATCHED_PAYMENT"


class Facility(BaseModel):
    """A disbursed facility: immutable inputs from which the schedule is DERIVED.

    Frozen: restructuring is out of scope and never mutates a facility -- it
    returns to stages 4-6 and (later) writes a new facility.  ``periodic_fee`` is
    a PROPOSED synthetic flat servicing fee charged each installment; it is the
    fees bucket that allocation fills first.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: FacilityId
    case_id: CaseId
    case_version: int = Field(ge=1)
    decision_id: UUID
    principal: Decimal = Field(gt=0)
    annual_rate_percent: Decimal = Field(ge=0)
    term_months: int = Field(ge=1)
    repayment_style: RepaymentStyle
    first_payment_date: date
    periodic_fee: Decimal = Field(default=Decimal("0"), ge=0)
    created_at: datetime | None = None


class RepaymentEvent(BaseModel):
    """One append-only payment or reversal.  ``amount`` is always positive; the
    sign of the economic effect comes from ``kind``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: RepaymentEventId
    facility_id: FacilityId
    kind: EventKind
    amount: Decimal = Field(gt=0)
    external_reference: str = Field(min_length=1)
    reversed_event_id: RepaymentEventId | None = None
    effective_date: date
    recorded_at: datetime | None = None

    @model_validator(mode="after")
    def _check_reference(self) -> RepaymentEvent:
        if self.kind is EventKind.REVERSAL and self.reversed_event_id is None:
            raise ValueError("a REVERSAL must reference the payment it undoes")
        if self.kind is EventKind.PAYMENT and self.reversed_event_id is not None:
            raise ValueError("a PAYMENT must not reference a reversed event")
        return self

    @property
    def signed_amount(self) -> Decimal:
        """+amount for a PAYMENT, -amount for a REVERSAL."""
        return self.amount if self.kind is EventKind.PAYMENT else -self.amount


class ExpectedInstallment(BaseModel):
    """One scheduled installment: the fee / interest / principal owed, and when."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: int = Field(ge=1)
    due_date: date
    fee: Decimal
    interest: Decimal
    principal: Decimal

    @property
    def total(self) -> Decimal:
        return self.fee + self.interest + self.principal


class LedgerPeriod(BaseModel):
    """The recomputed state of one installment: what was owed, what the fold
    allocated to it, what remains outstanding, and its coverage / timeliness."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    period: int = Field(ge=1)
    due_date: date
    expected_fee: Decimal
    expected_interest: Decimal
    expected_principal: Decimal
    allocated_fee: Decimal
    allocated_interest: Decimal
    allocated_principal: Decimal
    status: PeriodStatus
    overdue: bool

    @property
    def expected_total(self) -> Decimal:
        return self.expected_fee + self.expected_interest + self.expected_principal

    @property
    def allocated_total(self) -> Decimal:
        return self.allocated_fee + self.allocated_interest + self.allocated_principal

    @property
    def outstanding_total(self) -> Decimal:
        return self.expected_total - self.allocated_total


class CollectionsException(BaseModel):
    """A deterministic collections surface output (never a model prediction)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: CollectionsExceptionKind
    period: int | None
    amount: Decimal
    detail_vi: str = Field(min_length=1)


class LedgerSnapshot(BaseModel):
    """The fully recomputed ledger state for a facility as of an observation date.

    Every figure here is DERIVED from the schedule + event fold; nothing is
    stored.  ``net_paid`` is the signed net cash (payments minus reversals).
    The reconciliation invariant holds exactly at the money quantum:

        total_expected == allocated_total + outstanding_total
        net_paid       == allocated_total + overpayment      (when net_paid >= 0)
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    facility_id: FacilityId
    as_of: date
    schedule_result_id: str
    allocation_policy_version: str = ALLOCATION_POLICY_VERSION
    event_count: int = Field(ge=0)
    net_paid: Decimal
    total_expected_fees: Decimal
    total_expected_interest: Decimal
    total_expected_principal: Decimal
    outstanding_fees: Decimal
    outstanding_interest: Decimal
    outstanding_principal: Decimal
    overpayment: Decimal
    periods: tuple[LedgerPeriod, ...]
    exceptions: tuple[CollectionsException, ...]

    @property
    def total_expected(self) -> Decimal:
        return (
            self.total_expected_fees
            + self.total_expected_interest
            + self.total_expected_principal
        )

    @property
    def outstanding_total(self) -> Decimal:
        return (
            self.outstanding_fees
            + self.outstanding_interest
            + self.outstanding_principal
        )

    @property
    def allocated_total(self) -> Decimal:
        return self.total_expected - self.outstanding_total

    @property
    def is_settled(self) -> bool:
        """True IFF every scheduled obligation is fully covered with no excess."""
        return self.outstanding_total == 0 and self.overpayment == 0


# --- schedule + due dates -------------------------------------------------


def _days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _add_months(anchor: date, months: int) -> date:
    """``anchor`` shifted by whole months, clamping the day to the month length."""
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = min(anchor.day, _days_in_month(year, month))
    return date(year, month, day)


def build_expected_schedule(facility: Facility) -> DebtServiceSchedule:
    """The exact-decimal debt-service schedule, DELEGATED to the shared calculator.

    The schedule (principal + interest per period) is never re-derived here; this
    reuses ``debt_service_schedule`` verbatim so the ledger's expected principal
    and interest are exactly the underwriting schedule.
    """
    return debt_service_schedule(
        principal=CalculatorInput(name="principal", value=facility.principal),
        annual_rate_percent=CalculatorInput(
            name="annual_rate_percent", value=facility.annual_rate_percent
        ),
        term_months=CalculatorInput(
            name="term_months", value=Decimal(facility.term_months)
        ),
        repayment_style=facility.repayment_style,
    )


def build_expected_installments(facility: Facility) -> tuple[ExpectedInstallment, ...]:
    """The schedule rows layered with due dates and the per-period fee."""
    schedule = build_expected_schedule(facility)
    fee = _money(facility.periodic_fee)
    return tuple(
        ExpectedInstallment(
            period=row.period,
            due_date=_add_months(facility.first_payment_date, row.period - 1),
            fee=fee,
            interest=row.interest,
            principal=row.principal,
        )
        for row in schedule.rows
    )


# --- the fold -------------------------------------------------------------


def _order_key(event: RepaymentEvent) -> tuple[date, datetime, UUID]:
    return (event.effective_date, event.recorded_at or _MIN_DT, event.id)


def order_events(
    facility_id: FacilityId, events: Sequence[RepaymentEvent]
) -> tuple[RepaymentEvent, ...]:
    """Validate and canonically order the event history.

    Ordering is ``(effective_date, recorded_at, id)`` so backdated and
    out-of-order deliveries fold into the same deterministic sequence.  A
    reversal must reference a known PAYMENT of this facility, and the reversals
    of any one payment may not exceed the amount originally paid.
    """
    payment_amounts: dict[UUID, Decimal] = {}
    for event in events:
        if event.facility_id != facility_id:
            raise RepaymentLedgerError(
                f"event {event.id} does not belong to facility {facility_id}"
            )
        if event.kind is EventKind.PAYMENT:
            payment_amounts[event.id] = event.amount

    reversed_totals: dict[UUID, Decimal] = defaultdict(lambda: Decimal("0"))
    for event in events:
        if event.kind is EventKind.REVERSAL:
            target = event.reversed_event_id
            if target is None or target not in payment_amounts:
                raise RepaymentLedgerError(
                    f"reversal {event.id} references unknown payment {target}"
                )
            reversed_totals[target] += event.amount
    for payment_id, reversed_total in reversed_totals.items():
        if reversed_total > payment_amounts[payment_id]:
            raise RepaymentLedgerError(
                f"reversals of payment {payment_id} exceed the amount paid"
            )
    return tuple(sorted(events, key=_order_key))


def _take(remaining: Decimal, demand: Decimal) -> Decimal:
    """The cash this demand absorbs from ``remaining`` (never below zero)."""
    if remaining <= 0 or demand <= 0:
        return Decimal("0.00")
    return _money(min(remaining, demand))


def apply_events(
    facility: Facility,
    events: Sequence[RepaymentEvent],
    *,
    as_of: date,
) -> LedgerSnapshot:
    """Recompute the ledger as a pure fold of the ordered events over the schedule.

    ``as_of`` is the observation date that decides whether an unmet installment is
    overdue; it never mutates state.  The waterfall fills each installment
    FEES -> INTEREST -> PRINCIPAL, oldest installment first, from the NET cash
    (payments minus reversals); leftover cash is an overpayment carry.
    """
    schedule = build_expected_schedule(facility)
    installments = build_expected_installments(facility)
    ordered = order_events(facility.id, events)
    net_paid = sum((event.signed_amount for event in ordered), Decimal("0.00"))

    remaining = net_paid
    periods: list[LedgerPeriod] = []
    for installment in installments:
        allocated_fee = _take(remaining, installment.fee)
        remaining -= allocated_fee
        allocated_interest = _take(remaining, installment.interest)
        remaining -= allocated_interest
        allocated_principal = _take(remaining, installment.principal)
        remaining -= allocated_principal

        allocated_total = allocated_fee + allocated_interest + allocated_principal
        expected_total = installment.total
        if allocated_total >= expected_total:
            status = PeriodStatus.PAID
        elif allocated_total > 0:
            status = PeriodStatus.PARTIALLY_PAID
        else:
            status = PeriodStatus.UNPAID
        overdue = installment.due_date <= as_of and allocated_total < expected_total

        periods.append(
            LedgerPeriod(
                period=installment.period,
                due_date=installment.due_date,
                expected_fee=installment.fee,
                expected_interest=installment.interest,
                expected_principal=installment.principal,
                allocated_fee=allocated_fee,
                allocated_interest=allocated_interest,
                allocated_principal=allocated_principal,
                status=status,
                overdue=overdue,
            )
        )

    overpayment = remaining if remaining > 0 else Decimal("0.00")
    exceptions = _derive_exceptions(periods, overpayment)

    total_expected_fees = sum((p.expected_fee for p in periods), Decimal("0.00"))
    total_expected_interest = sum((p.expected_interest for p in periods), Decimal("0.00"))
    total_expected_principal = sum(
        (p.expected_principal for p in periods), Decimal("0.00")
    )
    outstanding_fees = total_expected_fees - sum(
        (p.allocated_fee for p in periods), Decimal("0.00")
    )
    outstanding_interest = total_expected_interest - sum(
        (p.allocated_interest for p in periods), Decimal("0.00")
    )
    outstanding_principal = total_expected_principal - sum(
        (p.allocated_principal for p in periods), Decimal("0.00")
    )

    return LedgerSnapshot(
        facility_id=facility.id,
        as_of=as_of,
        schedule_result_id=schedule.result_id,
        event_count=len(ordered),
        net_paid=net_paid,
        total_expected_fees=total_expected_fees,
        total_expected_interest=total_expected_interest,
        total_expected_principal=total_expected_principal,
        outstanding_fees=outstanding_fees,
        outstanding_interest=outstanding_interest,
        outstanding_principal=outstanding_principal,
        overpayment=overpayment,
        periods=tuple(periods),
        exceptions=exceptions,
    )


def _derive_exceptions(
    periods: Sequence[LedgerPeriod], overpayment: Decimal
) -> tuple[CollectionsException, ...]:
    """Compare the fold result to the schedule and surface collections exceptions.

    Deterministic and total: an overdue installment with nothing paid is
    OVERDUE_INSTALLMENT; an overdue installment only partially paid is
    UNDERPAID_PERIOD; unmatched excess cash is UNMATCHED_PAYMENT.
    """
    exceptions: list[CollectionsException] = []
    for period in periods:
        if not period.overdue:
            continue
        if period.allocated_total == 0:
            exceptions.append(
                CollectionsException(
                    kind=CollectionsExceptionKind.OVERDUE_INSTALLMENT,
                    period=period.period,
                    amount=period.outstanding_total,
                    detail_vi=(
                        f"Kỳ {period.period} đến hạn {period.due_date.isoformat()} "
                        "chưa được thanh toán."
                    ),
                )
            )
        else:
            exceptions.append(
                CollectionsException(
                    kind=CollectionsExceptionKind.UNDERPAID_PERIOD,
                    period=period.period,
                    amount=period.outstanding_total,
                    detail_vi=(
                        f"Kỳ {period.period} đến hạn {period.due_date.isoformat()} "
                        "mới được thanh toán một phần."
                    ),
                )
            )
    if overpayment > 0:
        exceptions.append(
            CollectionsException(
                kind=CollectionsExceptionKind.UNMATCHED_PAYMENT,
                period=None,
                amount=overpayment,
                detail_vi=(
                    "Có khoản tiền vượt quá toàn bộ nghĩa vụ theo lịch, "
                    "chưa khớp được với kỳ trả nợ nào."
                ),
            )
        )
    return tuple(exceptions)


__all__ = [
    "ALLOCATION_ORDER",
    "ALLOCATION_POLICY_VERSION",
    "CollectionsException",
    "CollectionsExceptionKind",
    "EventKind",
    "ExpectedInstallment",
    "Facility",
    "LedgerPeriod",
    "LedgerSnapshot",
    "PeriodStatus",
    "RepaymentEvent",
    "RepaymentLedgerError",
    "apply_events",
    "build_expected_installments",
    "build_expected_schedule",
    "order_events",
]
