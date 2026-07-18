"""Domain tests for the stage-7 deterministic notification template + records.

Covers: the deterministic render embedding the canonical synthetic notice and the
fixed not-a-disbursement disclaimer; content-hash stability and sensitivity;
approval-only rendering (fail closed on a non-approval decision); the frozen
draft/receipt invariants (hash-matches-content, mandatory sentences, labelled
mock channel).  All identifiers are synthetic.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from creditops.domain.credit_decisions import (
    ApprovedTerms,
    CreditDecisionType,
    HumanCreditDecision,
)
from creditops.domain.notifications import (
    MOCK_DELIVERY_CHANNEL,
    NOT_A_DISBURSEMENT_NOTICE_VI,
    CommunicationReceipt,
    CreditNotificationDraft,
    build_notification_draft,
    compute_content_hash,
    render_notification_content_vi,
)
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_VI

CASE_ID = UUID("10000000-0000-0000-0000-0000000000f7")
DECIDER = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
CREATOR = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _decision(
    *,
    decision: CreditDecisionType = CreditDecisionType.APPROVED_AS_PROPOSED,
    conditions: tuple[str, ...] = (),
) -> HumanCreditDecision:
    return HumanCreditDecision(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=1,
        decision=decision,
        rationale_vi="Đã rà soát hồ sơ (dữ liệu mô phỏng).",
        decided_by=DECIDER,
        decided_by_role="CREDIT_APPROVER",
        conditions=conditions,
    )


def test_render_embeds_both_mandatory_sentences() -> None:
    content = render_notification_content_vi(
        decision=_decision(),
        terms=ApprovedTerms(amount=Decimal("5000000000"), currency="VND"),
    )
    assert NOT_A_DISBURSEMENT_NOTICE_VI in content
    assert SYNTHETIC_NOTICE_VI in content
    assert "5000000000 VND" in content


def test_render_lists_conditions_for_conditional_approval() -> None:
    content = render_notification_content_vi(
        decision=_decision(
            decision=CreditDecisionType.APPROVED_WITH_CONDITIONS,
            conditions=("Bổ sung hợp đồng bảo đảm.", "Mở tài khoản tại ngân hàng."),
        ),
        terms=None,
    )
    assert "- Bổ sung hợp đồng bảo đảm." in content
    assert "- Mở tài khoản tại ngân hàng." in content
    # Missing optional terms are labelled, never invented.
    assert "Không cung cấp" in content


def test_render_is_deterministic_and_hash_is_stable() -> None:
    decision = _decision()
    terms = ApprovedTerms(amount=Decimal("1000"), currency="VND", term="12 tháng")
    first = render_notification_content_vi(decision=decision, terms=terms)
    second = render_notification_content_vi(decision=decision, terms=terms)
    assert first == second
    assert compute_content_hash(first) == compute_content_hash(second)


def test_hash_is_sensitive_to_content_change() -> None:
    decision = _decision()
    with_amount = render_notification_content_vi(
        decision=decision, terms=ApprovedTerms(amount=Decimal("1000"))
    )
    without_amount = render_notification_content_vi(decision=decision, terms=None)
    assert compute_content_hash(with_amount) != compute_content_hash(without_amount)


@pytest.mark.parametrize(
    "decision_type",
    [
        CreditDecisionType.DECLINED_BY_HUMAN,
        CreditDecisionType.RETURNED_FOR_REVISION,
        CreditDecisionType.MORE_INFORMATION_REQUIRED,
    ],
)
def test_render_fails_closed_on_non_approval(
    decision_type: CreditDecisionType,
) -> None:
    with pytest.raises(ValueError, match="does not permit"):
        render_notification_content_vi(
            decision=_decision(decision=decision_type), terms=None
        )


def test_build_notification_draft_produces_matching_hash() -> None:
    decision = _decision()
    draft = build_notification_draft(
        draft_id=uuid4(),
        decision=decision,
        terms=ApprovedTerms(amount=Decimal("1000"), currency="VND"),
        created_by=CREATOR,
    )
    assert draft.decision_id == decision.id
    assert draft.case_id == decision.case_id
    assert draft.case_version == decision.case_version
    assert draft.content_hash == compute_content_hash(draft.content_vi)


def test_draft_rejects_hash_content_mismatch() -> None:
    content = render_notification_content_vi(decision=_decision(), terms=None)
    with pytest.raises(ValidationError):
        CreditNotificationDraft(
            id=uuid4(),
            case_id=CASE_ID,
            case_version=1,
            decision_id=uuid4(),
            content_vi=content,
            content_hash="0" * 64,  # not the hash of content
            created_by=CREATOR,
        )


def test_draft_rejects_content_missing_disclaimer() -> None:
    content = "THÔNG BÁO TÍN DỤNG thiếu câu bắt buộc."
    with pytest.raises(ValidationError):
        CreditNotificationDraft(
            id=uuid4(),
            case_id=CASE_ID,
            case_version=1,
            decision_id=uuid4(),
            content_vi=content,
            content_hash=compute_content_hash(content),
            created_by=CREATOR,
        )


def test_receipt_rejects_non_mock_channel() -> None:
    with pytest.raises(ValidationError):
        CommunicationReceipt(
            id=uuid4(),
            draft_id=uuid4(),
            delivered_via="EMAIL",
            content_hash="a" * 64,
            recorded_by=CREATOR,
        )


def test_receipt_accepts_labelled_mock_channel() -> None:
    receipt = CommunicationReceipt(
        id=uuid4(),
        draft_id=uuid4(),
        delivered_via=MOCK_DELIVERY_CHANNEL,
        content_hash="a" * 64,
        receipt_note_vi="Đã giao mock (dữ liệu mô phỏng).",
        recorded_by=CREATOR,
    )
    assert receipt.delivered_via == MOCK_DELIVERY_CHANNEL
