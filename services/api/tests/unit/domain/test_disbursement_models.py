"""Unit tests for the stage-11 disbursement domain value objects + helpers.

Covers the exact-decimal money parsing, the currency-aware / cap-aware validation
against approved terms, the closed execution-status set + deterministic
transition map, and the frozen model invariants (positive amount; labelled-mock
receipt whose receipt_ref is present IFF the result confirmed execution).  All
data is synthetic.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from creditops.domain.disbursements import (
    ADAPTER_RESULT_STATUSES,
    ALLOWED_EXECUTION_TRANSITIONS,
    MOCK_DISBURSEMENT_ADAPTER_LABEL,
    REATTEMPTABLE_STATUSES,
    RECONCILABLE_STATUSES,
    AmountExceedsApprovedError,
    CurrencyMismatchError,
    DisbursementExecutionReceipt,
    ExecutionStatus,
    ProposedDisbursementAction,
    is_execution_transition_allowed,
    parse_exact_amount,
    validate_amount_against_terms,
)

CASE = uuid4()
DECISION = uuid4()
ACTOR = uuid4()


def _action(**overrides: object) -> ProposedDisbursementAction:
    base: dict[str, object] = dict(
        id=uuid4(),
        case_id=CASE,
        case_version=1,
        decision_id=DECISION,
        amount=Decimal("5000000000"),
        currency="VND",
        beneficiary_ref_vi="Nhà cung cấp (mô phỏng)",
        account_ref_vi="TK-BENEFICIARY-DEMO",
        created_by=ACTOR,
    )
    base.update(overrides)
    return ProposedDisbursementAction(**base)  # type: ignore[arg-type]


# -- exact-decimal amounts ----------------------------------------------------


def test_parse_exact_amount_accepts_valid_decimal() -> None:
    assert parse_exact_amount("1234.56") == Decimal("1234.56")
    # No float drift: the parsed value is an exact Decimal.
    assert parse_exact_amount("0.10") == Decimal("0.10")


@pytest.mark.parametrize("bad", ["-1", "0", "0.00", "abc", "1,000", "", "  "])
def test_parse_exact_amount_rejects_non_positive_or_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_exact_amount(bad)


# -- validation against approved terms ----------------------------------------


def test_validate_amount_currency_mismatch_raises() -> None:
    with pytest.raises(CurrencyMismatchError):
        validate_amount_against_terms(
            amount=Decimal("100"),
            currency="USD",
            approved_amount=Decimal("100"),
            approved_currency="VND",
        )


def test_validate_amount_exceeding_cap_raises() -> None:
    with pytest.raises(AmountExceedsApprovedError):
        validate_amount_against_terms(
            amount=Decimal("101"),
            currency="VND",
            approved_amount=Decimal("100"),
            approved_currency="VND",
        )


def test_validate_amount_partial_and_equal_are_allowed() -> None:
    # A partial disbursement (<= approved) and an equal one are both allowed.
    validate_amount_against_terms(
        amount=Decimal("60"),
        currency="VND",
        approved_amount=Decimal("100"),
        approved_currency="VND",
    )
    validate_amount_against_terms(
        amount=Decimal("100"),
        currency="VND",
        approved_amount=Decimal("100"),
        approved_currency="VND",
    )


def test_validate_amount_absent_approved_fields_are_not_compared() -> None:
    # No approved amount/currency fixed: nothing to validate against (never a
    # fabricated limit).
    validate_amount_against_terms(
        amount=Decimal("999999"),
        currency="USD",
        approved_amount=None,
        approved_currency=None,
    )


# -- execution status set + transitions ---------------------------------------


def test_execution_status_is_the_closed_five_value_set() -> None:
    assert {s.value for s in ExecutionStatus} == {
        "PROPOSED",
        "EXECUTION_REQUESTED",
        "EXECUTION_UNKNOWN",
        "CONFIRMED_EXECUTED",
        "CONFIRMED_NOT_EXECUTED",
    }


def test_adapter_result_and_reattempt_sets() -> None:
    assert ADAPTER_RESULT_STATUSES == frozenset(
        {ExecutionStatus.CONFIRMED_EXECUTED, ExecutionStatus.EXECUTION_UNKNOWN}
    )
    # Only a never-attempted or human-confirmed non-execution may re-attempt.
    assert REATTEMPTABLE_STATUSES == frozenset(
        {ExecutionStatus.PROPOSED, ExecutionStatus.CONFIRMED_NOT_EXECUTED}
    )
    # Unresolved states must be reconciled (never auto-retried).
    assert RECONCILABLE_STATUSES == frozenset(
        {ExecutionStatus.EXECUTION_REQUESTED, ExecutionStatus.EXECUTION_UNKNOWN}
    )


def test_allowed_transitions_cover_the_lifecycle() -> None:
    assert is_execution_transition_allowed(
        ExecutionStatus.PROPOSED, ExecutionStatus.EXECUTION_REQUESTED
    )
    assert is_execution_transition_allowed(
        ExecutionStatus.EXECUTION_REQUESTED, ExecutionStatus.CONFIRMED_EXECUTED
    )
    assert is_execution_transition_allowed(
        ExecutionStatus.EXECUTION_REQUESTED, ExecutionStatus.EXECUTION_UNKNOWN
    )
    # Human reconciliation edges from an unknown execution.
    assert is_execution_transition_allowed(
        ExecutionStatus.EXECUTION_UNKNOWN, ExecutionStatus.CONFIRMED_EXECUTED
    )
    assert is_execution_transition_allowed(
        ExecutionStatus.EXECUTION_UNKNOWN, ExecutionStatus.CONFIRMED_NOT_EXECUTED
    )
    # Only CONFIRMED_NOT_EXECUTED re-opens a new attempt.
    assert is_execution_transition_allowed(
        ExecutionStatus.CONFIRMED_NOT_EXECUTED, ExecutionStatus.EXECUTION_REQUESTED
    )


def test_forbidden_transitions_have_no_implicit_edge() -> None:
    # A confirmed execution is terminal; PROPOSED never jumps to a result.
    assert ALLOWED_EXECUTION_TRANSITIONS[ExecutionStatus.CONFIRMED_EXECUTED] == frozenset()
    assert not is_execution_transition_allowed(
        ExecutionStatus.PROPOSED, ExecutionStatus.CONFIRMED_EXECUTED
    )
    assert not is_execution_transition_allowed(
        ExecutionStatus.PROPOSED, ExecutionStatus.EXECUTION_UNKNOWN
    )
    # No self-transition.
    assert not is_execution_transition_allowed(
        ExecutionStatus.EXECUTION_UNKNOWN, ExecutionStatus.EXECUTION_UNKNOWN
    )


# -- ProposedDisbursementAction ----------------------------------------------


def test_action_defaults_to_proposed_and_exposes_amount_text() -> None:
    action = _action(amount=Decimal("5000000000"))
    assert action.status is ExecutionStatus.PROPOSED
    assert action.amount_text == "5000000000"


def test_action_rejects_non_positive_amount() -> None:
    with pytest.raises((ValidationError, ValueError)):
        _action(amount=Decimal("0"))
    with pytest.raises((ValidationError, ValueError)):
        _action(amount=Decimal("-1"))


# -- DisbursementExecutionReceipt --------------------------------------------


def _receipt(**overrides: object) -> DisbursementExecutionReceipt:
    base: dict[str, object] = dict(
        id=uuid4(),
        action_id=uuid4(),
        idempotency_key="idem-1",
        adapter_label=MOCK_DISBURSEMENT_ADAPTER_LABEL,
        result_status=ExecutionStatus.CONFIRMED_EXECUTED,
        receipt_ref="receipt-ref-1",
        is_mock=True,
    )
    base.update(overrides)
    return DisbursementExecutionReceipt(**base)  # type: ignore[arg-type]


def test_receipt_confirmed_requires_receipt_ref() -> None:
    with pytest.raises((ValidationError, ValueError)):
        _receipt(result_status=ExecutionStatus.CONFIRMED_EXECUTED, receipt_ref=None)


def test_receipt_unknown_must_not_carry_receipt_ref() -> None:
    with pytest.raises((ValidationError, ValueError)):
        _receipt(
            result_status=ExecutionStatus.EXECUTION_UNKNOWN,
            receipt_ref="should-not-exist",
        )
    # An unknown result with no receipt_ref is valid.
    receipt = _receipt(
        result_status=ExecutionStatus.EXECUTION_UNKNOWN, receipt_ref=None
    )
    assert receipt.result_status is ExecutionStatus.EXECUTION_UNKNOWN


def test_receipt_rejects_non_mock_label_and_non_adapter_result() -> None:
    with pytest.raises((ValidationError, ValueError)):
        _receipt(adapter_label="REAL_CORE_BANKING")
    with pytest.raises((ValidationError, ValueError)):
        # A human-only outcome is never an adapter result.
        _receipt(
            result_status=ExecutionStatus.CONFIRMED_NOT_EXECUTED, receipt_ref=None
        )
    with pytest.raises((ValidationError, ValueError)):
        _receipt(is_mock=False)
