"""Unit tests for the stage-8 contract-package domain.

Covers the two spec-critical pure behaviours: the deterministic template
renderer (byte-identical output for equal inputs, canonical notice + mock label
embedded, no clause invention) and the material-change detector (pure hash
comparison).  All data is synthetic and created solely for demonstration.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from creditops.domain.contract_packages import (
    MOCK_CONTRACT_LABEL_VI,
    ContractDecisionView,
    ContractPackage,
    ContractPackageState,
    ContractRedline,
    ContractSignatureEvidence,
    SignatureEvidenceKind,
    assert_renderable_decision,
    compute_content_hash,
    detect_material_change,
    render_contract_content_vi,
)
from creditops.domain.credit_decisions import ApprovedTerms, CreditDecisionType
from creditops.domain.synthetic_notice import SYNTHETIC_NOTICE_VI

CASE_ID = uuid4()
DECISION_ID = uuid4()
ACTOR = uuid4()


def _terms() -> ApprovedTerms:
    return ApprovedTerms(
        amount=Decimal("5000000000"), currency="VND", term="12 tháng", rate=Decimal("9.5")
    )


def _decision(
    *, decision_type: CreditDecisionType = CreditDecisionType.APPROVED_WITH_CONDITIONS
) -> ContractDecisionView:
    return ContractDecisionView(
        decision_type=decision_type,
        rationale_vi="Phê duyệt có điều kiện.",
        conditions=("Bổ sung hợp đồng bảo đảm.",),
    )


# -- deterministic rendering -------------------------------------------------


def test_render_is_byte_identical_for_equal_inputs() -> None:
    first = render_contract_content_vi(_decision(), _terms())
    second = render_contract_content_vi(_decision(), _terms())
    assert first == second


def test_render_embeds_synthetic_notice_and_mock_label() -> None:
    content = render_contract_content_vi(_decision(), _terms())
    assert SYNTHETIC_NOTICE_VI in content
    # The fixed mock label opens AND closes the document.
    assert content.startswith(MOCK_CONTRACT_LABEL_VI)
    assert content.rstrip().endswith(MOCK_CONTRACT_LABEL_VI)
    assert content.count(MOCK_CONTRACT_LABEL_VI) == 2


def test_render_prints_terms_and_conditions_verbatim() -> None:
    content = render_contract_content_vi(_decision(), _terms())
    assert "5000000000" in content
    assert "VND" in content
    assert "12 tháng" in content
    assert "9.5" in content
    assert "Bổ sung hợp đồng bảo đảm." in content
    assert "Phê duyệt có điều kiện." in content


def test_render_marks_unspecified_terms_without_inventing_values() -> None:
    bare = ApprovedTerms()
    content = render_contract_content_vi(
        ContractDecisionView(
            decision_type=CreditDecisionType.APPROVED_AS_PROPOSED,
            rationale_vi="Phê duyệt theo đề xuất.",
        ),
        bare,
    )
    # No approved-term field was supplied; each renders the fixed placeholder,
    # never a fabricated value.
    assert content.count("(không xác định)") == 4
    assert "(không có điều kiện)" in content


def test_render_differs_when_terms_differ() -> None:
    a = render_contract_content_vi(_decision(), _terms())
    b = render_contract_content_vi(
        _decision(),
        ApprovedTerms(amount=Decimal("1"), currency="VND", term="6 tháng", rate=Decimal("8")),
    )
    assert a != b


def test_assert_renderable_rejects_non_approval_decisions() -> None:
    for decision_type in (
        CreditDecisionType.DECLINED_BY_HUMAN,
        CreditDecisionType.RETURNED_FOR_REVISION,
        CreditDecisionType.MORE_INFORMATION_REQUIRED,
    ):
        with pytest.raises(ValueError):
            assert_renderable_decision(decision_type)


def test_assert_renderable_accepts_approvals() -> None:
    assert_renderable_decision(CreditDecisionType.APPROVED_AS_PROPOSED)
    assert_renderable_decision(CreditDecisionType.APPROVED_WITH_CONDITIONS)


# -- material-change detector ------------------------------------------------


def test_detect_material_change_true_on_hash_mismatch() -> None:
    assert detect_material_change("a" * 64, "b" * 64) is True


def test_detect_material_change_false_on_equal_hashes() -> None:
    assert detect_material_change("a" * 64, "a" * 64) is False


# -- value-object invariants -------------------------------------------------


def test_package_rejects_content_hash_mismatch() -> None:
    content = "noi dung hop dong mo phong"
    with pytest.raises(ValueError):
        ContractPackage(
            id=uuid4(),
            case_id=CASE_ID,
            case_version=1,
            decision_id=DECISION_ID,
            term_snapshot_hash="a" * 64,
            content_vi=content,
            content_hash="0" * 64,  # wrong hash
            package_version=1,
            state=ContractPackageState.DRAFT,
            created_by=ACTOR,
        )


def test_package_accepts_matching_content_hash() -> None:
    content = "noi dung hop dong mo phong"
    package = ContractPackage(
        id=uuid4(),
        case_id=CASE_ID,
        case_version=1,
        decision_id=DECISION_ID,
        term_snapshot_hash="a" * 64,
        content_vi=content,
        content_hash=compute_content_hash(content),
        package_version=1,
        state=ContractPackageState.DRAFT,
        created_by=ACTOR,
    )
    assert package.state is ContractPackageState.DRAFT


def test_redline_rejects_content_hash_mismatch() -> None:
    with pytest.raises(ValueError):
        ContractRedline(
            id=uuid4(),
            package_id=uuid4(),
            redline_version=1,
            change_note_vi="Sua dieu khoan",
            changed_content_vi="noi dung moi",
            changed_content_hash="0" * 64,
            created_by=ACTOR,
        )


def test_signature_evidence_is_mock_only_and_requires_signers() -> None:
    evidence = ContractSignatureEvidence(
        id=uuid4(),
        package_id=uuid4(),
        signer_names=("Nguyen Van A (mo phong)",),
        evidence_note_vi="Bang chung ky mo phong.",
        recorded_by=ACTOR,
    )
    assert evidence.kind is SignatureEvidenceKind.MOCK_SIGNATURE

    with pytest.raises(ValueError):
        ContractSignatureEvidence(
            id=uuid4(),
            package_id=uuid4(),
            signer_names=("   ",),  # blank signer name
            recorded_by=ACTOR,
        )
