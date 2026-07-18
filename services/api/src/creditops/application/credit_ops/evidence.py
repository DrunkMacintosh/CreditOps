"""Deterministic target enumeration over the three upstream assessments.

Mirrors ``application/risk_review/evidence.py``'s role, extended from two
maker sources to three: the closed set of ``section_path`` strings
addressable inside the underwriting, legal, and independent-risk-review
assessments for one execution is the universe every ``MemoFindingRef`` in a
drafted memo or proposed action is grounded against
(application/credit_ops/assembler.py).  The underwriting/legal enumerators
are reused as-is from the risk-review checker's evidence module -- the path
grammar for those two assessment shapes does not change here.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from creditops.application.risk_review.evidence import (
    legal_target_paths as legal_target_paths,
)
from creditops.application.risk_review.evidence import (
    underwriting_target_paths as underwriting_target_paths,
)
from creditops.domain.credit_ops import MemoFindingRef, MemoSource
from creditops.domain.risk_review import RiskReviewAssessment


def risk_review_target_paths(assessment: RiskReviewAssessment) -> tuple[str, ...]:
    """Enumerate every addressable path in a checker (risk-review) assessment."""

    paths: list[str] = []
    paths.extend(f"challenges[{i}]" for i in range(len(assessment.challenges)))
    paths.extend(f"omitted_risks[{i}]" for i in range(len(assessment.omitted_risks)))
    paths.extend(
        f"mitigant_adequacy_reviews[{i}]"
        for i in range(len(assessment.mitigant_adequacy_reviews))
    )
    paths.extend(f"recommendations[{i}]" for i in range(len(assessment.recommendations)))
    paths.extend(f"evidence_gaps[{i}]" for i in range(len(assessment.evidence_gaps)))
    return tuple(paths)


@dataclass(frozen=True, slots=True)
class MemoTargetUniverse:
    """The closed, per-execution set of valid ``MemoFindingRef`` values."""

    underwriting_assessment_id: UUID
    underwriting_paths: tuple[str, ...]
    legal_assessment_id: UUID
    legal_paths: tuple[str, ...]
    risk_review_assessment_id: UUID
    risk_review_paths: tuple[str, ...]

    def all_refs(self) -> tuple[MemoFindingRef, ...]:
        refs = [
            MemoFindingRef(
                source=MemoSource.CREDIT_UNDERWRITING,
                source_assessment_id=self.underwriting_assessment_id,
                section_path=path,
            )
            for path in self.underwriting_paths
        ]
        refs.extend(
            MemoFindingRef(
                source=MemoSource.LEGAL_COMPLIANCE_COLLATERAL,
                source_assessment_id=self.legal_assessment_id,
                section_path=path,
            )
            for path in self.legal_paths
        )
        refs.extend(
            MemoFindingRef(
                source=MemoSource.INDEPENDENT_RISK_REVIEW,
                source_assessment_id=self.risk_review_assessment_id,
                section_path=path,
            )
            for path in self.risk_review_paths
        )
        return tuple(refs)

    def contains(self, ref: MemoFindingRef) -> bool:
        if ref.source is MemoSource.CREDIT_UNDERWRITING:
            return (
                ref.source_assessment_id == self.underwriting_assessment_id
                and ref.section_path in self.underwriting_paths
            )
        if ref.source is MemoSource.LEGAL_COMPLIANCE_COLLATERAL:
            return (
                ref.source_assessment_id == self.legal_assessment_id
                and ref.section_path in self.legal_paths
            )
        return (
            ref.source_assessment_id == self.risk_review_assessment_id
            and ref.section_path in self.risk_review_paths
        )


def build_memo_target_universe(
    *,
    underwriting_assessment_id: UUID,
    underwriting_paths: tuple[str, ...],
    legal_assessment_id: UUID,
    legal_paths: tuple[str, ...],
    risk_review_assessment_id: UUID,
    risk_review_paths: tuple[str, ...],
) -> MemoTargetUniverse:
    return MemoTargetUniverse(
        underwriting_assessment_id=underwriting_assessment_id,
        underwriting_paths=underwriting_paths,
        legal_assessment_id=legal_assessment_id,
        legal_paths=legal_paths,
        risk_review_assessment_id=risk_review_assessment_id,
        risk_review_paths=risk_review_paths,
    )


__all__ = [
    "MemoTargetUniverse",
    "build_memo_target_universe",
    "legal_target_paths",
    "risk_review_target_paths",
    "underwriting_target_paths",
]
