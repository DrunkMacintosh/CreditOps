"""Structured output contract for the Independent Risk Review Agent (the Checker).

Design invariants (docs/AGENT_ARCHITECTURE.md §Independent Risk Review,
§Maker-checker and separation-of-duties invariants; CONTEXT.md glossary:
Checker, Challenge, Disposition):

- The checker NEVER edits or re-authors maker output, NEVER approves or
  rejects credit, and NEVER resolves a gap, exception, or its own challenge.
  ``extra="forbid"`` plus the module-level schema guard makes an
  approve/reject/clear/resolve/override/decision-capable field a
  construction-time error, not a reviewable mistake.
- Every challenge, omitted-risk item, and mitigant-adequacy review carries at
  least one evidence citation.  A challenge with no citations is structurally
  impossible.
- A challenge ``target`` always names the maker assessment id and the exact
  finding/section path being challenged; the schema rejects a target that
  does not resolve against the maker assessments actually reviewed
  (``provenance.maker_assessments_reviewed``).
- Recommendations are ADVISORY ONLY (request information, structural-change
  suggestion, manual review, escalation) -- never a decision.
- ``visibility_checks`` records, per BLOCKING gap and per exception declared
  by either maker, whether it is still visible; a checker execution that
  fails to carry one forward is a structural bug the schema can surface, not
  a silent resolution.
- The checker's own execution id must differ from every maker execution id
  it reviewed: "the same role execution must not author and independently
  clear the same material conclusion."
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.domain.ids import CaseId, ConfirmedFactId, DocumentVersionId, TaskId
from creditops.domain.underwriting import FORBIDDEN_DECISION_FIELD_NAMES
from creditops.domain.underwriting import ConfidenceLevel as ConfidenceLevel
from creditops.domain.underwriting import GapBlockingLevel as GapBlockingLevel

type RiskReviewAssessmentId = UUID

RISK_REVIEW_AGENT_ROLE: Literal["INDEPENDENT_RISK_REVIEW"] = "INDEPENDENT_RISK_REVIEW"

#: The checker's schema extends the maker's forbidden-decision vocabulary with
#: words that would let it clear, resolve, or override anything -- a checker
#: cannot express clearing its own challenge or resolving a gap/exception.
FORBIDDEN_CHECKER_FIELD_NAMES = FORBIDDEN_DECISION_FIELD_NAMES | frozenset(
    {
        "clear",
        "cleared",
        "clearance",
        "resolve",
        "resolved",
        "resolution",
        "override",
        "overridden",
        "overriding",
        "disposition",
        "dispositioned",
    }
)


class MakerSource(StrEnum):
    """Which of the two maker executions a target/citation points into."""

    CREDIT_UNDERWRITING = "CREDIT_UNDERWRITING"
    LEGAL_COMPLIANCE_COLLATERAL = "LEGAL_COMPLIANCE_COLLATERAL"


class ChallengeType(StrEnum):
    UNSUPPORTED_ASSUMPTION = "UNSUPPORTED_ASSUMPTION"
    OMITTED_RISK = "OMITTED_RISK"
    INADEQUATE_MITIGANT = "INADEQUATE_MITIGANT"
    GAP_VISIBILITY = "GAP_VISIBILITY"
    EXCEPTION_VISIBILITY = "EXCEPTION_VISIBILITY"
    OTHER_CONCERN = "OTHER_CONCERN"


class ChallengeSeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RecommendationType(StrEnum):
    """Advisory-only next steps.  Never a decision (AGENTS.md non-negotiable)."""

    REQUEST_INFORMATION = "REQUEST_INFORMATION"
    STRUCTURAL_CHANGE_SUGGESTION = "STRUCTURAL_CHANGE_SUGGESTION"
    MANUAL_REVIEW = "MANUAL_REVIEW"
    ESCALATION = "ESCALATION"


class RaisedBy(StrEnum):
    """Provenance of one challenge: deterministic pre-analysis or the LLM.

    Deterministic challenges are always present in the final output regardless
    of what the LLM returns; the LLM may only ADD challenges, never remove or
    relabel a deterministic one (enforced in
    ``application/risk_review/checker.py``, not here).
    """

    DETERMINISTIC = "DETERMINISTIC"
    LLM = "LLM"


class MakerFindingRef(BaseModel):
    """A pointer into one maker assessment: which one, and what path in it.

    ``section_path`` is a location string such as ``"business.findings[0]"``
    or ``"risks[2]"``, enumerated deterministically for one execution by
    ``application/risk_review/evidence.py``.  Grounding against the maker
    assessments actually reviewed is enforced by
    ``RiskReviewAssessment._targets_reference_reviewed_assessments``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    maker_source: MakerSource
    maker_assessment_id: UUID
    section_path: str = Field(min_length=1, max_length=200)


class ConfirmedFactCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["CONFIRMED_FACT"] = "CONFIRMED_FACT"
    confirmed_fact_id: ConfirmedFactId


class CalculatorResultCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["CALCULATOR_RESULT"] = "CALCULATOR_RESULT"
    result_id: str = Field(min_length=1)


class DocumentRegionCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["DOCUMENT_REGION"] = "DOCUMENT_REGION"
    document_version_id: DocumentVersionId
    region: str = Field(min_length=1, max_length=500)


class PolicyCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["POLICY_CITATION"] = "POLICY_CITATION"
    corpus_id: str = Field(min_length=1, max_length=200)
    corpus_version: str = Field(min_length=1, max_length=50)
    document_id: str = Field(min_length=1, max_length=200)
    clause_id: str = Field(min_length=1, max_length=50)
    quoted_text_vi: str = Field(min_length=1, max_length=2000)


class ControlledCheckCitation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["CONTROLLED_CHECK"] = "CONTROLLED_CHECK"
    invocation_id: UUID


class MakerFindingCitation(BaseModel):
    """Cites the exact maker passage a challenge disputes as its own evidence.

    Distinct from ``Challenge.target``: a challenge may (and typically does)
    cite the very finding it targets, but it may also cite an ADDITIONAL
    maker passage (e.g. a challenge on a risk citing the mitigant that fails
    to address it).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["MAKER_FINDING"] = "MAKER_FINDING"
    ref: MakerFindingRef


EvidenceCitation = Annotated[
    ConfirmedFactCitation
    | CalculatorResultCitation
    | DocumentRegionCitation
    | PolicyCitation
    | ControlledCheckCitation
    | MakerFindingCitation,
    Field(discriminator="kind"),
]


class Challenge(BaseModel):
    """One checker finding disputing a maker conclusion, assumption or omission.

    Always evidence-cited; persists until a human disposition (CONTEXT.md:
    "Disposition ... _Avoid_: resolution (agents cannot resolve)").  The
    checker itself has no field anywhere in this schema capable of marking a
    challenge resolved.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    target: MakerFindingRef
    challenge_type: ChallengeType
    statement_vi: str = Field(min_length=1, max_length=4000)
    citations: tuple[EvidenceCitation, ...] = Field(min_length=1)
    severity: ChallengeSeverity
    confidence: ConfidenceLevel
    raised_by: RaisedBy = RaisedBy.LLM


class OmittedRiskItem(BaseModel):
    """A material risk the checker judges the maker analysis omitted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description_vi: str = Field(min_length=1, max_length=4000)
    citations: tuple[EvidenceCitation, ...] = Field(min_length=1)
    confidence: ConfidenceLevel
    uncertainty_vi: str = Field(default="", max_length=2000)


class MitigantAdequacyReview(BaseModel):
    """Binds a maker risk+mitigant pair with an adequacy CONCERN.

    Deliberately not a verdict: the checker tests whether the mitigant
    plausibly addresses the stated risk and raises a concern when it does
    not; it never states the mitigant is adequate or inadequate as a
    conclusion the orchestration could treat as resolving anything.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_target: MakerFindingRef
    mitigant_target: MakerFindingRef
    concern_vi: str = Field(min_length=1, max_length=4000)
    citations: tuple[EvidenceCitation, ...] = Field(min_length=1)
    confidence: ConfidenceLevel
    uncertainty_vi: str = Field(default="", max_length=2000)


class VisibilityGapItem(BaseModel):
    """One BLOCKING gap declared by a maker, and whether it is still visible."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: MakerSource
    source_assessment_id: UUID
    missing_information_vi: str = Field(min_length=1, max_length=2000)
    blocking_level: GapBlockingLevel
    still_visible: bool


class VisibilityExceptionItem(BaseModel):
    """One exception declared by the legal reviewer, and whether it is still visible."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_assessment_id: UUID
    category: str = Field(min_length=1, max_length=50)
    possible_issue_vi: str = Field(min_length=1, max_length=4000)
    still_visible: bool


class VisibilityChecks(BaseModel):
    """Deterministically computed: every BLOCKING gap and exception, tracked.

    Built exclusively by ``application/risk_review/analysis.py`` BEFORE
    inference; the LLM never populates this section.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    blocking_gaps: tuple[VisibilityGapItem, ...] = ()
    exceptions: tuple[VisibilityExceptionItem, ...] = ()


class EvidenceGapItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    missing_information_vi: str = Field(min_length=1, max_length=2000)
    why_needed_vi: str = Field(min_length=1, max_length=2000)
    blocking_level: GapBlockingLevel
    suggested_evidence_vi: tuple[str, ...] = ()


class RecommendationItem(BaseModel):
    """An advisory next step.  Never a decision; a human authorizes any action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    recommendation_type: RecommendationType
    rationale_vi: str = Field(min_length=1, max_length=2000)
    citations: tuple[EvidenceCitation, ...] = ()


