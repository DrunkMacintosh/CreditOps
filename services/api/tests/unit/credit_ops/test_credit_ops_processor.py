"""Credit-ops worker-processor tests for CREDIT_OPERATIONS.

Requirements exercised: checkpoint/resume; redelivery idempotency;
fail-closed without gateway (the deterministic package alone is NEVER
persisted as a completed package); stale-version fencing; (e) package
persistence emits audit with full provenance; (f) the READY_FOR_HUMAN_DECISION
handoff binds the correct case version.

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
from test_credit_ops_analysis import (
    CASE_ID,
    build_legal,
    build_risk_review,
    build_underwriting,
)

from creditops.application.credit_ops.assembler import (
    HUMAN_DECISION_HANDOFF_STATE,
    CreditOpsPrompt,
    RunMemoInference,
)
from creditops.application.credit_ops.processor import (
    CHECKPOINT_DETERMINISTIC_PACKAGE,
    CHECKPOINT_EVIDENCE,
    CHECKPOINT_MEMO_DRAFTED,
    CHECKPOINT_PERSISTED,
    CreditOperationsProcessor,
)
from creditops.application.ports.credit_ops import (
    ChallengeDispositionSummary,
    CreditOpsUpstreamView,
    OpenGapRecord,
    PersistedCreditOpsOutput,
)
from creditops.application.ports.model_gateway import (
    InferenceResult,
    InferenceUnavailableError,
    ReasonRequest,
)
from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.application.ports.queue import TaskCheckpoint, TaskRecord
from creditops.application.use_cases.run_worker_once import WorkerOutcome
from creditops.domain.credit_ops import CreditOpsPackage
from creditops.domain.enums import TaskStatus
from creditops.domain.orchestration import TaskType
from creditops.domain.underwriting import GapBlockingLevel

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
TASK_ID = UUID("30000000-0000-0000-0000-00000000cc01")


def build_view(
    *,
    case_version: int = 1,
    with_intake: bool = True,
    with_underwriting: bool = True,
    with_legal: bool = True,
    with_risk_review: bool = True,
) -> CreditOpsUpstreamView:
    underwriting = build_underwriting() if with_underwriting else None
    legal = build_legal() if with_legal else None
    risk_review = (
        build_risk_review(underwriting or build_underwriting(), legal or build_legal())
        if with_risk_review
        else None
    )
    return CreditOpsUpstreamView(
        case_id=CASE_ID,
        case_version=case_version,
        built_at=NOW,
        has_intake_handoff=with_intake,
        intake_handoff_id=uuid4() if with_intake else None,
        underwriting=underwriting,
        underwriting_execution_id=(
            underwriting.provenance.execution_id if underwriting else None
        ),
        underwriting_handoff_id=uuid4() if underwriting else None,
        legal=legal,
        legal_execution_id=legal.provenance.execution_id if legal else None,
        legal_handoff_id=uuid4() if legal else None,
        risk_review=risk_review,
        risk_review_execution_id=(
            risk_review.provenance.execution_id if risk_review else None
        ),
        risk_review_handoff_id=uuid4() if risk_review else None,
    )


def ops_task(case_version: int = 1) -> TaskRecord:
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
        idempotency_key=f"OPS:{CASE_ID}:{case_version}",
        task_type=TaskType.CREDIT_OPERATIONS,
    )


def valid_payload(view: CreditOpsUpstreamView) -> dict[str, Any]:
    assert view.underwriting is not None
    citation = {
        "kind": "MEMO_FINDING",
        "ref": {
            "source": "CREDIT_UNDERWRITING",
            "source_assessment_id": str(view.underwriting.id),
            "section_path": "business.findings[0]",
        },
    }
    section = {
        "statements": [
            {"statement_vi": "Nhan dinh tong hop co trich dan (mo phong).", "citations": [citation]}
        ]
    }
    return {
        "tom_tat_nhu_cau": section,
        "phan_tich_maker": section,
        "ra_soat_phap_ly_tsbd": section,
        "thach_thuc_checker": section,
        "dieu_kien_de_xuat": section,
        "phu_luc_bang_chung": section,
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
            prompt_version="credit-ops-prompt-v1",
            schema_version="credit-ops-package-v1",
            route_version="fpt-route-v1",
            correlation_id=request.correlation_id,
            started_at=NOW,
            latency_ms=1,
        )

    async def extract_kie(self, request: object) -> InferenceResult:
        raise AssertionError("credit-ops must not call extraction")

    async def extract_table(self, request: object) -> InferenceResult:
        raise AssertionError("credit-ops must not call extraction")

    async def inspect_vision(self, request: object) -> InferenceResult:
        raise AssertionError("credit-ops must not call vision")

    async def embed(self, request: object) -> InferenceResult:
        raise AssertionError("credit-ops must not call embedding")


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


class FakeCreditOpsRepository:
    def __init__(
        self,
        *,
        view: CreditOpsUpstreamView | None,
        open_gaps: tuple[OpenGapRecord, ...] = (),
        dispositions: tuple[ChallengeDispositionSummary, ...] = (),
    ) -> None:
        self.view = view
        self.open_gaps = open_gaps
        self.dispositions = dispositions
        self.persisted: dict[tuple[UUID, int, UUID], PersistedCreditOpsOutput] = {}
        self.packages: dict[UUID, CreditOpsPackage] = {}
        self.handoffs: dict[UUID, StoredHandoff] = {}
        self.audit_events: list[OrchestrationAuditEvent] = []
        self.persist_calls = 0

    async def load_upstream_view(self, case_id: UUID) -> CreditOpsUpstreamView | None:
        if self.view is not None and self.view.case_id == case_id:
            return self.view
        return None

    async def load_open_gaps(
        self, case_id: UUID, case_version: int
    ) -> tuple[OpenGapRecord, ...]:
        del case_id, case_version
        return self.open_gaps

    async def load_dispositions(
        self, case_id: UUID, case_version: int
    ) -> tuple[ChallengeDispositionSummary, ...]:
        del case_id, case_version
        return self.dispositions

    async def load_latest_package(self, case_id: UUID) -> Any:
        del case_id
        return None

    async def load_action_authorizations(self, package_id: UUID) -> tuple[Any, ...]:
        del package_id
        return ()

    async def load_document_request_approvals(self, package_id: UUID) -> tuple[Any, ...]:
        del package_id
        return ()

    async def find_persisted(
        self, *, case_id: UUID, case_version: int, task_id: UUID
    ) -> PersistedCreditOpsOutput | None:
        found = self.persisted.get((case_id, case_version, task_id))
        if found is None:
            return None
        return replace(found, created=False)

    async def persist_package(
        self,
        *,
        package: CreditOpsPackage,
        handoff_id: UUID,
        handoff_state: str,
    ) -> PersistedCreditOpsOutput:
        self.persist_calls += 1
        key = (
            package.provenance.case_id,
            package.provenance.case_version,
            package.provenance.task_id,
        )
        existing = self.persisted.get(key)
        if existing is not None:
            return replace(existing, created=False)
        self.handoffs[handoff_id] = StoredHandoff(
            handoff_id=handoff_id,
            case_id=package.provenance.case_id,
            case_version=package.provenance.case_version,
            state=handoff_state,
            source_task_id=package.provenance.task_id,
        )
        self.packages[package.id] = package
        output = PersistedCreditOpsOutput(
            package_id=package.id,
            handoff_id=handoff_id,
            handoff_state=handoff_state,
            created=True,
        )
        self.persisted[key] = output
        return output

    async def record_action_authorization(self, **kwargs: Any) -> Any:
        raise AssertionError("the credit-ops processor must never record an authorization")

    async def record_document_request_approval(self, **kwargs: Any) -> Any:
        raise AssertionError("the credit-ops processor must never record an approval")

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
    repository: FakeCreditOpsRepository, gateway: FakeGateway | None
) -> CreditOperationsProcessor:
    inference = (
        RunMemoInference(gateway, prompt=CreditOpsPrompt("prompt-vi-test"))
        if gateway is not None
        else None
    )
    return CreditOperationsProcessor(repository, inference, clock=lambda: NOW)


@pytest.mark.asyncio
async def test_happy_path_persists_package_and_human_decision_handoff() -> None:
    view = build_view()
    repository = FakeCreditOpsRepository(view=view)
    gateway = FakeGateway(lambda request: valid_payload(view), validate_against_schema=True)
    processor = build_processor(repository, gateway)
    recorder = CheckpointRecorder()

    result = await processor.process(ops_task(), None, recorder.save)

    assert result.status is WorkerOutcome.SUCCEEDED
    assert recorder.types() == [
        CHECKPOINT_EVIDENCE,
        CHECKPOINT_DETERMINISTIC_PACKAGE,
        CHECKPOINT_MEMO_DRAFTED,
        CHECKPOINT_PERSISTED,
    ]
    assert len(repository.packages) == 1
    package = next(iter(repository.packages.values()))
    # The memo narrative came from the model; every deterministic part is
    # exactly the pre-computed skeleton.
    assert package.package_completeness.all_required_present is True
    # (f) the handoff binds the correct case version.
    handoff = next(iter(repository.handoffs.values()))
    assert handoff.state == HUMAN_DECISION_HANDOFF_STATE
    assert handoff.case_version == 1
    assert handoff.source_task_id == TASK_ID
    # (e) persistence emits audit with full provenance identifiers.
    persisted_events = [
        e for e in repository.audit_events if e.event_type == "CREDIT_OPS_PACKAGE_PERSISTED"
    ]
    assert len(persisted_events) == 1
    event_data = dict(persisted_events[0].event_data)
    assert event_data["packageId"] == str(package.id)
    assert event_data["executionId"] == str(package.provenance.execution_id)
    assert event_data["handoffState"] == HUMAN_DECISION_HANDOFF_STATE


@pytest.mark.asyncio
async def test_no_gateway_fails_closed_and_persists_nothing() -> None:
    # The deterministic package alone must not masquerade as a completed
    # memo: without a gateway the task fails closed BEFORE any persist.
    repository = FakeCreditOpsRepository(view=build_view())
    processor = build_processor(repository, None)
    recorder = CheckpointRecorder()

    result = await processor.process(ops_task(), None, recorder.save)

    assert result.status is WorkerOutcome.FAILED_MANUAL_REVIEW
    assert repository.packages == {}
    assert repository.handoffs == {}
    assert recorder.saved == []
    assert any(
        e.event_type == "CREDIT_OPS_GATEWAY_UNAVAILABLE" for e in repository.audit_events
    )


@pytest.mark.asyncio
async def test_gateway_unavailable_at_call_time_fails_closed() -> None:
    repository = FakeCreditOpsRepository(view=build_view())
    processor = build_processor(repository, UnavailableGateway())

    result = await processor.process(ops_task(), None, CheckpointRecorder().save)

    assert result.status is WorkerOutcome.FAILED_MANUAL_REVIEW
    assert repository.packages == {}
    assert any(
        e.event_type == "CREDIT_OPS_GATEWAY_UNAVAILABLE" for e in repository.audit_events
    )


@pytest.mark.asyncio
async def test_stale_case_version_is_fenced_as_superseded() -> None:
    # (f) stale-version redelivery is fenced: the view is at case version 2,
    # the task is bound to version 1.
    view = build_view(case_version=2)
    repository = FakeCreditOpsRepository(view=view)
    gateway = FakeGateway(lambda request: valid_payload(view))
    processor = build_processor(repository, gateway)

    result = await processor.process(ops_task(case_version=1), None, CheckpointRecorder().save)

    assert result.status is WorkerOutcome.SUPERSEDED
    assert repository.packages == {}
    assert gateway.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing",
    ["intake", "underwriting", "legal", "risk_review"],
)
async def test_missing_upstream_artifact_fails_closed_before_inference(missing: str) -> None:
    view = build_view(
        with_intake=missing != "intake",
        with_underwriting=missing != "underwriting",
        with_legal=missing != "legal",
        with_risk_review=missing != "risk_review",
    )
    repository = FakeCreditOpsRepository(view=view)
    gateway = FakeGateway(lambda request: {})
    processor = build_processor(repository, gateway)

    result = await processor.process(ops_task(), None, CheckpointRecorder().save)

    assert result.status is WorkerOutcome.FAILED_MANUAL_REVIEW
    assert gateway.requests == []  # never reached the model
    assert repository.packages == {}
    assert any(
        e.event_type == "CREDIT_OPS_UPSTREAM_INCOMPLETE" for e in repository.audit_events
    )


@pytest.mark.asyncio
async def test_redelivery_after_persist_is_idempotent() -> None:
    view = build_view()
    repository = FakeCreditOpsRepository(view=view)
    gateway = FakeGateway(lambda request: valid_payload(view))
    processor = build_processor(repository, gateway)

    first = await processor.process(ops_task(), None, CheckpointRecorder().save)
    assert first.status is WorkerOutcome.SUCCEEDED
    assert repository.persist_calls == 1
    gateway_calls = len(gateway.requests)

    # Redelivery with NO checkpoint (e.g. checkpoint write raced): the
    # durable find_persisted lookup short-circuits before any model call.
    recorder = CheckpointRecorder()
    second = await processor.process(ops_task(), None, recorder.save)

    assert second.status is WorkerOutcome.SUCCEEDED
    assert repository.persist_calls == 1  # no second persist
    assert len(gateway.requests) == gateway_calls  # no second inference
    assert recorder.types() == [CHECKPOINT_PERSISTED]
    assert len(repository.packages) == 1


@pytest.mark.asyncio
async def test_resume_from_memo_drafted_checkpoint_skips_inference() -> None:
    view = build_view()
    repository = FakeCreditOpsRepository(view=view)
    gateway = FakeGateway(lambda request: valid_payload(view))
    processor = build_processor(repository, gateway)
    recorder = CheckpointRecorder()
    await processor.process(ops_task(), None, recorder.save)
    memo_checkpoint = next(
        c for c in recorder.saved if c.checkpoint_type == CHECKPOINT_MEMO_DRAFTED
    )
    # Simulate a crash after the memo checkpoint but before persist: reset
    # durable state and resume from the checkpoint.
    repository.persisted.clear()
    repository.packages.clear()
    repository.handoffs.clear()
    gateway.requests.clear()

    resumed = CheckpointRecorder()
    result = await processor.process(ops_task(), memo_checkpoint, resumed.save)

    assert result.status is WorkerOutcome.SUCCEEDED
    assert gateway.requests == []  # inference never re-ran
    assert len(repository.packages) == 1
    assert resumed.types() == [CHECKPOINT_PERSISTED]


@pytest.mark.asyncio
async def test_resume_from_persisted_checkpoint_is_a_no_op() -> None:
    view = build_view()
    repository = FakeCreditOpsRepository(view=view)
    gateway = FakeGateway(lambda request: valid_payload(view))
    processor = build_processor(repository, gateway)
    recorder = CheckpointRecorder()
    await processor.process(ops_task(), None, recorder.save)
    persisted_checkpoint = recorder.saved[-1]
    assert persisted_checkpoint.checkpoint_type == CHECKPOINT_PERSISTED

    resumed = CheckpointRecorder()
    result = await processor.process(ops_task(), persisted_checkpoint, resumed.save)

    assert result.status is WorkerOutcome.SUCCEEDED
    assert resumed.saved == []
    assert repository.persist_calls == 1


@pytest.mark.asyncio
async def test_invalid_memo_citation_is_rejected_for_bounded_retry() -> None:
    view = build_view()

    def bad_payload(request: ReasonRequest) -> dict[str, Any]:
        payload = valid_payload(view)
        assert view.underwriting is not None
        payload["tom_tat_nhu_cau"]["statements"][0]["citations"] = [
            {
                "kind": "MEMO_FINDING",
                "ref": {
                    "source": "CREDIT_UNDERWRITING",
                    "source_assessment_id": str(view.underwriting.id),
                    "section_path": "business.findings[999]",  # outside the universe
                },
            }
        ]
        return payload

    repository = FakeCreditOpsRepository(view=view)
    processor = build_processor(repository, FakeGateway(bad_payload))

    result = await processor.process(ops_task(), None, CheckpointRecorder().save)

    assert result.status is WorkerOutcome.RETRY_WAIT
    assert repository.packages == {}
    assert any(
        e.event_type == "CREDIT_OPS_OUTPUT_REJECTED" for e in repository.audit_events
    )


@pytest.mark.asyncio
async def test_memo_section_without_citations_is_rejected() -> None:
    # (b) a memo section omitting citations fails validation.
    view = build_view()

    def uncited_payload(request: ReasonRequest) -> dict[str, Any]:
        payload = valid_payload(view)
        payload["dieu_kien_de_xuat"]["statements"][0]["citations"] = []
        return payload

    repository = FakeCreditOpsRepository(view=view)
    processor = build_processor(repository, FakeGateway(uncited_payload))

    result = await processor.process(ops_task(), None, CheckpointRecorder().save)

    assert result.status is WorkerOutcome.RETRY_WAIT
    assert repository.packages == {}


@pytest.mark.asyncio
async def test_llm_cannot_alter_the_deterministic_skeleton() -> None:
    # A payload trying to smuggle actions/requests/checklist edits changes
    # nothing: the assembler reads ONLY the memo narrative sections.
    view = build_view()
    gap = OpenGapRecord(
        gap_id=uuid4(),
        missing_information_vi="Hop dong dau ra nam hien tai (mo phong).",
        blocking_level=GapBlockingLevel.CONDITIONAL,
        status="FORMAL",
    )

    def smuggling_payload(request: ReasonRequest) -> dict[str, Any]:
        payload = valid_payload(view)
        payload["proposed_actions"] = [{"id": str(uuid4()), "execution_status": "EXECUTED"}]
        payload["document_requests"] = []
        payload["package_completeness"] = {"all_required_present": False}
        return payload

    repository = FakeCreditOpsRepository(view=view, open_gaps=(gap,))
    processor = build_processor(repository, FakeGateway(smuggling_payload))

    result = await processor.process(ops_task(), None, CheckpointRecorder().save)

    assert result.status is WorkerOutcome.SUCCEEDED
    package = next(iter(repository.packages.values()))
    # The drafted document request from the real open gap survived; the
    # smuggled EXECUTED action does not exist; every action stays DRAFT.
    assert len(package.document_requests) == 1
    assert package.document_requests[0].originating_gap_id == gap.gap_id
    assert all(a.execution_status.value == "DRAFT" for a in package.proposed_actions)
    assert package.package_completeness.all_required_present is True


def test_response_schema_forbids_non_memo_keys() -> None:
    # Defense in depth for the same property: the closed response schema
    # itself rejects a payload carrying any deterministic-section key.
    from creditops.application.credit_ops.assembler import (
        build_response_schema,
        target_universe_for,
    )

    view = build_view()
    schema = dict(build_response_schema(target_universe_for(view)))
    validator = Draft202012Validator(schema)
    good = valid_payload(view)
    validator.validate(good)  # the untampered payload is well-formed
    for smuggled_key in ("proposed_actions", "document_requests", "package_completeness"):
        bad = dict(good)
        bad[smuggled_key] = []
        assert not validator.is_valid(bad), f"schema accepted smuggled key {smuggled_key}"


@pytest.mark.asyncio
async def test_unknown_case_fails_closed() -> None:
    repository = FakeCreditOpsRepository(view=None)
    gateway = FakeGateway(lambda request: {})
    processor = build_processor(repository, gateway)

    result = await processor.process(ops_task(), None, CheckpointRecorder().save)

    assert result.status is WorkerOutcome.FAILED_MANUAL_REVIEW
    assert gateway.requests == []

