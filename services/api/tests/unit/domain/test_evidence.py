from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from creditops.domain.enums import FactDisposition
from creditops.domain.evidence import (
    CandidateFact,
    ConfirmationAuthority,
    ConfirmedFact,
    EvidenceEdge,
    EvidenceEdgeType,
    EvidenceEntityType,
    EvidenceNodeRef,
    FactConfirmation,
    PageRegion,
)

GRANTED_AT = datetime(2026, 7, 17, 9, 0, tzinfo=UTC)
CONFIRMED_AT = GRANTED_AT + timedelta(minutes=5)


def confirmation_authority(
    *,
    case_id: UUID,
    case_version: int = 7,
    actor_id: UUID | None = None,
    assigned_officer_id: UUID | None = None,
) -> ConfirmationAuthority:
    assigned_officer_id = assigned_officer_id or uuid4()
    return ConfirmationAuthority(
        case_id=case_id,
        case_version=case_version,
        actor_id=actor_id or assigned_officer_id,
        assigned_officer_id=assigned_officer_id,
        granted_at=GRANTED_AT,
        source="CASE_ASSIGNMENT",
    )


def candidate_fact(
    *,
    case_id: UUID | None = None,
    case_version: int = 7,
) -> CandidateFact:
    return CandidateFact(
        id=uuid4(),
        case_id=case_id or uuid4(),
        case_version=case_version,
        document_version_id=uuid4(),
        field_key="requested_amount",
        proposed_value="5000000000",
        confidence=0.91,
        source=PageRegion(page=1, x=0.1, y=0.2, width=0.3, height=0.04),
    )


def confirmation_for(
    candidate: CandidateFact,
    *,
    disposition: FactDisposition = FactDisposition.ACCEPTED,
    corrected_value: str | None = None,
    authority: ConfirmationAuthority | None = None,
) -> FactConfirmation:
    return FactConfirmation(
        id=uuid4(),
        candidate_id=candidate.id,
        disposition=disposition,
        authority=authority
        or confirmation_authority(
            case_id=candidate.case_id,
            case_version=candidate.case_version,
        ),
        confirmed_at=CONFIRMED_AT,
        corrected_value=corrected_value,
    )


def test_candidate_requires_normalized_addressable_source() -> None:
    candidate = candidate_fact()

    assert candidate.source.page == 1


def test_region_outside_page_is_rejected() -> None:
    with pytest.raises(ValidationError, match="region exceeds normalized page"):
        PageRegion(page=1, x=0.9, y=0.2, width=0.2, height=0.1)


def test_candidate_without_source_region_is_rejected() -> None:
    with pytest.raises(ValidationError, match="source"):
        CandidateFact(
            id=uuid4(),
            case_id=uuid4(),
            case_version=7,
            document_version_id=uuid4(),
            field_key="requested_amount",
            proposed_value="5000000000",
            confidence=0.91,
        )


def test_candidate_rejects_unsupported_structured_value() -> None:
    with pytest.raises(ValidationError, match="proposed_value"):
        CandidateFact(
            id=uuid4(),
            case_id=uuid4(),
            case_version=7,
            document_version_id=uuid4(),
            field_key="requested_amount",
            proposed_value=["not", "an", "evidence scalar"],
            confidence=0.91,
            source=PageRegion(page=1, x=0.1, y=0.2, width=0.3, height=0.04),
        )


def test_confirmation_authority_rejects_unassigned_actor() -> None:
    with pytest.raises(ValidationError, match="assigned officer"):
        confirmation_authority(
            case_id=uuid4(),
            actor_id=uuid4(),
            assigned_officer_id=uuid4(),
        )


def test_confirmation_requires_authority_proof() -> None:
    candidate = candidate_fact()

    with pytest.raises(ValidationError, match="authority"):
        FactConfirmation(
            id=uuid4(),
            candidate_id=candidate.id,
            disposition=FactDisposition.ACCEPTED,
            confirmed_at=CONFIRMED_AT,
        )


