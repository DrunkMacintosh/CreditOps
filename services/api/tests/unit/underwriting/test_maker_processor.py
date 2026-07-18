"""Maker use-case and worker-processor tests for CREDIT_UNDERWRITING.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture case
belongs to the invented SME "Cong ty TNHH Nong San Sach Vinh Phuc Demo"; every
figure is fabricated.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator

from creditops.application.orchestration.graph import DependencyTemplate
from creditops.application.orchestration.processors import (
    ManualReviewProcessor,
    ProcessorRegistry,
)
from creditops.application.orchestration.readiness import evaluate_readiness
from creditops.application.ports.model_gateway import (
    InferenceResult,
    InferenceUnavailableError,
    ReasonRequest,
)
from creditops.application.ports.orchestration import (
    BlockingGap,
    GateRecord,
    OrchestrationAuditEvent,
    OrchestrationSnapshot,
)
from creditops.application.ports.queue import (
    QueueMessage,
    RetryDecision,
    TaskCheckpoint,
    TaskRecord,
)
from creditops.application.ports.underwriting import (
    EvidenceFact,
    EvidenceView,
    PersistedMakerOutput,
    ProvisionalGapRecord,
)
from creditops.application.underwriting.evidence import (
    CANONICAL_FIELD_KEYS,
    PRIOR_PERIOD_FIELD_KEYS,
    CalculatorSuite,
    build_calculator_suite,
)
from creditops.application.underwriting.maker import (
    RISK_REVIEW_HANDOFF_STATE,
    RunUnderwritingInference,
    UnderwritingPrompt,
    build_response_schema,
    build_untrusted_context,
)
from creditops.application.underwriting.processor import (
    CHECKPOINT_CALCULATORS,
    CHECKPOINT_EVIDENCE_VIEW,
    CHECKPOINT_INFERENCE,
    CHECKPOINT_PERSISTED,
    CreditUnderwritingProcessor,
)
from creditops.application.use_cases.run_worker_once import (
    RunWorkerOnce,
    StageResult,
    WorkerOutcome,
)
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import (
    GateStatus,
    GateType,
    TaskType,
)
from creditops.domain.tasks import TaskEnvelopeV1
from creditops.domain.underwriting import GapBlockingLevel, UnderwritingAssessment

NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
CASE_ID = UUID("10000000-0000-0000-0000-00000000aa01")
TASK_ID = UUID("30000000-0000-0000-0000-00000000aa01")

# Synthetic figures for the invented SME (millions of VND, fabricated).
_FIXTURE_VALUES: dict[str, str] = {
    "financials.current_assets": "1500",
    "financials.current_liabilities": "1000",
    "financials.inventory": "600",
    "financials.total_debt": "800",
    "financials.total_equity": "400",
    "financials.total_assets": "2000",
    "financials.revenue": "10000",
    "financials.gross_profit": "2500",
    "financials.operating_profit": "1200",
    "financials.net_profit": "800",
    "financials.accounts_receivable": "500",
    "financials.accounts_payable": "400",
    "financials.cost_of_goods_sold": "7300",
    "financials.own_working_capital": "200",
    "financials.other_funding_sources": "65",
    "financials.prior.revenue": "8000",
    "financials.prior.net_profit": "500",
}


def make_view(
    *, exclude: frozenset[str] = frozenset(), case_version: int = 1
) -> EvidenceView:
    facts = tuple(
        EvidenceFact(
            confirmed_fact_id=uuid4(),
            field_key=field_key,
            value=value,
            document_version_id=uuid4(),
        )
        for field_key, value in _FIXTURE_VALUES.items()
        if field_key not in exclude
    )
    return EvidenceView(
        case_id=CASE_ID,
        case_version=case_version,
        built_at=NOW,
        confirmed_facts=facts,
    )


def maker_task(case_version: int = 1) -> TaskRecord:
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
        idempotency_key=f"UW:{CASE_ID}:1",
        task_type=TaskType.CREDIT_UNDERWRITING,
    )


def valid_payload(
    view: EvidenceView, suite: CalculatorSuite, *, propose_amount: bool = True
) -> dict[str, Any]:
    fact_cite = {
        "kind": "CONFIRMED_FACT",
        "confirmed_fact_id": str(view.confirmed_facts[0].confirmed_fact_id),
    }
    calc_cite = {"kind": "CALCULATOR_RESULT", "result_id": suite.results[0].result_id}
    wc_gap_cite = {
        "kind": "CALCULATOR_RESULT",
        "result_id": suite.results[-1].result_id,
    }
    scenario_cite = {
        "kind": "CALCULATOR_RESULT",
        "result_id": suite.scenario_results[0].result_id,
    }

    def finding(text: str, cite: Mapping[str, Any]) -> dict[str, Any]:
        return {"statement_vi": text, "citations": [cite], "confidence": "MEDIUM"}

    return {
        "business": {
            "findings": [finding("Hoat dong nong san on dinh (du lieu mo phong).", fact_cite)]
        },
        "financial": {
            "findings": [finding("He so thanh toan hien hanh theo ket qua tinh toan.", calc_cite)]
        },
        "cash_flow": {
            "findings": [finding("Chu ky chuyen doi tien mat theo ket qua tinh toan.", calc_cite)]
        },
        "repayment_source": {
            "findings": [finding("Nguon tra no chinh tu doanh thu ban hang.", fact_cite)],
            "downside_scenarios": [
                finding("Kich ban doanh thu giam 20% theo ket qua tinh toan.", scenario_cite)
            ],
        },
        "proposed_structure": {
            "instrument_vi": "Han muc von luu dong ngan han (de xuat so bo)",
            **(
                {"proposed_amount_result_id": suite.results[-1].result_id}
                if propose_amount
                else {}
            ),
            "tenor_months": 12,
            "findings": [
                finding("Muc de xuat theo khoang thieu hut von luu dong.", wc_gap_cite)
            ],
        },
        "risks": [
            {
                "risk_id": "rui-ro-tap-trung",
                "description_vi": "Phu thuoc mot nhom khach hang lon.",
                "citations": [fact_cite],
                "confidence": "MEDIUM",
            }
        ],
        "mitigants": [
            {
                "risk_id": "rui-ro-tap-trung",
                "description_vi": "Da dang hoa kenh phan phoi.",
                "citations": [fact_cite],
                "confidence": "LOW",
            }
        ],
        "assumptions": [
            {
                "statement_vi": "Gia nong san giu on dinh trong ky vay.",
                "rationale_vi": "Chua co du kien xac nhan ve gia ban ky toi.",
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
            prompt_version="underwriting-prompt-v1",
            schema_version="underwriting-assessment-v1",
            route_version="fpt-route-v1",
            correlation_id=request.correlation_id,
            started_at=NOW,
            latency_ms=1,
        )

    async def extract_kie(self, request: object) -> InferenceResult:
        raise AssertionError("maker must not call extraction")

    async def extract_table(self, request: object) -> InferenceResult:
        raise AssertionError("maker must not call extraction")

    async def inspect_vision(self, request: object) -> InferenceResult:
        raise AssertionError("maker must not call vision")

    async def embed(self, request: object) -> InferenceResult:
        raise AssertionError("maker must not call embedding")


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
    handoff_data: Mapping[str, Any]


class FakeUnderwritingRepository:
    def __init__(self, view: EvidenceView | None) -> None:
        self.view = view
        self.persisted: dict[tuple[UUID, int, UUID], PersistedMakerOutput] = {}
        self.assessments: dict[UUID, UnderwritingAssessment] = {}
        self.gap_rows: list[tuple[UUID, ProvisionalGapRecord, UUID]] = []
        self.handoffs: dict[UUID, StoredHandoff] = {}
        self.audit_events: list[OrchestrationAuditEvent] = []
        self.persist_calls = 0

    async def load_evidence_view(self, case_id: UUID) -> EvidenceView | None:
        if self.view is not None and self.view.case_id == case_id:
            return self.view
        return None

    async def find_persisted(
        self, *, case_id: UUID, case_version: int, task_id: UUID
    ) -> PersistedMakerOutput | None:
        found = self.persisted.get((case_id, case_version, task_id))
        if found is None:
            return None
        return replace(found, created=False)

    async def persist_assessment(
        self,
        *,
        assessment: UnderwritingAssessment,
        handoff_id: UUID,
        handoff_state: str,
        gaps: tuple[ProvisionalGapRecord, ...],
    ) -> PersistedMakerOutput:
        self.persist_calls += 1
        key = (
            assessment.provenance.case_id,
            assessment.provenance.case_version,
            assessment.provenance.task_id,
        )
        existing = self.persisted.get(key)
        if existing is not None:
            return replace(existing, created=False)
        gap_ids = []
        for gap in gaps:
            gap_id = uuid4()
            gap_ids.append(gap_id)
            self.gap_rows.append((gap_id, gap, assessment.provenance.task_id))
        self.handoffs[handoff_id] = StoredHandoff(
            handoff_id=handoff_id,
            case_id=assessment.provenance.case_id,
            case_version=assessment.provenance.case_version,
            state=handoff_state,
            source_task_id=assessment.provenance.task_id,
            handoff_data={
                "assessmentId": str(assessment.id),
                "executionId": str(assessment.provenance.execution_id),
            },
        )
        self.assessments[assessment.id] = assessment
        output = PersistedMakerOutput(
            assessment_id=assessment.id,
            handoff_id=handoff_id,
            gap_ids=tuple(gap_ids),
            handoff_state=handoff_state,
            created=True,
        )
        self.persisted[key] = output
        return output

    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        self.audit_events.append(event)


@dataclass
class CheckpointRecorder:
    saved: list[TaskCheckpoint] = field(default_factory=list)

    async def save(
        self, checkpoint_type: str, data: Mapping[str, object]
    ) -> TaskCheckpoint:
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
    repository: FakeUnderwritingRepository,
    gateway: FakeGateway | None,
) -> CreditUnderwritingProcessor:
    inference = (
        RunUnderwritingInference(gateway, prompt=UnderwritingPrompt("prompt-vi-test"))
        if gateway is not None
        else None
    )
    return CreditUnderwritingProcessor(
        repository,
        inference,
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_happy_path_persists_cited_assessment_and_risk_review_handoff() -> None:
    view = make_view()
    suite = build_calculator_suite(view)
    gateway = FakeGateway(
        lambda request: valid_payload(view, suite), validate_against_schema=True
    )
    repository = FakeUnderwritingRepository(view)
    processor = build_processor(repository, gateway)
    recorder = CheckpointRecorder()

    result = await processor.process(maker_task(), None, recorder.save)

    assert result == StageResult()
    assert recorder.types() == [
        CHECKPOINT_EVIDENCE_VIEW,
        CHECKPOINT_CALCULATORS,
        CHECKPOINT_INFERENCE,
        CHECKPOINT_PERSISTED,
    ]
    # (f) maker output is transferable to the checker: one handoff, bound to
    # the task's case version, in state READY_FOR_RISK_REVIEW.
    (handoff,) = repository.handoffs.values()
    assert handoff.state == RISK_REVIEW_HANDOFF_STATE == "READY_FOR_RISK_REVIEW"
    assert handoff.case_id == CASE_ID
    assert handoff.case_version == 1
    assert handoff.source_task_id == TASK_ID
    (assessment,) = repository.assessments.values()
    assert handoff.handoff_data["assessmentId"] == str(assessment.id)
    # (b) every material finding in the persisted assessment carries citations.
    all_findings = [
        *assessment.business.findings,
        *assessment.financial.findings,
        *assessment.cash_flow.findings,
        *assessment.repayment_source.findings,
        *assessment.repayment_source.downside_scenarios,
        *assessment.proposed_structure.findings,
    ]
    assert all_findings
    for finding in all_findings:
        assert finding.citations
    for item in (*assessment.risks, *assessment.mitigants):
        assert item.citations
    # Provenance envelope is complete.
    assert assessment.provenance.agent_role == "CREDIT_UNDERWRITING"
    assert assessment.provenance.case_id == CASE_ID
    assert assessment.provenance.case_version == 1
    assert assessment.provenance.task_id == TASK_ID
    # Deterministic amount: resolved from the cited calculator result, and the
    # calculators ran BEFORE inference (their results are in the request).
    assert assessment.proposed_structure.proposed_amount_vnd is not None
    request = gateway.requests[0]
    assert suite.results[0].result_id in request.content
    assert any(
        event.event_type == "UNDERWRITING_ASSESSMENT_PERSISTED"
        for event in repository.audit_events
    )


@pytest.mark.asyncio
async def test_unknown_citation_is_rejected_not_persisted() -> None:
    # (c) an LLM response citing evidence outside the scoped view is rejected.
    view = make_view()
    suite = build_calculator_suite(view)

    def poisoned(request: ReasonRequest) -> Mapping[str, Any]:
        payload = valid_payload(view, suite)
        payload["business"]["findings"][0]["citations"] = [
            {"kind": "CONFIRMED_FACT", "confirmed_fact_id": str(uuid4())}
        ]
        return payload

    repository = FakeUnderwritingRepository(view)
    processor = build_processor(repository, FakeGateway(poisoned))
    recorder = CheckpointRecorder()

    result = await processor.process(maker_task(), None, recorder.save)

    assert result.status == WorkerOutcome.RETRY_WAIT
    assert "rejected" in result.reason
    assert repository.assessments == {}
    assert repository.handoffs == {}
    assert any(
        event.event_type == "UNDERWRITING_OUTPUT_REJECTED"
        for event in repository.audit_events
    )


@pytest.mark.asyncio
async def test_empty_citations_are_rejected() -> None:
    view = make_view()
    suite = build_calculator_suite(view)

    def uncited(request: ReasonRequest) -> Mapping[str, Any]:
        payload = valid_payload(view, suite)
        payload["financial"]["findings"][0]["citations"] = []
        return payload

    repository = FakeUnderwritingRepository(view)
    processor = build_processor(repository, FakeGateway(uncited))

    result = await processor.process(maker_task(), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.RETRY_WAIT
    assert repository.assessments == {}


@pytest.mark.asyncio
async def test_unknown_calculator_result_citation_is_rejected() -> None:
    view = make_view()
    suite = build_calculator_suite(view)

    def invented(request: ReasonRequest) -> Mapping[str, Any]:
        payload = valid_payload(view, suite)
        payload["financial"]["findings"][0]["citations"] = [
            {"kind": "CALCULATOR_RESULT", "result_id": "calc_khong_ton_tai"}
        ]
        return payload

    repository = FakeUnderwritingRepository(view)
    processor = build_processor(repository, FakeGateway(invented))

    result = await processor.process(maker_task(), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.RETRY_WAIT
    assert repository.assessments == {}


@pytest.mark.asyncio
async def test_missing_evidence_creates_provisional_blocking_gap() -> None:
    # (d) a missing confirmed fact becomes a PROVISIONAL gap with its
    # blocking level, even when the model declares no gap itself.
    view = make_view(exclude=frozenset({"financials.inventory"}))
    suite = build_calculator_suite(view)
    repository = FakeUnderwritingRepository(view)
    processor = build_processor(
        repository,
        FakeGateway(
            lambda request: valid_payload(view, suite, propose_amount=False)
        ),
    )

    result = await processor.process(maker_task(), None, CheckpointRecorder().save)

    assert result == StageResult()
    assert repository.gap_rows
    blocking = [
        gap
        for _, gap, _ in repository.gap_rows
        if gap.blocking_level is GapBlockingLevel.BLOCKING
    ]
    assert any("financials.inventory" in gap.missing_information_vi for gap in blocking)
    (assessment,) = repository.assessments.values()
    assert any(
        gap.blocking_level is GapBlockingLevel.BLOCKING
        for gap in assessment.evidence_gaps
    )


def test_blocking_gap_surfaces_to_readiness() -> None:
    # (d, continued) the persisted BLOCKING gap blocks the node it is attached
    # to in the deterministic readiness engine.
    template = DependencyTemplate.canonical()
    snapshot = OrchestrationSnapshot(
        case_id=CASE_ID,
        case_version=1,
        has_intake_handoff=True,
        tasks=(),
        gates=(
            GateRecord(
                gate_type=GateType.G1_INTAKE_COMPLETE,
                case_version=1,
                status=GateStatus.SATISFIED,
            ),
        ),
        blocking_gaps=(),
    )
    baseline = evaluate_readiness(snapshot, template=template)
    assert (
        baseline.by_type(TaskType.CREDIT_UNDERWRITING).readiness.value == "READY"
    )
    from creditops.application.ports.orchestration import OrchestrationTaskRow

    blocked_snapshot = replace(
        snapshot,
        tasks=(
            OrchestrationTaskRow(
                task_id=TASK_ID,
                task_type=TaskType.CREDIT_UNDERWRITING,
                case_version=1,
                status=TaskStatus.SUPERSEDED,
            ),
        ),
        blocking_gaps=(
            BlockingGap(
                gap_id=uuid4(),
                affected_task_id=TASK_ID,
                blocking_level="BLOCKING",
            ),
        ),
    )
    report = evaluate_readiness(blocked_snapshot, template=template)
    node = report.by_type(TaskType.CREDIT_UNDERWRITING)
    assert node.readiness.value == "BLOCKED"
    assert "blocking evidence gap" in node.reason


@pytest.mark.asyncio
async def test_fail_closed_without_configured_gateway() -> None:
    view = make_view()
    repository = FakeUnderwritingRepository(view)
    processor = build_processor(repository, None)

    result = await processor.process(maker_task(), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.FAILED_MANUAL_REVIEW
    assert "reasoning endpoint" in result.reason
    assert repository.assessments == {}
    assert repository.handoffs == {}
    assert any(
        event.event_type == "UNDERWRITING_GATEWAY_UNAVAILABLE"
        for event in repository.audit_events
    )


@pytest.mark.asyncio
async def test_fail_closed_when_endpoint_unavailable() -> None:
    view = make_view()
    repository = FakeUnderwritingRepository(view)
    processor = build_processor(repository, UnavailableGateway())

    result = await processor.process(maker_task(), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.FAILED_MANUAL_REVIEW
    assert repository.assessments == {}


@pytest.mark.asyncio
async def test_stale_case_version_is_superseded() -> None:
    view = make_view(case_version=2)
    repository = FakeUnderwritingRepository(view)
    processor = build_processor(
        repository,
        FakeGateway(lambda request: {}),
    )

    result = await processor.process(maker_task(case_version=1), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.SUPERSEDED
    assert repository.assessments == {}


@pytest.mark.asyncio
async def test_resume_from_inference_checkpoint_skips_model_call() -> None:
    view = make_view()
    suite = build_calculator_suite(view)
    repository = FakeUnderwritingRepository(view)
    gateway = FakeGateway(lambda request: valid_payload(view, suite))
    processor = build_processor(repository, gateway)
    recorder = CheckpointRecorder()
    await processor.process(maker_task(), None, recorder.save)
    inference_checkpoint = next(
        checkpoint
        for checkpoint in recorder.saved
        if checkpoint.checkpoint_type == CHECKPOINT_INFERENCE
    )

    # Fresh repository state: the crash happened after INFERENCE_VALIDATED but
    # before persistence.  Resume must not call the model again.
    resumed_repository = FakeUnderwritingRepository(view)

    class ExplodingGateway(FakeGateway):
        async def reason(self, request: ReasonRequest) -> InferenceResult:
            raise AssertionError("resume must not re-call the model")

    resumed = build_processor(resumed_repository, ExplodingGateway(lambda r: {}))
    resume_recorder = CheckpointRecorder()

    result = await resumed.process(maker_task(), inference_checkpoint, resume_recorder.save)

    assert result == StageResult()
    assert resume_recorder.types() == [CHECKPOINT_PERSISTED]
    (assessment,) = resumed_repository.assessments.values()
    original = repository.assessments[UUID(str(assessment.id))]
    # The persisted assessment is byte-identical to the validated one: an
    # immutable, case-version-bound package.
    assert assessment == original


@pytest.mark.asyncio
async def test_redelivery_after_terminal_checkpoint_is_noop() -> None:
    view = make_view()
    suite = build_calculator_suite(view)
    repository = FakeUnderwritingRepository(view)
    processor = build_processor(
        repository, FakeGateway(lambda request: valid_payload(view, suite))
    )
    recorder = CheckpointRecorder()
    await processor.process(maker_task(), None, recorder.save)
    terminal = recorder.saved[-1]
    assert terminal.checkpoint_type == CHECKPOINT_PERSISTED
    persist_calls = repository.persist_calls

    result = await processor.process(maker_task(), terminal, CheckpointRecorder().save)

    assert result == StageResult()
    assert repository.persist_calls == persist_calls
    assert len(repository.handoffs) == 1


@pytest.mark.asyncio
async def test_redelivery_without_checkpoint_finds_durable_output() -> None:
    # Lost checkpoint + duplicate delivery: the durable persisted output is
    # found by (case, version, task) and no second assessment or handoff is
    # created; the model is not called again.
    view = make_view()
    suite = build_calculator_suite(view)
    repository = FakeUnderwritingRepository(view)
    first = build_processor(
        repository, FakeGateway(lambda request: valid_payload(view, suite))
    )
    await first.process(maker_task(), None, CheckpointRecorder().save)

    class ExplodingGateway(FakeGateway):
        async def reason(self, request: ReasonRequest) -> InferenceResult:
            raise AssertionError("redelivery must not re-call the model")

    second = build_processor(repository, ExplodingGateway(lambda r: {}))
    recorder = CheckpointRecorder()
    result = await second.process(maker_task(), None, recorder.save)

    assert result == StageResult()
    assert recorder.types() == [CHECKPOINT_PERSISTED]
    assert len(repository.assessments) == 1
    assert len(repository.handoffs) == 1


@pytest.mark.asyncio
async def test_empty_evidence_view_fails_closed() -> None:
    view = EvidenceView(case_id=CASE_ID, case_version=1, built_at=NOW)
    repository = FakeUnderwritingRepository(view)
    processor = build_processor(repository, FakeGateway(lambda request: {}))

    result = await processor.process(maker_task(), None, CheckpointRecorder().save)

    assert result.status == WorkerOutcome.FAILED_MANUAL_REVIEW
    assert "no confirmed facts" in result.reason


def test_response_schema_is_closed_and_decision_free() -> None:
    # (e) the closed response schema passes the gateway's own validation,
    # which rejects any forbidden decision key.
    view = make_view()
    suite = build_calculator_suite(view)
    fact_ids = tuple(str(fact.confirmed_fact_id) for fact in view.confirmed_facts)
    schema = build_response_schema(fact_ids, suite.result_ids())
    from creditops.infrastructure.fpt.gateway import _validate_schema

    _validate_schema(schema)  # would raise on a forbidden key or open schema
    Draft202012Validator.check_schema(dict(schema))
    payload = valid_payload(view, suite)
    Draft202012Validator(dict(schema)).validate(payload)


def test_gateway_rejects_decision_fields_in_maker_output() -> None:
    # (e, continued) even a schema-conforming transport cannot smuggle a
    # decision key past the gateway's output walk.
    from creditops.application.ports.model_gateway import InferenceValidationError
    from creditops.infrastructure.fpt.gateway import _walk_json

    with pytest.raises(InferenceValidationError):
        _walk_json({"business": {"findings": []}, "approve": True})
    with pytest.raises(InferenceValidationError):
        _walk_json({"credit_score": 700})


def test_untrusted_context_contains_only_scoped_evidence() -> None:
    view = make_view()
    suite = build_calculator_suite(view)
    content = build_untrusted_context(view, suite)
    assert str(view.confirmed_facts[0].confirmed_fact_id) in content
    for result in suite.results:
        assert result.result_id in content
    assert "NOT_COMPUTABLE" not in content or suite.missing


def test_all_prior_field_keys_have_current_counterparts() -> None:
    for metric in PRIOR_PERIOD_FIELD_KEYS:
        assert metric in CANONICAL_FIELD_KEYS


class _SingleMessageQueue:
    def __init__(self, envelope: TaskEnvelopeV1) -> None:
        self.message: QueueMessage | None = QueueMessage(7, 1, NOW, NOW, envelope)
        self.archived: list[int] = []

    async def send(self, envelope: TaskEnvelopeV1, *, delay_seconds: int = 0) -> int:
        del envelope, delay_seconds
        return 8

    async def read_one(
        self, *, visibility_timeout_seconds: int
    ) -> QueueMessage | None:
        del visibility_timeout_seconds
        return self.message

    async def extend_visibility(
        self, message_id: int, *, visibility_timeout_seconds: int
    ) -> None:
        del message_id, visibility_timeout_seconds

    async def archive(self, message_id: int) -> None:
        self.archived.append(message_id)
        self.message = None


class _MakerTaskStore:
    def __init__(self) -> None:
        self.record = replace(maker_task(), status=TaskStatus.PENDING, lease_token=None)
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
        self,
        task_id: UUID,
        *,
        case_id: UUID | None = None,
        actor_id: UUID | None = None,
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
        self.record = replace(
            self.record, status=TaskStatus.SUCCEEDED, lease_token=None
        )

    async def mark_superseded(self, **kwargs: object) -> None:
        del kwargs
        self.record = replace(
            self.record, status=TaskStatus.SUPERSEDED, lease_token=None
        )

    async def retry_or_fail(self, **kwargs: Any) -> RetryDecision:
        failed = self.record.attempt_count >= self.record.max_attempts
        status = (
            TaskStatus.FAILED_MANUAL_REVIEW if failed else TaskStatus.RETRY_WAIT
        )
        self.record = replace(self.record, status=status, lease_token=None)
        return RetryDecision(
            status,
            self.record.attempt_count,
            None if failed else NOW,
            str(kwargs["reason"]),
        )


@pytest.mark.asyncio
async def test_worker_loop_dispatches_registered_maker_processor() -> None:
    # End-to-end through RunWorkerOnce: the CREDIT_UNDERWRITING envelope is
    # routed to the registered maker processor, the durable task succeeds,
    # checkpoints persist, and the message is archived exactly once.
    view = make_view()
    suite = build_calculator_suite(view)
    repository = FakeUnderwritingRepository(view)
    registry = ProcessorRegistry(
        {
            TaskType.CREDIT_UNDERWRITING: build_processor(
                repository,
                FakeGateway(lambda request: valid_payload(view, suite)),
            )
        },
        fallback=ManualReviewProcessor(
            _NullOrchestrationRepository(), clock=lambda: NOW
        ),
    )
    store = _MakerTaskStore()
    queue = _SingleMessageQueue(
        TaskEnvelopeV1(
            task_id=TASK_ID,
            case_id=CASE_ID,
            case_version=1,
            task_type=TaskType.CREDIT_UNDERWRITING,
            document_version_id=None,
        )
    )
    worker = RunWorkerOnce(store, queue, registry, clock=lambda: NOW)

    result = await worker.run_once()

    assert result.outcome == WorkerOutcome.SUCCEEDED
    assert store.record.status is TaskStatus.SUCCEEDED
    assert queue.archived == [7]
    assert [checkpoint.checkpoint_type for checkpoint in store.checkpoints] == [
        CHECKPOINT_EVIDENCE_VIEW,
        CHECKPOINT_CALCULATORS,
        CHECKPOINT_INFERENCE,
        CHECKPOINT_PERSISTED,
    ]
    (handoff,) = repository.handoffs.values()
    assert handoff.state == RISK_REVIEW_HANDOFF_STATE


class _NullOrchestrationRepository:
    async def append_audit(self, event: OrchestrationAuditEvent) -> None:
        del event
