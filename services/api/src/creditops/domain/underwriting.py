"""Structured output contract for the Credit Underwriting Agent (the Maker).

Design invariants (docs/AGENT_ARCHITECTURE.md, AGENTS.md):

- The schema has NO field capable of expressing an approval, rejection, credit
  score, policy waiver, or legal determination.  ``extra="forbid"`` plus the
  module-level schema guard makes a decision field a construction-time error,
  not a reviewable mistake.
- Every material finding carries at least one evidence citation (Confirmed
  Fact, deterministic calculator result, or document region).  A finding with
  no citations is structurally impossible.
- An assumption is a separate type from a finding and can never be promoted
  into one implicitly; missing evidence is expressed as an Evidence Gap item.
- The full provenance envelope (case, case version, agent role, execution id,
  timestamps) is embedded so the assessment is auditable stand-alone.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.application.underwriting.calculators import (
    CalculatorResult,
    ScenarioResult,
    TrendResult,
)
from creditops.domain.ids import CaseId, ConfirmedFactId, DocumentVersionId, TaskId

type UnderwritingAssessmentId = UUID

UNDERWRITING_AGENT_ROLE: Literal["CREDIT_UNDERWRITING"] = "CREDIT_UNDERWRITING"

#: Normalized field names that would express a credit decision.  Mirrors the
#: gateway's forbidden-key strip list; the domain schema must never contain one.
FORBIDDEN_DECISION_FIELD_NAMES = frozenset(
    {
        "approve",
        "approved",
        "approval",
        "creditdecision",
        "creditscore",
        "decision",
        "disbursement",
        "reject",
        "rejected",
        "rejection",
        "releasefunds",
        "score",
        "waive",
        "waiver",
        "legaldetermination",
    }
)


class ConfidenceLevel(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class GapBlockingLevel(StrEnum):
    """Blocking level of an evidence gap (CONTEXT.md glossary)."""

    BLOCKING = "BLOCKING"
    CONDITIONAL = "CONDITIONAL"
    CLARIFICATION = "CLARIFICATION"


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


EvidenceCitation = Annotated[
    ConfirmedFactCitation | CalculatorResultCitation | DocumentRegionCitation,
    Field(discriminator="kind"),
]


class Finding(BaseModel):
    """One material analytical statement, always evidence-cited.

    ``citations`` has ``min_length=1``: an unsupported conclusion cannot be
    represented.  Numbers inside ``statement_vi`` must originate from a cited
    calculator result — the maker interprets, it does not compute.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement_vi: str = Field(min_length=1, max_length=4000)
    citations: tuple[EvidenceCitation, ...] = Field(min_length=1)
    confidence: ConfidenceLevel
    uncertainty_vi: str = Field(default="", max_length=2000)


class AssessmentSection(BaseModel):
    """A narrative section built exclusively from cited findings."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    findings: tuple[Finding, ...] = Field(min_length=1)


class RepaymentSourceSection(BaseModel):
    """Primary repayment source plus explicitly computed downside scenarios."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    findings: tuple[Finding, ...] = Field(min_length=1)
    downside_scenarios: tuple[Finding, ...] = ()


class ProposedStructureSection(BaseModel):
    """Preliminary financing-structure proposal.  A draft, never a decision.

    Any proposed amount must cite the deterministic calculation it came from
    through ``findings`` citations; the section cannot state an approval,
    only describe a structure for human and checker review.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instrument_vi: str = Field(min_length=1, max_length=500)
    proposed_amount_vnd: Decimal | None = None
    tenor_months: int | None = Field(default=None, ge=1, le=600)
    findings: tuple[Finding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _amount_requires_calculator_citation(self) -> Self:
        if self.proposed_amount_vnd is None:
            return self
        has_calc = any(
            citation.kind == "CALCULATOR_RESULT"
            for finding in self.findings
            for citation in finding.citations
        )
        if not has_calc:
            raise ValueError(
                "a proposed amount must cite a deterministic calculator result"
            )
        return self


class RiskItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_id: str = Field(min_length=1, max_length=100)
    description_vi: str = Field(min_length=1, max_length=4000)
    citations: tuple[EvidenceCitation, ...] = Field(min_length=1)
    confidence: ConfidenceLevel
    uncertainty_vi: str = Field(default="", max_length=2000)


class MitigantItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    risk_id: str = Field(min_length=1, max_length=100)
    description_vi: str = Field(min_length=1, max_length=4000)
    citations: tuple[EvidenceCitation, ...] = Field(min_length=1)
    confidence: ConfidenceLevel
    uncertainty_vi: str = Field(default="", max_length=2000)


class AssumptionItem(BaseModel):
    """A declared assumption.  Never a Confirmed Fact and never promoted to one.

    ``basis_citations`` may point at the evidence that motivated the
    assumption, but the type itself marks the statement as unverified.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    statement_vi: str = Field(min_length=1, max_length=4000)
    rationale_vi: str = Field(min_length=1, max_length=4000)
    basis_citations: tuple[EvidenceCitation, ...] = ()


