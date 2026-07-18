"""Reviewer use-case and worker-processor tests for LEGAL_COMPLIANCE_COLLATERAL.

All customer data, policies, documents, and banking-system responses in this
project are synthetic and created solely for demonstration.  The fixture case
belongs to the invented SME "Cong ty TNHH Che Bien Thuy San Song Hau Demo";
every figure is fabricated.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from jsonschema import Draft202012Validator

from creditops.application.governance import goal_contract_for
from creditops.application.legal.corpus import PolicyCorpus, load_corpus
from creditops.application.legal.processor import (
    CHECKPOINT_INFERENCE,
    CHECKPOINT_PERSISTED,
    LegalComplianceProcessor,
)
from creditops.application.legal.reviewer import LegalPrompt, RunLegalInference
from creditops.application.orchestration.processors import ProcessorRegistry
from creditops.application.ports.governance import PersistedManifest
from creditops.application.ports.legal import (
    ControlledCheckResult,
    LegalEvidenceView,
    LegalRepository,
    PersistedLegalOutput,
    ProvisionalGapRecord,
)
from creditops.application.ports.model_gateway import (
    InferenceResult,
    InferenceUnavailableError,
    ReasonRequest,
)
from creditops.application.ports.orchestration import OrchestrationAuditEvent
from creditops.application.ports.queue import TaskCheckpoint, TaskRecord
from creditops.domain.enums import TaskStatus
from creditops.domain.goal_contracts import ContextManifest, compute_context_hash
from creditops.domain.legal import LegalComplianceAssessment
from creditops.domain.orchestration import TaskType
from creditops.infrastructure.mock.legal_checks import MockControlledChecksGateway

NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
CASE_ID = UUID("10000000-0000-0000-0000-00000000bb01")
TASK_ID = UUID("30000000-0000-0000-0000-00000000bb01")
ENTITY_NAME = "Cong ty TNHH Che Bien Thuy San Song Hau Demo"

_CORPUS = load_corpus()


def make_fact(field_key: str, value: Any) -> Any:
    from creditops.application.ports.legal import EvidenceFact

    return EvidenceFact(
        confirmed_fact_id=uuid4(), field_key=field_key, value=value, document_version_id=uuid4()
    )


def make_view(*, case_version: int = 1, with_collateral: bool = True) -> LegalEvidenceView:
    facts = [
        make_fact("legal.entity.registered_name_vi", ENTITY_NAME),
        make_fact("collateral.asset.owner_name_vi", ENTITY_NAME),
    ]
    if with_collateral:
        facts += [
            make_fact("collateral.documents.giay_chung_nhan_qsdd.status", "PRESENT"),
            make_fact("collateral.documents.giay_chung_nhan_qsdd.expiry_date", "2099-01-01"),
            make_fact("collateral.documents.hop_dong_the_chap.status", "PRESENT"),
            make_fact("collateral.documents.bien_ban_dinh_gia.status", "PRESENT"),
            make_fact("collateral.documents.bien_ban_dinh_gia.expiry_date", "2099-01-01"),
        ]
    return LegalEvidenceView(
        case_id=CASE_ID, case_version=case_version, built_at=NOW, confirmed_facts=tuple(facts)
    )


def legal_task(case_version: int = 1) -> TaskRecord:
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
        idempotency_key=f"LEGAL:{CASE_ID}:1",
        task_type=TaskType.LEGAL_COMPLIANCE_COLLATERAL,
    )


def _finding(text: str, cite: Mapping[str, Any]) -> dict[str, Any]:
    return {"statement_vi": text, "citations": [cite], "confidence": "MEDIUM"}


def valid_payload(request: ReasonRequest) -> dict[str, Any]:
    import json

    ctx = json.loads(request.content)
    fact_cite = {
        "kind": "CONFIRMED_FACT",
        "confirmed_fact_id": ctx["confirmedFacts"][0]["confirmedFactId"],
    }
    hits = ctx["policyCorpus"]["hits"]
    policy_review: list[dict[str, Any]] = []
    if hits:
        hit = hits[0]
        policy_review.append(
            {
                "possible_issue_vi": "Co the ap dung dieu khoan tai san bao dam.",
                "citations": [
                    {
                        "kind": "POLICY_CITATION",
                        "corpus_id": hit["corpusId"],
                        "corpus_version": hit["corpusVersion"],
                        "document_id": hit["documentId"],
                        "clause_id": hit["clauseId"],
                        "quoted_text_vi": hit["quotedTextVi"],
                    }
                ],
                "confidence": "MEDIUM",
            }
        )
    checks = ctx["controlledCheckResults"]
    interpretations = [
        {
            "invocation_id": result["invocationId"],
            "statement_vi": "Khong phat hien canh bao trong ket qua mo phong.",
            "confidence": "HIGH",
        }
        for result in checks
    ]
    return {
        "legal_entity_review": {
            "findings": [_finding("Doanh nghiep dang hoat dong hop le.", fact_cite)]
        },
        "authority_signatory_review": {
            "findings": [_finding("Nguoi dai dien hop le theo dang ky.", fact_cite)]
        },
        "ownership_consistency": {
            "findings": [_finding("So huu nhat quan giua cac du kien.", fact_cite)]
        },
        "policy_review": policy_review,
        "controlled_check_interpretations": interpretations,
        "collateral_review": {
            "ownership_evidence_findings": [
                _finding("Ho so so huu tai san phu hop voi dang ky.", fact_cite)
            ]
        },
        "exceptions": [],
        "assumptions": [],
        "evidence_gaps": [],
    }


class FakeGateway:
    def __init__(
        self,
        payload_factory: Callable[[ReasonRequest], Mapping[str, Any]] = valid_payload,
        *,
        validate_against_schema: bool = True,
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
            prompt_version="legal-prompt-v1",
            schema_version="legal-assessment-v1",
            route_version="fpt-route-v1",
            correlation_id=request.correlation_id,
            started_at=NOW,
            latency_ms=1,
        )

    async def extract_kie(self, request: object) -> InferenceResult:
        raise AssertionError("reviewer must not call extraction")

    async def extract_table(self, request: object) -> InferenceResult:
        raise AssertionError("reviewer must not call extraction")

    async def inspect_vision(self, request: object) -> InferenceResult:
        raise AssertionError("reviewer must not call vision")

    async def embed(self, request: object) -> InferenceResult:
        raise AssertionError("reviewer must not call embedding")


class UnavailableGateway(FakeGateway):
    async def reason(self, request: ReasonRequest) -> InferenceResult:
        raise InferenceUnavailableError("endpoint disabled")


class AssertNoCallGateway(FakeGateway):
    async def reason(self, request: ReasonRequest) -> InferenceResult:
        raise AssertionError("resume from CHECKPOINT_INFERENCE must not call the model")


@dataclass
class StoredHandoff:
    handoff_id: UUID
    case_id: UUID
    case_version: int
    state: str
    source_task_id: UUID


class FakeLegalRepository:
    def __init__(self, view: LegalEvidenceView | None) -> None:
        self.view = view
        self.persisted: dict[tuple[UUID, int, UUID], PersistedLegalOutput] = {}
        self.assessments: dict[UUID, LegalComplianceAssessment] = {}
        self.gap_rows: list[tuple[UUID, ProvisionalGapRecord]] = []
        self.controlled_check_rows: list[ControlledCheckResult] = []
        self.handoffs: dict[UUID, StoredHandoff] = {}
        self.audit_events: list[OrchestrationAuditEvent] = []
        self.persist_calls = 0

    async def load_evidence_view(self, case_id: UUID) -> LegalEvidenceView | None:
        if self.view is not None and self.view.case_id == case_id:
            return self.view
        return None

    async def load_latest_assessment(self, case_id: UUID) -> Any:
        return None

    async def find_persisted(
        self, *, case_id: UUID, case_version: int, task_id: UUID
    ) -> PersistedLegalOutput | None:
        found = self.persisted.get((case_id, case_version, task_id))
        if found is None:
            return None
        return replace(found, created=False)

    async def persist_assessment(
        self,
        *,
        assessment: LegalComplianceAssessment,
        handoff_id: UUID,
        handoff_state: str,
        gaps: tuple[ProvisionalGapRecord, ...],
        controlled_checks: tuple[ControlledCheckResult, ...],
    ) -> PersistedLegalOutput:
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
            self.gap_rows.append((gap_id, gap))
        self.controlled_check_rows.extend(controlled_checks)
        self.handoffs[handoff_id] = StoredHandoff(
            handoff_id=handoff_id,
            case_id=assessment.provenance.case_id,
            case_version=assessment.provenance.case_version,
            state=handoff_state,
            source_task_id=assessment.provenance.task_id,
        )
        self.assessments[assessment.id] = assessment
        output = PersistedLegalOutput(
            assessment_id=assessment.id,
            handoff_id=handoff_id,
            gap_ids=tuple(gap_ids),
            controlled_check_record_ids=tuple(r.invocation_id for r in controlled_checks),
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


class FakeGovernanceRepository:
    """Dedupes context manifests per (task, contextHash), like the durable
    partial unique index; records every persist call."""

    def __init__(self) -> None:
        self.persist_calls: list[ContextManifest] = []
        self.manifests: dict[tuple[UUID | None, str], UUID] = {}

    async def persist_manifest(self, manifest: ContextManifest) -> PersistedManifest:
        self.persist_calls.append(manifest)
        context_hash = compute_context_hash(manifest)
        key = (manifest.task_id, context_hash)
        existing = self.manifests.get(key)
        if existing is not None:
            return PersistedManifest(existing, context_hash, created=False)
        self.manifests[key] = manifest.id
        return PersistedManifest(manifest.id, context_hash, created=True)

    async def ensure_goal_contract_rows(self, contracts: object) -> None:
        del contracts


def build_processor(
    repository: LegalRepository,
    gateway: FakeGateway | None,
    *,
    governance: FakeGovernanceRepository | None = None,
    corpus_loader: Callable[[], PolicyCorpus | None] = lambda: _CORPUS,
) -> LegalComplianceProcessor:
    inference = (
        RunLegalInference(gateway, prompt=LegalPrompt("prompt-vi-test"))
        if gateway is not None
        else None
    )
    return LegalComplianceProcessor(
        repository,
        inference,
        governance=governance,
        controlled_checks_gateway=MockControlledChecksGateway(clock=lambda: NOW),
        clock=lambda: NOW,
        corpus_loader=corpus_loader,
    )


@pytest.mark.asyncio
async def test_happy_path_persists_cited_assessment_and_risk_review_handoff() -> None:
    repository = FakeLegalRepository(make_view())
    gateway = FakeGateway()
    processor = build_processor(repository, gateway)
    checkpoints = CheckpointRecorder()

    result = await processor.process(legal_task(), None, checkpoints.save)

    assert result.status.value == "SUCCEEDED"
    assert repository.persist_calls == 1
    (assessment,) = repository.assessments.values()
    assert assessment.provenance.agent_role == "LEGAL_COMPLIANCE_COLLATERAL"
    assert len(assessment.policy_review) == 1
    assert len(assessment.controlled_check_interpretations) == 3
    (handoff,) = repository.handoffs.values()
    assert handoff.state == "READY_FOR_RISK_REVIEW"
    assert handoff.case_version == assessment.provenance.case_version == 1
    assert CHECKPOINT_PERSISTED in checkpoints.types()


@pytest.mark.asyncio
async def test_no_corpus_configured_abstains_and_records_gap_not_conclusion() -> None:
    repository = FakeLegalRepository(make_view())
    gateway = FakeGateway()
    processor = build_processor(repository, gateway, corpus_loader=lambda: None)

    result = await processor.process(legal_task(), None, CheckpointRecorder().save)

    assert result.status.value == "SUCCEEDED"
    (assessment,) = repository.assessments.values()
    assert assessment.policy_review == ()
    assert assessment.policy_hits == ()
    assert assessment.policy_corpus_ref is None
    assert any(
        "kho chính sách" in gap.missing_information_vi for gap in assessment.evidence_gaps
    )


@pytest.mark.asyncio
async def test_fail_closed_without_configured_gateway() -> None:
    repository = FakeLegalRepository(make_view())
    processor = build_processor(repository, None)

    result = await processor.process(legal_task(), None, CheckpointRecorder().save)

    assert result.status.value == "FAILED_MANUAL_REVIEW"
    assert repository.persist_calls == 0
    assert any(
        event.event_type == "LEGAL_GATEWAY_UNAVAILABLE" for event in repository.audit_events
    )


@pytest.mark.asyncio
async def test_fail_closed_when_endpoint_unavailable() -> None:
    repository = FakeLegalRepository(make_view())
    processor = build_processor(repository, UnavailableGateway())

    result = await processor.process(legal_task(), None, CheckpointRecorder().save)

    assert result.status.value == "FAILED_MANUAL_REVIEW"
    assert repository.persist_calls == 0


@pytest.mark.asyncio
async def test_stale_case_version_is_superseded() -> None:
    repository = FakeLegalRepository(make_view(case_version=2))
    processor = build_processor(repository, FakeGateway())

    result = await processor.process(legal_task(case_version=1), None, CheckpointRecorder().save)

    assert result.status.value == "SUPERSEDED"
    assert repository.persist_calls == 0


@pytest.mark.asyncio
async def test_empty_evidence_view_fails_closed() -> None:
    empty_view = LegalEvidenceView(case_id=CASE_ID, case_version=1, built_at=NOW)
    repository = FakeLegalRepository(empty_view)
    processor = build_processor(repository, FakeGateway())

    result = await processor.process(legal_task(), None, CheckpointRecorder().save)

    assert result.status.value == "FAILED_MANUAL_REVIEW"
    assert repository.persist_calls == 0


@pytest.mark.asyncio
async def test_resume_from_inference_checkpoint_skips_model_call() -> None:
    repository = FakeLegalRepository(make_view())
    processor_a = build_processor(repository, FakeGateway())
    checkpoints = CheckpointRecorder()
    await processor_a.process(legal_task(), None, checkpoints.save)

    inference_checkpoint = next(
        c for c in checkpoints.saved if c.checkpoint_type == CHECKPOINT_INFERENCE
    )
    fresh_repository = FakeLegalRepository(make_view())
    processor_b = build_processor(fresh_repository, AssertNoCallGateway())

    result = await processor_b.process(
        legal_task(), inference_checkpoint, CheckpointRecorder().save
    )

    assert result.status.value == "SUCCEEDED"
    assert fresh_repository.persist_calls == 1
    # Controlled-check records round-trip from the assessment's own record.
    (assessment,) = fresh_repository.assessments.values()
    assert len(fresh_repository.controlled_check_rows) == len(
        assessment.controlled_check_results
    )


@pytest.mark.asyncio
async def test_redelivery_after_terminal_checkpoint_is_noop() -> None:
    repository = FakeLegalRepository(make_view())
    processor = build_processor(repository, FakeGateway())
    checkpoints = CheckpointRecorder()
    await processor.process(legal_task(), None, checkpoints.save)
    persisted_checkpoint = next(
        c for c in checkpoints.saved if c.checkpoint_type == CHECKPOINT_PERSISTED
    )

    result = await processor.process(legal_task(), persisted_checkpoint, CheckpointRecorder().save)

    assert result.status.value == "SUCCEEDED"
    assert repository.persist_calls == 1  # no second persist


@pytest.mark.asyncio
async def test_redelivery_without_checkpoint_finds_durable_output() -> None:
    repository = FakeLegalRepository(make_view())
    processor = build_processor(repository, FakeGateway())
    await processor.process(legal_task(), None, CheckpointRecorder().save)
    assert repository.persist_calls == 1

    redelivered_checkpoints = CheckpointRecorder()
    result = await processor.process(legal_task(), None, redelivered_checkpoints.save)

    assert result.status.value == "SUCCEEDED"
    assert repository.persist_calls == 1
    assert CHECKPOINT_PERSISTED in redelivered_checkpoints.types()


@pytest.mark.asyncio
async def test_unknown_citation_is_rejected_not_persisted() -> None:
    def bad_payload(request: ReasonRequest) -> dict[str, Any]:
        payload = valid_payload(request)
        payload["legal_entity_review"]["findings"][0]["citations"] = [
            {"kind": "CONFIRMED_FACT", "confirmed_fact_id": str(uuid4())}
        ]
        return payload

    repository = FakeLegalRepository(make_view())
    gateway = FakeGateway(bad_payload, validate_against_schema=False)
    processor = build_processor(repository, gateway)

    result = await processor.process(legal_task(), None, CheckpointRecorder().save)

    assert result.status.value == "RETRY_WAIT"
    assert repository.persist_calls == 0


@pytest.mark.asyncio
async def test_fabricated_controlled_check_invocation_id_is_rejected() -> None:
    def bad_payload(request: ReasonRequest) -> dict[str, Any]:
        payload = valid_payload(request)
        payload["controlled_check_interpretations"] = [
            {
                "invocation_id": str(uuid4()),
                "statement_vi": "Ket qua bia.",
                "confidence": "LOW",
            }
        ]
        return payload

    repository = FakeLegalRepository(make_view())
    gateway = FakeGateway(bad_payload, validate_against_schema=False)
    processor = build_processor(repository, gateway)

    result = await processor.process(legal_task(), None, CheckpointRecorder().save)

    assert result.status.value == "RETRY_WAIT"
    assert repository.persist_calls == 0


@pytest.mark.asyncio
async def test_context_manifest_persisted_before_inference_from_registry_contract() -> None:
    view = make_view()
    governance = FakeGovernanceRepository()
    repository = FakeLegalRepository(view)
    processor = build_processor(repository, FakeGateway(), governance=governance)

    result = await processor.process(legal_task(), None, CheckpointRecorder().save)

    assert result.status.value == "SUCCEEDED"
    assert len(governance.persist_calls) == 1
    (manifest,) = governance.persist_calls
    contract = goal_contract_for(TaskType.LEGAL_COMPLIANCE_COLLATERAL)
    assert manifest.goal_contract_id == contract.id
    assert set(manifest.authoritative_fact_refs) == {
        fact.confirmed_fact_id for fact in view.confirmed_facts
    }
    # The controlled-check invocations the reviewer was fed are recorded as
    # tool-result refs (deterministic mock KYC/AML/related-party).
    assert manifest.tool_result_refs
    assert processor._governance_profile.goal_contract == contract
    assert any(
        event.event_type == "LEGAL_CONTEXT_MANIFEST_PERSISTED"
        for event in repository.audit_events
    )


@pytest.mark.asyncio
async def test_manifest_persisted_even_when_inference_unavailable() -> None:
    governance = FakeGovernanceRepository()
    repository = FakeLegalRepository(make_view())
    processor = build_processor(repository, UnavailableGateway(), governance=governance)

    result = await processor.process(legal_task(), None, CheckpointRecorder().save)

    assert result.status.value == "FAILED_MANUAL_REVIEW"
    assert len(governance.manifests) == 1
    assert repository.assessments == {}


@pytest.mark.asyncio
async def test_no_manifest_persisted_without_a_gateway() -> None:
    governance = FakeGovernanceRepository()
    processor = build_processor(
        FakeLegalRepository(make_view()), None, governance=governance
    )

    result = await processor.process(legal_task(), None, CheckpointRecorder().save)

    assert result.status.value == "FAILED_MANUAL_REVIEW"
    assert governance.persist_calls == []


@pytest.mark.asyncio
async def test_resume_from_inference_checkpoint_persists_no_new_manifest() -> None:
    view = make_view()
    governance = FakeGovernanceRepository()
    processor = build_processor(FakeLegalRepository(view), FakeGateway(), governance=governance)
    recorder = CheckpointRecorder()
    await processor.process(legal_task(), None, recorder.save)
    inference_checkpoint = next(
        c for c in recorder.saved if c.checkpoint_type == CHECKPOINT_INFERENCE
    )
    assert len(governance.persist_calls) == 1

    resumed_governance = FakeGovernanceRepository()
    resumed = build_processor(
        FakeLegalRepository(view), AssertNoCallGateway(), governance=resumed_governance
    )
    await resumed.process(legal_task(), inference_checkpoint, CheckpointRecorder().save)

    assert resumed_governance.persist_calls == []


def test_response_schema_is_closed_and_decision_free() -> None:
    from creditops.application.legal.reviewer import build_response_schema

    schema = build_response_schema(
        fact_ids=("a",), document_version_ids=(), policy_hits=(), invocation_ids=()
    )
    assert schema["additionalProperties"] is False
    assert schema["properties"]["policy_review"] == {"type": "array", "maxItems": 0}
    schema_json = str(schema)
    for forbidden in ("waiver", "approve", "collateral_value", "wrongdoing"):
        assert forbidden not in schema_json


def test_legal_processor_is_registered_by_task_type() -> None:
    repository = FakeLegalRepository(make_view())
    processor = build_processor(repository, FakeGateway())
    fallback = build_processor(repository, FakeGateway())
    registry = ProcessorRegistry(
        {TaskType.LEGAL_COMPLIANCE_COLLATERAL: processor}, fallback=fallback
    )
    assert registry.processor_for(TaskType.LEGAL_COMPLIANCE_COLLATERAL) is processor


@pytest.mark.asyncio
async def test_missing_controlled_check_subject_creates_blocking_gaps() -> None:
    repository = FakeLegalRepository(make_view())
    repository.view = LegalEvidenceView(
        case_id=CASE_ID,
        case_version=1,
        built_at=NOW,
        confirmed_facts=(make_fact("some.other.field", "x"),),
    )
    gateway = FakeGateway()
    processor = build_processor(repository, gateway)

    result = await processor.process(legal_task(), None, CheckpointRecorder().save)

    assert result.status.value == "SUCCEEDED"
    (assessment,) = repository.assessments.values()
    assert assessment.controlled_check_results == ()
    gap_texts = [gap.missing_information_vi for gap in assessment.evidence_gaps]
    assert any("KYC" in text for text in gap_texts)
    assert any("AML_WATCHLIST" in text for text in gap_texts)
    assert any("RELATED_PARTY" in text for text in gap_texts)
