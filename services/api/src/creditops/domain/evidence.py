from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from creditops.domain.enums import FactDisposition
from creditops.domain.ids import (
    ActorId,
    CandidateFactId,
    CaseId,
    ConfirmedFactId,
    DocumentVersionId,
    FactConfirmationId,
)

type FactValue = str | int | float | bool


class PageRegion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    page: int = Field(ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def within_page(self) -> Self:
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("region exceeds normalized page")
        return self


class ConfirmationAuthority(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: CaseId
    case_version: int = Field(ge=1)
    actor_id: ActorId
    assigned_officer_id: ActorId
    granted_at: datetime
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def actor_is_assigned_officer(self) -> Self:
        if self.actor_id != self.assigned_officer_id:
            raise ValueError("confirmation authority requires the assigned officer")
        return self


class CandidateFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: CandidateFactId
    case_id: CaseId
    case_version: int = Field(ge=1)
    document_version_id: DocumentVersionId
    field_key: str = Field(min_length=1)
    proposed_value: FactValue
    confidence: float = Field(ge=0, le=1)
    source: PageRegion


class FactConfirmation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: FactConfirmationId
    candidate_id: CandidateFactId
    disposition: FactDisposition
    authority: ConfirmationAuthority
    confirmed_at: datetime
    corrected_value: FactValue | None = None

    @model_validator(mode="after")
    def corrected_value_matches_disposition(self) -> Self:
        if self.disposition is FactDisposition.CORRECTED and self.corrected_value is None:
            raise ValueError("corrected_value is required for a corrected fact")
        if self.disposition is not FactDisposition.CORRECTED and self.corrected_value is not None:
            raise ValueError("corrected_value is only valid for a corrected fact")
        return self


class ConfirmedFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ConfirmedFactId
    case_id: CaseId
    case_version: int = Field(ge=1)
    candidate_id: CandidateFactId
    confirmation_id: FactConfirmationId
    document_version_id: DocumentVersionId
    field_key: str = Field(min_length=1)
    value: FactValue
    candidate_value: FactValue
    source: PageRegion
    authority: ConfirmationAuthority
    confirmed_at: datetime

    @model_validator(mode="after")
    def authority_matches_fact_version(self) -> Self:
        if self.authority.case_id != self.case_id:
            raise ValueError("confirmed fact authority does not match case")
        if self.authority.case_version != self.case_version:
            raise ValueError("confirmed fact authority does not match case version")
        return self

    @classmethod
    def from_confirmation(
        cls,
        *,
        id: ConfirmedFactId,
        candidate: CandidateFact,
        confirmation: FactConfirmation,
    ) -> Self:
        if confirmation.candidate_id != candidate.id:
            raise ValueError("confirmation does not reference the candidate")
        if confirmation.authority.case_id != candidate.case_id:
            raise ValueError("confirmation authority does not match candidate case")
        if confirmation.authority.case_version != candidate.case_version:
            raise ValueError("confirmation authority does not match candidate case version")
        if confirmation.disposition is FactDisposition.ACCEPTED:
            value = candidate.proposed_value
        elif confirmation.disposition is FactDisposition.CORRECTED:
            if confirmation.corrected_value is None:
                raise ValueError("corrected confirmation has no corrected_value")
            value = confirmation.corrected_value
        else:
            raise ValueError(
                f"{confirmation.disposition.value} does not support a confirmed fact"
            )

        return cls(
            id=id,
            case_id=candidate.case_id,
            case_version=candidate.case_version,
            candidate_id=candidate.id,
            confirmation_id=confirmation.id,
            document_version_id=candidate.document_version_id,
            field_key=candidate.field_key,
            value=value,
            candidate_value=candidate.proposed_value,
            source=candidate.source,
            authority=confirmation.authority,
            confirmed_at=confirmation.confirmed_at,
        )


class EvidenceEntityType(StrEnum):
    """The typed nodes the Case Evidence Graph lineage chain connects.

    The string values match the ``entity_type`` vocabulary the graph traversal
    (``infrastructure/postgres/retrieval.traverse_evidence_graph``) already
    speaks; nothing outside this closed set may appear on an edge.
    """

    CONFIRMED_FACT = "CONFIRMED_FACT"
    CANDIDATE_FACT = "CANDIDATE_FACT"
    PAGE_REGION = "PAGE_REGION"
    DOCUMENT_VERSION = "DOCUMENT_VERSION"


class EvidenceEdgeType(StrEnum):
    """The deterministic lineage edges materialised at fact confirmation.

    A confirmed fact points back at the exact evidence it was derived from --
    its candidate, the page region it was located in, and the document version
    it was sourced from.  These are the ONLY edge types this slice writes.
    """

    DERIVED_FROM_CANDIDATE = "DERIVED_FROM_CANDIDATE"
    LOCATED_IN_REGION = "LOCATED_IN_REGION"
    SOURCED_FROM_DOCUMENT_VERSION = "SOURCED_FROM_DOCUMENT_VERSION"


#: The closed allowlist of ``(edge_type, source_entity_type, target_entity_type)``
#: triples that may ever be persisted.  Any other combination is rejected (fail
#: closed): the writer never invents a typed edge outside this set.
_ALLOWED_EVIDENCE_EDGES: frozenset[
    tuple[EvidenceEdgeType, EvidenceEntityType, EvidenceEntityType]
] = frozenset(
    {
        (
            EvidenceEdgeType.DERIVED_FROM_CANDIDATE,
            EvidenceEntityType.CONFIRMED_FACT,
            EvidenceEntityType.CANDIDATE_FACT,
        ),
        (
            EvidenceEdgeType.LOCATED_IN_REGION,
            EvidenceEntityType.CONFIRMED_FACT,
            EvidenceEntityType.PAGE_REGION,
        ),
        (
            EvidenceEdgeType.SOURCED_FROM_DOCUMENT_VERSION,
            EvidenceEntityType.CONFIRMED_FACT,
            EvidenceEntityType.DOCUMENT_VERSION,
        ),
    }
)


class EvidenceNodeRef(BaseModel):
    """One typed, case+version-scoped node an evidence edge connects."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: CaseId
    case_version: int = Field(ge=1)
    entity_type: EvidenceEntityType
    entity_id: UUID


class EvidenceEdge(BaseModel):
    """A typed, immutable lineage edge in the Case Evidence Graph.

    An edge is only valid if its ``(edge_type, source_entity_type,
    target_entity_type)`` triple is allowlisted; both endpoints share the edge's
    single ``case_id`` + ``case_version`` (there is no cross-case edge).  The
    unique typed-edge constraint (``evidence_edges_unique_typed_edge``) makes
    persistence idempotent.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    case_id: CaseId
    case_version: int = Field(ge=1)
    edge_type: EvidenceEdgeType
    source_entity_type: EvidenceEntityType
    source_entity_id: UUID
    target_entity_type: EvidenceEntityType
    target_entity_id: UUID

    @model_validator(mode="after")
    def edge_is_allowlisted(self) -> Self:
        triple = (
            self.edge_type,
            self.source_entity_type,
            self.target_entity_type,
        )
        if triple not in _ALLOWED_EVIDENCE_EDGES:
            raise ValueError(f"evidence edge {triple} is not allowlisted")
        return self

    @classmethod
    def lineage(
        cls,
        *,
        edge_type: EvidenceEdgeType,
        source: EvidenceNodeRef,
        target: EvidenceNodeRef,
    ) -> Self:
        """Build one allowlisted lineage edge, guarding against a cross-case edge.

        The source and target MUST belong to the same ``case_id`` and
        ``case_version``; the shared scope is bound onto the edge so no caller
        can span two cases.  The allowlist is enforced by ``edge_is_allowlisted``.
        """

        if source.case_id != target.case_id:
            raise ValueError("evidence edge endpoints must share the same case_id")
        if source.case_version != target.case_version:
            raise ValueError(
                "evidence edge endpoints must share the same case_version"
            )
        return cls(
            case_id=source.case_id,
            case_version=source.case_version,
            edge_type=edge_type,
            source_entity_type=source.entity_type,
            source_entity_id=source.entity_id,
            target_entity_type=target.entity_type,
            target_entity_id=target.entity_id,
        )
