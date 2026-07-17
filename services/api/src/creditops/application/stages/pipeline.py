"""Pure orchestration of the bounded document stages.

The durable worker owns leases, checkpoints, and database writes.  This
function only wires deterministic parsing and classification to the injected
FPT gateway; it cannot create confirmed facts or mutate workflow state.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from creditops.application.ports.model_gateway import (
    EmbeddingRequest,
    InferenceGateway,
    InferenceResult,
    KIERequest,
    TableRequest,
    VisionRequest,
)
from creditops.application.stages.classify import Classification, classify_document
from creditops.application.stages.extract import (
    ExtractionCandidate,
    extraction_schema,
    validate_candidates,
)
from creditops.application.stages.index import IndexedPassage, validate_embedding
from creditops.application.stages.parse import DocumentParser, ParsedDocument, parse_document
from creditops.application.stages.security import SecureDocument


class ProcessingSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    parsed: ParsedDocument
    classification: Classification
    candidates: tuple[ExtractionCandidate, ...]
    passages: tuple[IndexedPassage, ...]


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


def _assert_scope(result: InferenceResult, case_id: UUID, document_version_id: UUID) -> None:
    if result.case_id != case_id or result.document_version_id != document_version_id:
        raise ValueError("FPT result scope does not match the current document version")


async def process_document(
    *,
    case_id: UUID,
    document_version_id: UUID,
    file_name: str,
    document: SecureDocument,
    correlation_id: str,
    gateway: InferenceGateway,
    parser: DocumentParser | None = None,
    expected_embedding_dimension: int | None = None,
) -> ProcessingSnapshot:
    """Run one bounded document through parse → classify → extract → index.

    Provider routing is explicit by content family.  Missing FPT capabilities
    raise visibly through the gateway; no local or non-FPT fallback exists.
    """

    parsed = parse_document(document_version_id, document, parser=parser)
    classification = classify_document(file_name=file_name, parsed=parsed)
    source_text = "\n".join(region.text for region in parsed.regions)
    if len(source_text) > 200_000:
        raise ValueError("parsed document text exceeds the model input limit")
    schema = extraction_schema(classification.family)
    if document.content_type in {"image/jpeg", "image/png"}:
        if len(document.content) > 15 * 1024 * 1024:
            raise ValueError("image exceeds the bounded vision input limit")
        response = await gateway.inspect_vision(
            VisionRequest(
                correlation_id=correlation_id,
                case_id=case_id,
                document_version_id=document_version_id,
                image_base64=base64.b64encode(document.content).decode("ascii"),
                media_type=document.content_type,
                response_schema=schema,
            )
        )
    elif document.content_type.endswith("spreadsheetml.sheet"):
        response = await gateway.extract_table(
            TableRequest(
                correlation_id=correlation_id,
                case_id=case_id,
                document_version_id=document_version_id,
                content=source_text,
                document_family=classification.family,
                response_schema=schema,
            )
        )
    else:
        response = await gateway.extract_kie(
            KIERequest(
                correlation_id=correlation_id,
                case_id=case_id,
                document_version_id=document_version_id,
                content=source_text,
                document_family=classification.family,
                response_schema=schema,
            )
        )
    _assert_scope(response, case_id, document_version_id)
    candidates = tuple(validate_candidates(_candidate_payload(response.payload), parsed))
    texts = [region.text for region in parsed.regions]
    passages: list[IndexedPassage] = []
    if texts:
        embedding_response = await gateway.embed(
            EmbeddingRequest(
                correlation_id=correlation_id,
                case_id=case_id,
                document_version_id=document_version_id,
                texts=texts,
                expected_dimension=expected_embedding_dimension,
            )
        )
        _assert_scope(embedding_response, case_id, document_version_id)
        vectors = embedding_response.payload
        if not isinstance(vectors, list) or len(vectors) != len(parsed.regions):
            raise ValueError("FPT embedding output count does not match parsed regions")
        for region, vector in zip(parsed.regions, vectors, strict=True):
            if not isinstance(vector, (list, tuple)):
                raise ValueError("FPT embedding row is invalid")
            passages.append(
                IndexedPassage(
                    page=region.page,
                    text=region.text,
                    embedding=validate_embedding(
                        vector,
                        expected_dimension=expected_embedding_dimension,
                    ),
                    embedding_model_id=embedding_response.model_id,
                )
            )
    return ProcessingSnapshot(
        parsed=parsed,
        classification=classification,
        candidates=candidates,
        passages=tuple(passages),
    )