class EvidenceGapItem(BaseModel):
    """Missing evidence surfaced by the maker; persisted as a PROVISIONAL gap."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    missing_information_vi: str = Field(min_length=1, max_length=2000)
    why_needed_vi: str = Field(min_length=1, max_length=2000)
    blocking_level: GapBlockingLevel
    suggested_evidence_vi: tuple[str, ...] = ()


class AssessmentProvenance(BaseModel):
    """Immutable provenance envelope recorded on every maker output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: CaseId
    case_version: int = Field(ge=1)
    agent_role: Literal["CREDIT_UNDERWRITING"] = UNDERWRITING_AGENT_ROLE
    execution_id: UUID
    task_id: TaskId
    prompt_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    endpoint_id: str = Field(min_length=1)
    evidence_view_built_at: datetime
    created_at: datetime


class UnderwritingAssessment(BaseModel):
    """The Maker's complete evidence-grounded assessment for one case version.

    Append-only once persisted.  Contains analysis, risks, mitigants, declared
    assumptions, and evidence gaps — and no decision of any kind.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UnderwritingAssessmentId
    provenance: AssessmentProvenance
    business: AssessmentSection
    financial: AssessmentSection
    cash_flow: AssessmentSection
    repayment_source: RepaymentSourceSection
    proposed_structure: ProposedStructureSection
    risks: tuple[RiskItem, ...] = ()
    mitigants: tuple[MitigantItem, ...] = ()
    assumptions: tuple[AssumptionItem, ...] = ()
    evidence_gaps: tuple[EvidenceGapItem, ...] = ()
    calculator_results: tuple[CalculatorResult, ...] = ()
    trend_results: tuple[TrendResult, ...] = ()
    scenario_results: tuple[ScenarioResult, ...] = ()

    def _iter_findings(self) -> tuple[tuple[str, Finding], ...]:
        located: list[tuple[str, Finding]] = []
        for section_name in ("business", "financial", "cash_flow"):
            section: AssessmentSection = getattr(self, section_name)
            located.extend((section_name, finding) for finding in section.findings)
        located.extend(
            ("repayment_source", finding)
            for finding in self.repayment_source.findings
        )
        located.extend(
            ("repayment_source.downside_scenarios", finding)
            for finding in self.repayment_source.downside_scenarios
        )
        located.extend(
            ("proposed_structure", finding)
            for finding in self.proposed_structure.findings
        )
        return tuple(located)

    @model_validator(mode="after")
    def _citations_resolve_and_mitigants_bind(self) -> Self:
        known_result_ids: set[str] = set()
        known_result_ids.update(item.result_id for item in self.calculator_results)
        known_result_ids.update(item.result_id for item in self.trend_results)
        known_result_ids.update(item.result_id for item in self.scenario_results)
        all_citations: list[tuple[str, EvidenceCitation]] = []
        for location, finding in self._iter_findings():
            all_citations.extend(
                (location, citation) for citation in finding.citations
            )
        for risk in self.risks:
            all_citations.extend(
                (f"risk:{risk.risk_id}", citation) for citation in risk.citations
            )
        for mitigant in self.mitigants:
            all_citations.extend(
                (f"mitigant:{mitigant.risk_id}", citation)
                for citation in mitigant.citations
            )
        for location, citation in all_citations:
            if (
                citation.kind == "CALCULATOR_RESULT"
                and citation.result_id not in known_result_ids
            ):
                raise ValueError(
                    "citation in "
                    f"{location} references unknown calculator result "
                    f"{citation.result_id}"
                )
        risk_ids = {risk.risk_id for risk in self.risks}
        if len(risk_ids) != len(self.risks):
            raise ValueError("risk_id values must be unique")
        for mitigant in self.mitigants:
            if mitigant.risk_id not in risk_ids:
                raise ValueError(
                    f"mitigant references unknown risk {mitigant.risk_id}"
                )
        return self


def _assert_no_forbidden_fields(model: type[BaseModel], seen: set[str]) -> None:
    name = model.__name__
    if name in seen:
        return
    seen.add(name)
    for field_name, field_info in model.model_fields.items():
        normalized = "".join(char for char in field_name.casefold() if char.isalnum())
        if normalized in FORBIDDEN_DECISION_FIELD_NAMES:
            raise AssertionError(
                f"{name}.{field_name} would express a credit decision"
            )
        annotation = field_info.annotation
        stack = [annotation]
        while stack:
            candidate = stack.pop()
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                _assert_no_forbidden_fields(candidate, seen)
            else:
                stack.extend(getattr(candidate, "__args__", ()))


# Import-time structural guard: the maker output schema can never grow a
# decision-capable field without failing every test run and worker start.
_assert_no_forbidden_fields(UnderwritingAssessment, set())
