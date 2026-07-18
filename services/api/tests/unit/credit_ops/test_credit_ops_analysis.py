"""Deterministic pre-analysis tests (application/credit_ops/analysis.py).

Requirements exercised: (a) a missing upstream artifact is reported MISSING
and the package skeleton still assembles with the absence recorded; (b) the
consolidated provenance index covers every upstream assessment id; drafted
document requests consolidate only open FORMAL/PROVISIONAL gaps and start
PENDING_APPROVAL; proposed actions are DRAFT-only.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture case
belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from creditops.application.credit_ops.analysis import (
    compute_deterministic_package,
    consolidate_document_requests,
    consolidate_evidence,
)
from creditops.application.credit_ops.evidence import risk_review_target_paths
from creditops.application.ports.credit_ops import (
    ChallengeDispositionSummary,
    CreditOpsUpstreamView,
    OpenGapRecord,
)
from creditops.domain.credit_ops import (
    ChecklistItemStatus,
    DocumentRequestApprovalStatus,
    ProposedActionExecutionStatus,
    ProposedActionType,
    UpstreamArtifactKind,
)
from creditops.domain.legal import (
    AssessmentSection as LegalAssessmentSection,
)
from creditops.domain.legal import (
    CollateralReviewSection,
    ConfidenceLevel,
    LegalAssessmentProvenance,
    LegalComplianceAssessment,
    OwnershipConsistencySection,
)
from creditops.domain.legal import ConfirmedFactCitation as LegalConfirmedFactCitation
from creditops.domain.legal import Finding as LegalFinding
from creditops.domain.risk_review import (
    Challenge,
    ChallengeSeverity,
    ChallengeType,
    MakerFindingCitation,
    MakerFindingRef,
    MakerReviewedRef,
    MakerSource,
    RiskReviewAssessment,
    RiskReviewProvenance,
    VisibilityChecks,
)
from creditops.domain.underwriting import (
    AssessmentProvenance,
    AssessmentSection,
    ConfirmedFactCitation,
    Finding,
    GapBlockingLevel,
    ProposedStructureSection,
    RepaymentSourceSection,
    UnderwritingAssessment,
)

NOW = datetime(2026, 7, 18, 11, 30, tzinfo=UTC)
CASE_ID = uuid4()
FACT_ID = uuid4()


def _finding() -> Finding:
    return Finding(
        statement_vi="Phat hien mo phong.",
        citations=(ConfirmedFactCitation(confirmed_fact_id=FACT_ID),),
        confidence=ConfidenceLevel.MEDIUM,
    )


def build_underwriting() -> UnderwritingAssessment:
    return UnderwritingAssessment(
        id=uuid4(),
        provenance=AssessmentProvenance(
            case_id=CASE_ID,
            case_version=1,
            execution_id=uuid4(),
            task_id=uuid4(),
            prompt_version="underwriting-prompt-v1",
            model_id="synthetic-model",
            endpoint_id="synthetic-endpoint",
            evidence_view_built_at=NOW,
            created_at=NOW,
        ),
        business=AssessmentSection(findings=(_finding(),)),
        financial=AssessmentSection(findings=(_finding(),)),
        cash_flow=AssessmentSection(findings=(_finding(),)),
        repayment_source=RepaymentSourceSection(findings=(_finding(),)),
        proposed_structure=ProposedStructureSection(
            instrument_vi="Han muc von luu dong (de xuat so bo)",
            findings=(_finding(),),
        ),
    )


def _legal_finding() -> LegalFinding:
    return LegalFinding(
        statement_vi="Phat hien phap ly mo phong.",
        citations=(LegalConfirmedFactCitation(confirmed_fact_id=FACT_ID),),
        confidence=ConfidenceLevel.MEDIUM,
    )


def build_legal() -> LegalComplianceAssessment:
    return LegalComplianceAssessment(
        id=uuid4(),
        provenance=LegalAssessmentProvenance(
            case_id=CASE_ID,
            case_version=1,
            execution_id=uuid4(),
            task_id=uuid4(),
            prompt_version="legal-prompt-v1",
            model_id="synthetic-model",
            endpoint_id="synthetic-endpoint",
            evidence_view_built_at=NOW,
            created_at=NOW,
        ),
        legal_entity_review=LegalAssessmentSection(findings=(_legal_finding(),)),
        authority_signatory_review=LegalAssessmentSection(findings=(_legal_finding(),)),
        ownership_consistency=OwnershipConsistencySection(findings=(_legal_finding(),)),
        collateral_review=CollateralReviewSection(
            ownership_evidence_findings=(_legal_finding(),)
        ),
    )


def build_risk_review(
    underwriting: UnderwritingAssessment,
    legal: LegalComplianceAssessment,
    *,
    challenges: tuple[Challenge, ...] = (),
) -> RiskReviewAssessment:
    return RiskReviewAssessment(
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
            maker_assessments_reviewed=(
                MakerReviewedRef(
                    maker_source=MakerSource.CREDIT_UNDERWRITING,
                    assessment_id=underwriting.id,
                    execution_id=underwriting.provenance.execution_id,
                ),
                MakerReviewedRef(
                    maker_source=MakerSource.LEGAL_COMPLIANCE_COLLATERAL,
                    assessment_id=legal.id,
                    execution_id=legal.provenance.execution_id,
                ),
            ),
        ),
        challenges=challenges,
        visibility_checks=VisibilityChecks(),
    )


def make_challenge(
    underwriting: UnderwritingAssessment,
    *,
    severity: ChallengeSeverity = ChallengeSeverity.HIGH,
) -> Challenge:
    target = MakerFindingRef(
        maker_source=MakerSource.CREDIT_UNDERWRITING,
        maker_assessment_id=underwriting.id,
        section_path="business.findings[0]",
    )
    return Challenge(
        id=uuid4(),
        target=target,
        challenge_type=ChallengeType.UNSUPPORTED_ASSUMPTION,
        statement_vi="Thach thuc mo phong.",
        citations=(MakerFindingCitation(ref=target),),
        severity=severity,
        confidence=ConfidenceLevel.MEDIUM,
    )


def build_view(
    *,
    has_intake: bool = True,
    with_underwriting: bool = True,
    with_legal: bool = True,
    with_risk_review: bool = True,
    challenges: tuple[Challenge, ...] = (),
    underwriting: UnderwritingAssessment | None = None,
) -> CreditOpsUpstreamView:
    if underwriting is None:
        underwriting = build_underwriting() if with_underwriting else None
    legal = build_legal() if with_legal else None
    risk_review = (
        build_risk_review(
            underwriting or build_underwriting(),
            legal or build_legal(),
            challenges=challenges,
        )
        if with_risk_review
        else None
    )
    return CreditOpsUpstreamView(
        case_id=CASE_ID,
        case_version=1,
        built_at=NOW,
        has_intake_handoff=has_intake,
        intake_handoff_id=uuid4() if has_intake else None,
        underwriting=underwriting,
        underwriting_execution_id=(
            underwriting.provenance.execution_id if underwriting else None
        ),
        underwriting_handoff_id=uuid4() if underwriting else None,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id if legal else None,
        legal_handoff_id=uuid4() if legal else None,
        risk_review=risk_review,
        risk_review_execution_id=(
            risk_review.provenance.execution_id if risk_review else None
        ),
        risk_review_handoff_id=uuid4() if risk_review else None,
    )


def _compute(view: CreditOpsUpstreamView, **kwargs: Any) -> Any:
    return compute_deterministic_package(
        view=view,
        open_gaps=kwargs.get("open_gaps", ()),
        dispositions=kwargs.get("dispositions", ()),
    )


# -- (a) completeness with a missing artifact ---------------------------------


def test_missing_upstream_artifact_is_reported_missing_and_package_still_assembles() -> None:
    view = build_view(with_legal=False)
    result = _compute(view)
    by_kind = {item.artifact: item for item in result.package_completeness.artifacts}
    assert by_kind[UpstreamArtifactKind.LEGAL_ASSESSMENT].status is ChecklistItemStatus.MISSING
    assert by_kind[UpstreamArtifactKind.LEGAL_ASSESSMENT].reference_id is None
    assert (
        by_kind[UpstreamArtifactKind.UNDERWRITING_ASSESSMENT].status
        is ChecklistItemStatus.PRESENT
    )
    assert result.package_completeness.all_required_present is False
    # The skeleton still assembled: every deterministic component exists.
    assert result.evidence_consolidation.entries
    assert result.proposed_actions


def test_every_artifact_kind_is_always_listed_exactly_once() -> None:
    for view in (build_view(), build_view(has_intake=False, with_risk_review=False)):
        result = _compute(view)
        kinds = [item.artifact for item in result.package_completeness.artifacts]
        assert sorted(k.value for k in kinds) == sorted(k.value for k in UpstreamArtifactKind)


def test_complete_view_reports_all_present_with_evidence_references() -> None:
    view = build_view()
    result = _compute(view)
    assert result.package_completeness.all_required_present is True
    for item in result.package_completeness.artifacts:
        assert item.status is ChecklistItemStatus.PRESENT
        assert item.reference_id is not None  # every item is evidence-referenced


# -- (b) provenance index covers every upstream assessment id -----------------


def test_provenance_index_covers_every_upstream_assessment_and_execution_id() -> None:
    view = build_view()
    consolidation = consolidate_evidence(view)
    assert view.underwriting is not None and view.legal is not None
    assert view.risk_review is not None
    by_kind = {entry.artifact: entry for entry in consolidation.entries}
    uw = by_kind[UpstreamArtifactKind.UNDERWRITING_ASSESSMENT]
    assert uw.assessment_id == view.underwriting.id
    assert uw.execution_id == view.underwriting_execution_id
    legal = by_kind[UpstreamArtifactKind.LEGAL_ASSESSMENT]
    assert legal.assessment_id == view.legal.id
    assert legal.execution_id == view.legal_execution_id
    rr = by_kind[UpstreamArtifactKind.RISK_REVIEW_ASSESSMENT]
    assert rr.assessment_id == view.risk_review.id
    assert rr.execution_id == view.risk_review_execution_id
    intake = by_kind[UpstreamArtifactKind.INTAKE_HANDOFF]
    assert intake.handoff_id == view.intake_handoff_id
    # Deduplicated: one entry per artifact kind.
    assert len(consolidation.entries) == len(by_kind)


def test_provenance_index_counts_citations() -> None:
    view = build_view()
    consolidation = consolidate_evidence(view)
    by_kind = {entry.artifact: entry for entry in consolidation.entries}
    # 5 underwriting findings + 4 legal findings, one citation each.
    assert by_kind[UpstreamArtifactKind.UNDERWRITING_ASSESSMENT].citation_count == 5
    assert by_kind[UpstreamArtifactKind.LEGAL_ASSESSMENT].citation_count == 4
    assert consolidation.distinct_citation_count == 9


# -- document-request consolidation -------------------------------------------


def _gap(status: str, level: GapBlockingLevel = GapBlockingLevel.BLOCKING) -> OpenGapRecord:
    return OpenGapRecord(
        gap_id=uuid4(),
        missing_information_vi="Bao cao tai chinh nam gan nhat (mo phong).",
        blocking_level=level,
        status=status,
    )


def test_document_requests_consolidate_only_open_formal_and_provisional_gaps() -> None:
    formal = _gap("FORMAL")
    provisional = _gap("PROVISIONAL", GapBlockingLevel.CONDITIONAL)
    resolved = _gap("RESOLVED")
    stale = _gap("STALE")
    requests = consolidate_document_requests((formal, provisional, resolved, stale))
    assert len(requests) == 2
    assert {r.originating_gap_id for r in requests} == {formal.gap_id, provisional.gap_id}
    for request in requests:
        # Each request carries its originating gap id and starts PENDING.
        assert request.approval_status is DocumentRequestApprovalStatus.PENDING_APPROVAL
        assert formal.missing_information_vi in request.request_text_vi or (
            provisional.missing_information_vi in request.request_text_vi
        )


def test_no_send_or_dispatch_surface_exists_on_a_document_request() -> None:
    request = consolidate_document_requests((_gap("FORMAL"),))[0]
    surface = {name.casefold() for name in type(request).model_fields}
    assert not surface & {"send", "sent", "dispatch", "dispatched", "delivered", "channel"}


# -- proposed actions ----------------------------------------------------------


def test_all_proposed_actions_are_draft_only() -> None:
    view = build_view()
    result = _compute(view, open_gaps=(_gap("FORMAL"),))
    assert result.proposed_actions
    for action in result.proposed_actions:
        assert action.execution_status is ProposedActionExecutionStatus.DRAFT


def test_document_request_actions_bind_to_their_request() -> None:
    view = build_view()
    result = _compute(view, open_gaps=(_gap("FORMAL"),))
    prepare_actions = [
        a
        for a in result.proposed_actions
        if a.action_type is ProposedActionType.PREPARE_DOCUMENT_REQUEST
    ]
    assert len(prepare_actions) == 1
    assert prepare_actions[0].related_document_request_id == result.document_requests[0].id


def test_mock_los_entry_is_only_proposed_when_the_package_is_complete() -> None:
    complete = _compute(build_view())
    incomplete = _compute(build_view(with_legal=False))
    complete_types = {a.action_type for a in complete.proposed_actions}
    incomplete_types = {a.action_type for a in incomplete.proposed_actions}
    assert ProposedActionType.SCHEDULE_MOCK_LOS_ENTRY in complete_types
    assert ProposedActionType.SCHEDULE_MOCK_LOS_ENTRY not in incomplete_types
    assert ProposedActionType.PREPARE_HANDOFF_PACKAGE in complete_types
    assert ProposedActionType.PREPARE_HANDOFF_PACKAGE in incomplete_types


# -- dispositions state / counts ----------------------------------------------


def test_unresolved_challenges_and_open_g3_are_recorded() -> None:
    underwriting = build_underwriting()
    challenge = make_challenge(underwriting)
    view = build_view(challenges=(challenge,), underwriting=underwriting)
    result = _compute(view)
    assert result.package_completeness.unresolved_challenge_count == 1
    assert "OPEN" in result.package_completeness.dispositions_state_vi


def test_disposed_challenges_flip_the_summary_to_satisfied() -> None:
    underwriting = build_underwriting()
    challenge = make_challenge(underwriting)
    view = build_view(challenges=(challenge,), underwriting=underwriting)
    result = _compute(
        view,
        dispositions=(
            ChallengeDispositionSummary(
                challenge_id=challenge.id, disposition_type="ACCEPTED_RISK"
            ),
        ),
    )
    assert result.package_completeness.unresolved_challenge_count == 0
    assert "SATISFIED" in result.package_completeness.dispositions_state_vi


def test_open_blocking_gap_count_counts_only_blocking_gaps() -> None:
    view = build_view()
    result = _compute(
        view,
        open_gaps=(
            _gap("FORMAL", GapBlockingLevel.BLOCKING),
            _gap("FORMAL", GapBlockingLevel.CONDITIONAL),
            _gap("PROVISIONAL", GapBlockingLevel.CLARIFICATION),
        ),
    )
    assert result.package_completeness.open_blocking_gap_count == 1


# -- risk-review target-path enumeration (used by the memo universe) ----------


def test_risk_review_target_paths_enumerate_every_addressable_item() -> None:
    underwriting = build_underwriting()
    legal = build_legal()
    assessment = build_risk_review(
        underwriting, legal, challenges=(make_challenge(underwriting),)
    )
    paths = risk_review_target_paths(assessment)
    assert "challenges[0]" in paths
    assert len(paths) == 1


def test_deterministic_ids_are_reproducible_with_an_injected_factory() -> None:
    view = build_view()
    counter = iter(range(1000))

    def fixed_ids() -> UUID:
        return UUID(int=next(counter))

    first = compute_deterministic_package(
        view=view, open_gaps=(_gap("FORMAL"),), dispositions=(), id_factory=fixed_ids
    )
    counter = iter(range(1000))
    second = compute_deterministic_package(
        view=view, open_gaps=(_gap("FORMAL"),), dispositions=(), id_factory=fixed_ids
    )
    assert [r.id for r in first.document_requests] == [r.id for r in second.document_requests]
    assert [a.id for a in first.proposed_actions] == [a.id for a in second.proposed_actions]
