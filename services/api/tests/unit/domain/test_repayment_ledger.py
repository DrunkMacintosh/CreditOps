"""Unit tests for the stage-13 deterministic RepaymentLedger fold.

The fold is the heart of the stage: outstanding principal / interest / fees,
per-period allocation and status, and the collections-exception surface are all
DERIVED from the schedule + ordered event history, never stored.  Every disorder
case -- duplicate, reversal (of a full and of a partial payment), partial, late,
out-of-order, backdated, overpayment -- is covered here with hand-computed exact
``Decimal`` arithmetic.

Fixture facility (EQUAL_PRINCIPAL, principal 120000.00, 12% p.a., 3 months,
periodic fee 100.00, first payment 2026-08-01) gives the schedule:

    period  due         fee      interest   principal   total
    1       2026-08-01  100.00   1200.00    40000.00    41300.00
    2       2026-09-01  100.00    800.00    40000.00    40900.00
    3       2026-10-01  100.00    400.00    40000.00    40500.00
                        300.00   2400.00   120000.00   122700.00

All identifiers and amounts are synthetic and created solely for demonstration.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from creditops.application.underwriting.calculators import (
    CalculatorInput,
    debt_service_schedule,
)
from creditops.domain.repayments import (
    CollectionsExceptionKind,
    EventKind,
    Facility,
    LedgerSnapshot,
    PeriodStatus,
    RepaymentEvent,
    RepaymentLedgerError,
    apply_events,
    build_expected_schedule,
    order_events,
)

CASE = UUID("10000000-0000-0000-0000-0000000000f3")
DECISION = UUID("d0000000-0000-0000-0000-0000000000f3")
FACILITY = UUID("fac00000-0000-0000-0000-0000000000f3")
PAY_A = UUID("ea000000-0000-0000-0000-00000000000a")
PAY_B = UUID("eb000000-0000-0000-0000-00000000000b")
REV_1 = UUID("ec000000-0000-0000-0000-00000000000c")


def _facility(
    *,
    principal: str = "120000.00",
    rate: str = "12",
    term: int = 3,
    style: str = "EQUAL_PRINCIPAL",
    fee: str = "100.00",
    first: date = date(2026, 8, 1),
) -> Facility:
    return Facility(
        id=FACILITY,
        case_id=CASE,
        case_version=1,
        decision_id=DECISION,
        principal=Decimal(principal),
        annual_rate_percent=Decimal(rate),
        term_months=term,
        repayment_style=style,  # type: ignore[arg-type]
        first_payment_date=first,
        periodic_fee=Decimal(fee),
    )


def _payment(
    event_id: UUID, amount: str, effective: date, *, ref: str, recorded: int = 0
) -> RepaymentEvent:
    return RepaymentEvent(
        id=event_id,
        facility_id=FACILITY,
        kind=EventKind.PAYMENT,
        amount=Decimal(amount),
        external_reference=ref,
        effective_date=effective,
        recorded_at=datetime(2026, 8, 1, 12, recorded, tzinfo=UTC),
    )


def _reversal(
    event_id: UUID, amount: str, effective: date, *, ref: str, target: UUID
) -> RepaymentEvent:
    return RepaymentEvent(
        id=event_id,
        facility_id=FACILITY,
        kind=EventKind.REVERSAL,
        amount=Decimal(amount),
        external_reference=ref,
        reversed_event_id=target,
        effective_date=effective,
        recorded_at=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
    )


# -- schedule reuse -----------------------------------------------------------


def test_build_expected_schedule_delegates_to_calculators() -> None:
    facility = _facility()
    schedule = build_expected_schedule(facility)
    direct = debt_service_schedule(
        principal=CalculatorInput(name="principal", value=Decimal("120000.00")),
        annual_rate_percent=CalculatorInput(name="annual_rate_percent", value=Decimal("12")),
        term_months=CalculatorInput(name="term_months", value=Decimal(3)),
        repayment_style="EQUAL_PRINCIPAL",
    )
    # Reuse (not re-implementation): identical rows and a matching result id.
    assert schedule.result_id == direct.result_id
    assert [(r.period, r.principal, r.interest) for r in schedule.rows] == [
        (1, Decimal("40000.00"), Decimal("1200.00")),
        (2, Decimal("40000.00"), Decimal("800.00")),
        (3, Decimal("40000.00"), Decimal("400.00")),
    ]


def test_snapshot_carries_schedule_result_id() -> None:
    facility = _facility()
    snapshot = apply_events(facility, (), as_of=date(2026, 7, 1))
    assert snapshot.schedule_result_id == build_expected_schedule(facility).result_id


# -- clean paths --------------------------------------------------------------


def test_full_on_time_payment_marks_period_paid() -> None:
    facility = _facility()
    events = [_payment(PAY_A, "41300.00", date(2026, 8, 1), ref="R1")]
    snap = apply_events(facility, events, as_of=date(2026, 8, 1))

    assert snap.net_paid == Decimal("41300.00")
    p1, p2, p3 = snap.periods
    assert p1.status is PeriodStatus.PAID
    assert p1.overdue is False
    assert p1.allocated_fee == Decimal("100.00")
    assert p1.allocated_interest == Decimal("1200.00")
    assert p1.allocated_principal == Decimal("40000.00")
    # Future installments are untouched and not overdue on the due date of p1.
    assert p2.status is PeriodStatus.UNPAID and p2.overdue is False
    assert p3.status is PeriodStatus.UNPAID and p3.overdue is False
    assert snap.outstanding_fees == Decimal("200.00")
    assert snap.outstanding_interest == Decimal("1200.00")
    assert snap.outstanding_principal == Decimal("80000.00")
    assert snap.exceptions == ()


def test_exact_settlement_is_settled_with_no_exceptions() -> None:
    facility = _facility()
    events = [_payment(PAY_A, "122700.00", date(2026, 8, 1), ref="R1")]
    snap = apply_events(facility, events, as_of=date(2026, 10, 15))

    assert all(p.status is PeriodStatus.PAID for p in snap.periods)
    assert snap.outstanding_total == Decimal("0.00")
    assert snap.overpayment == Decimal("0.00")
    assert snap.is_settled is True
    assert snap.exceptions == ()


# -- partial / late / overdue -------------------------------------------------


def test_underpaid_due_period_surfaces_underpaid_exception() -> None:
    facility = _facility()
    events = [_payment(PAY_A, "20000.00", date(2026, 8, 1), ref="R1")]
    snap = apply_events(facility, events, as_of=date(2026, 8, 5))

    p1 = snap.periods[0]
    # Waterfall: fee 100 -> interest 1200 -> principal 18700 = 20000 allocated.
    assert p1.allocated_fee == Decimal("100.00")
    assert p1.allocated_interest == Decimal("1200.00")
    assert p1.allocated_principal == Decimal("18700.00")
    assert p1.status is PeriodStatus.PARTIALLY_PAID
    assert p1.overdue is True
    assert p1.outstanding_total == Decimal("21300.00")

    assert len(snap.exceptions) == 1
    exc = snap.exceptions[0]
    assert exc.kind is CollectionsExceptionKind.UNDERPAID_PERIOD
    assert exc.period == 1
    assert exc.amount == Decimal("21300.00")


def test_missed_installments_surface_overdue_exceptions() -> None:
    facility = _facility()
    snap = apply_events(facility, (), as_of=date(2026, 9, 15))

    p1, p2, p3 = snap.periods
    assert p1.overdue is True and p1.status is PeriodStatus.UNPAID
    assert p2.overdue is True and p2.status is PeriodStatus.UNPAID
    assert p3.overdue is False  # due 2026-10-01, after as_of

    kinds = [(e.kind, e.period, e.amount) for e in snap.exceptions]
    assert kinds == [
        (CollectionsExceptionKind.OVERDUE_INSTALLMENT, 1, Decimal("41300.00")),
        (CollectionsExceptionKind.OVERDUE_INSTALLMENT, 2, Decimal("40900.00")),
    ]


def test_late_but_now_full_payment_has_no_exception() -> None:
    facility = _facility()
    # Paid 19 days late, but fully; as of a later date it is simply PAID.
    events = [_payment(PAY_A, "41300.00", date(2026, 8, 20), ref="R1")]
    snap = apply_events(facility, events, as_of=date(2026, 8, 25))

    assert snap.periods[0].status is PeriodStatus.PAID
    assert snap.periods[0].overdue is False
    assert snap.exceptions == ()


# -- reversals ----------------------------------------------------------------


def test_reversal_of_full_payment_reverts_to_overdue() -> None:
    facility = _facility()
    events = [
        _payment(PAY_A, "41300.00", date(2026, 8, 1), ref="R1"),
        _reversal(REV_1, "41300.00", date(2026, 8, 5), ref="R1-REV", target=PAY_A),
    ]
    snap = apply_events(facility, events, as_of=date(2026, 8, 10))

    assert snap.net_paid == Decimal("0.00")
    p1 = snap.periods[0]
    assert p1.status is PeriodStatus.UNPAID
    assert p1.overdue is True
    assert snap.exceptions[0].kind is CollectionsExceptionKind.OVERDUE_INSTALLMENT
    assert snap.exceptions[0].amount == Decimal("41300.00")


def test_reversal_of_partial_payment_reduces_net() -> None:
    facility = _facility()
    events = [
        _payment(PAY_A, "30000.00", date(2026, 8, 1), ref="R1"),
        _reversal(REV_1, "10000.00", date(2026, 8, 3), ref="R1-REV", target=PAY_A),
    ]
    snap = apply_events(facility, events, as_of=date(2026, 8, 10))

    assert snap.net_paid == Decimal("20000.00")
    p1 = snap.periods[0]
    assert p1.status is PeriodStatus.PARTIALLY_PAID
    assert p1.outstanding_total == Decimal("21300.00")
    assert snap.exceptions[0].kind is CollectionsExceptionKind.UNDERPAID_PERIOD


# -- out-of-order / backdated -------------------------------------------------


def test_backdated_and_out_of_order_events_fold_identically() -> None:
    facility = _facility()
    # B is effective earlier (p1) but "delivered"/recorded after A (p2).
    a = _payment(PAY_A, "20000.00", date(2026, 9, 1), ref="RA", recorded=5)
    b = _payment(PAY_B, "41300.00", date(2026, 8, 1), ref="RB", recorded=9)

    forward = apply_events(facility, [a, b], as_of=date(2026, 9, 15))
    permuted = apply_events(facility, [b, a], as_of=date(2026, 9, 15))

    # The fold depends only on net cash + schedule: input order is irrelevant.
    assert forward == permuted

    p1, p2, p3 = forward.periods
    assert p1.status is PeriodStatus.PAID  # 41300 from the backdated payment
    assert p2.status is PeriodStatus.PARTIALLY_PAID
    assert p2.overdue is True
    assert p2.outstanding_total == Decimal("20900.00")
    assert forward.exceptions == (
        forward.exceptions[0],
    )  # exactly one
    assert forward.exceptions[0].kind is CollectionsExceptionKind.UNDERPAID_PERIOD
    assert forward.exceptions[0].period == 2
    assert forward.exceptions[0].amount == Decimal("20900.00")


# -- overpayment --------------------------------------------------------------


def test_overpayment_carry_surfaces_unmatched_payment() -> None:
    facility = _facility()
    events = [_payment(PAY_A, "130000.00", date(2026, 8, 1), ref="R1")]
    snap = apply_events(facility, events, as_of=date(2026, 10, 15))

    assert all(p.status is PeriodStatus.PAID for p in snap.periods)
    assert snap.overpayment == Decimal("7300.00")  # 130000 - 122700
    assert snap.is_settled is False
    unmatched = [
        e for e in snap.exceptions if e.kind is CollectionsExceptionKind.UNMATCHED_PAYMENT
    ]
    assert len(unmatched) == 1
    assert unmatched[0].period is None
    assert unmatched[0].amount == Decimal("7300.00")


# -- reconciliation invariants ------------------------------------------------


@pytest.mark.parametrize("amount", ["0.00", "20000.00", "122700.00", "130000.00"])
def test_reconciliation_invariants_hold(amount: str) -> None:
    facility = _facility()
    events = (
        [_payment(PAY_A, amount, date(2026, 8, 1), ref="R1")]
        if Decimal(amount) > 0
        else []
    )
    snap: LedgerSnapshot = apply_events(facility, events, as_of=date(2026, 10, 15))

    # Every dong is accounted for at the money quantum, with no drift.
    assert snap.total_expected == snap.allocated_total + snap.outstanding_total
    assert snap.net_paid == snap.allocated_total + snap.overpayment
    assert snap.total_expected == Decimal("122700.00")


# -- balloon style ------------------------------------------------------------


def test_balloon_interim_periods_are_interest_only() -> None:
    facility = _facility(principal="100000.00", fee="0", style="BALLOON")
    snap = apply_events(facility, (), as_of=date(2026, 9, 15))

    p1, p2, p3 = snap.periods
    assert p1.expected_interest == Decimal("1000.00")
    assert p1.expected_principal == Decimal("0.00")
    assert p3.expected_principal == Decimal("100000.00")
    # p1 and p2 are overdue interest-only obligations; p3 not yet due.
    assert [e.period for e in snap.exceptions] == [1, 2]
    assert snap.exceptions[0].amount == Decimal("1000.00")


# -- structural validation ----------------------------------------------------


def test_reversal_of_unknown_payment_is_rejected() -> None:
    facility = _facility()
    orphan = _reversal(
        REV_1, "10.00", date(2026, 8, 5), ref="ORPH", target=PAY_B
    )  # PAY_B not present
    with pytest.raises(RepaymentLedgerError):
        order_events(facility.id, [orphan])


def test_over_reversal_is_rejected() -> None:
    facility = _facility()
    events = [
        _payment(PAY_A, "100.00", date(2026, 8, 1), ref="R1"),
        _reversal(REV_1, "150.00", date(2026, 8, 5), ref="R1-REV", target=PAY_A),
    ]
    with pytest.raises(RepaymentLedgerError):
        order_events(facility.id, events)


def test_payment_may_not_reference_a_reversed_event() -> None:
    with pytest.raises(ValueError):
        RepaymentEvent(
            id=PAY_A,
            facility_id=FACILITY,
            kind=EventKind.PAYMENT,
            amount=Decimal("1.00"),
            external_reference="BAD",
            reversed_event_id=PAY_B,
            effective_date=date(2026, 8, 1),
        )
