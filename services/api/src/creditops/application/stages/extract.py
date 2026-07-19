from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.application.stages.parse import ParsedDocument, ParsedRegion


class ExtractionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    field_key: str = Field(min_length=1, max_length=120)
    proposed_value: str | int | float | bool
    confidence: float = Field(ge=0, le=1)
    page: int = Field(ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def valid_region(self) -> ExtractionCandidate:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("source region exceeds normalized page")
        return self


def _overlaps(candidate: ExtractionCandidate, region: ParsedRegion) -> bool:
    return bool(
        candidate.page == region.page
        and candidate.x < region.x + region.width
        and candidate.x + candidate.width > region.x
        and candidate.y < region.y + region.height
        and candidate.y + candidate.height > region.y
    )


def _normalized_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _digits_only(value: str) -> str:
    return "".join(char for char in value if char.isdigit())


def _region_grounding(candidate: ExtractionCandidate, region: ParsedRegion) -> bool:
    """True when the candidate's proposed value literally appears in the region.

    Deterministic, server-side evidence grounding: a numeric value matches on
    its digit sequence (so ``2500000000`` grounds against the Vietnamese
    thousands format ``2.500.000.000``), a textual value on case-insensitive
    whitespace-collapsed containment.  No model judgement is involved.
    """
    raw = str(candidate.proposed_value).strip()
    if not raw:
        return False
    region_text = _normalized_text(region.text)
    if isinstance(candidate.proposed_value, bool):
        return _normalized_text(raw) in region_text
    digits = _digits_only(raw)
    separators_stripped = raw.replace(".", "").replace(",", "").replace(" ", "")
    if digits and (
        isinstance(candidate.proposed_value, (int, float)) or digits == separators_stripped
    ):
        return digits in _digits_only(region.text)
    return _normalized_text(raw) in region_text


def validate_candidates(
    candidates: Iterable[ExtractionCandidate],
    parsed: ParsedDocument,
) -> list[ExtractionCandidate]:
    """Ground every candidate to a REAL parsed region.

    Text-route models receive no page geometry, so their coordinate fields are
    unreliable; a candidate whose coordinates match no region is re-anchored to
    the first parsed region whose text literally contains the proposed value
    (the region's true parser geometry replaces the model's invented one).  A
    candidate that can be grounded neither way is DROPPED -- an unverifiable
    proposal never becomes evidence.  If nothing survives, the extraction as a
    whole is rejected (live 2026-07-19: a blanket raise here failed the entire
    ingestion although the model's values were correct and present verbatim in
    the parsed text).
    """
    regions = parsed.regions
    validated: list[ExtractionCandidate] = []
    for candidate in candidates:
        if candidate.x + candidate.width > 1 or candidate.y + candidate.height > 1:
            raise ValueError("candidate source region exceeds normalized page")
        if any(_overlaps(candidate, region) for region in regions):
            validated.append(candidate)
            continue
        anchor = next(
            (region for region in regions if _region_grounding(candidate, region)),
            None,
        )
        if anchor is None:
            # Unverifiable: exclude the candidate rather than fabricate an
            # evidence region for it.
            continue
        validated.append(
            candidate.model_copy(
                update={
                    "page": anchor.page,
                    "x": anchor.x,
                    "y": anchor.y,
                    "width": anchor.width,
                    "height": anchor.height,
                }
            )
        )
    if not validated:
        raise ValueError("no extraction candidate could be grounded to a parsed region")
    return validated


def extraction_schema(document_family: str) -> dict[str, Any]:
    # The model can propose candidates only.  The schema deliberately has no
    # confirmation, approval, score, or workflow-transition field.
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidates"],
        "properties": {
            "candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "field_key",
                        "proposed_value",
                        "confidence",
                        "page",
                        "x",
                        "y",
                        "width",
                        "height",
                    ],
                    "properties": {
                        "field_key": {"type": "string", "minLength": 1},
                        "proposed_value": {},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "page": {"type": "integer", "minimum": 1},
                        "x": {"type": "number", "minimum": 0, "maximum": 1},
                        "y": {"type": "number", "minimum": 0, "maximum": 1},
                        "width": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                        "height": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                    },
                },
            },
            "document_family": {"const": document_family},
        },
    }