class MakerReviewedRef(BaseModel):
    """Identifies one maker execution the checker independently inspected."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    maker_source: MakerSource
    assessment_id: UUID
    execution_id: UUID


class RiskReviewProvenance(BaseModel):
    """Immutable provenance envelope recorded on every checker output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: CaseId
    case_version: int = Field(ge=1)
    agent_role: Literal["INDEPENDENT_RISK_REVIEW"] = RISK_REVIEW_AGENT_ROLE
    execution_id: UUID
    task_id: TaskId
    prompt_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    evidence_view_built_at: datetime
    created_at: datetime
    #: Both maker executions the checker independently inspected.  Readiness
    #: (application/orchestration/graph.py) already gates INDEPENDENT_RISK_REVIEW
    #: on both maker task types completing; this field is the schema-level
    #: mirror of that invariant -- a checker output that reviewed only one
    #: maker is structurally impossible.
    maker_assessments_reviewed: tuple[MakerReviewedRef, ...] = Field(min_length=2)


class RiskReviewAssessment(BaseModel):
    """The Checker's complete independent review for one case version.

    Append-only once persisted.  Contains challenges, omitted-risk items,
    mitigant-adequacy concerns, deterministic visibility checks,
    recommendations and evidence gaps -- and no approval, rejection,
    clearance, resolution, override, or credit decision of any kind.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: RiskReviewAssessmentId
    provenance: RiskReviewProvenance
    challenges: tuple[Challenge, ...] = ()
    omitted_risks: tuple[OmittedRiskItem, ...] = ()
    mitigant_adequacy_reviews: tuple[MitigantAdequacyReview, ...] = ()
    visibility_checks: VisibilityChecks
    recommendations: tuple[RecommendationItem, ...] = ()
    evidence_gaps: tuple[EvidenceGapItem, ...] = ()

    def _iter_all_citations(self) -> tuple[EvidenceCitation, ...]:
        citations: list[EvidenceCitation] = []
        for challenge in self.challenges:
            citations.extend(challenge.citations)
        for omitted in self.omitted_risks:
            citations.extend(omitted.citations)
        for review in self.mitigant_adequacy_reviews:
            citations.extend(review.citations)
        for recommendation in self.recommendations:
            citations.extend(recommendation.citations)
        return tuple(citations)

    @model_validator(mode="after")
    def _maker_assessments_reviewed_are_both_makers(self) -> Self:
        sources = {ref.maker_source for ref in self.provenance.maker_assessments_reviewed}
        if sources != {MakerSource.CREDIT_UNDERWRITING, MakerSource.LEGAL_COMPLIANCE_COLLATERAL}:
            raise ValueError(
                "an independent risk review must reference exactly one "
                "CREDIT_UNDERWRITING and one LEGAL_COMPLIANCE_COLLATERAL execution"
            )
        return self

    @model_validator(mode="after")
    def _checker_execution_is_independent(self) -> Self:
        # "The same role execution must not author and independently clear
        # the same material conclusion" (AGENT_ARCHITECTURE.md).  A checker
        # execution id colliding with a reviewed maker's execution id would
        # mean the same execution authored and challenged the same output.
        maker_execution_ids = {
            ref.execution_id for ref in self.provenance.maker_assessments_reviewed
        }
        if self.provenance.execution_id in maker_execution_ids:
            raise ValueError(
                "checker execution id must differ from every reviewed maker "
                "execution id (maker-checker separation)"
            )
        return self

    @model_validator(mode="after")
    def _targets_reference_reviewed_assessments(self) -> Self:
        known_ids = {ref.assessment_id for ref in self.provenance.maker_assessments_reviewed}

        def _check(ref: MakerFindingRef, where: str) -> None:
            if ref.maker_assessment_id not in known_ids:
                raise ValueError(
                    f"{where} references a maker assessment that was not "
                    f"reviewed: {ref.maker_assessment_id}"
                )

        for challenge in self.challenges:
            _check(challenge.target, "challenge target")
        for review in self.mitigant_adequacy_reviews:
            _check(review.risk_target, "mitigant adequacy risk_target")
            _check(review.mitigant_target, "mitigant adequacy mitigant_target")
        for citation in self._iter_all_citations():
            if isinstance(citation, MakerFindingCitation):
                _check(citation.ref, "MAKER_FINDING citation")
        return self


def _assert_no_forbidden_fields(model: type[BaseModel], seen: set[str]) -> None:
    name = model.__name__
    if name in seen:
        return
    seen.add(name)
    for field_name, field_info in model.model_fields.items():
        normalized = "".join(char for char in field_name.casefold() if char.isalnum())
        if normalized in FORBIDDEN_CHECKER_FIELD_NAMES:
            raise AssertionError(
                f"{name}.{field_name} would express a decision, clearance, or resolution"
            )
        annotation = field_info.annotation
        stack = [annotation]
        while stack:
            candidate = stack.pop()
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                _assert_no_forbidden_fields(candidate, seen)
            else:
                stack.extend(getattr(candidate, "__args__", ()))


# Import-time structural guard: the checker output schema can never grow a
# field capable of expressing a decision, approval, clearance, resolution, or
# override without failing every test run and worker start.
_assert_no_forbidden_fields(RiskReviewAssessment, set())
