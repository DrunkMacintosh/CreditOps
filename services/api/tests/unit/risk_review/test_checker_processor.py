"""Checker use-case and worker-processor tests for INDEPENDENT_RISK_REVIEW.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture case
belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator

from creditops.application.orchestration.processors import (
    ManualReviewProcessor,
    ProcessorRegistry,
)
from creditops.application.ports.model_gateway import (
    InferenceResult,
    InferenceUnavailableError,
    ReasonRequest,
)
from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.application.ports.queue import (
    QueueMessage,
    RetryDecision,
    TaskCheckpoint,
    TaskRecord,
)
from creditops.application.ports.risk_review import (
    CheckerEvidenceView,
    EvidenceFact,
    MakerOutputsView,
    OpenGapRecord,
    PersistedCheckerOutput,
    ProvisionalGapRecord,
)
from creditops.application.risk_review.checker import (
    OPERATIONS_HANDOFF_STATE,
    CheckerPrompt,
    RunCheckerInference,
    build_response_schema,
)
from creditops.application.risk_review.evidence import build_target_universe
from creditops.application.risk_review.processor import (
    CHECKPOINT_EVIDENCE_VIEW,
    CHECKPOINT_INFERENCE,
    CHECKPOINT_MAKER_OUTPUTS,
    CHECKPOINT_PERSISTED,
    CHECKPOINT_PRE_ANALYSIS,
    IndependentRiskReviewProcessor,
)
from creditops.application.use_cases.run_worker_once import (
    RunWorkerOnce,
    StageResult,
    WorkerOutcome,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.legal import AssessmentSection as LegalAssessmentSection
from creditops.domain.legal import (
    CollateralReviewSection,
    ConfidenceLevel,
    LegalAssessmentProvenance,
    LegalComplianceAssessment,
    OwnershipConsistencySection,
)
from creditops.domain.legal import ConfirmedFactCitation as LegalConfirmedFactCitation
from creditops.domain.legal import Finding as LegalFinding
from creditops.domain.orchestration import TaskType
from creditops.domain.risk_review import RiskReviewAssessment
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.domain.underwriting import (
    AssessmentProvenance,
    AssessmentSection,
    ConfirmedFactCitation,
    ProposedStructureSection,
    RepaymentSourceSection,
    RiskItem,
    UnderwritingAssessment,
)

NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
CASE_ID = UUID("10000000-0000-0000-0000-00000000bb01")
TASK_ID = UUID("30000000-0000-0000-0000-00000000bb01")
LIVE_FACT_ID = UUID("40000000-0000-0000-0000-00000000bb01")


def _uw_provenance(execution_id: UUID | None = None) -> AssessmentProvenance:
    return AssessmentProvenance(
        case_id=CASE_ID,
        case_version=1,
        execution_id=execution_id or uuid4(),
        task_id=uuid4(),
        prompt_version="underwriting-prompt-v1",
        model_id="synthetic-model",
        endpoint_id="synthetic-endpoint",
        evidence_view_built_at=NOW,
        created_at=NOW,
    )


def _uw_finding() -> Any:
    from creditops.domain.underwriting import Finding

    return Finding(
        statement_vi="Phat hien mo phong.",
        citations=(ConfirmedFactCitation(confirmed_fact_id=LIVE_FACT_ID),),
        confidence=ConfidenceLevel.MEDIUM,
    )


def build_underwriting(execution_id: UUID | None = None) -> UnderwritingAssessment:
    return UnderwritingAssessment(
        id=uuid4(),
        provenance=_uw_provenance(execution_id),
        business=AssessmentSection(findings=(_uw_finding(),)),
        financial=AssessmentSection(findings=(_uw_finding(),)),
        cash_flow=AssessmentSection(findings=(_uw_finding(),)),
        repayment_source=RepaymentSourceSection(findings=(_uw_finding(),)),
        proposed_structure=ProposedStructureSection(
            instrument_vi="Han muc von luu dong (de xuat so bo)",
            findings=(_uw_finding(),),
        ),
        risks=(
            RiskItem(
                risk_id="rui-ro-tap-trung",
                description_vi="Phu thuoc mot nhom khach hang lon.",
                citations=(ConfirmedFactCitation(confirmed_fact_id=LIVE_FACT_ID),),
                confidence=ConfidenceLevel.MEDIUM,
            ),
        ),
    )


def _legal_provenance(execution_id: UUID | None = None) -> LegalAssessmentProvenance:
    return LegalAssessmentProvenance(
        case_id=CASE_ID,
        case_version=1,
        execution_id=execution_id or uuid4(),
        task_id=uuid4(),
        prompt_version="legal-prompt-v1",
        model_id="synthetic-model",
        endpoint_id="synthetic-endpoint",
        evidence_view_built_at=NOW,
        created_at=NOW,
    )


def _legal_finding() -> LegalFinding:
    return LegalFinding(
        statement_vi="Phat hien phap ly mo phong.",
        citations=(LegalConfirmedFactCitation(confirmed_fact_id=LIVE_FACT_ID),),
        confidence=ConfidenceLevel.MEDIUM,
    )


def build_legal(execution_id: UUID | None = None) -> LegalComplianceAssessment:
    return LegalComplianceAssessment(
        id=uuid4(),
        provenance=_legal_provenance(execution_id),
        legal_entity_review=LegalAssessmentSection(findings=(_legal_finding(),)),
        authority_signatory_review=LegalAssessmentSection(findings=(_legal_finding(),)),
        ownership_consistency=OwnershipConsistencySection(findings=(_legal_finding(),)),
        collateral_review=CollateralReviewSection(ownership_evidence_findings=(_legal_finding(),)),
    )


def checker_view(case_version: int = 1) -> CheckerEvidenceView:
    return CheckerEvidenceView(
        case_id=CASE_ID,
        case_version=case_version,
        built_at=NOW,
        confirmed_facts=(
            EvidenceFact(
                confirmed_fact_id=LIVE_FACT_ID,
                field_key="financials.revenue",
                value="1000",
                document_version_id=uuid4(),
            ),
        ),
    )


def checker_task(case_version: int = 1) -> TaskRecord:
    return TaskRecord(
        id=TASK_ID,
        case_id=CASE_ID,
        case_version=case_version,
        document_version_id=None,
        status=TaskStatus.RUNNING,
        attempt_count=1,
        max_attempts=3,
        available_at=NOW,
        lease_token=uuid4(),
        lease_until=NOW,
        input_schema_version="1",
        input_payload={},
        idempotency_key=f"IRR:{CASE_ID}:1",
        task_type=TaskType.INDEPENDENT_RISK_REVIEW,
    )


def valid_payload(
    underwriting: UnderwritingAssessment, legal: LegalComplianceAssessment
) -> dict[str, Any]:
    universe = build_target_universe(underwriting, legal)
    target = {
        "maker_source": "CREDIT_UNDERWRITING",
        "maker_assessment_id": str(underwriting.id),
        "section_path": "risks[0]",
    }
    assert "risks[0]" in universe.underwriting_paths
    return {
        "challenges": [
            {
                "target": target,
                "challenge_type": "UNSUPPORTED_ASSUMPTION",
                "statement_vi": "Rui ro tap trung chua duoc dinh luong ro.",
                "citations": [{"kind": "MAKER_FINDING", "ref": target}],
                "severity": "MEDIUM",
                "confidence": "MEDIUM",
            }
        ],
        "omitted_risks": [
            {
                "description_vi": "Rui ro thoi tiet doi voi nong san chua duoc de cap.",
                "citations": [
                    {"kind": "CONFIRMED_FACT", "confirmed_fact_id": str(LIVE_FACT_ID)}
                ],
                "confidence": "LOW",
            }
        ],
        "mitigant_adequacy_reviews": [],
        "recommendations": [
            {
                "recommendation_type": "REQUEST_INFORMATION",
                "rationale_vi": "Can bo sung du kien ve rui ro thoi tiet.",
            }
        ],
        "evidence_gaps": [],
    }


class FakeGateway:
    def __init__(
        self,
        payload_factory: Callable[[ReasonRequest], Mapping[str, Any]],
        *,
        validate_against_schema: bool = False,
    ) -> None:
        self.requests: list[ReasonRequest] = []
        self._factory = payload_factory
        self._validate = validate_against_schema

    async def reason(self, request: ReasonRequest) -> InferenceResult:
        self.requests.append(request)
        payload = self._factory(request)
        if self._validate:
            Draft202012Validator(dict(request.response_schema)).validate(payload)
        return InferenceResult(
            capability="reasoning",
            provider="FPT",
            case_id=request.case_id,
            endpoint_id="fake-endpoint",
            model_id="fake-model",
            payload=payload,
            prompt_version="risk-review-prompt-v1",
            schema_version="risk-review-assessment-v1",
            route_version="fpt-route-v1",
            correlation_id=request.correlation_id,
            started_at=NOW,
            latency_ms=1,
        )

    async def extract_kie(self, request: object) -> InferenceResult:
        raise AssertionError("checker must not call extraction")

    async def extract_table(self, request: object) -> InferenceResult:
        raise AssertionError("checker must not call extraction")

    async def inspect_vision(self, request: object) -> InferenceResult:
        raise AssertionError("checker must not call vision")

    async def embed(self, request: object) -> InferenceResult:
        raise AssertionError("checker must not call embedding")


class UnavailableGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__(lambda request: {})

    async def reason(self, request: ReasonRequest) -> InferenceResult:
        raise InferenceUnavailableError("endpoint disabled")


@dataclass
class StoredHandoff:
    handoff_id: UUID
    case_id: UUID
    case_version: int
    state: str
    source_task_id: UUID


class FakeRiskReviewRepository:
    def __init__(
        self,
        *,
        view: CheckerEvidenceView | None,
        underwriting: UnderwritingAssessment | None,
        underwriting_execution_id: UUID | None,
        legal: LegalComplianceAssessment | None,
        legal_execution_id: UUID | None,
        open_gaps: tuple[OpenGapRecord, ...] = (),
    ) -> None:
        self.view = view
        self.underwriting = underwriting
        self.underwriting_execution_id = underwriting_execution_id
        self.legal = legal
        self.legal_execution_id = legal_execution_id
        self.open_gaps = open_gaps
        self.persisted: dict[tuple[UUID, int, UUID], PersistedCheckerOutput] = {}
        self.assessments: dict[UUID, RiskReviewAssessment] = {}
        self.handoffs: dict[UUID, StoredHandoff] = {}
        self.audit_events: list[OrchestrationAuditEvent] = []
        self.gap_rows: list[ProvisionalGapRecord] = []
        self.persist_calls = 0

    async def load_evidence_view(self, case_id: UUID) -> CheckerEvidenceView | None:
        if self.view is not None and self.view.case_id == case_id:
            return self.view
        return None

    async def load_maker_outputs(self, case_id: UUID, case_version: int) -> MakerOutputsView:
        del case_id, case_version
        return MakerOutputsView(
            underwriting=self.underwriting,
            underwriting_execution_id=self.underwriting_execution_id,
            underwriting_handoff_id=uuid4() if self.underwriting is not None else None,
            legal=self.legal,
            legal_execution_id=self.legal_execution_id,
            legal_handoff_id=uuid4() if self.legal is not None else None,
        )

    async def load_open_gaps(self, case_id: UUID, case_version: int) -> tuple[OpenGapRecord, ...]:
        del case_id, case_version
        return self.open_gaps

    async def load_latest_assessment(self, case_id: UUID) -> Any:
        del case_id
        return None

    async def load_dispositions(self, case_id: UUID, case_version: int) -> tuple[Any, ...]:
        del case_id, case_version
        return ()

    async def find_persisted(
        self, *, case_id: UUID, case_version: int, task_id: UUID
    ) -> PersistedCheckerOutput | None:
        found = self.persisted.get((case_id, case_version, task_id))
        if found is None:
            return None
        return replace(found, created=False)

    async def persist_assessment(
        self,
        *,
        assessment: RiskReviewAssessment,
        handoff_id: UUID,
        handoff_state: str,
        gaps: tuple[ProvisionalGapRecord, ...],
    ) -> PersistedCheckerOutput:
        self.persist_calls += 1
        key = (
            assessment.provenance.case_id,
            assessment.provenance.case_version,
            assessment.provenance.task_id,
        )
        existing = self.persisted.get(key)
        if existing is not None:
            return replace(existing, created=False)
        self.gap_rows.extend(gaps)
        self.handoffs[handoff_id] = StoredHandoff(
            handoff_id=handoff_id,
            case_id=assessment.provenance.case_id,
            case_version=assessment.provenance.case_version,
            state=handoff_state,
            source_task_id=assessment.provenance.task_id,
        )
        self.assessments[assessment.id] = assessment
        output = PersistedCheckerOutput(
            assessment_id=assessment.id,
            handoff_id=handoff_id,
            challenge_ids=tuple(uuid4() for _ in assessment.challenges),
            handoff_state=handoff_state,
            created=True,
        )
        self.persisted[key] = output
        return output

    async def record_disposition(self, **kwargs: Any) -> Any:
        raise AssertionError("the checker processor must never write a disposition")

    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        self.audit_events.append(event)


@dataclass
class CheckpointRecorder:
    saved: list[TaskCheckpoint] = field(default_factory=list)

    async def save(self, checkpoint_type: str, data: Mapping[str, object]) -> TaskCheckpoint:
        checkpoint = TaskCheckpoint(
            task_id=TASK_ID,
            case_id=CASE_ID,
            case_version=1,
            document_version_id=None,
            sequence_no=len(self.saved) + 1,
            checkpoint_type=checkpoint_type,
            checkpoint_schema_version="1",
            checkpoint_data=dict(data),
            created_at=NOW,
        )
        self.saved.append(checkpoint)
        return checkpoint

    def types(self) -> list[str]:
        return [checkpoint.checkpoint_type for checkpoint in self.saved]


def build_processor(
    repository: FakeRiskReviewRepository,
    gateway: FakeGateway | None,
    *,
    execution_id_factory: Callable[[], UUID] | None = None,
) -> IndependentRiskReviewProcessor:
    inference = (
        RunCheckerInference(gateway, prompt=CheckerPrompt("prompt-vi-test"))
        if gateway is not None
        else None
    )
    return IndependentRiskReviewProcessor(
        repository, inference, clock=lambda: NOW, execution_id_factory=execution_id_factory
    )


@pytest.mark.asyncio
async def test_happy_path_persists_challenges_and_operations_handoff() -> None:
    underwriting = build_underwriting()
    legal = build_legal()
    repository = FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=underwriting,
        underwriting_execution_id=underwriting.provenance.execution_id,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )
    gateway = FakeGateway(
        lambda request: valid_payload(underwriting, legal), validate_against_schema=True
    )
    processor = build_processor(repository, gateway)
    recorder = CheckpointRecorder()

    result = await processor.process(checker_task(), None, recorder.save)

    assert result == StageResult()
    assert recorder.types() == [
        CHECKPOINT_EVIDENCE_VIEW,
        CHECKPOINT_MAKER_OUTPUTS,
        CHECKPOINT_PRE_ANALYSIS,
        CHECKPOINT_INFERENCE,
        CHECKPOINT_PERSISTED,
    ]
    (handoff,) = repository.handoffs.values()
    assert handoff.state == OPERATIONS_HANDOFF_STATE == "READY_FOR_OPERATIONS"
    (assessment,) = repository.assessments.values()
    # (b) every challenge (LLM or deterministic) carries >=1 evidence citation.
    assert assessment.challenges
    for challenge in assessment.challenges:
        assert challenge.citations
    for omitted in assessment.omitted_risks:
        assert omitted.citations
    assert assessment.provenance.agent_role == "INDEPENDENT_RISK_REVIEW"
    assert {ref.maker_source.value for ref in assessment.provenance.maker_assessments_reviewed} == {
        "CREDIT_UNDERWRITING",
        "LEGAL_COMPLIANCE_COLLATERAL",
    }
    assert any(
        event.event_type == "RISK_REVIEW_ASSESSMENT_PERSISTED"
        for event in repository.audit_events
    )


@pytest.mark.asyncio
async def test_fabricated_target_id_is_rejected_not_persisted() -> None:
    underwriting = build_underwriting()
    legal = build_legal()

    def poisoned(request: ReasonRequest) -> Mapping[str, Any]:
        payload = valid_payload(underwriting, legal)
        payload["challenges"][0]["target"] = {
            "maker_source": "CREDIT_UNDERWRITING",
            "maker_assessment_id": str(underwriting.id),
            "section_path": "risks[999]",  # does not exist
        }
        payload["challenges"][0]["citations"] = [
            {
                "kind": "MAKER_FINDING",
                "ref": {
                    "maker_source": "CREDIT_UNDERWRITING",
                    "maker_assessment_id": str(underwriting.id),
                    "section_path": "risks[999]",
                },
            }
        ]
        return payload

    repository = FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=underwriting,
        underwriting_execution_id=underwriting.provenance.execution_id,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )
    processor = build_processor(repository, FakeGateway(poisoned))

    result = await processor.process(checker_task(), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.RETRY_WAIT
    assert repository.assessments == {}
    assert repository.handoffs == {}
    assert any(
        event.event_type == "RISK_REVIEW_OUTPUT_REJECTED" for event in repository.audit_events
    )


@pytest.mark.asyncio
async def test_readiness_requires_both_maker_handoffs_missing_underwriting() -> None:
    legal = build_legal()
    repository = FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=None,
        underwriting_execution_id=None,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )
    processor = build_processor(repository, FakeGateway(lambda request: {}))

    result = await processor.process(checker_task(), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.FAILED_MANUAL_REVIEW
    assert "both maker handoffs" in result.reason
    assert any(
        event.event_type == "RISK_REVIEW_MAKER_OUTPUTS_INCOMPLETE"
        for event in repository.audit_events
    )


@pytest.mark.asyncio
async def test_readiness_requires_both_maker_handoffs_missing_legal() -> None:
    underwriting = build_underwriting()
    repository = FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=underwriting,
        underwriting_execution_id=underwriting.provenance.execution_id,
        legal=None,
        legal_execution_id=None,
    )
    processor = build_processor(repository, FakeGateway(lambda request: {}))

    result = await processor.process(checker_task(), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.FAILED_MANUAL_REVIEW
    assert "both maker handoffs" in result.reason


@pytest.mark.asyncio
async def test_same_execution_guard_fails_closed_before_any_model_call() -> None:
    # (e) maker-checker separation: the processor refuses to run if its own
    # execution id would equal a reviewed maker's execution id.
    colliding_id = uuid4()
    underwriting = build_underwriting(execution_id=colliding_id)
    legal = build_legal()
    repository = FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=underwriting,
        underwriting_execution_id=colliding_id,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )

    class ExplodingGateway(FakeGateway):
        async def reason(self, request: ReasonRequest) -> InferenceResult:
            raise AssertionError("the same-execution guard must fire before any model call")

    processor = build_processor(
        repository, ExplodingGateway(lambda r: {}), execution_id_factory=lambda: colliding_id
    )

    result = await processor.process(checker_task(), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.FAILED_MANUAL_REVIEW
    assert "maker-checker separation" in result.reason
    assert any(
        event.event_type == "RISK_REVIEW_SAME_EXECUTION_GUARD_TRIGGERED"
        for event in repository.audit_events
    )
    assert repository.assessments == {}


@pytest.mark.asyncio
async def test_fail_closed_without_configured_gateway() -> None:
    underwriting = build_underwriting()
    legal = build_legal()
    repository = FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=underwriting,
        underwriting_execution_id=underwriting.provenance.execution_id,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )
    processor = build_processor(repository, None)

    result = await processor.process(checker_task(), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.FAILED_MANUAL_REVIEW
    assert "reasoning endpoint" in result.reason
    assert any(
        event.event_type == "RISK_REVIEW_GATEWAY_UNAVAILABLE"
        for event in repository.audit_events
    )


@pytest.mark.asyncio
async def test_fail_closed_when_endpoint_unavailable() -> None:
    underwriting = build_underwriting()
    legal = build_legal()
    repository = FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=underwriting,
        underwriting_execution_id=underwriting.provenance.execution_id,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )
    processor = build_processor(repository, UnavailableGateway())

    result = await processor.process(checker_task(), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.FAILED_MANUAL_REVIEW
    assert repository.assessments == {}


@pytest.mark.asyncio
async def test_stale_case_version_is_superseded() -> None:
    underwriting = build_underwriting()
    legal = build_legal()
    repository = FakeRiskReviewRepository(
        view=checker_view(case_version=2),
        underwriting=underwriting,
        underwriting_execution_id=underwriting.provenance.execution_id,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )
    processor = build_processor(repository, FakeGateway(lambda request: {}))

    result = await processor.process(checker_task(case_version=1), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.SUPERSEDED
    assert repository.assessments == {}


@pytest.mark.asyncio
async def test_resume_from_inference_checkpoint_skips_model_call() -> None:
    underwriting = build_underwriting()
    legal = build_legal()
    repository = FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=underwriting,
        underwriting_execution_id=underwriting.provenance.execution_id,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )
    gateway = FakeGateway(lambda request: valid_payload(underwriting, legal))
    processor = build_processor(repository, gateway)
    recorder = CheckpointRecorder()
    await processor.process(checker_task(), None, recorder.save)
    inference_checkpoint = next(
        c for c in recorder.saved if c.checkpoint_type == CHECKPOINT_INFERENCE
    )

    resumed_repository = FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=underwriting,
        underwriting_execution_id=underwriting.provenance.execution_id,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )

    class ExplodingGateway(FakeGateway):
        async def reason(self, request: ReasonRequest) -> InferenceResult:
            raise AssertionError("resume must not re-call the model")

    resumed = build_processor(resumed_repository, ExplodingGateway(lambda r: {}))
    resume_recorder = CheckpointRecorder()

    result = await resumed.process(checker_task(), inference_checkpoint, resume_recorder.save)

    assert result == StageResult()
    assert resume_recorder.types() == [CHECKPOINT_PERSISTED]
    assert len(resumed_repository.assessments) == 1


@pytest.mark.asyncio
async def test_redelivery_after_terminal_checkpoint_is_noop() -> None:
    underwriting = build_underwriting()
    legal = build_legal()
    repository = FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=underwriting,
        underwriting_execution_id=underwriting.provenance.execution_id,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )
    processor = build_processor(
        repository, FakeGateway(lambda request: valid_payload(underwriting, legal))
    )
    recorder = CheckpointRecorder()
    await processor.process(checker_task(), None, recorder.save)
    terminal = recorder.saved[-1]
    assert terminal.checkpoint_type == CHECKPOINT_PERSISTED
    persist_calls = repository.persist_calls

    result = await processor.process(checker_task(), terminal, CheckpointRecorder().save)

    assert result == StageResult()
    assert repository.persist_calls == persist_calls
    assert len(repository.handoffs) == 1


@pytest.mark.asyncio
async def test_redelivery_without_checkpoint_finds_durable_output_idempotently() -> None:
    underwriting = build_underwriting()
    legal = build_legal()
    repository = FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=underwriting,
        underwriting_execution_id=underwriting.provenance.execution_id,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )
    first = build_processor(
        repository, FakeGateway(lambda request: valid_payload(underwriting, legal))
    )
    await first.process(checker_task(), None, CheckpointRecorder().save)

    class ExplodingGateway(FakeGateway):
        async def reason(self, request: ReasonRequest) -> InferenceResult:
            raise AssertionError("redelivery must not re-call the model")

    second = build_processor(repository, ExplodingGateway(lambda r: {}))
    recorder = CheckpointRecorder()
    result = await second.process(checker_task(), None, recorder.save)

    assert result == StageResult()
    assert recorder.types() == [CHECKPOINT_PERSISTED]
    assert len(repository.assessments) == 1
    assert len(repository.handoffs) == 1


def test_response_schema_is_closed_and_decision_free() -> None:
    underwriting = build_underwriting()
    legal = build_legal()
    universe = build_target_universe(underwriting, legal)
    fact_ids = (str(LIVE_FACT_ID),)
    schema = build_response_schema(
        fact_ids=fact_ids,
        calculator_result_ids=(),
        policy_hits=(),
        invocation_ids=(),
        universe=universe,
    )
    from creditops.infrastructure.fpt.gateway import _validate_schema

    _validate_schema(schema)  # would raise on a forbidden key or open schema
    Draft202012Validator.check_schema(dict(schema))
    payload = valid_payload(underwriting, legal)
    Draft202012Validator(dict(schema)).validate(payload)


def test_gateway_rejects_decision_fields_in_checker_output() -> None:
    from creditops.application.ports.model_gateway import InferenceValidationError
    from creditops.infrastructure.fpt.gateway import _walk_json

    with pytest.raises(InferenceValidationError):
        _walk_json({"challenges": [], "approve": True})
    with pytest.raises(InferenceValidationError):
        _walk_json({"decision": "APPROVED"})


@dataclass
class _CheckerTaskStore:
    def __init__(self) -> None:
        self.record = replace(checker_task(), status=TaskStatus.PENDING, lease_token=None)
        self.checkpoints: list[TaskCheckpoint] = []
        self.slot_taken = False

    async def acquire_worker_slot(self, **kwargs: object) -> bool:
        del kwargs
        if self.slot_taken:
            return False
        self.slot_taken = True
        return True

    async def release_worker_slot(self, **kwargs: object) -> None:
        del kwargs
        self.slot_taken = False

    async def claim(self, **kwargs: object) -> TaskRecord | None:
        if kwargs["task_id"] != self.record.id:
            return None
        if self.record.status not in (TaskStatus.PENDING, TaskStatus.RETRY_WAIT):
            return None
        token = kwargs["lease_token"]
        assert isinstance(token, UUID)
        self.record = replace(self.record, status=TaskStatus.RUNNING, lease_token=token)
        return self.record

    async def get(
        self, task_id: UUID, *, case_id: UUID | None = None, actor_id: UUID | None = None
    ) -> TaskRecord | None:
        del case_id, actor_id
        return self.record if task_id == self.record.id else None

    async def latest_checkpoint(self, **kwargs: object) -> TaskCheckpoint | None:
        del kwargs
        return self.checkpoints[-1] if self.checkpoints else None

    async def checkpoint(self, **kwargs: Any) -> TaskCheckpoint:
        checkpoint = TaskCheckpoint(
            task_id=kwargs["task_id"],
            case_id=kwargs["case_id"],
            case_version=kwargs["case_version"],
            document_version_id=kwargs["document_version_id"],
            sequence_no=kwargs["sequence_no"],
            checkpoint_type=kwargs["checkpoint_type"],
            checkpoint_schema_version=kwargs["checkpoint_schema_version"],
            checkpoint_data=kwargs["checkpoint_data"],
            created_at=NOW,
        )
        self.checkpoints.append(checkpoint)
        return checkpoint

    async def succeed(self, **kwargs: object) -> None:
        del kwargs
        self.record = replace(self.record, status=TaskStatus.SUCCEEDED, lease_token=None)

    async def mark_superseded(self, **kwargs: object) -> None:
        del kwargs
        self.record = replace(self.record, status=TaskStatus.SUPERSEDED, lease_token=None)

    async def retry_or_fail(self, **kwargs: Any) -> RetryDecision:
        failed = self.record.attempt_count >= self.record.max_attempts
        status = TaskStatus.FAILED_MANUAL_REVIEW if failed else TaskStatus.RETRY_WAIT
        self.record = replace(self.record, status=status, lease_token=None)
        return RetryDecision(
            status, self.record.attempt_count, None if failed else NOW, str(kwargs["reason"])
        )


class _SingleMessageQueue:
    def __init__(self, envelope: TaskEnvelopeV1) -> None:
        self.message: QueueMessage | None = QueueMessage(7, 1, NOW, NOW, envelope)
        self.archived: list[int] = []

    async def send(self, envelope: TaskEnvelopeV1, *, delay_seconds: int = 0) -> int:
        del envelope, delay_seconds
        return 8

    async def read_one(self, *, visibility_timeout_seconds: int) -> QueueMessage | None:
        del visibility_timeout_seconds
        return self.message

    async def extend_visibility(self, message_id: int, *, visibility_timeout_seconds: int) -> None:
        del message_id, visibility_timeout_seconds

    async def archive(self, message_id: int) -> None:
        self.archived.append(message_id)
        self.message = None


class _NullOrchestrationRepository:
    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        del event


@pytest.mark.asyncio
async def test_worker_loop_dispatches_registered_checker_processor() -> None:
    underwriting = build_underwriting()
    legal = build_legal()
    repository = FakeRiskReviewRepository(
        view=checker_view(),
        underwriting=underwriting,
        underwriting_execution_id=underwriting.provenance.execution_id,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id,
    )
    registry = ProcessorRegistry(
        {
            TaskType.INDEPENDENT_RISK_REVIEW: build_processor(
                repository, FakeGateway(lambda request: valid_payload(underwriting, legal))
            )
        },
        fallback=ManualReviewProcessor(_NullOrchestrationRepository(), clock=lambda: NOW),
    )
    store = _CheckerTaskStore()
    queue = _SingleMessageQueue(
        TaskEnvelopeV1(
            task_id=TASK_ID,
            case_id=CASE_ID,
            case_version=1,
            task_type=TaskType.INDEPENDENT_RISK_REVIEW,
            document_version_id=None,
        )
    )
    worker = RunWorkerOnce(store, queue, registry, clock=lambda: NOW)

    result = await worker.run_once()

    assert result.outcome == WorkerOutcome.SUCCEEDED
    assert store.record.status is TaskStatus.SUCCEEDED
    assert queue.archived == [7]
    (handoff,) = repository.handoffs.values()
    assert handoff.state == OPERATIONS_HANDOFF_STATE
