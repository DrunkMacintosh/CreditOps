"""Domain schema tests for the Independent Risk Review Agent (Checker).

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture case
belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from creditops.domain.risk_review import (
    RISK_REVIEW_AGENT_ROLE,
    Challenge,
    ChallengeSeverity,
    ChallengeType,
    ConfidenceLevel,
    ConfirmedFactCitation,
    MakerFindingCitation,
    MakerFindingRef,
    MakerReviewedRef,
    MakerSource,
    RaisedBy,
    RiskReviewAssessment,
    RiskReviewProvenance,
    VisibilityChecks,
)

NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
CASE_ID = uuid4()
UW_ASSESSMENT_ID = uuid4()
UW_EXECUTION_ID = uuid4()
LEGAL_ASSESSMENT_ID = uuid4()
LEGAL_EXECUTION_ID = uuid4()


def _reviewed_refs() -> tuple[MakerReviewedRef, ...]:
    return (
        MakerReviewedRef(
            maker_source=MakerSource.CREDIT_UNDERWRITING,
            assessment_id=UW_ASSESSMENT_ID,
            execution_id=UW_EXECUTION_ID,
        ),
        MakerReviewedRef(
            maker_source=MakerSource.LEGAL_COMPLIANCE_COLLATERAL,
            assessment_id=LEGAL_ASSESSMENT_ID,
            execution_id=LEGAL_EXECUTION_ID,
        ),
    )


def _provenance(*, execution_id: object = None) -> RiskReviewProvenance:
    return RiskReviewProvenance(
        case_id=CASE_ID,
        case_version=1,
        execution_id=execution_id or uuid4(),
        task_id=uuid4(),
        prompt_version="risk-review-prompt-v1",
        model_id="synthetic-model",
        endpoint_id="synthetic-endpoint",
        evidence_view_built_at=NOW,
        created_at=NOW,
        maker_assessments_reviewed=_reviewed_refs(),
    )


def _target() -> MakerFindingRef:
    return MakerFindingRef(
        maker_source=MakerSource.CREDIT_UNDERWRITING,
        maker_assessment_id=UW_ASSESSMENT_ID,
        section_path="risks[0]",
    )


def _challenge() -> Challenge:
    return Challenge(
        id=uuid4(),
        target=_target(),
        challenge_type=ChallengeType.UNSUPPORTED_ASSUMPTION,
        statement_vi="Gia dinh khong duoc dan chieu boi du kien nao.",
        citations=(ConfirmedFactCitation(confirmed_fact_id=uuid4()),),
        severity=ChallengeSeverity.HIGH,
        confidence=ConfidenceLevel.MEDIUM,
    )


def test_agent_role_is_pinned() -> None:
    assert RISK_REVIEW_AGENT_ROLE == "INDEPENDENT_RISK_REVIEW"


def test_minimal_assessment_with_no_findings_is_valid() -> None:
    assessment = RiskReviewAssessment(
        id=uuid4(),
        provenance=_provenance(),
        visibility_checks=VisibilityChecks(),
    )
    assert assessment.challenges == ()
    assert assessment.provenance.agent_role == "INDEPENDENT_RISK_REVIEW"


def test_challenge_without_citations_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Challenge(
            id=uuid4(),
            target=_target(),
            challenge_type=ChallengeType.OMITTED_RISK,
            statement_vi="khong co can cu",
            citations=(),
            severity=ChallengeSeverity.LOW,
            confidence=ConfidenceLevel.LOW,
        )


def test_challenge_target_must_reference_a_reviewed_maker_assessment() -> None:
    stray = Challenge(
        id=uuid4(),
        target=MakerFindingRef(
            maker_source=MakerSource.CREDIT_UNDERWRITING,
            maker_assessment_id=uuid4(),  # not in maker_assessments_reviewed
            section_path="risks[0]",
        ),
        challenge_type=ChallengeType.OTHER_CONCERN,
        statement_vi="tham chieu sai",
        citations=(ConfirmedFactCitation(confirmed_fact_id=uuid4()),),
        severity=ChallengeSeverity.LOW,
        confidence=ConfidenceLevel.LOW,
    )
    with pytest.raises(ValidationError, match="was not.*reviewed"):
        RiskReviewAssessment(
            id=uuid4(),
            provenance=_provenance(),
            challenges=(stray,),
            visibility_checks=VisibilityChecks(),
        )


def test_maker_finding_citation_must_also_resolve() -> None:
    poisoned = Challenge(
        id=uuid4(),
        target=_target(),
        challenge_type=ChallengeType.INADEQUATE_MITIGANT,
        statement_vi="trich dan sai muc tieu",
        citations=(
            MakerFindingCitation(
                ref=MakerFindingRef(
                    maker_source=MakerSource.LEGAL_COMPLIANCE_COLLATERAL,
                    maker_assessment_id=uuid4(),
                    section_path="exceptions[0]",
                )
            ),
        ),
        severity=ChallengeSeverity.MEDIUM,
        confidence=ConfidenceLevel.MEDIUM,
    )
    with pytest.raises(ValidationError, match="MAKER_FINDING citation"):
        RiskReviewAssessment(
            id=uuid4(),
            provenance=_provenance(),
            challenges=(poisoned,),
            visibility_checks=VisibilityChecks(),
        )


def test_requires_both_maker_sources_not_just_two_of_the_same() -> None:
    duplicate_refs = (
        MakerReviewedRef(
            maker_source=MakerSource.CREDIT_UNDERWRITING,
            assessment_id=UW_ASSESSMENT_ID,
            execution_id=UW_EXECUTION_ID,
        ),
        MakerReviewedRef(
            maker_source=MakerSource.CREDIT_UNDERWRITING,
            assessment_id=uuid4(),
            execution_id=uuid4(),
        ),
    )
    with pytest.raises(ValidationError, match="exactly one"):
        RiskReviewAssessment(
            id=uuid4(),
            provenance=RiskReviewProvenance(
                case_id=CASE_ID,
                case_version=1,
                execution_id=uuid4(),
                task_id=uuid4(),
                prompt_version="risk-review-prompt-v1",
                model_id="synthetic-model",
                endpoint_id="synthetic-endpoint",
                evidence_view_built_at=NOW,
                created_at=NOW,
                maker_assessments_reviewed=duplicate_refs,
            ),
            visibility_checks=VisibilityChecks(),
        )


def test_provenance_requires_at_least_two_maker_refs() -> None:
    with pytest.raises(ValidationError):
        RiskReviewProvenance(
            case_id=CASE_ID,
            case_version=1,
            execution_id=uuid4(),
            task_id=uuid4(),
            prompt_version="risk-review-prompt-v1",
            model_id="synthetic-model",
            endpoint_id="synthetic-endpoint",
            evidence_view_built_at=NOW,
            created_at=NOW,
            maker_assessments_reviewed=(
                MakerReviewedRef(
                    maker_source=MakerSource.CREDIT_UNDERWRITING,
                    assessment_id=UW_ASSESSMENT_ID,
                    execution_id=UW_EXECUTION_ID,
                ),
            ),
        )


def test_same_execution_guard_rejects_checker_id_equal_to_maker_id() -> None:
    # (e) maker-checker separation enforced at the schema level: the checker's
    # own execution id must never equal a reviewed maker's execution id.
    with pytest.raises(ValidationError, match="maker-checker separation"):
        RiskReviewAssessment(
            id=uuid4(),
            provenance=_provenance(execution_id=UW_EXECUTION_ID),
            visibility_checks=VisibilityChecks(),
        )


def test_forbidden_decision_field_rejected_at_import_time() -> None:
    from pydantic import BaseModel, ConfigDict

    from creditops.domain.risk_review import _assert_no_forbidden_fields

    class _PoisonedResolve(BaseModel):
        model_config = ConfigDict(frozen=True, extra="forbid")
        resolve: bool = False

    with pytest.raises(AssertionError):
        _assert_no_forbidden_fields(_PoisonedResolve, set())


def test_raised_by_defaults_to_llm_and_deterministic_is_representable() -> None:
    assert _challenge().raised_by == RaisedBy.LLM
    deterministic = _challenge().model_copy(update={"raised_by": RaisedBy.DETERMINISTIC})
    assert deterministic.raised_by == RaisedBy.DETERMINISTIC


def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError):
        RiskReviewAssessment.model_validate(
            {
                "id": str(uuid4()),
                "provenance": _provenance().model_dump(mode="json"),
                "visibility_checks": {},
                "approved": True,
            }
        )
