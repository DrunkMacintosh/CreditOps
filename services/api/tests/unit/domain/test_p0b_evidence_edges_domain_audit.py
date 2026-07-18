"""P0-B INDEPENDENT AUDIT -- the closed evidence-edge allowlist (domain layer).

NEW standalone tests (not editing the concurrent build task's
``test_evidence.py``).  These exhaustively pin the fail-closed contract of
``EvidenceEdge`` / ``EvidenceEdge.lineage``: only the three lineage triples may
ever be built, EVERY other (edge_type, source_type, target_type) combination --
including a valid type used in the WRONG direction -- is rejected, and the
allowlist cannot be bypassed by constructing ``EvidenceEdge`` directly.

The closed allowlist lives ONLY in this Python domain layer -- the
``evidence_edges`` table stores ``edge_type`` / ``*_entity_type`` as free text
(length-checked only) and carries no FK to its endpoint entities.  So this
allowlist is the sole enforcement of edge shape + direction on the application
write path; hence the exhaustive audit here.
"""

from __future__ import annotations

import itertools
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from creditops.domain.evidence import (
    EvidenceEdge,
    EvidenceEdgeType,
    EvidenceEntityType,
    EvidenceNodeRef,
)

_ALLOWED = {
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


def _node(
    entity_type: EvidenceEntityType,
    *,
    case_id: UUID,
    case_version: int = 3,
    entity_id: UUID | None = None,
) -> EvidenceNodeRef:
    return EvidenceNodeRef(
        case_id=case_id,
        case_version=case_version,
        entity_type=entity_type,
        entity_id=entity_id or uuid4(),
    )


def _build(
    edge_type: EvidenceEdgeType,
    source_type: EvidenceEntityType,
    target_type: EvidenceEntityType,
    *,
    case_id: UUID,
) -> EvidenceEdge:
    return EvidenceEdge.lineage(
        edge_type=edge_type,
        source=_node(source_type, case_id=case_id),
        target=_node(target_type, case_id=case_id),
    )


# --- (7)/(8) exhaustive: exactly the 3 triples pass, everything else fails ---


@pytest.mark.parametrize(
    "edge_type,source_type,target_type",
    list(
        itertools.product(EvidenceEdgeType, EvidenceEntityType, EvidenceEntityType)
    ),
)
def test_only_allowlisted_triples_are_accepted(
    edge_type: EvidenceEdgeType,
    source_type: EvidenceEntityType,
    target_type: EvidenceEntityType,
) -> None:
    case_id = uuid4()
    triple = (edge_type, source_type, target_type)
    if triple in _ALLOWED:
        edge = _build(edge_type, source_type, target_type, case_id=case_id)
        assert edge.edge_type is edge_type
        assert edge.source_entity_type is source_type
        assert edge.target_entity_type is target_type
    else:
        with pytest.raises(ValidationError, match="not allowlisted"):
            _build(edge_type, source_type, target_type, case_id=case_id)


# --- (8) a valid edge type used in the reversed direction is rejected --------


@pytest.mark.parametrize("edge_type,source_type,target_type", list(_ALLOWED))
def test_reversed_direction_of_each_allowlisted_edge_is_rejected(
    edge_type: EvidenceEdgeType,
    source_type: EvidenceEntityType,
    target_type: EvidenceEntityType,
) -> None:
    case_id = uuid4()
    with pytest.raises(ValidationError, match="not allowlisted"):
        _build(edge_type, target_type, source_type, case_id=case_id)


# --- allowlist cannot be bypassed by direct construction --------------------


def test_direct_construction_of_non_allowlisted_edge_is_rejected() -> None:
    case_id = uuid4()
    with pytest.raises(ValidationError, match="not allowlisted"):
        EvidenceEdge(
            case_id=case_id,
            case_version=3,
            # PAGE_REGION -> DOCUMENT_VERSION is not an allowlisted lineage edge.
            edge_type=EvidenceEdgeType.LOCATED_IN_REGION,
            source_entity_type=EvidenceEntityType.PAGE_REGION,
            source_entity_id=uuid4(),
            target_entity_type=EvidenceEntityType.DOCUMENT_VERSION,
            target_entity_id=uuid4(),
        )


# --- (4) cross-case / cross-version endpoints fail closed -------------------


def test_lineage_rejects_cross_case_endpoints() -> None:
    source = _node(EvidenceEntityType.CONFIRMED_FACT, case_id=uuid4())
    target = _node(EvidenceEntityType.CANDIDATE_FACT, case_id=uuid4())
    with pytest.raises(ValueError, match="case_id"):
        EvidenceEdge.lineage(
            edge_type=EvidenceEdgeType.DERIVED_FROM_CANDIDATE,
            source=source,
            target=target,
        )


def test_lineage_rejects_cross_case_version_endpoints() -> None:
    case_id = uuid4()
    source = _node(EvidenceEntityType.CONFIRMED_FACT, case_id=case_id, case_version=3)
    target = _node(EvidenceEntityType.PAGE_REGION, case_id=case_id, case_version=4)
    with pytest.raises(ValueError, match="case_version"):
        EvidenceEdge.lineage(
            edge_type=EvidenceEdgeType.LOCATED_IN_REGION,
            source=source,
            target=target,
        )


def test_lineage_binds_the_single_shared_case_scope_onto_the_edge() -> None:
    case_id = uuid4()
    edge = _build(
        EvidenceEdgeType.SOURCED_FROM_DOCUMENT_VERSION,
        EvidenceEntityType.CONFIRMED_FACT,
        EvidenceEntityType.DOCUMENT_VERSION,
        case_id=case_id,
    )
    assert edge.case_id == case_id
    assert edge.case_version == 3


# --- scope note: the analytical-side hops are NOT modelled here -------------


def test_entity_vocabulary_covers_only_the_provenance_nodes() -> None:
    # DOCUMENT_VERSION, PAGE_REGION, CANDIDATE_FACT, CONFIRMED_FACT only.  The
    # downstream chain (CALCULATION_RESULT, UNDERWRITING/LEGAL_FINDING,
    # RISK_CHALLENGE/MAKER_RESPONSE, CREDIT_OPS_MEMO_SECTION) has no entity type
    # and no edge type -- those hops are NOT_IMPLEMENTED (see audit findings).
    assert {e.value for e in EvidenceEntityType} == {
        "CONFIRMED_FACT",
        "CANDIDATE_FACT",
        "PAGE_REGION",
        "DOCUMENT_VERSION",
    }
    assert {e.value for e in EvidenceEdgeType} == {
        "DERIVED_FROM_CANDIDATE",
        "LOCATED_IN_REGION",
        "SOURCED_FROM_DOCUMENT_VERSION",
    }
