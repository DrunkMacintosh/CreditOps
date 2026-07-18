"""Credit Underwriting maker: evidence view -> calculators -> bounded inference.

Order is non-negotiable: the deterministic calculators run FIRST, then the LLM
receives their results and may only reference them by ``result_id`` through a
closed JSON response schema (citation enums are the literal in-scope ids, so a
well-formed response cannot cite evidence outside the scoped view).  The LLM
interprets; it never computes, decides, or promotes an assumption to a fact.
Missing evidence becomes a PROVISIONAL Evidence Gap deterministically — with no
configured reasoning endpoint there is NO assessment (fail closed): the
calculators alone must not fabricate analysis.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from importlib import resources
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from creditops.application.ports.model_gateway import (
    InferenceGateway,
    ReasonRequest,
)
from creditops.application.ports.underwriting import (
    EvidenceView,
    ProvisionalGapRecord,
    UnderwritingRepository,
)
from creditops.application.underwriting.calculators import (
    ComputedOutcome,
)
from creditops.application.underwriting.evidence import (
    CalculatorSuite,
    build_calculator_suite,
)
from creditops.domain.underwriting import (
    AssessmentProvenance,
    AssessmentSection,
    AssumptionItem,
    CalculatorResultCitation,
    ConfidenceLevel,
    ConfirmedFactCitation,
    EvidenceCitation,
    EvidenceGapItem,
    Finding,
    GapBlockingLevel,
    MitigantItem,
    ProposedStructureSection,
    RepaymentSourceSection,
    RiskItem,
    UnderwritingAssessment,
)

UNDERWRITING_PROMPT_VERSION = "underwriting-prompt-v1"
UNDERWRITING_SCHEMA_VERSION = "underwriting-assessment-v1"
RISK_REVIEW_HANDOFF_STATE = "READY_FOR_RISK_REVIEW"


class MakerOutputInvalid(ValueError):
    """The LLM response failed deterministic validation; bounded retry applies."""


class UnderwritingPrompt:
    """Load the versioned Vietnamese trusted-instruction maker prompt."""

    version = UNDERWRITING_PROMPT_VERSION

    def __init__(self, text: str | None = None) -> None:
        self._text = text if text is not None else self._load()

    @staticmethod
    def _load() -> str:
        return (
            resources.files("creditops.prompts.underwriting")
            .joinpath("v1.md")
            .read_text(encoding="utf-8")
        )

    @property
    def text(self) -> str:
        return self._text


def _citation_schema(
    fact_ids: Sequence[str], result_ids: Sequence[str]
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
    if result_ids:
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "result_id"],
                "properties": {
                    "kind": {"const": "CALCULATOR_RESULT"},
                    "result_id": {"type": "string", "enum": list(result_ids)},
                },
            }
        )
    return {"oneOf": branches}


def _finding_schema(citation: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["statement_vi", "citations", "confidence"],
        "properties": {
            "statement_vi": {"type": "string", "minLength": 1, "maxLength": 4000},
            "citations": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": citation,
            },
            "confidence": {"enum": ["HIGH", "MEDIUM", "LOW"]},
            "uncertainty_vi": {"type": "string", "maxLength": 2000},
        },
    }


def build_response_schema(
    fact_ids: Sequence[str], result_ids: Sequence[str]
) -> Mapping[str, Any]:
    """Closed response schema; numeric analysis references calculator ids only.

    ``additionalProperties: false`` everywhere plus citation-id enums means the
    validated response cannot carry a decision field, an uncited conclusion, or
    a citation to evidence outside the scoped view.  The proposed amount is a
    ``result_id`` reference — the LLM cannot state a number for it.
    """

    citation = _citation_schema(fact_ids, result_ids)
    finding = _finding_schema(citation)
    findings = {"type": "array", "minItems": 1, "maxItems": 20, "items": finding}
    section = {
        "type": "object",
        "additionalProperties": False,
        "required": ["findings"],
        "properties": {"findings": findings},
    }
    risk_item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["risk_id", "description_vi", "citations", "confidence"],
        "properties": {
            "risk_id": {"type": "string", "minLength": 1, "maxLength": 100},
            "description_vi": {"type": "string", "minLength": 1, "maxLength": 4000},
            "citations": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": citation,
            },
            "confidence": {"enum": ["HIGH", "MEDIUM", "LOW"]},
            "uncertainty_vi": {"type": "string", "maxLength": 2000},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "business",
            "financial",
            "cash_flow",
            "repayment_source",
            "proposed_structure",
            "risks",
            "mitigants",
            "assumptions",
            "evidence_gaps",
        ],
        "properties": {
            "business": section,
            "financial": section,
            "cash_flow": section,
            "repayment_source": {
                "type": "object",
                "additionalProperties": False,
                "required": ["findings", "downside_scenarios"],
                "properties": {
                    "findings": findings,
                    "downside_scenarios": {
                        "type": "array",
                        "maxItems": 10,
                        "items": finding,
                    },
                },
            },
            "proposed_structure": {
                "type": "object",
                "additionalProperties": False,
                "required": ["instrument_vi", "findings"],
                "properties": {
                    "instrument_vi": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 500,
                    },
                    "proposed_amount_result_id": {
                        "type": ["string", "null"],
                        **({"enum": [*result_ids, None]} if result_ids else {}),
                    },
                    "tenor_months": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                        "maximum": 600,
                    },
                    "findings": findings,
                },
            },
            "risks": {"type": "array", "maxItems": 20, "items": risk_item},
            "mitigants": {"type": "array", "maxItems": 20, "items": risk_item},
            "assumptions": {
                "type": "array",
                "maxItems": 20,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["statement_vi", "rationale_vi"],
                    "properties": {
                        "statement_vi": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4000,
                        },
                        "rationale_vi": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 4000,
                        },
                        "basis_citations": {
                            "type": "array",
                            "maxItems": 20,
                            "items": citation,
                        },
                    },
                },
            },
            "evidence_gaps": {
                "type": "array",
                "maxItems": 30,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "missing_information_vi",
                        "why_needed_vi",
                        "blocking_level",
                    ],
                    "properties": {
                        "missing_information_vi": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 2000,
                        },
                        "why_needed_vi": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 2000,
                        },
                        "blocking_level": {
                            "enum": ["BLOCKING", "CONDITIONAL", "CLARIFICATION"]
                        },
                        "suggested_evidence_vi": {
                            "type": "array",
                            "maxItems": 10,
                            "items": {"type": "string", "maxLength": 500},
                        },
                    },
                },
            },
        },
    }


def _outcome_json(outcome: object) -> Mapping[str, Any]:
    if isinstance(outcome, ComputedOutcome):
        return {"status": "COMPUTED", "value": format(outcome.value, "f")}
    reason = getattr(outcome, "reason", "not computable")
    return {"status": "NOT_COMPUTABLE", "reason": str(reason)}


def build_untrusted_context(view: EvidenceView, suite: CalculatorSuite) -> str:
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
        "calculatorResults": [
            {
                "resultId": result.result_id,
                "calculator": result.calculator,
                "outcome": _outcome_json(result.outcome),
            }
            for result in suite.results
        ],
        "trendResults": [
            {
                "resultId": trend.result_id,
                "metric": trend.metric,
                "steps": [
                    {
                        "fromPeriod": step.from_period,
                        "toPeriod": step.to_period,
                        "delta": _outcome_json(step.delta),
                        "growthRate": _outcome_json(step.growth_rate),
                    }
                    for step in trend.steps
                ],
            }
            for trend in suite.trend_results
        ],
        "scenarioResults": [
            {
                "resultId": scenario.result_id,
                "scenarioName": scenario.scenario_name,
                "adjustments": [
                    {
                        "metric": adj.metric,
                        "relativeChange": format(adj.relative_change, "f"),
                        "absoluteChange": format(adj.absolute_change, "f"),
                    }
                    for adj in scenario.adjustments
                ],
                "metrics": [
                    {
                        "metric": metric.metric,
                        "base": _outcome_json(metric.base),
                        "adjusted": _outcome_json(metric.adjusted),
                    }
                    for metric in scenario.metrics
                ],
            }
            for scenario in suite.scenario_results
        ],
        "missingEvidence": [
            {
                "inputName": item.input_name,
                "fieldKey": item.field_key,
                "reason": item.reason,
                "blockingLevel": item.blocking_level.value,
            }
            for item in suite.missing
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def deterministic_gap_items(suite: CalculatorSuite) -> tuple[EvidenceGapItem, ...]:
    """Missing calculator inputs become Evidence Gap items — never guesses."""

    items: list[EvidenceGapItem] = []
    seen: set[str] = set()
    for missing in suite.missing:
        missing_information = (
            f"Thieu du kien da xac nhan cho truong '{missing.field_key}'"
            f" ({missing.reason})."
        )
        if missing_information in seen:
            continue
        seen.add(missing_information)
        items.append(
            EvidenceGapItem(
                missing_information_vi=missing_information,
                why_needed_vi=(
                    "Can cho tinh toan xac dinh "
                    f"'{missing.input_name}' trong phan tich tin dung."
                ),
                blocking_level=missing.blocking_level,
                suggested_evidence_vi=(
                    "Bao cao tai chinh da xac nhan boi can bo phu trach.",
                ),
            )
        )
    return tuple(items)


def _citations_from(
    raw: Sequence[Mapping[str, Any]], view: EvidenceView
) -> tuple[EvidenceCitation, ...]:
    known_fact_ids = {str(fact.confirmed_fact_id) for fact in view.confirmed_facts}
    citations: list[EvidenceCitation] = []
    for item in raw:
        kind = item.get("kind")
        if kind == "CONFIRMED_FACT":
            fact_id = str(item.get("confirmed_fact_id", ""))
            if fact_id not in known_fact_ids:
                raise MakerOutputInvalid(
                    f"citation references a confirmed fact outside the scoped "
                    f"evidence view: {fact_id}"
                )
            citations.append(
                ConfirmedFactCitation(confirmed_fact_id=UUID(fact_id))
            )
        elif kind == "CALCULATOR_RESULT":
            citations.append(
                CalculatorResultCitation(result_id=str(item.get("result_id", "")))
            )
        else:
            raise MakerOutputInvalid(f"unsupported citation kind: {kind!r}")
    return tuple(citations)


def _finding_from(item: Mapping[str, Any], view: EvidenceView) -> Finding:
    raw_citations = item.get("citations")
    if not isinstance(raw_citations, list) or not raw_citations:
        raise MakerOutputInvalid("a finding without citations is not acceptable")
    return Finding(
        statement_vi=str(item.get("statement_vi", "")),
        citations=_citations_from(raw_citations, view),
        confidence=ConfidenceLevel(str(item.get("confidence", ""))),
        uncertainty_vi=str(item.get("uncertainty_vi", "")),
    )


def _findings_from(
    payload: Mapping[str, Any], key: str, view: EvidenceView
) -> tuple[Finding, ...]:
    section = payload.get(key)
    if not isinstance(section, Mapping):
        raise MakerOutputInvalid(f"section {key} is missing")
    raw = section.get("findings")
    if not isinstance(raw, list):
        raise MakerOutputInvalid(f"section {key} has no findings list")
    return tuple(
        _finding_from(item, view) for item in raw if isinstance(item, Mapping)
    )


@dataclass(frozen=True, slots=True)
class MakerRunContext:
    """Identity of one maker execution, recorded in provenance."""

    task_id: UUID
    execution_id: UUID
    correlation_id: str


class BuildAssessment:
    """Deterministically validate an LLM payload into an UnderwritingAssessment.

    Any structural problem raises ``MakerOutputInvalid`` — the caller converts
    that into the bounded durable retry path; nothing invalid is persisted.
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
        view: EvidenceView,
        suite: CalculatorSuite,
        run: MakerRunContext,
        model_id: str,
        endpoint_id: str,
    ) -> UnderwritingAssessment:
        try:
            return self._build(
                payload=payload,
                view=view,
                suite=suite,
                run=run,
                model_id=model_id,
                endpoint_id=endpoint_id,
            )
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            if isinstance(exc, MakerOutputInvalid):
                raise
            raise MakerOutputInvalid(f"maker output rejected: {exc}") from exc

    def _build(
        self,
        *,
        payload: Mapping[str, Any],
        view: EvidenceView,
        suite: CalculatorSuite,
        run: MakerRunContext,
        model_id: str,
        endpoint_id: str,
    ) -> UnderwritingAssessment:
        repayment_raw = payload.get("repayment_source")
        if not isinstance(repayment_raw, Mapping):
            raise MakerOutputInvalid("section repayment_source is missing")
        downside_raw = repayment_raw.get("downside_scenarios", [])
        downside = tuple(
            _finding_from(item, view)
            for item in downside_raw
            if isinstance(item, Mapping)
        )

        structure_raw = payload.get("proposed_structure")
        if not isinstance(structure_raw, Mapping):
            raise MakerOutputInvalid("section proposed_structure is missing")
        amount = self._resolve_amount(structure_raw, suite)
        tenor = structure_raw.get("tenor_months")
        structure = ProposedStructureSection(
            instrument_vi=str(structure_raw.get("instrument_vi", "")),
            proposed_amount_vnd=amount,
            tenor_months=int(tenor) if isinstance(tenor, int) else None,
            findings=_findings_from(payload, "proposed_structure", view),
        )

        risks = tuple(
            RiskItem(
                risk_id=str(item.get("risk_id", "")),
                description_vi=str(item.get("description_vi", "")),
                citations=_citations_from(list(item.get("citations", [])), view),
                confidence=ConfidenceLevel(str(item.get("confidence", ""))),
                uncertainty_vi=str(item.get("uncertainty_vi", "")),
            )
            for item in payload.get("risks", [])
            if isinstance(item, Mapping)
        )
        mitigants = tuple(
            MitigantItem(
                risk_id=str(item.get("risk_id", "")),
                description_vi=str(item.get("description_vi", "")),
                citations=_citations_from(list(item.get("citations", [])), view),
                confidence=ConfidenceLevel(str(item.get("confidence", ""))),
                uncertainty_vi=str(item.get("uncertainty_vi", "")),
            )
            for item in payload.get("mitigants", [])
            if isinstance(item, Mapping)
        )
        assumptions = tuple(
            AssumptionItem(
                statement_vi=str(item.get("statement_vi", "")),
                rationale_vi=str(item.get("rationale_vi", "")),
                basis_citations=_citations_from(
                    list(item.get("basis_citations", [])), view
                ),
            )
            for item in payload.get("assumptions", [])
            if isinstance(item, Mapping)
        )
        llm_gaps = tuple(
            EvidenceGapItem(
                missing_information_vi=str(item.get("missing_information_vi", "")),
                why_needed_vi=str(item.get("why_needed_vi", "")),
                blocking_level=GapBlockingLevel(str(item.get("blocking_level", ""))),
                suggested_evidence_vi=tuple(
                    str(entry) for entry in item.get("suggested_evidence_vi", [])
                ),
            )
            for item in payload.get("evidence_gaps", [])
            if isinstance(item, Mapping)
        )
        # Deterministic missing-evidence gaps are always present, whether or
        # not the model declared them; duplicates collapse on the text key.
        deterministic = deterministic_gap_items(suite)
        seen = {gap.missing_information_vi for gap in deterministic}
        merged_gaps = deterministic + tuple(
            gap for gap in llm_gaps if gap.missing_information_vi not in seen
        )

        now = self._clock()
        return UnderwritingAssessment(
            id=self._id_factory(),
            provenance=AssessmentProvenance(
                case_id=view.case_id,
                case_version=view.case_version,
                execution_id=run.execution_id,
                task_id=run.task_id,
                prompt_version=UNDERWRITING_PROMPT_VERSION,
                model_id=model_id,
                endpoint_id=endpoint_id,
                evidence_view_built_at=view.built_at,
                created_at=now,
            ),
            business=AssessmentSection(
                findings=_findings_from(payload, "business", view)
            ),
            financial=AssessmentSection(
                findings=_findings_from(payload, "financial", view)
            ),
            cash_flow=AssessmentSection(
                findings=_findings_from(payload, "cash_flow", view)
            ),
            repayment_source=RepaymentSourceSection(
                findings=_findings_from(payload, "repayment_source", view),
                downside_scenarios=downside,
            ),
            proposed_structure=structure,
            risks=risks,
            mitigants=mitigants,
            assumptions=assumptions,
            evidence_gaps=merged_gaps,
            calculator_results=suite.results,
            trend_results=suite.trend_results,
            scenario_results=suite.scenario_results,
        )

    @staticmethod
    def _resolve_amount(
        structure_raw: Mapping[str, Any], suite: CalculatorSuite
    ) -> Decimal | None:
        """The amount is resolved from a calculator result — never LLM-stated."""

        result_id = structure_raw.get("proposed_amount_result_id")
        if result_id is None:
            return None
        by_id: dict[str, ComputedOutcome] = {}
        for result in suite.results:
            if isinstance(result.outcome, ComputedOutcome):
                by_id[result.result_id] = result.outcome
        outcome = by_id.get(str(result_id))
        if outcome is None:
            raise MakerOutputInvalid(
                "proposed amount references an unknown or not-computable "
                f"calculator result: {result_id}"
            )
        return outcome.value


