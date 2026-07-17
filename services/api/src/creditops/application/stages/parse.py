from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.application.stages.security import SecureDocument


class ParsedRegion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    page: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=100_000)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def fits_page(self) -> ParsedRegion:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("parsed region exceeds normalized page")
        return self


class ParsedDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    document_version_id: UUID
    content_type: str
    regions: tuple[ParsedRegion, ...]
    extraction_method: str = Field(min_length=1)
    parser_version: str = Field(default="deterministic-parser-v1", min_length=1)

    @model_validator(mode="after")
    def bounded_regions(self) -> ParsedDocument:
        if len(self.regions) > 50_000:
            raise ValueError("parsed document has too many regions")
        if sum(len(region.text) for region in self.regions) > 2_000_000:
            raise ValueError("parsed document text exceeds the stage limit")
        return self


class DocumentParser(Protocol):
    def parse(self, document_version_id: UUID, document: SecureDocument) -> ParsedDocument: ...


def _parser_for(content_type: str) -> DocumentParser:
    # Imports stay local so an unavailable optional parser fails at the stage
    # boundary rather than making API health/configuration unusable.
    if content_type == "application/pdf":
        from creditops.infrastructure.parsers.pdf import PdfParser

        return PdfParser()
    if content_type.endswith("wordprocessingml.document"):
        from creditops.infrastructure.parsers.docx import DocxParser

        return DocxParser()
    if content_type.endswith("spreadsheetml.sheet"):
        from creditops.infrastructure.parsers.xlsx import XlsxParser

        return XlsxParser()
    if content_type in {"image/jpeg", "image/png"}:
        from creditops.infrastructure.parsers.images import ImageParser

        return ImageParser()
    raise ValueError("no deterministic parser for document content type")


def parse_document(
    document_version_id: UUID,
    document: SecureDocument,
    *,
    parser: DocumentParser | None = None,
) -> ParsedDocument:
    selected = parser or _parser_for(document.content_type)
    parsed = selected.parse(document_version_id, document)
    if parsed.document_version_id != document_version_id:
        raise ValueError("parser returned a different document version")
    if parsed.content_type != document.content_type:
        raise ValueError("parser returned a different content type")
    return parsed


def iter_page_regions(parsed: ParsedDocument, page: int) -> Iterable[ParsedRegion]:
    return (region for region in parsed.regions if region.page == page)
