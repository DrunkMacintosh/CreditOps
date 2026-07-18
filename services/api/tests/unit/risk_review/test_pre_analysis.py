"""Pass A (blind pre-analysis) tests for the two-pass Independent Risk Review.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture case
belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".

The shared fixtures/fakes (repository, gateway, checkpoint recorder, processor
builder, and the dispatching payload factory that serves BOTH passes) are
reused from ``test_checker_processor`` so both suites exercise the same seams.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import pytest
from test_checker_processor import (  # sibling fixtures (prepend import mode)
    LIVE_FACT_ID,
    CheckpointRecorder,
    FakeGateway,
    FakeRiskReviewRepository,
    _is_pass_a,
    blind_payload,
    build_legal,
    build_processor,
    build_underwriting,
    checker_task,
    checker_view,
    dispatched,
    valid_payload,
)

from creditops.application.ports.model_gateway import InferenceResult, ReasonRequest
from creditops.application.ports.risk_review import (
    PreAnalysisEvidenceView,
    PreAnalysisRecord,
)
from creditops.application.risk_review.checker import (
    PRE_ANALYSIS_PROMPT_VERSION,
    PRE_ANALYSIS_SCHEMA_VERSION,
    BuildPreAnalysis,
    PreAnalysisOutputInvalid,
    RiskPreAnalysis,
    build_pre_analysis_response_schema,
)
from creditops.application.risk_review.evidence import build_target_universe
from creditops.application.risk_review.processor import (
    CHECKPOINT_PRE_ANALYSIS_PERSISTED,
)
from creditops.application.use_cases.run_worker_once import StageResult, WorkerOutcome
from creditops.domain.risk_review import RaisedBy


def _repo() -> FakeRiskReviewRepository:
    underwriting = build_underwriting()
    legal = build_legal()
    return FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=underwriting,
        underwriting_execution_id=underwriting.provenance.execution_id,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )


def _pass_a_requests(gateway: FakeGateway) -> list[ReasonRequest]:
    return [request for request in gateway.requests if _is_pass_a(request)]


def _pass_b_requests(gateway: FakeGateway) -> list[ReasonRequest]:
    return [request for request in gateway.requests if not _is_pass_a(request)]


def test_blind_view_type_carries_no_maker_attribute() -> None:
    # The blind view the checker reasons over in Pass A is a distinct type with
    # no field capable of holding a maker/legal conclusion.
    field_names = {field.name for field in dataclasses.fields(PreAnalysisEvidenceView)}
    assert field_names == {"case_id", "case_version", "built_at", "confirmed_facts"}
    for forbidden in ("underwriting", "legal", "maker", "assessment"):
        assert not any(forbidden in name.lower() for name in field_names)


@pytest.mark.asyncio
async def test_pass_a_uses_blind_loader_and_excludes_maker_content() -> None:
    repository = _repo()
    underwriting, legal = repository.underwriting, repository.legal
    assert underwriting is not None and legal is not None
    gateway = FakeGateway(
        dispatched(valid_payload(underwriting, legal)), validate_against_schema=True
    )
    processor = build_processor(repository, gateway)

    await processor.process(checker_task(), None, CheckpointRecorder().save)

    # Pass A read the BLIND loader exactly once (not the full checker view).
    assert repository.blind_view_loads == 1
    (pass_a_request,) = _pass_a_requests(gateway)
    # The blind pass literally never sees a maker conclusion.
    assert "underwritingAssessment" not in pass_a_request.content
    assert "legalAssessment" not in pass_a_request.content
    assert "blindPreAnalysis" not in pass_a_request.content
    assert "confirmedFacts" in pass_a_request.content
    # Its response schema admits only independent risks/observations.
    assert set(pass_a_request.response_schema["properties"]) == {
        "independent_risks",
        "observations",
    }


@pytest.mark.asyncio
async def test_pass_a_persists_blind_pre_analysis_and_checkpoint() -> None:
    repository = _repo()
    underwriting, legal = repository.underwriting, repository.legal
    assert underwriting is not None and legal is not None
    gateway = FakeGateway(dispatched(valid_payload(underwriting, legal)))
    processor = build_processor(repository, gateway)
    recorder = CheckpointRecorder()

    await processor.process(checker_task(), None, recorder.save)

    assert repository.pre_analysis_persist_calls == 1
    (record,) = repository.pre_analyses.values()
    assert record.prompt_version == PRE_ANALYSIS_PROMPT_VERSION
    assert record.schema_version == PRE_ANALYSIS_SCHEMA_VERSION
    assert record.analysis["independent_risks"]  # blind pass surfaced a risk
    assert CHECKPOINT_PRE_ANALYSIS_PERSISTED in recorder.types()
    # The blind checkpoint precedes the Pass B inference checkpoint.
    types = recorder.types()
    assert types.index(CHECKPOINT_PRE_ANALYSIS_PERSISTED) < types.index("INFERENCE_VALIDATED")
    assert any(
        event.event_type == "RISK_REVIEW_PRE_ANALYSIS_PERSISTED"
        for event in repository.audit_events
    )


@pytest.mark.asyncio
async def test_resume_from_pre_analysis_checkpoint_skips_pass_a_inference() -> None:
    repository = _repo()
    underwriting, legal = repository.underwriting, repository.legal
    assert underwriting is not None and legal is not None
    gateway = FakeGateway(dispatched(valid_payload(underwriting, legal)))
    processor = build_processor(repository, gateway)
    recorder = CheckpointRecorder()
    await processor.process(checker_task(), None, recorder.save)
    pre_analysis_checkpoint = next(
        c for c in recorder.saved if c.checkpoint_type == CHECKPOINT_PRE_ANALYSIS_PERSISTED
    )

    resumed_repository = _repo_like(repository)

    class PassAForbiddenGateway(FakeGateway):
        async def reason(self, request: ReasonRequest) -> InferenceResult:
            if _is_pass_a(request):
                raise AssertionError("resume must not re-run the blind Pass A model")
            return await super().reason(request)

    resumed_gateway = PassAForbiddenGateway(dispatched(valid_payload(underwriting, legal)))
    resumed = build_processor(resumed_repository, resumed_gateway)
    resume_recorder = CheckpointRecorder()

    result = await resumed.process(
        checker_task(), pre_analysis_checkpoint, resume_recorder.save
    )

    assert result == StageResult()
    # Pass A never called the model on resume; Pass B did, exactly once.
    assert _pass_a_requests(resumed_gateway) == []
    assert len(_pass_b_requests(resumed_gateway)) == 1
    # And Pass A was not persisted again on the fresh repository.
    assert resumed_repository.pre_analysis_persist_calls == 0
    assert len(resumed_repository.assessments) == 1


@pytest.mark.asyncio
async def test_pass_b_receives_blind_pre_analysis_and_marks_raised_blind() -> None:
    repository = _repo()
    underwriting, legal = repository.underwriting, repository.legal
    assert underwriting is not None and legal is not None
    gateway = FakeGateway(
        dispatched(_pass_b_with_confirmed_fact_challenge(underwriting, legal)),
        validate_against_schema=True,
    )
    processor = build_processor(repository, gateway)

    await processor.process(checker_task(), None, CheckpointRecorder().save)

    # Pass B's untrusted context carried the checker's own blind pre-analysis.
    (pass_b_request,) = _pass_b_requests(gateway)
    assert "blindPreAnalysis" in pass_b_request.content
    assert "independent_risks" in pass_b_request.content

    (assessment,) = repository.assessments.values()
    llm_challenges = [c for c in assessment.challenges if c.raised_by is RaisedBy.LLM]
    # The challenge sharing a Confirmed Fact with the blind pass is flagged
    # raised_blind; the one citing only a maker passage is not.
    raised_blind_flags = {c.raised_blind for c in llm_challenges}
    assert raised_blind_flags == {True, False}
    # Every deterministic challenge stays not-blind (it did not originate in
    # the blind pass).
    for challenge in assessment.challenges:
        if challenge.raised_by is RaisedBy.DETERMINISTIC:
            assert challenge.raised_blind is False


def test_pass_a_schema_and_validator_reject_maker_citations() -> None:
    # The closed Pass A schema has no maker-artifact citation branch at all ...
    schema = build_pre_analysis_response_schema(fact_ids=(str(LIVE_FACT_ID),))
    citation_schema = schema["properties"]["independent_risks"]["items"]["properties"][
        "citations"
    ]["items"]
    assert citation_schema["properties"]["kind"]["const"] == "CONFIRMED_FACT"

    # ... and the builder rejects any maker-artifact citation kind outright.
    blind_view = PreAnalysisEvidenceView(
        case_id=checker_view().case_id,
        case_version=1,
        built_at=checker_view().built_at,
        confirmed_facts=checker_view().confirmed_facts,
    )
    maker_cited_payload: Mapping[str, Any] = {
        "independent_risks": [
            {
                "description_vi": "Tham chieu trai phep den ket luan cua MAKER.",
                "citations": [
                    {
                        "kind": "MAKER_FINDING",
                        "ref": {
                            "maker_source": "CREDIT_UNDERWRITING",
                            "maker_assessment_id": str(uuid4()),
                            "section_path": "risks[0]",
                        },
                    }
                ],
                "severity": "HIGH",
                "confidence": "HIGH",
            }
        ],
        "observations": [],
    }
    with pytest.raises(PreAnalysisOutputInvalid, match="maker-artifact"):
        BuildPreAnalysis().build(payload=maker_cited_payload, view=blind_view)


@pytest.mark.asyncio
async def test_inference_none_fails_closed_with_no_pre_analysis_persistence() -> None:
    repository = _repo()
    processor = build_processor(repository, None)

    result = await processor.process(checker_task(), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.FAILED_MANUAL_REVIEW
    assert repository.pre_analyses == {}
    assert repository.assessments == {}
    assert any(
        event.event_type == "RISK_REVIEW_GATEWAY_UNAVAILABLE"
        for event in repository.audit_events
    )


@pytest.mark.asyncio
async def test_redelivery_after_pass_a_crash_resumes_without_duplicate_row() -> None:
    # Simulate the durable blind pre-analysis row surviving a crash that
    # happened BEFORE its checkpoint was written: the redelivery must resume
    # into Pass B off the durable row, never re-running or duplicating Pass A.
    repository = _repo()
    underwriting, legal = repository.underwriting, repository.legal
    assert underwriting is not None and legal is not None
    task = checker_task()
    blind = RiskPreAnalysis.model_validate(blind_payload())
    repository.pre_analyses[(task.case_id, task.case_version, task.id)] = PreAnalysisRecord(
        id=uuid4(),
        case_id=task.case_id,
        case_version=task.case_version,
        task_id=task.id,
        execution_id=uuid4(),
        prompt_version=PRE_ANALYSIS_PROMPT_VERSION,
        schema_version=PRE_ANALYSIS_SCHEMA_VERSION,
        analysis=blind.model_dump(mode="json"),
    )

    class PassAForbiddenGateway(FakeGateway):
        async def reason(self, request: ReasonRequest) -> InferenceResult:
            if _is_pass_a(request):
                raise AssertionError("a durable blind pre-analysis must not be re-run")
            return await super().reason(request)

    gateway = PassAForbiddenGateway(dispatched(valid_payload(underwriting, legal)))
    processor = build_processor(repository, gateway)

    result = await processor.process(task, None, CheckpointRecorder().save)

    assert result == StageResult()
    assert _pass_a_requests(gateway) == []
    # No second Pass A persist, exactly one blind row remains.
    assert repository.pre_analysis_persist_calls == 0
    assert len(repository.pre_analyses) == 1
    assert len(repository.assessments) == 1


def _repo_like(source: FakeRiskReviewRepository) -> FakeRiskReviewRepository:
    return FakeRiskReviewRepository(
        view=source.view,
        underwriting=source.underwriting,
        underwriting_execution_id=source.underwriting_execution_id,
        legal=source.legal,
        legal_execution_id=source.legal_execution_id,
    )


def _pass_b_with_confirmed_fact_challenge(
    underwriting: Any, legal: Any
) -> dict[str, Any]:
    universe = build_target_universe(underwriting, legal)
    assert "risks[0]" in universe.underwriting_paths
    target = {
        "maker_source": "CREDIT_UNDERWRITING",
        "maker_assessment_id": str(underwriting.id),
        "section_path": "risks[0]",
    }
    return {
        "challenges": [
            {
                # Cites the same Confirmed Fact the blind pass cited -> blind.
                "target": target,
                "challenge_type": "OMITTED_RISK",
                "statement_vi": "Rui ro tap trung, da nhan dien doc lap tu bang chung.",
                "citations": [
                    {"kind": "CONFIRMED_FACT", "confirmed_fact_id": str(LIVE_FACT_ID)}
                ],
                "severity": "MEDIUM",
                "confidence": "MEDIUM",
            },
            {
                # Cites only a maker passage -> reactive, not blind.
                "target": target,
                "challenge_type": "UNSUPPORTED_ASSUMPTION",
                "statement_vi": "Gia dinh cua MAKER chua duoc dinh luong.",
                "citations": [{"kind": "MAKER_FINDING", "ref": target}],
                "severity": "LOW",
                "confidence": "LOW",
            },
        ],
        "omitted_risks": [],
        "mitigant_adequacy_reviews": [],
        "recommendations": [],
        "evidence_gaps": [],
    }
