"""Deterministic pre-analysis tests (application/risk_review/analysis.py).

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture case
belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from creditops.application.ports.risk_review import CheckerEvidenceView, EvidenceFact, OpenGapRecord
from creditops.application.risk_review.analysis import (
    compute_deterministic_pre_analysis,
    cross_check_citation_grounding,
    detect_unaddressed_assumptions,
    flag_low_confidence_findings,
    recompute_visibility,
    visibility_challenges_from,
)
from creditops.domain.legal import (
    AssessmentSection as LegalAssessmentSection,
)
from creditops.domain.legal import (
    CollateralReviewSection,
    ConfidenceLevel,
    GapBlockingLevel,
    LegalAssessmentProvenance,
    LegalComplianceAssessment,
    OwnershipConsistencySection,
)
from creditops.domain.legal import ConfirmedFactCitation as LegalConfirmedFactCitation
from creditops.domain.legal import Finding as LegalFinding
from creditops.domain.risk_review import ChallengeType
from creditops.domain.underwriting import (
    AssessmentProvenance,
    AssessmentSection,
    AssumptionItem,
    ConfirmedFactCitation,
    EvidenceGapItem,
    Finding,
    MitigantItem,
    ProposedStructureSection,
    RepaymentSourceSection,
    RiskItem,
    UnderwritingAssessment,
)

NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
CASE_ID = uuid4()
LIVE_FACT_ID = uuid4()
DEAD_FACT_ID = uuid4()  # cited by a maker but no longer in the live evidence view


def _uw_provenance() -> AssessmentProvenance:
    return AssessmentProvenance(
        case_id=CASE_ID,
        case_version=1,
        execution_id=uuid4(),
        task_id=uuid4(),
        prompt_version="underwriting-prompt-v1",
        model_id="synthetic-model",
        endpoint_id="synthetic-endpoint",
        evidence_view_built_at=NOW,
        created_at=NOW,
    )


def _finding(
    fact_id: object = LIVE_FACT_ID, confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
) -> Finding:
    return Finding(
        statement_vi="Phat hien mo phong.",
        citations=(ConfirmedFactCitation(confirmed_fact_id=fact_id),),  # type: ignore[arg-type]
        confidence=confidence,
    )


def build_underwriting(**overrides: Any) -> UnderwritingAssessment:
    base: dict[str, Any] = {
        "id": uuid4(),
        "provenance": _uw_provenance(),
        "business": AssessmentSection(findings=(_finding(),)),
        "financial": AssessmentSection(findings=(_finding(),)),
        "cash_flow": AssessmentSection(findings=(_finding(),)),
        "repayment_source": RepaymentSourceSection(findings=(_finding(),)),
        "proposed_structure": ProposedStructureSection(
            instrument_vi="Han muc von luu dong (de xuat so bo)",
            findings=(_finding(),),
        ),
        "risks": (),
        "mitigants": (),
        "assumptions": (),
        "evidence_gaps": (),
    }
    base.update(overrides)
    return UnderwritingAssessment(**base)


def _legal_provenance() -> LegalAssessmentProvenance:
    return LegalAssessmentProvenance(
        case_id=CASE_ID,
        case_version=1,
        execution_id=uuid4(),
        task_id=uuid4(),
        prompt_version="legal-prompt-v1",
        model_id="synthetic-model",
        endpoint_id="synthetic-endpoint",
        evidence_view_built_at=NOW,
        created_at=NOW,
    )


def _legal_finding() -> LegalFinding:
    return LegalFinding(
        statement_vi="Phat hien phap ly mo phong.",
        citations=(LegalConfirmedFactCitation(confirmed_fact_id=LIVE_FACT_ID),),
        confidence=ConfidenceLevel.MEDIUM,
    )


def build_legal(**overrides: Any) -> LegalComplianceAssessment:
    base: dict[str, Any] = {
        "id": uuid4(),
        "provenance": _legal_provenance(),
        "legal_entity_review": LegalAssessmentSection(findings=(_legal_finding(),)),
        "authority_signatory_review": LegalAssessmentSection(findings=(_legal_finding(),)),
        "ownership_consistency": OwnershipConsistencySection(findings=(_legal_finding(),)),
        "collateral_review": CollateralReviewSection(
            ownership_evidence_findings=(_legal_finding(),)
        ),
        "exceptions": (),
        "assumptions": (),
        "evidence_gaps": (),
    }
    base.update(overrides)
    return LegalComplianceAssessment(**base)


def checker_view(fact_ids: tuple[object, ...] = (LIVE_FACT_ID,)) -> CheckerEvidenceView:
    return CheckerEvidenceView(
        case_id=CASE_ID,
        case_version=1,
        built_at=NOW,
        confirmed_facts=tuple(
            EvidenceFact(
                confirmed_fact_id=fact_id,  # type: ignore[arg-type]
                field_key="financials.revenue",
                value="1000",
                document_version_id=uuid4(),
            )
            for fact_id in fact_ids
        ),
    )


def test_broken_confirmed_fact_citation_is_flagged() -> None:
    underwriting = build_underwriting(
        financial=AssessmentSection(findings=(_finding(fact_id=DEAD_FACT_ID),))
    )
    legal = build_legal()
    challenges = cross_check_citation_grounding(
        underwriting=underwriting, legal=legal, checker_view=checker_view()
    )
    assert len(challenges) == 1
    assert challenges[0].challenge_type == ChallengeType.OTHER_CONCERN
    assert challenges[0].target.section_path == "financial.findings[0]"


def test_citations_that_still_resolve_are_not_flagged() -> None:
    underwriting = build_underwriting()
    legal = build_legal()
    challenges = cross_check_citation_grounding(
        underwriting=underwriting, legal=legal, checker_view=checker_view()
    )
    assert challenges == ()


def test_blocking_gap_missing_from_open_registry_is_auto_raised() -> None:
    underwriting = build_underwriting(
        evidence_gaps=(
            EvidenceGapItem(
                missing_information_vi="Thieu bao cao tai chinh nam gan nhat.",
                why_needed_vi="Can de tinh toan chi so.",
                blocking_level=GapBlockingLevel.BLOCKING,
            ),
        )
    )
    legal = build_legal()

    checks, challenges = recompute_visibility(
        underwriting=underwriting, legal=legal, open_gaps=()
    )

    assert len(checks.blocking_gaps) == 1
    assert checks.blocking_gaps[0].still_visible is False
    assert len(challenges) == 1
    assert challenges[0].challenge_type == ChallengeType.GAP_VISIBILITY


def test_blocking_gap_present_in_open_registry_is_not_flagged() -> None:
    text = "Thieu bao cao tai chinh nam gan nhat."
    underwriting = build_underwriting(
        evidence_gaps=(
            EvidenceGapItem(
                missing_information_vi=text,
                why_needed_vi="Can de tinh toan chi so.",
                blocking_level=GapBlockingLevel.BLOCKING,
            ),
        )
    )
    legal = build_legal()
    open_gaps = (
        OpenGapRecord(
            gap_id=uuid4(),
            missing_information_vi=text,
            blocking_level=GapBlockingLevel.BLOCKING,
            status="PROVISIONAL",
        ),
    )

    checks, challenges = recompute_visibility(
        underwriting=underwriting, legal=legal, open_gaps=open_gaps
    )

    assert checks.blocking_gaps[0].still_visible is True
    assert challenges == ()


def test_conditional_gap_is_not_part_of_the_blocking_visibility_check() -> None:
    underwriting = build_underwriting(
        evidence_gaps=(
            EvidenceGapItem(
                missing_information_vi="Chi tiet nho, khong chan.",
                why_needed_vi="Tham khao them.",
                blocking_level=GapBlockingLevel.CLARIFICATION,
            ),
        )
    )
    legal = build_legal()
    checks, challenges = recompute_visibility(underwriting=underwriting, legal=legal, open_gaps=())
    assert checks.blocking_gaps == ()
    assert challenges == ()


def test_visibility_challenges_from_is_directly_testable_against_a_broken_struct() -> None:
    # Defense-in-depth: feed a deliberately-incomplete VisibilityChecks
    # directly (simulating a hypothetical merge bug that dropped an item's
    # visibility) and prove the auto-raise mechanism fires on its own.
    from creditops.domain.risk_review import MakerSource, VisibilityChecks, VisibilityGapItem

    broken = VisibilityChecks(
        blocking_gaps=(
            VisibilityGapItem(
                source=MakerSource.CREDIT_UNDERWRITING,
                source_assessment_id=uuid4(),
                missing_information_vi="mat tich",
                blocking_level=GapBlockingLevel.BLOCKING,
                still_visible=False,
            ),
        )
    )
    challenges = visibility_challenges_from(
        broken, underwriting_id=uuid4(), legal_id=uuid4()
    )
    assert len(challenges) == 1
    assert challenges[0].challenge_type == ChallengeType.GAP_VISIBILITY


def test_unaddressed_assumption_is_flagged() -> None:
    orphan_citation = ConfirmedFactCitation(confirmed_fact_id=LIVE_FACT_ID)
    underwriting = build_underwriting(
        assumptions=(
            AssumptionItem(
                statement_vi="Gia nong san on dinh.",
                rationale_vi="Chua co du kien xac nhan gia ky toi.",
                basis_citations=(orphan_citation,),
            ),
        ),
        risks=(),
        mitigants=(),
    )
    challenges = detect_unaddressed_assumptions(underwriting)
    assert len(challenges) == 1
    assert challenges[0].challenge_type.value == "UNSUPPORTED_ASSUMPTION"
    assert challenges[0].target.section_path == "assumptions[0]"


def test_assumption_referenced_by_a_risk_is_not_flagged() -> None:
    shared_citation = ConfirmedFactCitation(confirmed_fact_id=LIVE_FACT_ID)
    underwriting = build_underwriting(
        assumptions=(
            AssumptionItem(
                statement_vi="Gia nong san on dinh.",
                rationale_vi="Ly do.",
                basis_citations=(shared_citation,),
            ),
        ),
        risks=(
            RiskItem(
                risk_id="rui-ro-gia",
                description_vi="Rui ro bien dong gia.",
                citations=(shared_citation,),
                confidence=ConfidenceLevel.MEDIUM,
            ),
        ),
    )
    challenges = detect_unaddressed_assumptions(underwriting)
    assert challenges == ()


def test_assumption_with_no_basis_citations_is_skipped_not_flagged() -> None:
    underwriting = build_underwriting(
        assumptions=(
            AssumptionItem(statement_vi="Gia dinh khong co can cu.", rationale_vi="Ly do."),
        )
    )
    challenges = detect_unaddressed_assumptions(underwriting)
    assert challenges == ()


def test_low_confidence_finding_is_flagged() -> None:
    underwriting = build_underwriting(
        business=AssessmentSection(
            findings=(_finding(confidence=ConfidenceLevel.LOW),)
        )
    )
    legal = build_legal()
    challenges = flag_low_confidence_findings(underwriting=underwriting, legal=legal)
    assert len(challenges) == 1
    assert challenges[0].target.section_path == "business.findings[0]"


def test_medium_and_high_confidence_findings_are_not_flagged() -> None:
    underwriting = build_underwriting()  # MEDIUM confidence by default
    legal = build_legal()
    challenges = flag_low_confidence_findings(underwriting=underwriting, legal=legal)
    assert challenges == ()


def test_mitigant_low_confidence_is_flagged_too() -> None:
    underwriting = build_underwriting(
        risks=(
            RiskItem(
                risk_id="rui-ro-tap-trung",
                description_vi="Tap trung khach hang.",
                citations=(ConfirmedFactCitation(confirmed_fact_id=LIVE_FACT_ID),),
                confidence=ConfidenceLevel.MEDIUM,
            ),
        ),
        mitigants=(
            MitigantItem(
                risk_id="rui-ro-tap-trung",
                description_vi="Da dang hoa khach hang.",
                citations=(ConfirmedFactCitation(confirmed_fact_id=LIVE_FACT_ID),),
                confidence=ConfidenceLevel.LOW,
            ),
        ),
    )
    legal = build_legal()
    challenges = flag_low_confidence_findings(underwriting=underwriting, legal=legal)
    assert any(c.target.section_path == "mitigants[0]" for c in challenges)


def test_compute_deterministic_pre_analysis_merges_every_category() -> None:
    underwriting = build_underwriting(
        financial=AssessmentSection(findings=(_finding(fact_id=DEAD_FACT_ID),)),
        evidence_gaps=(
            EvidenceGapItem(
                missing_information_vi="Thieu bao cao tai chinh.",
                why_needed_vi="Can de tinh toan.",
                blocking_level=GapBlockingLevel.BLOCKING,
            ),
        ),
        assumptions=(
            AssumptionItem(
                statement_vi="Gia on dinh.",
                rationale_vi="Ly do.",
                basis_citations=(ConfirmedFactCitation(confirmed_fact_id=LIVE_FACT_ID),),
            ),
        ),
    )
    legal = build_legal()

    pre_analysis = compute_deterministic_pre_analysis(
        underwriting=underwriting,
        legal=legal,
        checker_view=checker_view(),
        open_gaps=(),
    )

    types = {c.challenge_type for c in pre_analysis.all_challenges}
    assert ChallengeType.OTHER_CONCERN in types  # broken citation
    assert ChallengeType.GAP_VISIBILITY in types  # missing blocking gap
    assert ChallengeType.UNSUPPORTED_ASSUMPTION in types  # unaddressed assumption
    assert len(pre_analysis.all_challenges) == (
        len(pre_analysis.citation_grounding_challenges)
        + len(pre_analysis.visibility_challenges)
        + len(pre_analysis.unaddressed_assumption_challenges)
        + len(pre_analysis.low_confidence_challenges)
    )
