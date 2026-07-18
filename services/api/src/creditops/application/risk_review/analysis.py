"""Deterministic pre-analysis: run BEFORE inference, pure functions only.

Mirrors ``application/underwriting/evidence.py`` (calculators run before the
maker's LLM call) and ``application/legal/evidence.py`` (collateral checklist
and ownership cross-check run before the reviewer's LLM call).  Here the
checker's deterministic pass:

1. cross-checks every maker finding's ``CONFIRMED_FACT`` citations still
   resolve against the case's CURRENT confirmed facts (the maker may have run
   against a fact that was later corrected/superseded within the same case
   version's evidence view -- unlikely but never assumed away);
2. recomputes the BLOCKING-gap and exception visibility lists from both maker
   assessments and cross-checks them against the case's live open-gap
   registry, auto-raising a challenge for anything that silently stopped
   being visible;
3. detects maker assumptions with no downstream treatment (an assumption
   whose basis citations are never also cited by any risk or mitigant); and
4. flags maker findings/risks/mitigants whose confidence is at or below a
   named threshold.

These results are merged into the final ``RiskReviewAssessment`` regardless
of what the LLM returns (``application/risk_review/checker.py``): the LLM may
ADD challenges, it can never remove or relabel a deterministic one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

from creditops.application.ports.risk_review import CheckerEvidenceView, OpenGapRecord
from creditops.domain.legal import EvidenceCitation as LegalCitation
from creditops.domain.legal import LegalComplianceAssessment
from creditops.domain.risk_review import (
    Challenge,
    ChallengeSeverity,
    ChallengeType,
    ConfidenceLevel,
    MakerFindingCitation,
    MakerFindingRef,
    MakerSource,
    VisibilityChecks,
    VisibilityExceptionItem,
    VisibilityGapItem,
)
from creditops.domain.underwriting import EvidenceCitation as UnderwritingCitation
from creditops.domain.underwriting import GapBlockingLevel, UnderwritingAssessment

#: Named threshold (deliverable 3): a maker finding/risk/mitigant AT OR BELOW
#: this confidence level is deterministically flagged for checker attention.
CONFIDENCE_FLAG_THRESHOLD: ConfidenceLevel = ConfidenceLevel.LOW


def _underwriting_citation_key(citation: UnderwritingCitation) -> tuple[str, str]:
    if citation.kind == "CONFIRMED_FACT":
        return ("CONFIRMED_FACT", str(citation.confirmed_fact_id))
    if citation.kind == "CALCULATOR_RESULT":
        return ("CALCULATOR_RESULT", citation.result_id)
    return ("DOCUMENT_REGION", f"{citation.document_version_id}:{citation.region}")


def _iter_underwriting_citations(
    assessment: UnderwritingAssessment,
) -> Iterator[tuple[str, tuple[UnderwritingCitation, ...]]]:
    for section_name in ("business", "financial", "cash_flow"):
        section = getattr(assessment, section_name)
        for index, finding in enumerate(section.findings):
            yield f"{section_name}.findings[{index}]", finding.citations
    for index, finding in enumerate(assessment.repayment_source.findings):
        yield f"repayment_source.findings[{index}]", finding.citations
    for index, finding in enumerate(assessment.repayment_source.downside_scenarios):
        yield f"repayment_source.downside_scenarios[{index}]", finding.citations
    for index, finding in enumerate(assessment.proposed_structure.findings):
        yield f"proposed_structure.findings[{index}]", finding.citations
    for index, risk in enumerate(assessment.risks):
        yield f"risks[{index}]", risk.citations
    for index, mitigant in enumerate(assessment.mitigants):
        yield f"mitigants[{index}]", mitigant.citations


def _iter_legal_citations(
    assessment: LegalComplianceAssessment,
) -> Iterator[tuple[str, tuple[LegalCitation, ...]]]:
    for section_name in ("legal_entity_review", "authority_signatory_review"):
        section = getattr(assessment, section_name)
        for index, finding in enumerate(section.findings):
            yield f"{section_name}.findings[{index}]", finding.citations
    for index, finding in enumerate(assessment.ownership_consistency.findings):
        yield f"ownership_consistency.findings[{index}]", finding.citations
    for index, policy_finding in enumerate(assessment.policy_review):
        yield f"policy_review[{index}]", policy_finding.citations
    for index, item in enumerate(assessment.collateral_review.document_items):
        yield f"collateral_review.document_items[{index}]", item.citations
    for index, finding in enumerate(assessment.collateral_review.ownership_evidence_findings):
        yield f"collateral_review.ownership_evidence_findings[{index}]", finding.citations
    for index, exception in enumerate(assessment.exceptions):
        yield f"exceptions[{index}]", exception.citations


def cross_check_citation_grounding(
    *,
    underwriting: UnderwritingAssessment,
    legal: LegalComplianceAssessment,
    checker_view: CheckerEvidenceView,
    id_factory: Callable[[], UUID] = uuid4,
) -> tuple[Challenge, ...]:
    """Flag every CONFIRMED_FACT citation that no longer resolves for the case.

    Calculator, policy, and controlled-check citations are already grounded
    against the assessment's OWN recorded results at construction time
    (domain validators); the only fact that can drift between maker execution
    and checker execution is a Confirmed Fact being corrected or superseded.
    """

    live_fact_ids = {str(fact.confirmed_fact_id) for fact in checker_view.confirmed_facts}
    challenges: list[Challenge] = []
    for source, maker_id, iterator in (
        (
            MakerSource.CREDIT_UNDERWRITING,
            underwriting.id,
            _iter_underwriting_citations(underwriting),
        ),
        (MakerSource.LEGAL_COMPLIANCE_COLLATERAL, legal.id, _iter_legal_citations(legal)),
    ):
        for path, citations in iterator:
            for citation in citations:
                if citation.kind != "CONFIRMED_FACT":
                    continue
                if str(citation.confirmed_fact_id) in live_fact_ids:
                    continue
                target = MakerFindingRef(
                    maker_source=source, maker_assessment_id=maker_id, section_path=path
                )
                challenges.append(
                    Challenge(
                        id=id_factory(),
                        target=target,
                        challenge_type=ChallengeType.OTHER_CONCERN,
                        statement_vi=(
                            "Trich dan den mot du kien da xac nhan khong con "
                            f"ton tai trong pham vi bang chung hien tai cua ho so ({path})."
                        ),
                        citations=(MakerFindingCitation(ref=target),),
                        severity=ChallengeSeverity.HIGH,
                        confidence=ConfidenceLevel.HIGH,
                    )
                )
    return tuple(challenges)


def recompute_visibility(
    *,
    underwriting: UnderwritingAssessment,
    legal: LegalComplianceAssessment,
    open_gaps: tuple[OpenGapRecord, ...],
    id_factory: Callable[[], UUID] = uuid4,
) -> tuple[VisibilityChecks, tuple[Challenge, ...]]:
    """Recompute BLOCKING-gap/exception visibility; auto-raise for anything missing."""

    open_gap_texts = {gap.missing_information_vi for gap in open_gaps}

    gap_items: list[VisibilityGapItem] = []
    for source, maker_id, gaps in (
        (MakerSource.CREDIT_UNDERWRITING, underwriting.id, underwriting.evidence_gaps),
        (MakerSource.LEGAL_COMPLIANCE_COLLATERAL, legal.id, legal.evidence_gaps),
    ):
        for gap in gaps:
            if gap.blocking_level is not GapBlockingLevel.BLOCKING:
                continue
            gap_items.append(
                VisibilityGapItem(
                    source=source,
                    source_assessment_id=maker_id,
                    missing_information_vi=gap.missing_information_vi,
                    blocking_level=gap.blocking_level,
                    still_visible=gap.missing_information_vi in open_gap_texts,
                )
            )

    exception_items: list[VisibilityExceptionItem] = [
        VisibilityExceptionItem(
            source_assessment_id=legal.id,
            category=exception.category.value,
            possible_issue_vi=exception.possible_issue_vi,
            # Exceptions live only inside the immutable legal assessment (no
            # separate mutable store); they are "still visible" precisely
            # when this deterministic recomputation carries them forward,
            # which it always does by construction.  The flag stays
            # first-class so a future refactor that drops one is caught by
            # ``visibility_challenges_from`` instead of silently vanishing.
            still_visible=True,
        )
        for exception in legal.exceptions
    ]

    checks = VisibilityChecks(blocking_gaps=tuple(gap_items), exceptions=tuple(exception_items))
    return checks, visibility_challenges_from(
        checks, underwriting_id=underwriting.id, legal_id=legal.id, id_factory=id_factory
    )


def visibility_challenges_from(
    checks: VisibilityChecks,
    *,
    underwriting_id: UUID,
    legal_id: UUID,
    id_factory: Callable[[], UUID] = uuid4,
) -> tuple[Challenge, ...]:
    """Auto-raise GAP_VISIBILITY/EXCEPTION_VISIBILITY for anything not still visible.

    Exposed separately from ``recompute_visibility`` so a deliberately
    incomplete ``VisibilityChecks`` can be fed in directly to prove the
    auto-raise mechanism fires (defense-in-depth self-check).
    """

    challenges: list[Challenge] = []
    for gap_item in checks.blocking_gaps:
        if gap_item.still_visible:
            continue
        assessment_id = (
            underwriting_id
            if gap_item.source is MakerSource.CREDIT_UNDERWRITING
            else legal_id
        )
        target = MakerFindingRef(
            maker_source=gap_item.source,
            maker_assessment_id=assessment_id,
            section_path="evidence_gaps[*]",
        )
        challenges.append(
            Challenge(
                id=id_factory(),
                target=target,
                challenge_type=ChallengeType.GAP_VISIBILITY,
                statement_vi=(
                    "Khoang trong bang chung chan (BLOCKING) da cong bo khong "
                    f"con hien thi trong ho so: {gap_item.missing_information_vi}"
                ),
                citations=(MakerFindingCitation(ref=target),),
                severity=ChallengeSeverity.HIGH,
                confidence=ConfidenceLevel.HIGH,
            )
        )
    for exception_item in checks.exceptions:
        if exception_item.still_visible:
            continue
        target = MakerFindingRef(
            maker_source=MakerSource.LEGAL_COMPLIANCE_COLLATERAL,
            maker_assessment_id=legal_id,
            section_path="exceptions[*]",
        )
        challenges.append(
            Challenge(
                id=id_factory(),
                target=target,
                challenge_type=ChallengeType.EXCEPTION_VISIBILITY,
                statement_vi=(
                    "Kha nang ngoai le da cong bo khong con hien thi trong ho so: "
                    f"{exception_item.possible_issue_vi}"
                ),
                citations=(MakerFindingCitation(ref=target),),
                severity=ChallengeSeverity.HIGH,
                confidence=ConfidenceLevel.HIGH,
            )
        )
    return tuple(challenges)


def detect_unaddressed_assumptions(
    underwriting: UnderwritingAssessment,
    *,
    id_factory: Callable[[], UUID] = uuid4,
) -> tuple[Challenge, ...]:
    """Flag an underwriting assumption never cited back by any risk or mitigant.

    Deterministic rule: an assumption with at least one basis citation whose
    citations do not intersect any risk/mitigant citation set is flagged.  An
    assumption declared with NO basis citation cannot be checked by citation
    overlap and is left to the LLM's contextual judgement instead.
    """

    downstream_keys: set[tuple[str, str]] = set()
    for risk in underwriting.risks:
        downstream_keys.update(
            _underwriting_citation_key(citation) for citation in risk.citations
        )
    for mitigant in underwriting.mitigants:
        downstream_keys.update(
            _underwriting_citation_key(citation) for citation in mitigant.citations
        )

    challenges: list[Challenge] = []
    for index, assumption in enumerate(underwriting.assumptions):
        if not assumption.basis_citations:
            continue
        assumption_keys = {
            _underwriting_citation_key(citation) for citation in assumption.basis_citations
        }
        if assumption_keys & downstream_keys:
            continue
        target = MakerFindingRef(
            maker_source=MakerSource.CREDIT_UNDERWRITING,
            maker_assessment_id=underwriting.id,
            section_path=f"assumptions[{index}]",
        )
        challenges.append(
            Challenge(
                id=id_factory(),
                target=target,
                challenge_type=ChallengeType.UNSUPPORTED_ASSUMPTION,
                statement_vi=(
                    "Gia dinh cua MAKER khong duoc bat ky rui ro hay bien phap "
                    f"giam thieu nao tham chieu lai: {assumption.statement_vi}"
                ),
                citations=(MakerFindingCitation(ref=target),),
                severity=ChallengeSeverity.MEDIUM,
                confidence=ConfidenceLevel.MEDIUM,
            )
        )
    return tuple(challenges)


def flag_low_confidence_findings(
    *,
    underwriting: UnderwritingAssessment,
    legal: LegalComplianceAssessment,
    id_factory: Callable[[], UUID] = uuid4,
    threshold: ConfidenceLevel = CONFIDENCE_FLAG_THRESHOLD,
) -> tuple[Challenge, ...]:
    """Flag every maker finding/risk/mitigant at or below the named threshold."""

    challenges: list[Challenge] = []
    for source, maker_id, confidences in (
        (
            MakerSource.CREDIT_UNDERWRITING,
            underwriting.id,
            _underwriting_confidence_items(underwriting),
        ),
        (
            MakerSource.LEGAL_COMPLIANCE_COLLATERAL,
            legal.id,
            _legal_confidence_items(legal),
        ),
    ):
        for path, confidence in confidences:
            if confidence != threshold:
                continue
            target = MakerFindingRef(
                maker_source=source, maker_assessment_id=maker_id, section_path=path
            )
            challenges.append(
                Challenge(
                    id=id_factory(),
                    target=target,
                    challenge_type=ChallengeType.OTHER_CONCERN,
                    statement_vi=(
                        f"Muc do tin cay cua MAKER cho muc '{path}' o nguong "
                        f"{confidence.value}, can duoc xem xet them."
                    ),
                    citations=(MakerFindingCitation(ref=target),),
                    severity=ChallengeSeverity.LOW,
                    confidence=ConfidenceLevel.MEDIUM,
                )
            )
    return tuple(challenges)


def _underwriting_confidence_items(
    assessment: UnderwritingAssessment,
) -> Iterator[tuple[str, ConfidenceLevel]]:
    for section_name in ("business", "financial", "cash_flow"):
        section = getattr(assessment, section_name)
        for index, finding in enumerate(section.findings):
            yield f"{section_name}.findings[{index}]", finding.confidence
    for index, risk in enumerate(assessment.risks):
        yield f"risks[{index}]", risk.confidence
    for index, mitigant in enumerate(assessment.mitigants):
        yield f"mitigants[{index}]", mitigant.confidence


def _legal_confidence_items(
    assessment: LegalComplianceAssessment,
) -> Iterator[tuple[str, ConfidenceLevel]]:
    for section_name in ("legal_entity_review", "authority_signatory_review"):
        section = getattr(assessment, section_name)
        for index, finding in enumerate(section.findings):
            yield f"{section_name}.findings[{index}]", finding.confidence
    for index, exception in enumerate(assessment.exceptions):
        yield f"exceptions[{index}]", exception.confidence


@dataclass(frozen=True, slots=True)
class DeterministicPreAnalysis:
    """Every deterministic result computed BEFORE inference, for one execution."""

    citation_grounding_challenges: tuple[Challenge, ...]
    visibility_checks: VisibilityChecks
    visibility_challenges: tuple[Challenge, ...]
    unaddressed_assumption_challenges: tuple[Challenge, ...]
    low_confidence_challenges: tuple[Challenge, ...]

    @property
    def all_challenges(self) -> tuple[Challenge, ...]:
        return (
            self.citation_grounding_challenges
            + self.visibility_challenges
            + self.unaddressed_assumption_challenges
            + self.low_confidence_challenges
        )


def compute_deterministic_pre_analysis(
    *,
    underwriting: UnderwritingAssessment,
    legal: LegalComplianceAssessment,
    checker_view: CheckerEvidenceView,
    open_gaps: tuple[OpenGapRecord, ...],
    id_factory: Callable[[], UUID] = uuid4,
) -> DeterministicPreAnalysis:
    visibility_checks, visibility_challenges = recompute_visibility(
        underwriting=underwriting, legal=legal, open_gaps=open_gaps, id_factory=id_factory
    )
    return DeterministicPreAnalysis(
        citation_grounding_challenges=cross_check_citation_grounding(
            underwriting=underwriting,
            legal=legal,
            checker_view=checker_view,
            id_factory=id_factory,
        ),
        visibility_checks=visibility_checks,
        visibility_challenges=visibility_challenges,
        unaddressed_assumption_challenges=detect_unaddressed_assumptions(
            underwriting, id_factory=id_factory
        ),
        low_confidence_challenges=flag_low_confidence_findings(
            underwriting=underwriting, legal=legal, id_factory=id_factory
        ),
    )


__all__ = [
    "CONFIDENCE_FLAG_THRESHOLD",
    "DeterministicPreAnalysis",
    "compute_deterministic_pre_analysis",
    "cross_check_citation_grounding",
    "detect_unaddressed_assumptions",
    "flag_low_confidence_findings",
    "recompute_visibility",
    "visibility_challenges_from",
]
