"""Deterministic pre-analysis: run BEFORE inference, pure functions only.

Mirrors ``application/risk_review/analysis.py``'s role for the Credit
Operations Agent: package completeness, provenance consolidation, and
document-request consolidation are ALL computed here, deterministically,
before any model call.  The LLM (application/credit_ops/assembler.py) only
drafts the memo NARRATIVE over this pre-built, closed skeleton -- it can
never add, remove, or relabel a checklist item, a consolidated provenance
entry, a document request, or a proposed action.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from creditops.application.orchestration.gates import derive_g3_status
from creditops.application.ports.credit_ops import (
    ChallengeDispositionSummary,
    CreditOpsUpstreamView,
    OpenGapRecord,
)
from creditops.domain.credit_ops import (
    ChecklistItemStatus,
    DocumentRequest,
    EvidenceConsolidation,
    PackageChecklistItem,
    PackageCompleteness,
    ProposedAction,
    ProposedActionType,
    ProvenanceIndexEntry,
    UpstreamArtifactKind,
)
from creditops.domain.legal import LegalComplianceAssessment
from creditops.domain.orchestration import GateStatus
from creditops.domain.risk_review import ChallengeSeverity, RiskReviewAssessment
from creditops.domain.underwriting import GapBlockingLevel, UnderwritingAssessment

#: Only these two evidence-gap lifecycle states are consolidated into a
#: drafted document request (CONTEXT.md: PROVISIONAL -> FORMAL -> RESOLVED /
#: STALE).  A RESOLVED or STALE gap has nothing left to request.
_CONSOLIDATABLE_GAP_STATUSES = frozenset({"PROVISIONAL", "FORMAL"})


def _count_underwriting_citations(assessment: UnderwritingAssessment) -> int:
    total = 0
    for section_name in ("business", "financial", "cash_flow"):
        section = getattr(assessment, section_name)
        total += sum(len(finding.citations) for finding in section.findings)
    total += sum(len(finding.citations) for finding in assessment.repayment_source.findings)
    total += sum(
        len(finding.citations) for finding in assessment.repayment_source.downside_scenarios
    )
    total += sum(len(finding.citations) for finding in assessment.proposed_structure.findings)
    total += sum(len(risk.citations) for risk in assessment.risks)
    total += sum(len(mitigant.citations) for mitigant in assessment.mitigants)
    return total


def _count_legal_citations(assessment: LegalComplianceAssessment) -> int:
    total = 0
    total += sum(len(finding.citations) for finding in assessment.legal_entity_review.findings)
    total += sum(
        len(finding.citations) for finding in assessment.authority_signatory_review.findings
    )
    total += sum(
        len(finding.citations) for finding in assessment.ownership_consistency.findings
    )
    total += sum(
        len(item.citations) for item in assessment.ownership_consistency.inconsistencies
    )
    total += sum(len(item.citations) for item in assessment.policy_review)
    total += sum(len(item.citations) for item in assessment.collateral_review.document_items)
    total += sum(
        len(finding.citations)
        for finding in assessment.collateral_review.ownership_evidence_findings
    )
    total += sum(len(item.citations) for item in assessment.exceptions)
    return total


def _count_risk_review_citations(assessment: RiskReviewAssessment) -> int:
    total = 0
    total += sum(len(challenge.citations) for challenge in assessment.challenges)
    total += sum(len(item.citations) for item in assessment.omitted_risks)
    total += sum(len(item.citations) for item in assessment.mitigant_adequacy_reviews)
    total += sum(len(item.citations) for item in assessment.recommendations)
    return total


def compute_package_completeness(
    view: CreditOpsUpstreamView,
    *,
    dispositions_state_vi: str,
    unresolved_challenge_count: int,
    open_blocking_gap_count: int,
) -> PackageCompleteness:
    """Compute the deterministic completeness checklist.

    Never raises for a missing artifact: absence is RECORDED as a ``MISSING``
    checklist item so the package still assembles (deliverable: "missing
    upstream artifact -> reported missing, package still assembles with the
    absence recorded").
    """

    items: list[PackageChecklistItem] = []
    if view.has_intake_handoff and view.intake_handoff_id is not None:
        items.append(
            PackageChecklistItem(
                artifact=UpstreamArtifactKind.INTAKE_HANDOFF,
                status=ChecklistItemStatus.PRESENT,
                detail_vi="Da co ban ban giao tiep nhan (intake handoff) cho phien ban ho so nay.",
                reference_id=view.intake_handoff_id,
            )
        )
    else:
        items.append(
            PackageChecklistItem(
                artifact=UpstreamArtifactKind.INTAKE_HANDOFF,
                status=ChecklistItemStatus.MISSING,
                detail_vi=(
                    "Chua co ban ban giao tiep nhan (intake handoff) "
                    "cho phien ban ho so nay."
                ),
            )
        )
    if view.underwriting is not None:
        items.append(
            PackageChecklistItem(
                artifact=UpstreamArtifactKind.UNDERWRITING_ASSESSMENT,
                status=ChecklistItemStatus.PRESENT,
                detail_vi=(
                    "Da co ban danh gia tham dinh tin dung (underwriting) "
                    "cho phien ban ho so nay."
                ),
                reference_id=view.underwriting.id,
            )
        )
    else:
        items.append(
            PackageChecklistItem(
                artifact=UpstreamArtifactKind.UNDERWRITING_ASSESSMENT,
                status=ChecklistItemStatus.MISSING,
                detail_vi=(
                    "Chua co ban danh gia tham dinh tin dung (underwriting) "
                    "cho phien ban ho so nay."
                ),
            )
        )
    if view.legal is not None:
        items.append(
            PackageChecklistItem(
                artifact=UpstreamArtifactKind.LEGAL_ASSESSMENT,
                status=ChecklistItemStatus.PRESENT,
                detail_vi="Da co ban ra soat phap ly/tuan thu/TSBD cho phien ban ho so nay.",
                reference_id=view.legal.id,
            )
        )
    else:
        items.append(
            PackageChecklistItem(
                artifact=UpstreamArtifactKind.LEGAL_ASSESSMENT,
                status=ChecklistItemStatus.MISSING,
                detail_vi="Chua co ban ra soat phap ly/tuan thu/TSBD cho phien ban ho so nay.",
            )
        )
    if view.risk_review is not None:
        items.append(
            PackageChecklistItem(
                artifact=UpstreamArtifactKind.RISK_REVIEW_ASSESSMENT,
                status=ChecklistItemStatus.PRESENT,
                detail_vi="Da co ban ra soat rui ro doc lap cho phien ban ho so nay.",
                reference_id=view.risk_review.id,
            )
        )
    else:
        items.append(
            PackageChecklistItem(
                artifact=UpstreamArtifactKind.RISK_REVIEW_ASSESSMENT,
                status=ChecklistItemStatus.MISSING,
                detail_vi="Chua co ban ra soat rui ro doc lap cho phien ban ho so nay.",
            )
        )

    all_required_present = all(item.status is ChecklistItemStatus.PRESENT for item in items)
    return PackageCompleteness(
        artifacts=tuple(items),
        dispositions_state_vi=dispositions_state_vi,
        unresolved_challenge_count=unresolved_challenge_count,
        open_blocking_gap_count=open_blocking_gap_count,
        all_required_present=all_required_present,
    )


def consolidate_evidence(view: CreditOpsUpstreamView) -> EvidenceConsolidation:
    """Build the full, deduplicated provenance index over every present
    upstream artifact.  One entry per artifact kind; a missing artifact
    contributes no entry (nothing to index)."""

    entries: list[ProvenanceIndexEntry] = []
    total_citations = 0
    if view.has_intake_handoff and view.intake_handoff_id is not None:
        entries.append(
            ProvenanceIndexEntry(
                artifact=UpstreamArtifactKind.INTAKE_HANDOFF,
                handoff_id=view.intake_handoff_id,
                citation_count=0,
            )
        )
    if view.underwriting is not None:
        count = _count_underwriting_citations(view.underwriting)
        total_citations += count
        entries.append(
            ProvenanceIndexEntry(
                artifact=UpstreamArtifactKind.UNDERWRITING_ASSESSMENT,
                assessment_id=view.underwriting.id,
                execution_id=view.underwriting_execution_id,
                handoff_id=view.underwriting_handoff_id,
                citation_count=count,
            )
        )
    if view.legal is not None:
        count = _count_legal_citations(view.legal)
        total_citations += count
        entries.append(
            ProvenanceIndexEntry(
                artifact=UpstreamArtifactKind.LEGAL_ASSESSMENT,
                assessment_id=view.legal.id,
                execution_id=view.legal_execution_id,
                handoff_id=view.legal_handoff_id,
                citation_count=count,
            )
        )
    if view.risk_review is not None:
        count = _count_risk_review_citations(view.risk_review)
        total_citations += count
        entries.append(
            ProvenanceIndexEntry(
                artifact=UpstreamArtifactKind.RISK_REVIEW_ASSESSMENT,
                assessment_id=view.risk_review.id,
                execution_id=view.risk_review_execution_id,
                handoff_id=view.risk_review_handoff_id,
                citation_count=count,
            )
        )
    return EvidenceConsolidation(entries=tuple(entries), distinct_citation_count=total_citations)


def consolidate_document_requests(
    open_gaps: tuple[OpenGapRecord, ...],
    *,
    id_factory: Callable[[], UUID] = uuid4,
) -> tuple[DocumentRequest, ...]:
    """Consolidate every open FORMAL/PROVISIONAL gap into a drafted,
    PENDING_APPROVAL document request.  No send/dispatch happens here or
    anywhere in this codebase; a human must separately approve each one."""

    return tuple(
        DocumentRequest(
            id=id_factory(),
            originating_gap_id=gap.gap_id,
            request_text_vi=(
                "De nghi khach hang/don vi lien quan bo sung: "
                f"{gap.missing_information_vi}"
            ),
            blocking_level=gap.blocking_level,
        )
        for gap in open_gaps
        if gap.status in _CONSOLIDATABLE_GAP_STATUSES
    )


def build_proposed_actions(
    *,
    document_requests: tuple[DocumentRequest, ...],
    package_complete: bool,
    id_factory: Callable[[], UUID] = uuid4,
) -> tuple[ProposedAction, ...]:
    """Build the deterministic set of drafted, DRAFT-only proposed actions.

    One ``PREPARE_DOCUMENT_REQUEST`` per drafted request, an optional
    ``SCHEDULE_MOCK_LOS_ENTRY`` only once the package is materially
    complete, and always a final ``PREPARE_HANDOFF_PACKAGE``.  Every action
    is created with ``execution_status=DRAFT`` (the schema's only value) and
    is never executed by this function or any other code path.
    """

    actions: list[ProposedAction] = [
        ProposedAction(
            id=id_factory(),
            action_type=ProposedActionType.PREPARE_DOCUMENT_REQUEST,
            description_vi=(
                "Chuan bi yeu cau bo sung tai lieu (chi soan thao, "
                f"cho phe duyet cua con nguoi): {request.request_text_vi}"
            ),
            related_document_request_id=request.id,
        )
        for request in document_requests
    ]
    if package_complete:
        actions.append(
            ProposedAction(
                id=id_factory(),
                action_type=ProposedActionType.SCHEDULE_MOCK_LOS_ENTRY,
                description_vi=(
                    "De xuat lich nhap lieu vao he thong LOS mo phong (mock) sau khi "
                    "ho so day du va duoc con nguoi uy quyen."
                ),
            )
        )
    actions.append(
        ProposedAction(
            id=id_factory(),
            action_type=ProposedActionType.PREPARE_HANDOFF_PACKAGE,
            description_vi="Chuan bi goi ho so ban giao cho nguoi ra quyet dinh tin dung.",
        )
    )
    return tuple(actions)


def _dispositions_state_vi(status: GateStatus, unresolved_challenge_count: int) -> str:
    if status is GateStatus.SATISFIED:
        return (
            "G3_RISK_DISPOSITION: DA XU LY (SATISFIED) - moi thach thuc nghiem trong tu "
            "muc HIGH tro len (hoac su vang mat cua chung) da duoc con nguoi ghi nhan quyet dinh."
        )
    return (
        "G3_RISK_DISPOSITION: CHUA XU LY (OPEN) - con "
        f"{unresolved_challenge_count} thach thuc chua co quyet dinh cua con nguoi co tham quyen."
    )


@dataclass(frozen=True, slots=True)
class DeterministicCreditOpsPackage:
    """Everything the deterministic pre-analysis produces, BEFORE inference:
    the exact skeleton the memo narrative is drafted over."""

    package_completeness: PackageCompleteness
    evidence_consolidation: EvidenceConsolidation
    document_requests: tuple[DocumentRequest, ...]
    proposed_actions: tuple[ProposedAction, ...]


def compute_deterministic_package(
    *,
    view: CreditOpsUpstreamView,
    open_gaps: tuple[OpenGapRecord, ...],
    dispositions: tuple[ChallengeDispositionSummary, ...],
    id_factory: Callable[[], UUID] = uuid4,
) -> DeterministicCreditOpsPackage:
    """Run the full deterministic pass: completeness, provenance, drafted
    document requests, and drafted proposed actions -- in that order, all
    pure and all BEFORE any model call."""

    disposed_challenge_ids = {d.challenge_id for d in dispositions if d.challenge_id is not None}
    has_assessment_level_disposition = any(d.challenge_id is None for d in dispositions)
    severities: dict[UUID, ChallengeSeverity] = (
        {challenge.id: challenge.severity for challenge in view.risk_review.challenges}
        if view.risk_review is not None
        else {}
    )
    g3_status = derive_g3_status(
        assessment_exists=view.risk_review is not None,
        challenge_severities=severities,
        disposed_challenge_ids=disposed_challenge_ids,
        has_assessment_level_disposition=has_assessment_level_disposition,
    )
    unresolved_challenge_count = (
        sum(
            1
            for challenge in view.risk_review.challenges
            if challenge.id not in disposed_challenge_ids
        )
        if view.risk_review is not None
        else 0
    )
    open_blocking_gap_count = sum(
        1 for gap in open_gaps if gap.blocking_level is GapBlockingLevel.BLOCKING
    )
    completeness = compute_package_completeness(
        view,
        dispositions_state_vi=_dispositions_state_vi(g3_status, unresolved_challenge_count),
        unresolved_challenge_count=unresolved_challenge_count,
        open_blocking_gap_count=open_blocking_gap_count,
    )
    evidence_consolidation = consolidate_evidence(view)
    document_requests = consolidate_document_requests(open_gaps, id_factory=id_factory)
    proposed_actions = build_proposed_actions(
        document_requests=document_requests,
        package_complete=completeness.all_required_present,
        id_factory=id_factory,
    )
    return DeterministicCreditOpsPackage(
        package_completeness=completeness,
        evidence_consolidation=evidence_consolidation,
        document_requests=document_requests,
        proposed_actions=proposed_actions,
    )


__all__ = [
    "DeterministicCreditOpsPackage",
    "build_proposed_actions",
    "compute_deterministic_package",
    "compute_package_completeness",
    "consolidate_document_requests",
    "consolidate_evidence",
]
