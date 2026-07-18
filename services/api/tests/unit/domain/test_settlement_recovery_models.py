"""Unit tests for the stage-14 settlement / recovery domain.

Covers the deterministic eligibility / trigger derivations (Decimal edge cases:
'0.00' vs '0', negative rejected, sustained-shortfall boundary), the frozen
models' invariants (zero-balance consistency, non-empty evidence pack + options),
and the canonical amount normalization.  All data is synthetic.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from creditops.domain.settlement_recovery import (
    MOCK_SETTLEMENT_RECEIPTS,
    SUSTAINED_SHORTFALL_PERIODS,
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

CASE = UUID("10000000-0000-0000-0000-0000000000f1")
ACTOR = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CHECK = UUID("50000000-0000-0000-0000-0000000000f1")
RECOVERY = UUID("60000000-0000-0000-0000-0000000000f1")


def _option() -> RecoveryOption:
    return RecoveryOption(
        label_vi="Cơ cấu lại thời hạn trả nợ (mô phỏng).",
        description_vi="Đề xuất phương án cơ cấu lại (mô phỏng).",
        consequences_vi="Hệ quả: kéo dài thời hạn, tăng chi phí lãi (mô phỏng).",
    )


# -- settlement eligibility (Decimal edge cases) ------------------------------


@pytest.mark.parametrize(
    ("principal", "interest", "fees"),
    [
        ("0", "0", "0"),
        ("0.00", "0.0", "0E-3"),
        ("0.000", "0", "0.00"),
    ],
)
def test_zero_balance_eligible_across_decimal_zero_forms(
    principal: str, interest: str, fees: str
) -> None:
    # '0.00' compares EQUAL to '0' -- Decimal, never string equality.
    inputs = SettlementLedgerInputs(
        outstanding_principal=principal,
        outstanding_interest=interest,
        outstanding_fees=fees,
        open_exception_count=0,
    )
    verdict = derive_settlement_eligible(inputs)
    assert verdict.zero_balance is True
    assert verdict.eligible is True
    # Canonicalized to the single '0' token (keeps the DB text CHECK sound).
    assert verdict.outstanding_principal == "0"
    assert verdict.outstanding_interest == "0"
    assert verdict.outstanding_fees == "0"


def test_nonzero_balance_is_ineligible() -> None:
    inputs = SettlementLedgerInputs(
        outstanding_principal="0",
        outstanding_interest="0.01",
        outstanding_fees="0",
        open_exception_count=0,
    )
    verdict = derive_settlement_eligible(inputs)
    assert verdict.zero_balance is False
    assert verdict.eligible is False
    # Non-zero amounts are echoed verbatim (not canonicalized to '0').
    assert verdict.outstanding_interest == "0.01"


def test_open_exception_blocks_eligibility_even_at_zero_balance() -> None:
    inputs = SettlementLedgerInputs(
        outstanding_principal="0",
        outstanding_interest="0",
        outstanding_fees="0",
        open_exception_count=1,
    )
    verdict = derive_settlement_eligible(inputs)
    assert verdict.zero_balance is True
    assert verdict.eligible is False
    assert verdict.open_exception_count == 1


@pytest.mark.parametrize("bad", ["-0.01", "-1000", "-0.00000001"])
def test_negative_amount_is_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        SettlementLedgerInputs(
            outstanding_principal=bad,
            outstanding_interest="0",
            outstanding_fees="0",
            open_exception_count=0,
        )


@pytest.mark.parametrize("bad", ["abc", "", "1,000", "NaN", "Infinity"])
def test_invalid_amount_is_rejected(bad: str) -> None:
    with pytest.raises(ValidationError):
        SettlementLedgerInputs(
            outstanding_principal=bad,
            outstanding_interest="0",
            outstanding_fees="0",
            open_exception_count=0,
        )


def test_negative_exception_count_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SettlementLedgerInputs(
            outstanding_principal="0",
            outstanding_interest="0",
            outstanding_fees="0",
            open_exception_count=-1,
        )


# -- recovery trigger ---------------------------------------------------------


def test_recovery_trigger_fires_on_sustained_shortfall() -> None:
    inputs = RecoveryTriggerInputs(
        outstanding_total="1000000",
        periods_in_shortfall=SUSTAINED_SHORTFALL_PERIODS,
    )
    assessment = derive_recovery_trigger(inputs)
    assert assessment.triggered is True
    assert assessment.threshold_periods == SUSTAINED_SHORTFALL_PERIODS
    assert assessment.outstanding_total == "1000000"


def test_recovery_trigger_needs_positive_balance() -> None:
    inputs = RecoveryTriggerInputs(
        outstanding_total="0",
        periods_in_shortfall=SUSTAINED_SHORTFALL_PERIODS + 5,
    )
    assert derive_recovery_trigger(inputs).triggered is False


def test_recovery_trigger_below_threshold_does_not_fire() -> None:
    inputs = RecoveryTriggerInputs(
        outstanding_total="1000000",
        periods_in_shortfall=SUSTAINED_SHORTFALL_PERIODS - 1,
    )
    assert derive_recovery_trigger(inputs).triggered is False


def test_recovery_trigger_boundary_is_inclusive() -> None:
    # Exactly N periods triggers; N-1 does not (proven above).
    at_threshold = RecoveryTriggerInputs(
        outstanding_total="1", periods_in_shortfall=SUSTAINED_SHORTFALL_PERIODS
    )
    assert derive_recovery_trigger(at_threshold).triggered is True


def test_recovery_trigger_rejects_negative_total() -> None:
    with pytest.raises(ValidationError):
        RecoveryTriggerInputs(outstanding_total="-1", periods_in_shortfall=9)


# -- settlement check model invariant -----------------------------------------


def test_settlement_check_requires_consistent_zero_balance_flag() -> None:
    # zero_balance_confirmed=True while a total is non-zero is rejected.
    with pytest.raises(ValidationError):
        SettlementCheck(
            id=CHECK,
            case_id=CASE,
            case_version=1,
            outstanding_principal="100",
            outstanding_interest="0",
            outstanding_fees="0",
            open_exception_count=0,
            zero_balance_confirmed=True,
            recorded_by=ACTOR,
        )


def test_settlement_check_accepts_consistent_zero_balance() -> None:
    check = SettlementCheck(
        id=CHECK,
        case_id=CASE,
        case_version=1,
        outstanding_principal="0.00",
        outstanding_interest="0",
        outstanding_fees="0",
        open_exception_count=0,
        zero_balance_confirmed=True,
        recorded_by=ACTOR,
    )
    assert check.outstanding_principal == "0"
    assert check.zero_balance_confirmed is True


def test_mock_receipt_kinds_are_the_two_labelled_mocks() -> None:
    assert MOCK_SETTLEMENT_RECEIPTS == (
        SettlementReceiptKind.MOCK_CLOSURE,
        SettlementReceiptKind.MOCK_RELEASE,
    )


# -- recovery case model invariant --------------------------------------------


def test_recovery_case_requires_non_empty_evidence_and_options() -> None:
    with pytest.raises(ValidationError):
        RecoveryCase(
            id=RECOVERY,
            case_id=CASE,
            case_version=1,
            trigger_summary_vi="Shortfall kéo dài (mô phỏng).",
            escalated_by=ACTOR,
            escalation_rationale_vi="Đề nghị chuẩn bị thu hồi (mô phỏng).",
            evidence_refs=(),
            options=(_option(),),
        )
    with pytest.raises(ValidationError):
        RecoveryCase(
            id=RECOVERY,
            case_id=CASE,
            case_version=1,
            trigger_summary_vi="Shortfall kéo dài (mô phỏng).",
            escalated_by=ACTOR,
            escalation_rationale_vi="Đề nghị chuẩn bị thu hồi (mô phỏng).",
            evidence_refs=("ref://ledger/exception-1",),
            options=(),
        )


def test_recovery_case_defaults_to_preparing() -> None:
    recovery = RecoveryCase(
        id=RECOVERY,
        case_id=CASE,
        case_version=1,
        trigger_summary_vi="Shortfall kéo dài (mô phỏng).",
        escalated_by=ACTOR,
        escalation_rationale_vi="Đề nghị chuẩn bị thu hồi (mô phỏng).",
        evidence_refs=("ref://ledger/exception-1",),
        options=(_option(),),
    )
    assert recovery.status is RecoveryStatus.PREPARING


def test_recovery_case_rejects_blank_evidence_ref() -> None:
    with pytest.raises(ValidationError):
        RecoveryCase(
            id=RECOVERY,
            case_id=CASE,
            case_version=1,
            trigger_summary_vi="Shortfall kéo dài (mô phỏng).",
            escalated_by=ACTOR,
            escalation_rationale_vi="Đề nghị chuẩn bị thu hồi (mô phỏng).",
            evidence_refs=("   ",),
            options=(_option(),),
        )
