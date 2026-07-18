"""Domain tests for the stage-6 human credit decision + approved-term snapshot.

Covers: the decision-type/conditions coupling; the snapshot hash determinism
and hash-matches-terms invariant; the ``assert_snapshot_matches_decision``
pairing rules (forbidden-outcome snapshot, wrong decision, wrong case version);
and ``build_term_snapshot`` producing a matching hash.  All identifiers are
synthetic.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from creditops.domain.credit_decisions import (
    ApprovedTerms,
    ApprovedTermSnapshot,
    CreditDecisionType,
    HumanCreditDecision,
    assert_snapshot_matches_decision,
    build_term_snapshot,
    compute_terms_hash,
)

CASE_ID = UUID("10000000-0000-0000-0000-0000000000d1")
DECIDER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


def _decision(
    *,
    decision: CreditDecisionType = CreditDecisionType.APPROVED_AS_PROPOSED,
    conditions: tuple[str, ...] = (),
    case_version: int = 1,
) -> HumanCreditDecision:
    return HumanCreditDecision(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=case_version,
        decision=decision,
        rationale_vi="Đã rà soát hồ sơ (dữ liệu mô phỏng).",
        decided_by=DECIDER,
        decided_by_role="CREDIT_APPROVER",
        conditions=conditions,
    )


# -- decision-type / conditions coupling --------------------------------------


def test_approved_with_conditions_requires_non_empty_conditions() -> None:
    with pytest.raises(ValidationError):
        _decision(decision=CreditDecisionType.APPROVED_WITH_CONDITIONS)


def test_approved_with_conditions_rejects_blank_condition() -> None:
    with pytest.raises(ValidationError):
        _decision(
            decision=CreditDecisionType.APPROVED_WITH_CONDITIONS, conditions=("   ",)
        )


def test_approved_with_conditions_accepts_conditions() -> None:
    decision = _decision(
        decision=CreditDecisionType.APPROVED_WITH_CONDITIONS,
        conditions=("Bổ sung hợp đồng bảo đảm.",),
    )
    assert decision.conditions == ("Bổ sung hợp đồng bảo đảm.",)


def test_non_conditional_decision_cannot_carry_conditions() -> None:
    with pytest.raises(ValidationError):
        _decision(
            decision=CreditDecisionType.DECLINED_BY_HUMAN,
            conditions=("Điều kiện thừa.",),
        )


def test_five_synthetic_decision_types_exist() -> None:
    assert {member.value for member in CreditDecisionType} == {
        "APPROVED_AS_PROPOSED",
        "APPROVED_WITH_CONDITIONS",
        "RETURNED_FOR_REVISION",
        "MORE_INFORMATION_REQUIRED",
        "DECLINED_BY_HUMAN",
    }


# -- snapshot hash determinism ------------------------------------------------


def test_terms_hash_is_deterministic_and_64_hex() -> None:
    terms = ApprovedTerms(
        amount=Decimal("5000000000"), currency="VND", term="12 tháng", rate=Decimal("9.5")
    )
    first = compute_terms_hash(terms)
    second = compute_terms_hash(
        ApprovedTerms(
            amount=Decimal("5000000000"),
            currency="VND",
            term="12 tháng",
            rate=Decimal("9.5"),
        )
    )
    assert first == second
    assert len(first) == 64
    assert all(character in "0123456789abcdef" for character in first)


def test_terms_hash_changes_with_any_field() -> None:
    base = ApprovedTerms(amount=Decimal("100"), currency="VND")
    assert compute_terms_hash(base) != compute_terms_hash(
        ApprovedTerms(amount=Decimal("101"), currency="VND")
    )
    assert compute_terms_hash(base) != compute_terms_hash(
        ApprovedTerms(amount=Decimal("100"), currency="USD")
    )
    assert compute_terms_hash(ApprovedTerms()) != compute_terms_hash(base)


def test_snapshot_hash_must_match_terms() -> None:
    with pytest.raises(ValidationError):
        ApprovedTermSnapshot(
            id=uuid4(),
            decision_id=uuid4(),
            case_id=CASE_ID,
            case_version=1,
            terms=ApprovedTerms(amount=Decimal("100")),
            snapshot_hash="0" * 64,
        )


def test_build_term_snapshot_produces_matching_hash() -> None:
    decision = _decision()
    terms = ApprovedTerms(amount=Decimal("250"), currency="VND")
    snapshot = build_term_snapshot(snapshot_id=uuid4(), decision=decision, terms=terms)
    assert snapshot.snapshot_hash == compute_terms_hash(terms)
    assert snapshot.decision_id == decision.id
    assert snapshot.case_version == decision.case_version


# -- decision / snapshot pairing ----------------------------------------------


def test_declined_decision_forbids_a_snapshot() -> None:
    decision = _decision(decision=CreditDecisionType.DECLINED_BY_HUMAN)
    snapshot = build_term_snapshot(
        snapshot_id=uuid4(), decision=decision, terms=ApprovedTerms(amount=Decimal("1"))
    )
    with pytest.raises(ValueError, match="cannot carry an approved-term snapshot"):
        assert_snapshot_matches_decision(decision=decision, snapshot=snapshot)


@pytest.mark.parametrize(
    "decision_type",
    [
        CreditDecisionType.RETURNED_FOR_REVISION,
        CreditDecisionType.MORE_INFORMATION_REQUIRED,
    ],
)
def test_returned_and_more_info_forbid_a_snapshot(
    decision_type: CreditDecisionType,
) -> None:
    decision = _decision(decision=decision_type)
    snapshot = build_term_snapshot(
        snapshot_id=uuid4(), decision=decision, terms=ApprovedTerms()
    )
    with pytest.raises(ValueError):
        assert_snapshot_matches_decision(decision=decision, snapshot=snapshot)


def test_approval_allows_a_snapshot() -> None:
    decision = _decision(decision=CreditDecisionType.APPROVED_AS_PROPOSED)
    snapshot = build_term_snapshot(
        snapshot_id=uuid4(), decision=decision, terms=ApprovedTerms(amount=Decimal("9"))
    )
    assert_snapshot_matches_decision(decision=decision, snapshot=snapshot)


def test_approval_allows_no_snapshot() -> None:
    decision = _decision(decision=CreditDecisionType.APPROVED_AS_PROPOSED)
    assert_snapshot_matches_decision(decision=decision, snapshot=None)


def test_snapshot_for_a_different_decision_is_rejected() -> None:
    decision = _decision()
    other = _decision()
    snapshot = build_term_snapshot(
        snapshot_id=uuid4(), decision=other, terms=ApprovedTerms()
    )
    with pytest.raises(ValueError, match="does not reference this decision"):
        assert_snapshot_matches_decision(decision=decision, snapshot=snapshot)


def test_snapshot_for_a_different_case_version_is_rejected() -> None:
    decision = _decision(case_version=2)
    snapshot = ApprovedTermSnapshot(
        id=uuid4(),
        decision_id=decision.id,
        case_id=CASE_ID,
        case_version=1,
        terms=ApprovedTerms(),
        snapshot_hash=compute_terms_hash(ApprovedTerms()),
    )
    with pytest.raises(ValueError, match="same case version"):
        assert_snapshot_matches_decision(decision=decision, snapshot=snapshot)