class RunUnderwritingInference:
    """Call the reasoning endpoint with the closed maker schema and validate."""

    def __init__(
        self,
        gateway: InferenceGateway,
        *,
        prompt: UnderwritingPrompt | None = None,
        builder: BuildAssessment | None = None,
    ) -> None:
        self._gateway = gateway
        self._prompt = prompt or UnderwritingPrompt()
        self._builder = builder or BuildAssessment()

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    async def infer(
        self,
        *,
        view: EvidenceView,
        suite: CalculatorSuite,
        run: MakerRunContext,
    ) -> UnderwritingAssessment:
        fact_ids = tuple(str(fact.confirmed_fact_id) for fact in view.confirmed_facts)
        schema = build_response_schema(fact_ids, suite.result_ids())
        result = await self._gateway.reason(
            ReasonRequest(
                correlation_id=run.correlation_id,
                case_id=view.case_id,
                content=build_untrusted_context(view, suite),
                response_schema=schema,
                system_context=self._prompt.text,
            )
        )
        if not isinstance(result.payload, Mapping):
            raise MakerOutputInvalid("maker output is not a JSON object")
        return self._builder.build(
            payload=result.payload,
            view=view,
            suite=suite,
            run=run,
            model_id=result.model_id,
            endpoint_id=result.endpoint_id,
        )


