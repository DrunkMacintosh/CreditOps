"""Unit tests for the concrete document-ingestion persistence processor.

All documents, values and provider responses here are synthetic and exist only
to exercise the durable stage machine.  The processor is driven with fake ports
(storage, ingestion persistence, FPT gateway) so no live Postgres, Storage or
model endpoint is required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

import pytest

from creditops.application.ports.document_ingestion import (
    DocumentIngestionContext,
    PersistableCandidate,
    PersistablePassage,
    PersistableRegion,
)
from creditops.application.ports.model_gateway import (
    InferenceNotProvisionedError,
    InferenceResult,
    InferenceUnavailableError,
)
from creditops.application.ports.queue import TaskCheckpoint, TaskRecord
from creditops.application.stages.ingestion_processor import (
    CHECKPOINT_CLASSIFIED,
    CHECKPOINT_EXTRACTED,
    CHECKPOINT_INDEXED,
    CHECKPOINT_PARSED,
    CHECKPOINT_READY,
    CHECKPOINT_SECURITY_VALIDATED,
    DocumentIngestionProcessor,
)
from creditops.application.stages.parse import ParsedDocument, ParsedRegion
from creditops.application.use_cases.run_worker_once import StageResult, WorkerOutcome
from creditops.domain.enums import DocumentStage
from creditops.domain.orchestration import TaskType

CASE = UUID("10000000-0000-0000-0000-000000000001")
DOC = UUID("20000000-0000-0000-0000-000000000001")
TASK = UUID("30000000-0000-0000-0000-000000000001")
PDF_BYTES = b"%PDF-1.7\nsynthetic credit request body\n"
PDF_SHA = sha256(PDF_BYTES).hexdigest()


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class FakeStorage:
    def __init__(self, chunks: Sequence[bytes]) -> None:
        self._chunks = list(chunks)
        self.open_calls: list[tuple[str, str]] = []

    def open_object(self, *, bucket_id: str, object_key: str) -> AsyncIterator[bytes]:
        self.open_calls.append((bucket_id, object_key))
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class FakeParser:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.calls = 0

    def parse(self, document_version_id: UUID, document: Any) -> ParsedDocument:
        self.calls += 1
        if self._fail:
            raise ValueError("synthetic OCR failure: page is unreadable")
        return ParsedDocument(
            document_version_id=document_version_id,
            content_type=document.content_type,
            extraction_method="deterministic-parser-test",
            regions=(
                ParsedRegion(
                    page=1,
                    text="So tien de nghi: 500000000",
                    x=0.0,
                    y=0.0,
                    width=1.0,
                    height=0.1,
                ),
            ),
        )


class FakeGateway:
    def __init__(
        self,
        *,
        embed_unavailable: bool = False,
        embed_not_provisioned: bool = False,
        extract_unavailable: bool = False,
    ) -> None:
        self._embed_unavailable = embed_unavailable
        self._embed_not_provisioned = embed_not_provisioned
        self._extract_unavailable = extract_unavailable
        self.extract_calls = 0
        self.embed_calls = 0

    async def reason(self, request: Any) -> InferenceResult:
        # Text extraction rides the benchmark-passed REASONING route (the KIE
        # capability stays unpinned/fail-closed until its own benchmark).
        self.extract_calls += 1
        if self._extract_unavailable:
            raise InferenceUnavailableError("FPT reasoning endpoint unavailable")
        return _inference(
            capability="reasoning",
            model_id="reasoning-benchmarked-v1",
            payload={
                "candidates": [
                    {
                        "field_key": "requested_amount",
                        "proposed_value": "500000000",
                        "confidence": 0.91,
                        "page": 1,
                        "x": 0.0,
                        "y": 0.0,
                        "width": 0.3,
                        "height": 0.1,
                    }
                ]
            },
        )

    async def embed(self, request: Any) -> InferenceResult:
        self.embed_calls += 1
        if self._embed_not_provisioned:
            raise InferenceNotProvisionedError("FPT capability is not configured: embedding")
        if self._embed_unavailable:
            raise InferenceUnavailableError("FPT embedding endpoint unavailable")
        return _inference(
            capability="embedding",
            model_id="embedding-gated-v1",
            payload=[[0.1, 0.2]],
        )


def _inference(*, capability: str, model_id: str, payload: object) -> InferenceResult:
    return InferenceResult(
        capability=capability,
        provider="FPT",
        case_id=CASE,
        document_version_id=DOC,
        endpoint_id=f"{capability}-endpoint",
        model_id=model_id,
        payload=payload,
        prompt_version="p1",
        schema_version="s1",
        route_version="r1",
        correlation_id="corr",
        started_at=datetime.now(UTC),
        latency_ms=1,
    )


_STAGE_RANK = {
    DocumentStage.REGISTERED: 0,
    DocumentStage.SECURITY_VALIDATED: 1,
    DocumentStage.PARSED: 2,
    DocumentStage.CLASSIFIED: 3,
    DocumentStage.EXTRACTED: 4,
    DocumentStage.INDEXED: 5,
    DocumentStage.READY_FOR_OFFICER_REVIEW: 6,
}


@dataclass
class FakePort:
    stage: DocumentStage = DocumentStage.REGISTERED
    is_stale: bool = False
    missing: bool = False
    regions: list[PersistableRegion] = field(default_factory=list)
    candidates: list[PersistableCandidate] = field(default_factory=list)
    passages: list[PersistablePassage] = field(default_factory=list)
    advances: list[tuple[DocumentStage, DocumentStage]] = field(default_factory=list)

    async def load_document(
        self, *, case_id: UUID, case_version: int, document_version_id: UUID
    ) -> DocumentIngestionContext | None:
        if self.missing:
            return None
        return DocumentIngestionContext(
            case_id=case_id,
            case_version=case_version,
            document_version_id=document_version_id,
            stage=self.stage,
            original_filename="don_de_nghi_cap_tin_dung.pdf",
            declared_content_type="application/pdf",
            detected_content_type="application/pdf",
            storage_bucket="creditops-originals",
            storage_object_key=f"originals/{case_id}/intent",
            byte_size=len(PDF_BYTES),
            content_sha256=PDF_SHA,
            is_stale=self.is_stale,
        )

    def _advance(self, from_stage: DocumentStage, to_stage: DocumentStage) -> None:
        # Idempotent guarded advance: only move forward from the expected stage.
        if _STAGE_RANK[self.stage] <= _STAGE_RANK[from_stage]:
            self.stage = to_stage
        self.advances.append((from_stage, to_stage))

    async def persist_parsed_regions(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        regions: Sequence[PersistableRegion],
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None:
        for region in regions:
            if all(r.page_region_id != region.page_region_id for r in self.regions):
                self.regions.append(region)
        self._advance(from_stage, to_stage)

    async def persist_candidates(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        candidates: Sequence[PersistableCandidate],
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None:
        for candidate in candidates:
            if all(c.candidate_fact_id != candidate.candidate_fact_id for c in self.candidates):
                self.candidates.append(candidate)
        self._advance(from_stage, to_stage)

    async def persist_passages(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        passages: Sequence[PersistablePassage],
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None:
        for passage in passages:
            if all(p.passage_id != passage.passage_id for p in self.passages):
                self.passages.append(passage)
        self._advance(from_stage, to_stage)

    async def advance_stage(
        self,
        *,
        case_id: UUID,
        case_version: int,
        document_version_id: UUID,
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None:
        self._advance(from_stage, to_stage)


class CheckpointRecorder:
    def __init__(self) -> None:
        self.saved: list[tuple[str, Mapping[str, object]]] = []
        self._sequence = 0

    async def __call__(
        self, checkpoint_type: str, checkpoint_data: Mapping[str, object]
    ) -> TaskCheckpoint:
        self._sequence += 1
        self.saved.append((checkpoint_type, dict(checkpoint_data)))
        return TaskCheckpoint(
            task_id=TASK,
            case_id=CASE,
            case_version=1,
            document_version_id=DOC,
            sequence_no=self._sequence,
            checkpoint_type=checkpoint_type,
            checkpoint_schema_version="1",
            checkpoint_data=dict(checkpoint_data),
            created_at=datetime.now(UTC),
        )

    @property
    def types(self) -> list[str]:
        return [entry[0] for entry in self.saved]


def _task() -> TaskRecord:
    return TaskRecord(
        id=TASK,
        case_id=CASE,
        case_version=1,
        document_version_id=DOC,
        status=None,  # type: ignore[arg-type]  # unused by the processor
        attempt_count=0,
        max_attempts=3,
        available_at=datetime.now(UTC),
        lease_token=uuid4(),
        lease_until=datetime.now(UTC),
        input_schema_version="1",
        input_payload={
            "storageBucket": "creditops-originals",
            "storageObjectKey": f"originals/{CASE}/intent",
            "contentSha256": PDF_SHA,
        },
        idempotency_key=f"UPLOAD:{DOC}",
        task_type=TaskType.DOCUMENT_INGESTION,
    )


def _checkpoint(checkpoint_type: str) -> TaskCheckpoint:
    return TaskCheckpoint(
        task_id=TASK,
        case_id=CASE,
        case_version=1,
        document_version_id=DOC,
        sequence_no=1,
        checkpoint_type=checkpoint_type,
        checkpoint_schema_version="1",
        checkpoint_data={},
        created_at=datetime.now(UTC),
    )


def _processor(
    port: FakePort, gateway: FakeGateway, parser: FakeParser
) -> DocumentIngestionProcessor:
    return DocumentIngestionProcessor(
        port=port,
        storage=FakeStorage([PDF_BYTES]),
        gateway=gateway,
        parser=parser,
        expected_embedding_dimension=2,
    )


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_happy_path_runs_every_stage_and_finishes_ready() -> None:
    port = FakePort()
    gateway = FakeGateway()
    processor = _processor(port, gateway, FakeParser())
    recorder = CheckpointRecorder()

    result = await processor.process(_task(), None, recorder)

    assert result == StageResult()
    assert port.stage is DocumentStage.READY_FOR_OFFICER_REVIEW
    assert recorder.types == [
        CHECKPOINT_SECURITY_VALIDATED,
        CHECKPOINT_PARSED,
        CHECKPOINT_CLASSIFIED,
        CHECKPOINT_EXTRACTED,
        CHECKPOINT_INDEXED,
        CHECKPOINT_READY,
    ]
    assert port.advances == [
        (DocumentStage.REGISTERED, DocumentStage.SECURITY_VALIDATED),
        (DocumentStage.SECURITY_VALIDATED, DocumentStage.PARSED),
        (DocumentStage.PARSED, DocumentStage.CLASSIFIED),
        (DocumentStage.CLASSIFIED, DocumentStage.EXTRACTED),
        (DocumentStage.EXTRACTED, DocumentStage.INDEXED),
        (DocumentStage.INDEXED, DocumentStage.READY_FOR_OFFICER_REVIEW),
    ]
    # Extraction candidates persist grounded to a persisted evidence region.
    assert len(port.candidates) == 1
    region_ids = {region.page_region_id for region in port.regions}
    assert port.candidates[0].page_region_id in region_ids
    assert port.candidates[0].field_key == "requested_amount"
    # Retrieval passages persist with a real, validated embedding.
    assert len(port.passages) == 1
    assert port.passages[0].embedding == (0.1, 0.2)
    assert port.passages[0].embedding_model == "embedding-gated-v1"
    assert gateway.extract_calls == 1
    assert gateway.embed_calls == 1


@pytest.mark.asyncio
async def test_resume_from_checkpoint_skips_completed_stages() -> None:
    port = FakePort(stage=DocumentStage.CLASSIFIED)
    gateway = FakeGateway()
    processor = _processor(port, gateway, FakeParser())
    recorder = CheckpointRecorder()

    result = await processor.process(_task(), _checkpoint(CHECKPOINT_CLASSIFIED), recorder)

    assert result == StageResult()
    assert port.stage is DocumentStage.READY_FOR_OFFICER_REVIEW
    # Parse/classify already done: their persistence + advances are skipped.
    assert port.regions == []
    assert recorder.types == [
        CHECKPOINT_EXTRACTED,
        CHECKPOINT_INDEXED,
        CHECKPOINT_READY,
    ]
    assert port.advances == [
        (DocumentStage.CLASSIFIED, DocumentStage.EXTRACTED),
        (DocumentStage.EXTRACTED, DocumentStage.INDEXED),
        (DocumentStage.INDEXED, DocumentStage.READY_FOR_OFFICER_REVIEW),
    ]
    assert len(port.candidates) == 1
    assert len(port.passages) == 1


@pytest.mark.asyncio
async def test_redelivery_after_completion_is_idempotent() -> None:
    port = FakePort()
    gateway = FakeGateway()
    processor = _processor(port, gateway, FakeParser())

    first = await processor.process(_task(), None, CheckpointRecorder())
    assert first == StageResult()
    assert port.stage is DocumentStage.READY_FOR_OFFICER_REVIEW

    # A duplicate delivery observes an already-READY version and repeats no work.
    recorder = CheckpointRecorder()
    second = await processor.process(_task(), _checkpoint(CHECKPOINT_READY), recorder)

    assert second == StageResult()
    assert len(port.regions) == 1
    assert len(port.candidates) == 1
    assert len(port.passages) == 1
    assert recorder.types == []
    assert gateway.extract_calls == 1
    assert gateway.embed_calls == 1


@pytest.mark.asyncio
async def test_unreadable_parse_fails_to_manual_review() -> None:
    port = FakePort()
    gateway = FakeGateway()
    processor = _processor(port, gateway, FakeParser(fail=True))

    result = await processor.process(_task(), None, CheckpointRecorder())

    assert result.status is WorkerOutcome.FAILED_MANUAL_REVIEW
    assert port.stage is not DocumentStage.READY_FOR_OFFICER_REVIEW
    assert port.regions == []
    assert port.candidates == []
    assert port.passages == []
    assert gateway.extract_calls == 0


@pytest.mark.asyncio
async def test_embedding_unavailable_retries_without_faking_a_vector() -> None:
    port = FakePort()
    gateway = FakeGateway(embed_unavailable=True)
    processor = _processor(port, gateway, FakeParser())

    result = await processor.process(_task(), None, CheckpointRecorder())

    assert result.status is WorkerOutcome.RETRY_WAIT
    # Extraction completed and persisted; indexing did not fabricate a vector.
    assert len(port.candidates) == 1
    assert port.passages == []
    # Partial processing must never reach the officer-review handoff.
    assert port.stage is not DocumentStage.READY_FOR_OFFICER_REVIEW
    assert port.stage is DocumentStage.EXTRACTED


@pytest.mark.asyncio
async def test_embedding_not_provisioned_advances_ready_with_audited_skip() -> None:
    # An UNPROVISIONED embedding route is permanent for this deployment:
    # retrying can never succeed, so the stage advances with ZERO persisted
    # passages (nothing fabricated) and records the degradation in the
    # INDEXED checkpoint. Extraction evidence (the officer-review core) is
    # fully persisted and the document reaches READY.
    port = FakePort()
    gateway = FakeGateway(embed_not_provisioned=True)
    recorder = CheckpointRecorder()
    processor = _processor(port, gateway, FakeParser())

    result = await processor.process(_task(), None, recorder)

    assert result.status is WorkerOutcome.SUCCEEDED
    assert len(port.candidates) == 1
    assert port.passages == []
    assert port.stage is DocumentStage.READY_FOR_OFFICER_REVIEW
    indexed = next(
        data for name, data in recorder.saved if name == "INDEXED"
    )
    assert indexed["passageCount"] == 0
    assert "not configured" in str(indexed["embeddingSkipped"])


@pytest.mark.asyncio
async def test_extraction_unavailable_retries_and_never_reaches_ready() -> None:
    port = FakePort()
    gateway = FakeGateway(extract_unavailable=True)
    processor = _processor(port, gateway, FakeParser())

    result = await processor.process(_task(), None, CheckpointRecorder())

    assert result.status is WorkerOutcome.RETRY_WAIT
    assert port.candidates == []
    assert port.passages == []
    assert port.stage is DocumentStage.CLASSIFIED
    assert gateway.embed_calls == 0


@pytest.mark.asyncio
async def test_stale_document_version_is_superseded() -> None:
    port = FakePort(is_stale=True)
    gateway = FakeGateway()
    processor = _processor(port, gateway, FakeParser())

    result = await processor.process(_task(), None, CheckpointRecorder())

    assert result.status is WorkerOutcome.SUPERSEDED
    assert port.regions == []
    assert port.stage is not DocumentStage.READY_FOR_OFFICER_REVIEW


@pytest.mark.asyncio
async def test_missing_document_version_fails_manual_review() -> None:
    port = FakePort(missing=True)
    gateway = FakeGateway()
    processor = _processor(port, gateway, FakeParser())

    result = await processor.process(_task(), None, CheckpointRecorder())

    assert result.status is WorkerOutcome.FAILED_MANUAL_REVIEW
