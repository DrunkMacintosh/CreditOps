"""Deterministic target enumeration over the two maker assessments.

Mirrors ``application/underwriting/evidence.py``'s role: pure functions that
turn a scoped view into the closed set of ids the LLM schema will be built
from.  Here the "calculator suite" is the finite set of ``section_path``
strings addressable inside each maker assessment for one execution -- the
closed universe ``Challenge.target``, ``MitigantAdequacyReview`` refs, and
``MakerFindingCitation`` are enum-pinned against
(application/risk_review/checker.py).

Maker ``Finding``/``RiskItem``/etc. objects have no independent id; a path is
built positionally (``"business.findings[0]"``, ``"risks[2]"``) from the
assessment content itself, so it is entirely deterministic for one execution
and requires no schema change on the maker side.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from creditops.domain.legal import LegalComplianceAssessment
from creditops.domain.risk_review import MakerFindingRef, MakerSource
from creditops.domain.underwriting import UnderwritingAssessment


def underwriting_target_paths(assessment: UnderwritingAssessment) -> tuple[str, ...]:
    """Enumerate every addressable finding/section path in a maker assessment."""

    paths: list[str] = []
    for section_name in ("business", "financial", "cash_flow"):
        section = getattr(assessment, section_name)
        paths.extend(f"{section_name}.findings[{i}]" for i in range(len(section.findings)))
    paths.extend(
        f"repayment_source.findings[{i}]"
        for i in range(len(assessment.repayment_source.findings))
    )
    paths.extend(
        f"repayment_source.downside_scenarios[{i}]"
        for i in range(len(assessment.repayment_source.downside_scenarios))
    )
    paths.extend(
        f"proposed_structure.findings[{i}]"
        for i in range(len(assessment.proposed_structure.findings))
    )
    paths.extend(f"risks[{i}]" for i in range(len(assessment.risks)))
    paths.extend(f"mitigants[{i}]" for i in range(len(assessment.mitigants)))
    paths.extend(f"assumptions[{i}]" for i in range(len(assessment.assumptions)))
    paths.extend(f"evidence_gaps[{i}]" for i in range(len(assessment.evidence_gaps)))
    return tuple(paths)


def legal_target_paths(assessment: LegalComplianceAssessment) -> tuple[str, ...]:
    """Enumerate every addressable finding/section path in a legal assessment."""

    paths: list[str] = []
    for section_name in ("legal_entity_review", "authority_signatory_review"):
        section = getattr(assessment, section_name)
        paths.extend(f"{section_name}.findings[{i}]" for i in range(len(section.findings)))
    paths.extend(
        f"ownership_consistency.findings[{i}]"
        for i in range(len(assessment.ownership_consistency.findings))
    )
    paths.extend(
        f"ownership_consistency.inconsistencies[{i}]"
        for i in range(len(assessment.ownership_consistency.inconsistencies))
    )
    paths.extend(f"policy_review[{i}]" for i in range(len(assessment.policy_review)))
    paths.extend(
        f"controlled_check_interpretations[{i}]"
        for i in range(len(assessment.controlled_check_interpretations))
    )
    paths.extend(
        f"collateral_review.document_items[{i}]"
        for i in range(len(assessment.collateral_review.document_items))
    )
    paths.extend(
        f"collateral_review.ownership_evidence_findings[{i}]"
        for i in range(len(assessment.collateral_review.ownership_evidence_findings))
    )
    paths.extend(f"exceptions[{i}]" for i in range(len(assessment.exceptions)))
    paths.extend(f"assumptions[{i}]" for i in range(len(assessment.assumptions)))
    paths.extend(f"evidence_gaps[{i}]" for i in range(len(assessment.evidence_gaps)))
    return tuple(paths)


@dataclass(frozen=True, slots=True)
class TargetUniverse:
    """The closed, per-execution set of valid ``MakerFindingRef`` values."""

    underwriting_assessment_id: UUID
    underwriting_paths: tuple[str, ...]
    legal_assessment_id: UUID
    legal_paths: tuple[str, ...]

    def all_refs(self) -> tuple[MakerFindingRef, ...]:
        refs = [
            MakerFindingRef(
                maker_source=MakerSource.CREDIT_UNDERWRITING,
                maker_assessment_id=self.underwriting_assessment_id,
                section_path=path,
            )
            for path in self.underwriting_paths
        ]
        refs.extend(
            MakerFindingRef(
                maker_source=MakerSource.LEGAL_COMPLIANCE_COLLATERAL,
                maker_assessment_id=self.legal_assessment_id,
                section_path=path,
            )
            for path in self.legal_paths
        )
        return tuple(refs)

    def contains(self, ref: MakerFindingRef) -> bool:
        if ref.maker_source is MakerSource.CREDIT_UNDERWRITING:
            return (
                ref.maker_assessment_id == self.underwriting_assessment_id
                and ref.section_path in self.underwriting_paths
            )
        return (
            ref.maker_assessment_id == self.legal_assessment_id
            and ref.section_path in self.legal_paths
        )


def build_target_universe(
    underwriting: UnderwritingAssessment,
    legal: LegalComplianceAssessment,
) -> TargetUniverse:
    return TargetUniverse(
        underwriting_assessment_id=underwriting.id,
        underwriting_paths=underwriting_target_paths(underwriting),
        legal_assessment_id=legal.id,
        legal_paths=legal_target_paths(legal),
    )


__all__ = [
    "TargetUniverse",
    "build_target_universe",
    "legal_target_paths",
    "underwriting_target_paths",
]
