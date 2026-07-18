"""Concrete document-ingestion persistence processor (P0 #2).

This is the durable ``TaskProcessor`` for ``DOCUMENT_INGESTION`` tasks.  It
loads the immutable original from private Storage, runs the pure stage functions
(security -> parse -> classify -> extract -> index) in order, persists each
stage's output through :class:`DocumentIngestionPort`, checkpoints after each
stage, and advances ``document_versions.stage`` per ``domain/transitions.py``
until the version reaches ``READY_FOR_OFFICER_REVIEW``.

Non-negotiable invariants (design sections 14.4 and 3.4):

- **Untrusted document text.**  Parsed text, extracted values and passages come
  from the customer's file.  They are treated strictly as data: never used for
  control flow, never interpolated into SQL (the adapter binds parameters), and
  never fed to a model as instructions (the FPT requests separate ``content``
  from ``response_schema``/``system_context``).
- **Model output is candidate only.**  Extraction produces ``candidate_facts``
  with an addressable evidence region; nothing here confirms a fact or clears a
  gate.  Human confirmation is a different, authority-bearing path.
- **No fabricated inference.**  When the FPT gateway raises
  :class:`InferenceUnavailableError`, the stage returns ``RETRY_WAIT`` and
  persists nothing — an embedding or extraction is never invented.
- **Partial processing never finishes.**  ``READY_FOR_OFFICER_REVIEW`` is
  reached only after every prior stage has durably persisted and advanced; any
  early return (retry, manual review, superseded) leaves the version short of
  READY.
- **Resumable and idempotent.**  A redelivered task resumes from the furthest of
  the persisted stage and the latest checkpoint, re-running only the stages that
  had not durably completed.  Because the persist writes are idempotent and each
  is fused with its stage advance, a committed row implies a committed advance,
  so redelivery repeats no external effect and creates no duplicate row.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import ValidationError

from creditops.application.ports.document_ingestion import (
    DocumentIngestionContext,
    DocumentIngestionError,
    DocumentIngestionPort,
    PersistableCandidate,
    PersistablePassage,
    PersistableRegion,
)
from creditops.application.ports.model_gateway import (
    EmbeddingRequest,
    InferenceError,
    InferenceGateway,
    InferenceResult,
    InferenceUnavailableError,
    KIERequest,
    TableRequest,
    VisionRequest,
)
from creditops.application.ports.queue import TaskCheckpoint, TaskRecord
from creditops.application.ports.storage import (
    StorageError,
    StorageObjectMismatch,
    StorageObjectNotFound,
    StoragePort,
)
from creditops.application.stages.classify import Classification, classify_document
from creditops.application.stages.extract import (
    ExtractionCandidate,
    extraction_schema,
    validate_candidates,
)
from creditops.application.stages.index import validate_embedding
from creditops.application.stages.parse import (
    DocumentParser,
    ParsedDocument,
    ParsedRegion,
    parse_document,
)
from creditops.application.stages.security import SecureDocument, validate_document_bytes
from creditops.application.use_cases.run_worker_once import (
    CheckpointCallback,
    StageResult,
    WorkerOutcome,
)
from creditops.domain.enums import DocumentStage
from creditops.domain.transitions import advance_document

CHECKPOINT_SECURITY_VALIDATED = "SECURITY_VALIDATED"
CHECKPOINT_PARSED = "PARSED"
CHECKPOINT_CLASSIFIED = "CLASSIFIED"
CHECKPOINT_EXTRACTED = "EXTRACTED"
CHECKPOINT_INDEXED = "INDEXED"
CHECKPOINT_READY = "READY_FOR_OFFICER_REVIEW"

# Deterministic id namespace so re-persisting a region/candidate/passage yields
# the identical primary key and ``on conflict (id) do nothing`` is a no-op.
_ID_NAMESPACE = uuid5(NAMESPACE_URL, "creditops:document-ingestion:v1")

_MAX_MODEL_INPUT_CHARS = 200_000
_MAX_VISION_INPUT_BYTES = 15 * 1024 * 1024

_STAGE_RANK: Mapping[DocumentStage, int] = {
    DocumentStage.REGISTERED: 0,
    DocumentStage.SECURITY_VALIDATED: 1,
    DocumentStage.PARSED: 2,
    DocumentStage.CLASSIFIED: 3,
    DocumentStage.EXTRACTED: 4,
    DocumentStage.INDEXED: 5,
    DocumentStage.READY_FOR_OFFICER_REVIEW: 6,
}


def _rank(stage: DocumentStage) -> int:
    return _STAGE_RANK[stage]


def _checkpoint_rank(checkpoint: TaskCheckpoint | None) -> int:
    if checkpoint is None:
        return -1
    try:
        return _rank(DocumentStage(checkpoint.checkpoint_type))
    except ValueError:
        return -1


def _region_id(document_version_id: UUID, region: ParsedRegion) -> UUID:
    key = (
        f"page-region:{document_version_id}:{region.page}:"
        f"{region.x!r}:{region.y!r}:{region.width!r}:{region.height!r}"
    )
    return uuid5(_ID_NAMESPACE, key)


def _candidate_id(document_version_id: UUID, page_region_id: UUID, field_key: str) -> UUID:
    return uuid5(
        _ID_NAMESPACE, f"candidate-fact:{document_version_id}:{page_region_id}:{field_key}"
    )


def _passage_id(document_version_id: UUID, page_region_id: UUID) -> UUID:
    return uuid5(_ID_NAMESPACE, f"retrieval-passage:{document_version_id}:{page_region_id}")


def _overlaps(candidate: ExtractionCandidate, region: ParsedRegion) -> bool:
    return bool(
        candidate.page == region.page
        and candidate.x < region.x + region.width
        and candidate.x + candidate.width > region.x
        and candidate.y < region.y + region.height
        and candidate.y + candidate.height > region.y
    )


class DocumentIngestionProcessor:
    """Resumable, idempotent ``DOCUMENT_INGESTION`` task processor."""

    def __init__(
        self,
        *,
        port: DocumentIngestionPort,
        storage: StoragePort,
        gateway: InferenceGateway,
        parser: DocumentParser | None = None,
        clock: Callable[[], datetime] | None = None,
        expected_embedding_dimension: int | None = None,
    ) -> None:
        self._port = port
        self._storage = storage
        self._gateway = gateway
        self._parser = parser
        self._clock = clock or (lambda: datetime.now(UTC))
        self._expected_embedding_dimension = expected_embedding_dimension

    async def process(
        self,
        task: TaskRecord,
        checkpoint: TaskCheckpoint | None,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult:
        document_version_id = task.document_version_id
        if document_version_id is None:
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "document ingestion task carries no document version id",
            )

        context = await self._port.load_document(
            case_id=task.case_id,
            case_version=task.case_version,
            document_version_id=document_version_id,
        )
        if context is None:
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "document version is not present for this case scope",
            )
        if context.is_stale:
            return StageResult(
                WorkerOutcome.SUPERSEDED,
                "document version was superseded before ingestion completed",
            )

        resume_rank = max(_rank(context.stage), _checkpoint_rank(checkpoint))
        if resume_rank >= _rank(DocumentStage.READY_FOR_OFFICER_REVIEW):
            # Already durably READY: a duplicate delivery repeats no effect.
            return StageResult()

        correlation_id = f"document-ingestion:{task.id}"

        if resume_rank < _rank(DocumentStage.INDEXED):
            outcome = await self._run_content_stages(
                task=task,
                document_version_id=document_version_id,
                context=context,
                resume_rank=resume_rank,
                correlation_id=correlation_id,
                save_checkpoint=save_checkpoint,
            )
            if outcome is not None:
                return outcome

        await self._advance(
            task,
            document_version_id,
            DocumentStage.INDEXED,
            DocumentStage.READY_FOR_OFFICER_REVIEW,
        )
        await save_checkpoint(
            CHECKPOINT_READY, {"documentVersionId": str(document_version_id)}
        )
        return StageResult()

    async def _run_content_stages(
        self,
        *,
        task: TaskRecord,
        document_version_id: UUID,
        context: DocumentIngestionContext,
        resume_rank: int,
        correlation_id: str,
        save_checkpoint: CheckpointCallback,
    ) -> StageResult | None:
        """Run security..index; return a terminal ``StageResult`` or ``None``.

        ``None`` means every content stage up to INDEXED is durably complete and
        the caller may perform the final READY advance.
        """

        # -- load immutable original + security validation -------------------
        try:
            data = await self._load_original(context)
        except (StorageObjectNotFound, StorageObjectMismatch) as exc:
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                f"immutable original could not be located: {exc}",
            )
        except StorageError as exc:
            return StageResult(
                WorkerOutcome.RETRY_WAIT, f"private storage read failed: {exc}"
            )

        try:
            secure = validate_document_bytes(
                data, content_type=context.declared_content_type
            )
        except ValueError as exc:
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                f"immutable original failed security validation: {exc}",
            )
        if secure.sha256 != context.content_sha256:
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "immutable original digest does not match the registered version",
            )
        if resume_rank < _rank(DocumentStage.SECURITY_VALIDATED):
            await self._advance(
                task,
                document_version_id,
                DocumentStage.REGISTERED,
                DocumentStage.SECURITY_VALIDATED,
            )
            await save_checkpoint(
                CHECKPOINT_SECURITY_VALIDATED,
                {
                    "sha256": secure.sha256,
                    "contentType": secure.content_type,
                    "sizeBytes": secure.size_bytes,
                },
            )

        # -- parse -----------------------------------------------------------
        try:
            parsed = parse_document(document_version_id, secure, parser=self._parser)
        except Exception as exc:  # noqa: BLE001 -- any parser/OCR failure is manual review
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                f"document could not be parsed: {exc}",
            )
        if not parsed.regions:
            return StageResult(
                WorkerOutcome.FAILED_MANUAL_REVIEW,
                "document parsed to no readable regions",
            )
        region_rows = self._region_rows(document_version_id, parsed)
        if resume_rank < _rank(DocumentStage.PARSED):
            await self._port.persist_parsed_regions(
                case_id=task.case_id,
                case_version=task.case_version,
                document_version_id=document_version_id,
                regions=region_rows,
                from_stage=DocumentStage.SECURITY_VALIDATED,
                to_stage=DocumentStage.PARSED,
            )
            await save_checkpoint(
                CHECKPOINT_PARSED,
                {
                    "regionCount": len(region_rows),
                    "parserVersion": parsed.parser_version,
                    "extractionMethod": parsed.extraction_method,
                },
            )

        # -- classify (deterministic) ---------------------------------------
        classification = classify_document(
            file_name=context.original_filename, parsed=parsed
        )
        if resume_rank < _rank(DocumentStage.CLASSIFIED):
            await self._advance(
                task,
                document_version_id,
                DocumentStage.PARSED,
                DocumentStage.CLASSIFIED,
            )
            await save_checkpoint(
                CHECKPOINT_CLASSIFIED,
                {
                    "family": classification.family,
                    "confidence": classification.confidence,
                    "method": classification.method,
                },
            )

        # -- extract (FPT) ---------------------------------------------------
        if resume_rank < _rank(DocumentStage.EXTRACTED):
            try:
                candidates, method = await self._extract(
                    context=context,
                    document_version_id=document_version_id,
                    secure=secure,
                    parsed=parsed,
                    classification=classification,
                    correlation_id=correlation_id,
                )
            except InferenceUnavailableError as exc:
                return StageResult(
                    WorkerOutcome.RETRY_WAIT,
                    f"FPT extraction endpoint is unavailable: {exc}",
                )
            except (InferenceError, ValidationError, ValueError) as exc:
                return StageResult(
                    WorkerOutcome.RETRY_WAIT, f"extraction output was rejected: {exc}"
                )
            candidate_rows = self._candidate_rows(
                document_version_id, candidates, parsed, region_rows, method
            )
            if candidate_rows is None:
                return StageResult(
                    WorkerOutcome.FAILED_MANUAL_REVIEW,
                    "an extraction candidate had no addressable evidence region",
                )
            await self._port.persist_candidates(
                case_id=task.case_id,
                case_version=task.case_version,
                document_version_id=document_version_id,
                candidates=candidate_rows,
                from_stage=DocumentStage.CLASSIFIED,
                to_stage=DocumentStage.EXTRACTED,
            )
            await save_checkpoint(
                CHECKPOINT_EXTRACTED, {"candidateCount": len(candidate_rows)}
            )

        # -- index (FPT embeddings) -----------------------------------------
        if resume_rank < _rank(DocumentStage.INDEXED):
            try:
                passages = await self._index(
                    context=context,
                    document_version_id=document_version_id,
                    parsed=parsed,
                    region_rows=region_rows,
                    correlation_id=correlation_id,
                )
            except InferenceUnavailableError as exc:
                # Never fabricate an embedding: pause for a bounded retry instead.
                return StageResult(
                    WorkerOutcome.RETRY_WAIT,
                    f"FPT embedding endpoint is unavailable: {exc}",
                )
            except (InferenceError, ValidationError, ValueError) as exc:
                return StageResult(
                    WorkerOutcome.RETRY_WAIT, f"embedding output was rejected: {exc}"
                )
            await self._port.persist_passages(
                case_id=task.case_id,
                case_version=task.case_version,
                document_version_id=document_version_id,
                passages=passages,
                from_stage=DocumentStage.EXTRACTED,
                to_stage=DocumentStage.INDEXED,
            )
            await save_checkpoint(CHECKPOINT_INDEXED, {"passageCount": len(passages)})

        return None

    # -- helpers ------------------------------------------------------------

    async def _advance(
        self,
        task: TaskRecord,
        document_version_id: UUID,
        from_stage: DocumentStage,
        to_stage: DocumentStage,
    ) -> None:
        # ``advance_document`` re-validates the single-step transition before the
        # durable guarded write; an illegal pair fails closed here in-process.
        advance_document(from_stage, to_stage)
        await self._port.advance_stage(
            case_id=task.case_id,
            case_version=task.case_version,
            document_version_id=document_version_id,
            from_stage=from_stage,
            to_stage=to_stage,
        )

    async def _load_original(self, context: DocumentIngestionContext) -> bytes:
        buffer = bytearray()
        async for chunk in self._storage.open_object(
            bucket_id=context.storage_bucket,
            object_key=context.storage_object_key,
        ):
            if not isinstance(chunk, bytes):
                raise StorageObjectMismatch("private storage returned a non-byte chunk")
            buffer.extend(chunk)
            if len(buffer) > context.byte_size:
                raise StorageObjectMismatch(
                    "immutable original is larger than the registered byte size"
                )
        if len(buffer) != context.byte_size:
            raise StorageObjectMismatch(
                "immutable original size does not match the registered byte size"
            )
        return bytes(buffer)

    def _region_rows(
        self, document_version_id: UUID, parsed: ParsedDocument
    ) -> list[PersistableRegion]:
        return [
            PersistableRegion(
                page_region_id=_region_id(document_version_id, region),
                page_number=region.page,
                x=region.x,
                y=region.y,
                width=region.width,
                height=region.height,
                extraction_method=parsed.extraction_method,
            )
            for region in parsed.regions
        ]

    def _candidate_rows(
        self,
        document_version_id: UUID,
        candidates: Sequence[ExtractionCandidate],
        parsed: ParsedDocument,
        region_rows: Sequence[PersistableRegion],
        method: str,
    ) -> list[PersistableCandidate] | None:
        rows: list[PersistableCandidate] = []
        for candidate in candidates:
            page_region_id: UUID | None = None
            for region, region_row in zip(parsed.regions, region_rows, strict=True):
                if _overlaps(candidate, region):
                    page_region_id = region_row.page_region_id
                    break
            if page_region_id is None:
                return None
            rows.append(
                PersistableCandidate(
                    candidate_fact_id=_candidate_id(
                        document_version_id, page_region_id, candidate.field_key
                    ),
                    page_region_id=page_region_id,
                    field_key=candidate.field_key,
                    proposed_value=candidate.proposed_value,
                    confidence=candidate.confidence,
                    extraction_method=method,
                )
            )
        return rows

    async def _extract(
        self,
        *,
        context: DocumentIngestionContext,
        document_version_id: UUID,
        secure: SecureDocument,
        parsed: ParsedDocument,
        classification: Classification,
        correlation_id: str,
    ) -> tuple[list[ExtractionCandidate], str]:
        source_text = "\n".join(region.text for region in parsed.regions)
        if len(source_text) > _MAX_MODEL_INPUT_CHARS:
            raise ValueError("parsed document text exceeds the model input limit")
        schema = extraction_schema(classification.family)

        if secure.content_type in {"image/jpeg", "image/png"}:
            if len(secure.content) > _MAX_VISION_INPUT_BYTES:
                raise ValueError("image exceeds the bounded vision input limit")
            method = "fpt-vision"
            response = await self._gateway.inspect_vision(
                VisionRequest(
                    correlation_id=correlation_id,
                    case_id=context.case_id,
                    document_version_id=document_version_id,
                    image_base64=base64.b64encode(secure.content).decode("ascii"),
                    media_type=secure.content_type,
                    response_schema=schema,
                )
            )
        elif secure.content_type.endswith("spreadsheetml.sheet"):
            method = "fpt-table"
            response = await self._gateway.extract_table(
                TableRequest(
                    correlation_id=correlation_id,
                    case_id=context.case_id,
                    document_version_id=document_version_id,
                    content=source_text,
                    document_family=classification.family,
                    response_schema=schema,
                )
            )
        else:
            method = "fpt-kie"
            response = await self._gateway.extract_kie(
                KIERequest(
                    correlation_id=correlation_id,
                    case_id=context.case_id,
                    document_version_id=document_version_id,
                    content=source_text,
                    document_family=classification.family,
                    response_schema=schema,
                )
            )
        self._assert_scope(response, context.case_id, document_version_id)
        candidates = validate_candidates(_candidate_payload(response.payload), parsed)
        return candidates, method

    async def _index(
        self,
        *,
        context: DocumentIngestionContext,
        document_version_id: UUID,
        parsed: ParsedDocument,
        region_rows: Sequence[PersistableRegion],
        correlation_id: str,
    ) -> list[PersistablePassage]:
        texts = [region.text for region in parsed.regions]
        response = await self._gateway.embed(
            EmbeddingRequest(
                correlation_id=correlation_id,
                case_id=context.case_id,
                document_version_id=document_version_id,
                texts=texts,
                expected_dimension=self._expected_embedding_dimension,
            )
        )
        self._assert_scope(response, context.case_id, document_version_id)
        vectors = response.payload
        if not isinstance(vectors, list) or len(vectors) != len(parsed.regions):
            raise ValueError("FPT embedding output count does not match parsed regions")
        passages: list[PersistablePassage] = []
        for region, region_row, vector in zip(
            parsed.regions, region_rows, vectors, strict=True
        ):
            if not isinstance(vector, (list, tuple)):
                raise ValueError("FPT embedding row is invalid")
            embedding = validate_embedding(
                vector, expected_dimension=self._expected_embedding_dimension
            )
            passages.append(
                PersistablePassage(
                    passage_id=_passage_id(document_version_id, region_row.page_region_id),
                    page_region_id=region_row.page_region_id,
                    passage_text=region.text,
                    extraction_method=parsed.extraction_method,
                    embedding=embedding,
                    embedding_model=response.model_id,
                    embedding_version=response.route_version,
                )
            )
        return passages

    @staticmethod
    def _assert_scope(
        result: InferenceResult, case_id: UUID, document_version_id: UUID
    ) -> None:
        if result.case_id != case_id or result.document_version_id != document_version_id:
            raise ValueError(
                "FPT result scope does not match the current document version"
            )


def _candidate_payload(payload: Any) -> list[ExtractionCandidate]:
    if not isinstance(payload, Mapping):
        raise ValueError("FPT extraction payload must be an object")
    raw = payload.get("candidates")
    if not isinstance(raw, list):
        raise ValueError("FPT extraction payload has no candidate list")
    try:
        return [ExtractionCandidate.model_validate(item) for item in raw]
    except (TypeError, ValueError) as exc:
        raise ValueError("FPT extraction candidates are invalid") from exc


# Re-exported so composition/tests can catch a single persistence error type.
__all__ = [
    "CHECKPOINT_CLASSIFIED",
    "CHECKPOINT_EXTRACTED",
    "CHECKPOINT_INDEXED",
    "CHECKPOINT_PARSED",
    "CHECKPOINT_READY",
    "CHECKPOINT_SECURITY_VALIDATED",
    "DocumentIngestionError",
    "DocumentIngestionProcessor",
]
