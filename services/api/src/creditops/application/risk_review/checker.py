"""Checker: deterministic pre-analysis -> bounded, evidence-grounded inference.

Order is non-negotiable, mirroring ``application/underwriting/maker.py`` and
``application/legal/reviewer.py``: ``application/risk_review/analysis.py``
runs FIRST and produces ``visibility_checks`` plus a set of deterministic
challenges that are ALWAYS present in the final output.  The LLM then
receives the deterministic results, both maker assessments, and the case's
confirmed facts, and may only reference evidence through a closed JSON
response schema -- citation branches are enum/const-pinned to the exact
in-scope ids (confirmed facts, calculator results, maker finding/section
paths, policy citations, controlled-check invocation ids) so a well-formed
response cannot cite anything outside the scoped view or invent a maker
finding that does not exist.  The LLM may only ADD challenges; it can never
narrow, remove, or relabel a deterministic one, and it never populates
``visibility_checks`` at all.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from creditops.application.ports.model_gateway import InferenceGateway, ReasonRequest
from creditops.application.ports.risk_review import (
    CheckerEvidenceView,
    ProvisionalGapRecord,
    RiskReviewRepository,
)
from creditops.application.risk_review.analysis import DeterministicPreAnalysis
from creditops.application.risk_review.evidence import TargetUniverse
from creditops.domain.legal import (
    ControlledCheckResultRecord,
    LegalComplianceAssessment,
    PolicyHitRecord,
)
from creditops.domain.risk_review import (
    CalculatorResultCitation,
    Challenge,
    ChallengeSeverity,
    ChallengeType,
    ConfidenceLevel,
    ConfirmedFactCitation,
    ControlledCheckCitation,
    EvidenceCitation,
    EvidenceGapItem,
    GapBlockingLevel,
    MakerFindingCitation,
    MakerFindingRef,
    MakerReviewedRef,
    MakerSource,
    MitigantAdequacyReview,
    OmittedRiskItem,
    PolicyCitation,
    RaisedBy,
    RecommendationItem,
    RecommendationType,
    RiskReviewAssessment,
    RiskReviewProvenance,
)
from creditops.domain.underwriting import UnderwritingAssessment

RISK_REVIEW_PROMPT_VERSION = "risk-review-prompt-v1"
RISK_REVIEW_SCHEMA_VERSION = "risk-review-assessment-v1"
OPERATIONS_HANDOFF_STATE = "READY_FOR_OPERATIONS"


class CheckerOutputInvalid(ValueError):
    """The LLM response failed deterministic validation; bounded retry applies."""


class SameExecutionGuardTriggered(ValueError):
    """The checker execution id collided with a reviewed maker execution id.

    "The same role execution must not author and independently clear the
    same material conclusion" (docs/AGENT_ARCHITECTURE.md).  This is checked
    early (application/risk_review/processor.py) so a colliding execution
    never even reaches the model gateway; the domain schema also refuses to
    construct such an assessment as a second, independent safety net.
    """


class CheckerPrompt:
    """Load the versioned Vietnamese trusted-instruction checker prompt."""

    version = RISK_REVIEW_PROMPT_VERSION

    def __init__(self, text: str | None = None) -> None:
        self._text = text if text is not None else self._load()

    @staticmethod
    def _load() -> str:
        return (
            resources.files("creditops.prompts.risk_review")
            .joinpath("v1.md")
            .read_text(encoding="utf-8")
        )

    @property
    def text(self) -> str:
        return self._text


def _target_schema(universe: TargetUniverse) -> Mapping[str, Any]:
    return _filtered_target_schema(universe)


def _filtered_target_schema(
    universe: TargetUniverse,
    *,
    source: MakerSource | None = None,
    prefix: str | None = None,
) -> Mapping[str, Any]:
    branches: list[Mapping[str, Any]] = []
    if source is None or source is MakerSource.CREDIT_UNDERWRITING:
        paths = [p for p in universe.underwriting_paths if prefix is None or p.startswith(prefix)]
        if paths:
            branches.append(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["maker_source", "maker_assessment_id", "section_path"],
                    "properties": {
                        "maker_source": {"const": "CREDIT_UNDERWRITING"},
                        "maker_assessment_id": {
                            "const": str(universe.underwriting_assessment_id)
                        },
                        "section_path": {"enum": paths},
                    },
                }
            )
    if source is None or source is MakerSource.LEGAL_COMPLIANCE_COLLATERAL:
        paths = [p for p in universe.legal_paths if prefix is None or p.startswith(prefix)]
        if paths:
            branches.append(
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["maker_source", "maker_assessment_id", "section_path"],
                    "properties": {
                        "maker_source": {"const": "LEGAL_COMPLIANCE_COLLATERAL"},
                        "maker_assessment_id": {"const": str(universe.legal_assessment_id)},
                        "section_path": {"enum": paths},
                    },
                }
            )
    return {"oneOf": branches}


def _policy_citation_branches(hits: Sequence[PolicyHitRecord]) -> list[Mapping[str, Any]]:
    return [
        {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "kind",
                "corpus_id",
                "corpus_version",
                "document_id",
                "clause_id",
                "quoted_text_vi",
            ],
            "properties": {
                "kind": {"const": "POLICY_CITATION"},
                "corpus_id": {"const": hit.corpus_id},
                "corpus_version": {"const": hit.corpus_version},
                "document_id": {"const": hit.document_id},
                "clause_id": {"const": hit.clause_id},
                "quoted_text_vi": {"const": hit.quoted_text_vi},
            },
        }
        for hit in hits
    ]


def _citation_schema(
    *,
    fact_ids: Sequence[str],
    calculator_result_ids: Sequence[str],
    policy_hits: Sequence[PolicyHitRecord],
    invocation_ids: Sequence[str],
    universe: TargetUniverse,
) -> Mapping[str, Any]:
    branches: list[Mapping[str, Any]] = []
    if fact_ids:
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "confirmed_fact_id"],
                "properties": {
                    "kind": {"const": "CONFIRMED_FACT"},
                    "confirmed_fact_id": {"type": "string", "enum": list(fact_ids)},
                },
            }
        )
    if calculator_result_ids:
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "result_id"],
                "properties": {
                    "kind": {"const": "CALCULATOR_RESULT"},
                    "result_id": {"type": "string", "enum": list(calculator_result_ids)},
                },
            }
        )
    branches.extend(_policy_citation_branches(policy_hits))
    if invocation_ids:
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "invocation_id"],
                "properties": {
                    "kind": {"const": "CONTROLLED_CHECK"},
                    "invocation_id": {"type": "string", "enum": list(invocation_ids)},
                },
            }
        )
    branches.append(
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "ref"],
            "properties": {"kind": {"const": "MAKER_FINDING"}, "ref": _target_schema(universe)},
        }
    )
    return {"oneOf": branches}


def build_response_schema(
    *,
    fact_ids: Sequence[str],
    calculator_result_ids: Sequence[str],
    policy_hits: Sequence[PolicyHitRecord],
    invocation_ids: Sequence[str],
    universe: TargetUniverse,
) -> Mapping[str, Any]:
    """Closed response schema.  ``visibility_checks`` is deliberately absent --
    it is deterministic-only and never populated by the LLM."""

    citation = _citation_schema(
        fact_ids=fact_ids,
        calculator_result_ids=calculator_result_ids,
        policy_hits=policy_hits,
        invocation_ids=invocation_ids,
        universe=universe,
    )
    citations_array = {"type": "array", "minItems": 1, "maxItems": 10, "items": citation}
    challenge_item = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "target",
            "challenge_type",
            "statement_vi",
            "citations",
            "severity",
            "confidence",
        ],
        "properties": {
            "target": _target_schema(universe),
            "challenge_type": {
                "enum": [item.value for item in ChallengeType]
            },
            "statement_vi": {"type": "string", "minLength": 1, "maxLength": 4000},
            "citations": citations_array,
            "severity": {"enum": [item.value for item in ChallengeSeverity]},
            "confidence": {"enum": [item.value for item in ConfidenceLevel]},
        },
    }
    omitted_risk_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["description_vi", "citations", "confidence"],
        "properties": {
            "description_vi": {"type": "string", "minLength": 1, "maxLength": 4000},
            "citations": citations_array,
            "confidence": {"enum": [item.value for item in ConfidenceLevel]},
            "uncertainty_vi": {"type": "string", "maxLength": 2000},
        },
    }
    has_risks = any(path.startswith("risks[") for path in universe.underwriting_paths)
    has_mitigants = any(path.startswith("mitigants[") for path in universe.underwriting_paths)
    mitigant_review_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["risk_target", "mitigant_target", "concern_vi", "citations", "confidence"],
        "properties": {
            "risk_target": _filtered_target_schema(
                universe, source=MakerSource.CREDIT_UNDERWRITING, prefix="risks["
            ),
            "mitigant_target": _filtered_target_schema(
                universe, source=MakerSource.CREDIT_UNDERWRITING, prefix="mitigants["
            ),
            "concern_vi": {"type": "string", "minLength": 1, "maxLength": 4000},
            "citations": citations_array,
            "confidence": {"enum": [item.value for item in ConfidenceLevel]},
            "uncertainty_vi": {"type": "string", "maxLength": 2000},
        },
    }
    # A mitigant-adequacy review needs both a risk AND a mitigant to bind
    # together; if the underwriting assessment declared neither (or only
    # one), the array is structurally forced empty rather than building an
    # unsatisfiable ``oneOf: []`` branch (mirrors the legal reviewer's
    # ADR-0002 empty-corpus abstention pattern).
    mitigant_reviews_schema: Mapping[str, Any] = (
        {"type": "array", "maxItems": 20, "items": mitigant_review_item}
        if has_risks and has_mitigants
        else {"type": "array", "maxItems": 0}
    )
    recommendation_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["recommendation_type", "rationale_vi"],
        "properties": {
            "recommendation_type": {"enum": [item.value for item in RecommendationType]},
            "rationale_vi": {"type": "string", "minLength": 1, "maxLength": 2000},
            "citations": {"type": "array", "maxItems": 10, "items": citation},
        },
    }
    evidence_gap_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["missing_information_vi", "why_needed_vi", "blocking_level"],
        "properties": {
            "missing_information_vi": {"type": "string", "minLength": 1, "maxLength": 2000},
            "why_needed_vi": {"type": "string", "minLength": 1, "maxLength": 2000},
            "blocking_level": {"enum": ["BLOCKING", "CONDITIONAL", "CLARIFICATION"]},
            "suggested_evidence_vi": {
                "type": "array",
                "maxItems": 10,
                "items": {"type": "string", "maxLength": 500},
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "challenges",
            "omitted_risks",
            "mitigant_adequacy_reviews",
            "recommendations",
            "evidence_gaps",
        ],
        "properties": {
            "challenges": {"type": "array", "maxItems": 30, "items": challenge_item},
            "omitted_risks": {"type": "array", "maxItems": 20, "items": omitted_risk_item},
            "mitigant_adequacy_reviews": mitigant_reviews_schema,
            "recommendations": {"type": "array", "maxItems": 10, "items": recommendation_item},
            "evidence_gaps": {"type": "array", "maxItems": 30, "items": evidence_gap_item},
        },
    }


def _calculator_result_ids(underwriting: UnderwritingAssessment) -> tuple[str, ...]:
    ids: list[str] = [item.result_id for item in underwriting.calculator_results]
    ids.extend(item.result_id for item in underwriting.trend_results)
    ids.extend(item.result_id for item in underwriting.scenario_results)
    return tuple(ids)


def build_untrusted_context(
    *,
    view: CheckerEvidenceView,
    underwriting: UnderwritingAssessment,
    legal: LegalComplianceAssessment,
    pre_analysis: DeterministicPreAnalysis,
    universe: TargetUniverse,
) -> str:
    """Serialize the scoped evidence for the prompt's untrusted-data region."""

    payload: dict[str, Any] = {
        "caseId": str(view.case_id),
        "caseVersion": view.case_version,
        "confirmedFacts": [
            {
                "confirmedFactId": str(fact.confirmed_fact_id),
                "fieldKey": fact.field_key,
                "value": fact.value,
            }
            for fact in view.confirmed_facts
        ],
        "underwritingAssessment": underwriting.model_dump(mode="json"),
        "underwritingTargetPaths": list(universe.underwriting_paths),
        "legalAssessment": legal.model_dump(mode="json"),
        "legalTargetPaths": list(universe.legal_paths),
        "deterministicPreAnalysis": {
            "citationGroundingChallengeCount": len(pre_analysis.citation_grounding_challenges),
            "visibilityChallengeCount": len(pre_analysis.visibility_challenges),
            "unaddressedAssumptionChallengeCount": len(
                pre_analysis.unaddressed_assumption_challenges
            ),
            "lowConfidenceChallengeCount": len(pre_analysis.low_confidence_challenges),
            "note": (
                "Cac thach thuc tat dinh o tren da duoc tinh san va se luon co "
                "mat trong ket qua cuoi cung; ban KHONG can lap lai chung, "
                "chi bo sung them thach thuc moi neu co can cu."
            ),
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _target_from(raw: Mapping[str, Any], universe: TargetUniverse) -> MakerFindingRef:
    try:
        ref = MakerFindingRef(
            maker_source=MakerSource(str(raw.get("maker_source", ""))),
            maker_assessment_id=UUID(str(raw.get("maker_assessment_id", ""))),
            section_path=str(raw.get("section_path", "")),
        )
    except (ValueError, ValidationError) as exc:
        raise CheckerOutputInvalid(f"invalid target reference: {raw}") from exc
    if not universe.contains(ref):
        raise CheckerOutputInvalid(f"target does not resolve in the reviewed assessments: {ref}")
    return ref


def _citations_from(
    raw: Sequence[Mapping[str, Any]],
    *,
    known_fact_ids: set[str],
    known_result_ids: set[str],
    known_hits: set[tuple[str, str, str, str, str]],
    known_invocations: set[str],
    universe: TargetUniverse,
) -> tuple[EvidenceCitation, ...]:
    citations: list[EvidenceCitation] = []
    for item in raw:
        kind = item.get("kind")
        if kind == "CONFIRMED_FACT":
            fact_id = str(item.get("confirmed_fact_id", ""))
            if fact_id not in known_fact_ids:
                raise CheckerOutputInvalid(
                    f"citation references a confirmed fact outside scope: {fact_id}"
                )
            citations.append(ConfirmedFactCitation(confirmed_fact_id=UUID(fact_id)))
        elif kind == "CALCULATOR_RESULT":
            result_id = str(item.get("result_id", ""))
            if result_id not in known_result_ids:
                raise CheckerOutputInvalid(
                    f"citation references an unknown calculator result: {result_id}"
                )
            citations.append(CalculatorResultCitation(result_id=result_id))
        elif kind == "POLICY_CITATION":
            key = (
                str(item.get("corpus_id", "")),
                str(item.get("corpus_version", "")),
                str(item.get("document_id", "")),
                str(item.get("clause_id", "")),
                str(item.get("quoted_text_vi", "")),
            )
            if key not in known_hits:
                raise CheckerOutputInvalid(f"policy citation does not resolve: {key}")
            citations.append(
                PolicyCitation(
                    corpus_id=key[0],
                    corpus_version=key[1],
                    document_id=key[2],
                    clause_id=key[3],
                    quoted_text_vi=key[4],
                )
            )
        elif kind == "CONTROLLED_CHECK":
            invocation_id = str(item.get("invocation_id", ""))
            if invocation_id not in known_invocations:
                raise CheckerOutputInvalid(
                    f"citation references an unknown controlled-check invocation: {invocation_id}"
                )
            citations.append(ControlledCheckCitation(invocation_id=UUID(invocation_id)))
        elif kind == "MAKER_FINDING":
            ref_raw = item.get("ref")
            if not isinstance(ref_raw, Mapping):
                raise CheckerOutputInvalid("MAKER_FINDING citation missing ref")
            citations.append(MakerFindingCitation(ref=_target_from(ref_raw, universe)))
        else:
            raise CheckerOutputInvalid(f"unsupported citation kind: {kind!r}")
    return tuple(citations)


@dataclass(frozen=True, slots=True)
class CheckerRunContext:
    """Identity of one checker execution, recorded in provenance."""

    task_id: UUID
    execution_id: UUID
    correlation_id: str


class BuildAssessment:
    """Deterministically validate an LLM payload into a RiskReviewAssessment.

    Deterministic challenges are ALWAYS included regardless of what the LLM
    returns; ``visibility_checks`` is taken exclusively from the pre-analysis,
    never from the LLM payload.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or uuid4

    def build(
        self,
        *,
        payload: Mapping[str, Any],
        view: CheckerEvidenceView,
        underwriting: UnderwritingAssessment,
        underwriting_execution_id: UUID,
        legal: LegalComplianceAssessment,
        legal_execution_id: UUID,
        pre_analysis: DeterministicPreAnalysis,
        universe: TargetUniverse,
        policy_hits: Sequence[PolicyHitRecord],
        controlled_check_results: Sequence[ControlledCheckResultRecord],
        run: CheckerRunContext,
        model_id: str,
        endpoint_id: str,
    ) -> RiskReviewAssessment:
        try:
            return self._build(
                payload=payload,
                view=view,
                underwriting=underwriting,
                underwriting_execution_id=underwriting_execution_id,
                legal=legal,
                legal_execution_id=legal_execution_id,
                pre_analysis=pre_analysis,
                universe=universe,
                policy_hits=policy_hits,
                controlled_check_results=controlled_check_results,
                run=run,
                model_id=model_id,
                endpoint_id=endpoint_id,
            )
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, CheckerOutputInvalid | SameExecutionGuardTriggered):
                raise
            raise CheckerOutputInvalid(f"checker output rejected: {exc}") from exc

    def _build(
        self,
        *,
        payload: Mapping[str, Any],
        view: CheckerEvidenceView,
        underwriting: UnderwritingAssessment,
        underwriting_execution_id: UUID,
        legal: LegalComplianceAssessment,
        legal_execution_id: UUID,
        pre_analysis: DeterministicPreAnalysis,
        universe: TargetUniverse,
        policy_hits: Sequence[PolicyHitRecord],
        controlled_check_results: Sequence[ControlledCheckResultRecord],
        run: CheckerRunContext,
        model_id: str,
        endpoint_id: str,
    ) -> RiskReviewAssessment:
        if run.execution_id in (underwriting_execution_id, legal_execution_id):
            raise SameExecutionGuardTriggered(
                "checker execution id must differ from every reviewed maker execution id"
            )

        known_fact_ids = {str(fact.confirmed_fact_id) for fact in view.confirmed_facts}
        known_result_ids = set(_calculator_result_ids(underwriting))
        known_hits = {
            (hit.corpus_id, hit.corpus_version, hit.document_id, hit.clause_id, hit.quoted_text_vi)
            for hit in policy_hits
        }
        known_invocations = {str(result.invocation_id) for result in controlled_check_results}

        def citations_from(raw: Sequence[Mapping[str, Any]]) -> tuple[EvidenceCitation, ...]:
            return _citations_from(
                raw,
                known_fact_ids=known_fact_ids,
                known_result_ids=known_result_ids,
                known_hits=known_hits,
                known_invocations=known_invocations,
                universe=universe,
            )

        raw_challenges = payload.get("challenges", [])
        if not isinstance(raw_challenges, list):
            raise CheckerOutputInvalid("challenges must be a list")
        llm_challenges = tuple(
            Challenge(
                id=self._id_factory(),
                target=_target_from(dict(item.get("target", {})), universe),
                challenge_type=ChallengeType(str(item.get("challenge_type", ""))),
                statement_vi=str(item.get("statement_vi", "")),
                citations=citations_from(list(item.get("citations", []))),
                severity=ChallengeSeverity(str(item.get("severity", ""))),
                confidence=ConfidenceLevel(str(item.get("confidence", ""))),
                raised_by=RaisedBy.LLM,
            )
            for item in raw_challenges
            if isinstance(item, Mapping)
        )
        # Deterministic challenges are always present; the LLM may only ADD.
        challenges = pre_analysis.all_challenges + llm_challenges

        raw_omitted = payload.get("omitted_risks", [])
        if not isinstance(raw_omitted, list):
            raise CheckerOutputInvalid("omitted_risks must be a list")
        omitted_risks = tuple(
            OmittedRiskItem(
                description_vi=str(item.get("description_vi", "")),
                citations=citations_from(list(item.get("citations", []))),
                confidence=ConfidenceLevel(str(item.get("confidence", ""))),
                uncertainty_vi=str(item.get("uncertainty_vi", "")),
            )
            for item in raw_omitted
            if isinstance(item, Mapping)
        )

        raw_reviews = payload.get("mitigant_adequacy_reviews", [])
        if not isinstance(raw_reviews, list):
            raise CheckerOutputInvalid("mitigant_adequacy_reviews must be a list")
        mitigant_reviews = tuple(
            MitigantAdequacyReview(
                risk_target=_target_from(dict(item.get("risk_target", {})), universe),
                mitigant_target=_target_from(dict(item.get("mitigant_target", {})), universe),
                concern_vi=str(item.get("concern_vi", "")),
                citations=citations_from(list(item.get("citations", []))),
                confidence=ConfidenceLevel(str(item.get("confidence", ""))),
                uncertainty_vi=str(item.get("uncertainty_vi", "")),
            )
            for item in raw_reviews
            if isinstance(item, Mapping)
        )

        raw_recommendations = payload.get("recommendations", [])
        if not isinstance(raw_recommendations, list):
            raise CheckerOutputInvalid("recommendations must be a list")
        recommendations = tuple(
            RecommendationItem(
                recommendation_type=RecommendationType(str(item.get("recommendation_type", ""))),
                rationale_vi=str(item.get("rationale_vi", "")),
                citations=citations_from(list(item.get("citations", []))),
            )
            for item in raw_recommendations
            if isinstance(item, Mapping)
        )

        raw_gaps = payload.get("evidence_gaps", [])
        if not isinstance(raw_gaps, list):
            raise CheckerOutputInvalid("evidence_gaps must be a list")
        llm_gaps = tuple(
            EvidenceGapItem(
                missing_information_vi=str(item.get("missing_information_vi", "")),
                why_needed_vi=str(item.get("why_needed_vi", "")),
                blocking_level=GapBlockingLevel(str(item.get("blocking_level", ""))),
                suggested_evidence_vi=tuple(
                    str(entry) for entry in item.get("suggested_evidence_vi", [])
                ),
            )
            for item in raw_gaps
            if isinstance(item, Mapping)
        )

        now = self._clock()
        return RiskReviewAssessment(
            id=self._id_factory(),
            provenance=RiskReviewProvenance(
                case_id=view.case_id,
                case_version=view.case_version,
                execution_id=run.execution_id,
                task_id=run.task_id,
                prompt_version=RISK_REVIEW_PROMPT_VERSION,
                model_id=model_id,
                endpoint_id=endpoint_id,
                evidence_view_built_at=view.built_at,
                created_at=now,
                maker_assessments_reviewed=(
                    MakerReviewedRef(
                        maker_source=MakerSource.CREDIT_UNDERWRITING,
                        assessment_id=underwriting.id,
                        execution_id=underwriting_execution_id,
                    ),
                    MakerReviewedRef(
                        maker_source=MakerSource.LEGAL_COMPLIANCE_COLLATERAL,
                        assessment_id=legal.id,
                        execution_id=legal_execution_id,
                    ),
                ),
            ),
            challenges=challenges,
            omitted_risks=omitted_risks,
            mitigant_adequacy_reviews=mitigant_reviews,
            visibility_checks=pre_analysis.visibility_checks,
            recommendations=recommendations,
            evidence_gaps=llm_gaps,
        )


class RunCheckerInference:
    """Call the reasoning endpoint with the closed checker schema and validate."""

    def __init__(
        self,
        gateway: InferenceGateway,
        *,
        prompt: CheckerPrompt | None = None,
        builder: BuildAssessment | None = None,
    ) -> None:
        self._gateway = gateway
        self._prompt = prompt or CheckerPrompt()
        self._builder = builder or BuildAssessment()

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def infer(
        self,
        *,
        view: CheckerEvidenceView,
        underwriting: UnderwritingAssessment,
        underwriting_execution_id: UUID,
        legal: LegalComplianceAssessment,
        legal_execution_id: UUID,
        pre_analysis: DeterministicPreAnalysis,
        universe: TargetUniverse,
        policy_hits: Sequence[PolicyHitRecord],
        controlled_check_results: Sequence[ControlledCheckResultRecord],
        run: CheckerRunContext,
    ) -> RiskReviewAssessment:
        if run.execution_id in (underwriting_execution_id, legal_execution_id):
            raise SameExecutionGuardTriggered(
                "checker execution id must differ from every reviewed maker execution id"
            )
        fact_ids = tuple(str(fact.confirmed_fact_id) for fact in view.confirmed_facts)
        schema = build_response_schema(
            fact_ids=fact_ids,
            calculator_result_ids=_calculator_result_ids(underwriting),
            policy_hits=policy_hits,
            invocation_ids=[str(result.invocation_id) for result in controlled_check_results],
            universe=universe,
        )
        result = await self._gateway.reason(
            ReasonRequest(
                correlation_id=run.correlation_id,
                case_id=view.case_id,
                content=build_untrusted_context(
                    view=view,
                    underwriting=underwriting,
                    legal=legal,
                    pre_analysis=pre_analysis,
                    universe=universe,
                ),
                response_schema=schema,
                system_context=self._prompt.text,
            )
        )
        if not isinstance(result.payload, Mapping):
            raise CheckerOutputInvalid("checker output is not a JSON object")
        return self._builder.build(
            payload=result.payload,
            view=view,
            underwriting=underwriting,
            underwriting_execution_id=underwriting_execution_id,
            legal=legal,
            legal_execution_id=legal_execution_id,
            pre_analysis=pre_analysis,
            universe=universe,
            policy_hits=policy_hits,
            controlled_check_results=controlled_check_results,
            run=run,
            model_id=result.model_id,
            endpoint_id=result.endpoint_id,
        )


def gap_records_from(assessment: RiskReviewAssessment) -> tuple[ProvisionalGapRecord, ...]:
    return tuple(
        ProvisionalGapRecord(
            issue_vi=gap.why_needed_vi,
            missing_information_vi=gap.missing_information_vi,
            blocking_level=gap.blocking_level,
            suggested_evidence_vi=gap.suggested_evidence_vi,
        )
        for gap in assessment.evidence_gaps
    )


async def persist_checker_output(
    repository: RiskReviewRepository,
    assessment: RiskReviewAssessment,
    *,
    handoff_id: UUID,
) -> Any:
    """Persist assessment + challenges + PROVISIONAL gaps + ops handoff atomically."""

    return await repository.persist_assessment(
        assessment=assessment,
        handoff_id=handoff_id,
        handoff_state=OPERATIONS_HANDOFF_STATE,
        gaps=gap_records_from(assessment),
    )


__all__ = [
    "OPERATIONS_HANDOFF_STATE",
    "RISK_REVIEW_PROMPT_VERSION",
    "RISK_REVIEW_SCHEMA_VERSION",
    "BuildAssessment",
    "CheckerOutputInvalid",
    "CheckerPrompt",
    "CheckerRunContext",
    "RunCheckerInference",
    "SameExecutionGuardTriggered",
    "build_response_schema",
    "build_untrusted_context",
    "gap_records_from",
    "persist_checker_output",
]
