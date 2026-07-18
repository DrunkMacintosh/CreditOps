"""Schema tests for the maker's UnderwritingAssessment contract.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture case
belongs to the invented SME "Cong ty TNHH Ca Phe Chon Gia Demo".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from creditops.application.underwriting.calculators import (
    CalculatorInput,
    FactRef,
    current_ratio,
)
from creditops.domain.underwriting import (
    FORBIDDEN_DECISION_FIELD_NAMES,
    AssessmentProvenance,
    AssessmentSection,
    CalculatorResultCitation,
    ConfidenceLevel,
    ConfirmedFactCitation,
    EvidenceGapItem,
    Finding,
    GapBlockingLevel,
    MitigantItem,
    ProposedStructureSection,
    RepaymentSourceSection,
    RiskItem,
    UnderwritingAssessment,
)

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
FACT_ID = uuid4()

CALC_RESULT = current_ratio(
    CalculatorInput(
        name="current_assets",
        value=Decimal("1500"),
        fact_refs=(FactRef(kind="CONFIRMED_FACT", ref_id=str(FACT_ID)),),
    ),
    CalculatorInput(
        name="current_liabilities",
        value=Decimal("1000"),
        fact_refs=(FactRef(kind="CONFIRMED_FACT", ref_id=str(FACT_ID)),),
    ),
)


def fact_citation() -> ConfirmedFactCitation:
    return ConfirmedFactCitation(confirmed_fact_id=FACT_ID)


def calc_citation() -> CalculatorResultCitation:
    return CalculatorResultCitation(result_id=CALC_RESULT.result_id)


def finding(text: str = "Doanh nghiep hoat dong on dinh.") -> Finding:
    return Finding(
        statement_vi=text,
        citations=(fact_citation(),),
        confidence=ConfidenceLevel.MEDIUM,
    )


def provenance() -> AssessmentProvenance:
    return AssessmentProvenance(
        case_id=uuid4(),
        case_version=1,
        execution_id=uuid4(),
        task_id=uuid4(),
        prompt_version="underwriting-v1",
        model_id="synthetic-model",
        endpoint_id="synthetic-endpoint",
        evidence_view_built_at=NOW,
        created_at=NOW,
    )


def assessment(**overrides: Any) -> UnderwritingAssessment:
    base: dict[str, Any] = {
        "id": uuid4(),
        "provenance": provenance(),
        "business": AssessmentSection(findings=(finding(),)),
        "financial": AssessmentSection(
            findings=(
                Finding(
                    statement_vi="He so thanh toan hien hanh la 1.5.",
                    citations=(calc_citation(),),
                    confidence=ConfidenceLevel.HIGH,
                ),
            )
        ),
        "cash_flow": AssessmentSection(findings=(finding(),)),
        "repayment_source": RepaymentSourceSection(findings=(finding(),)),
        "proposed_structure": ProposedStructureSection(
            instrument_vi="Han muc von luu dong ngan han (de xuat so bo)",
            findings=(finding(),),
        ),
        "calculator_results": (CALC_RESULT,),
    }
    base.update(overrides)
    return UnderwritingAssessment(**base)


class TestCitationsRequired:
    def test_valid_assessment_builds(self) -> None:
        built = assessment()
        assert built.provenance.agent_role == "CREDIT_UNDERWRITING"

    def test_finding_with_empty_citations_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Finding(
                statement_vi="Ket luan khong co can cu.",
                citations=(),
                confidence=ConfidenceLevel.LOW,
            )

    def test_risk_and_mitigant_require_citations(self) -> None:
        with pytest.raises(ValidationError):
            RiskItem(
                risk_id="r1",
                description_vi="Rui ro khong can cu.",
                citations=(),
                confidence=ConfidenceLevel.LOW,
            )
        with pytest.raises(ValidationError):
            MitigantItem(
                risk_id="r1",
                description_vi="Bien phap khong can cu.",
                citations=(),
                confidence=ConfidenceLevel.LOW,
            )

    def test_unknown_calculator_result_citation_is_rejected(self) -> None:
        bad = Finding(
            statement_vi="So lieu khong ro nguon.",
            citations=(CalculatorResultCitation(result_id="calc_unknown"),),
            confidence=ConfidenceLevel.HIGH,
        )
        with pytest.raises(ValidationError, match="unknown calculator result"):
            assessment(financial=AssessmentSection(findings=(bad,)))

    def test_mitigant_must_reference_existing_risk(self) -> None:
        with pytest.raises(ValidationError, match="unknown risk"):
            assessment(
                mitigants=(
                    MitigantItem(
                        risk_id="khong-ton-tai",
                        description_vi="Bien phap giam thieu.",
                        citations=(fact_citation(),),
                        confidence=ConfidenceLevel.MEDIUM,
                    ),
                )
            )


class TestNoDecisionFields:
    def test_extra_decision_field_is_rejected(self) -> None:
        for forbidden in ("decision", "approved", "credit_score", "waiver"):
            with pytest.raises(ValidationError):
                UnderwritingAssessment.model_validate(
                    {**assessment().model_dump(), forbidden: "APPROVE"}
                )

    def test_schema_has_no_forbidden_field_names(self) -> None:
        schema = json.dumps(UnderwritingAssessment.model_json_schema())
        properties: set[str] = set()

        def collect(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "properties" and isinstance(value, dict):
                        properties.update(value.keys())
                    collect(value)
            elif isinstance(node, list):
                for item in node:
                    collect(item)

        collect(json.loads(schema))
        normalized = {
            "".join(char for char in name.casefold() if char.isalnum())
            for name in properties
        }
        assert not normalized & FORBIDDEN_DECISION_FIELD_NAMES

    def test_proposed_amount_requires_calculator_citation(self) -> None:
        with pytest.raises(ValidationError, match="deterministic calculator"):
            ProposedStructureSection(
                instrument_vi="Han muc von luu dong",
                proposed_amount_vnd=Decimal("300000000"),
                findings=(finding(),),
            )
        section = ProposedStructureSection(
            instrument_vi="Han muc von luu dong",
            proposed_amount_vnd=Decimal("300000000"),
            findings=(
                Finding(
                    statement_vi="Muc de xuat theo ket qua tinh toan.",
                    citations=(calc_citation(),),
                    confidence=ConfidenceLevel.MEDIUM,
                ),
            ),
        )
        assert section.proposed_amount_vnd == Decimal("300000000")


class TestImmutabilityAndGaps:
    def test_assessment_is_frozen(self) -> None:
        built = assessment()
        with pytest.raises(ValidationError):
            built.id = uuid4()  # type: ignore[misc]

    def test_evidence_gap_item_carries_blocking_level(self) -> None:
        gap = EvidenceGapItem(
            missing_information_vi="Thieu bao cao tai chinh nam 2025.",
            why_needed_vi="Can de phan tich xu huong doanh thu.",
            blocking_level=GapBlockingLevel.BLOCKING,
        )
        built = assessment(evidence_gaps=(gap,))
        assert built.evidence_gaps[0].blocking_level is GapBlockingLevel.BLOCKING
