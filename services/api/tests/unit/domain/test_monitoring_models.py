"""Unit tests for the stage-12 post-credit monitoring domain.

Covers the deterministic obligation schedule engine (including month-end and
leap-year edge cases), the exact-Decimal covenant comparison, the alert
lifecycle map, and the two deterministic alert-rule predicates.  All data is
synthetic.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from creditops.domain.monitoring import (
    ALLOWED_ALERT_TRANSITIONS,
    AlertStatus,
    ComparisonOperator,
    CovenantThreshold,
    ObligationFrequency,
    ObligationSpec,
    build_breach_detail,
    build_overdue_detail,
    covenant_breach_detected,
    evaluate_covenant,
    generate_obligations,
    is_alert_transition_allowed,
    obligation_overdue,
)


def _spec(frequency: ObligationFrequency) -> ObligationSpec:
    return ObligationSpec(
        frequency=frequency, requirement_text_vi="Nộp báo cáo tài chính (mô phỏng)."
    )


# -- obligation schedule engine -----------------------------------------------


def test_monthly_generation_is_one_month_apart() -> None:
    obligations = generate_obligations(
        _spec(ObligationFrequency.MONTHLY), date(2026, 1, 15), 3
    )
    assert [o.due_date for o in obligations] == [
        date(2026, 2, 15),
        date(2026, 3, 15),
        date(2026, 4, 15),
    ]
    assert [o.sequence for o in obligations] == [1, 2, 3]


def test_quarterly_generation_is_three_months_apart() -> None:
    obligations = generate_obligations(
        _spec(ObligationFrequency.QUARTERLY), date(2026, 1, 15), 4
    )
    assert [o.due_date for o in obligations] == [
        date(2026, 4, 15),
        date(2026, 7, 15),
        date(2026, 10, 15),
        date(2027, 1, 15),
    ]


def test_month_end_is_clamped_without_compounding_drift() -> None:
    # Anchored on 31 Jan: every due date is computed from the ORIGINAL anchor, so
    # the clamp to a short month never bleeds into the next (Mar is 31, not 28).
    obligations = generate_obligations(
        _spec(ObligationFrequency.MONTHLY), date(2026, 1, 31), 4
    )
    assert [o.due_date for o in obligations] == [
        date(2026, 2, 28),
        date(2026, 3, 31),
        date(2026, 4, 30),
        date(2026, 5, 31),
    ]


def test_leap_year_february_end_is_respected() -> None:
    obligations = generate_obligations(
        _spec(ObligationFrequency.MONTHLY), date(2024, 1, 31), 1
    )
    assert obligations[0].due_date == date(2024, 2, 29)


def test_quarterly_month_end_clamps_each_target_month() -> None:
    obligations = generate_obligations(
        _spec(ObligationFrequency.QUARTERLY), date(2024, 1, 31), 4
    )
    assert [o.due_date for o in obligations] == [
        date(2024, 4, 30),
        date(2024, 7, 31),
        date(2024, 10, 31),
        date(2025, 1, 31),
    ]


def test_generation_is_deterministic() -> None:
    args = (_spec(ObligationFrequency.MONTHLY), date(2026, 6, 30), 5)
    assert generate_obligations(*args) == generate_obligations(*args)


def test_generation_rejects_non_positive_count() -> None:
    with pytest.raises(ValueError):
        generate_obligations(_spec(ObligationFrequency.MONTHLY), date(2026, 1, 1), 0)


# -- covenant evaluation (exact, no division) ---------------------------------


def _threshold(
    operator: ComparisonOperator, value: str, version: int = 1
) -> CovenantThreshold:
    return CovenantThreshold(
        metric_key="DSCR",
        operator=operator,
        threshold_value=Decimal(value),
        threshold_version=version,
    )


def test_covenant_pass_on_gte_boundary_is_exact() -> None:
    # 1.20 exactly meets a >= 1.20 threshold; cross-multiplication is exact.
    evaluation = evaluate_covenant(
        Decimal("1.20"), Decimal("1"), _threshold(ComparisonOperator.GTE, "1.20")
    )
    assert evaluation.passed is True
    assert evaluation.comparison_lhs == Decimal("1.20")
    assert evaluation.comparison_rhs == Decimal("1.20")


def test_covenant_ratio_uses_exact_cross_multiplication() -> None:
    # 1000 / 800 = 1.25 >= 1.20.  We never divide: we compare 1000 vs 1.20*800.
    evaluation = evaluate_covenant(
        Decimal("1000"), Decimal("800"), _threshold(ComparisonOperator.GTE, "1.20")
    )
    assert evaluation.comparison_rhs == Decimal("960.00")
    assert evaluation.comparison_lhs == Decimal("1000")
    assert evaluation.passed is True


def test_covenant_fail_when_ratio_below_threshold() -> None:
    # 1000 / 900 ~= 1.111 < 1.20; the breach predicate fires.
    evaluation = evaluate_covenant(
        Decimal("1000"), Decimal("900"), _threshold(ComparisonOperator.GTE, "1.20")
    )
    assert evaluation.passed is False
    assert covenant_breach_detected(evaluation) is True


def test_covenant_third_ratio_has_no_rounding_error() -> None:
    # 1 / 3 vs a 0.333... threshold would be inexact under division; cross-
    # multiplication stays exact: compare 1 vs 0.3333333333 * 3 = 0.9999999999.
    evaluation = evaluate_covenant(
        Decimal("1"), Decimal("3"), _threshold(ComparisonOperator.GTE, "0.3333333333")
    )
    assert evaluation.comparison_rhs == Decimal("0.9999999999")
    assert evaluation.passed is True  # 1 >= 0.9999999999


@pytest.mark.parametrize(
    ("operator", "numerator", "threshold", "expected"),
    [
        (ComparisonOperator.GT, "1.21", "1.20", True),
        (ComparisonOperator.GT, "1.20", "1.20", False),
        (ComparisonOperator.LTE, "0.80", "0.80", True),
        (ComparisonOperator.LT, "0.80", "0.80", False),
        (ComparisonOperator.EQ, "1.50", "1.50", True),
        (ComparisonOperator.EQ, "1.51", "1.50", False),
    ],
)
def test_every_operator_reflects_the_declared_comparison(
    operator: ComparisonOperator, numerator: str, threshold: str, expected: bool
) -> None:
    evaluation = evaluate_covenant(
        Decimal(numerator), Decimal("1"), _threshold(operator, threshold)
    )
    assert evaluation.passed is expected


def test_covenant_rejects_non_positive_denominator() -> None:
    with pytest.raises(ValueError):
        evaluate_covenant(
            Decimal("1"), Decimal("0"), _threshold(ComparisonOperator.GTE, "1")
        )


# -- overdue-obligation predicate ---------------------------------------------


def test_observation_after_due_date_is_overdue() -> None:
    assert (
        obligation_overdue(date(2026, 3, 31), datetime(2026, 4, 1, 9, 0, tzinfo=UTC))
        is True
    )


def test_observation_on_due_date_is_not_overdue() -> None:
    assert (
        obligation_overdue(date(2026, 3, 31), datetime(2026, 3, 31, 23, 0, tzinfo=UTC))
        is False
    )


def test_observation_before_due_date_is_not_overdue() -> None:
    assert (
        obligation_overdue(date(2026, 3, 31), datetime(2026, 3, 1, 0, 0, tzinfo=UTC))
        is False
    )


# -- alert lifecycle map ------------------------------------------------------

_ALLOWED_ALERT_PAIRS = [
    (AlertStatus.OPEN, AlertStatus.ACKNOWLEDGED),
    (AlertStatus.OPEN, AlertStatus.ESCALATED),
    (AlertStatus.OPEN, AlertStatus.DISMISSED_BY_HUMAN),
    (AlertStatus.ACKNOWLEDGED, AlertStatus.ESCALATED),
    (AlertStatus.ACKNOWLEDGED, AlertStatus.DISMISSED_BY_HUMAN),
    (AlertStatus.ESCALATED, AlertStatus.DISMISSED_BY_HUMAN),
]

_FORBIDDEN_ALERT_PAIRS = [
    # A dispositioned alert never reopens.
    (AlertStatus.ACKNOWLEDGED, AlertStatus.OPEN),
    (AlertStatus.ESCALATED, AlertStatus.OPEN),
    (AlertStatus.ESCALATED, AlertStatus.ACKNOWLEDGED),
    # DISMISSED_BY_HUMAN is fully terminal.
    (AlertStatus.DISMISSED_BY_HUMAN, AlertStatus.OPEN),
    (AlertStatus.DISMISSED_BY_HUMAN, AlertStatus.ESCALATED),
    # A rule may never re-create OPEN over an existing alert as a transition.
    (AlertStatus.OPEN, AlertStatus.OPEN),
]


@pytest.mark.parametrize(("frm", "to"), _ALLOWED_ALERT_PAIRS)
def test_allowed_alert_transitions_are_permitted(
    frm: AlertStatus, to: AlertStatus
) -> None:
    assert is_alert_transition_allowed(frm, to) is True


@pytest.mark.parametrize(("frm", "to"), _FORBIDDEN_ALERT_PAIRS)
def test_forbidden_alert_transitions_are_rejected(
    frm: AlertStatus, to: AlertStatus
) -> None:
    assert is_alert_transition_allowed(frm, to) is False


@pytest.mark.parametrize("status", list(AlertStatus))
def test_no_alert_self_transition_is_allowed(status: AlertStatus) -> None:
    assert is_alert_transition_allowed(status, status) is False


def test_alert_map_covers_every_status_exhaustively() -> None:
    assert set(ALLOWED_ALERT_TRANSITIONS) == set(AlertStatus)
    for targets in ALLOWED_ALERT_TRANSITIONS.values():
        assert targets <= set(AlertStatus)
    assert ALLOWED_ALERT_TRANSITIONS[AlertStatus.DISMISSED_BY_HUMAN] == frozenset()


# -- deterministic detail lines (no model) ------------------------------------


def test_breach_detail_echoes_metric_and_threshold() -> None:
    evaluation = evaluate_covenant(
        Decimal("1000"), Decimal("900"), _threshold(ComparisonOperator.GTE, "1.20", 2)
    )
    detail = build_breach_detail(evaluation)
    assert "DSCR" in detail
    assert "1000/900" in detail
    assert "1.20" in detail
    assert "2" in detail  # threshold version


def test_overdue_detail_echoes_both_dates() -> None:
    detail = build_overdue_detail(
        date(2026, 3, 31), datetime(2026, 4, 2, 9, 0, tzinfo=UTC)
    )
    assert "2026-04-02" in detail
    assert "2026-03-31" in detail
