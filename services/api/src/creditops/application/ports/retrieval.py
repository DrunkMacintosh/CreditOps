"""Durable-state contract for graph-guided hybrid retrieval (master design
sections 12, 12.2, 12.3).

The ``RunRetrieval`` application service depends ONLY on this port, never on
psycopg or pgvector.  The five operations mirror the section 12.2 pipeline order
after authorization:

* ``traverse_evidence_graph`` -- bounded, case+version-scoped recursive walk of
  the typed ``evidence_edges`` from the seed nodes to candidate source regions;
* ``lexical_search`` / ``vector_search`` -- ranking over ORIGINAL passages within
  the candidate set, each fully case+version-scoped and stale-filtered.  The
  QUERY embedding is supplied by the caller (from the injected model gateway);
  the adapter never fabricates one, so a missing gateway simply means no
  ``vector_search`` call (lexical-only degradation) rather than a fake vector;
* ``hydrate_passages`` -- joining the ranked passage ids back to their immutable
  source text + document/page/region + sha256 passage hash;
* ``persist_query_and_hits`` -- appending the retrieval trace (one query + its
  hits) in ONE transaction.

Every method is case+version scoped: the port cannot retrieve or persist across
cases.  Passage text carried through here originates in customer documents and is
UNTRUSTED -- the adapter binds it only as SQL parameters, never as instructions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from creditops.domain.retrieval import SeedNodeRef


class RetrievalPortError(RuntimeError):
    """A durable retrieval operation failed."""


@dataclass(frozen=True, slots=True)
class CandidatePassage:
    """A source passage reachable from the seeds within the traversal bounds."""

    passage_id: UUID
    document_version_id: UUID
    page_region_id: UUID | None


@dataclass(frozen=True, slots=True)
class ScoredPassage:
    """One passage ranked by a single search leg, best-first in the returned
    sequence.  ``score`` is that leg's own metric (lexical rank or vector
    similarity); the pipeline merges legs by position, not by raw score."""

    passage_id: UUID
    score: float


@dataclass(frozen=True, slots=True)
class HydratedPassage:
    """A ranked passage joined back to its immutable source.

    ``case_id``/``case_version`` are returned so the pipeline can assert the
    hydrated passage is in the request's authorized scope (defense in depth
    behind the scoped SQL); ``passage_hash`` is the sha256 of the source text a
    citation is validated against."""

    passage_id: UUID
    case_id: UUID
    case_version: int
    document_version_id: UUID
    page_number: int | None
    page_region_id: UUID | None
    passage_text: str
    passage_hash: str


@dataclass(frozen=True, slots=True)
class PersistableHit:
    """One retrieval hit to append to the trace, in final merged rank order."""

    passage_id: UUID
    rank: int
    lexical_score: float | None
    vector_score: float | None
    passage_hash: str


class EvidenceGraphRetrievalPort(Protocol):
    async def traverse_evidence_graph(
        self,
        *,
        case_id: UUID,
        case_version: int,
        seeds: Sequence[SeedNodeRef],
        max_hops: int,
        max_nodes: int,
    ) -> tuple[CandidatePassage, ...]: ...

    async def lexical_search(
        self,
        *,
        case_id: UUID,
        case_version: int,
        query_text: str,
        candidate_passage_ids: Sequence[UUID],
        limit: int,
    ) -> tuple[ScoredPassage, ...]: ...

    async def vector_search(
        self,
        *,
        case_id: UUID,
        case_version: int,
        query_embedding: Sequence[float],
        candidate_passage_ids: Sequence[UUID],
        limit: int,
    ) -> tuple[ScoredPassage, ...]: ...

    async def hydrate_passages(
        self,
        *,
        case_id: UUID,
        case_version: int,
        passage_ids: Sequence[UUID],
    ) -> tuple[HydratedPassage, ...]: ...

    async def persist_query_and_hits(
        self,
        *,
        case_id: UUID,
        case_version: int,
        task_id: UUID | None,
        query_text_vi: str,
        seed_node_refs: Sequence[SeedNodeRef],
        filters: Mapping[str, object],
        hits: Sequence[PersistableHit],
    ) -> UUID: ...
