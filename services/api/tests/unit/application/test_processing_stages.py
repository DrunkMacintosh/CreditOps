from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from creditops.application.ports.model_gateway import InferenceResult
from creditops.application.stages.classify import classify_document
from creditops.application.stages.extract import ExtractionCandidate, validate_candidates
from creditops.application.stages.parse import ParsedDocument, ParsedRegion
from creditops.application.stages.pipeline import process_document
from creditops.application.stages.security import SecureDocument, validate_document_bytes


def _parsed() -> ParsedDocument:
    return ParsedDocument(
        document_version_id=uuid4(),
        content_type="application/pdf",
        extraction_method="test",
        regions=(
            ParsedRegion(page=1, text="Số tiền đề nghị: 500000000", x=0, y=0, width=1, height=0.1),
        ),
    )


def test_security_rejects_mismatched_content_type() -> None:
    with pytest.raises(ValueError, match="PDF"):
        validate_document_bytes(b"not-a-pdf", content_type="application/pdf")


def test_secure_document_rejects_tampered_integrity_metadata() -> None:
    document = validate_document_bytes(b"%PDF-1.7", content_type="application/pdf")
    with pytest.raises(ValueError, match="digest"):
        SecureDocument.model_validate({**document.model_dump(), "sha256": "0" * 64})


def test_classifier_is_deterministic_and_does_not_make_a_credit_decision() -> None:
    result = classify_document(file_name="don_de_nghi_cap_tin_dung.pdf", parsed=_parsed())
    assert result.family == "CREDIT_REQUEST"
    assert result.confidence == 0.85
    assert "decision" not in result.model_dump_json().lower()


def test_grounded_candidates_must_reference_a_parsed_region() -> None:
    parsed = _parsed()
    candidate = ExtractionCandidate(
        field_key="requested_amount",
        proposed_value="500000000",
        confidence=0.92,
        page=1,
        x=0,
        y=0,
        width=0.2,
        height=0.1,
    )
    assert validate_candidates([candidate], parsed) == [candidate]
    outside = candidate.model_copy(update={"x": 0.8, "width": 0.3})
    with pytest.raises(ValueError, match="source region"):
        validate_candidates([outside], parsed)


class _Parser:
    def parse(self, document_version_id: object, document: SecureDocument) -> ParsedDocument:
        return ParsedDocument(
            document_version_id=document_version_id,  # type: ignore[arg-type]
            content_type=document.content_type,
            extraction_method="test",
            regions=(
                ParsedRegion(
                    page=1,
                    text="Số tiền đề nghị: 500000000",
                    x=0,
                    y=0,
                    width=1,
                    height=0.1,
                ),
            ),
        )


class _Gateway:
    def __init__(self, case_id: object, document_version_id: object) -> None:
        self.case_id = case_id
        self.document_version_id = document_version_id

    async def extract_kie(self, request: object) -> InferenceResult:
        candidate = {
            "field_key": "requested_amount",
            "proposed_value": "500000000",
            "confidence": 0.9,
            "page": 1,
            "x": 0,
            "y": 0,
            "width": 0.3,
            "height": 0.1,
        }
        return InferenceResult(
            capability="kie",
            provider="FPT",
            case_id=self.case_id,  # type: ignore[arg-type]
            document_version_id=self.document_version_id,  # type: ignore[arg-type]
            endpoint_id="kie-1",
            model_id="kie-gated",
            payload={"candidates": [candidate]},
            prompt_version="p1",
            schema_version="s1",
            route_version="r1",
            correlation_id="corr",
            started_at=datetime.now(UTC),
            latency_ms=1,
        )

    async def embed(self, request: object) -> InferenceResult:
        return InferenceResult(
            capability="embedding",
            provider="FPT",
            case_id=self.case_id,  # type: ignore[arg-type]
            document_version_id=self.document_version_id,  # type: ignore[arg-type]
            endpoint_id="embedding-1",
            model_id="embedding-gated",
            payload=[(0.1, 0.2)],
            prompt_version="p1",
            schema_version="s1",
            route_version="r1",
            correlation_id="corr",
            started_at=datetime.now(UTC),
            latency_ms=1,
        )


@pytest.mark.asyncio
async def test_pipeline_wires_parser_to_fpt_and_returns_only_candidates() -> None:
    case_id = uuid4()
    version_id = uuid4()
    result = await process_document(
        case_id=case_id,
        document_version_id=version_id,
        file_name="don_de_nghi_cap_tin_dung.pdf",
        document=validate_document_bytes(b"%PDF-1.7", content_type="application/pdf"),
        correlation_id="corr",
        gateway=_Gateway(case_id, version_id),  # type: ignore[arg-type]
        parser=_Parser(),  # type: ignore[arg-type]
        expected_embedding_dimension=2,
    )
    assert result.classification.family == "CREDIT_REQUEST"
    assert result.candidates[0].field_key == "requested_amount"
    assert result.passages[0].embedding_model_id == "embedding-gated"