def test_confirmation_requires_timestamp() -> None:
    candidate = candidate_fact()

    with pytest.raises(ValidationError, match="confirmed_at"):
        FactConfirmation(
            id=uuid4(),
            candidate_id=candidate.id,
            disposition=FactDisposition.ACCEPTED,
            authority=confirmation_authority(
                case_id=candidate.case_id,
                case_version=candidate.case_version,
            ),
        )


def test_confirmation_rejects_open_disposition() -> None:
    candidate = candidate_fact()

    with pytest.raises(ValidationError, match="OPEN"):
        FactConfirmation(
            id=uuid4(),
            candidate_id=candidate.id,
            disposition="OPEN",
            authority=confirmation_authority(
                case_id=candidate.case_id,
                case_version=candidate.case_version,
            ),
            confirmed_at=CONFIRMED_AT,
        )


def test_correction_requires_a_replacement_value() -> None:
    candidate = candidate_fact()

    with pytest.raises(ValidationError, match="corrected_value"):
        confirmation_for(candidate, disposition=FactDisposition.CORRECTED)


def test_only_supported_confirmation_creates_confirmed_fact() -> None:
    candidate = candidate_fact()
    confirmation = confirmation_for(candidate)

    fact = ConfirmedFact.from_confirmation(
        id=uuid4(),
        candidate=candidate,
        confirmation=confirmation,
    )

    assert fact.case_id == candidate.case_id
    assert fact.case_version == candidate.case_version
    assert fact.value == candidate.proposed_value
    assert fact.candidate_value == candidate.proposed_value
    assert fact.source == candidate.source
    assert fact.authority == confirmation.authority
    assert fact.confirmed_at == confirmation.confirmed_at


def test_correction_preserves_candidate_value_and_source() -> None:
    candidate = candidate_fact()
    confirmation = confirmation_for(
        candidate,
        disposition=FactDisposition.CORRECTED,
        corrected_value="4800000000",
    )

    fact = ConfirmedFact.from_confirmation(
        id=uuid4(),
        candidate=candidate,
        confirmation=confirmation,
    )

    assert fact.value == "4800000000"
    assert fact.candidate_value == "5000000000"
    assert fact.source == candidate.source


@pytest.mark.parametrize(
    "disposition",
    [FactDisposition.ABSENT, FactDisposition.UNREADABLE],
)
def test_unsupported_disposition_cannot_create_confirmed_fact(
    disposition: FactDisposition,
) -> None:
    candidate = candidate_fact()
    confirmation = confirmation_for(candidate, disposition=disposition)

    with pytest.raises(ValueError, match="does not support a confirmed fact"):
        ConfirmedFact.from_confirmation(
            id=uuid4(),
            candidate=candidate,
            confirmation=confirmation,
        )


def test_confirmation_must_reference_the_candidate() -> None:
    candidate = candidate_fact()
    confirmation = confirmation_for(candidate).model_copy(update={"candidate_id": uuid4()})

    with pytest.raises(ValueError, match="candidate"):
        ConfirmedFact.from_confirmation(
            id=uuid4(),
            candidate=candidate,
            confirmation=confirmation,
        )


def test_confirmation_authority_must_match_candidate_case() -> None:
    candidate = candidate_fact()
    confirmation = confirmation_for(
        candidate,
        authority=confirmation_authority(case_id=uuid4()),
    )

    with pytest.raises(ValueError, match="case"):
        ConfirmedFact.from_confirmation(
            id=uuid4(),
            candidate=candidate,
            confirmation=confirmation,
        )


def test_confirmation_authority_must_match_candidate_version() -> None:
    candidate = candidate_fact(case_version=7)
    confirmation = confirmation_for(
        candidate,
        authority=confirmation_authority(
            case_id=candidate.case_id,
            case_version=8,
        ),
    )

    with pytest.raises(ValueError, match="version"):
        ConfirmedFact.from_confirmation(
            id=uuid4(),
            candidate=candidate,
            confirmation=confirmation,
        )