def gap_records_from(assessment: UnderwritingAssessment) -> tuple[
    ProvisionalGapRecord, ...
]:
    return tuple(
        ProvisionalGapRecord(
            issue_vi=gap.why_needed_vi,
            missing_information_vi=gap.missing_information_vi,
            blocking_level=gap.blocking_level,
            suggested_evidence_vi=gap.suggested_evidence_vi,
        )
        for gap in assessment.evidence_gaps
    )


async def persist_maker_output(
    repository: UnderwritingRepository,
    assessment: UnderwritingAssessment,
    *,
    handoff_id: UUID,
) -> Any:
    """Persist assessment + PROVISIONAL gaps + maker->checker handoff atomically."""

    return await repository.persist_assessment(
        assessment=assessment,
        handoff_id=handoff_id,
        handoff_state=RISK_REVIEW_HANDOFF_STATE,
        gaps=gap_records_from(assessment),
    )


__all__ = [
    "RISK_REVIEW_HANDOFF_STATE",
    "UNDERWRITING_PROMPT_VERSION",
    "UNDERWRITING_SCHEMA_VERSION",
    "BuildAssessment",
    "MakerOutputInvalid",
    "MakerRunContext",
    "RunUnderwritingInference",
    "UnderwritingPrompt",
    "build_calculator_suite",
    "build_response_schema",
    "build_untrusted_context",
    "deterministic_gap_items",
    "gap_records_from",
    "persist_maker_output",
]