def test_direct_confirmed_fact_requires_full_authority_proof() -> None:
    candidate = candidate_fact()
    confirmation = confirmation_for(candidate)

    with pytest.raises(ValidationError, match="authority|confirmed_at"):
        ConfirmedFact(
            id=uuid4(),
            case_id=candidate.case_id,
            case_version=candidate.case_version,
            candidate_id=candidate.id,
            confirmation_id=confirmation.id,
            document_version_id=candidate.document_version_id,
            field_key=candidate.field_key,
            value=candidate.proposed_value,
            candidate_value=candidate.proposed_value,
            source=candidate.source,
        )


def test_evidence_models_are_immutable() -> None:
    candidate = candidate_fact()
    authority = confirmation_authority(case_id=candidate.case_id)

    with pytest.raises(ValidationError, match="frozen"):
        candidate.field_key = "mutated"
    with pytest.raises(ValidationError, match="frozen"):
        authority.source = "mutated"


# --- evidence edges --------------------------------------------------------


def _node(
    entity_type: EvidenceEntityType,
    *,
    case_id: UUID,
    case_version: int = 7,
    entity_id: UUID | None = None,
) -> EvidenceNodeRef:
    return EvidenceNodeRef(
        case_id=case_id,
        case_version=case_version,
        entity_type=entity_type,
        entity_id=entity_id or uuid4(),
    )


def test_lineage_edge_binds_shared_case_scope_for_allowlisted_triple() -> None:
    case_id = uuid4()
    source = _node(EvidenceEntityType.CONFIRMED_FACT, case_id=case_id)
    target = _node(EvidenceEntityType.DOCUMENT_VERSION, case_id=case_id)

    edge = EvidenceEdge.lineage(
        edge_type=EvidenceEdgeType.SOURCED_FROM_DOCUMENT_VERSION,
        source=source,
        target=target,
    )

    assert edge.case_id == case_id
    assert edge.case_version == 7
    assert edge.source_entity_id == source.entity_id
    assert edge.target_entity_id == target.entity_id


def test_lineage_edge_rejects_cross_case_endpoints() -> None:
    source = _node(EvidenceEntityType.CONFIRMED_FACT, case_id=uuid4())
    target = _node(EvidenceEntityType.CANDIDATE_FACT, case_id=uuid4())

    with pytest.raises(ValueError, match="case_id"):
        EvidenceEdge.lineage(
            edge_type=EvidenceEdgeType.DERIVED_FROM_CANDIDATE,
            source=source,
            target=target,
        )


def test_lineage_edge_rejects_cross_case_version_endpoints() -> None:
    case_id = uuid4()
    source = _node(EvidenceEntityType.CONFIRMED_FACT, case_id=case_id, case_version=7)
    target = _node(EvidenceEntityType.PAGE_REGION, case_id=case_id, case_version=8)

    with pytest.raises(ValueError, match="case_version"):
        EvidenceEdge.lineage(
            edge_type=EvidenceEdgeType.LOCATED_IN_REGION,
            source=source,
            target=target,
        )


def test_lineage_edge_rejects_non_allowlisted_triple() -> None:
    case_id = uuid4()
    source = _node(EvidenceEntityType.CONFIRMED_FACT, case_id=case_id)
    # DERIVED_FROM_CANDIDATE with a DOCUMENT_VERSION target is not allowlisted.
    target = _node(EvidenceEntityType.DOCUMENT_VERSION, case_id=case_id)

    with pytest.raises(ValidationError, match="not allowlisted"):
        EvidenceEdge.lineage(
            edge_type=EvidenceEdgeType.DERIVED_FROM_CANDIDATE,
            source=source,
            target=target,
        )


def test_evidence_edge_is_immutable() -> None:
    case_id = uuid4()
    edge = EvidenceEdge.lineage(
        edge_type=EvidenceEdgeType.LOCATED_IN_REGION,
        source=_node(EvidenceEntityType.CONFIRMED_FACT, case_id=case_id),
        target=_node(EvidenceEntityType.PAGE_REGION, case_id=case_id),
    )
    with pytest.raises(ValidationError, match="frozen"):
        edge.edge_type = EvidenceEdgeType.DERIVED_FROM_CANDIDATE
